"""Peak-then-total teaching overload priority: exact minima, soft targets, honest staging."""

from collections import Counter

import pytest

from schedence_teaching.solver import SolverStatus
from schedence_teaching.workload import WorkloadBand

from test_fairness_solver import fairness, options, run
from test_staff_solver import demand
from test_work_period_solver import scenario

PEAK = "DELIVERY_COST_THEN_PEAK_OVERAGE_TOTAL_OVERAGE_FAIRNESS_PREFERENCES"
TOTAL = "DELIVERY_COST_THEN_OVERAGE_FAIRNESS_PREFERENCES"


def overloaded(*, extra_pools=None, extra_requirements=(), minutes=720):
    """Four modules of 270 minutes each; D (DR, 480 min target) and P (Professor, 360) are both eligible."""
    pools = {f"M{index}": [("D", "LIC"), ("P", "TEAM_MEMBER")] for index in range(1, 5)}
    requirements = [(f"M{index}", f"G{index}", 9) for index in range(1, 5)]
    prepared = demand({**pools, **(extra_pools or {})}, [*requirements, *extra_requirements])
    data = scenario(prepared=prepared, minutes=minutes)
    for profile in data[2].workload_input.profiles:
        if profile.staff_id == "D":
            profile.workload_band = WorkloadBand.DR
    return data


def minutes(result):
    totals = Counter()
    for row in result.schedule:
        totals[row.assigned_staff_id] += row.end_minute - row.start_minute
    return dict(totals)


def test_peak_priority_spreads_unavoidable_overload_more_evenly_than_total_only():
    _, _, total = run(overloaded(), fairness_config=options(priority=TOTAL))
    _, _, peak = run(overloaded(), fairness_config=options(priority=PEAK))
    assert total.status is peak.status is SolverStatus.OPTIMAL
    # Both priorities prove the same unavoidable total overload: 1,080 required minutes against 840 target minutes.
    assert dict(total.fairness_report.objective_components)["TOTAL_OVER_TARGET_MINUTES"] == 240
    assert dict(peak.fairness_report.objective_components)["TOTAL_OVER_TARGET_MINUTES"] == 240
    # Normalized deviation alone accepts a 150-minute individual overload; the peak stage proves 120 is the minimum.
    assert minutes(total) == {"D": 630, "P": 450}
    assert minutes(peak) == {"D": 600, "P": 480}
    assert dict(peak.fairness_report.objective_components)["MAXIMUM_OVER_TARGET_MINUTES"] == 120
    assert peak.fairness_report.priority_order == ("DELIVERIES", "CONTACT_MINUTES", "MAXIMUM_OVER_TARGET_MINUTES",
        "TOTAL_OVER_TARGET_MINUTES", "MAXIMUM_TARGET_DEVIATION", "AGGREGATE_TARGET_DEVIATION")
    assert peak.fairness_report.all_stages_proven_optimal
    assert len(peak.schedule) == len(total.schedule) == 36


def test_constant_delivery_stages_are_proven_without_extra_native_solves(monkeypatch):
    from ortools.sat.python import cp_model
    native, calls = cp_model.CpSolver.solve, []
    def counted(self, model, *args, **kwargs):
        calls.append(1)
        return native(self, model, *args, **kwargs)
    monkeypatch.setattr(cp_model.CpSolver, "solve", counted)
    _, _, result = run(overloaded(), fairness_config=options(priority=PEAK))
    stages = result.fairness_report.stages
    assert [row.criterion for row in stages] == list(result.fairness_report.priority_order)
    assert all(row.status == "OPTIMAL" for row in stages)
    assert stages[0].objective_value == 36 and stages[1].objective_value == 1080
    assert len(calls) == 4  # Only the four modeled workload stages need CP-SAT.


def test_peak_priority_never_assigns_ineligible_staff_to_reduce_overload():
    # X has a large unused target but is only on the small module X1, never on M1-M4.
    data = overloaded(extra_pools={"X1": [("X", "LIC")]}, extra_requirements=[("X1", "GX", 2)])
    _, _, result = run(data, fairness_policy=fairness(("D", "P", "X")), fairness_config=options(priority=PEAK))
    assert result.status is SolverStatus.OPTIMAL
    assert {row.assigned_staff_id for row in result.schedule if row.session.offering_id.startswith("M")} <= {"D", "P"}
    assert {row.assigned_staff_id for row in result.schedule if row.session.offering_id == "X1"} == {"X"}
    assert dict(result.fairness_report.objective_components)["MAXIMUM_OVER_TARGET_MINUTES"] == 120


def test_staff_can_be_balanced_across_multiple_modules():
    _, _, result = run(overloaded(), fairness_config=options(priority=PEAK))
    modules = {staff: {row.session.offering_id for row in result.schedule if row.assigned_staff_id == staff} for staff in "DP"}
    # 600 minutes exceeds any single 270-minute module, so no one-module restriction can exist.
    assert len(modules["D"]) >= 3 and len(modules["P"]) >= 2


def test_unavoidable_single_eligible_overload_is_proven_and_target_stays_soft():
    data = scenario(prepared=demand({"A": [("P", "LIC")]}, [("A", "G", 14)]), minutes=600)
    _, _, result = run(data, fairness_policy=fairness(["P"]), fairness_config=options(priority=PEAK))
    components = dict(result.fairness_report.objective_components)
    assert result.status is SolverStatus.OPTIMAL and len(result.schedule) == 14
    assert components["MAXIMUM_OVER_TARGET_MINUTES"] == components["TOTAL_OVER_TARGET_MINUTES"] == 60


def test_combined_delivery_counts_once_under_peak_priority():
    from test_combination_solver import inputs, venues
    from schedence_teaching.preparation import prepare_group_combinations
    combinations = prepare_group_combinations(inputs(staff_pools={"OFFER": ["D", "P"]}))
    data = scenario(prepared=combinations.prepared, minutes=120)
    data[2].workload_input.profiles[0].workload_band = WorkloadBand.DR
    _, _, result = run(data, combination_input=combinations, venue_input=venues(),
                       fairness_config=options(priority=PEAK))
    report = result.fairness_report
    assert result.status is SolverStatus.OPTIMAL and result.validation.assigned_occurrences == 4
    assert report.assessment.scheduled_teaching_minutes == 60 == sum(row.teaching_minutes for row in report.modeled_workloads)


def test_feasible_stop_keeps_the_incumbent_and_never_claims_optimality(monkeypatch):
    from ortools.sat.python import cp_model
    native, calls = cp_model.CpSolver.solve, []
    def first_feasible(self, model, *args, **kwargs):
        status = native(self, model, *args, **kwargs)
        calls.append(status)
        return cp_model.FEASIBLE if len(calls) == 1 else status
    monkeypatch.setattr(cp_model.CpSolver, "solve", first_feasible)
    _, _, result = run(overloaded(), fairness_config=options(priority=PEAK))
    assert result.status is SolverStatus.FEASIBLE and len(result.schedule) == 36
    assert [row.status for row in result.fairness_report.stages] == ["OPTIMAL", "OPTIMAL", "FEASIBLE"]
    assert not result.fairness_report.all_stages_proven_optimal


def test_infeasible_peak_configuration_emits_no_timetable_or_stage_claims():
    data = overloaded(minutes=240)  # Each 270-minute group cannot fit a 240-minute day.
    _, _, result = run(data, fairness_config=options(priority=PEAK))
    assert result.status is SolverStatus.INFEASIBLE and result.schedule == () and result.fairness_report is None


def test_staged_minimize_options_are_opt_in_and_carry_complete_incumbent_hints():
    from types import SimpleNamespace
    from ortools.sat.python import cp_model
    from schedence_teaching.solver.lexicographic import staged_minimize

    def problem():
        model = cp_model.CpModel()
        x, y = model.new_int_var(0, 10, "x"), model.new_int_var(0, 10, "y")
        model.add(x + y >= 7)
        return model, {"CONSTANT": 5, "X": x, "Y": y}

    config = SimpleNamespace(max_time_seconds=10.0, num_search_workers=1, random_seed=7)
    model, components = problem()
    _, status, stages = staged_minimize(model, config, ("CONSTANT", "X", "Y"), components,
                                        skip_constant_stages=True, carry_incumbent_hints=True)
    assert status is SolverStatus.OPTIMAL
    assert [(row.criterion, row.status, row.objective_value) for row in stages] == [
        ("CONSTANT", "OPTIMAL", 5), ("X", "OPTIMAL", 0), ("Y", "OPTIMAL", 7)]
    assert len(model.proto.solution_hint.vars) == len(model.proto.variables)
    legacy, components = problem()
    _, _, default = staged_minimize(legacy, config, ("CONSTANT", "X", "Y"), components)
    assert [row.status for row in default] == ["OPTIMAL"] * 3 and not legacy.proto.solution_hint.vars


def test_peak_option_is_rejected_for_invalid_priority_names():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        options(priority="PEAK_OVERLOAD_ONLY")
