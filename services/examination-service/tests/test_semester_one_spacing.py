"""Small projections of the existing synthetic Semester 1 seed; never a database.

Keep the reported November calendar, default S1-S5, actual synthetic normal /
repeat registrations and fairness. Only seven modules and two existing rooms
are solved; no full-dataset run or production preferences are changed.
"""
import datetime as dt
from dataclasses import replace
from types import SimpleNamespace

import pytest

pytest.importorskip("ortools")
from ortools.sat.python import cp_model

from app.generation.analysis import build_analysis
from app.generation.preferences import effective_configuration
from app.scheduling import (FairnessPolicy, OptimizationPolicy, SchedulingPolicy,
                            SolverSettings, evaluate_timetable, load_snapshot,
                            optimize_timetable, validate_timetable)
from app.scheduling.availability import minute_offset
from app.scheduling.configuration import SchedulingConfiguration
from app.scheduling.model import build_hard_model, decode_draft
from app.scheduling.objective import add_objective
from app.scheduling.types import SolveStatus
from app.seed.assumptions import DEFAULT_ASSUMPTIONS
from app.seed.cycles import build_synthetic_cycle
from app.seed.generator import generate_dataset
from app.seating.memory import MemorySeatingStore
from app.sources import DatasetSource

START, END = dt.date(2026, 11, 1), dt.date(2026, 11, 30)
SCORING = OptimizationPolicy()  # 25 / 20 / 30 / 15 / 10; 12 / 48 / 72 hours.
FAIRNESS = FairnessPolicy(enabled=True)
SETTINGS = SolverSettings(time_limit_seconds=5, num_search_workers=1)
CLUSTER = {"IE2022": 27, "IE2032": 28, "IE2062": 29, "IE2082": 30,
           "IT2090": 25, "IT1020": 3, "IT1060": 6}


@pytest.fixture(scope="module")
def cyber_data():
    assumptions = replace(DEFAULT_ASSUMPTIONS, semester=1, period_code="SYNTHETIC-S1-FINAL",
                          period_name="Synthetic Semester 1 regression", period_start=START, period_end=END,
                          include_saturdays=True, include_sundays=True, excluded_dates=())
    data = build_synthetic_cycle(generate_dataset(assumptions), assumptions=assumptions)
    students = tuple(s for s in data.students if s.academic_year == 2 and s.specialization == "CYBER")
    ids = {s.student_id for s in students}
    registrations = tuple(r for r in data.registrations if r.student_id in ids)
    codes = {r.module_code for r in registrations}
    # Select existing capacity-sufficient rooms without simulated blocks. Never remove a room's blocks.
    blocked = {b.venue_id for b in data.venue_blocks}
    venues = tuple(next(v for v in data.venues if v.venue_type == kind
                        and v.normal_capacity >= 2 * len(students) and v.venue_id not in blocked)
                   for kind in ("lecture_hall", "computer_lab"))
    venue_ids = {v.venue_id for v in venues}
    return replace(data, students=students, registrations=registrations, venues=venues,
                   modules=tuple(m for m in data.modules if m.module_code in codes),
                   module_specializations=tuple(m for m in data.module_specializations if m.module_code in codes),
                   exam_period_modules=tuple(m for m in data.exam_period_modules if m.module_code in codes),
                   venue_blocks=tuple(b for b in data.venue_blocks if b.venue_id in venue_ids))


async def snapshot_for(data, *, policy=SchedulingPolicy()):
    return await load_snapshot(DatasetSource(data), MemorySeatingStore(50),
                               period_code=data.exam_periods[0].period_code, from_cycle=True, policy=policy)


def fixed_draft(snapshot, dates):
    model, layout, _ = build_hard_model(snapshot)
    for (code, _), start in layout.starts.items():
        instant = dt.datetime.combine(dt.date(2026, 11, dates[code]), dt.time(8, 30))
        model.add(start == minute_offset(snapshot, instant))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 1
    solver.parameters.num_search_workers = 1
    assert solver.solve(model) in (cp_model.FEASIBLE, cp_model.OPTIMAL)
    draft = decode_draft(snapshot, solver, layout)
    assert not validate_timetable(snapshot, draft)
    return draft


def own_exams(snapshot, timetable):
    by_registration = {(seat.student_id, session.module_code): session
                       for session in timetable.sessions for room in session.rooms for seat in room.seats}
    result = {}
    for row in snapshot.registrations:
        result.setdefault(row.student_id, []).append(by_registration[row.student_id, row.module_code])
    return {student: sorted(exams, key=lambda exam: exam.start) for student, exams in result.items()}


async def test_existing_cyber_seed_cluster_is_penalized_and_can_be_spread_without_changing_preferences(cyber_data):
    snapshot = await snapshot_for(cyber_data)
    assert len(snapshot.students) == 19 and len(snapshot.modules) == 7
    assert {r.registration_type for r in snapshot.registrations} == {"normal", "repeat"}
    assert all(m.difficulty < SCORING.high_difficulty_minimum for m in snapshot.modules)
    baseline = fixed_draft(snapshot, CLUSTER)
    before = evaluate_timetable(snapshot, baseline, SCORING)
    assert before.penalties[1].penalty_points > 0  # S2 sees the end-to-start preparation deficit.
    assert before.penalties[4].penalty_points > 0  # S5 sees >2 exams in a three-calendar-day window.
    assert before.same_day_student_pairs == before.short_gap_student_pairs == 0
    assert before.difficult_pairs_below_target == 0  # These modules are not difficulty 4 or 5.

    result = optimize_timetable(snapshot, SETTINGS, SCORING, baseline=baseline, fairness=FAIRNESS)
    assert result.improved and result.selected_evaluation.objective_units < before.objective_units
    assert result.selected_evaluation.total_weighted_penalty == 0
    assert all(p.penalty_points == 0 for p in result.selected_evaluation.penalties)
    assert not validate_timetable(snapshot, result.timetable)
    assert result.fairness_comparison.limits_satisfied
    assert result.fairness_comparison.students_worsened == 0
    for exams in own_exams(snapshot, result.timetable).values():
        assert all((b.start - a.end).total_seconds() >= SCORING.preparation_target_minutes * 60
                   for a, b in zip(exams, exams[1:]))
        assert max(sum(0 <= (other.start.date() - exam.start.date()).days < SCORING.window_days
                       for other in exams) for exam in exams) <= SCORING.preferred_window_exams
    configuration = SchedulingConfiguration(scheduling=snapshot.policy, optimization=SCORING, fairness=FAIRNESS)
    analysis = build_analysis(SimpleNamespace(snapshot=snapshot, configuration=configuration,
        timetable=result.timetable, result=result, evaluation=result.selected_evaluation))
    assert analysis["baseline"]["statistics"]["short_gap_student_pairs"] == 0
    assert analysis["selected"]["statistics"]["registrations"] == len(snapshot.registrations)
    assert all(c["statistics"]["short_consecutive_gaps"] == 0 for c in analysis["selected"]["cohorts"])
    assert result.policy == SCORING and snapshot.policy == SchedulingPolicy()


@pytest.mark.parametrize("component", [1, 2, 3, 4, 5])
async def test_seed_registration_weighted_objective_agrees_with_independent_s1_s5(cyber_data, component):
    snapshot = await snapshot_for(cyber_data)
    # Make the first cluster pair same-day: now S3 must count each registered student, including repeats.
    dates = {**CLUSTER, "IE2032": 27}
    model, layout, _ = build_hard_model(snapshot)
    policy = replace(SCORING, **{f"s{i}_weight": int(i == component) for i in range(1, 6)})
    # Exercise S1 too, without asserting a difficult rating for the actual catalogue modules.
    if component == 1:
        snapshot = replace(snapshot, modules=tuple(replace(m, difficulty=5) for m in snapshot.modules))
        model, layout, _ = build_hard_model(snapshot)
    objective = add_objective(model, layout, snapshot, policy)
    for (code, _), start in layout.starts.items():
        clock = dt.time(13, 30) if code == "IE2032" else dt.time(8, 30)
        model.add(start == minute_offset(snapshot, dt.datetime.combine(dt.date(2026, 11, dates[code]), clock)))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 2
    solver.parameters.num_search_workers = 1
    assert solver.solve(model) == cp_model.OPTIMAL
    draft = decode_draft(snapshot, solver, layout)
    report = evaluate_timetable(snapshot, draft, policy)
    assert solver.value(objective) == report.objective_units
    assert report.same_day_student_pairs == 19
    assert report.short_gap_student_pairs == 19
    assert report.penalties[component - 1].penalty_points > 0
    assert report.students == len({r.student_id for r in snapshot.registrations})


async def test_same_cohort_metadata_does_not_create_unregistered_exam_pairs(cyber_data):
    # Keep five real catalogue modules but give each synthetic student exactly one actual registration.
    normal = {(row.student_id, row.module_code): row for row in cyber_data.registrations if row.registration_type == "normal"}
    codes = sorted({code for _, code in normal})
    rows = tuple(normal[student.student_id, codes[index % len(codes)]]
                 for index, student in enumerate(cyber_data.students))
    data = replace(cyber_data, registrations=rows)
    registered = {r.module_code for r in data.registrations}
    data = replace(data, modules=tuple(m for m in data.modules if m.module_code in registered),
                   module_specializations=tuple(m for m in data.module_specializations if m.module_code in registered),
                   exam_period_modules=tuple(m for m in data.exam_period_modules if m.module_code in registered))
    snapshot = await snapshot_for(data)
    assert len(snapshot.modules) == 5
    draft = fixed_draft(snapshot, CLUSTER)
    report = evaluate_timetable(snapshot, draft)
    assert report.students == 19 and report.total_weighted_penalty == 0
    assert report.same_day_student_pairs == report.short_gap_student_pairs == 0
    assert report.maximum_window_exams == 1


async def test_remaining_close_group_is_reported_when_the_calendar_cannot_fit_preparation_targets(cyber_data):
    # A deliberately constrained synthetic control: four days, four exams, one start per day.
    codes = {"IE2022", "IE2032", "IE2062", "IE2082"}
    data = replace(cyber_data, modules=tuple(m for m in cyber_data.modules if m.module_code in codes),
                   registrations=tuple(r for r in cyber_data.registrations if r.module_code in codes),
                   module_specializations=tuple(m for m in cyber_data.module_specializations if m.module_code in codes),
                   exam_period_modules=tuple(m for m in cyber_data.exam_period_modules if m.module_code in codes),
                   exam_periods=tuple(replace(p, start_date=dt.date(2026, 11, 27)) for p in cyber_data.exam_periods))
    snapshot = await snapshot_for(data, policy=SchedulingPolicy(permitted_start_times=(dt.time(8, 30),)))
    baseline = fixed_draft(snapshot, CLUSTER)
    result = optimize_timetable(snapshot, SETTINGS, baseline=baseline, fairness=FAIRNESS)
    assert result.optimization_status == SolveStatus.OPTIMAL
    assert result.selected_evaluation.objective_units <= result.baseline_evaluation.objective_units
    assert result.selected_evaluation.penalties[1].penalty_points > 0
    assert result.selected_evaluation.penalties[4].penalty_points > 0
    assert result.selected_evaluation.same_day_student_pairs == result.selected_evaluation.short_gap_student_pairs == 0
    assert result.fairness_comparison.limits_satisfied and not validate_timetable(snapshot, result.timetable)
    groups = own_exams(snapshot, result.timetable)
    assert len(groups) == 19
    assert all([s.start.day for s in exams] == [27, 28, 29, 30] for exams in groups.values())


async def test_exhausted_search_budget_keeps_clustered_baseline_without_claiming_improvement(cyber_data):
    snapshot = await snapshot_for(cyber_data)
    baseline = fixed_draft(snapshot, CLUSTER)
    result = optimize_timetable(snapshot, SolverSettings(time_limit_seconds=1e-9), baseline=baseline, fairness=FAIRNESS)
    assert result.status == SolveStatus.FEASIBLE and result.optimization_status == SolveStatus.UNKNOWN
    assert result.timetable is baseline and not result.improved
    assert result.selected_evaluation == result.baseline_evaluation
    assert result.selected_evaluation.penalties[1].penalty_points > 0
    assert result.selected_evaluation.penalties[4].penalty_points > 0
    assert any("Kept independently validated baseline" in message for message in result.diagnostics)
    assert result.fairness_comparison.limits_satisfied


def test_displayed_preferences_reach_configuration_without_changing_hard_rules():
    base = SchedulingConfiguration()
    effective = effective_configuration(base, {"solver": {"num_search_workers": 4, "time_limit_seconds": 120},
        "optimization": {"s1_weight": 25, "s2_weight": 20, "s3_weight": 30, "s4_weight": 15, "s5_weight": 10,
                         "short_gap_minutes": 720, "preparation_target_minutes": 2880,
                         "difficult_target_minutes": 4320, "high_difficulty_minimum": 4,
                         "window_days": 3, "preferred_window_exams": 2}, "fairness": {"enabled": True}})
    assert effective.optimization == SCORING
    assert effective.solver.num_search_workers == 4 and effective.solver.time_limit_seconds == 120
    assert effective.scheduling == base.scheduling
    assert effective.fairness.enabled and not effective.fairness.automatic_relaxation
