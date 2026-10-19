"""Capacity-based multiple examination sessions (SYNTHETIC data, no database)."""

from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict
from dataclasses import replace

import pytest

from app.scheduling import SchedulingPolicy, SolverSettings, generate_timetable, validate_timetable
from app.scheduling.evaluation import evaluate_timetable
from app.scheduling.fairness_policy import FairnessPolicy
from app.scheduling.inputs import SnapshotError
from app.scheduling.optimization import optimize_timetable
from app.scheduling.sessions import required_sessions, sitting_capacity
from app.scheduling.types import DraftTimetable, ExamSession, RoomAllocation, SeatAllocation, SolveStatus
from app.sources.types import VenueBlock
from scheduling_support import MONDAY, make_case

pytest.importorskip("ortools")

SIX = tuple(f"IT3000000{i}" for i in range(1, 7))
TWO_DAYS = {"end_date": MONDAY + dt.timedelta(days=1)}  # 4 slots: 08:30 and 13:30 on two days
OK = (SolveStatus.OPTIMAL, SolveStatus.FEASIBLE)


def _by_module(timetable):
    result = defaultdict(list)
    for s in timetable.sessions:
        result[s.module_code].append(s)
    return {code: sorted(sessions, key=lambda s: s.start) for code, sessions in result.items()}


def _seated(session):
    return [seat.student_id for room in session.rooms for seat in room.seats]


def _check_split(snapshot, timetable):
    """Every candidate exactly once; rooms within effective capacity; sessions in order, not overlapping."""
    capacities = {c.venue_id: c.exam_capacity for c in snapshot.capacities.venues}
    for code, sessions in _by_module(timetable).items():
        assert [s.session_number for s in sessions] == list(range(1, len(sessions) + 1))
        assert all(s.session_count == len(sessions) for s in sessions)
        assert all(a.end <= b.start for a, b in zip(sessions, sessions[1:]))
        seated = Counter(student for s in sessions for student in _seated(s))
        expected = {r.student_id for r in snapshot.registrations if r.module_code == code}
        assert seated == Counter({student: 1 for student in expected})
        for s in sessions:
            assert all(len(room.seats) <= capacities[room.venue_id] for room in s.rooms)


# --- Session count -------------------------------------------------------------------------


async def test_enough_capacity_keeps_one_session_and_the_old_session_id():
    snapshot, _, _ = await make_case()
    assert required_sessions(snapshot, snapshot.modules[0]) == 1
    [session] = generate_timetable(snapshot).timetable.sessions
    assert (session.session_id, session.session_number, session.session_count) == ("TEST:IT1001", 1, 1)


async def test_single_session_model_has_no_session_choices():
    from app.scheduling.model import build_hard_model

    snapshot, _, _ = await make_case()
    model, layout, _ = build_hard_model(snapshot)
    assert layout.sessions == {"IT1001": 1} and not layout.assigned
    assert "sits:" not in str(model.proto)


async def test_session_count_uses_the_configurable_seating_percentage():
    # Two rooms of 4 seats: 50% -> 2 + 2 = 4 seats for 6 candidates -> 2 sessions; 100% -> 8 seats -> 1 session.
    half, _, _ = await make_case(candidates=(SIX,), **TWO_DAYS)
    full, _, _ = await make_case(candidates=(SIX,), percentage=100, **TWO_DAYS)
    assert (sitting_capacity(half, half.modules[0]), required_sessions(half, half.modules[0])) == (4, 2)
    assert (sitting_capacity(full, full.modules[0]), required_sessions(full, full.modules[0])) == (8, 1)
    assert len(generate_timetable(full).timetable.sessions) == 1


async def test_three_sessions_when_one_sitting_holds_only_two():
    snapshot, _, _ = await make_case(candidates=(SIX,), policy=SchedulingPolicy(max_rooms_per_exam=1), **TWO_DAYS)
    assert required_sessions(snapshot, snapshot.modules[0]) == 3
    result = generate_timetable(snapshot)
    assert result.status in OK and validate_timetable(snapshot, result.timetable) == ()
    sessions = _by_module(result.timetable)["IT1001"]
    assert [s.session_id for s in sessions] == ["TEST:IT1001:S01", "TEST:IT1001:S02", "TEST:IT1001:S03"]
    assert len({s.start for s in sessions}) == 3
    _check_split(snapshot, result.timetable)


async def test_configured_maximum_sessions_is_enforced_before_solving():
    with pytest.raises(SnapshotError, match="needs 3 sessions for 6 candidates.*maximum is 2"):
        await make_case(candidates=(SIX,), policy=SchedulingPolicy(max_rooms_per_exam=1, max_sessions_per_exam=2),
                        **TWO_DAYS)


async def test_not_enough_permitted_times_for_the_sessions_is_infeasible():
    snapshot, _, _ = await make_case(candidates=(SIX,), policy=SchedulingPolicy(max_rooms_per_exam=1))  # 2 slots
    assert generate_timetable(snapshot).status == SolveStatus.INFEASIBLE


# --- Hard constraints with split modules ---------------------------------------------------------


async def test_split_candidate_never_clashes_with_their_other_exam():
    # One day, two slots. IT1001 needs both slots (4 candidates, 2 seats per sitting). IT30000001 also sits
    # IT1002 (a repeat module), so IT1002 takes one slot and that student must sit IT1001 in the other.
    candidates = (SIX[:4], (SIX[0],))
    snapshot, _, _ = await make_case(candidates=candidates, venue_types=("lecture_hall",) * 3,
                                     full_capacities=(4, 4, 4), policy=SchedulingPolicy(max_rooms_per_exam=1))
    assert [m.module_code for m in snapshot.modules if required_sessions(snapshot, m) > 1] == ["IT1001"]
    result = generate_timetable(snapshot)
    assert result.status in OK and validate_timetable(snapshot, result.timetable) == ()
    sessions = _by_module(result.timetable)
    [other] = sessions["IT1002"]
    [own] = [s for s in sessions["IT1001"] if SIX[0] in _seated(s)]
    assert own.end + dt.timedelta(minutes=snapshot.policy.minimum_break_minutes) <= other.start or \
        other.end + dt.timedelta(minutes=snapshot.policy.minimum_break_minutes) <= own.start


async def test_block_mode_repeat_split_module_respects_venue_blocks():
    # The split module (IT1002) is a repeat, Block Mode module; room R1 is blocked on the first morning.
    block = VenueBlock("R1", MONDAY, dt.time(8, 0), dt.time(12, 0), "simulated_maintenance", "SIMULATED", None)
    snapshot, _, _ = await make_case(candidates=((SIX[0],), SIX), delivery=(("regular", None), ("block", "full_module")),
                                     blocks=(block,), policy=SchedulingPolicy(max_rooms_per_exam=1), **TWO_DAYS)
    result = generate_timetable(snapshot)
    assert result.status in OK and validate_timetable(snapshot, result.timetable) == ()
    for s in result.timetable.sessions:
        if s.start.date() == MONDAY and s.start.time() < dt.time(12):
            assert all(room.venue_id != "R1" for room in s.rooms)
    _check_split(snapshot, result.timetable)


async def test_reading_time_and_duration_apply_to_every_session():
    snapshot, _, _ = await make_case(candidates=(SIX,), durations=(120,), readings=(15,),
                                     policy=SchedulingPolicy(max_rooms_per_exam=1), **TWO_DAYS)
    for s in generate_timetable(snapshot).timetable.sessions:
        assert s.end - s.start == dt.timedelta(minutes=135)


# --- Independent validator ------------------------------------------------------------------------------


async def _split_case():
    snapshot, _, _ = await make_case(candidates=(SIX[:4],), policy=SchedulingPolicy(max_rooms_per_exam=1),
                                     **TWO_DAYS)
    timetable = generate_timetable(snapshot).timetable
    assert len(timetable.sessions) == 2
    return snapshot, timetable


def _with_sessions(timetable, sessions):
    return replace(timetable, sessions=tuple(sessions))


async def test_validator_rejects_a_student_seated_twice_or_missing():
    snapshot, timetable = await _split_case()
    first, second = sorted(timetable.sessions, key=lambda s: s.start)
    moved = _seated(first)[0]
    room = second.rooms[0]
    twice = replace(second, rooms=(replace(room, seats=room.seats + (SeatAllocation(moved, 99),)),))
    errors = validate_timetable(snapshot, _with_sessions(timetable, (first, twice)))
    assert any(v.constraint == "H10" and "exactly one session" in v.message for v in errors)
    missing = replace(first, rooms=(replace(first.rooms[0], seats=first.rooms[0].seats[1:]),))
    assert any(v.constraint == "H10" for v in validate_timetable(snapshot, _with_sessions(timetable, (missing, second))))


async def test_validator_rejects_overlapping_or_misnumbered_sessions():
    snapshot, timetable = await _split_case()
    first, second = sorted(timetable.sessions, key=lambda s: s.start)
    other_room = "R2" if first.rooms[0].venue_id == "R1" else "R1"
    same_time = replace(second, start=first.start, end=first.end,
                        rooms=(replace(second.rooms[0], venue_id=other_room),))
    errors = validate_timetable(snapshot, _with_sessions(timetable, (first, same_time)))
    assert any("different, non-overlapping times" in v.message for v in errors)
    swapped = (replace(first, session_number=2), replace(second, session_number=1))
    assert any("numbered 1..2" in v.message for v in validate_timetable(snapshot, _with_sessions(timetable, swapped)))


async def test_validator_rejects_extra_sessions_when_one_sitting_is_enough():
    snapshot, _, _ = await make_case(candidates=(SIX[:2],))
    [session] = generate_timetable(snapshot).timetable.sessions
    seats = [seat for room in session.rooms for seat in room.seats]
    later = session.start + dt.timedelta(hours=5)
    split = (
        replace(session, session_id="TEST:IT1001:S01", rooms=(RoomAllocation("R1", (SeatAllocation(seats[0].student_id, 1),)),),
                session_count=2),
        ExamSession("TEST:IT1001:S02", "IT1001", "TEST", later, later + (session.end - session.start),
                    (RoomAllocation("R1", (SeatAllocation(seats[1].student_id, 1),)),), 2, 2),
    )
    errors = validate_timetable(snapshot, DraftTimetable("TEST", snapshot.fingerprint, split))
    assert any("must have exactly 1 examination session" in v.message for v in errors)


# --- Cognitive load (S1-S5) and fairness on each student's own session -----------------------------------


async def test_workload_scores_use_each_students_own_session():
    # IT1001 is split into two sessions; IT30000001 also sits IT1002.
    candidates = (SIX[:4], (SIX[0],))
    snapshot, _, _ = await make_case(candidates=candidates, venue_types=("lecture_hall",) * 3,
                                     full_capacities=(4, 4, 4), policy=SchedulingPolicy(max_rooms_per_exam=1),
                                     **TWO_DAYS)
    timetable = generate_timetable(snapshot).timetable
    evaluation = evaluate_timetable(snapshot, timetable)
    sessions = _by_module(timetable)
    [own] = [s for s in sessions["IT1001"] if SIX[0] in _seated(s)]
    [other] = sessions["IT1002"]
    # Only IT30000001 sits two exams; the same-day count follows their own session, not the module's first one.
    assert evaluation.same_day_student_pairs == int(own.start.date() == other.start.date())
    others = {p.student_id: p.objective_units for p in evaluation.student_penalties if p.student_id != SIX[0]}
    assert set(others.values()) == {0}


@pytest.mark.parametrize("fairness", [FairnessPolicy(), FairnessPolicy(enabled=True)])
async def test_optimization_with_split_modules_matches_the_independent_score(fairness):
    candidates = (SIX[:4], (SIX[0], SIX[1]))
    snapshot, _, _ = await make_case(candidates=candidates, venue_types=("lecture_hall",) * 3,
                                     full_capacities=(4, 4, 4), policy=SchedulingPolicy(max_rooms_per_exam=1),
                                     end_date=MONDAY + dt.timedelta(days=3))
    result = optimize_timetable(snapshot, SolverSettings(time_limit_seconds=10), fairness=fairness)
    # An objective/evaluation mismatch would be reported as MODEL_INVALID with an OPTIMIZATION violation.
    assert result.status in OK and not result.violations, result.violations
    assert validate_timetable(snapshot, result.timetable) == ()
    assert result.selected_evaluation.objective_units <= result.baseline_evaluation.objective_units
    if fairness.enabled:
        assert result.fairness_comparison.limits_satisfied
    # The two shared candidates are spread so neither sits both exams on the same day when avoidable.
    assert result.selected_evaluation.same_day_student_pairs == 0


# --- Configurable maximum ---------------------------------------------------------------------------------


def test_settings_document_carries_the_session_maximum():
    from app.scheduling.configuration import SchedulingConfiguration

    document = SchedulingConfiguration().to_document()
    assert document["scheduling"]["max_sessions_per_exam"] == 3
    document["scheduling"]["max_sessions_per_exam"] = 5
    assert SchedulingConfiguration.from_document(document).scheduling.max_sessions_per_exam == 5


def test_documents_saved_before_the_session_maximum_use_the_default():
    from app.scheduling.configuration import SchedulingConfiguration

    document = SchedulingConfiguration().to_document()
    del document["scheduling"]["max_sessions_per_exam"]
    assert SchedulingConfiguration.from_document(document) == SchedulingConfiguration()


@pytest.mark.parametrize("value", [0, 11, 2.5, True, None, "3"])
def test_invalid_session_maximum_is_rejected(value):
    from app.scheduling.configuration import SchedulingConfiguration
    from app.scheduling.contracts import SchedulingValidationError

    document = SchedulingConfiguration().to_document()
    document["scheduling"]["max_sessions_per_exam"] = value
    with pytest.raises(SchedulingValidationError, match="Maximum sessions per exam"):
        SchedulingConfiguration.from_document(document)


# --- Venue blocks in the session count -------------------------------------------------------------------


def _blocked(venue, day, start, end):
    return VenueBlock(venue, day, start, end, "simulated_maintenance", "SIMULATED", None)


ALL_DAY = (dt.time(8, 0), dt.time(18, 0))


async def test_room_blocked_at_every_start_needs_an_extra_session():
    # Two rooms of 2 seats: 4 candidates fit one sitting, but R2 is blocked on both days -> 2 seats per sitting.
    blocks = tuple(_blocked("R2", MONDAY + dt.timedelta(days=d), *ALL_DAY) for d in range(2))
    snapshot, _, _ = await make_case(candidates=(SIX[:4],), blocks=blocks, **TWO_DAYS)
    module = snapshot.modules[0]
    assert (sitting_capacity(snapshot, module), required_sessions(snapshot, module)) == (2, 2)
    result = generate_timetable(snapshot)
    assert result.status in OK and validate_timetable(snapshot, result.timetable) == ()
    assert all(room.venue_id == "R1" for s in result.timetable.sessions for room in s.rooms)
    _check_split(snapshot, result.timetable)


async def test_one_free_start_keeps_one_session():
    # R2 is blocked only on the first day: the second day can seat everyone, so no extra session.
    snapshot, _, _ = await make_case(candidates=(SIX[:4],), blocks=(_blocked("R2", MONDAY, *ALL_DAY),), **TWO_DAYS)
    assert required_sessions(snapshot, snapshot.modules[0]) == 1
    [session] = generate_timetable(snapshot).timetable.sessions
    assert session.start.date() == MONDAY + dt.timedelta(days=1) and len(session.rooms) == 2


async def test_block_covering_only_part_of_the_exam_still_makes_the_room_unavailable():
    # R2 is free at 08:30 but blocked from 09:00: a 60-minute exam starting 08:30 cannot use it.
    blocks = (_blocked("R2", MONDAY, dt.time(9, 0), dt.time(9, 30)), _blocked("R2", MONDAY, dt.time(13, 0), dt.time(18, 0)))
    snapshot, _, _ = await make_case(candidates=(SIX[:4],), blocks=blocks)
    from app.scheduling.sessions import slot_capacities

    assert [seats for _, seats in slot_capacities(snapshot, snapshot.modules[0])] == [2, 2]
    assert required_sessions(snapshot, snapshot.modules[0]) == 2


async def test_blocks_that_need_more_sessions_than_allowed_are_refused_clearly():
    blocks = tuple(_blocked("R2", MONDAY + dt.timedelta(days=d), *ALL_DAY) for d in range(2))
    with pytest.raises(SnapshotError, match="needs 2 sessions.*seats at most 2.*maximum is 1"):
        await make_case(candidates=(SIX[:4],), blocks=blocks, policy=SchedulingPolicy(max_sessions_per_exam=1),
                        **TWO_DAYS)


@pytest.mark.parametrize("slots, duration, candidates, expected", [
    ([(0, 3), (300, 3)], 120, 4, 2),
    ([(0, 5), (60, 5), (300, 1)], 120, 6, 2),  # 0 and 60 overlap: best pair is 0 + 300
    ([(0, 5), (60, 5)], 120, 6, None),  # only overlapping slots: no number of sittings is enough
    ([(0, 2), (300, 2), (1440, 2), (1740, 2)], 120, 7, 4),
    ([(0, 1), (300, 9)], 120, 9, 1),
])
def test_fewest_sittings_is_exact(slots, duration, candidates, expected):
    from app.scheduling.sessions import fewest_sittings

    assert fewest_sittings(slots, duration, candidates) == expected


@pytest.mark.parametrize("blocked_days", [0, 1, 2])
async def test_validator_count_agrees_with_the_solver_count(blocked_days):
    from app.scheduling.validation import _sessions_needed

    blocks = tuple(_blocked("R2", MONDAY + dt.timedelta(days=d), *ALL_DAY) for d in range(blocked_days))
    snapshot, _, _ = await make_case(candidates=(SIX[:4],), blocks=blocks, **TWO_DAYS)
    assert _sessions_needed(snapshot, snapshot.modules[0]) == required_sessions(snapshot, snapshot.modules[0])


@pytest.mark.parametrize("fairness", [FairnessPolicy(), FairnessPolicy(enabled=True)])
async def test_blocked_split_case_optimizes_with_matching_scores(fairness):
    blocks = tuple(_blocked("R3", MONDAY + dt.timedelta(days=d), *ALL_DAY) for d in range(4))
    snapshot, _, _ = await make_case(candidates=(SIX[:4], (SIX[0], SIX[1])), venue_types=("lecture_hall",) * 3,
                                     full_capacities=(4, 4, 4), blocks=blocks,
                                     policy=SchedulingPolicy(max_rooms_per_exam=1), end_date=MONDAY + dt.timedelta(days=3))
    result = optimize_timetable(snapshot, SolverSettings(time_limit_seconds=10), fairness=fairness)
    assert result.status in OK and not result.violations, result.violations
    assert validate_timetable(snapshot, result.timetable) == ()
    assert all(room.venue_id != "R3" for s in result.timetable.sessions for room in s.rooms)
    if fairness.enabled:
        assert result.fairness_comparison.limits_satisfied
