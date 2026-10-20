"""Synthetic soft preference evaluation; all hard scheduling validation is reused."""

from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
import runpy

import pytest
from pydantic import ValidationError

from schedence_teaching.preferences import (
    LecturerPreference, LecturerPreferenceInput, PreferenceCalendar, PreferenceCategory,
    PreferenceConfirmation, PreferenceDirection, PreferenceExpression, PreferenceInputError,
    PreferenceScope, PreferenceScoringPolicy, PreferenceSatisfaction,
    prepare_lecturer_preferences, score_lecturer_preferences,
)
from schedence_teaching.solver import (
    SolverStatus, TimeWindow, TimetableValidationError, solve_timetable, validate_timetable,
)
from schedence_teaching.workload import confirmed_campus_policy

from test_staff_solver import demand, windows
from test_combination_solver import inputs as combination_inputs, venues
from schedence_teaching.preparation import prepare_group_combinations
from test_work_period_solver import scenario, run as protected_run
from test_work_periods import FIRST, admin, research, research_policy


MONDAY = date(2026, 10, 19)


def preference(*, identity="PREF", category="WORKING_DAY", direction="PREFER", days=("MONDAY",), **updates):
    return LecturerPreference.model_validate({"preference_id": identity, "staff_id": "STAFF", "category": category,
        "direction": direction, "days": days, "scope": {"week_reference": "SYNTHETIC-WEEK"},
        "provenance": {"source": "synthetic", "reference": "synthetic:preference-evidence"}, **updates})


def calendar(config, *, first=MONDAY, **updates):
    return PreferenceCalendar.model_validate({"week_reference": "SYNTHETIC-WEEK", "week_start": MONDAY,
        "week_end": MONDAY + timedelta(days=6), "day_dates": {
            day.day: first + timedelta(days=index) for index, day in enumerate(config.days)},
        "synthetic_data": True, **updates})


def policy(**updates):
    return PreferenceScoringPolicy.model_validate({"policy_reference": "synthetic:unweighted", **updates})


def evaluate(preferences=(), *, prepared=None, config=None, result=None, dates=None, scoring=None, **context):
    prepared = prepared or demand({"OFFER": [("STAFF", "LIC")]})
    config = config or windows()
    result = result or solve_timetable(prepared, config, **context)
    before = result.to_dict()
    inputs = LecturerPreferenceInput(preferences=list(preferences))
    prepared_preferences = prepare_lecturer_preferences(prepared.input_snapshot, inputs)
    report = score_lecturer_preferences(prepared, config, result, prepared_preferences,
        dates or calendar(config), scoring or policy(), **context)
    assert result.to_dict() == before  # Post-solve scoring cannot reschedule or mutate the result.
    return report, result


@pytest.mark.parametrize("preferred,assigned,satisfied", [
    ("SATURDAY", "SATURDAY", True), ("SATURDAY", "SUNDAY", False),
    ("SUNDAY", "SUNDAY", True), ("SUNDAY", "SATURDAY", False)])
def test_saturday_vs_sunday_preference(preferred, assigned, satisfied):
    config = windows(days=(assigned,))
    day = MONDAY + timedelta(days=5 if assigned == "SATURDAY" else 6)
    report, result = evaluate([preference(category="WEEKEND_DAY", days=[preferred])], config=config,
        dates=calendar(config, first=day))
    assert result.status is SolverStatus.OPTIMAL
    assert report.satisfied_count == int(satisfied) and report.violation_count == int(not satisfied)


def test_weekend_preferences_do_not_penalize_weekday_teaching():
    report, _ = evaluate([preference(category="WEEKEND_DAY", days=["SATURDAY"])])
    assert report.evaluated_count == report.violation_count == 0


@pytest.mark.parametrize("label,start,end,satisfied", [
    ("MORNING", 0, 30, True), ("MORNING", 60, 90, False),
    ("AFTERNOON", 60, 90, True), ("AFTERNOON", 0, 30, False),
    ("MORNING", 45, 75, False)])
def test_time_of_day_uses_only_configured_boundaries(label, start, end, satisfied):
    config = windows(120)
    config.days[0].windows["WEEKDAY"] = [TimeWindow(start_minute=start, end_minute=end)]
    scoring = policy(time_of_day_windows={"MORNING": {"start_minute": 0, "end_minute": 60},
        "AFTERNOON": {"start_minute": 60, "end_minute": 120}}, time_window_policy_reference="synthetic:clock-bands")
    report, _ = evaluate([preference(category="TIME_OF_DAY", time_of_day=label)], config=config, scoring=scoring)
    assert report.satisfied_count == int(satisfied) and report.violation_count == int(not satisfied)


@pytest.mark.parametrize("label", ["MORNING", "AFTERNOON"])
def test_absent_time_of_day_boundaries_are_unresolved(label):
    report, _ = evaluate([preference(category="TIME_OF_DAY", time_of_day=label)])
    assert report.unresolved_count == 1 and report.violation_count == 0
    assert not report.to_dict()["preference_evaluation_complete"]
    assert {row.code for row in report.diagnostics} == {"missing_time_of_day_boundaries"}


@pytest.mark.parametrize("direction,start,end,violated", [
    ("PREFER", 0, 30, False), ("PREFER", 15, 45, True),
    ("AVOID", 0, 30, True), ("AVOID", 15, 45, True), ("AVOID", 30, 60, False)])
def test_preferred_window_requires_containment_avoided_window_uses_overlap(direction, start, end, violated):
    config = windows(60)
    config.days[0].windows["WEEKDAY"] = [TimeWindow(start_minute=start, end_minute=end)]
    report, _ = evaluate([preference(category="TEACHING_WINDOW", direction=direction,
        time_window={"start_minute": 0, "end_minute": 30})], config=config)
    assert report.violation_count == int(violated)


def test_clock_preferences_with_other_day_selector_are_not_applicable():
    report, _ = evaluate([preference(category="TIME_OF_DAY", time_of_day="MORNING", days=["SUNDAY"])])
    assert report.evaluated_count == report.unresolved_count == 0


def test_unspecified_preferences_do_not_create_penalties():
    report, _ = evaluate()
    assert report.evaluated_count == report.violation_count == 0
    assert report.total_weighted_penalty is None and report.staff[0].supplied_preference_count == 0
    assert report.staff[0].scheduled_teaching_minutes == 30


@pytest.mark.parametrize("change", ["staff", "id", "scope", "category", "day", "weekend_day", "clock", "ambiguous_selectors", "weight_type", "missing_weight_reference", "missing_weight", "source_location"])
def test_contract_and_reference_errors(change):
    payload = preference().model_dump(round_trip=True)
    if change == "staff":
        payload["staff_id"] = "UNKNOWN"
    elif change == "id":
        payload["staff_id"] = "   "
    elif change == "scope":
        payload["scope"] = {}
    elif change == "category":
        payload["category"] = "HARD_AVAILABILITY"
    elif change == "day":
        payload["days"] = ["FUNDAY"]
    elif change == "weekend_day":
        payload["category"] = "WEEKEND_DAY"
    elif change == "clock":
        payload.update(category="TEACHING_WINDOW", time_window={"start_minute": 60, "end_minute": 30})
    elif change == "ambiguous_selectors":
        payload.update(category="TIME_OF_DAY", time_of_day="MORNING", time_window={"start_minute": 0, "end_minute": 60})
    elif change == "weight_type":
        payload.update(penalty_weight=True, weight_reference="synthetic:weight")
    elif change == "missing_weight_reference":
        payload["penalty_weight"] = 3
    elif change == "missing_weight":
        payload["weight_reference"] = "synthetic:weight"
    else:
        payload["provenance"]["row"] = 4
    with pytest.raises((ValidationError, PreferenceInputError)):
        evaluate([LecturerPreference.model_validate(payload)])


@pytest.mark.parametrize("scope", [
    {"week_reference": "OTHER"}, {"effective_from": "2026-10-20"},
    {"effective_from": "2026-10-01", "effective_until": "2026-10-18"},
    {"week_reference": "SYNTHETIC-WEEK", "effective_from": "2026-10-20"}])
def test_outside_effective_dates_or_academic_week_does_not_apply(scope):
    report, _ = evaluate([preference(scope=scope)])
    assert report.evaluated_count == 0 and report.inactive_preference_count == 1


@pytest.mark.parametrize("scope", [
    {"effective_from": "2026-10-19", "effective_until": "2026-10-19"},
    {"effective_from": "2026-10-19"}, {"week_reference": "SYNTHETIC-WEEK"}])
def test_effective_date_bounds_are_inclusive(scope):
    report, _ = evaluate([preference(scope=scope)])
    assert report.satisfied_count == 1


def test_rejected_preferences_are_not_applied():
    report, _ = evaluate([preference(confirmation_status="REJECTED")])
    assert report.evaluated_count == 0 and report.inactive_preference_count == 1


def test_stream_scope_preserves_weekday_and_weekend_identity():
    report, _ = evaluate([preference(stream="WEEKEND")])
    assert report.evaluated_count == 0


def test_conflicting_soft_preferences_report_review_and_keep_timetable_feasible():
    report, result = evaluate([preference(), preference(identity="AVOID", direction="AVOID")])
    assert result.status is SolverStatus.OPTIMAL and report.violation_count == report.satisfied_count == 1
    assert "conflicting_preferences" in {row.code for row in report.diagnostics}


def test_duplicate_preference_ids_rejected_without_merging_source_records():
    with pytest.raises(ValidationError, match="unique"):
        LecturerPreferenceInput(preferences=[preference(), preference()])


@pytest.mark.parametrize("confirmed,weight", [(True, 3), (True, 0), (False, 3), (False, None)])
def test_only_explicit_confirmed_weights_generate_authoritative_weighted_total(confirmed, weight):
    updates = {"confirmation_status": "CONFIRMED" if confirmed else None}
    if weight is not None:
        updates.update(penalty_weight=weight, weight_reference="synthetic:confirmed-weight")
    report, _ = evaluate([preference(direction="AVOID", **updates)], scoring=policy(weight_mode="CONFIRMED_WEIGHTS"))
    assert report.violation_count == 1
    assert report.total_weighted_penalty == (weight if confirmed else None)
    assert report.weighted_scoring_complete is confirmed
    assert report.known_weighted_penalty == (weight if confirmed else 0)


def test_missing_weight_keeps_aggregate_incomplete_without_silent_default():
    report, _ = evaluate([preference(direction="AVOID", penalty_weight=5, weight_reference="synthetic:weight", confirmation_status="CONFIRMED"),
        preference(identity="OTHER", direction="AVOID")], scoring=policy(weight_mode="CONFIRMED_WEIGHTS"))
    assert report.known_weighted_penalty == 5 and report.total_weighted_penalty is None
    assert not report.weighted_scoring_complete


def test_synthetic_default_is_explicitly_labeled_and_rejected_for_real_data():
    scoring = policy(weight_mode="SYNTHETIC_DEFAULTS", synthetic_default_weight=7)
    report, _ = evaluate([preference(direction="AVOID")], scoring=scoring)
    assert report.total_weighted_penalty == 7 and report.to_dict()["synthetic_weights_only"]
    config = windows()
    with pytest.raises(PreferenceInputError, match="Synthetic default"):
        evaluate([preference()], config=config, dates=calendar(config, synthetic_data=False), scoring=scoring)


@pytest.mark.parametrize("payload", [
    {"weight_mode": "SYNTHETIC_DEFAULTS"}, {"synthetic_default_weight": 2},
    {"time_of_day_windows": {"MORNING": {"start_minute": 0, "end_minute": 60}}},
    {"time_window_policy_reference": "synthetic:missing-boundaries"},
    {"weight_mode": "SYNTHETIC_DEFAULTS", "synthetic_default_weight": -1}])
def test_implicit_or_invalid_scoring_assumptions_rejected(payload):
    with pytest.raises(ValidationError):
        policy(**payload)


@pytest.mark.parametrize("corruption", ["missing_day", "wrong_named_day", "date_outside_week", "duplicate_dates"])
def test_ambiguous_calendar_mapping_is_rejected(corruption):
    config = windows(days=("MONDAY", "TUESDAY"))
    values = calendar(config).model_dump(round_trip=True)
    if corruption == "missing_day":
        del values["day_dates"]["MONDAY"]
    elif corruption == "wrong_named_day":
        values["day_dates"] = {"MONDAY": MONDAY + timedelta(days=1), "TUESDAY": MONDAY}
    elif corruption == "date_outside_week":
        values["day_dates"]["MONDAY"] = MONDAY - timedelta(days=7)
    else:
        values["day_dates"]["TUESDAY"] = MONDAY
    with pytest.raises((ValidationError, PreferenceInputError)):
        evaluate([preference()], config=config, dates=PreferenceCalendar.model_validate(values))


@pytest.mark.parametrize("mode", ["combined", "separate", "mixed"])
def test_selected_combined_separate_and_mixed_deliveries_count_once(mode):
    combinations = prepare_group_combinations(combination_inputs())
    rooms = venues(20) if mode == "separate" else venues(large_minutes=30) if mode == "mixed" else venues()
    report, result = evaluate([preference(direction="AVOID")], prepared=combinations.prepared,
        config=windows(120), combination_input=combinations, venue_input=rooms,
        scoring=policy(weight_mode="SYNTHETIC_DEFAULTS", synthetic_default_weight=2))
    count = {"combined": 2, "separate": 4, "mixed": 3}[mode]
    assert report.assigned_delivery_count == report.violation_count == count
    assert report.total_weighted_penalty == count * 2
    assert report.staff[0].assigned_delivery_count == count
    assert report.staff[0].scheduled_teaching_minutes == count * 30
    assert result.objective_value == count * 121 + count * 30
    assert len(report.evaluations) == count  # Unselected alternatives are absent.


@pytest.mark.parametrize("kind", ["admin", "research", "availability", "teaching_cap"])
def test_hard_constraints_take_priority_over_soft_preference(kind):
    data = scenario(minutes=60)
    if kind == "admin":
        data[3].administrative_periods = [admin(data[2].workload_input,
            window={"start_minute": 0, "end_minute": 30}, planned_duration_minutes=30)]
    elif kind == "research":
        data[3].research_policies = [research_policy()]
        data[3].research_reservations = [research(on_date=FIRST, full_day=False,
            windows=[{"start_minute": 0, "end_minute": 30}])]
    elif kind == "availability":
        data[2].availability[0].available_windows["DAY"] = [TimeWindow(start_minute=30, end_minute=60)]
    else:
        data[2].availability[0].weekly_teaching_max_minutes = 0
    result, context = protected_run(data)
    if kind == "teaching_cap":
        assert result.status is SolverStatus.INFEASIBLE
        with pytest.raises(PreferenceInputError, match="complete feasible"):
            evaluate([preference()], prepared=data[0], config=data[1], result=result,
                dates=calendar(data[1]), **context)
        return
    report, _ = evaluate([preference(category="TEACHING_WINDOW", time_window={"start_minute": 0, "end_minute": 30})],
        prepared=data[0], config=data[1], result=result, dates=calendar(data[1]), **context)
    assert result.schedule[0].start_minute == 30 and report.violation_count == 1
    assert result.status is SolverStatus.OPTIMAL
    broken = replace(result, schedule=(replace(result.schedule[0], start_minute=0, end_minute=30),))
    with pytest.raises(TimetableValidationError):
        evaluate([preference()], prepared=data[0], config=data[1], result=broken,
            dates=calendar(data[1]), **context)


def test_original_work_period_and_staff_context_is_required():
    data = scenario()
    result, context = protected_run(data)
    with pytest.raises(ValueError):
        evaluate([preference()], prepared=data[0], config=data[1], result=result,
            dates=calendar(data[1]), staff_input=data[2])
    with pytest.raises(PreferenceInputError, match="same dates"):
        evaluate([preference()], prepared=data[0], config=data[1], result=result,
            dates=calendar(data[1], week_reference="OTHER"), **context)


def test_staff_summaries_and_safe_aggregate_output():
    report, _ = evaluate([preference(), preference(identity="UNSATISFIED", direction="AVOID")])
    row = report.staff[0]
    assert row.evaluated_count == 2 and row.satisfied_count == row.violation_count == 1
    assert row.teaching_day_count == 1 and row.daily_teaching_minutes == (("DAY", 30),)
    assert row.longest_consecutive_teaching_minutes == 30
    assert row.required_teaching_minutes is row.teaching_target_ratio is None
    output = report.to_dict()
    assert "staff" not in output and "evaluations" not in output
    assert not output["university_ready"] and not output["cp_sat_preference_objective_enabled"]
    assert report.to_dict(include_staff_details=True)["staff"][0]["staff_id"] == "STAFF"


def test_consecutive_teaching_resets_at_gaps_and_never_uses_research_or_admin():
    prepared = demand({"OFFER": [("STAFF", "LIC")]}, requirements=[("OFFER", "GROUP", 3)])
    config = windows(120)
    result = solve_timetable(prepared, config)
    actual = tuple(replace(row, start_minute=start, end_minute=start + 30)
                   for row, start in zip(result.schedule, (0, 30, 90), strict=True))
    result = replace(result, schedule=actual, validation=validate_timetable(prepared, config, actual))
    report, _ = evaluate(prepared=prepared, config=config, result=result)
    assert report.staff[0].longest_consecutive_teaching_minutes == 60
    assert report.staff[0].scheduled_teaching_minutes == 90


@pytest.mark.parametrize("band,target", [("PROFESSOR", 360), ("LECTURER", 960), ("ASSISTANT_PROFESSOR", 360)])
def test_fairness_descriptors_use_explicit_role_specific_teaching_target(band, target):
    data = scenario(band=band)
    result, context = protected_run(data)
    report, _ = evaluate(prepared=data[0], config=data[1], result=result, dates=calendar(data[1]), **context)
    assert report.staff[0].required_teaching_minutes == target
    assert report.staff[0].teaching_target_ratio == 30 / target
    if band == "ASSISTANT_PROFESSOR":
        assert result.workload_report.staff[0].policy.compulsory_campus_days == 6
        assert not result.workload_report.staff[0].policy.campus_day_exempt


def test_preparation_clones_inputs_and_rejects_changed_derived_diagnostics():
    prepared = demand({"OFFER": [("STAFF", "LIC")]})
    source = LecturerPreferenceInput(preferences=[preference()])
    preferences = prepare_lecturer_preferences(prepared.input_snapshot, source)
    source.preferences[0].staff_id = "CHANGED"
    assert preferences.input_snapshot.preferences[0].staff_id == "STAFF"
    config = windows()
    result = solve_timetable(prepared, config)
    bad = replace(preferences, diagnostics=("FORGED",))
    with pytest.raises(PreferenceInputError, match="regenerated"):
        score_lecturer_preferences(prepared, config, result, bad, calendar(config), policy())


def test_example_script_is_synthetic_and_executable(capsys):
    runpy.run_path(str(Path(__file__).resolve().parents[1] / "examples" / "lecturer-preferences-demo.py"), run_name="__main__")
    output = capsys.readouterr().out
    assert "synthetic" in output and "total_weighted_penalty" in output


def test_explicit_preferred_alternatives_are_evaluated_once_per_delivery():
    rows = [preference(identity=label, category="TIME_OF_DAY", time_of_day=label, alternative_group_id="DAY-PARTS")
            for label in ("MORNING", "AFTERNOON")]
    scoring = policy(weight_mode="SYNTHETIC_DEFAULTS", synthetic_default_weight=2,
        time_of_day_windows={"MORNING": {"start_minute": 0, "end_minute": 60},
                            "AFTERNOON": {"start_minute": 60, "end_minute": 120}},
        time_window_policy_reference="synthetic:bands")
    report, _ = evaluate(rows, scoring=scoring)
    assert report.satisfied_count == report.evaluated_count == 1 and report.violation_count == 0
    assert report.total_weighted_penalty == 0
    assert report.evaluations[0].preference_ids == ("MORNING", "AFTERNOON")
    assert report.staff[0].supplied_preference_count == 2


@pytest.mark.parametrize("cause", ["direction", "days", "weight", "stream", "scope"])
def test_incompatible_alternative_group_contracts_are_rejected(cause):
    first = preference(alternative_group_id="CHOICES")
    changes = {"direction": {"direction": "AVOID"}, "days": {"days": ["TUESDAY"]},
        "weight": {"penalty_weight": 2, "weight_reference": "synthetic:weight"},
        "stream": {"stream": "WEEKEND"}, "scope": {"scope": {"week_reference": "OTHER"}}}[cause]
    with pytest.raises(ValidationError):
        second = preference(identity="SECOND", alternative_group_id="CHOICES", **changes)
        LecturerPreferenceInput(preferences=[first, second])


def test_unresolved_time_or_weights_never_report_a_complete_zero_penalty():
    report, _ = evaluate([preference(category="TIME_OF_DAY", time_of_day="MORNING")],
        scoring=policy(weight_mode="SYNTHETIC_DEFAULTS", synthetic_default_weight=1))
    assert report.known_weighted_penalty == 0 and report.total_weighted_penalty is None
    assert not report.weighted_scoring_complete and report.unresolved_count == 1


def test_priority_label_does_not_create_an_implicit_numeric_weight():
    report, _ = evaluate([preference(direction="AVOID", strength="HIGH", confirmation_status="CONFIRMED")],
        scoring=policy(weight_mode="CONFIRMED_WEIGHTS"))
    assert report.violation_count == 1 and report.total_weighted_penalty is None
    assert report.evaluations[0].penalty_weight is None


def test_fairness_metrics_distinguish_professor_and_instructor_targets():
    prepared = demand({"OFFER-P": [("PROF", "LIC")], "OFFER-I": [("INSTR", "TEAM_MEMBER")]},
        staff_types={"INSTR": "INSTRUCTOR"})
    data = scenario(prepared=prepared)
    result, context = protected_run(data)
    report, _ = evaluate(prepared=data[0], config=data[1], result=result, dates=calendar(data[1]), **context)
    staff = {row.staff_id: row for row in report.staff}
    assert staff["PROF"].scheduled_teaching_minutes == staff["INSTR"].scheduled_teaching_minutes == 30
    assert staff["PROF"].teaching_target_ratio == 30 / 360
    assert staff["INSTR"].teaching_target_ratio == 30 / 1200
    assert staff["INSTR"].supplied_preference_count == 0  # No compulsory instructor preference records.
