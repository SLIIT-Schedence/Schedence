"""Synthetic, independently validated workload distribution; no fairness scheduling."""

from dataclasses import replace
from fractions import Fraction
import json
from pathlib import Path
import runpy

import pytest
from pydantic import ValidationError

from schedence_teaching.fairness import (
    FairnessInputError, FairnessPolicy, assess_fairness, teaching_target_metrics, validate_fairness_report,
)
from schedence_teaching.preparation import prepare_timetable_input
from schedence_teaching.schemas import TeachingTimetableInput
from schedence_teaching.solver import StaffBlockedInterval, TimetableValidationError, solve_timetable, validate_timetable
from schedence_teaching.workload import StaffAppointment, WorkloadBand, WorkloadRule, confirmed_campus_policy, prepare_work_periods
from schedence_teaching.workload.period_contracts import AdministrativePolicyResolution

from test_combination_solver import inputs as combination_source, solve as combination_solve, venues
from test_preferences import calendar
from test_staff_solver import demand, windows
from test_work_period_solver import scenario, run as protected_run
from test_work_periods import FIRST, admin, appointment, research, research_policy


def policy(**updates):
    return FairnessPolicy.model_validate({"policy_reference": "synthetic:fairness-assessment",
        "week_reference": "SYNTHETIC-WEEK", "provenance": {"source": "synthetic", "reference": "example-only"},
        "comparison_scope": "IDENTICAL_ELIGIBILITY_POOLS", "include_variance": True, **updates})


def individual_data(durations=None, bands=None, appointments=None, **options):
    durations = durations or {"P": 30}
    prepared = demand({identity: [(identity, "LIC")] for identity in durations})
    payload = prepared.input_snapshot.model_dump(round_trip=True)
    for row in payload["session_requirements"]:
        row["duration_minutes"] = durations[row["offering_id"]]
    prepared = prepare_timetable_input(TeachingTimetableInput.model_validate(payload))
    data = scenario(minutes=1440, prepared=prepared, **options)
    for profile in data[2].workload_input.profiles:
        profile.workload_band = WorkloadBand((bands or {}).get(profile.staff_id, "PROFESSOR"))
        profile.appointment = (appointments or {}).get(profile.staff_id)
    return data


def evaluate(data=None, *, fairness=None, result=None, context=None):
    data = data or individual_data()
    prepared, config, staff, _, _ = data
    context = context if context is not None else {"staff_input": staff}
    result = result or solve_timetable(prepared, config, **context)
    dates = calendar(config)
    report = assess_fairness(prepared, config, result, dates, fairness or policy(), **context)
    assert validate_fairness_report(report, prepared, config, result, dates, fairness or policy(), **context) == report
    return report, result


def explicit(ids, **updates):
    return policy(comparison_scope="EXPLICIT_GROUPS", comparison_groups=[{
        "group_id": "SUPPLIED", "staff_ids": ids, "reason": "synthetic comparison authorization"}], **updates)


def test_equal_normalized_utilization_and_role_specific_targets():
    data = individual_data({"L": 720, "D": 360, "P": 360}, {"L": "LECTURER", "D": "DR"})
    report, _ = evaluate(data, fairness=explicit(["L", "D", "P"]))
    rows = {row.staff_id: row for row in report.staff}
    assert [rows[key].target.target_minutes for key in ("L", "D", "P")] == [960, 480, 360]
    assert [rows[key].target.utilization for key in ("L", "D", "P")] == [Fraction(3, 4), Fraction(3, 4), Fraction(1)]
    group = report.groups[0]
    assert group.mean == Fraction(5, 6) and group.variance == Fraction(1, 72)
    assert group.maximum_deviation == Fraction(1, 6) and group.maximum_relative_deviation == Fraction(1, 5)
    assert rows["L"].target.shortfall_minutes == 240


def test_equal_raw_minutes_are_not_equal_normalized_workloads():
    report, _ = evaluate(individual_data({"L": 360, "D": 360}, {"L": "LECTURER", "D": "DR"}),
        fairness=explicit(["L", "D"]))
    assert dict(report.groups[0].distribution) == {"D": Fraction(3, 4), "L": Fraction(3, 8)}
    assert report.groups[0].variance > 0


def test_equal_normalized_workloads_have_zero_variance_despite_different_raw_hours():
    report, _ = evaluate(individual_data({"L": 720, "D": 360}, {"L": "LECTURER", "D": "DR"}),
        fairness=explicit(["L", "D"]))
    assert report.groups[0].variance == report.groups[0].maximum_deviation == 0


@pytest.mark.parametrize("band,appointment_name,target,admin_minutes,exempt", [
    ("INSTRUCTOR", None, 1200, None, False), ("ASSISTANT_LECTURER", None, 1080, None, False),
    ("LECTURER", None, 960, None, False), ("SENIOR_LECTURER", None, 720, None, False),
    ("DR", None, 480, None, False), ("ASSISTANT_PROFESSOR", None, 360, None, False),
    ("PROFESSOR", None, 360, 240, True), ("LECTURER", "HOD", 240, 240, True),
    ("SENIOR_LECTURER", "DEAN", 240, 240, True),
])
def test_reuses_confirmed_classification_and_appointment_policies(band, appointment_name, target, admin_minutes, exempt):
    data = scenario(band=band, appointment_name=appointment_name)
    if band == "INSTRUCTOR":
        payload = data[0].input_snapshot.model_dump(round_trip=True)
        payload["staff_members"][0]["staff_type"] = "INSTRUCTOR"
        payload["teaching_team_members"][0]["role"] = "TEAM_MEMBER"
        data = scenario(prepared=prepare_timetable_input(TeachingTimetableInput.model_validate(payload)))
    report, _ = evaluate(data)
    row = report.staff[0]
    assert row.target.target_minutes == target and row.administrative_allocation_minutes == admin_minutes
    assert row.workload_band == band and row.appointment == appointment_name and row.campus_day_exempt is exempt
    assert row.verified_administrative_minutes == 0 and not row.recognized_work_ledger_complete
    assert row.evidenced_campus_days == () and not row.campus_attendance_complete


@pytest.mark.parametrize("layout,expected", [("combined", 60), ("mixed", 90), ("separate", 120)])
def test_combined_mixed_and_separate_count_each_physical_booking_once(layout, expected):
    source = combination_source()
    rooms = venues() if layout == "combined" else venues(large_minutes=30 if layout == "mixed" else 15)
    combinations, config, result = combination_solve(source, rooms=rooms)
    staff = scenario(prepared=combinations.prepared, minutes=120)[2]
    report = assess_fairness(combinations.prepared, config, result, calendar(config),
        policy(require_availability_evidence=False), workload_input=staff.workload_input,
        combination_input=combinations, venue_input=rooms)
    assert report.scheduled_teaching_minutes == expected and report.assigned_delivery_count == expected // 30
    assert report.staff[0].teaching.scheduled_teaching_minutes == expected
    assert len(report.staff[0].opportunities.eligible_occurrence_ids) == 4
    assert not report.staff[0].opportunities.joint_feasibility_established


def test_eligibility_pools_are_offering_based_and_multi_module_opportunities_are_explicit():
    prepared = demand({"A": [("P", "LIC"), ("Q", "TEAM_MEMBER")],
                       "B": [("P", "LIC"), ("R", "TEAM_MEMBER")]}, extra_staff=["UNASSIGNED"])
    report, _ = evaluate(scenario(prepared=prepared))
    rows = {row.staff_id: row for row in report.staff}
    assert rows["P"].opportunities.eligible_offering_ids == ("A", "B")
    assert len(rows["P"].opportunities.eligible_requirement_ids) == 2
    assert rows["Q"].opportunities.eligible_offering_ids == ("A",)
    assert rows["R"].opportunities.eligible_offering_ids == ("B",)
    assert not rows["UNASSIGNED"].included_in_comparison and rows["UNASSIGNED"].teaching.assigned_delivery_count == 0
    assert "no_eligible_demand_in_scope" in rows["UNASSIGNED"].comparison_reasons
    assert {group.included_staff_ids for group in report.groups} == {("P",), ("Q",), ("R",)}


def test_zero_assigned_deliveries_still_have_a_target_and_eligibility():
    data = scenario(prepared=demand({"A": [("P", "LIC"), ("Q", "TEAM_MEMBER")]}))
    report, _ = evaluate(data)
    unassigned = next(row for row in report.staff if row.teaching.assigned_delivery_count == 0)
    assert unassigned.target.utilization == 0 and unassigned.target.shortfall_minutes == 360
    assert unassigned.included_in_comparison and unassigned.opportunities.eligible_offering_ids == ("A",)


@pytest.mark.parametrize("restriction", ["no_days", "blocked", "expired", "weekly_cap", "daily_cap", "short_window", "off_grid"])
def test_calendar_constraints_distinguish_unavailable_staff(restriction):
    data = scenario(minutes=30, prepared=demand({"A": [("P", "LIC"), ("Q", "TEAM_MEMBER")]}))
    row = data[2].availability[0]
    if restriction == "no_days":
        row.allowed_teaching_days = set()
        row.available_windows = {}
    elif restriction == "blocked":
        row.blocked_periods = [StaffBlockedInterval(day="DAY", start_minute=0, end_minute=30)]
    elif restriction == "expired":
        row.effective_from = row.effective_until = FIRST.replace(day=12)
    elif restriction == "short_window":
        row.available_windows["DAY"][0].end_minute = 15
    elif restriction == "off_grid":
        row.available_windows["DAY"][0].start_minute = 1
        row.available_windows["DAY"][0].end_minute = 31
    else:
        setattr(row, f"{restriction.split('_')[0]}_teaching_max_minutes", 0)
    report, _ = evaluate(data)
    assert report.staff[0].opportunities.individually_available_occurrence_ids == ()
    assert not report.staff[0].included_in_comparison
    assert "no_individual_fit_under_supplied_hard_restrictions" in report.staff[0].comparison_reasons


def test_research_and_admin_protection_reduce_opportunities_without_work_credit():
    prepared = demand({"A": [("STAFF", "LIC"), ("OTHER", "TEAM_MEMBER")]})
    data = scenario(minutes=60, prepared=prepared)
    data[3].research_policies = [research_policy()]
    data[3].research_reservations = [research(on_date=FIRST)]
    data[3].administrative_periods = [admin(data[2].workload_input, staff_id="OTHER",
        window={"start_minute": 0, "end_minute": 30}, planned_duration_minutes=30)]
    result, context = protected_run(data)
    report, _ = evaluate(data, result=result, context=context)
    rows = {row.staff_id: row for row in report.staff}
    assert rows["STAFF"].hard_research_protection_minutes == 1440
    assert not rows["STAFF"].included_in_comparison and rows["STAFF"].teaching.scheduled_teaching_minutes == 0
    assert rows["OTHER"].reserved_administrative_minutes == 30
    assert rows["OTHER"].teaching.scheduled_teaching_minutes == 30 and rows["OTHER"].verified_administrative_minutes == 0


@pytest.mark.parametrize("target,actual,ratio,status,shortfall,excess", [
    (960, 720, Fraction(3, 4), "BELOW_TARGET", 240, 0),
    (360, 360, Fraction(1), "MET", 0, 0), (240, 300, Fraction(5, 4), "ABOVE_TARGET", 0, 60),
    (None, 0, None, "MISSING_TARGET", None, None), (None, 30, None, "MISSING_TARGET", None, None),
    (0, 0, None, "ZERO_TARGET", 0, 0), (0, 30, None, "ZERO_TARGET", 0, 30),
])
def test_exact_target_arithmetic_handles_missing_and_zero_without_fabrication(target, actual, ratio, status, shortfall, excess):
    row = teaching_target_metrics(actual, target)
    assert (row.utilization, row.status, row.shortfall_minutes, row.excess_minutes) == (ratio, status, shortfall, excess)


def test_existing_workload_contract_continues_to_reject_zero_targets():
    with pytest.raises(ValidationError):
        WorkloadRule(weekly_target_minutes=0)


@pytest.mark.parametrize("actual,target", [(-1, 10), (True, 10), (1.5, 10), (0, -1), (0, True), (0, "30")])
def test_target_helper_rejects_invalid_numeric_evidence(actual, target):
    with pytest.raises(ValueError):
        teaching_target_metrics(actual, target)


@pytest.mark.parametrize("cause", ["no_workload", "no_profile", "no_rule", "no_override", "legacy_total"])
def test_missing_policy_evidence_is_diagnosed_without_rank_fallback(cause):
    data = scenario()
    prepared, config, staff, _, _ = data
    source = staff.workload_input.model_copy(deep=True)
    if cause == "no_profile":
        source.profiles = []
    elif cause == "no_rule":
        source.policy.band_rules = {}
    elif cause == "no_override":
        source.profiles[0].appointment = StaffAppointment.HOD
        source.policy.appointment_overrides = {}
    elif cause == "legacy_total":
        source.policy = confirmed_campus_policy(target_basis="TOTAL_RECOGNIZED_WORK")
    result = solve_timetable(prepared, config)
    report = assess_fairness(prepared, config, result, calendar(config), policy(require_availability_evidence=False),
        workload_input=None if cause == "no_workload" else source)
    row = report.staff[0]
    assert row.target.utilization is None and row.target.target_minutes is None
    assert not row.included_in_comparison and "missing_separate_teaching_target" in row.missing_evidence
    assert report.groups[0].mean is None


def test_missing_evidence_can_fail_explicitly_or_exclude_with_a_reason():
    data = scenario()
    result = solve_timetable(data[0], data[1])
    with pytest.raises(FairnessInputError, match="Missing fairness comparison evidence"):
        assess_fairness(data[0], data[1], result, calendar(data[1]), policy(missing_evidence_behavior="ERROR"))
    report = assess_fairness(data[0], data[1], result, calendar(data[1]), policy())
    assert "staff_availability_not_supplied" in report.staff[0].missing_evidence


def test_above_target_is_reported_but_not_a_cap_violation():
    report, result = evaluate(individual_data({"P": 390}))
    assert report.staff[0].target.utilization == Fraction(13, 12)
    assert report.staff[0].target.excess_minutes == 30 and report.staff[0].weekly_teaching_cap_minutes is None
    assert result.validation.valid_for_implemented_constraints


@pytest.mark.parametrize("kind", ["daily", "weekly"])
def test_explicit_teaching_caps_are_still_hard_invalid(kind):
    data = individual_data({"P": 390})
    result = solve_timetable(data[0], data[1], staff_input=data[2])
    setattr(data[2].availability[0], f"{kind}_teaching_max_minutes", 360)
    with pytest.raises(TimetableValidationError, match="teaching_cap"):
        assess_fairness(data[0], data[1], result, calendar(data[1]), policy(), staff_input=data[2])


def test_consecutive_runs_daily_load_and_supplied_zero_days():
    prepared = demand({"A": [("STAFF", "LIC")]}, [("A", "G", 4)])
    config = windows(120, days=("ONE", "TWO", "THREE"))
    result = solve_timetable(prepared, config)
    schedule = tuple(replace(row, day=day, start_minute=start, end_minute=start + 30) for row, (day, start) in
        zip(result.schedule, [("ONE", 0), ("ONE", 30), ("ONE", 90), ("TWO", 0)], strict=True))
    result = replace(result, schedule=schedule, validation=validate_timetable(prepared, config, schedule))
    source = scenario(prepared=prepared)[2].workload_input
    report = assess_fairness(prepared, config, result, calendar(config), policy(require_availability_evidence=False),
        workload_input=source)
    row = report.staff[0].teaching
    assert row.daily_teaching_minutes == (("ONE", 90), ("TWO", 30), ("THREE", 0))
    assert row.teaching_day_count == 2 and row.maximum_daily_teaching_minutes == 90
    assert row.longest_consecutive_teaching_minutes == 60 and row.assigned_delivery_count == 4


def test_explicit_group_filter_and_staff_decisions_preserve_original_metrics():
    data = individual_data({"P": 30, "Q": 60, "R": 90})
    report, _ = evaluate(data, fairness=explicit(["P", "Q"], staff_decisions=[
        {"staff_id": "Q", "include": False, "reason": "supplied separate comparison scope"},
        {"staff_id": "P", "include": True, "reason": "approved synthetic cohort"}]))
    assert report.groups[0].included_staff_ids == ("P",)
    assert report.staff[1].teaching.scheduled_teaching_minutes == 60
    assert report.staff[2].comparison_reasons == ("outside_comparison_groups",)
    assert "explicit_exclusion" in report.staff[1].comparison_reasons[0]


def test_offering_filter_changes_eligibility_scope_not_weekly_workload():
    data = individual_data({"P": 30, "Q": 60})
    report, _ = evaluate(data, fairness=policy(eligibility_offering_ids={"P"}))
    assert report.staff[1].teaching.scheduled_teaching_minutes == 60
    assert not report.staff[1].included_in_comparison and not report.staff[1].opportunities.eligible_occurrence_ids


@pytest.mark.parametrize("metric", ["MAXIMUM_DEVIATION", "MEAN_ABSOLUTE_DEVIATION", "VARIANCE"])
def test_optional_exact_weights_distribution_and_soft_relative_threshold(metric):
    data = individual_data({"P": 30, "Q": 90})
    report, _ = evaluate(data, fairness=explicit(["P", "Q"], normalization="RAW_MINUTES", balance_metric=metric,
        staff_weights={"P": "3", "Q": "1"}, weight_reference="synthetic:weights", relative_deviation_threshold="1/2"))
    group = report.groups[0]
    assert group.mean == 45 and group.variance == 675 and group.mean_absolute_deviation == Fraction(45, 2)
    assert group.maximum_deviation == 45 and group.maximum_relative_deviation == 1
    assert group.relative_threshold_exceeded_staff_ids == ("Q",)
    assert group.balance_value == {"MAXIMUM_DEVIATION": 45, "MEAN_ABSOLUTE_DEVIATION": Fraction(45, 2), "VARIANCE": 675}[metric]


def test_optional_statistics_can_be_disabled():
    report, _ = evaluate(fairness=policy(balance_metric="MEAN_ABSOLUTE_DEVIATION",
        include_maximum_deviation=False, include_variance=False))
    assert report.groups[0].variance is report.groups[0].maximum_deviation is None


@pytest.mark.parametrize("cause", ["scope", "weights", "empty_group", "duplicate_group", "duplicate_staff",
    "decision_reason", "duplicate_decision", "negative_threshold", "float_threshold", "bool_weight", "zero_weight",
    "variance_flag", "maximum_flag", "blank_week", "extra_field"])
def test_invalid_fairness_contracts(cause):
    changes = {"scope": {"comparison_scope": "EXPLICIT_GROUPS"}, "weights": {"staff_weights": {"P": 1}},
        "empty_group": {"comparison_scope": "EXPLICIT_GROUPS", "comparison_groups": [{"group_id": "G", "staff_ids": [], "reason": "test"}]},
        "duplicate_group": {"comparison_scope": "EXPLICIT_GROUPS", "comparison_groups": [{"group_id": "G", "staff_ids": ["P"], "reason": "test"}] * 2},
        "duplicate_staff": {"comparison_scope": "EXPLICIT_GROUPS", "comparison_groups": [{"group_id": "G", "staff_ids": ["P", "P"], "reason": "test"}]},
        "decision_reason": {"staff_decisions": [{"staff_id": "P", "include": False, "reason": " "}]},
        "duplicate_decision": {"staff_decisions": [{"staff_id": "P", "include": False, "reason": "test"}] * 2},
        "negative_threshold": {"relative_deviation_threshold": -1}, "float_threshold": {"relative_deviation_threshold": 0.5},
        "bool_weight": {"staff_weights": {"P": True}, "weight_reference": "synthetic"},
        "zero_weight": {"staff_weights": {"P": 0}, "weight_reference": "synthetic"},
        "variance_flag": {"balance_metric": "VARIANCE", "include_variance": False},
        "maximum_flag": {"include_maximum_deviation": False}, "blank_week": {"week_reference": " "},
        "extra_field": {"invented_university_rule": True}}[cause]
    with pytest.raises(ValidationError):
        policy(**changes)


@pytest.mark.parametrize("cause", ["staff", "offering", "week", "calendar", "workload_week", "workload_reference"])
def test_invalid_source_references_and_weeks(cause):
    data = individual_data()
    prepared, config, staff, _, _ = data
    result = solve_timetable(prepared, config)
    source = staff.workload_input.model_copy(deep=True)
    settings = policy(require_availability_evidence=False)
    dates = calendar(config)
    if cause == "staff":
        settings = explicit(["UNKNOWN"])
    elif cause == "offering":
        settings.eligibility_offering_ids = {"UNKNOWN"}
    elif cause == "week":
        settings.week_reference = "OTHER"
    elif cause == "calendar":
        dates.day_dates = {}
    elif cause == "workload_week":
        source.week_reference = "OTHER"
    else:
        source.recognized_work_complete_for = {"UNKNOWN"}
    with pytest.raises(ValueError):
        assess_fairness(prepared, config, result, dates, settings, workload_input=source)


@pytest.mark.parametrize("resolved", [True, False])
def test_multiple_appointments_never_invent_a_teaching_override(resolved):
    data = scenario(appointment_name="HOD")
    data[3].appointments = [appointment(), appointment(identity="APPT-DEAN", position_code="DEAN")]
    if resolved:
        data[3].administrative_resolutions = [AdministrativePolicyResolution.model_validate({"staff_id": "STAFF", "appointment_ids": ["APPT-HOD", "APPT-DEAN"],
            "status": "COMPUTED", "allocation_minutes": 240, "rule": "UPSTREAM_REPLACEMENT",
            "resolution_reference": "synthetic:upstream", "effective_from": FIRST,
            "provenance": {"source": "synthetic", "reference": "upstream"}})]
    source = type(data[3]).model_validate(data[3].model_dump())
    periods = prepare_work_periods(data[0].input_snapshot, data[2].workload_input, source)
    context = {"staff_input": data[2], "work_periods": periods, "work_period_config": data[4]}
    report, _ = evaluate(data, context=context)
    row = report.staff[0]
    assert not row.included_in_comparison and row.workload_band == "PROFESSOR" and row.appointment == "HOD"
    assert "multiple_appointments_teaching_override_requires_reconciliation" in row.missing_evidence
    assert ("unresolved_administrative_appointment_aggregation" in row.missing_evidence) is not resolved


@pytest.mark.parametrize("cause", ["workload_minutes", "workload_daily", "workload_attendance", "workload_missing",
    "schedule_duration", "staff_eligibility", "validation", "failed", "missing_staff_context"])
def test_corrupted_results_are_rejected_before_fairness_scoring(cause):
    data = individual_data()
    result = solve_timetable(data[0], data[1], staff_input=data[2])
    context = {"staff_input": data[2]}
    if cause.startswith("workload_"):
        row = result.workload_report.staff[0]
        if cause == "workload_minutes":
            row = replace(row, scheduled_teaching_minutes=1)
        elif cause == "workload_daily":
            row = replace(row, daily_teaching_minutes=(("DAY", 1),))
        elif cause == "workload_attendance":
            row = replace(row, campus_attendance_complete=True)
        result = replace(result, workload_report=None if cause == "workload_missing" else replace(result.workload_report, staff=(row,)))
    elif cause in {"schedule_duration", "staff_eligibility"}:
        changes = {"end_minute": result.schedule[0].end_minute + 15} if cause == "schedule_duration" else {"assigned_staff_id": "UNKNOWN"}
        result = replace(result, schedule=(replace(result.schedule[0], **changes),))
    elif cause == "validation":
        result = replace(result, validation=replace(result.validation, assigned_occurrences=0))
    elif cause == "failed":
        from schedence_teaching.solver import SolverStatus
        result = replace(result, status=SolverStatus.UNKNOWN)
    else:
        context = {}
    with pytest.raises(ValueError):
        assess_fairness(data[0], data[1], result, calendar(data[1]), policy(), **context)


def test_fairness_report_validation_detects_tampering_including_bool_integer_changes():
    data = individual_data()
    report, result = evaluate(data)
    for changed in [replace(report, scheduled_teaching_minutes=31),
                    replace(report, cp_sat_fairness_objective_enabled=True),
                    replace(report, assigned_delivery_count=True),
                    replace(report, staff=(replace(report.staff[0], target=teaching_target_metrics(999, 360)),))]:
        with pytest.raises(FairnessInputError, match="independently recomputed"):
            validate_fairness_report(changed, data[0], data[1], result, calendar(data[1]), policy(), staff_input=data[2])


def test_assessment_is_independent_deterministic_serializable_and_does_not_mutate_inputs():
    data = individual_data()
    before = (data[0].input_snapshot.model_dump_json(), data[2].model_dump_json())
    report, result = evaluate(data)
    again, _ = evaluate(data, result=result)
    assert report == again
    assert before == (data[0].input_snapshot.model_dump_json(), data[2].model_dump_json())
    output = report.to_dict(include_staff_details=True)
    assert output["staff"][0]["target"]["utilization"] == {"numerator": 1, "denominator": 12}
    assert json.loads(json.dumps(output)) == output
    assert not output["cp_sat_fairness_objective_enabled"] and not output["hard_constraints_overridden"]
    assert "staff" not in report.to_dict()


def test_synthetic_demo_runs(capsys):
    runpy.run_path(str(Path(__file__).resolve().parents[1] / "examples" / "fairness-assessment-demo.py"), run_name="__main__")
    output = capsys.readouterr().out
    assert "synthetic" in output and "3/4" in output and "cp_sat_fairness_objective_enabled" in output


@pytest.mark.parametrize("with_staff", [True, False])
def test_preference_optimized_results_keep_original_objective_and_require_full_context(with_staff):
    from test_preference_solver import run as preference_run
    data = scenario()
    hard = {"staff_input": data[2]} if with_staff else {}
    prepared, config, result, preferences = preference_run(prepared=data[0], config=data[1], **hard)
    before = repr(result)
    source = {} if with_staff else {"workload_input": data[2].workload_input}
    settings = policy(require_availability_evidence=with_staff)
    report = assess_fairness(prepared, config, result, preferences["preference_calendar"], settings,
        **preferences, **hard, **source)
    assert report.preference_optimization_enabled and not report.cp_sat_fairness_objective_enabled
    assert report.scheduled_teaching_minutes == 30 and repr(result) == before
    with pytest.raises(ValueError):
        assess_fairness(prepared, config, result, preferences["preference_calendar"], settings, **hard, **source)
    corrupt = replace(result, preference_report=replace(result.preference_report, objective_value=999))
    with pytest.raises(ValueError):
        assess_fairness(prepared, config, corrupt, preferences["preference_calendar"], settings, **preferences, **hard, **source)


def test_flexible_administrative_reservations_are_not_credited_as_teaching_or_completed_work():
    from test_work_period_solver import with_flexible
    data = scenario(minutes=300)
    with_flexible(data[2], data[3], duration=240)
    result, context = protected_run(data)
    report, _ = evaluate(data, result=result, context=context)
    row = report.staff[0]
    assert row.reserved_administrative_minutes == row.administrative_allocation_minutes == 240
    assert row.teaching.scheduled_teaching_minutes == 30 and row.verified_administrative_minutes == 0
    assert row.target.shortfall_minutes == 330 and row.target.utilization == Fraction(1, 12)


def test_only_explicit_completed_admin_credits_are_reported_and_targets_remain_teaching_only():
    from schedence_teaching.workload import RecognizedWorkRecord
    data = scenario()
    data[2].workload_input.recognized_work = [RecognizedWorkRecord(work_id="SYNTHETIC-CREDIT", staff_id="STAFF",
        work_type="ADMINISTRATIVE", duration_minutes=240, evidence_reference="synthetic:verified-completion")]
    data[2].workload_input.recognized_work_complete_for = {"STAFF"}
    report, _ = evaluate(data)
    row = report.staff[0]
    assert row.verified_administrative_minutes == 240 and row.recognized_work_ledger_complete
    assert row.teaching.scheduled_teaching_minutes == 30 and row.target.shortfall_minutes == 330
    assert row.target.utilization == Fraction(1, 12) and row.reserved_administrative_minutes is None


def test_empty_demand_and_zero_mean_do_not_claim_an_unfair_workload_or_divide_by_zero():
    data = scenario(prepared=demand({"A": [("P", "LIC")]}, requirements=[]))
    report, _ = evaluate(data, fairness=explicit(["P"]))
    assert report.scheduled_teaching_minutes == report.assigned_delivery_count == 0
    assert report.groups[0].mean is None and report.groups[0].balance_value is None
    assert report.staff[0].target.utilization == 0 and not report.staff[0].included_in_comparison


def test_explicit_inclusion_cannot_override_missing_evidence():
    data = scenario()
    result = solve_timetable(data[0], data[1])
    report = assess_fairness(data[0], data[1], result, calendar(data[1]), policy(staff_decisions=[
        {"staff_id": "STAFF", "include": True, "reason": "synthetic request to compare"}]))
    assert not report.staff[0].included_in_comparison and report.staff[0].target.utilization is None


def test_explicit_exclusion_can_skip_strict_missing_evidence_without_hiding_metrics():
    data = scenario()
    result = solve_timetable(data[0], data[1])
    report = assess_fairness(data[0], data[1], result, calendar(data[1]), policy(
        missing_evidence_behavior="ERROR", staff_decisions=[{"staff_id": "STAFF", "include": False,
        "reason": "authoritative workload snapshot still pending"}]))
    assert report.staff[0].teaching.scheduled_teaching_minutes == 30 and not report.staff[0].included_in_comparison


def test_conflicting_academic_instructor_classification_is_invalid_not_missing_evidence():
    data = scenario()
    result = solve_timetable(data[0], data[1])
    source = data[2].workload_input.model_copy(deep=True)
    source.profiles[0].workload_band = WorkloadBand.INSTRUCTOR
    with pytest.raises(FairnessInputError, match="academic/instructor distinction"):
        assess_fairness(data[0], data[1], result, calendar(data[1]), policy(), workload_input=source)


@pytest.mark.parametrize("cause", ["summary_bool", "combination_bool", "preference_bool"])
def test_bool_is_not_accepted_as_an_integer_summary_or_resource_mode(cause):
    data = scenario()
    result = solve_timetable(data[0], data[1])
    if cause == "summary_bool":
        result = replace(result, validation=replace(result.validation, assigned_occurrences=True))
    elif cause == "combination_bool":
        result = replace(result, combination_enabled=0)
    else:
        result = replace(result, preference_optimization_enabled=0)
    with pytest.raises(ValueError):
        assess_fairness(data[0], data[1], result, calendar(data[1]), policy())


def test_zero_utilization_cohort_has_no_relative_deviation_or_threshold_verdict():
    prepared = demand({"A": [("ASSIGNED", "LIC"), ("ZERO1", "TEAM_MEMBER"), ("ZERO2", "TEAM_MEMBER")]})
    data = scenario(prepared=prepared)
    result = solve_timetable(prepared, data[1], staff_input=data[2])
    zeros = [row.staff_id for row in prepared.input_snapshot.staff_members if row.staff_id != result.schedule[0].assigned_staff_id]
    report, _ = evaluate(data, result=result, fairness=explicit(zeros, relative_deviation_threshold="1/2"))
    group = report.groups[0]
    assert group.mean == group.variance == group.maximum_deviation == 0
    assert group.maximum_relative_deviation is None and not group.relative_threshold_exceeded_staff_ids
    assert "relative_deviation_undefined_for_zero_mean" in group.diagnostics


def test_assessment_builds_no_cp_model(monkeypatch):
    from ortools.sat.python import cp_model
    data = individual_data()
    result = solve_timetable(data[0], data[1], staff_input=data[2])
    def unexpected_model(*args, **kwargs):
        raise AssertionError("Fairness evaluation must not construct a CP-SAT model")
    monkeypatch.setattr(cp_model, "CpModel", unexpected_model)
    report, _ = evaluate(data, result=result)
    assert report.scheduled_teaching_minutes == 30
