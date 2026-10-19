"""P3.1: every objective in one shared, validated lexicographic plan on small synthetic data.

All policy values are synthetic test settings. Each solve is independently re-validated.
"""

import pytest
from ortools.sat.python import cp_model

from schedence_teaching.consecutive import assess_consecutive_sessions
from schedence_teaching.reporting.timetable_quality import timetable_quality
from schedence_teaching.solver import (ConsecutiveOptimizationConfig, SolverConfig, SolverInputError, SolverStatus,
                                       TimetableValidationError, solve_timetable)
from schedence_teaching.solver.objective_stage_plan import validate_combined_result
from schedence_teaching.workload import WorkloadBand

from test_consecutive import scoring
from test_daily_load_distribution import STAFF, STUDENT, daily
from test_fairness_solver import fairness, options as fairness_options
from test_preference_solver import inputs as preference_inputs
from test_preferences import calendar, policy as preference_policy, preference
from test_staff_solver import demand
from test_teaching_day_compactness import compact
from test_work_period_solver import scenario

PEAK = "DELIVERY_COST_THEN_PEAK_OVERAGE_TOTAL_OVERAGE_FAIRNESS_PREFERENCES"
DAYS = ("D1", "D2", "D3")  # Monday to Wednesday in the synthetic calendar.
PLAN = ("MAX_STUDENT_DAILY_MINUTES", "MAX_STAFF_DAILY_MINUTES", "THIN_TEACHING_DAYS", "CONSECUTIVE_PENALTY",
        "PREFERENCE_PENALTY", "AGGREGATE_TARGET_DEVIATION")
FULL_ORDER = ("DELIVERIES", "CONTACT_MINUTES", "MAXIMUM_OVER_TARGET_MINUTES", "TOTAL_OVER_TARGET_MINUTES",
              "MAXIMUM_TARGET_DEVIATION", *PLAN)


def base_data(*, limit=60.0):
    """Two shared modules for D (DR) and P over three days; small enough to prove every stage."""
    pools = {f"M{index}": [("D", "LIC"), ("P", "TEAM_MEMBER")] for index in (1, 2)}
    data = scenario(prepared=demand(pools, [("M1", "G1", 6), ("M2", "G2", 6)]), minutes=240, days=DAYS, limit=limit)
    for profile in data[2].workload_input.profiles:
        if profile.staff_id == "D":
            profile.workload_band = WorkloadBand.DR
    return data


def planned(config, order=PLAN, *, reference="SYNTHETIC:STAGE-PLAN"):
    quality = compact(daily(config, (STUDENT, STAFF), placement="BEFORE_AGGREGATE_TARGET_DEVIATION",
                            relative="DAILY_LOAD_THEN_COMPACTNESS"), minimum=60, placement="BEFORE_AGGREGATE_TARGET_DEVIATION")
    return SolverConfig.model_validate({**quality.model_dump(round_trip=True),
                                        "objective_stage_plan": dict(plan_reference=reference, order=list(order))})


def contexts(data, *, consecutive=True, preferences=True, formulation="TIME_INDEXED"):
    prepared, config, staff = data[0], data[1], data[2]
    output = {"staff_input": staff, "fairness_policy": fairness(), "fairness_calendar": calendar(config),
              "fairness_config": fairness_options(priority=PEAK)}
    if preferences:
        output.update(preference_inputs(prepared, config, [preference(staff_id="D", direction="AVOID", days=("MONDAY",),
            confirmation_status="CONFIRMED", penalty_weight=2, weight_reference="synthetic:test-weight")],
            scoring=preference_policy(weight_mode="CONFIRMED_WEIGHTS")))
    if consecutive:
        output.update(consecutive_policy=scoring(), consecutive_calendar=calendar(config),
                      consecutive_config=ConsecutiveOptimizationConfig.model_validate({
                          "priority": "EXISTING_OBJECTIVES_THEN_CONSECUTIVE",
                          "priority_policy_reference": "synthetic:consecutive-in-plan", "formulation": formulation}))
    return output


def combined_solve(data=None, *, order=PLAN, **flags):
    data = data or base_data()
    config = planned(data[1], order)
    context = contexts(data, **flags)
    result = solve_timetable(data[0], config, **context)
    if result.status in {SolverStatus.OPTIMAL, SolverStatus.FEASIBLE}:
        validate_combined_result(data[0], config, result, **context)  # Independent recomputation of every component.
        assert len(result.schedule) == len(data[0].sessions)
    return data, config, context, result


@pytest.fixture(scope="module")
def solved():
    return combined_solve()


def test_all_objectives_run_in_one_plan_with_every_stage_proven(solved):
    _, _, _, result = solved
    report = result.objective_stage_report
    assert result.status is SolverStatus.OPTIMAL and report.all_stages_proven_optimal
    assert report.priority_order == FULL_ORDER and report.proven_stage_count == len(FULL_ORDER)
    assert [row.criterion for row in report.stages] == list(FULL_ORDER)
    assert result.fairness_report.priority_order == (*FULL_ORDER[:5], "PREFERENCE_PENALTY", "AGGREGATE_TARGET_DEVIATION")
    assert result.consecutive_report.priority_order == (*FULL_ORDER[:5], "CONSECUTIVE_PENALTY", "PREFERENCE_PENALTY",
                                                        "AGGREGATE_TARGET_DEVIATION")
    assert result.preference_report.objective_status == "EVALUATED_COMPONENT"
    values = dict(report.stage_values)
    assert values["THIN_TEACHING_DAYS"] == result.teaching_day_compactness_report.thin_staff_days
    assert values["CONSECUTIVE_PENALTY"] == result.consecutive_report.total_weighted_penalty
    assert {"staff_clashes", "same_group_and_ancestor_clashes"} <= set(result.validation.checks)
    serialized = result.to_dict()["objective_stage_plan"]
    assert serialized["proven_stages"] == list(FULL_ORDER) and serialized["model_variables"] > 0


def test_priority_one_values_stay_fixed_and_quality_is_no_worse_than_the_workload_only_optimum(solved):
    data, _, _, result = solved
    from test_fairness_solver import run as fairness_run
    _, _, baseline = fairness_run(base_data(), fairness_config=fairness_options(priority=PEAK))
    before, after = dict(baseline.fairness_report.objective_components), dict(result.fairness_report.objective_components)
    for key in FULL_ORDER[:5]:
        assert after[key] == before[key]
    # The baseline satisfies every fixed Priority 1 stage, so the first quality stage cannot be worse.
    baseline_student = timetable_quality(data[0], baseline.schedule).summary()["student_groups"]["maximum_day_minutes"]
    assert dict(result.objective_stage_report.stage_values)["MAX_STUDENT_DAILY_MINUTES"] <= baseline_student


def test_tampered_stage_values_are_rejected(solved):
    from dataclasses import replace
    data, config, context, result = solved
    report = result.objective_stage_report
    values = dict(report.stage_values)
    values["THIN_TEACHING_DAYS"] += 1
    tampered = replace(result, objective_stage_report=replace(report, stage_values=tuple(values.items())))
    with pytest.raises(TimetableValidationError, match="objective_stage_plan_changed"):
        validate_combined_result(data[0], config, tampered, **context)


def test_an_unfinished_later_stage_keeps_earlier_proofs_and_never_claims_optimal(monkeypatch):
    native, calls = cp_model.CpSolver.solve, []

    def stop_at_thin_days(self, model, *args, **kwargs):
        status = native(self, model, *args, **kwargs)
        calls.append(status)
        # Constant delivery stages need no solve: peak, total, max deviation, student, staff, then thin days (6th).
        return cp_model.FEASIBLE if len(calls) == 6 else status

    monkeypatch.setattr(cp_model.CpSolver, "solve", stop_at_thin_days)
    _, _, _, result = combined_solve()
    report = result.objective_stage_report
    assert result.status is SolverStatus.FEASIBLE and not report.all_stages_proven_optimal
    assert report.proven_stage_count == 7 and [row.status for row in report.stages][-1] == "FEASIBLE"
    assert [row.criterion for row in report.stages] == list(FULL_ORDER[:8])  # Later stages were never run.
    assert not result.fairness_report.all_stages_proven_optimal  # Its preference and aggregate stages did not run.


@pytest.mark.parametrize("order,message", [
    (PLAN[:-1], "missing"),
    (("MAX_STAFF_DAILY_MINUTES", "MAX_STUDENT_DAILY_MINUTES", *PLAN[2:]), "reorders"),
    (("AGGREGATE_TARGET_DEVIATION", *PLAN[:5]), "placement"),  # Contradicts BEFORE_AGGREGATE_TARGET_DEVIATION.
    (("THIN_TEACHING_DAYS", "MAX_STUDENT_DAILY_MINUTES", "MAX_STAFF_DAILY_MINUTES", *PLAN[3:]), "relative_to_compactness"),
])
def test_invalid_or_inconsistent_plans_are_rejected(order, message):
    data = base_data(limit=5.0)
    with pytest.raises(SolverInputError, match=message):
        solve_timetable(data[0], planned(data[1], order), **contexts(data))


def test_plans_listing_criteria_that_are_not_enabled_are_rejected():
    data = base_data(limit=5.0)
    with pytest.raises(SolverInputError, match="not enabled"):
        solve_timetable(data[0], planned(data[1]), **contexts(data, consecutive=False))


def test_priority_modes_that_put_preferences_inside_the_protected_prefix_are_rejected():
    data = base_data(limit=5.0)
    context = contexts(data, consecutive=False)
    context["fairness_config"] = fairness_options()  # EXISTING_OBJECTIVES_THEN_FAIRNESS: preferences precede fairness.
    order = [key for key in PLAN if key != "CONSECUTIVE_PENALTY"]
    with pytest.raises(SolverInputError, match="inside that prefix"):
        solve_timetable(data[0], planned(data[1], order), **context)


def test_without_a_plan_previous_restrictions_and_payloads_are_unchanged():
    data = base_data(limit=5.0)
    config = compact(data[1], minimum=60)
    with pytest.raises(SolverInputError, match="not yet combined with consecutive"):
        solve_timetable(data[0], config, **contexts(data))
    assert "objective_stage_plan" not in data[1].model_dump(round_trip=True)
    from test_fairness_solver import run as fairness_run
    _, _, legacy = fairness_run(base_data(limit=5.0), fairness_config=fairness_options(priority=PEAK))
    assert legacy.objective_stage_report is None and "objective_stage_plan" not in legacy.to_dict()


def test_before_after_quality_metrics_are_measured_not_assumed(solved):
    data, config, context, result = solved
    from test_fairness_solver import run as fairness_run
    _, original, baseline = fairness_run(base_data(), fairness_config=fairness_options(priority=PEAK))
    before = assess_consecutive_sessions(data[0], data[1], baseline, calendar(data[1]), scoring(), **original)
    after = result.consecutive_report.assessment
    # Proven lexicographic order: student peak first, so it can only match or improve the baseline.
    q_before = timetable_quality(data[0], baseline.schedule).summary(preferred_minimum_minutes=60)
    q_after = timetable_quality(data[0], result.schedule).summary(preferred_minimum_minutes=60)
    assert q_after["student_groups"]["maximum_day_minutes"] <= q_before["student_groups"]["maximum_day_minutes"]
    assert before.total_soft_penalty is not None and after.total_soft_penalty is not None


def sealed_institution(order, *, tenant=None):
    """P2.4 batch online days + a P2.5 hard block + P2.1/P2.2/P2.3/P2.5 under one sealed institution plan."""
    from test_batch_online_days import REFERENCE, build
    from test_institution_policy import revision
    from test_persistence_contracts import snapshot_for
    from test_preference_solver import options as preference_options
    from schedence_teaching.persistence.policy import resolve_snapshot_policy
    from schedence_teaching.persistence.snapshots import replay_snapshot
    from schedence_teaching.preferences import LecturerPreferenceInput
    from schedence_teaching.solver import StaffBlockedInterval
    prepared, config, staff, rooms = build({"A": dict(year=1, lectures=3, child_lectures=1, labs=2)})
    for calendar_row in staff.availability:
        if calendar_row.staff_id == "L-A":  # P2.5 hard Not Available: the lecturer's whole Monday.
            calendar_row.blocked_periods.append(StaffBlockedInterval(day="MONDAY", start_minute=0, end_minute=1440))
    quality = compact(daily(config, (STUDENT, STAFF), relative="DAILY_LOAD_THEN_COMPACTNESS", reference=REFERENCE),
                      minimum=120, reference=REFERENCE)
    config = SolverConfig.model_validate({**quality.model_dump(round_trip=True),
                                          "objective_stage_plan": dict(plan_reference=REFERENCE, order=list(order))})
    preferences = LecturerPreferenceInput(preferences=[preference(staff_id="L-A", direction="AVOID", days=("TUESDAY",),
        confirmation_status="CONFIRMED", penalty_weight=2, weight_reference="synthetic:test-weight")])
    document = snapshot_for(prepared, config, contexts=dict(staff_input=staff, venue_input=rooms,
        preference_input=preferences, preference_policy=preference_policy(weight_mode="CONFIRMED_WEIGHTS"),
        preference_config=preference_options(config), consecutive_policy=scoring(),
        consecutive_config=ConsecutiveOptimizationConfig.model_validate({"priority": "EXISTING_OBJECTIVES_THEN_CONSECUTIVE",
            "priority_policy_reference": REFERENCE, "formulation": "TIME_INDEXED"})))
    if tenant is not None:
        document.institution_id = tenant
    bound = resolve_snapshot_policy(document, revision(document))
    _, sealed_prepared, sealed_config, sealed_contexts = replay_snapshot(bound)
    result = solve_timetable(sealed_prepared, sealed_config, **sealed_contexts)
    validate_combined_result(sealed_prepared, sealed_config, result, **sealed_contexts)
    return bound, sealed_prepared, result


SEALED_ORDER = ("MAX_STUDENT_DAILY_MINUTES", "MAX_STAFF_DAILY_MINUTES", "THIN_TEACHING_DAYS", "CONSECUTIVE_PENALTY", "PREFERENCE_PENALTY")


def test_hard_rules_hold_with_every_soft_objective_enabled():
    from test_batch_online_days import assert_online_day_rules, selected
    _, prepared, result = sealed_institution(SEALED_ORDER)
    assert result.status is SolverStatus.OPTIMAL and len(result.schedule) == len(prepared.sessions)
    assert {"batch_online_day_selection", "staff_blocked_periods", "staff_clashes", "same_group_and_ancestor_clashes",
            "rank_appointment_session_eligibility"} <= set(result.validation.checks)
    ((_, day),) = selected(result).items()
    assert_online_day_rules(result, "A", day)  # Only online lectures on the batch's selected day.
    assert not any(row.assigned_staff_id == "L-A" and row.day == "MONDAY" for row in result.schedule)  # Hard block kept.
    assert {(row.session.session_type.value, row.assigned_staff_id) for row in result.schedule} == {("LECTURE", "L-A"), ("LAB", "I-A")}
    # The child group's daily load includes the inherited parent lectures.
    child = [row for row in timetable_quality(prepared, result.schedule).student_days if row.entity_id == "A-G.L1"]
    assert sum(row.teaching_minutes for row in child) == sum(s.duration_minutes for s in prepared.sessions)
    assert result.objective_stage_report.priority_order == ("DELIVERIES", "CONTACT_MINUTES", *SEALED_ORDER)


def test_two_institutions_keep_their_own_sealed_plans():
    from test_persistence_contracts import TENANT, OTHER_TENANT
    other = ("PREFERENCE_PENALTY", "CONSECUTIVE_PENALTY", "MAX_STUDENT_DAILY_MINUTES", "MAX_STAFF_DAILY_MINUTES", "THIN_TEACHING_DAYS")
    first, _, first_result = sealed_institution(SEALED_ORDER, tenant=TENANT)
    second, _, second_result = sealed_institution(other, tenant=OTHER_TENANT)
    assert first.institution_id != second.institution_id and first.resolved_policy_digest != second.resolved_policy_digest
    assert first_result.objective_stage_report.priority_order[2:] == SEALED_ORDER
    assert second_result.objective_stage_report.priority_order[2:] == other
    again, _, _ = sealed_institution(SEALED_ORDER, tenant=TENANT)
    assert again.resolved_policy_digest == first.resolved_policy_digest  # Unaffected by the other institution.


def test_sealed_plan_reference_must_match_the_institution_revision():
    from test_batch_online_days import build
    from test_institution_policy import revision
    from test_persistence_contracts import snapshot_for
    from schedence_teaching.persistence.policy import resolve_snapshot_policy
    prepared, config, staff, rooms = build({"A": dict(year=1, lectures=2)}, enabled=False)
    config = SolverConfig.model_validate({**config.model_dump(round_trip=True), "objective_stage_plan": dict(
        plan_reference="SYNTHETIC:SOME-OTHER-POLICY", order=["PREFERENCE_PENALTY"])})
    document = snapshot_for(prepared, config, contexts=dict(staff_input=staff, venue_input=rooms))
    with pytest.raises(ValueError, match="Objective stage plan reference"):
        resolve_snapshot_policy(document, revision(document))


def test_both_consecutive_formulations_agree_on_every_stage_both_prove(solved):
    _, _, _, grid = solved
    # Both are exact; within a bounded budget the circuit may stop early and must then say FEASIBLE.
    _, _, _, circuit = combined_solve(base_data(limit=30.0), formulation="SUCCESSOR_CIRCUIT")
    assert grid.status is SolverStatus.OPTIMAL and circuit.status in {SolverStatus.OPTIMAL, SolverStatus.FEASIBLE}
    proven = circuit.objective_stage_report.proven_stage_count
    assert proven >= 5  # The protected workload stages are proven by both.
    circuit_values, grid_values = dict(circuit.objective_stage_report.stage_values), dict(grid.objective_stage_report.stage_values)
    assert all(circuit_values[key] == grid_values[key] for key in FULL_ORDER[:proven])
