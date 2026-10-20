"""Synthetic native CP-SAT fairness and independent source-minute validation."""

from dataclasses import replace
from fractions import Fraction

import pytest
from pydantic import ValidationError

from schedence_teaching.fairness import BalanceMetric, FairnessPolicy, Normalization, assess_fairness
from schedence_teaching.solver import (
    FairnessOptimizationConfig, SolverInputError, SolverStatus, TimetableValidationError,
    solve_timetable, validate_fairness_result, validate_staff_policy_result,
)
from schedence_teaching.workload import WorkloadBand, assess_weekly_workload

from test_staff_solver import demand
from test_work_period_solver import scenario
from test_preferences import calendar


def options(**updates):
    return FairnessOptimizationConfig.model_validate({
        "priority": "EXISTING_OBJECTIVES_THEN_FAIRNESS", "priority_policy_reference": "synthetic:fairness-last",
        "deviation_reference": "TEACHING_TARGET", "cohort_rule": "INPUT_ELIGIBILITY_AND_FIXED_CALENDAR", **updates})


def fairness(ids=("D", "P"), **updates):
    return FairnessPolicy.model_validate({"policy_reference": "synthetic:normalized-target-deviation",
        "week_reference": "SYNTHETIC-WEEK", "provenance": {"source": "synthetic", "reference": "development-only"},
        "comparison_scope": "EXPLICIT_GROUPS", "comparison_groups": [
            {"group_id": "SYNTHETIC-COHORT", "staff_ids": list(ids), "reason": "explicit synthetic comparison"}], **updates})


def allocation_data(*, shared=14, fixed=6, minutes=720, days=("DAY",)):
    prepared = demand({"BASE": [("P", "LIC")], "SHARED": [("D", "LIC"), ("P", "TEAM_MEMBER")]},
        [("BASE", "BASE-G", fixed), ("SHARED", "SHARED-G", shared)])
    data = scenario(prepared=prepared, minutes=minutes, days=days)
    data[2].workload_input.profiles[0].workload_band = WorkloadBand.DR
    return data


def context(data, *, policy=None, config=None):
    return {"staff_input": data[2], "fairness_policy": policy or fairness(),
        "fairness_calendar": calendar(data[1]), "fairness_config": config or options()}


def run(data=None, **changes):
    data = data or allocation_data()
    original = {**context(data), **changes}
    before = repr((data[0], data[1], original))
    result = solve_timetable(data[0], data[1], **original)
    assert before == repr((data[0], data[1], original))
    summary = validate_fairness_result(data[0], data[1], result, **original)
    if result.status in {SolverStatus.OPTIMAL, SolverStatus.FEASIBLE}:
        assert summary == result.validation and result.fairness_report is not None
        assert validate_staff_policy_result(data[0], data[1], result, **original) == result.validation
        workload = assess_weekly_workload(data[0], data[1], result, data[2].workload_input, **original)
        assert workload == result.workload_report
    else:
        assert summary is None and result.schedule == result.administrative_schedule == ()
        assert result.fairness_report is result.workload_report is result.preference_report is None
    return data, original, result


def test_native_fairness_matches_exact_exhaustive_allocation_minimum():
    data, original, result = run()
    assert result.status is SolverStatus.OPTIMAL
    report = result.fairness_report
    expected = min((max(abs(Fraction(180 + count * 30, 360) - 1), abs(Fraction((14 - count) * 30, 480) - 1)),
                    abs(Fraction(180 + count * 30, 360) - 1) + abs(Fraction((14 - count) * 30, 480) - 1))
                   for count in range(15))
    assert (report.maximum_normalized_target_deviation, report.aggregate_normalized_target_deviation) == expected
    assert report.priority_order == ("DELIVERIES", "CONTACT_MINUTES", "MAXIMUM_TARGET_DEVIATION", "AGGREGATE_TARGET_DEVIATION")
    assert report.all_stages_proven_optimal and all(stage.status == "OPTIMAL" for stage in report.stages)
    assert report.assessment.scheduled_teaching_minutes == 600 and report.assessment.assigned_delivery_count == 20
    assert dict(report.targets) == {"D": 480, "P": 360}
    assert report.normalization_scale == 1440


def test_fairness_changes_eligible_staff_assignments_and_preserves_delivery_totals():
    data = allocation_data()
    baseline = solve_timetable(data[0], data[1], staff_input=data[2])
    _, _, optimized = run(data)
    before = {row.policy.staff_id: row.scheduled_teaching_minutes for row in baseline.workload_report.staff}
    baseline_maximum = max(abs(Fraction(before[key], target) - 1) for key, target in optimized.fairness_report.targets)
    assert optimized.fairness_report.maximum_normalized_target_deviation < baseline_maximum
    assert sum(before.values()) == optimized.fairness_report.assessment.scheduled_teaching_minutes == 600
    assert len(baseline.schedule) == len(optimized.schedule) == 20


def test_disabled_mode_preserves_output_keys_and_existing_assignments():
    data = allocation_data()
    baseline = solve_timetable(data[0], data[1], staff_input=data[2])
    again = solve_timetable(data[0], data[1], staff_input=data[2], fairness_policy=None, fairness_calendar=None, fairness_config=None)
    assert baseline.schedule == again.schedule and baseline.validation == again.validation
    assert not baseline.fairness_optimization_enabled and baseline.fairness_report is None
    assert "fairness_optimization" not in baseline.to_dict()


@pytest.mark.parametrize("missing", ["policy", "calendar", "config", "staff"])
def test_enabled_mode_requires_explicit_policy_calendar_config_and_staff(missing):
    data = allocation_data()
    original = context(data)
    original[{"policy": "fairness_policy", "calendar": "fairness_calendar", "config": "fairness_config", "staff": "staff_input"}[missing]] = None
    with pytest.raises(ValueError):
        solve_timetable(data[0], data[1], **original)


@pytest.mark.parametrize("cause", ["target", "legacy", "week", "raw", "variance", "unknown_staff", "no_eligible"])
def test_invalid_or_missing_required_fairness_evidence_rejected(cause):
    data = allocation_data()
    original = context(data)
    if cause == "target":
        data[2].workload_input.policy.band_rules.pop(WorkloadBand.DR)
    elif cause == "legacy":
        from schedence_teaching.workload import confirmed_campus_policy
        data[2].workload_input.policy = confirmed_campus_policy(target_basis="TOTAL_RECOGNIZED_WORK")
    elif cause == "week":
        original["fairness_policy"].week_reference = "OTHER"
    elif cause == "raw":
        original["fairness_policy"].normalization = Normalization.RAW_MINUTES
    elif cause == "variance":
        original["fairness_policy"].balance_metric = BalanceMetric.VARIANCE
        original["fairness_policy"].include_variance = True
    elif cause == "unknown_staff":
        original["fairness_policy"] = fairness(["UNKNOWN"])
    else:
        original["fairness_policy"].eligibility_offering_ids = set()
    with pytest.raises(ValueError):
        solve_timetable(data[0], data[1], **original)


def test_explicit_exclusion_does_not_remove_teaching_eligibility():
    data, _, result = run(fairness_policy=fairness(staff_decisions=[
        {"staff_id": "P", "include": False, "reason": "synthetic separate assessment"}]))
    assert dict(result.fairness_report.targets) == {"D": 480}
    assert any(row.assigned_staff_id == "P" for row in result.schedule)
    assert "explicit_exclusion" in dict(result.fairness_report.exclusions)["P"][0]


@pytest.mark.parametrize("cause", ["workload", "utilization", "maximum", "aggregate", "cohort", "target", "objective", "stage", "status", "scale", "policy"])
def test_independent_validator_rejects_corrupted_fairness_results(cause):
    data, original, result = run()
    report = result.fairness_report
    if cause in {"workload", "utilization"}:
        row = report.modeled_workloads[0]
        changes = {"teaching_minutes": row.teaching_minutes + 30} if cause == "workload" else {"scaled_utilization": row.scaled_utilization + 1}
        report = replace(report, modeled_workloads=(replace(row, **changes), *report.modeled_workloads[1:]))
    else:
        changes = {
            "maximum": {"maximum_normalized_target_deviation": Fraction(0)},
            "aggregate": {"aggregate_normalized_target_deviation": Fraction(0)},
            "cohort": {"comparison_cohorts": ()}, "target": {"targets": (("D", 999),)},
            "objective": {"objective_vector": (0,)}, "stage": {"stages": tuple(reversed(report.stages))},
            "status": {"all_stages_proven_optimal": False}, "scale": {"normalization_scale": 1},
            "policy": {"optimization_config_json": "{}"}}[cause]
        report = replace(report, **changes)
    with pytest.raises(TimetableValidationError):
        validate_fairness_result(data[0], data[1], replace(result, fairness_report=report), **original)


def test_validation_requires_original_fairness_context_and_rejects_disabled_stray_reports():
    data, original, result = run()
    with pytest.raises(ValueError):
        validate_staff_policy_result(data[0], data[1], result, staff_input=data[2])
    with pytest.raises(ValueError):
        validate_staff_policy_result(data[0], data[1], replace(result, fairness_optimization_enabled=False), **original)


@pytest.mark.parametrize("layout,expected", [("combined", 60), ("mixed", 90), ("separate", 120)])
def test_combined_mixed_separate_and_unselected_alternatives_count_once(layout, expected):
    from test_combination_solver import inputs, venues
    from schedence_teaching.preparation import prepare_group_combinations
    source = inputs(staff_pools={"OFFER": ["D", "P"]})
    combinations = prepare_group_combinations(source)
    data = scenario(prepared=combinations.prepared, minutes=120)
    data[2].workload_input.profiles[0].workload_band = WorkloadBand.DR
    rooms = venues() if layout == "combined" else venues(large_minutes=30 if layout == "mixed" else 15)
    _, original, result = run(data, combination_input=combinations, venue_input=rooms)
    report = result.fairness_report
    assert report.assessment.scheduled_teaching_minutes == expected
    assert len(result.schedule) == expected // 30 and result.validation.assigned_occurrences == 4
    assert sum(row.teaching_minutes for row in report.modeled_workloads) == expected
    assert result.objective_status == "EVALUATED_COMPONENT"
    assert sum(row.teaching.scheduled_teaching_minutes for row in report.assessment.staff) == expected
    assert all(row.combined_student_count <= next(venue.capacity for venue in rooms.venues if venue.venue_id == row.assigned_venue_id)
               for row in result.schedule)


@pytest.mark.parametrize("common", ["D", "P"])
def test_combined_delivery_uses_only_common_offering_eligibility(common):
    from test_combination_solver import inputs, venues
    from test_group_combinations import shared_content
    from schedence_teaching.preparation import prepare_group_combinations
    other = "P" if common == "D" else "D"
    combinations = prepare_group_combinations(shared_content(inputs(separate_offerings=True,
        staff_pools={"OFFER-1": [common, other], "OFFER-2": [common]})))
    data = scenario(prepared=combinations.prepared, minutes=120)
    _, _, result = run(data, combination_input=combinations, venue_input=venues())
    assert all(row.is_combined and row.assigned_staff_id == common for row in result.schedule)
    assert result.fairness_report.assessment.scheduled_teaching_minutes == 60


def test_weekday_and_weekend_streams_share_one_teaching_total():
    prepared = demand({"A": [("STAFF", "LIC")], "B": [("STAFF", "LIC")]},
        streams={"A": "WEEKDAY", "B": "WEEKEND"})
    data = scenario(prepared=prepared, minutes=60, both_streams=True)
    _, _, result = run(data, fairness_policy=fairness(["STAFF"]))
    row = result.fairness_report.modeled_workloads[0]
    assert row.teaching_minutes == 60 and result.fairness_report.maximum_normalized_target_deviation == Fraction(5, 6)


@pytest.mark.parametrize("kind", ["day", "daily_cap", "weekly_cap", "admin", "research"])
def test_hard_staff_restrictions_limit_fairness_without_being_relaxed(kind):
    data = allocation_data(days=("MONDAY", "TUESDAY"))
    if kind == "day":
        data[2].availability[0].allowed_teaching_days = {"TUESDAY"}
        data[2].availability[0].available_windows.pop("MONDAY")
    elif kind in {"daily_cap", "weekly_cap"}:
        setattr(data[2].availability[0], f"{kind.split('_')[0]}_teaching_max_minutes", 60)
    else:
        from schedence_teaching.workload import prepare_work_periods
        from test_work_periods import admin, research, research_policy, FIRST
        if kind == "admin":
            data[3].administrative_periods = [admin(data[2].workload_input, staff_id="P", on_date=FIRST,
                window={"start_minute": 0, "end_minute": 720}, planned_duration_minutes=720)]
        else:
            data[3].research_policies = [research_policy(staff_id="D")]
            data[3].research_reservations = [research(staff_id="D", on_date=FIRST)]
        periods = prepare_work_periods(data[0].input_snapshot, data[2].workload_input, data[3])
        _, _, result = run(data, work_periods=periods, work_period_config=data[4])
        identity = "P" if kind == "admin" else "D"
        assert all(row.day != "MONDAY" for row in result.schedule if row.assigned_staff_id == identity)
        return
    _, _, result = run(data)
    if kind == "day":
        assert all(row.day == "TUESDAY" for row in result.schedule if row.assigned_staff_id == "D")
    else:
        daily = dict(next(row for row in result.workload_report.staff if row.policy.staff_id == "D").daily_teaching_minutes)
        assert max(daily.values(), default=0) <= 60
        if kind == "weekly_cap":
            assert sum(daily.values()) <= 60


def test_missing_eligibility_does_not_receive_fairness_driven_assignments():
    data = allocation_data()
    _, _, result = run(data)
    assert all(row.assigned_staff_id == "P" for row in result.schedule if row.session.offering_id == "BASE")


def test_zero_assigned_member_remains_visible_with_unit_target_deviation():
    data = allocation_data(shared=1, fixed=1)
    # A very small daily cap makes P unable to teach any shared session after
    # its compulsory BASE session; the comparison population is still preserved.
    data[2].availability[1].weekly_teaching_max_minutes = 30
    _, _, result = run(data)
    assert dict(result.fairness_report.targets) == {"D": 480, "P": 360}
    assert all(row.teaching_minutes == 30 for row in result.fairness_report.modeled_workloads)


def test_zero_deliveries_can_remain_in_the_cohort_without_a_teaching_cap():
    data = scenario(prepared=demand({"A": [("D", "LIC"), ("P", "TEAM_MEMBER")]}), minutes=30)
    _, _, result = run(data)
    zero = next(row for row in result.fairness_report.modeled_workloads if row.teaching_minutes == 0)
    assert dict(result.fairness_report.normalized_absolute_target_deviations)[zero.staff_id] == 1
    assert len(result.fairness_report.targets) == 2


def test_above_target_is_soft_and_never_replaces_a_real_cap():
    data = scenario(prepared=demand({"A": [("P", "LIC")]}, [("A", "G", 14)]), minutes=600)
    _, _, result = run(data, fairness_policy=fairness(["P"]))
    assert result.fairness_report.maximum_normalized_target_deviation == Fraction(1, 6)
    assert result.fairness_report.assessment.staff[0].target.excess_minutes == 60
    data[2].availability[0].weekly_teaching_max_minutes = 360
    _, _, failed = run(data, fairness_policy=fairness(["P"]))
    assert failed.status is SolverStatus.INFEASIBLE


@pytest.mark.parametrize("appointment,target", [(None, 360), ("HOD", 240), ("DEAN", 240)])
def test_professor_and_administrative_appointment_targets_stay_separate(appointment, target):
    data = scenario(appointment_name=appointment)
    _, _, result = run(data, fairness_policy=fairness(["STAFF"]))
    row = result.fairness_report.assessment.staff[0]
    assert dict(result.fairness_report.targets) == {"STAFF": target}
    assert row.administrative_allocation_minutes == 240 and row.verified_administrative_minutes == 0
    assert row.target.target_minutes == target and row.workload_band == "PROFESSOR"


def test_reconciled_admin_aggregation_does_not_invent_a_teaching_override():
    from schedence_teaching.workload import prepare_work_periods
    from test_work_periods import appointment
    data = scenario(appointment_name="HOD")
    data[3].appointments = [appointment(), appointment(identity="DEAN", position_code="DEAN")]
    periods = prepare_work_periods(data[0].input_snapshot, data[2].workload_input, data[3])
    with pytest.raises(SolverInputError, match="appointment"):
        run(data, fairness_policy=fairness(["STAFF"]), work_periods=periods, work_period_config=data[4])


def test_real_data_requires_explicit_verification_and_policy_approval_references():
    data = allocation_data()
    dates = calendar(data[1])
    dates.synthetic_data = False
    with pytest.raises(SolverInputError, match="verification"):
        run(data, fairness_calendar=dates)
    _, _, result = run(data, fairness_calendar=dates, fairness_config=options(
        target_verification_reference="synthetic:test-only-authoritative-snapshot",
        policy_approval_reference="synthetic:test-only-policy-approval"))
    assert result.status is SolverStatus.OPTIMAL


@pytest.mark.parametrize("limit", ["max_normalization_scale", "max_weight_scale", "max_integer_bound", "max_comparison_staff",
    "max_model_variables", "max_model_constraints"])
def test_configured_scaling_and_model_limits_reject_instead_of_rounding_or_dropping(limit):
    data = allocation_data()
    updates = {limit: 1}
    settings = fairness(staff_weights={"D": "1/3"}, weight_reference="synthetic:third") if limit == "max_weight_scale" else fairness()
    with pytest.raises(SolverInputError, match="limit|bound"):
        run(data, fairness_policy=settings, fairness_config=options(**updates))


def test_exact_rational_staff_and_cohort_weights_have_integer_objective_values():
    data, original, result = run(fairness_policy=fairness(staff_weights={"D": "1/3", "P": "2/5"},
        weight_reference="synthetic:staff-weights"), fairness_config=options(cohort_weights={"SYNTHETIC-COHORT": "3/7"},
        cohort_weight_reference="synthetic:cohort-weight"))
    report = result.fairness_report
    assert report.weight_scale == 35
    assert dict(report.integer_weights) == {"D": 5, "P": 6}
    deviations = dict(report.normalized_absolute_target_deviations)
    assert report.aggregate_normalized_target_deviation == deviations["D"] / 7 + deviations["P"] * Fraction(6, 35)
    assert dict(report.objective_components)["AGGREGATE_TARGET_DEVIATION"] == report.aggregate_normalized_target_deviation * report.normalization_scale * report.weight_scale


@pytest.mark.parametrize("cause", ["overlap", "unknown_weight", "unreferenced_weight", "float_weight", "zero_weight", "missing_priority"])
def test_invalid_fairness_optimization_contracts_and_cohort_configuration(cause):
    with pytest.raises(ValueError):
        if cause == "overlap":
            source = fairness()
            source.comparison_groups.append(source.comparison_groups[0].model_copy(update={"group_id": "OVERLAP"}))
            run(fairness_policy=source)
        elif cause == "unknown_weight":
            run(fairness_config=options(cohort_weights={"UNKNOWN": 2}, cohort_weight_reference="synthetic"))
        elif cause == "missing_priority":
            payload = options().model_dump()
            payload.pop("priority")
            FairnessOptimizationConfig.model_validate(payload)
        else:
            value = {"unreferenced_weight": 1, "float_weight": 0.5, "zero_weight": 0}[cause]
            options(cohort_weights={"SYNTHETIC-COHORT": value},
                cohort_weight_reference=None if cause == "unreferenced_weight" else "synthetic")


@pytest.mark.parametrize("at_stage", [1, 3, 4])
def test_feasible_status_stops_stages_and_never_claims_lexicographic_optimality(monkeypatch, at_stage):
    from ortools.sat.python import cp_model
    native = cp_model.CpSolver.solve
    calls = []
    def feasible(self, model, *args, **kwargs):
        status = native(self, model, *args, **kwargs)
        calls.append(status)
        return cp_model.FEASIBLE if len(calls) == at_stage else status
    monkeypatch.setattr(cp_model.CpSolver, "solve", feasible)
    _, _, result = run()
    assert result.status is SolverStatus.FEASIBLE and len(calls) == at_stage
    assert result.fairness_report.stages[-1].status == "FEASIBLE"
    assert not result.fairness_report.all_stages_proven_optimal


def test_later_unknown_retains_the_previous_validated_incumbent(monkeypatch):
    from ortools.sat.python import cp_model
    native = cp_model.CpSolver.solve
    calls = []
    def timeout(self, model, *args, **kwargs):
        calls.append(1)
        return native(self, model, *args, **kwargs) if len(calls) == 1 else cp_model.UNKNOWN
    monkeypatch.setattr(cp_model.CpSolver, "solve", timeout)
    _, _, result = run()
    assert result.status is SolverStatus.FEASIBLE and len(result.schedule) == 20
    assert [row.status for row in result.fairness_report.stages] == ["OPTIMAL", "UNKNOWN"]
    assert not result.fairness_report.all_stages_proven_optimal


def test_search_budget_exhaustion_keeps_a_previous_incumbent_without_starting_a_stage(monkeypatch):
    import schedence_teaching.solver.lexicographic as stages
    ticks = iter([0.0, 0.0, 10.0])
    monkeypatch.setattr(stages, "perf_counter", lambda: next(ticks))
    _, _, result = run()
    assert result.status is SolverStatus.FEASIBLE
    assert result.fairness_report.stages[-1].status == "NOT_RUN_TIME_LIMIT"


@pytest.mark.parametrize("native_status", ["UNKNOWN", "MODEL_INVALID"])
def test_no_incumbent_emits_an_empty_failure_result(monkeypatch, native_status):
    from ortools.sat.python import cp_model
    monkeypatch.setattr(cp_model.CpSolver, "solve", lambda *args, **kwargs: getattr(cp_model, native_status))
    _, _, result = run()
    assert result.status.value == native_status and result.fairness_report is None


def test_equal_utilization_with_different_teaching_targets_can_be_fully_satisfied():
    _, _, result = run(allocation_data(shared=22, fixed=6, minutes=1440))
    assert dict(result.fairness_report.normalized_absolute_target_deviations) == {"D": 0, "P": 0}
    assert {row.staff_id: row.teaching_minutes for row in result.fairness_report.modeled_workloads} == {"D": 480, "P": 360}
    assert result.fairness_report.maximum_normalized_target_deviation == result.fairness_report.aggregate_normalized_target_deviation == 0


def test_confirmed_preference_penalty_has_priority_over_fairness_improvement():
    from test_preference_solver import inputs
    from test_preferences import preference, policy as preference_policy
    data = allocation_data()
    preference_context = inputs(data[0], data[1], [preference(staff_id="D", direction="AVOID", confirmation_status="CONFIRMED",
        penalty_weight=100_000, weight_reference="synthetic:confirmed-test-weight")],
        scoring=preference_policy(weight_mode="CONFIRMED_WEIGHTS"))
    _, _, result = run(data, **preference_context)
    _, _, fair_only = run(data)
    assert result.preference_report.score.total_weighted_penalty == 0
    assert next(row for row in result.fairness_report.modeled_workloads if row.staff_id == "D").teaching_minutes == 0
    assert result.fairness_report.maximum_normalized_target_deviation > fair_only.fairness_report.maximum_normalized_target_deviation
    assert result.preference_report.objective_status == "EVALUATED_COMPONENT"
    assert result.fairness_report.priority_order[2] == "PREFERENCE_PENALTY"


def test_exact_scaling_limits_accept_the_boundary_and_reject_the_next_unsafe_value():
    _, _, result = run(fairness_config=options(max_normalization_scale=1440, max_integer_bound=3840))
    assert result.status is SolverStatus.OPTIMAL
    with pytest.raises(SolverInputError, match="bound"):
        run(fairness_config=options(max_normalization_scale=1440, max_integer_bound=3839))
    with pytest.raises(SolverInputError, match="scale limit"):
        run(fairness_config=options(max_normalization_scale=1439))


@pytest.mark.parametrize("cause", ["zero", "profile", "override"])
def test_missing_or_zero_teaching_targets_are_never_substituted(cause):
    data = allocation_data()
    if cause == "zero":
        data[2].workload_input.policy.band_rules[WorkloadBand.DR].weekly_target_minutes = 0
    elif cause == "profile":
        data[2].workload_input.profiles.pop(0)
    else:
        from schedence_teaching.workload import StaffAppointment
        data[2].workload_input.profiles[0].appointment = StaffAppointment.HOD
        data[2].workload_input.policy.appointment_overrides = {}
    with pytest.raises(ValueError):
        run(data)


@pytest.mark.parametrize("cause", ["assignment", "coverage", "workload", "exclusion", "provenance", "history_value", "premature_optimal"])
def test_independent_validation_rejects_invalid_assignments_and_audit_changes(cause):
    data, original, result = run()
    if cause == "assignment":
        row = next(row for row in result.schedule if row.session.offering_id == "BASE")
        result = replace(result, schedule=tuple(replace(item, assigned_staff_id="D") if item is row else item for item in result.schedule))
    elif cause == "coverage":
        result = replace(result, schedule=result.schedule[:-1])
    elif cause == "workload":
        row = replace(result.workload_report.staff[0], scheduled_teaching_minutes=0)
        result = replace(result, workload_report=replace(result.workload_report, staff=(row, *result.workload_report.staff[1:])))
    else:
        report = result.fairness_report
        if cause == "exclusion":
            report = replace(report, exclusions=(("P", ("unapproved-exclusion",)),))
        elif cause == "provenance":
            report = replace(report, assessment=replace(report.assessment, policy_snapshot_json="{}"))
        elif cause == "history_value":
            report = replace(report, stages=(replace(report.stages[0], objective_value=999), *report.stages[1:]))
        else:
            report = replace(report, stages=report.stages[:-1])
        result = replace(result, fairness_report=report)
    with pytest.raises(TimetableValidationError):
        validate_fairness_result(data[0], data[1], result, **original)


def test_postsolve_fairness_assessment_requires_context_and_revalidates_optimized_results():
    data, original, result = run()
    report = assess_fairness(data[0], data[1], result, original["fairness_calendar"], original["fairness_policy"], **original)
    assert report.staff == result.fairness_report.assessment.staff
    with pytest.raises(ValueError):
        assess_fairness(data[0], data[1], result, original["fairness_calendar"], original["fairness_policy"], staff_input=data[2])


def test_synthetic_optimization_demo_reports_actual_native_improvement(capsys):
    from pathlib import Path
    import runpy
    demo = runpy.run_path(str(Path(__file__).resolve().parents[1] / "examples" / "fairness-optimization-demo.py"))
    output = demo["main"]()
    assert output["fairness_improved"] and output["independently_validated"]
    assert output["baseline"]["delivery_count"] == output["optimized"]["delivery_count"] == 20
    assert output["baseline"]["preference_penalty"] == output["optimized"]["preference_penalty"] == 0
    assert output["optimized"]["maximum_normalized_target_deviation"] == "5/16"
    assert "synthetic" in capsys.readouterr().out


def test_delivery_count_beats_fairness_even_when_more_teaching_would_reduce_total_shortfall():
    from test_preference_solver import priority_source
    combinations = priority_source()
    data = scenario(prepared=combinations.prepared, minutes=90)
    _, _, result = run(data, combination_input=combinations, fairness_policy=fairness(["A", "B", "COMMON"]))
    assert len(result.schedule) == 1 and result.schedule[0].assigned_staff_id == "COMMON"
    assert result.fairness_report.aggregate_normalized_target_deviation == Fraction(35, 12)
    assert Fraction(17, 6) < result.fairness_report.aggregate_normalized_target_deviation  # Two separate bookings could improve only the lower tier.


@pytest.mark.parametrize("mode,minutes", [("DELIVERIES_THEN_MINUTES", 120), ("DELIVERIES", 150)])
def test_contact_minute_priority_is_preserved_and_can_only_change_with_explicit_original_mode(mode, minutes):
    from test_preference_solver import minute_priority_source
    from test_combination_solver import venues
    from schedence_teaching.solver import CombinationSolverConfig
    combinations = minute_priority_source()
    data = scenario(prepared=combinations.prepared, minutes=240)
    ids = [row.staff_id for row in combinations.prepared.input_snapshot.staff_members]
    _, _, result = run(data, combination_input=combinations, venue_input=venues(minutes=240, large_minutes=60),
        combination_config=CombinationSolverConfig(objective=mode), fairness_policy=fairness(ids))
    assert len(result.schedule) == 3 and result.fairness_report.assessment.scheduled_teaching_minutes == minutes


@pytest.mark.parametrize("priority,prefix,count", [
    ("TEACHING_THEN_PREFERENCES", ("DELIVERIES", "CONTACT_MINUTES", "PREFERENCE_PENALTY"), 1),
    ("PREFERENCES_THEN_TEACHING", ("PREFERENCE_PENALTY", "DELIVERIES", "CONTACT_MINUTES"), 2),
    ("DELIVERIES_THEN_PREFERENCES_THEN_MINUTES", ("DELIVERIES", "PREFERENCE_PENALTY", "CONTACT_MINUTES"), 1),
])
def test_original_preference_priority_remains_an_explicit_prefix(priority, prefix, count):
    from test_preference_solver import priority_source, inputs, options as preference_options
    from test_preferences import preference
    combinations = priority_source()
    data = scenario(prepared=combinations.prepared, minutes=90)
    preferences = inputs(data[0], data[1], [preference(staff_id="COMMON", direction="AVOID")],
        optimization=preference_options(data[1], priority=priority))
    _, _, result = run(data, combination_input=combinations, fairness_policy=fairness(["A", "B", "COMMON"]), **preferences)
    assert result.fairness_report.priority_order[:3] == prefix and len(result.schedule) == count


def test_secondary_aggregate_target_deviation_breaks_equal_maximum_ties():
    # D's 480-minute target rewards a minute less than P's 360-minute target.
    # One occurrence and a third, eligible, unassigned person keep max deviation
    # at one: the secondary objective must favor the smaller positive target.
    data = scenario(prepared=demand({"A": [("D", "LIC"), ("P", "TEAM_MEMBER"), ("THIRD", "TEAM_MEMBER")]}), minutes=30)
    data[2].workload_input.profiles[0].workload_band = WorkloadBand.DR
    data[2].workload_input.profiles[2].workload_band = WorkloadBand.LECTURER
    _, _, result = run(data, fairness_policy=fairness(["D", "P", "THIRD"]))
    assert result.schedule[0].assigned_staff_id == "P"
    assert result.fairness_report.maximum_normalized_target_deviation == 1


def test_explicit_weights_can_change_the_aggregate_tie_break_without_changing_maximum():
    data = scenario(prepared=demand({"A": [("D", "LIC"), ("P", "TEAM_MEMBER"), ("THIRD", "TEAM_MEMBER")]}), minutes=30)
    data[2].workload_input.profiles[0].workload_band = WorkloadBand.DR
    _, _, result = run(data, fairness_policy=fairness(["D", "P", "THIRD"],
        staff_weights={"D": 2}, weight_reference="synthetic:approved-experiment"))
    assert result.schedule[0].assigned_staff_id == "D"
    assert result.fairness_report.maximum_normalized_target_deviation == 1


def test_independent_validator_detects_incorrect_native_fairness_linearization(monkeypatch):
    from ortools.sat.python import cp_model
    monkeypatch.setattr(cp_model.CpModel, "add_abs_equality", lambda self, target, expression: self.add(target == 0))
    with pytest.raises(TimetableValidationError, match="fairness_workload_changed"):
        run()


def test_optimized_postsolve_reports_identify_fairness_as_enabled():
    data, original, result = run()
    report = assess_fairness(data[0], data[1], result, original["fairness_calendar"], original["fairness_policy"], **original)
    assert report.cp_sat_fairness_objective_enabled
    assert report.to_dict()["cp_sat_fairness_objective_enabled"]
    assert result.fairness_report.assessment.cp_sat_fairness_objective_enabled


def test_selected_flexible_protection_cannot_remove_a_member_from_the_frozen_objective():
    from schedence_teaching.workload import prepare_work_periods
    from test_work_periods import flexible_policy, flexible_request, FIRST
    prepared = demand({"A": [("D", "LIC"), ("P", "TEAM_MEMBER")]}, [("A", "G", 4)])
    data = scenario(prepared=prepared, minutes=300)
    data[2].availability[1].available_windows["DAY"][0].end_minute = 240
    data[3].flexible_policies = [flexible_policy(data[2].workload_input, staff_id="P", reservation_required=True)]
    data[3].flexible_requests = [flexible_request(staff_id="P", planned_duration_minutes=240,
        eligible_windows=[{"on_date": FIRST, "start_minute": 0, "end_minute": 240}])]
    periods = prepare_work_periods(prepared.input_snapshot, data[2].workload_input, data[3])
    _, _, result = run(data, work_periods=periods, work_period_config=data[4])
    assert result.fairness_report.maximum_normalized_target_deviation == 1
    assert dict(result.fairness_report.targets)["P"] == 360
    assert next(row for row in result.fairness_report.modeled_workloads if row.staff_id == "P").teaching_minutes == 0
    assert dict(result.fairness_report.input_opportunities)["P"].individually_available_occurrence_ids
    postsolve = next(row for row in result.fairness_report.assessment.staff if row.staff_id == "P")
    assert postsolve.opportunities.individually_available_occurrence_ids == ()


def test_postsolve_preference_assessment_retains_fairness_validation():
    from test_preferences import preference, policy as preference_policy
    from schedence_teaching.preferences import LecturerPreferenceInput, prepare_lecturer_preferences, score_lecturer_preferences
    data, original, result = run()
    preferences = prepare_lecturer_preferences(data[0].input_snapshot,
        LecturerPreferenceInput(preferences=[preference(staff_id="P")]))
    report = score_lecturer_preferences(data[0], data[1], result, preferences, original["fairness_calendar"],
        preference_policy(), staff_input=data[2], fairness_policy=original["fairness_policy"],
        fairness_calendar=original["fairness_calendar"], fairness_config=original["fairness_config"])
    assert sum(row.scheduled_teaching_minutes for row in report.staff) == 600
    corrupt = replace(result, fairness_report=replace(result.fairness_report, maximum_normalized_target_deviation=Fraction(0)))
    with pytest.raises(TimetableValidationError):
        score_lecturer_preferences(data[0], data[1], corrupt, preferences, original["fairness_calendar"],
            preference_policy(), staff_input=data[2], fairness_policy=original["fairness_policy"],
            fairness_calendar=original["fairness_calendar"], fairness_config=original["fairness_config"])
