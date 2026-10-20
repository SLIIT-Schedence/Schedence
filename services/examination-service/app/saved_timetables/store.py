"""Storage of saved examination timetables (``examinations.exam_timetable*``).

:class:`TimetableStore` is the interface; :class:`DatabaseTimetableStore` implements
it on PostgreSQL and :class:`app.saved_timetables.memory.MemoryTimetableStore` in
memory. Every read and write is limited to one institution. The caller owns the
session; only :meth:`commit` / :meth:`rollback` end its transaction.

Rows are plain dicts with the column names of :mod:`app.db.timetable_models`.
Status changes (0011) are conditional on the expected status and revision and
always write a history row in the same transaction.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import and_, func, insert, select, text, update
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.timetable_models import (
    STATUS_CHANGES,
    ExamTimetable,
    ExamTimetableHistory,
    ExamTimetableRoom,
    ExamTimetableSeat,
    ExamTimetableSession,
)
from app.saved_timetables.errors import (
    SavedTimetableBusyError,
    SavedTimetablesNotConfiguredError,
    TimetableWorkflowError,
)
from app.scheduling.protected import PROTECTED_STATUSES, ProtectedSchedule, build_protected_schedule, count_overlapping_drafts

Row = dict[str, Any]
CREATE_REASON = "Saved as a new draft"


@dataclass(frozen=True)
class NewTimetable:
    """A validated timetable to save. ``timetable_id`` and ``draft_number`` are given by the store."""

    header: Row  # exam_timetables columns without the generated ones
    sessions: list[Row] = field(default_factory=list)
    rooms: list[Row] = field(default_factory=list)  # with session_id
    seats: list[Row] = field(default_factory=list)  # with session_id and venue_id


@dataclass(frozen=True)
class TimetableDetail:
    header: Row
    sessions: list[Row]  # time order
    rooms: list[Row]


@dataclass(frozen=True)
class ListFilter:
    period_code: str | None = None
    exam_kind: str | None = None
    status: str | None = None


class TimetableStore(Protocol):
    async def save(self, institution_id: UUID, timetable: NewTimetable) -> Row:
        """Insert as a new DRAFT with the next draft number of (institution, cycle); returns the header."""

    async def list(self, institution_id: UUID, filters: ListFilter, *, limit: int, offset: int
                   ) -> tuple[list[Row], int]:
        """Headers, newest first, and the total count."""

    async def get(self, institution_id: UUID, timetable_id: int) -> TimetableDetail | None: ...

    async def seats(self, institution_id: UUID, timetable_id: int, *, session_id: str | None, limit: int,
                    offset: int) -> tuple[list[Row], int] | None:
        """``None`` when the timetable is not found in the institution."""

    async def lock(self, institution_id: UUID, timetable_id: int) -> Row | None:
        """The header, locked until the end of the transaction (SELECT ... FOR UPDATE)."""

    async def lock_cycle(self, institution_id: UUID, period_code: str) -> None:
        """Serialise saves and publications of one examination cycle until the end of the transaction."""

    async def lock_protected(self, institution_id: UUID) -> None:
        """Serialise approvals and publications of the whole institution until the end of the transaction.

        Cycles protect each other, so two cycles must not become protected at the same time unchecked.
        """

    async def protected_schedule(self, institution_id: UUID, *, exclude_period_code: str, start_date: dt.date,
                                 end_date: dt.date, student_ids: Collection[str], minimum_break_minutes: int = 0) -> ProtectedSchedule:
        """The APPROVED / PUBLISHED timetables of the institution's other cycles that overlap the dates.

        Read only. Includes sessions near date boundaries within minimum_break_minutes,
        with their venues and the seats of student_ids only.
        DRAFT timetables are not protected; those with overlapping dates are only counted.
        """

    async def allocations(self, timetable_id: int) -> tuple[list[Row], list[Row], list[Row]]:
        """Every stored session, room and seat of a timetable already found in the institution."""

    async def published(self, institution_id: UUID, period_code: str) -> Row | None:
        """The cycle's PUBLISHED timetable, if any."""

    async def change_status(self, institution_id: UUID, timetable_id: int, *, from_status: str, to_status: str,
                            expected_revision: int, user_id: UUID, reason: str, details: dict[str, Any]
                            ) -> tuple[Row, Row] | None:
        """(header, history row); ``None`` when the status or revision is no longer the expected one."""

    async def history(self, institution_id: UUID, timetable_id: int) -> list[Row] | None:
        """Oldest first; ``None`` when the timetable is not found in the institution."""

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


def workflow_values(to_status: str, user_id: UUID, now) -> dict[str, Any]:
    if to_status == "APPROVED":
        return {"approved_by": user_id, "approved_at": now}
    if to_status == "PUBLISHED":
        return {"published_by": user_id, "published_at": now}
    return {}


_HEADER = tuple(ExamTimetable.__table__.columns)
_SESSION = tuple(ExamTimetableSession.__table__.columns)
_ROOM = tuple(ExamTimetableRoom.__table__.columns)
_SEAT = tuple(ExamTimetableSeat.__table__.columns)
_HISTORY = tuple(ExamTimetableHistory.__table__.columns)


def _sqlstate(exc: Exception) -> str | None:
    return getattr(getattr(exc, "orig", None), "sqlstate", None)


class DatabaseTimetableStore:
    def __init__(self, session: AsyncSession, *, lock_timeout_ms: int = 10_000):
        self._session = session
        self._lock_timeout_ms = int(lock_timeout_ms)

    async def _execute(self, statement, params=None):
        try:
            return await self._session.execute(statement, params)
        except ProgrammingError as exc:
            if _sqlstate(exc) in ("42P01", "42703"):  # undefined table / column: migration 0010 / 0011 missing
                raise SavedTimetablesNotConfiguredError() from None
            raise
        except OperationalError as exc:
            if _sqlstate(exc) == "55P03":  # lock_not_available (lock_timeout)
                raise SavedTimetableBusyError() from None
            raise

    async def _lock_timeout(self) -> None:
        await self._execute(text(f"SET LOCAL lock_timeout = {self._lock_timeout_ms}"))

    async def lock_cycle(self, institution_id: UUID, period_code: str) -> None:
        await self._lock_timeout()
        await self._execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                            {"key": f"exam_timetables:{institution_id}:{period_code}"})

    async def lock_protected(self, institution_id: UUID) -> None:
        await self._lock_timeout()
        await self._execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                            {"key": f"exam_timetables_protected:{institution_id}"})

    async def protected_schedule(self, institution_id: UUID, *, exclude_period_code: str, start_date: dt.date,
                                 end_date: dt.date, student_ids: Collection[str], minimum_break_minutes: int = 0) -> ProtectedSchedule:
        other = (ExamTimetable.institution_id == institution_id, ExamTimetable.period_code != exclude_period_code)
        headers = [r._asdict() for r in await self._execute(
            select(ExamTimetable.timetable_id, ExamTimetable.period_code, ExamTimetable.period_name, ExamTimetable.status,
                   ExamTimetable.draft_number, ExamTimetable.revision, ExamTimetable.inputs)
            .where(*other, ExamTimetable.status.in_(PROTECTED_STATUSES)))]
        drafts = [r._asdict() for r in await self._execute(
            select(ExamTimetable.inputs).where(*other, ExamTimetable.status == "DRAFT"))]
        window = (start_date, end_date)
        overlapping = count_overlapping_drafts(drafts, window)
        ids = [h["timetable_id"] for h in headers]
        if not ids:
            return ProtectedSchedule((), overlapping)
        gap = dt.timedelta(minutes=minimum_break_minutes)
        first = dt.datetime.combine(start_date, dt.time()) - gap
        last = dt.datetime.combine(end_date + dt.timedelta(days=1), dt.time()) + gap
        in_window = (ExamTimetableSession.timetable_id.in_(ids), ExamTimetableSession.starts_at < last,
                     ExamTimetableSession.ends_at > first)

        def of_session(model):  # the rows of model that belong to one of the sessions inside the dates
            return and_(model.timetable_id == ExamTimetableSession.timetable_id,
                        model.session_id == ExamTimetableSession.session_id)

        sessions = await self._execute(select(
            ExamTimetableSession.timetable_id, ExamTimetableSession.session_id, ExamTimetableSession.module_code,
            ExamTimetableSession.starts_at, ExamTimetableSession.ends_at).where(*in_window))
        rooms = await self._execute(
            select(ExamTimetableRoom.timetable_id, ExamTimetableRoom.session_id, ExamTimetableRoom.venue_id)
            .join(ExamTimetableSession, of_session(ExamTimetableRoom)).where(*in_window))
        seats = await self._execute(
            select(ExamTimetableSeat.timetable_id, ExamTimetableSeat.session_id, ExamTimetableSeat.student_id)
            .join(ExamTimetableSession, of_session(ExamTimetableSeat))
            .where(*in_window, ExamTimetableSeat.student_id.in_(student_ids)))
        return build_protected_schedule(
            headers, [r._asdict() for r in sessions], [r._asdict() for r in rooms], [r._asdict() for r in seats],
            window=window, student_ids=student_ids, overlapping_drafts=overlapping,
            minimum_break_minutes=minimum_break_minutes)

    async def save(self, institution_id: UUID, timetable: NewTimetable) -> Row:
        period_code = timetable.header["period_code"]
        # Serialises saves of the same cycle, so two drafts never get the same number.
        await self.lock_cycle(institution_id, period_code)
        latest = (await self._execute(
            select(func.coalesce(func.max(ExamTimetable.draft_number), 0))
            .where(ExamTimetable.institution_id == institution_id, ExamTimetable.period_code == period_code)
        )).scalar_one()
        header = (await self._execute(
            insert(ExamTimetable)
            .values(**timetable.header, institution_id=institution_id, draft_number=latest + 1, status="DRAFT")
            .returning(*_HEADER)
        )).one()._asdict()
        timetable_id = header["timetable_id"]
        for model, rows in ((ExamTimetableSession, timetable.sessions), (ExamTimetableRoom, timetable.rooms),
                            (ExamTimetableSeat, timetable.seats)):
            if rows:
                await self._execute(insert(model), [{**row, "timetable_id": timetable_id} for row in rows])
        await self._execute(insert(ExamTimetableHistory).values(
            timetable_id=timetable_id, institution_id=institution_id, action="create", from_status=None,
            to_status="DRAFT", revision=header["revision"], changed_by=header["created_by"], reason=CREATE_REASON,
            details={"source_timetable_id": header.get("source_timetable_id")}))
        return header

    async def list(self, institution_id: UUID, filters: ListFilter, *, limit: int, offset: int
                   ) -> tuple[list[Row], int]:
        where = [ExamTimetable.institution_id == institution_id]
        for name in ("period_code", "exam_kind", "status"):
            if getattr(filters, name) is not None:
                where.append(getattr(ExamTimetable, name) == getattr(filters, name))
        total = (await self._execute(select(func.count()).select_from(ExamTimetable).where(*where))).scalar_one()
        rows = await self._execute(
            select(*_HEADER).where(*where)
            .order_by(ExamTimetable.created_at.desc(), ExamTimetable.timetable_id.desc())
            .limit(limit).offset(offset)
        )
        return [row._asdict() for row in rows], total

    def _header_query(self, institution_id: UUID, timetable_id: int):
        return select(*_HEADER).where(ExamTimetable.institution_id == institution_id,
                                      ExamTimetable.timetable_id == timetable_id)

    async def get(self, institution_id: UUID, timetable_id: int) -> TimetableDetail | None:
        header = (await self._execute(self._header_query(institution_id, timetable_id))).one_or_none()
        if header is None:
            return None
        sessions = await self._execute(
            select(*_SESSION).where(ExamTimetableSession.timetable_id == timetable_id)
            .order_by(ExamTimetableSession.starts_at, ExamTimetableSession.module_code,
                      ExamTimetableSession.session_number)
        )
        rooms = await self._execute(
            select(*_ROOM).where(ExamTimetableRoom.timetable_id == timetable_id)
            .order_by(ExamTimetableRoom.session_id, ExamTimetableRoom.venue_id)
        )
        return TimetableDetail(header._asdict(), [r._asdict() for r in sessions], [r._asdict() for r in rooms])

    async def _exists(self, institution_id: UUID, timetable_id: int) -> bool:
        return (await self._execute(
            select(ExamTimetable.timetable_id).where(ExamTimetable.institution_id == institution_id,
                                                     ExamTimetable.timetable_id == timetable_id)
        )).one_or_none() is not None

    async def seats(self, institution_id: UUID, timetable_id: int, *, session_id: str | None, limit: int,
                    offset: int) -> tuple[list[Row], int] | None:
        if not await self._exists(institution_id, timetable_id):
            return None
        where = [ExamTimetableSeat.timetable_id == timetable_id]
        if session_id is not None:
            where.append(ExamTimetableSeat.session_id == session_id)
        total = (await self._execute(select(func.count()).select_from(ExamTimetableSeat).where(*where))).scalar_one()
        rows = await self._execute(
            select(*_SEAT).where(*where)
            .order_by(ExamTimetableSeat.session_id, ExamTimetableSeat.venue_id, ExamTimetableSeat.seat_number)
            .limit(limit).offset(offset)
        )
        return [row._asdict() for row in rows], total

    async def lock(self, institution_id: UUID, timetable_id: int) -> Row | None:
        await self._lock_timeout()
        row = (await self._execute(self._header_query(institution_id, timetable_id).with_for_update())).one_or_none()
        return row._asdict() if row else None

    async def allocations(self, timetable_id: int) -> tuple[list[Row], list[Row], list[Row]]:
        result = []
        for model, columns in ((ExamTimetableSession, _SESSION), (ExamTimetableRoom, _ROOM),
                               (ExamTimetableSeat, _SEAT)):
            rows = await self._execute(select(*columns).where(model.timetable_id == timetable_id))
            result.append([r._asdict() for r in rows])
        return result[0], result[1], result[2]

    async def published(self, institution_id: UUID, period_code: str) -> Row | None:
        row = (await self._execute(select(*_HEADER).where(
            ExamTimetable.institution_id == institution_id, ExamTimetable.period_code == period_code,
            ExamTimetable.status == "PUBLISHED"))).one_or_none()
        return row._asdict() if row else None

    async def change_status(self, institution_id: UUID, timetable_id: int, *, from_status: str, to_status: str,
                            expected_revision: int, user_id: UUID, reason: str, details: dict[str, Any]
                            ) -> tuple[Row, Row] | None:
        action = STATUS_CHANGES[from_status, to_status]
        statement = (
            update(ExamTimetable)
            .where(ExamTimetable.institution_id == institution_id, ExamTimetable.timetable_id == timetable_id,
                   ExamTimetable.status == from_status, ExamTimetable.revision == expected_revision)
            .values(status=to_status, revision=expected_revision + 1, **workflow_values(to_status, user_id, func.now()))
            .returning(*_HEADER)
        )
        try:
            row = (await self._execute(statement)).one_or_none()
        except IntegrityError as exc:
            # One published timetable per cycle, or a workflow rule of migration 0011.
            if _sqlstate(exc) == "23505":
                raise TimetableWorkflowError("ALREADY_PUBLISHED",
                                             "Another timetable of this cycle was published at the same time.") from None
            raise TimetableWorkflowError("STATUS", "The database refused this status change.") from None
        if row is None:
            return None
        history = (await self._execute(insert(ExamTimetableHistory).values(
            timetable_id=timetable_id, institution_id=institution_id, action=action, from_status=from_status,
            to_status=to_status, revision=expected_revision + 1, changed_by=user_id, reason=reason, details=details,
        ).returning(*_HISTORY))).one()._asdict()
        return row._asdict(), history

    async def history(self, institution_id: UUID, timetable_id: int) -> list[Row] | None:
        if not await self._exists(institution_id, timetable_id):
            return None
        rows = await self._execute(select(*_HISTORY).where(ExamTimetableHistory.timetable_id == timetable_id)
                                   .order_by(ExamTimetableHistory.history_id))
        return [r._asdict() for r in rows]

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()
