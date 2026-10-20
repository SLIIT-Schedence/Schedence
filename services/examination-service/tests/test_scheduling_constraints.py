"""H1-H11 behavior against real CP-SAT using small synthetic cases."""

from __future__ import annotations

import datetime as dt
from dataclasses import replace

import pytest

pytest.importorskip("ortools", reason="install the examination service [solver] extra")

from app.scheduling import SchedulingPolicy, SolverSettings, generate_timetable, validate_timetable
from app.scheduling.inputs import SnapshotError
from app.scheduling.types import SolveStatus
from app.sources.types import VenueBlock
from scheduling_support import MONDAY, make_case


def solved(snapshot, settings=SolverSettings(time_limit_seconds=10)):
    result = generate_timetable(snapshot, settings)
    assert result.status in (SolveStatus.OPTIMAL, SolveStatus.FEASIBLE), result
    assert result.timetable is not None
    assert not validate_timetable(snapshot, result.timetable)
    return result.timetable


async def test_h1_repeat_student_no_clash_and_two_exams_per_day_are_allowed():
    snapshot, _, _ = await make_case(candidates=(("IT30000001",), ("IT30000001",)))
    assert snapshot.registrations[1].registration_type == "repeat"
    draft = solved(snapshot)
    first, second = sorted(draft.sessions, key=lambda s: s.start)
    assert first.start.date() == second.start.date() == MONDAY
    assert second.start - first.end >= dt.timedelta(minutes=60)


@pytest.mark.parametrize("break_minutes,expected", [(0, SolveStatus.OPTIMAL), (60, SolveStatus.INFEASIBLE)])
async def test_h1_minimum_break_is_configurable_and_includes_reading(break_minutes, expected):
    policy = SchedulingPolicy(permitted_start_times=(dt.time(8, 30), dt.time(10)), minimum_break_minutes=break_minutes)
    snapshot, _, _ = await make_case(candidates=(("IT30000001",), ("IT30000001",)),
                                      durations=(80, 80), readings=(10, 10), policy=policy)
    result = generate_timetable(snapshot)
    assert result.status == expected
    if result.timetable:
        assert not validate_timetable(snapshot, result.timetable)


@pytest.mark.parametrize("break_minutes", [0, 30, 60, 90])
@pytest.mark.parametrize("gap_shortfall", [0, 1])
async def test_h1_break_boundaries_are_hard_even_at_zero(break_minutes, gap_shortfall):
    # Both papers last 80 minutes + 10 reading minutes. With two compatible rooms,
    # only the shared student's hard constraint can make these starts impossible.
    start = dt.datetime.combine(MONDAY, dt.time(8, 30))
    second = start + dt.timedelta(minutes=90 + break_minutes - gap_shortfall)
    policy = SchedulingPolicy(permitted_start_times=(start.time(), second.time()),
                              minimum_break_minutes=break_minutes)
    snapshot, _, _ = await make_case(candidates=(("IT30000001",), ("IT30000001",)),
                                      durations=(80, 80), readings=(10, 10), policy=policy)
    result = generate_timetable(snapshot)
    if gap_shortfall:
        assert result.status == SolveStatus.INFEASIBLE and result.timetable is None
    else:
        assert result.status in (SolveStatus.OPTIMAL, SolveStatus.FEASIBLE)
        first_exam, next_exam = sorted(result.timetable.sessions, key=lambda s: s.start)
        assert next_exam.start - first_exam.end == dt.timedelta(minutes=break_minutes)
        assert not validate_timetable(snapshot, result.timetable)


async def test_h1_zero_break_never_disables_student_no_overlap():
    policy = SchedulingPolicy(permitted_start_times=(dt.time(8, 30),), minimum_break_minutes=0)
    snapshot, _, _ = await make_case(candidates=(("IT30000001",), ("IT30000001",)), policy=policy)
    # There are enough separate rooms for simultaneous papers, but one student
    # is registered for both (one is a repeat), so a clash-free sitting is impossible.
    assert len(snapshot.venues) == 2
    result = generate_timetable(snapshot)
    assert result.status == SolveStatus.INFEASIBLE and result.timetable is None


async def test_h2_h10_multroom_uses_effective_capacity_and_unique_logical_seats():
    # Legacy seed seats = 4 per room, effective seats = 2 at the configured 25%.
    ids = tuple(f"IT3000000{i}" for i in range(1, 5))
    snapshot, _, _ = await make_case(candidates=(ids,), full_capacities=(8, 8), percentage=25)
    session = solved(snapshot).sessions[0]
    assert len(session.rooms) == 2
    assert all(len(room.seats) == 2 for room in session.rooms)
    assert {seat.student_id for room in session.rooms for seat in room.seats} == set(ids)
    assert all({seat.seat_number for seat in room.seats} == {1, 2} for room in session.rooms)


@pytest.mark.parametrize("exam_type,venue_type,allowed", [
    ("written", "lecture_hall", True), ("written", "smart_classroom", True),
    ("written", "computer_lab", False), ("computer_based", "computer_lab", True),
    ("computer_based", "byod_lab", False), ("computer_based", "lecture_hall", False),
    ("hybrid", "byod_lab", True), ("hybrid", "computer_lab", True),
])
async def test_h3_approved_compatibility(exam_type, venue_type, allowed):
    snapshot, _, _ = await make_case(exam_types=(exam_type,), venue_types=(venue_type,))
    result = generate_timetable(snapshot)
    assert result.status == (SolveStatus.OPTIMAL if allowed else SolveStatus.INFEASIBLE)


async def test_h3_module_override_is_configurable():
    policy = SchedulingPolicy(module_venue_overrides=(("IT1001", ("computer_lab",)),))
    snapshot, _, _ = await make_case(exam_types=("hybrid",), venue_types=("byod_lab", "computer_lab"), policy=policy)
    assert solved(snapshot).sessions[0].rooms[0].venue_id == "R2"


@pytest.mark.parametrize("source", ["teaching_timetable", "maintenance", "simulated_teaching", "simulated_maintenance"])
async def test_h4_all_availability_sources_are_respected(source):
    block = VenueBlock("R1", MONDAY, dt.time(8, 30), dt.time(12), source, "SIMULATED test block", None)
    snapshot, _, _ = await make_case(venue_types=("lecture_hall",), blocks=(block,))
    assert solved(snapshot).sessions[0].start.time() == dt.time(13, 30)


async def test_h4_overlapping_blocks_are_unioned_and_touching_boundaries_are_allowed():
    blocks = (
        VenueBlock("R1", MONDAY, dt.time(7), dt.time(8), "simulated_teaching", "SIMULATED", None),
        VenueBlock("R1", MONDAY, dt.time(7, 30), dt.time(8, 30), "simulated_maintenance", "SIMULATED", None),
        VenueBlock("R1", MONDAY, dt.time(9, 30), dt.time(17, 30), "simulated_teaching", "SIMULATED", None),
    )
    snapshot, _, _ = await make_case(venue_types=("lecture_hall",), blocks=blocks)
    assert solved(snapshot).sessions[0].start.time() == dt.time(8, 30)


async def test_h4_blocked_all_day_is_infeasible():
    block = VenueBlock("R1", MONDAY, dt.time(8), dt.time(18), "simulated_maintenance", "SIMULATED", None)
    snapshot, _, _ = await make_case(venue_types=("lecture_hall",), blocks=(block,))
    assert generate_timetable(snapshot).status == SolveStatus.INFEASIBLE


@pytest.mark.parametrize("rooms,expected", [(1, SolveStatus.INFEASIBLE), (2, SolveStatus.OPTIMAL)])
async def test_h5_room_overlap_and_parallel_exams_for_different_students(rooms, expected):
    policy = SchedulingPolicy(permitted_start_times=(dt.time(8, 30),))
    snapshot, _, _ = await make_case(candidates=(("IT30000001",), ("IT30000002",)),
                                      venue_types=("lecture_hall",) * rooms, policy=policy)
    assert generate_timetable(snapshot).status == expected


async def test_h6_h7_h8_period_hours_weekends_and_exclusions():
    # Saturday and Sunday disabled, Monday excluded: Tuesday is the only legal day.
    saturday = dt.date(2026, 11, 14)
    snapshot, _, _ = await make_case(start_date=saturday, end_date=MONDAY + dt.timedelta(days=1), excluded=(MONDAY,))
    session = solved(snapshot).sessions[0]
    assert session.start.date() == dt.date(2026, 11, 17)
    assert dt.time(8, 30) <= session.start.time() < session.end.time() <= dt.time(17, 30)


@pytest.mark.parametrize("weekday,flag", [(5, "include_saturdays"), (6, "include_sundays")])
async def test_h8_weekend_flags_are_configurable(weekday, flag):
    day = dt.date(2026, 11, 14) + dt.timedelta(days=weekday - 5)
    disabled, _, _ = await make_case(start_date=day, end_date=day)
    assert generate_timetable(disabled).status == SolveStatus.INFEASIBLE
    enabled, _, _ = await make_case(start_date=day, end_date=day, **{flag: True})
    assert solved(enabled).sessions[0].start.date() == day


@pytest.mark.parametrize("reading,expected", [(0, SolveStatus.OPTIMAL), (10, SolveStatus.INFEASIBLE)])
async def test_h9_reading_time_must_fit_daily_window(reading, expected):
    policy = SchedulingPolicy(permitted_start_times=(dt.time(13, 30),))
    snapshot, _, _ = await make_case(durations=(240,), readings=(reading,), policy=policy)
    assert generate_timetable(snapshot).status == expected


async def test_h9_reading_time_also_reserves_the_room():
    policy = SchedulingPolicy(permitted_start_times=(dt.time(8, 30), dt.time(9, 30)), minimum_break_minutes=0)
    snapshot, _, _ = await make_case(candidates=(("IT30000001",), ("IT30000002",)), durations=(60, 60),
                                      readings=(10, 10), venue_types=("lecture_hall",), policy=policy)
    assert generate_timetable(snapshot).status == SolveStatus.INFEASIBLE


async def test_insufficient_capacity_zero_capacity_and_room_limit_are_infeasible():
    snapshot, _, _ = await make_case(full_capacities=(1, 1))  # no usable seat at all
    assert generate_timetable(snapshot).status == SolveStatus.INFEASIBLE
    # Too few seats for one sitting, and splitting is switched off: refused before solving.
    one = SchedulingPolicy(max_sessions_per_exam=1)
    snapshot, _, _ = await make_case(full_capacities=(2, 2), policy=SchedulingPolicy(max_rooms_per_exam=1))
    with pytest.raises(SnapshotError, match="needs 2 sessions"):
        await make_case(full_capacities=(2, 2), policy=SchedulingPolicy(max_rooms_per_exam=1, max_sessions_per_exam=1))
    with pytest.raises(SnapshotError, match="needs 2 sessions for 3 candidates"):
        await make_case(candidates=(("IT30000001", "IT30000002", "IT30000003"),), full_capacities=(2, 2), policy=one)
    # With the default limit the module is split into two sittings instead.
    result = generate_timetable(snapshot)
    assert result.status in (SolveStatus.OPTIMAL, SolveStatus.FEASIBLE)
    assert [s.session_number for s in result.timetable.sessions] == [1, 2]


async def test_settings_reproduce_a_small_schedule_and_unknown_is_not_infeasible():
    snapshot, _, _ = await make_case()
    first, second = generate_timetable(snapshot), generate_timetable(snapshot)
    assert first.timetable == second.timetable
    assert first.settings.num_search_workers == 1 and first.solver_version == "9.15.6755"
    result = generate_timetable(snapshot, SolverSettings(time_limit_seconds=1e-9))
    assert result.status == SolveStatus.UNKNOWN and result.timetable is None
    assert "proof of infeasibility" in result.diagnostics[0]


async def test_invalid_model_returns_explicit_status_without_a_draft():
    snapshot, _, _ = await make_case()
    bad = replace(snapshot, registrations=())
    result = generate_timetable(bad)
    assert result.status == SolveStatus.MODEL_INVALID and result.timetable is None
    assert any(v.constraint == "H10" for v in result.violations)


@pytest.mark.parametrize("status", list(SolveStatus))
def test_all_cp_sat_statuses_are_distinct(status):
    from ortools.sat.python import cp_model
    solver = cp_model.CpSolver()
    assert SolveStatus(solver.status_name(getattr(cp_model, status.value))) == status
