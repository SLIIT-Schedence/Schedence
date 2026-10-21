"""SQLAlchemy implementation of the roster repository (schema ``examinations``).

The caller owns the session and its transaction; only :meth:`commit` / :meth:`rollback` end it.
Every statement is built from the model classes (schema-qualified, bound parameters), touches
only the six duty-roster tables, and uses the database rules as the final guard:
optimistic ``row_version`` in the ``UPDATE ... WHERE``, partial unique indexes, ``FOR UPDATE``.
Needs migration 0013; missing tables produce ``PERSISTENCE_NOT_MIGRATED``.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.duty_allocation.persistence import models as m
from app.duty_allocation.persistence.domain import (
    OPEN_CHANGE, WORKING, AssignmentRecord, ChangeKind, ChangeRequestRecord, ChangeStatus, EventRecord, PersistenceConflict,
    RosterRecord, RosterStatus, SeatingDraftRecord, SeatingDraftStatus, SeatRecord, assignment_from_json, assignment_to_json,
)
from app.duty_allocation.persistence.repository import ACTIVE_ROSTER_EXISTS, STAFF_DOUBLE_BOOKED, STALE_ROSTER

NOT_MIGRATED = "PERSISTENCE_NOT_MIGRATED"
_CONSTRAINT_CODES = {
    "uq_duty_rosters_one_working_per_period": (ACTIVE_ROSTER_EXISTS, "This period already has a working roster."),
    "uq_duty_rosters_one_published_per_period": (ACTIVE_ROSTER_EXISTS, "This period already has a published roster."),
    "uq_duty_rosters_institution_id_period_code_version": ("ROSTER_VERSION_EXISTS", "That roster version already exists."),
    "uq_duty_roster_assignments_roster_session_staff": (STAFF_DOUBLE_BOOKED, "A staff member holds two duties in one session."),
    "ck_duty_rosters_published_needs_verified_timetable": ("PUBLISH_NOT_ALLOWED",
                                                          "Only verified, non-synthetic rosters can be published."),
    "uq_seating_draft_seats_draft_session_student": ("SEAT_DOUBLE_BOOKED", "A student is seated twice."),
    "uq_seating_draft_seats_draft_session_venue_seat": ("SEAT_DOUBLE_BOOKED", "A seat is used twice."),
    "ck_seating_drafts_synthetic_never_verified": ("SEATS_NOT_VERIFIABLE", "Synthetic seating can never be VERIFIED."),
}
_ROSTER_COLUMNS = tuple(m.DutyRoster.__table__.columns)
_ASSIGNMENT_COLUMNS = (m.DutyRosterAssignment.session_id, m.DutyRosterAssignment.venue_id, m.DutyRosterAssignment.staff_id,
                       m.DutyRosterAssignment.role, m.DutyRosterAssignment.staff_number, m.DutyRosterAssignment.module_code,
                       m.DutyRosterAssignment.starts_at, m.DutyRosterAssignment.ends_at)
_UUID_FIELDS = ("created_by", "validated_by", "approved_by", "published_by", "rejected_by", "parent_roster_id")


def _sqlstate(exc: Exception) -> str | None:
    return getattr(getattr(exc, "orig", None), "sqlstate", None)


def _roster(row) -> RosterRecord:
    values = row._asdict()
    values["status"] = RosterStatus(values["status"])
    return RosterRecord(**values)


def _assignment(row) -> AssignmentRecord:
    values = row._asdict()
    values["staff_id"] = str(values["staff_id"])
    return AssignmentRecord(**values)


def _assignment_values(roster_id: uuid.UUID, a: AssignmentRecord) -> dict[str, Any]:
    return dict(roster_id=roster_id, session_id=a.session_id, venue_id=a.venue_id, staff_id=uuid.UUID(a.staff_id), role=a.role,
                staff_number=a.staff_number, module_code=a.module_code, starts_at=a.starts_at, ends_at=a.ends_at)


def _request(row) -> ChangeRequestRecord:
    v = row._asdict()
    v.update(kind=ChangeKind(v["kind"]), status=ChangeStatus(v["status"]),
             removed=tuple(assignment_from_json(x) for x in v["removed"]),
             added=tuple(assignment_from_json(x) for x in v["added"]))
    return ChangeRequestRecord(**v)


def _seating(row) -> SeatingDraftRecord:
    v = row._asdict()
    v["status"] = SeatingDraftStatus(v["status"])
    return SeatingDraftRecord(**v)


class SqlDutyRosterRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _execute(self, statement):
        try:
            return await self._session.execute(statement)
        except IntegrityError as exc:
            name = getattr(getattr(getattr(exc, "orig", None), "diag", None), "constraint_name", None)
            code, message = _CONSTRAINT_CODES.get(name, ("PERSISTENCE_CONFLICT", "The change conflicts with saved data."))
            raise PersistenceConflict(code, message) from None
        except ProgrammingError as exc:
            if _sqlstate(exc) == "42P01":  # undefined_table: migration 0013 is not applied
                raise PersistenceConflict(NOT_MIGRATED, "Saved rosters are not set up in this database yet.") from None
            raise

    # --- rosters ------------------------------------------------------------------------------------

    async def insert_roster(self, roster: RosterRecord, assignments: Sequence[AssignmentRecord]) -> None:
        values = {c.name: getattr(roster, c.name) for c in _ROSTER_COLUMNS}
        values["status"] = roster.status.value
        await self._execute(insert(m.DutyRoster).values(**values))
        if assignments:
            await self._execute(insert(m.DutyRosterAssignment).values(
                [_assignment_values(roster.roster_id, a) for a in assignments]))

    async def get_roster(self, roster_id, institution_id, *, lock: bool = False) -> RosterRecord | None:
        stmt = select(*_ROSTER_COLUMNS).where(m.DutyRoster.roster_id == roster_id,
                                              m.DutyRoster.institution_id == institution_id)
        if lock:
            stmt = stmt.with_for_update()
        row = (await self._execute(stmt)).one_or_none()
        return _roster(row) if row else None

    async def list_rosters(self, institution_id, *, period_code, status, limit, offset):
        conditions = [m.DutyRoster.institution_id == institution_id]
        if period_code is not None:
            conditions.append(m.DutyRoster.period_code == period_code)
        if status is not None:
            conditions.append(m.DutyRoster.status == status.value)
        total = (await self._execute(select(func.count()).select_from(m.DutyRoster).where(*conditions))).scalar_one()
        rows = await self._execute(select(*_ROSTER_COLUMNS).where(*conditions)
                                   .order_by(m.DutyRoster.period_code, m.DutyRoster.version.desc()).limit(limit).offset(offset))
        return [_roster(r) for r in rows], total

    async def latest_version(self, institution_id, period_code) -> int:
        stmt = select(func.coalesce(func.max(m.DutyRoster.version), 0)).where(
            m.DutyRoster.institution_id == institution_id, m.DutyRoster.period_code == period_code)
        return (await self._execute(stmt)).scalar_one()

    async def get_assignments(self, roster_id) -> list[AssignmentRecord]:
        rows = await self._execute(select(*_ASSIGNMENT_COLUMNS).where(m.DutyRosterAssignment.roster_id == roster_id)
                                   .order_by(m.DutyRosterAssignment.assignment_id))
        return [_assignment(r) for r in rows]

    async def update_roster(self, roster_id, expected_row_version, **changes: Any) -> RosterRecord:
        if "status" in changes:
            changes["status"] = changes["status"].value
        stmt = (update(m.DutyRoster)
                .where(m.DutyRoster.roster_id == roster_id, m.DutyRoster.row_version == expected_row_version)
                .values(**changes, row_version=expected_row_version + 1, updated_at=func.now())
                .returning(*_ROSTER_COLUMNS))
        row = (await self._execute(stmt)).one_or_none()
        if row is None:
            raise PersistenceConflict(STALE_ROSTER, "The roster was changed by someone else; reload it.")
        return _roster(row)

    async def apply_assignment_change(self, roster_id, removed, added) -> None:
        table = m.DutyRosterAssignment
        for a in removed:
            result = await self._execute(delete(table).where(
                table.roster_id == roster_id, table.session_id == a.session_id, table.venue_id == a.venue_id,
                table.staff_id == uuid.UUID(a.staff_id), table.role == a.role))
            if result.rowcount != 1:
                raise PersistenceConflict(STALE_ROSTER, f"Duty {a.session_id}/{a.venue_id} {a.role} is no longer in the roster.")
        if added:
            await self._execute(insert(table).values([_assignment_values(roster_id, a) for a in added]))

    async def overlapping_assignments(self, institution_id, staff_ids, starts_at, ends_at, exclude_period_code):
        if not staff_ids:
            return []
        a, r = m.DutyRosterAssignment, m.DutyRoster
        stmt = (select(r.roster_id, r.period_code, *_ASSIGNMENT_COLUMNS).join(r, r.roster_id == a.roster_id)
                .where(r.institution_id == institution_id, r.period_code != exclude_period_code,
                       r.status.in_([s.value for s in (*WORKING, RosterStatus.PUBLISHED)]),
                       a.staff_id.in_([uuid.UUID(s) for s in staff_ids]), a.starts_at < ends_at, a.ends_at > starts_at))
        return [(row.roster_id, row.period_code, _assignment(row)) for row in await self._execute(stmt)]

    # --- audit trail -------------------------------------------------------------------------------

    async def add_event(self, roster_id, action, from_status, to_status, actor_id, reason, details) -> EventRecord:
        stmt = insert(m.DutyRosterEvent).values(roster_id=roster_id, action=action, from_status=from_status,
                                                to_status=to_status, actor_id=actor_id, reason=reason, details=details
                                                ).returning(*m.DutyRosterEvent.__table__.columns)
        return EventRecord(**(await self._execute(stmt)).one()._asdict())

    async def list_events(self, roster_id) -> list[EventRecord]:
        rows = await self._execute(select(*m.DutyRosterEvent.__table__.columns)
                                   .where(m.DutyRosterEvent.roster_id == roster_id).order_by(m.DutyRosterEvent.event_id))
        return [EventRecord(**r._asdict()) for r in rows]

    # --- change requests ---------------------------------------------------------------------------

    async def insert_change_request(self, request: ChangeRequestRecord) -> None:
        values = {c.name: getattr(request, c.name) for c in m.DutyChangeRequest.__table__.columns}
        values.update(kind=request.kind.value, status=request.status.value,
                      removed=[assignment_to_json(a) for a in request.removed],
                      added=[assignment_to_json(a) for a in request.added])
        await self._execute(insert(m.DutyChangeRequest).values(**values))

    async def get_change_request(self, request_id, institution_id) -> ChangeRequestRecord | None:
        row = (await self._execute(select(*m.DutyChangeRequest.__table__.columns).where(
            m.DutyChangeRequest.request_id == request_id, m.DutyChangeRequest.institution_id == institution_id))).one_or_none()
        return _request(row) if row else None

    async def list_change_requests(self, roster_id) -> list[ChangeRequestRecord]:
        rows = await self._execute(select(*m.DutyChangeRequest.__table__.columns)
                                   .where(m.DutyChangeRequest.roster_id == roster_id).order_by(m.DutyChangeRequest.requested_at))
        return [_request(r) for r in rows]

    async def update_change_request(self, request_id, expected_status, **changes) -> ChangeRequestRecord:
        if "status" in changes:
            changes["status"] = changes["status"].value
        stmt = (update(m.DutyChangeRequest).where(m.DutyChangeRequest.request_id == request_id,
                                                  m.DutyChangeRequest.status == expected_status.value)
                .values(**changes).returning(*m.DutyChangeRequest.__table__.columns))
        row = (await self._execute(stmt)).one_or_none()
        if row is None:
            raise PersistenceConflict(STALE_ROSTER, "The request was changed by someone else; reload it.")
        return _request(row)

    async def mark_open_requests_stale(self, roster_id, except_request_id) -> int:
        stmt = (update(m.DutyChangeRequest).where(
            m.DutyChangeRequest.roster_id == roster_id, m.DutyChangeRequest.request_id != except_request_id,
            m.DutyChangeRequest.status.in_([s.value for s in OPEN_CHANGE])).values(status=ChangeStatus.STALE.value))
        return (await self._execute(stmt)).rowcount

    # --- seating drafts ----------------------------------------------------------------------------

    async def latest_seating_version(self, institution_id, period_code) -> int:
        stmt = select(func.coalesce(func.max(m.SeatingDraft.version), 0)).where(
            m.SeatingDraft.institution_id == institution_id, m.SeatingDraft.period_code == period_code)
        return (await self._execute(stmt)).scalar_one()

    async def insert_seating_draft(self, draft: SeatingDraftRecord, seats: Sequence[SeatRecord]) -> None:
        await self._execute(update(m.SeatingDraft).where(
            m.SeatingDraft.institution_id == draft.institution_id, m.SeatingDraft.period_code == draft.period_code,
            m.SeatingDraft.status == SeatingDraftStatus.DRAFT.value).values(status=SeatingDraftStatus.SUPERSEDED.value))
        values = {c.name: getattr(draft, c.name) for c in m.SeatingDraft.__table__.columns}
        values["status"] = draft.status.value
        await self._execute(insert(m.SeatingDraft).values(**values))
        if seats:
            await self._execute(insert(m.SeatingDraftSeat).values([
                dict(draft_id=draft.draft_id, session_id=s.session_id, venue_id=s.venue_id, student_id=s.student_id,
                     module_code=s.module_code, logical_seat_number=s.logical_seat_number,
                     physical_seat_number=s.physical_seat_number, physical_seat_label=s.physical_seat_label,
                     verification_status=s.verification_status) for s in seats]))

    async def get_seating_draft(self, draft_id, institution_id) -> SeatingDraftRecord | None:
        row = (await self._execute(select(*m.SeatingDraft.__table__.columns).where(
            m.SeatingDraft.draft_id == draft_id, m.SeatingDraft.institution_id == institution_id))).one_or_none()
        return _seating(row) if row else None

    async def list_seating_drafts(self, institution_id, *, period_code, limit, offset):
        conditions = [m.SeatingDraft.institution_id == institution_id]
        if period_code is not None:
            conditions.append(m.SeatingDraft.period_code == period_code)
        total = (await self._execute(select(func.count()).select_from(m.SeatingDraft).where(*conditions))).scalar_one()
        rows = await self._execute(select(*m.SeatingDraft.__table__.columns).where(*conditions)
                                   .order_by(m.SeatingDraft.period_code, m.SeatingDraft.version.desc()).limit(limit).offset(offset))
        return [_seating(r) for r in rows], total

    async def get_seats(self, draft_id) -> list[SeatRecord]:
        cols = (m.SeatingDraftSeat.session_id, m.SeatingDraftSeat.venue_id, m.SeatingDraftSeat.student_id,
                m.SeatingDraftSeat.module_code, m.SeatingDraftSeat.logical_seat_number,
                m.SeatingDraftSeat.physical_seat_number, m.SeatingDraftSeat.physical_seat_label,
                m.SeatingDraftSeat.verification_status)
        rows = await self._execute(select(*cols).where(m.SeatingDraftSeat.draft_id == draft_id).order_by(m.SeatingDraftSeat.seat_id))
        return [SeatRecord(**r._asdict()) for r in rows]

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()
