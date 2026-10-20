"""Approved baseline-relative workload guards: exact limits, solver and independent checks."""

import datetime as dt
import json
from dataclasses import FrozenInstanceError, asdict, replace

import pytest

from app.scheduling import (FairnessPolicy, OptimizationPolicy, SchedulingValidationError,
                            SolverSettings, compare_fairness, evaluate_timetable,
                            generate_timetable, optimize_timetable, validate_timetable)
from app.scheduling.__main__ import main, read_fairness_policy
from app.scheduling.evaluation import StudentPenalty
from optimization_support import optimization_case, starts_on, timed_draft
from scheduling_support import MONDAY, make_case


@pytest.mark.parametrize("name", ["enabled", "protect_worst_student", "automatic_relaxation"])
@pytest.mark.parametrize("value", [None, 0, 1, "false", [], float("nan")])
def test_boolean_settings_require_actual_booleans(name, value):
    with pytest.raises(SchedulingValidationError):
        FairnessPolicy.from_mapping({name: value})


@pytest.mark.parametrize("name", ["max_individual_increase_points", "max_worst_increase_points"])
@pytest.mark.parametrize("value", [None, True, -1, 101, float("nan"), float("inf"), "5", 10**500])
def test_point_limits_require_finite_normalized_points(name, value):
    with pytest.raises(SchedulingValidationError):
        FairnessPolicy.from_mapping({name: value})


@pytest.mark.parametrize("values", [{"automatic_relaxation": True}, {"typo": 5}, [], None])
def test_automatic_relaxation_and_unknown_fields_are_rejected(values):
    with pytest.raises(SchedulingValidationError):
        FairnessPolicy.from_mapping(values)


def test_policy_is_immutable_serializable_and_canonical():
    p = FairnessPolicy()
    assert not p.enabled and p.protect_worst_student and not p.automatic_relaxation
    assert FairnessPolicy.from_mapping(json.loads(json.dumps(asdict(p)))) == p
    assert FairnessPolicy(max_individual_increase_points=5).fingerprint == p.fingerprint
    assert replace(p, enabled=True).fingerprint != p.fingerprint
    with pytest.raises(FrozenInstanceError):
        p.enabled = True


def controlled_units(report, units):
    """Controlled score records to test exact rational boundaries in isolation."""
    return replace(report, student_penalties=tuple(StudentPenalty(row.student_id, value)
                   for row, value in zip(report.student_penalties, units)), objective_units=sum(units))


async def two_student_report(scoring=OptimizationPolicy()):
    x = await optimization_case(2, difficulties=(5, 5), candidates=(("A", "B"), ("A", "B")))
    return evaluate_timetable(x, timed_draft(x, starts_on(0, 4)), scoring)


async def test_independent_limits_accept_exact_boundary_and_reject_one_integer_unit_more():
    report = await two_student_report()
    baseline = controlled_units(report, (3700, 28120))
    selected = controlled_units(report, (8700, 28120))
    p = FairnessPolicy(enabled=True)
    c = compare_fairness(baseline, selected, OptimizationPolicy(), p)
    assert c.limits_satisfied and c.maximum_individual_increase_points == 5
    assert c.students_worsened == 1 and c.students_unchanged == 1
    too_much = compare_fairness(baseline, controlled_units(report, (8701, 28120)), OptimizationPolicy(), p)
    assert not too_much.limits_satisfied and too_much.individual_limit_exceedances == 1
    worse_max = compare_fairness(baseline, controlled_units(report, (3700, 28121)), OptimizationPolicy(), p)
    assert worse_max.worst_limit_exceeded and worse_max.individual_limit_exceedances == 0
    relaxed_max = compare_fairness(baseline, controlled_units(report, (3700, 28121)), OptimizationPolicy(),
                                 replace(p, protect_worst_student=False))
    assert relaxed_max.limits_satisfied


async def test_normalization_is_invariant_when_all_weights_are_scaled():
    a = OptimizationPolicy()
    b = OptimizationPolicy(**{f"s{i}_weight": w * 2 for i, w in enumerate(a.weights, 1)})
    ra, rb = await two_student_report(a), await two_student_report(b)
    p = FairnessPolicy(enabled=True, max_individual_increase_points=2.75)
    first = compare_fairness(controlled_units(ra, (3700, 28120)), controlled_units(ra, (6450, 28120)), a, p)
    second = compare_fairness(controlled_units(rb, (7400, 56240)), controlled_units(rb, (12900, 56240)), b, p)
    assert first == second and first.limits_satisfied
    assert not compare_fairness(controlled_units(rb, (7400, 56240)), controlled_units(rb, (12901, 56240)), b, p).limits_satisfied


async def test_disabled_protection_reports_changes_without_rejecting_them():
    r = await two_student_report()
    c = compare_fairness(controlled_units(r, (3700, 28120)), controlled_units(r, (44055, 28120)), OptimizationPolicy())
    assert not c.protection_applied and c.limits_satisfied is None and not c.violations
    assert c.maximum_individual_increase_points == 40.355
    assert c.individual_limit_exceedances == 1 and c.worst_limit_exceeded


@pytest.mark.parametrize("kind", ["input", "configuration", "student", "duplicate", "missing", "type"])
async def test_independent_check_rejects_inconsistent_reports(kind):
    r = await two_student_report()
    if kind == "input": bad = replace(r, input_fingerprint="stale")
    elif kind == "configuration": bad = replace(r, configuration_fingerprint="wrong-weights")
    elif kind == "student": bad = replace(r, student_penalties=(StudentPenalty("UNKNOWN", 0), r.student_penalties[1]))
    elif kind == "duplicate": bad = replace(r, student_penalties=(r.student_penalties[0],) * 2)
    elif kind == "missing": bad = replace(r, student_penalties=())
    else: bad = replace(r, student_penalties=(StudentPenalty([], 0), r.student_penalties[1]))
    with pytest.raises(SchedulingValidationError):
        compare_fairness(r, bad, OptimizationPolicy(), FairnessPolicy(enabled=True))


def test_updated_json_configuration_is_read_each_run(monkeypatch):
    monkeypatch.setattr("pathlib.Path.read_text", lambda *a, **k: '{"enabled":true,"max_individual_increase_points":2.5}')
    first = read_fairness_policy("settings.json")
    monkeypatch.setattr("pathlib.Path.read_text", lambda *a, **k: '{"enabled":false,"max_individual_increase_points":0}')
    second = read_fairness_policy("settings.json")
    assert first.enabled and first.max_individual_increase_points == 2.5
    assert not second.enabled and second.max_individual_increase_points == 0


@pytest.mark.parametrize("text", ['NaN', '[]', '{"enabled":1}', '{"enabled":true,"enabled":false}',
                                 '{"max_individual_increase_points":Infinity}', '{"automatic_relaxation":true}'])
def test_cli_bad_configuration_remains_structured_standard_json(text, monkeypatch, capsys):
    monkeypatch.setattr("pathlib.Path.read_text", lambda *a, **k: text)
    assert main(["--optimize", "--fairness-config", "settings.json"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "MODEL_INVALID"


@pytest.mark.parametrize("args", [["--fairness"], ["--no-fairness"], ["--fairness-config", "settings.json"]])
def test_cli_requires_optimization_mode(args, capsys):
    with pytest.raises(SystemExit): main(args)
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "USAGE_ERROR"


async def loss_fixture():
    majority = tuple(f"MAJ-{i}" for i in range(10))
    x = await optimization_case(3, difficulties=(5, 5, 5),
        candidates=(majority, (*majority, "MINORITY"), ("MINORITY",)), full_capacities=(30, 30))
    morning = dt.datetime.combine(MONDAY, dt.time(8, 30))
    afternoon = dt.datetime.combine(MONDAY, dt.time(13, 30))
    baseline = timed_draft(x, (morning, afternoon, afternoon + dt.timedelta(days=3)))
    unfair = timed_draft(x, (morning, morning + dt.timedelta(days=3), afternoon + dt.timedelta(days=3)))
    assert not validate_timetable(x, baseline) and not validate_timetable(x, unfair)
    return x, baseline, unfair


async def test_known_total_improvement_can_be_unfair_to_a_repeat_student():
    x, baseline, unfair = await loss_fixture()
    assert any(r.registration_type == "repeat" and r.student_id == "MINORITY" for r in x.registrations)
    a, b = evaluate_timetable(x, baseline), evaluate_timetable(x, unfair)
    assert b.objective_units < a.objective_units
    c = compare_fairness(a, b, OptimizationPolicy(), FairnessPolicy(enabled=True))
    assert c.students_improved == 10 and c.students_worsened == 1
    assert c.individual_limit_exceedances == 1 and not c.limits_satisfied


@pytest.mark.parametrize("count", [1, 2, 5, 9])
async def test_dynamic_sizes_keep_hard_constraints_and_fairness(count):
    pytest.importorskip("ortools")
    x = await optimization_case(count, difficulties=tuple(1 + i % 5 for i in range(count)),
                                candidates=(("A", "B"),) * count)
    baseline = generate_timetable(x, SolverSettings(time_limit_seconds=5)).timetable
    r = optimize_timetable(x, SolverSettings(time_limit_seconds=2), baseline=baseline, fairness=FairnessPolicy(enabled=True))
    assert r.timetable and not validate_timetable(x, r.timetable)
    assert r.selected_evaluation.objective_units <= r.baseline_evaluation.objective_units
    assert r.fairness_comparison.limits_satisfied and not r.fairness_comparison.violations
    assert r.fairness_comparison.maximum_individual_increase_points <= 5
    assert r.fairness_comparison.worst_student_increase_points == 0


async def test_protected_optimization_can_improve_without_sacrificing_the_minority():
    pytest.importorskip("ortools")
    x, baseline, _ = await loss_fixture()
    r = optimize_timetable(x, SolverSettings(time_limit_seconds=5), baseline=baseline, fairness=FairnessPolicy(enabled=True))
    assert r.improved and r.selected_evaluation.objective_units < r.baseline_evaluation.objective_units
    assert r.fairness_comparison.limits_satisfied and not validate_timetable(x, r.timetable)


async def test_solver_guard_cannot_be_bypassed_by_an_unfair_improving_candidate(monkeypatch):
    pytest.importorskip("ortools")
    import app.scheduling.objective as objective
    from app.scheduling.availability import minute_offset
    x, baseline, unfair = await loss_fixture()
    original = objective.add_objective
    def omit_guards(model, starts, snapshot, scoring, baseline, **kwargs):
        expression = original(model, starts, snapshot, scoring, baseline)
        for session in unfair.sessions:
            model.add(starts.starts[session.module_code, 0] == minute_offset(snapshot, session.start))
        return expression
    monkeypatch.setattr(objective, "add_objective", omit_guards)
    r = optimize_timetable(x, SolverSettings(time_limit_seconds=5), baseline=baseline, fairness=FairnessPolicy(enabled=True))
    assert r.timetable is baseline and not r.improved
    assert r.optimization_status.value == "MODEL_INVALID" and any(v.constraint == "FAIRNESS" for v in r.violations)
    assert r.fairness_comparison.limits_satisfied  # selected baseline, not rejected candidate


@pytest.mark.parametrize("students", [1, 5, 11])
@pytest.mark.parametrize("allowance,feasible", [(1.025, True), (1.024999, False), (0, False)])
@pytest.mark.parametrize("weight_multiplier", [1, 2])
async def test_solver_individual_cap_is_exact_and_not_multiplied_by_population(students, allowance, feasible, weight_multiplier):
    pytest.importorskip("ortools")
    from ortools.sat.python import cp_model
    from app.scheduling.model import build_hard_model
    from app.scheduling.objective import add_objective
    from app.scheduling.availability import minute_offset
    members = tuple(f"S-{i}" for i in range(students))
    x = await optimization_case(2, difficulties=(5, 5), candidates=(members, members), full_capacities=(30, 30))
    baseline, candidate = timed_draft(x, starts_on(0, 4)), timed_draft(x, starts_on(0, 3))
    p = FairnessPolicy(enabled=True, protect_worst_student=False, max_individual_increase_points=allowance)
    scoring = OptimizationPolicy(**{f"s{i}_weight": weight_multiplier * w for i, w in enumerate(OptimizationPolicy().weights, 1)})
    model, starts, _ = build_hard_model(x)
    a, b = evaluate_timetable(x, baseline, scoring), evaluate_timetable(x, candidate, scoring)
    add_objective(model, starts, x, scoring, baseline, fairness=p, baseline_evaluation=a)
    for session in candidate.sessions: model.add(starts.starts[session.module_code, 0] == minute_offset(x, session.start))
    solver = cp_model.CpSolver(); solver.parameters.num_search_workers = 1
    status = solver.solve(model)
    assert (status in (cp_model.OPTIMAL, cp_model.FEASIBLE)) is feasible
    assert bool(compare_fairness(a, b, scoring, p).limits_satisfied) is feasible


@pytest.mark.parametrize("protect,allowance,feasible", [(True, 0, False), (True, 75, True), (False, 0, True)])
async def test_solver_worst_student_guard_is_independently_configurable(protect, allowance, feasible):
    pytest.importorskip("ortools")
    from ortools.sat.python import cp_model
    from app.scheduling.model import build_hard_model
    from app.scheduling.objective import add_objective
    from app.scheduling.availability import minute_offset
    x = await optimization_case(2, difficulties=(5, 5))
    first = starts_on(0)[0]
    baseline = timed_draft(x, starts_on(0, 4))
    candidate = timed_draft(x, (first, first + dt.timedelta(hours=5)))
    p = FairnessPolicy(enabled=True, max_individual_increase_points=100,
                       protect_worst_student=protect, max_worst_increase_points=allowance)
    a, b = evaluate_timetable(x, baseline), evaluate_timetable(x, candidate)
    model, starts, _ = build_hard_model(x)
    add_objective(model, starts, x, OptimizationPolicy(), baseline, fairness=p, baseline_evaluation=a)
    for session in candidate.sessions: model.add(starts.starts[session.module_code, 0] == minute_offset(x, session.start))
    solver = cp_model.CpSolver(); solver.parameters.num_search_workers = 1
    status = solver.solve(model)
    assert (status in (cp_model.OPTIMAL, cp_model.FEASIBLE)) is feasible
    assert bool(compare_fairness(a, b, OptimizationPolicy(), p).limits_satisfied) is feasible


async def test_disabled_path_has_identical_model_and_selection():
    pytest.importorskip("ortools")
    from app.scheduling.model import build_hard_model
    from app.scheduling.objective import add_objective
    x = await optimization_case(2, difficulties=(5, 5))
    baseline = timed_draft(x, starts_on(0, 1))
    a, starts_a, _ = build_hard_model(x)
    b, starts_b, _ = build_hard_model(x)
    add_objective(a, starts_a, x, OptimizationPolicy(), baseline)
    add_objective(b, starts_b, x, OptimizationPolicy(), baseline, fairness=FairnessPolicy())
    assert str(a.proto) == str(b.proto)
    first = optimize_timetable(x, SolverSettings(time_limit_seconds=3), baseline=baseline)
    second = optimize_timetable(x, SolverSettings(time_limit_seconds=3), baseline=baseline, fairness=FairnessPolicy())
    assert first.timetable == second.timetable and first.selected_evaluation == second.selected_evaluation
    assert first.configuration_fingerprint == second.configuration_fingerprint


async def test_no_baseline_means_no_unprotected_search(monkeypatch):
    import app.scheduling.optimization as service
    from app.scheduling.types import SolveResult, SolveStatus
    x = await optimization_case(2, difficulties=(5, 5))
    monkeypatch.setattr(service, "generate_timetable", lambda *a: SolveResult(SolveStatus.UNKNOWN, wall_time_seconds=.01))
    r = optimize_timetable(x, fairness=FairnessPolicy(enabled=True))
    assert r.status == SolveStatus.UNKNOWN and r.timetable is None and r.fairness_comparison is None
    assert "no limits were relaxed" in r.diagnostics[0].lower()


async def test_timeout_keeps_exact_baseline_and_zero_increases():
    pytest.importorskip("ortools")
    x = await optimization_case()
    baseline = timed_draft(x, starts_on(0, 1, 7, 8))
    r = optimize_timetable(x, SolverSettings(time_limit_seconds=1e-9), baseline=baseline, fairness=FairnessPolicy(enabled=True))
    assert r.timetable is baseline and r.optimization_status.value == "UNKNOWN"
    assert r.fairness_comparison.limits_satisfied and r.fairness_comparison.maximum_individual_increase_points == 0


async def test_infeasible_and_stale_inputs_are_not_relaxed():
    pytest.importorskip("ortools")
    x, _, _ = await make_case(full_capacities=(1, 1))  # no usable seat: truly infeasible
    r = optimize_timetable(x, SolverSettings(time_limit_seconds=3), fairness=FairnessPolicy(enabled=True))
    assert r.status.value == "INFEASIBLE" and r.timetable is None
    x = await optimization_case(1, difficulties=(3,))
    stale = replace(timed_draft(x, starts_on(0)), input_fingerprint="changed-data")
    assert optimize_timetable(x, baseline=stale, fairness=FairnessPolicy(enabled=True)).status.value == "MODEL_INVALID"


async def test_zero_weights_and_zero_allowance_preserve_valid_baseline():
    pytest.importorskip("ortools")
    scoring = OptimizationPolicy(**{f"s{i}_weight": 0 for i in range(1, 6)})
    x = await optimization_case(2, difficulties=(5, 5))
    baseline = timed_draft(x, starts_on(0, 1))
    r = optimize_timetable(x, SolverSettings(time_limit_seconds=3), scoring, baseline=baseline,
        fairness=FairnessPolicy(enabled=True, max_individual_increase_points=0))
    assert r.timetable is baseline and not r.improved
    assert r.fairness_comparison.limits_satisfied and r.fairness_comparison.baseline_worst_normalized_penalty == 0


async def test_equal_protected_score_keeps_exact_baseline_with_active_weights():
    pytest.importorskip("ortools")
    x = await optimization_case(1, difficulties=(5,))
    baseline = timed_draft(x, starts_on(0))
    r = optimize_timetable(x, SolverSettings(time_limit_seconds=3), baseline=baseline, fairness=FairnessPolicy(enabled=True))
    assert r.timetable is baseline and not r.improved and r.fairness_comparison.limits_satisfied


async def test_zero_allowance_prevents_every_individual_increase_with_active_weights():
    pytest.importorskip("ortools")
    x, baseline, _ = await loss_fixture()
    r = optimize_timetable(x, SolverSettings(time_limit_seconds=3), baseline=baseline,
        fairness=FairnessPolicy(enabled=True, max_individual_increase_points=0))
    assert r.timetable and not validate_timetable(x, r.timetable)
    assert r.fairness_comparison.limits_satisfied and r.fairness_comparison.students_worsened == 0


async def test_repeat_and_normal_students_with_the_same_pattern_have_the_same_score_and_protection():
    x = await optimization_case(2, difficulties=(5, 5), candidates=(("A", "B"), ("A", "B")))
    x = replace(x, registrations=tuple(replace(r, registration_type="normal" if r.student_id == "A" else "repeat")
                                      for r in x.registrations))
    baseline, candidate = timed_draft(x, starts_on(0, 1)), timed_draft(x, starts_on(0, 4))
    a, b = evaluate_timetable(x, baseline), evaluate_timetable(x, candidate)
    assert a.student_penalties[0].objective_units == a.student_penalties[1].objective_units
    assert b.student_penalties[0].objective_units == b.student_penalties[1].objective_units
    c = compare_fairness(a, b, OptimizationPolicy(), FairnessPolicy(enabled=True))
    assert c.limits_satisfied and c.students_improved == 2


async def test_fairness_model_requires_a_validated_baseline():
    pytest.importorskip("ortools")
    from app.scheduling.model import build_hard_model
    from app.scheduling.objective import add_objective
    x = await optimization_case(1, difficulties=(3,))
    model, starts, _ = build_hard_model(x)
    with pytest.raises(SchedulingValidationError):
        add_objective(model, starts, x, OptimizationPolicy(), fairness=FairnessPolicy(enabled=True))
    baseline = timed_draft(x, starts_on(0))
    measured = evaluate_timetable(x, baseline)
    with pytest.raises(SchedulingValidationError):
        add_objective(model, starts, x, OptimizationPolicy(), baseline, fairness=FairnessPolicy(enabled=True),
                      baseline_evaluation=replace(measured, configuration_fingerprint="wrong-scoring"))


async def test_full_dataset_baseline_is_a_valid_protected_fallback():
    pytest.importorskip("ortools")
    from app.sources import DatasetSource
    from app.seed.generator import generate_dataset
    from app.seating.memory import MemorySeatingStore
    from app.scheduling import load_snapshot
    x = await load_snapshot(DatasetSource(generate_dataset()), MemorySeatingStore(), period_code="PP1-2026-S2-FINAL")
    baseline = generate_timetable(x).timetable
    r = optimize_timetable(x, SolverSettings(time_limit_seconds=1e-9), baseline=baseline, fairness=FairnessPolicy(enabled=True))
    assert r.timetable is baseline and not validate_timetable(x, r.timetable)
    assert r.baseline_evaluation.input_fingerprint == r.selected_evaluation.input_fingerprint == x.fingerprint
    assert r.fairness_comparison.limits_satisfied and r.fairness_comparison.maximum_individual_increase_points == 0


def test_cli_opt_in_reports_aggregate_fairness_without_student_records(capsys):
    pytest.importorskip("ortools")
    assert main(["--optimize", "--fairness", "--module", "IT1010", "--time-limit", "3"]) == 0
    p = json.loads(capsys.readouterr().out)
    assert p["fairness_policy"]["enabled"] and p["fairness_comparison"]["limits_satisfied"]
    assert "student_penalties" not in p["baseline_evaluation"] and "student_penalties" not in p["selected_evaluation"]


def test_cli_explicit_disable_overrides_enabled_file(monkeypatch, capsys):
    pytest.importorskip("ortools")
    monkeypatch.setattr("pathlib.Path.read_text", lambda *a, **k: '{"enabled":true}')
    assert main(["--optimize", "--no-fairness", "--fairness-config", "settings.json", "--module", "IT1010", "--time-limit", "3"]) == 0
    p = json.loads(capsys.readouterr().out)
    assert not p["fairness_policy"]["enabled"] and not p["fairness_comparison"]["protection_applied"]
