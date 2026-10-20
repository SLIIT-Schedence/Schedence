"""Repository interface for saved rosters, plus the in-memory implementation.

The in-memory repository enforces the same rules as the database constraints (one working
and one published roster per period, unique staff per session, optimistic ``row_version``)
and is atomic: changes live in a private working copy until :meth:`commit`, and a commit is
refused when another request committed in the meantime (so there are no lost updates).
It is used for tests and for the frontend ``MEMORY`` persistence mode (not durable).
"""

from __future__ import annotations

import copy
import datetime as dt
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from app.duty_allocation.persistence.domain import (
    WORKING, AssignmentRecord, ChangeRequestRecord, ChangeStatus, EventRecord, OPEN_CHANGE, PersistenceConflict,
    RosterRecord, RosterStatus, SeatingDraftRecord, SeatingDraftStatus, SeatRecord,
)

ACTIVE_ROSTER_EXISTS = "ACTIVE_ROSTER_EXISTS"
STALE_ROSTER = "STALE_ROSTER"
STAFF_DOUBLE_BOOKED = "STAFF_DOUBLE_BOOKED"
CONCURRENT_CHANGE = "CONCURRENT_CHANGE"


class DutyRosterRepository(Protocol):
    async def insert_roster(self, roster: RosterRecord, assignments: Sequence[AssignmentRecord]) -> None: ...
    async def get_roster(self, roster_id: uuid.UUID, institution_id: uuid.UUID, *, lock: bool = False) -> RosterRecord | None: ...
    async def list_rosters(self, institution_id: uuid.UUID, *, period_code: str | None, status: RosterStatus | None,
                           limit: int, offset: int) -> tuple[list[RosterRecord], int]: ...
    async def latest_version(self, institution_id: uuid.UUID, period_code: str) -> int: ...
    async def get_assignments(self, roster_id: uuid.UUID) -> list[AssignmentRecord]: ...
    async def update_roster(self, roster_id: uuid.UUID, expected_row_version: int, **changes: Any) -> RosterRecord: ...
    async def apply_assignment_change(self, roster_id: uuid.UUID, removed: Sequence[AssignmentRecord],
                                      added: Sequence[AssignmentRecord]) -> None: ...
    async def overlapping_assignments(self, institution_id: uuid.UUID, staff_ids: Sequence[str], starts_at: dt.datetime,
                                      ends_at: dt.datetime, exclude_period_code: str
                                      ) -> list[tuple[uuid.UUID, str, AssignmentRecord]]: ...
    async def add_event(self, roster_id: uuid.UUID, action: str, from_status: str | None, to_status: str | None,
                        actor_id: uuid.UUID, reason: str | None, details: dict[str, Any]) -> EventRecord: ...
    async def list_events(self, roster_id: uuid.UUID) -> list[EventRecord]: ...
    async def insert_change_request(self, request: ChangeRequestRecord) -> None: ...
    async def get_change_request(self, request_id: uuid.UUID, institution_id: uuid.UUID) -> ChangeRequestRecord | None: ...
    async def list_change_requests(self, roster_id: uuid.UUID) -> list[ChangeRequestRecord]: ...
    async def update_change_request(self, request_id: uuid.UUID, expected_status: ChangeStatus,
                                    **changes: Any) -> ChangeRequestRecord: ...
    async def mark_open_requests_stale(self, roster_id: uuid.UUID, except_request_id: uuid.UUID) -> int: ...
    async def latest_seating_version(self, institution_id: uuid.UUID, period_code: str) -> int: ...
    async def insert_seating_draft(self, draft: SeatingDraftRecord, seats: Sequence[SeatRecord]) -> None: ...
    async def get_seating_draft(self, draft_id: uuid.UUID, institution_id: uuid.UUID) -> SeatingDraftRecord | None: ...
    async def list_seating_drafts(self, institution_id: uuid.UUID, *, period_code: str | None, limit: int,
                                  offset: int) -> tuple[list[SeatingDraftRecord], int]: ...
    async def get_seats(self, draft_id: uuid.UUID) -> list[SeatRecord]: ...
    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...


def overlaps(a_start: dt.datetime, a_end: dt.datetime, b_start: dt.datetime, b_end: dt.datetime) -> bool:
    return a_start < b_end and b_start < a_end  # touching intervals do not overlap


@dataclass
class _Data:
    rosters: dict[uuid.UUID, RosterRecord] = field(default_factory=dict)
    assignments: dict[uuid.UUID, list[AssignmentRecord]] = field(default_factory=dict)
    events: list[EventRecord] = field(default_factory=list)
    requests: dict[uuid.UUID, ChangeRequestRecord] = field(default_factory=dict)
    seating: dict[uuid.UUID, SeatingDraftRecord] = field(default_factory=dict)
    seats: dict[uuid.UUID, list[SeatRecord]] = field(default_factory=dict)
    next_event_id: int = 1


class MemoryState:
    """The shared, process-wide store behind :class:`MemoryDutyRosterRepository`."""

    def __init__(self) -> None:
        self.data = _Data()
        self.revision = 0


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class MemoryDutyRosterRepository:
    def __init__(self, state: MemoryState | None = None, clock: Callable[[], dt.datetime] = _utc_now) -> None:
        self._state, self._clock = state or MemoryState(), clock
        self._start_revision = self._state.revision
        self._work = copy.deepcopy(self._state.data)

    # --- transactions -------------------------------------------------------------------------------

    async def commit(self) -> None:
        if self._state.revision != self._start_revision:
            raise PersistenceConflict(CONCURRENT_CHANGE, "Another change was saved at the same time; nothing was changed. Reload and retry.")
        self._state.data = self._work
        self._state.revision += 1
        self._start_revision = self._state.revision
        self._work = copy.deepcopy(self._state.data)

    async def rollback(self) -> None:
        self._start_revision = self._state.revision
        self._work = copy.deepcopy(self._state.data)

    # --- rosters ------------------------------------------------------------------------------------

    def _check_unique(self, roster: RosterRecord, skip: uuid.UUID | None = None) -> None:
        for other in self._work.rosters.values():
            if other.roster_id == skip or (other.institution_id, other.period_code) != (roster.institution_id, roster.period_code):
                continue
            if other.version == roster.version and other.roster_id != roster.roster_id:
                raise PersistenceConflict("ROSTER_VERSION_EXISTS", f"Version {roster.version} of this period already exists.")
            both_working = other.status in WORKING and roster.status in WORKING
            both_published = other.status is RosterStatus.PUBLISHED and roster.status is RosterStatus.PUBLISHED
            if both_working or both_published:
                raise PersistenceConflict(ACTIVE_ROSTER_EXISTS,
                                          f"Period {roster.period_code} already has a {other.status.value} roster.",
                                          {"roster_id": str(other.roster_id), "status": other.status.value})

    @staticmethod
    def _check_assignments(assignments: Sequence[AssignmentRecord]) -> None:
        seen = set()
        for a in assignments:
            if (a.session_id, a.staff_id) in seen:
                raise PersistenceConflict(STAFF_DOUBLE_BOOKED, f"Staff {a.staff_id} holds two duties in session {a.session_id}.")
            seen.add((a.session_id, a.staff_id))

    async def insert_roster(self, roster: RosterRecord, assignments: Sequence[AssignmentRecord]) -> None:
        self._check_unique(roster)
        self._check_assignments(assignments)
        if roster.status is RosterStatus.PUBLISHED and not (roster.timetable_verified and not roster.synthetic):
            raise PersistenceConflict("PUBLISH_NOT_ALLOWED", "Only verified, non-synthetic rosters can be published.")
        self._work.rosters[roster.roster_id] = roster
        self._work.assignments[roster.roster_id] = list(assignments)

    async def get_roster(self, roster_id: uuid.UUID, institution_id: uuid.UUID, *, lock: bool = False) -> RosterRecord | None:
        found = self._work.rosters.get(roster_id)
        return found if found is not None and found.institution_id == institution_id else None

    async def list_rosters(self, institution_id, *, period_code, status, limit, offset):
        items = [r for r in self._work.rosters.values() if r.institution_id == institution_id
                 and (period_code is None or r.period_code == period_code) and (status is None or r.status is status)]
        items.sort(key=lambda r: (r.period_code, -r.version))
        return items[offset:offset + limit], len(items)

    async def latest_version(self, institution_id: uuid.UUID, period_code: str) -> int:
        return max((r.version for r in self._work.rosters.values()
                    if (r.institution_id, r.period_code) == (institution_id, period_code)), default=0)

    async def get_assignments(self, roster_id: uuid.UUID) -> list[AssignmentRecord]:
        return list(self._work.assignments.get(roster_id, ()))

    async def update_roster(self, roster_id: uuid.UUID, expected_row_version: int, **changes: Any) -> RosterRecord:
        current = self._work.rosters.get(roster_id)
        if current is None or current.row_version != expected_row_version:
            raise PersistenceConflict(STALE_ROSTER, "The roster was changed by someone else; reload it.")
        updated = replace(current, **changes, row_version=current.row_version + 1, updated_at=self._clock())
        self._check_unique(updated, skip=roster_id)
        if updated.status is RosterStatus.PUBLISHED and not (updated.timetable_verified and not updated.synthetic
                                                             and updated.published_timetable_reference):
            raise PersistenceConflict("PUBLISH_NOT_ALLOWED", "Only verified, non-synthetic rosters can be published.")
        self._work.rosters[roster_id] = updated
        return updated

    async def apply_assignment_change(self, roster_id, removed, added) -> None:
        current = list(self._work.assignments.get(roster_id, ()))
        for a in removed:
            if a not in current:
                raise PersistenceConflict(STALE_ROSTER, f"Duty {a.session_id}/{a.venue_id} {a.role} is no longer in the roster.")
            current.remove(a)
        current.extend(added)
        self._check_assignments(current)
        self._work.assignments[roster_id] = current

    async def overlapping_assignments(self, institution_id, staff_ids, starts_at, ends_at, exclude_period_code):
        staff, found = set(staff_ids), []
        for roster in self._work.rosters.values():
            if roster.institution_id != institution_id or roster.period_code == exclude_period_code \
                    or roster.status not in (*WORKING, RosterStatus.PUBLISHED):
                continue
            for a in self._work.assignments.get(roster.roster_id, ()):
                if a.staff_id in staff and overlaps(a.starts_at, a.ends_at, starts_at, ends_at):
                    found.append((roster.roster_id, roster.period_code, a))
        return found

    # --- audit trail -------------------------------------------------------------------------------

    async def add_event(self, roster_id, action, from_status, to_status, actor_id, reason, details) -> EventRecord:
        event = EventRecord(self._work.next_event_id, roster_id, action, from_status, to_status, actor_id,
                            self._clock(), reason, dict(details))
        self._work.next_event_id += 1
        self._work.events.append(event)
        return event

    async def list_events(self, roster_id: uuid.UUID) -> list[EventRecord]:
        return [e for e in self._work.events if e.roster_id == roster_id]

    # --- change requests ---------------------------------------------------------------------------

    async def insert_change_request(self, request: ChangeRequestRecord) -> None:
        self._work.requests[request.request_id] = request

    async def get_change_request(self, request_id, institution_id) -> ChangeRequestRecord | None:
        found = self._work.requests.get(request_id)
        return found if found is not None and found.institution_id == institution_id else None

    async def list_change_requests(self, roster_id: uuid.UUID) -> list[ChangeRequestRecord]:
        return sorted((r for r in self._work.requests.values() if r.roster_id == roster_id), key=lambda r: r.requested_at)

    async def update_change_request(self, request_id, expected_status, **changes) -> ChangeRequestRecord:
        current = self._work.requests.get(request_id)
        if current is None or current.status is not expected_status:
            raise PersistenceConflict(STALE_ROSTER, "The request was changed by someone else; reload it.")
        updated = replace(current, **changes)
        self._work.requests[request_id] = updated
        return updated

    async def mark_open_requests_stale(self, roster_id: uuid.UUID, except_request_id: uuid.UUID) -> int:
        count = 0
        for rid, req in list(self._work.requests.items()):
            if req.roster_id == roster_id and rid != except_request_id and req.status in OPEN_CHANGE:
                self._work.requests[rid] = replace(req, status=ChangeStatus.STALE)
                count += 1
        return count

    # --- seating drafts ----------------------------------------------------------------------------

    async def latest_seating_version(self, institution_id: uuid.UUID, period_code: str) -> int:
        return max((d.version for d in self._work.seating.values()
                    if (d.institution_id, d.period_code) == (institution_id, period_code)), default=0)

    async def insert_seating_draft(self, draft: SeatingDraftRecord, seats: Sequence[SeatRecord]) -> None:
        seen_student, seen_seat = set(), set()
        for s in seats:
            if (s.session_id, s.student_id) in seen_student or (s.session_id, s.venue_id, s.physical_seat_number) in seen_seat:
                raise PersistenceConflict("SEAT_DOUBLE_BOOKED", "A student or a seat appears twice in the seating draft.")
            seen_student.add((s.session_id, s.student_id))
            seen_seat.add((s.session_id, s.venue_id, s.physical_seat_number))
        if draft.synthetic and (draft.seat_state == "VERIFIED" or any(s.verification_status == "VERIFIED" for s in seats)):
            raise PersistenceConflict("SEATS_NOT_VERIFIABLE", "Synthetic seating can never be VERIFIED.")
        for key, old in list(self._work.seating.items()):
            if (old.institution_id, old.period_code) == (draft.institution_id, draft.period_code) \
                    and old.status is SeatingDraftStatus.DRAFT:
                self._work.seating[key] = replace(old, status=SeatingDraftStatus.SUPERSEDED)
        self._work.seating[draft.draft_id] = draft
        self._work.seats[draft.draft_id] = list(seats)

    async def get_seating_draft(self, draft_id, institution_id) -> SeatingDraftRecord | None:
        found = self._work.seating.get(draft_id)
        return found if found is not None and found.institution_id == institution_id else None

    async def list_seating_drafts(self, institution_id, *, period_code, limit, offset):
        items = [d for d in self._work.seating.values() if d.institution_id == institution_id
                 and (period_code is None or d.period_code == period_code)]
        items.sort(key=lambda d: (d.period_code, -d.version))
        return items[offset:offset + limit], len(items)

    async def get_seats(self, draft_id: uuid.UUID) -> list[SeatRecord]:
        return list(self._work.seats.get(draft_id, ()))
