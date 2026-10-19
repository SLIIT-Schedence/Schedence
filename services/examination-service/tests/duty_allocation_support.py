"""Synthetic builders for Member 3 duty-allocation tests. Not SLIIT data or policy."""

from __future__ import annotations

import datetime as dt
import uuid

from app.duty_allocation import (
    DutyAllocationInput, DutyLimitPolicy, DutyRole, EligibilityPolicy, RoleRule, RoomDutyRequirement,
    StaffMember,
)
from app.scheduling.types import DraftTimetable, ExamSession, RoomAllocation, SeatAllocation

DAY = dt.date(2026, 11, 2)
BOTH = (DutyRole.SUPERVISOR, DutyRole.INVIGILATOR)
INV = (DutyRole.INVIGILATOR,)
SUP = (DutyRole.SUPERVISOR,)


def at(hour: int, minute: int = 0, day: int = 0) -> dt.datetime:
    return dt.datetime.combine(DAY + dt.timedelta(days=day), dt.time(hour, minute))


def sid(n: int) -> str:
    return str(uuid.UUID(int=n))


def member(n: int, roles=BOTH, rank: str | None = None) -> StaffMember:
    return StaffMember(sid(n), f"EMP{n:03}", f"Staff {n}", rank, roles)


def team(count: int, roles=BOTH) -> list[StaffMember]:
    return [member(n, roles) for n in range(1, count + 1)]


def session(session_id: str, start: dt.datetime, end: dt.datetime, *venues: str, module="IT1010") -> ExamSession:
    rooms = tuple(RoomAllocation(v, (SeatAllocation(f"{session_id}-{v}", 1),)) for v in venues)
    return ExamSession(session_id, module, "TEST", start, end, rooms)


def make_input(sessions, staff, need=(1, 1), unavailability=(), availability=(), needs=None) -> DutyAllocationInput:
    """need applies to every room; needs={(session, venue): (sup, inv)} overrides single rooms."""
    needs = needs or {}
    timetable = DraftTimetable("TEST", "fp-1", tuple(sessions))
    reqs = tuple(RoomDutyRequirement(s.session_id, r.venue_id, *needs.get((s.session_id, r.venue_id), need))
                 for s in sessions for r in s.rooms)
    return DutyAllocationInput(timetable, tuple(staff), reqs, tuple(unavailability), tuple(availability))


def open_policy(gap=0, restrictions=(), rank_order=(), supervisor_min=None) -> EligibilityPolicy:
    rules = (RoleRule(DutyRole.SUPERVISOR, supervisor_min, supervisor_min is None),
             RoleRule(DutyRole.INVIGILATOR, None, True))
    return EligibilityPolicy(tuple(rank_order), rules, gap, "synthetic test policy", tuple(restrictions))


def limits(per_day=None, per_period=None) -> DutyLimitPolicy:
    return DutyLimitPolicy(per_day, per_period, "synthetic test limits")
