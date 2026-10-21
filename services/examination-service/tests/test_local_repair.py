"""Deterministic, isolated synthetic repairs; no DB, services or full CP-SAT runs."""
import datetime as dt
from dataclasses import replace
from itertools import islice
from types import SimpleNamespace

import pytest

from app.scheduling import FairnessPolicy, OptimizationPolicy, SolverSettings, evaluate_timetable, generate_timetable, optimize_timetable, validate_timetable
from app.scheduling import local_repair, runtime_budget
from app.scheduling.contracts import SchedulingValidationError
from app.scheduling.fairness import compare_fairness
from app.scheduling.local_repair import LocalRepairLimits, repair_timetable
from app.scheduling.protected import ProtectedSchedule, ProtectedSession, ProtectedTimetable
from app.scheduling.types import SolveStatus
from app.sources.types import VenueBlock
from optimization_support import optimization_case, starts_on, timed_draft
from scheduling_support import MONDAY

FAIRNESS = FairnessPolicy(enabled=True)


async def case(*args, **kwargs):
    snapshot = await optimization_case(*args, **kwargs)
    return replace(snapshot, period=replace(snapshot.period, semester=1),
                   modules=tuple(replace(m, semester=1) for m in snapshot.modules))


def moved(draft, index, start):
    original = draft.sessions[index]
    changed = replace(original, start=start, end=start + (original.end - original.start))
    return replace(draft, sessions=tuple(changed if i == index else s for i, s in enumerate(draft.sessions)))


@pytest.mark.parametrize("component", [0, 1, 2, 3, 4, 5])
async def test_deterministic_repair_improves_unchanged_s1_s5_and_preserves_assignments(component):
    snapshot = await case(readings=(10, 0, 15, 0))
    first = starts_on(0)[0]
    baseline = timed_draft(snapshot, (first, first + dt.timedelta(hours=5), *starts_on(1, 2)))
    policy = OptimizationPolicy() if component == 0 else OptimizationPolicy(
        **{f"s{i}_weight": int(i == component) for i in range(1, 6)})
    before = evaluate_timetable(snapshot, baseline, policy)
    assert before.objective_units > 0
    limits = LocalRepairLimits(max_candidates=12)
    a = repair_timetable(snapshot, baseline, baseline, policy, fairness=FAIRNESS, limits=limits)
    b = repair_timetable(snapshot, baseline, baseline, policy, fairness=FAIRNESS, limits=limits)
    assert a.timetable == b.timetable and a.evaluation == b.evaluation
    assert a.accepted_moves == b.accepted_moves > 0
    assert a.evaluation.objective_units < before.objective_units
    assert a.evaluation == evaluate_timetable(snapshot, a.timetable, policy)
    assert not validate_timetable(snapshot, a.timetable)
    assert compare_fairness(before, a.evaluation, policy, FAIRNESS).limits_satisfied
    for old, new in zip(baseline.sessions, a.timetable.sessions):
        assert old.rooms == new.rooms  # actual registrations, seats, rooms and repeats stay fixed
        assert old.end - old.start == new.end - new.start
        assert old.module_code == new.module_code and old.session_id == new.session_id
    assert a.candidates_considered <= 12 and a.accepted_moves <= 3
    if component == 0:
        assert a.evaluation.same_day_student_pairs < before.same_day_student_pairs
        assert a.evaluation.maximum_window_exams < before.maximum_window_exams


@pytest.mark.parametrize("limits", [LocalRepairLimits(time_limit_seconds=0), LocalRepairLimits(max_candidates=0), LocalRepairLimits(max_moves=0)])
async def test_disabled_or_empty_budget_keeps_feasible_input(limits):
    snapshot = await case()
    baseline = timed_draft(snapshot, starts_on(0, 1, 2, 3))
    result = repair_timetable(snapshot, baseline, baseline, fairness=FAIRNESS, limits=limits)
    assert result.timetable is baseline and result.accepted_moves == result.candidates_considered == 0


async def test_equal_score_never_moves_and_zero_score_skips_candidates(monkeypatch):
    snapshot = await case(2, difficulties=(5, 5))
    baseline = timed_draft(snapshot, starts_on(0, 4))
    monkeypatch.setattr(local_repair, "_moves", lambda *args: pytest.fail("zero score must not search"))
    result = repair_timetable(snapshot, baseline, baseline)
    assert result.timetable is baseline and result.evaluation.objective_units == 0


@pytest.mark.parametrize("rule", ["H1", "H2", "H3", "H4", "H5", "H6", "H7", "H8", "H9", "H10", "H11", "X1", "X2"])
async def test_existing_hard_and_cross_cycle_validator_rejects_bad_moves(monkeypatch, rule):
    excluded = MONDAY + dt.timedelta(days=7)
    blocked_day = MONDAY + dt.timedelta(days=10)
    block = VenueBlock("R1", blocked_day, dt.time(8, 30), dt.time(17, 30), "simulated_maintenance", "SYNTHETIC", None)
    snapshot = await case(2, difficulties=(5, 5), excluded=(excluded,), blocks=(block,),
                          venue_types=("lecture_hall", "computer_lab"))
    baseline = timed_draft(snapshot, starts_on(0, 1))
    candidate = moved(baseline, 0, starts_on(4)[0])
    s = candidate.sessions[0]
    if rule in ("H1", "H5"):
        candidate = moved(baseline, 0, baseline.sessions[1].start)
    elif rule in ("H2", "H3", "H10"):
        room = s.rooms[0]
        if rule == "H2":
            room = replace(room, seats=(replace(room.seats[0], seat_number=999),))
        elif rule == "H3":
            room = replace(room, venue_id="R2")
        else:
            room = replace(room, seats=())
        candidate = replace(candidate, sessions=(replace(s, rooms=(room,)), candidate.sessions[1]))
    elif rule == "H4":
        candidate = moved(baseline, 0, dt.datetime.combine(blocked_day, dt.time(8, 30)))
    elif rule == "H6":
        candidate = moved(baseline, 0, starts_on(11)[0])
    elif rule == "H7":
        candidate = moved(baseline, 0, starts_on(4)[0] + dt.timedelta(minutes=30))
    elif rule == "H8":
        candidate = moved(baseline, 0, dt.datetime.combine(excluded, dt.time(8, 30)))
    elif rule == "H9":
        candidate = replace(candidate, sessions=(replace(s, end=s.end + dt.timedelta(minutes=10)), candidate.sessions[1]))
    elif rule == "H11":
        candidate = replace(candidate, input_fingerprint="stale")
    else:
        day = candidate.sessions[0].start
        protected = ProtectedSession("OTHER:EXAM", "OTHER", day if rule == "X1" else day - dt.timedelta(minutes=90),
            day + dt.timedelta(minutes=60) if rule == "X1" else day - dt.timedelta(minutes=30),
            ("R1",) if rule == "X1" else ("EXTERNAL",), () if rule == "X1" else ("SYNTHETIC",))
        timetable = ProtectedTimetable(7, "OTHER", "Synthetic other cycle", "APPROVED", 1, 1,
            day.date(), day.date(), (protected,))
        snapshot = replace(snapshot, protected=ProtectedSchedule((timetable,)))
    assert not validate_timetable(snapshot, baseline)
    assert rule in {v.constraint for v in validate_timetable(snapshot, candidate)}
    monkeypatch.setattr(local_repair, "_moves", lambda *args: iter((candidate,)))
    result = repair_timetable(snapshot, baseline, baseline, fairness=FAIRNESS)
    assert result.timetable is baseline and result.accepted_moves == 0


@pytest.mark.parametrize("already_improved", [False, True])
async def test_fairness_never_ratchets_from_original_baseline(monkeypatch, already_improved):
    majority = tuple(f"MAJ-{i}" for i in range(10))
    snapshot = await case(3, difficulties=(5, 5, 5),
                          candidates=(majority, (*majority, "MINORITY"), ("MINORITY",)), full_capacities=(30, 30))
    first = starts_on(0)[0]
    baseline = timed_draft(snapshot, (first, first + dt.timedelta(hours=5), first + dt.timedelta(days=3, hours=5)))
    allowed = moved(baseline, 1, starts_on(1)[0])
    ratchet = moved(allowed, 1, starts_on(2)[0])
    policy = OptimizationPolicy()
    fairness = FairnessPolicy(enabled=True, max_individual_increase_points=20)
    a, b, c = (evaluate_timetable(snapshot, draft, policy) for draft in (baseline, allowed, ratchet))
    assert c.objective_units < b.objective_units < a.objective_units
    assert compare_fairness(a, b, policy, fairness).limits_satisfied
    assert compare_fairness(b, c, policy, fairness).limits_satisfied  # the erroneous moving reference would allow it
    assert not compare_fairness(a, c, policy, fairness).limits_satisfied
    def moves(snapshot, selected, evaluation):
        yield allowed if selected is baseline else ratchet
    monkeypatch.setattr(local_repair, "_moves", moves)
    result = repair_timetable(snapshot, allowed if already_improved else baseline, baseline, fairness=fairness)
    assert result.timetable is allowed and result.accepted_moves == int(not already_improved)
    assert result.evaluation == b


async def test_repeat_student_harm_is_rejected_even_when_population_score_improves(monkeypatch):
    majority = tuple(f"MAJ-{i}" for i in range(10))
    snapshot = await case(3, difficulties=(5, 5, 5), candidates=(majority, (*majority, "MINORITY"), ("MINORITY",)), full_capacities=(30, 30))
    first = starts_on(0)[0]
    baseline = timed_draft(snapshot, (first, first + dt.timedelta(hours=5), first + dt.timedelta(days=3, hours=5)))
    unfair = moved(baseline, 1, starts_on(3)[0])
    assert evaluate_timetable(snapshot, unfair).objective_units < evaluate_timetable(snapshot, baseline).objective_units
    monkeypatch.setattr(local_repair, "_moves", lambda *args: iter((unfair,)))
    assert repair_timetable(snapshot, baseline, baseline, fairness=FAIRNESS).timetable is baseline


async def test_deadline_interrupts_validation_and_context_is_restored(monkeypatch):
    snapshot = await case()
    baseline = timed_draft(snapshot, starts_on(0, 1, 2, 3))
    ticks = [0]
    def clock():
        ticks[0] += 1
        return ticks[0] / 1000
    monkeypatch.setattr(runtime_budget, "clock", clock)
    result = repair_timetable(snapshot, baseline, baseline, limits=LocalRepairLimits(time_limit_seconds=.005))
    assert result.budget_exhausted and result.timetable is baseline and not result.accepted_moves
    assert ticks[0] < 10  # interrupted inside validation; never completes all rows
    assert not validate_timetable(snapshot, baseline)  # the expired context cannot leak to later callers


async def test_deadline_returns_last_validated_improvement_and_honors_outer_budget(monkeypatch):
    snapshot = await case(2, difficulties=(5, 5))
    baseline = timed_draft(snapshot, starts_on(0, 1))
    improved = moved(baseline, 0, starts_on(7)[0])
    now = [0.0]
    monkeypatch.setattr(runtime_budget, "clock", lambda: now[0])
    def moves(snapshot, selected, evaluation):
        if selected is not baseline:
            now[0] = .5
        yield improved
    monkeypatch.setattr(local_repair, "_moves", moves)
    # A two-exam zero-score improvement would stop naturally; retain some S2 penalty.
    policy = OptimizationPolicy(preparation_target_minutes=8 * 1440, difficult_target_minutes=9 * 1440)
    result = repair_timetable(snapshot, baseline, baseline, policy, deadline=.5)
    assert result.budget_exhausted and result.timetable is improved and result.accepted_moves == 1
    assert result.elapsed_seconds == .5 and result.evaluation == evaluate_timetable(snapshot, improved, policy)


async def test_no_late_candidate_can_be_committed(monkeypatch):
    snapshot = await case(2, difficulties=(5, 5))
    baseline = timed_draft(snapshot, starts_on(0, 1))
    improved = moved(baseline, 0, starts_on(7)[0])
    now = [0.0]
    monkeypatch.setattr(runtime_budget, "clock", lambda: now[0])
    real = local_repair.evaluate_timetable
    def evaluation(snapshot, draft, policy):
        report = real(snapshot, draft, policy)
        if draft is improved:
            now[0] = 1.0
        return report
    monkeypatch.setattr(local_repair, "evaluate_timetable", evaluation)
    monkeypatch.setattr(local_repair, "_moves", lambda *args: iter((improved,)))
    result = repair_timetable(snapshot, baseline, baseline, limits=LocalRepairLimits(time_limit_seconds=1))
    assert result.budget_exhausted and result.timetable is baseline and result.accepted_moves == 0


async def test_candidate_cap_stops_search_without_accepting_equal_score(monkeypatch):
    snapshot = await case(2, difficulties=(5, 5))
    baseline = timed_draft(snapshot, starts_on(0, 1))
    monkeypatch.setattr(local_repair, "_moves", lambda *args: iter((baseline, moved(baseline, 0, starts_on(7)[0]))))
    result = repair_timetable(snapshot, baseline, baseline, limits=LocalRepairLimits(max_candidates=1))
    assert result.timetable is baseline and result.candidates_considered == 1 and not result.accepted_moves


async def test_short_search_rotates_between_modules_before_retrying_one_module():
    snapshot = await case()
    baseline = timed_draft(snapshot, starts_on(0, 1, 2, 3))
    candidates = list(islice(local_repair._moves(snapshot, baseline, evaluate_timetable(snapshot, baseline)), 4))
    changed = [next(s.module_code for s, old in zip(d.sessions, baseline.sessions) if s.start != old.start)
               for d in candidates]
    assert len(changed) == len(set(changed)) == 4


async def test_split_sittings_keep_their_sessions_seats_and_student_assignments():
    snapshot = await case(2, difficulties=(5, 5), candidates=(("A", "B", "C"),) * 2,
                          full_capacities=(2,), venue_types=("lecture_hall",), end_date=MONDAY + dt.timedelta(days=3))
    baseline = generate_timetable(snapshot, SolverSettings(time_limit_seconds=1)).timetable
    assert baseline and all(s.session_count == 3 for s in baseline.sessions)
    result = repair_timetable(snapshot, baseline, baseline, fairness=FAIRNESS)
    assert result.timetable is baseline and result.accepted_moves == result.candidates_considered == 0
    assert not validate_timetable(snapshot, result.timetable)


@pytest.mark.parametrize("values", [{"time_limit_seconds": -1}, {"time_limit_seconds": float("nan")},
    {"time_limit_seconds": float("inf")}, {"time_limit_seconds": 6}, {"time_limit_seconds": True},
    {"time_limit_seconds": 10**500},
    {"max_candidates": 49}, {"max_candidates": False}, {"max_moves": 4}])
def test_repair_limits_cannot_exceed_server_caps(values):
    with pytest.raises(ValueError):
        LocalRepairLimits(**values)


@pytest.mark.parametrize("semester", [1, 2])
async def test_optimizer_repairs_semester_one_fallback_within_shared_budget(monkeypatch, semester):
    from ortools.sat.python import cp_model
    snapshot = await case(2, difficulties=(5, 5))
    snapshot = replace(snapshot, period=replace(snapshot.period, semester=semester),
                       modules=tuple(replace(m, semester=semester) for m in snapshot.modules))
    baseline = timed_draft(snapshot, starts_on(0, 1))
    fake = SimpleNamespace(parameters=SimpleNamespace(), solve=lambda model: cp_model.UNKNOWN,
                           status_name=lambda status: "UNKNOWN", wall_time=0.0)
    monkeypatch.setattr(cp_model, "CpSolver", lambda: fake)
    result = optimize_timetable(snapshot, SolverSettings(time_limit_seconds=5), baseline=baseline, fairness=FAIRNESS)
    assert result.baseline is baseline and result.status == SolveStatus.FEASIBLE
    assert result.optimization_status == SolveStatus.UNKNOWN and result.fairness_comparison.limits_satisfied
    assert result.improved == (semester == 1)
    assert (result.local_repair_moves > 0) == (semester == 1)
    assert result.selected_evaluation == evaluate_timetable(snapshot, result.timetable)
    assert result.total_time_seconds < 5
    if semester == 1:
        assert fake.parameters.max_time_in_seconds <= 4.5
        assert result.local_repair_seconds <= .5
    else:
        assert result.timetable is baseline and result.local_repair_seconds == 0

