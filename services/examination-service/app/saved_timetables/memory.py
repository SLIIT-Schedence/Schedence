"""In-memory ``TimetableStore`` for tests and offline tools.

Behaves like the database store: changes are visible to this store only until
:meth:`commit`; :meth:`rollback` throws them away. Starts empty, like migration 0010.
Status changes follow the same rules as the database triggers of migration 0011.
"""

from __future__ import annotations

import copy
import datetime as dt
from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from app.db.timetable_models import STATUS_CHANGES
from app.saved_timetables.errors import TimetableWorkflowError
from app.saved_timetables.store import CREATE_REASON, ListFilter, NewTimetable, Row, TimetableDetail, workflow_values
from app.scheduling.protected import PROTECTED_STATUSES, ProtectedSchedule, build_protected_schedule, count_overlapping_drafts

_PREPARED = {"revision": 1, "approved_by": None, "approved_at": None, "published_by": None, "published_at": None,
             "content_fingerprint": None, "source_timetable_id": None}


@dataclass
class _State:
    timetables: list[tuple[UUID, NewTimetable, Row]] = field(default_factory=list)
    history: list[Row] = field(default_factory=list)


class MemoryTimetableStore:
    def __init__(self, *, clock=lambda: dt.datetime.now(dt.UTC)):
        self._clock = clock
        self._committed = _State()
        self._work = _State()
        self.commits = 0
        self.fail_on: str | None = None  # name of a method that raises, to test rollback

    def _maybe_fail(self, name: str) -> None:
        if self.fail_on == name:
            raise RuntimeError(f"simulated failure in {name}")

    def _add_history(self, **values: Any) -> Row:
        row = {"history_id": len(self._work.history) + 1, "changed_at": self._clock(), **values}
        self._work.history.append(row)
        return dict(row)

    async def save(self, institution_id: UUID, timetable: NewTimetable) -> Row:
        self._maybe_fail("save")
        period = timetable.header["period_code"]
        latest = max((h["draft_number"] for i, _, h in self._work.timetables if i == institution_id
                      and h["period_code"] == period), default=0)
        all_ids = [h["timetable_id"] for _, _, h in self._committed.timetables + self._work.timetables]
        header = {**_PREPARED, **copy.deepcopy(timetable.header), "timetable_id": max(all_ids, default=0) + 1,
                  "institution_id": institution_id, "draft_number": latest + 1, "status": "DRAFT", "revision": 1,
                  "created_at": self._clock()}
        self._work.timetables.append((institution_id, copy.deepcopy(timetable), header))
        self._add_history(timetable_id=header["timetable_id"], institution_id=institution_id, action="create",
                          from_status=None, to_status="DRAFT", revision=1, changed_by=header.get("created_by"),
                          reason=CREATE_REASON, details={"source_timetable_id": header["source_timetable_id"]})
        return dict(header)

    def _find(self, institution_id: UUID, timetable_id: int):
        return next(((t, h) for i, t, h in self._work.timetables
                     if i == institution_id and h["timetable_id"] == timetable_id), None)

    async def list(self, institution_id: UUID, filters: ListFilter, *, limit: int, offset: int
                   ) -> tuple[list[Row], int]:
        rows = [h for i, _, h in self._work.timetables if i == institution_id
                and all(getattr(filters, k) in (None, h[k]) for k in ("period_code", "exam_kind", "status"))]
        rows.sort(key=lambda h: (h["created_at"], h["timetable_id"]), reverse=True)
        return [dict(h) for h in rows[offset:offset + limit]], len(rows)

    async def get(self, institution_id: UUID, timetable_id: int) -> TimetableDetail | None:
        found = self._find(institution_id, timetable_id)
        if found is None:
            return None
        timetable, header = found
        sessions = sorted(timetable.sessions, key=lambda s: (s["starts_at"], s["module_code"], s["session_number"]))
        rooms = sorted(timetable.rooms, key=lambda r: (r["session_id"], r["venue_id"]))
        return TimetableDetail(dict(header), [{**s, "timetable_id": timetable_id} for s in sessions],
                               [{**r, "timetable_id": timetable_id} for r in rooms])

    async def seats(self, institution_id: UUID, timetable_id: int, *, session_id: str | None, limit: int,
                    offset: int) -> tuple[list[Row], int] | None:
        found = self._find(institution_id, timetable_id)
        if found is None:
            return None
        rows = sorted((s for s in found[0].seats if session_id in (None, s["session_id"])),
                      key=lambda s: (s["session_id"], s["venue_id"], s["seat_number"]))
        return [{**s, "timetable_id": timetable_id} for s in rows[offset:offset + limit]], len(rows)

    async def lock(self, institution_id: UUID, timetable_id: int) -> Row | None:
        found = self._find(institution_id, timetable_id)
        return dict(found[1]) if found else None

    async def lock_cycle(self, institution_id: UUID, period_code: str) -> None:
        return None

    async def lock_protected(self, institution_id: UUID) -> None:
        return None

    async def protected_schedule(self, institution_id: UUID, *, exclude_period_code: str, start_date: dt.date,
                                 end_date: dt.date, student_ids: Collection[str], minimum_break_minutes: int = 0) -> ProtectedSchedule:
        others = [(t, h) for i, t, h in self._work.timetables if i == institution_id
                  and h["period_code"] != exclude_period_code]
        protected = [(t, h) for t, h in others if h["status"] in PROTECTED_STATUSES]
        drafts = [{"inputs": h["inputs"]} for _, h in others if h["status"] == "DRAFT"]
        keyed = lambda rows, h: [{**r, "timetable_id": h["timetable_id"]} for r in rows]  # noqa: E731
        return build_protected_schedule(
            [h for _, h in protected],
            [r for t, h in protected for r in keyed(t.sessions, h)],
            [r for t, h in protected for r in keyed(t.rooms, h)],
            [r for t, h in protected for r in keyed(t.seats, h)],
            window=(start_date, end_date), student_ids=student_ids,
            overlapping_drafts=count_overlapping_drafts(drafts, (start_date, end_date)),
            minimum_break_minutes=minimum_break_minutes)

    async def allocations(self, timetable_id: int) -> tuple[list[Row], list[Row], list[Row]]:
        timetable = next(t for _, t, h in self._work.timetables if h["timetable_id"] == timetable_id)
        return tuple([{**r, "timetable_id": timetable_id} for r in rows]
                     for rows in (timetable.sessions, timetable.rooms, timetable.seats))

    async def published(self, institution_id: UUID, period_code: str) -> Row | None:
        return next((dict(h) for i, _, h in self._work.timetables if i == institution_id
                     and h["period_code"] == period_code and h["status"] == "PUBLISHED"), None)

    async def change_status(self, institution_id: UUID, timetable_id: int, *, from_status: str, to_status: str,
                            expected_revision: int, user_id: UUID, reason: str, details: dict[str, Any]
                            ) -> tuple[Row, Row] | None:
        self._maybe_fail("change_status")
        action = STATUS_CHANGES[from_status, to_status]
        found = self._find(institution_id, timetable_id)
        if found is None or found[1]["status"] != from_status or found[1]["revision"] != expected_revision:
            return None
        header = found[1]
        if to_status == "PUBLISHED" and await self.published(institution_id, header["period_code"]):
            raise TimetableWorkflowError("ALREADY_PUBLISHED",
                                         "Another timetable of this cycle was published at the same time.")
        header.update(status=to_status, revision=expected_revision + 1,
                      **workflow_values(to_status, user_id, self._clock()))
        history = self._add_history(timetable_id=timetable_id, institution_id=institution_id, action=action,
                                    from_status=from_status, to_status=to_status, revision=expected_revision + 1,
                                    changed_by=user_id, reason=reason, details=copy.deepcopy(details))
        return dict(header), history

    async def history(self, institution_id: UUID, timetable_id: int) -> list[Row] | None:
        if self._find(institution_id, timetable_id) is None:
            return None
        return [dict(h) for h in self._work.history if h["timetable_id"] == timetable_id]

    def tamper_seat(self, timetable_id: int, **changes: Any) -> None:
        """Test helper: change a stored seat behind the API's back (as direct SQL would)."""
        timetable = next(t for _, t, h in self._work.timetables if h["timetable_id"] == timetable_id)
        timetable.seats[0].update(changes)

    async def commit(self) -> None:
        self._maybe_fail("commit")
        self._committed = copy.deepcopy(self._work)
        self.commits += 1

    async def rollback(self) -> None:
        self._work = copy.deepcopy(self._committed)
