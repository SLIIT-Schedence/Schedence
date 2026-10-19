"""CP-SAT staff duty allocation: hard constraints, status handling and validated rosters."""

from __future__ import annotations

from collections import Counter

import pytest

pytest.importorskip("ortools", reason="install the examination service [solver] extra")

from ortools.sat.python import cp_model

from app.duty_allocation import (
    DutyAllocationStatus, DutyAssignment, DutyRole, StaffAvailability, StaffRestriction, StaffUnavailability,
    allocate_duties, validate_allocation,
)
from app.duty_allocation.eligibility import ROOM_SHORTAGE, TIME_SHORTAGE
from app.scheduling.policy import SolverSettings
from duty_allocation_support import INV, SUP, at, limits, make_input, member, open_policy, session, sid, team

S, I = DutyRole.SUPERVISOR, DutyRole.INVIGILATOR
OK = (DutyAllocationStatus.OPTIMAL, DutyAllocationStatus.FEASIBLE)
FAST = SolverSettings(time_limit_seconds=10)


def solve(data, policy=None, limit=None, settings=FAST):
    return allocate_duties(data, policy or open_policy(), limit or limits(), settings)


def assert_valid(result, data, policy=None, limit=None):
    assert result.status in OK, (result.status, result.violations, result.diagnostics)
    assert validate_allocation(data, policy or open_policy(), limit or limits(), result.assignments) == ()


def roster(result) -> dict:
    return {(a.session_id, a.venue_id, a.role): sorted(x.staff_id for x in result.assignments
                                                      if (x.session_id, x.venue_id, x.role) == (a.session_id, a.venue_id, a.role))
            for a in result.assignments}


# --- Successful allocation --------------------------------------------------------------------

def test_fills_every_required_position():
    data = make_input([session("X1", at(8, 30), at(10, 30), "R1", "R2")], team(8),
                      needs={("X1", "R1"): (1, 1), ("X1", "R2"): (2, 2)})
    result = solve(data)
    assert_valid(result, data)
    counts = Counter((a.venue_id, a.role) for a in result.assignments)
    assert counts == {("R1", S): 1, ("R1", I): 1, ("R2", S): 2, ("R2", I): 2}
    assert result.input_fingerprint == "fp-1" and result.solver_version == "9.15.6755"


def test_result_is_reproducible_with_fixed_seed():
    data = make_input([session("X1", at(8, 30), at(10, 30), "R1", "R2")], team(10))
    assert solve(data).assignments == solve(data).assignments


def test_zero_requirement_room_gets_no_staff():
    data = make_input([session("X1", at(8, 30), at(10, 30), "R1")], team(2), need=(0, 0))
    result = solve(data)
    assert result.status in OK and result.assignments == ()


# --- Overlaps, simultaneous rooms and breaks ----------------------------------------------------

def test_simultaneous_rooms_use_different_people():
    data = make_input([session("X1", at(8, 30), at(10, 30), "R1", "R2", "R3")], team(6))
    result = solve(data)
    assert_valid(result, data)
    assert len({a.staff_id for a in result.assignments}) == 6


def test_overlapping_exams_never_share_staff():
    sessions = [session("X1", at(8, 30), at(10, 30), "R1"), session("X2", at(10), at(12), "R2")]
    data = make_input(sessions, team(4))
    result = solve(data)
    assert_valid(result, data)
    x1 = {a.staff_id for a in result.assignments if a.session_id == "X1"}
    x2 = {a.staff_id for a in result.assignments if a.session_id == "X2"}
    assert not x1 & x2


def test_separate_sessions_can_reuse_staff():
    sessions = [session("X1", at(8, 30), at(10, 30), "R1"), session("X2", at(13), at(15), "R2")]
    data = make_input(sessions, team(2))
    assert_valid(solve(data), data)


def test_minimum_break_is_respected():
    sessions = [session("X1", at(8, 30), at(10, 30), "R1"), session("X2", at(10, 45), at(12), "R2")]
    data = make_input(sessions, team(2))
    assert solve(data, open_policy(gap=15)).status in OK
    assert solve(data, open_policy(gap=30)).status is DutyAllocationStatus.INFEASIBLE
    data4 = make_input(sessions, team(4))
    result = solve(data4, open_policy(gap=30))
    assert_valid(result, data4, open_policy(gap=30))


def test_one_person_never_takes_two_roles_in_a_session():
    data = make_input([session("X1", at(8, 30), at(10, 30), "R1")], [member(1)])
    result = solve(data)
    assert result.status is DutyAllocationStatus.INFEASIBLE and result.assignments == ()


# --- Eligibility: rank, availability, unavailability, restrictions ---------------------------------

def test_rank_and_role_eligibility_are_respected():
    staff = [member(1, rank="Rank A"), member(2, rank="Rank B"), member(3, INV, rank="Rank A")]
    policy = open_policy(rank_order=("Rank A", "Rank B"), supervisor_min="Rank A")
    data = make_input([session("X1", at(8, 30), at(10, 30), "R1")], staff)
    result = solve(data, policy)
    assert_valid(result, data, policy)
    assert roster(result)[("X1", "R1", S)] == [sid(1)]


def test_unavailable_staff_are_not_assigned():
    sessions = [session("X1", at(8, 30), at(10, 30), "R1")]
    off = [StaffUnavailability(sid(1), at(8), at(12), "leave"), StaffUnavailability(sid(2), at(10), at(11))]
    data = make_input(sessions, team(4), unavailability=off)
    result = solve(data)
    assert_valid(result, data)
    assert {a.staff_id for a in result.assignments} == {sid(3), sid(4)}


def test_declared_availability_is_respected():
    sessions = [session("X1", at(8, 30), at(10, 30), "R1"), session("X2", at(13), at(15), "R2")]
    windows = [StaffAvailability(sid(1), at(8), at(11)), StaffAvailability(sid(2), at(8), at(11))]
    data = make_input(sessions, team(4), availability=windows)
    result = solve(data)
    assert_valid(result, data)
    assert {a.staff_id for a in result.assignments if a.session_id == "X2"} == {sid(3), sid(4)}


def test_restrictions_are_respected():
    policy = open_policy(restrictions=[StaffRestriction(sid(1), "module lecturer", module_code="IT1010")])
    data = make_input([session("X1", at(8, 30), at(10, 30), "R1")], team(3))
    result = solve(data, policy)
    assert_valid(result, data, policy)
    assert sid(1) not in {a.staff_id for a in result.assignments}


# --- Duty limits -------------------------------------------------------------------------------

def test_max_duties_per_day():
    sessions = [session("X1", at(8, 30), at(10, 30), "R1"), session("X2", at(13), at(15), "R2")]
    data = make_input(sessions, team(2))
    assert solve(data, limit=limits(per_day=1)).status is DutyAllocationStatus.INFEASIBLE
    data4 = make_input(sessions, team(4))
    result = solve(data4, limit=limits(per_day=1))
    assert_valid(result, data4, limit=limits(per_day=1))
    assert max(Counter(a.staff_id for a in result.assignments).values()) == 1


def test_max_duties_per_period_spans_days():
    sessions = [session(f"X{d}", at(9, day=d), at(11, day=d), f"R{d}") for d in range(3)]
    assert solve(make_input(sessions, team(2)), limit=limits(per_period=2)).status is DutyAllocationStatus.INFEASIBLE
    data = make_input(sessions, team(3))
    result = solve(data, limit=limits(per_period=2))
    assert_valid(result, data, limit=limits(per_period=2))
    assert max(Counter(a.staff_id for a in result.assignments).values()) <= 2


# --- Shortages and infeasibility ------------------------------------------------------------------

def test_room_shortage_is_infeasible_before_solving():
    data = make_input([session("X1", at(8, 30), at(10, 30), "R1")], [member(1, SUP), member(2, INV)], need=(2, 1))
    result = solve(data)
    assert result.status is DutyAllocationStatus.INFEASIBLE
    assert [v.constraint for v in result.violations] == [ROOM_SHORTAGE]
    assert result.solver_version is None  # proven by the pre-check, solver not run


def test_simultaneous_shortage_is_infeasible_before_solving():
    data = make_input([session("X1", at(8, 30), at(10, 30), "R1", "R2")], team(3))
    result = solve(data)
    assert result.status is DutyAllocationStatus.INFEASIBLE
    assert TIME_SHORTAGE in {v.constraint for v in result.violations}


def test_solver_proves_infeasibility_the_precheck_cannot_see():
    # Pools look sufficient per room and per time, but a 1-duty period limit leaves too few people.
    sessions = [session("X1", at(8, 30), at(10, 30), "R1"), session("X2", at(13), at(15), "R2")]
    result = solve(make_input(sessions, team(3)), limit=limits(per_period=1))
    assert result.status is DutyAllocationStatus.INFEASIBLE and result.violations == ()
    assert result.diagnostics and result.solver_version == "9.15.6755"


# --- Status handling ----------------------------------------------------------------------------

def test_invalid_settings_are_model_invalid():
    data = make_input([session("X1", at(8, 30), at(10, 30), "R1")], team(2))
    result = solve(data, settings=SolverSettings(time_limit_seconds=0))
    assert result.status is DutyAllocationStatus.MODEL_INVALID and result.violations


def test_wrong_argument_types_are_model_invalid():
    data = make_input([session("X1", at(8, 30), at(10, 30), "R1")], team(2))
    assert allocate_duties(data, {"rank_order": []}, limits()).status is DutyAllocationStatus.MODEL_INVALID
    assert allocate_duties("data", open_policy(), limits()).status is DutyAllocationStatus.MODEL_INVALID


def test_unknown_status_is_reported(monkeypatch):
    monkeypatch.setattr(cp_model.CpSolver, "status_name", lambda self, status=None: "UNKNOWN")
    result = solve(make_input([session("X1", at(8, 30), at(10, 30), "R1")], team(2)))
    assert result.status is DutyAllocationStatus.UNKNOWN and result.assignments == ()
    assert "longer limit" in result.diagnostics[0]


def test_invalid_model_is_reported(monkeypatch):
    monkeypatch.setattr(cp_model.CpModel, "validate", lambda self: "synthetic model error")
    result = solve(make_input([session("X1", at(8, 30), at(10, 30), "R1")], team(2)))
    assert result.status is DutyAllocationStatus.MODEL_INVALID and result.diagnostics == ("synthetic model error",)


def test_roster_rejected_by_validator_is_never_returned(monkeypatch):
    import app.duty_allocation.model as duty_model
    bad = (DutyAssignment("X1", "R1", sid(1), S), DutyAssignment("X1", "R1", sid(1), I))
    monkeypatch.setattr(duty_model, "decode_assignments", lambda model, solver: bad)
    result = solve(make_input([session("X1", at(8, 30), at(10, 30), "R1")], team(2)))
    assert result.status is DutyAllocationStatus.MODEL_INVALID and result.assignments == ()
    assert "D3" in {v.constraint for v in result.violations}


# --- Larger synthetic period ------------------------------------------------------------------------

def test_multi_day_period_with_limits_and_breaks():
    sessions = []
    for day in range(3):
        sessions.append(session(f"D{day}AM", at(8, 30, day), at(11, 30, day), "A1", "A2", "A3", module=f"M{day}A"))
        sessions.append(session(f"D{day}PM", at(13, 30, day), at(16, 30, day), "B1", "B2", module=f"M{day}B"))
    staff = [member(n, rank="Rank A" if n <= 8 else "Rank B") for n in range(1, 21)]
    policy = open_policy(gap=60, rank_order=("Rank A", "Rank B"), supervisor_min="Rank A")
    limit = limits(per_day=1, per_period=3)
    data = make_input(sessions, staff, unavailability=[StaffUnavailability(sid(1), at(0), at(23, 59))])
    result = solve(data, policy, limit)
    assert_valid(result, data, policy, limit)
    assert len(result.assignments) == 30  # 5 rooms a day x 3 days x (1 Supervisor + 1 Invigilator)
    assert sid(1) not in {a.staff_id for a in result.assignments if a.session_id.startswith("D0")}
