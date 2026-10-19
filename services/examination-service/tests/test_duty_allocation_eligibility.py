"""Per-room eligibility, session conflicts and early shortage checks."""

from __future__ import annotations

import datetime as dt
import uuid

import pytest

from app.duty_allocation import (
    DutyAllocationInput, DutyRole, EligibilityPolicy, RoleRule, RoomDutyRequirement, StaffAvailability,
    StaffMember, StaffRestriction, StaffUnavailability, build_eligibility,
)
from app.duty_allocation.eligibility import ROOM_SHORTAGE, TIME_SHORTAGE
from app.scheduling.contracts import SchedulingValidationError
from app.scheduling.types import DraftTimetable, ExamSession, RoomAllocation, SeatAllocation

DAY = dt.date(2026, 11, 2)
BOTH = (DutyRole.SUPERVISOR, DutyRole.INVIGILATOR)
INV = (DutyRole.INVIGILATOR,)


def at(hour: int, minute: int = 0) -> dt.datetime:
    return dt.datetime.combine(DAY, dt.time(hour, minute))


def sid(n: int) -> str:
    return str(uuid.UUID(int=n))


def member(n: int, roles=BOTH) -> StaffMember:
    return StaffMember(sid(n), f"EMP{n:03}", f"Staff {n}", None, roles)


def policy(gap=0, restrictions=()) -> EligibilityPolicy:
    rules = (RoleRule(DutyRole.SUPERVISOR, None, True), RoleRule(DutyRole.INVIGILATOR, None, True))
    return EligibilityPolicy((), rules, gap, "test", tuple(restrictions))


def session(session_id: str, start: dt.datetime, end: dt.datetime, *venues: str, module="IT1010") -> ExamSession:
    rooms = tuple(RoomAllocation(v, (SeatAllocation(f"{session_id}-{v}", 1),)) for v in venues)
    return ExamSession(session_id, module, "TEST", start, end, rooms)


def make_input(sessions, staff, need=(1, 1), unavailability=(), availability=()) -> DutyAllocationInput:
    timetable = DraftTimetable("TEST", "fp-1", tuple(sessions))
    reqs = tuple(RoomDutyRequirement(s.session_id, r.venue_id, *need) for s in sessions for r in s.rooms)
    return DutyAllocationInput(timetable, tuple(staff), reqs, tuple(unavailability), tuple(availability))


def codes(report) -> list[str]:
    return [v.constraint for v in report.violations]


def test_all_suitable_staff_are_eligible_for_their_roles():
    data = make_input([session("X1", at(8, 30), at(10, 30), "R1")], [member(1), member(2, INV)])
    room = build_eligibility(data, policy()).room("X1", "R1")
    assert room.staff_for(DutyRole.SUPERVISOR) == (sid(1),)
    assert room.staff_for(DutyRole.INVIGILATOR) == (sid(1), sid(2))
    assert [(e.staff_id, e.role) for e in room.exclusions] == [(sid(2), DutyRole.SUPERVISOR)]
    assert "rank rules" in room.exclusions[0].reason


def test_unavailability_overlapping_a_session_excludes_staff():
    off = StaffUnavailability(sid(1), at(10), at(12), "medical leave")
    data = make_input([session("X1", at(8, 30), at(10, 30), "R1"), session("X2", at(13), at(15), "R2")],
                      [member(1), member(2), member(3)], unavailability=[off])
    report = build_eligibility(data, policy())
    assert sid(1) not in report.room("X1", "R1").staff_for(DutyRole.INVIGILATOR)
    assert "medical leave" in report.room("X1", "R1").exclusions[0].reason
    assert sid(1) in report.room("X2", "R2").staff_for(DutyRole.INVIGILATOR)


def test_unavailability_touching_a_session_does_not_exclude():
    off = StaffUnavailability(sid(1), at(10, 30), at(12))
    data = make_input([session("X1", at(8, 30), at(10, 30), "R1")], [member(1)], unavailability=[off])
    assert sid(1) in build_eligibility(data, policy()).room("X1", "R1").staff_for(DutyRole.SUPERVISOR)


def test_declared_availability_limits_staff_to_those_windows():
    morning = StaffAvailability(sid(1), at(8), at(12))
    data = make_input([session("X1", at(8, 30), at(10, 30), "R1"), session("X2", at(13), at(15), "R2")],
                      [member(1), member(2), member(3)], availability=[morning])
    report = build_eligibility(data, policy())
    assert sid(1) in report.room("X1", "R1").staff_for(DutyRole.SUPERVISOR)
    assert sid(1) not in report.room("X2", "R2").staff_for(DutyRole.SUPERVISOR)
    assert sid(2) in report.room("X2", "R2").staff_for(DutyRole.SUPERVISOR)  # no windows: available


def test_configured_restrictions_exclude_by_module_role_or_session():
    restrictions = [StaffRestriction(sid(1), "module lecturer", module_code="IT1010"),
                    StaffRestriction(sid(2), "no supervising", role=DutyRole.SUPERVISOR)]
    data = make_input([session("X1", at(8, 30), at(10, 30), "R1"),
                       session("X2", at(13), at(15), "R2", module="IT2020")], [member(1), member(2), member(3)])
    report = build_eligibility(data, policy(restrictions=restrictions))
    x1, x2 = report.room("X1", "R1"), report.room("X2", "R2")
    assert x1.staff_for(DutyRole.SUPERVISOR) == (sid(3),)
    assert x1.staff_for(DutyRole.INVIGILATOR) == (sid(2), sid(3))
    assert x2.staff_for(DutyRole.SUPERVISOR) == (sid(1), sid(3))
    assert any("module lecturer" in e.reason for e in x1.exclusions)


def test_overlapping_sessions_are_reported_as_conflicts():
    sessions = [session("X1", at(8, 30), at(10, 30), "R1"), session("X2", at(10), at(12), "R2"),
                session("X3", at(10, 30), at(12), "R3"), session("X4", at(13), at(15), "R4")]
    report = build_eligibility(make_input(sessions, [member(n) for n in range(1, 9)]), policy())
    assert report.session_conflicts == (("X1", "X2"), ("X2", "X3"))


def test_minimum_gap_turns_close_sessions_into_conflicts():
    sessions = [session("X1", at(8, 30), at(10, 30), "R1"), session("X3", at(10, 45), at(12), "R3")]
    staff = [member(n) for n in range(1, 5)]
    assert build_eligibility(make_input(sessions, staff), policy(gap=0)).session_conflicts == ()
    # The sessions are 15 minutes apart: a 15-minute minimum is met, 16 is not.
    assert build_eligibility(make_input(sessions, staff), policy(gap=15)).session_conflicts == ()
    assert build_eligibility(make_input(sessions, staff), policy(gap=16)).session_conflicts == (("X1", "X3"),)


def test_room_shortage_is_reported_per_role():
    data = make_input([session("X1", at(8, 30), at(10, 30), "R1")], [member(1), member(2, INV)], need=(2, 1))
    report = build_eligibility(data, policy())
    assert codes(report) == [ROOM_SHORTAGE]
    assert "needs 2 SUPERVISOR, only 1 eligible" in report.violations[0].message
    assert not report.is_feasible_so_far


def test_parallel_rooms_need_distinct_people():
    # Each room alone is coverable by 2 people, but 2 rooms at once need 4.
    data = make_input([session("X1", at(8, 30), at(10, 30), "R1", "R2")], [member(1), member(2), member(3)])
    report = build_eligibility(data, policy())
    assert codes(report) == [TIME_SHORTAGE]
    assert "need 4 duty staff in total at once, only 3 eligible" in report.violations[0].message


def test_overlapping_sessions_share_one_staff_pool():
    sessions = [session("X1", at(8, 30), at(10, 30), "R1"), session("X2", at(9), at(11), "R2")]
    assert codes(build_eligibility(make_input(sessions, [member(n) for n in range(1, 4)]), policy())) == [TIME_SHORTAGE]
    assert build_eligibility(make_input(sessions, [member(n) for n in range(1, 5)]), policy()).is_feasible_so_far


def test_separate_sessions_can_reuse_the_same_people():
    sessions = [session("X1", at(8, 30), at(10, 30), "R1"), session("X2", at(13), at(15), "R2")]
    report = build_eligibility(make_input(sessions, [member(1), member(2)]), policy())
    assert report.is_feasible_so_far and report.session_conflicts == ()


def test_rank_rules_apply_even_to_staff_not_built_by_import():
    ranked = EligibilityPolicy(("Rank A", "Rank B"), (RoleRule(DutyRole.SUPERVISOR, "Rank A", False),
                                                     RoleRule(DutyRole.INVIGILATOR, None, True)), 0, "test")
    junior = StaffMember(sid(1), "EMP001", "Staff 1", "Rank B", BOTH)  # roles claim both
    data = make_input([session("X1", at(8, 30), at(10, 30), "R1")], [junior, member(2)])
    room = build_eligibility(data, ranked).room("X1", "R1")
    assert sid(1) not in room.staff_for(DutyRole.SUPERVISOR)
    assert any("below the SUPERVISOR minimum" in e.reason for e in room.exclusions)


def test_build_eligibility_rejects_wrong_arguments():
    data = make_input([session("X1", at(8, 30), at(10, 30), "R1")], [member(1)])
    with pytest.raises(SchedulingValidationError):
        build_eligibility(data, {"rank_order": []})
    with pytest.raises(SchedulingValidationError):
        build_eligibility("data", policy())
