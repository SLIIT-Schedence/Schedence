"""Native fixed lunch enforcement with independent assessment and original modes."""

from dataclasses import replace
from datetime import date, timedelta
import json
from pathlib import Path
import runpy
from types import SimpleNamespace

from ortools.sat.python import cp_model
from pydantic import ValidationError
import pytest

from schedence_teaching.lunch import assess_student_lunch, confirmed_student_lunch_policy, validate_student_lunch_report
from schedence_teaching.preferences import PreferenceCalendar
from schedence_teaching.preparation import prepare_group_combinations
from schedence_teaching.schemas import Stream
from schedence_teaching.solver import (
    SolverConfig, SolverInputError, SolverStatus, StudentLunchEnforcementConfig,
    TimetableValidationError, solve_timetable, validate_student_lunch_result, validate_timetable,
)
from schedence_teaching.solver.evaluation import validate_evaluation_result

from test_student_lunch import fixture, FIRST, LAST, WEEK


def options(**updates):
    return StudentLunchEnforcementConfig.model_validate(dict(applicability_confirmation="CONFIRMED",
        applicability_confirmation_reference="synthetic:confirmed-date-stream-applicability", **updates))


def source(*, year=2, duration=30, first=720, last=900, kind="LECTURE", granularity=1, stream="WEEKDAY", day="DAY", on_date=FIRST):
    prepared, _, _, calendar = fixture([("G", first, first+duration, kind, day)], years={"G": year},
        streams={"G": stream}, dates={day: on_date})
    config = SolverConfig(days=[dict(day=day, windows={stream: [dict(start_minute=first, end_minute=last)]})],
        granularity_minutes=granularity, max_time_seconds=2, random_seed=7)
    return prepared, config, calendar


def context(calendar, **updates):
    policy = confirmed_student_lunch_policy(policy_reference="synthetic:confirmed-fixed-lunch", week_reference=calendar.week_reference,
        provenance=dict(source="synthetic", reference="test-only"), applicable_dates=set(calendar.day_dates.values()),
        applicable_streams={"WEEKDAY", "WEEKEND"}, applicability_reference="synthetic:explicit-scope")
    return dict(student_lunch_policy=policy, student_lunch_calendar=calendar, student_lunch_config=options(), **updates)


def run(inputs=None, **updates):
    prepared, config, calendar = inputs or source()
    original = {**context(calendar), **updates}
    before = repr((prepared, config, original))
    result = solve_timetable(prepared, config, **original)
    assert repr((prepared, config, original)) == before
    summary = validate_student_lunch_result(prepared, config, result, **original)
    if result.status in {SolverStatus.OPTIMAL, SolverStatus.FEASIBLE}:
        assert summary == result.validation
        report = result.student_lunch_report
        assert report is not None and report.assessment.violation_count == 0
        assert report.assessment.hard_lunch_constraints_enforced_by_solver
        assessment = assess_student_lunch(prepared, config, result, calendar, original["student_lunch_policy"], **original)
        assert assessment == report.assessment
        assert validate_student_lunch_report(assessment, prepared, config, result, calendar, original["student_lunch_policy"], **original) == assessment
    else:
        assert summary is None and result.schedule == result.administrative_schedule == ()
        assert result.validation is result.workload_report is result.work_period_report is None
        assert result.preference_report is result.fairness_report is result.consecutive_report is result.student_lunch_report is None
        assert result.objective_value is result.objective_status is None
        with pytest.raises(SolverInputError, match="complete feasible"):
            validate_evaluation_result(prepared, config, result, **original)
    return prepared, config, result, original


@pytest.mark.parametrize("year", [1, 2, 3, 4])
@pytest.mark.parametrize("placement", ["end_at_lunch", "start_after_lunch", "inside", "one_minute_start", "one_minute_end", "spanning"])
def test_exact_fixed_periods_and_positive_overlap_boundaries(year, placement):
    start = 750 if year <= 2 else 810
    end = start+60
    periods = {"end_at_lunch": (start-30, start), "start_after_lunch": (end, end+30),
        "inside": (start+15, end-15), "one_minute_start": (start-29, start+1),
        "one_minute_end": (end-1, end+29), "spanning": (start-15, end+15)}
    first, last = periods[placement]
    _, _, result, _ = run(source(year=year, duration=last-first, first=first, last=last))
    assert result.status is (SolverStatus.OPTIMAL if placement in {"end_at_lunch", "start_after_lunch"} else SolverStatus.INFEASIBLE)
    if result.schedule:
        assert result.student_lunch_report.enforced_academic_years == (year,)


@pytest.mark.parametrize("kind", ["LECTURE", "LAB", "TUTORIAL", "WORKSHOP"])
def test_all_session_types_are_hard_protected(kind):
    assert run(source(first=750, last=780, kind=kind))[2].status is SolverStatus.INFEASIBLE


def test_the_previously_conflicting_year_two_lab_gets_a_native_alternative():
    prepared, config, calendar = source(kind="LAB", duration=60, first=720, last=900, granularity=15)
    baseline = solve_timetable(prepared, config)
    assert (baseline.schedule[0].start_minute, baseline.schedule[0].end_minute) == (720, 780)
    _, _, result, _ = run((prepared, config, calendar))
    assert (result.schedule[0].start_minute, result.schedule[0].end_minute) == (810, 870)
    assert result.validation.assigned_occurrences == 1 and result.administrative_schedule == ()


@pytest.mark.parametrize("year", [3, 4])
def test_later_years_can_teach_during_earlier_year_lunch_without_blocking_staff(year):
    _, _, result, _ = run(source(year=year, first=750, last=810, duration=60))
    assert result.status is SolverStatus.OPTIMAL and result.schedule[0].start_minute == 750
    assert result.student_lunch_report.to_dict()["lecturer_lunch_enforced"] is False


@pytest.mark.parametrize("granularity,first,last,duration", [(15, 720, 900, 60), (20, 720, 900, 40), (60, 720, 900, 60)])
def test_grid_alignment_never_rounds_or_changes_the_fixed_lunch(granularity, first, last, duration):
    _, _, result, _ = run(source(first=first, last=last, duration=duration, granularity=granularity))
    row = result.schedule[0]
    assert row.end_minute <= 750 or row.start_minute >= 810
    period = next(item for item in result.student_lunch_report.assessment.groups if item.academic_year == 2)
    assert (period.lunch_start_minute, period.lunch_end_minute) == (750, 810)


@pytest.mark.parametrize("applicable", [True, False])
def test_weekend_applicability_is_explicit_and_not_inferred(applicable):
    inputs = source(first=750, last=780, stream="WEEKEND", day="SATURDAY", on_date=date(2026, 10, 24))
    original = context(inputs[2])
    original["student_lunch_policy"].applicable_streams = {Stream.WEEKEND} if applicable else {Stream.WEEKDAY}
    _, _, result, _ = run(inputs, **original)
    assert result.status is (SolverStatus.INFEASIBLE if applicable else SolverStatus.OPTIMAL)
    if not applicable:
        assert result.student_lunch_report.assessment.compliant is None
        assert not result.student_lunch_report.enforced_academic_years


def test_explicit_date_exclusion_is_not_a_compliance_claim():
    inputs = source(first=750, last=780)
    original = context(inputs[2])
    original["student_lunch_policy"].applicable_dates = {FIRST+timedelta(days=1)}
    result = run(inputs, **original)[2]
    assert result.status is SolverStatus.OPTIMAL and result.student_lunch_report.assessment.compliant is None


@pytest.mark.parametrize("missing", ["policy", "calendar", "config", "dates", "streams", "confirmation", "confirmation_reference", "week", "calendar_dates", "period", "study_year", "group_reference"])
def test_ambiguous_missing_or_changed_policies_are_rejected_before_solving(monkeypatch, missing):
    inputs = source()
    original = context(inputs[2])
    if missing in {"policy", "calendar", "config"}:
        original[f"student_lunch_{missing}"] = None
    elif missing in {"dates", "streams"}:
        setattr(original["student_lunch_policy"], f"applicable_{missing}", None)
    elif missing.startswith("confirmation"):
        field = "applicability_confirmation" if missing == "confirmation" else "applicability_confirmation_reference"
        original["student_lunch_config"] = original["student_lunch_config"].model_copy(update={field: None})
    elif missing == "week":
        original["student_lunch_policy"].week_reference = "UNKNOWN-WEEK"
    elif missing == "calendar_dates":
        original["student_lunch_calendar"].day_dates = {}
    elif missing == "period":
        original["student_lunch_policy"].year_periods[0].window.start_minute = 720
        original["student_lunch_policy"].year_periods[0].window.end_minute = 780
    elif missing == "study_year":
        inputs[0].input_snapshot.student_groups[0].academic_year = 2026
    else:
        inputs[0].input_snapshot.session_requirements[0].group_id = "UNKNOWN"
    def unexpected(*args, **kwargs):
        raise AssertionError("Invalid source policy reached the native solver")
    monkeypatch.setattr(cp_model.CpSolver, "solve", unexpected)
    with pytest.raises(ValueError):
        solve_timetable(inputs[0], inputs[1], **original)


@pytest.mark.parametrize("updates", [dict(applicability_confirmation="UNCONFIRMED"), dict(applicability_confirmation_reference=" "),
    dict(max_rule_windows=0), dict(max_model_variables=True), dict(max_model_constraints=0)])
def test_enforcement_config_is_explicit_and_strict(updates):
    payload = dict(applicability_confirmation="CONFIRMED", applicability_confirmation_reference="synthetic:confirmation")
    with pytest.raises(ValidationError):
        StudentLunchEnforcementConfig.model_validate({**payload, **updates})


@pytest.mark.parametrize("field", ["max_rule_windows", "max_model_variables", "max_model_constraints"])
def test_technical_limits_reject_instead_of_dropping_lunch_rules(field):
    from test_combination_solver import inputs as combinations_input
    from schedence_teaching.preparation import prepare_group_combinations
    combinations = prepare_group_combinations(combinations_input())
    _, config, calendar = source()
    with pytest.raises(SolverInputError, match="limit"):
        run((combinations.prepared, config, calendar), combination_input=combinations,
            student_lunch_config=options(**{field: 1}))


def test_disabled_mode_preserves_assignments_and_serialized_output():
    prepared, config, _ = source()
    before = solve_timetable(prepared, config)
    after = solve_timetable(prepared, config, student_lunch_policy=None, student_lunch_calendar=None, student_lunch_config=None)
    assert before.schedule == after.schedule and not after.student_lunch_enabled
    first, second = before.to_dict(), after.to_dict()
    first.pop("runtime_seconds"); second.pop("runtime_seconds")
    assert first == second and "student_lunch" not in second and "student_lunch_enabled" not in second


def test_parent_child_and_nested_attendance_remain_covered():
    prepared, config, _, calendar = fixture([("P", 720, 750), ("CHILD", 810, 840, "LAB")],
        years={"P": 2, "CHILD": 2, "LEAF": 2, "SIBLING": 2}, parents={"CHILD": "P", "LEAF": "CHILD", "SIBLING": "P"})
    _, _, result, _ = run((prepared, config, calendar))
    rows = {row.group_id: row for row in result.student_lunch_report.assessment.groups}
    assert len(rows["LEAF"].inherited_delivery_ids) == 2 and not rows["P"].inherited_delivery_ids
    assert len(rows["SIBLING"].inherited_delivery_ids) == 1 and not rows["SIBLING"].direct_delivery_ids
    assert result.validation.assigned_occurrences == 2


def test_sibling_labs_can_still_share_the_same_non_lunch_time():
    prepared, _, _, calendar = fixture([("A", 720, 750, "LAB"), ("B", 720, 750, "LAB")],
        years={"P": 2, "A": 2, "B": 2}, parents={"A": "P", "B": "P"})
    config = SolverConfig(days=[dict(day="DAY", windows={"WEEKDAY": [dict(start_minute=720, end_minute=750)]})],
        granularity_minutes=15, max_time_seconds=2)
    result = run((prepared, config, calendar))[2]
    assert {row.start_minute for row in result.schedule} == {720} and len(result.schedule) == 2


@pytest.mark.parametrize("layout,deliveries", [("combined", 2), ("mixed", 3), ("separate", 4)])
def test_selected_combined_mixed_separate_deliveries_preserve_original_coverage(layout, deliveries):
    from test_combination_solver import inputs, venues
    combinations = prepare_group_combinations(inputs())
    rooms = venues() if layout == "combined" else venues(large_minutes=30 if layout == "mixed" else 15)
    for row in rooms.calendars:
        row.available_windows["DAY"][0].start_minute += 720
        row.available_windows["DAY"][0].end_minute += 720
        if row.venue_id == "SMALL":
            row.available_windows["DAY"][0].end_minute = 930
    if layout == "separate":
        rooms.calendars[0].available_windows["DAY"] = []
    _, config, calendar = source(first=720, last=930, granularity=15)
    result = run((combinations.prepared, config, calendar), combination_input=combinations, venue_input=rooms)[2]
    assert result.status is SolverStatus.OPTIMAL
    assert len(result.schedule) == deliveries and result.validation.assigned_occurrences == 4
    assert result.student_lunch_report.assessment.covered_original_occurrence_count == 4
    assert all(len(row.direct_delivery_ids) == 2 for row in result.student_lunch_report.assessment.groups)


def test_different_study_year_groups_use_their_own_windows():
    prepared, _, _, calendar = fixture([("Y1", 750, 780), ("Y3", 750, 780)], years={"Y1": 1, "Y3": 3})
    config = SolverConfig(days=[dict(day="DAY", windows={"WEEKDAY": [dict(start_minute=750, end_minute=840)]})],
        granularity_minutes=15, max_time_seconds=2)
    result = run((prepared, config, calendar))[2]
    groups = {row.session.group_id: row for row in result.schedule}
    assert groups["Y1"].start_minute == 810 and groups["Y3"].end_minute <= 810


def test_delivery_domain_respects_all_attendees_even_when_year_windows_differ():
    # Defensive primitive test: current combination eligibility still requires a
    # common study year. This does not invent a cross-year eligible offering.
    from schedence_teaching.solver.lunch_inputs import checked_student_lunch_inputs
    from schedence_teaching.solver.lunch_constraints import add_student_lunch_constraint
    prepared, config, _, calendar = fixture([], years={"Y1": 1, "Y3": 3})
    original = context(calendar)
    checked = checked_student_lunch_inputs(prepared, config, original["student_lunch_policy"], calendar,
        original["student_lunch_config"], [SimpleNamespace(delivery_id="SYNTHETIC-DEFENSIVE", group_ids=("Y1", "Y3"))])
    rules = dict(checked.delivery_rules)["SYNTHETIC-DEFENSIVE"]
    assert rules == ((0, 750, 810), (0, 810, 870))
    for minute, expected in [(720, cp_model.OPTIMAL), (750, cp_model.INFEASIBLE), (810, cp_model.INFEASIBLE), (870, cp_model.OPTIMAL)]:
        model = cp_model.CpModel()
        start = model.new_int_var(minute, minute, "start")
        add_student_lunch_constraint(model, start, [[minute, minute]], 30, None, rules, config)
        assert cp_model.CpSolver().solve(model) == expected


@pytest.mark.parametrize("resource", ["staff", "venue", "admin", "research"])
def test_original_hard_protection_cannot_be_relaxed_to_make_lunch_fit(resource):
    from test_work_period_solver import scenario
    from test_work_periods import admin, research, research_policy
    from test_venue_solver import physical
    from schedence_teaching.workload import prepare_work_periods
    prepared, _, staff, periods, period_config = scenario(duration=60)
    _, config, calendar = source(duration=60, first=720, last=870, granularity=15)
    calendar = calendar.model_copy(update={"week_reference": staff.workload_input.week_reference})
    original = dict(staff_input=staff)
    if resource == "staff":
        from schedence_teaching.solver import TimeWindow
        staff.availability[0].available_windows = {"DAY": [TimeWindow(start_minute=720, end_minute=780)]}
    elif resource == "venue":
        rooms = physical()
        rooms.calendars[0].available_windows["DAY"][0].start_minute = 720
        rooms.calendars[0].available_windows["DAY"][0].end_minute = 780
        original["venue_input"] = rooms
    else:
        if resource == "admin":
            periods.administrative_periods = [admin(staff.workload_input, window=dict(start_minute=810, end_minute=870), planned_duration_minutes=60)]
        else:
            periods.research_policies = [research_policy()]
            periods.research_reservations = [research(on_date=FIRST, full_day=False, windows=[dict(start_minute=810, end_minute=870)])]
        original.update(work_periods=prepare_work_periods(prepared.input_snapshot, staff.workload_input, periods), work_period_config=period_config)
    result = run((prepared, config, calendar), **original)[2]
    assert result.status is SolverStatus.INFEASIBLE


@pytest.mark.parametrize("status", [SolverStatus.UNKNOWN, SolverStatus.MODEL_INVALID])
def test_unsuccessful_native_statuses_emit_no_partial_lunch_compliance(monkeypatch, status):
    monkeypatch.setattr(cp_model.CpSolver, "solve", lambda *args, **kwargs: cp_model.UNKNOWN if status is SolverStatus.UNKNOWN else cp_model.MODEL_INVALID)
    assert run()[2].status is status


@pytest.mark.parametrize("kind", ["FEASIBLE", "LATER_UNKNOWN"])
def test_lunch_keeps_honest_lexicographic_incumbents(monkeypatch, kind):
    from test_consecutive import scoring
    prepared, config, calendar = source()
    native, calls = cp_model.CpSolver.solve, []
    def controlled(solver, model, *args, **kwargs):
        calls.append(1)
        if len(calls) > 1:
            return cp_model.UNKNOWN
        status = native(solver, model, *args, **kwargs)
        assert status == cp_model.OPTIMAL
        return cp_model.FEASIBLE if kind == "FEASIBLE" else status
    monkeypatch.setattr(cp_model.CpSolver, "solve", controlled)
    from schedence_teaching.solver import ConsecutiveOptimizationConfig
    settings = scoring()
    settings.week_reference = calendar.week_reference
    result = run((prepared, config, calendar), consecutive_policy=settings, consecutive_calendar=calendar,
        consecutive_config=ConsecutiveOptimizationConfig(priority="EXISTING_OBJECTIVES_THEN_CONSECUTIVE", priority_policy_reference="synthetic:order"))[2]
    assert result.status is SolverStatus.FEASIBLE and result.student_lunch_report.assessment.compliant
    assert not result.consecutive_report.all_stages_proven_optimal


@pytest.mark.parametrize("change", ["booking", "summary", "policy", "year", "groups", "confirmation", "mode", "coverage", "fake_admin"])
def test_independent_validation_rejects_corrupt_hard_lunch_outputs(change):
    prepared, config, result, original = run()
    report = result.student_lunch_report
    if change == "booking":
        first = result.schedule[0]
        schedule = (replace(first, start_minute=750, end_minute=780),)
        result = replace(result, schedule=schedule, validation=validate_timetable(prepared, config, schedule))
    elif change == "summary":
        report = replace(report, assessment=replace(report.assessment, groups=()))
    elif change == "policy":
        report = replace(report, assessment=replace(report.assessment, policy_snapshot_json="{}"))
    elif change == "year":
        report = replace(report, enforced_academic_years=(2026,))
    elif change == "groups":
        report = replace(report, evaluated_group_ids=("UNKNOWN",))
    elif change == "confirmation":
        report = replace(report, enforcement_config_json="{}")
    elif change == "mode":
        result = replace(result, student_lunch_enabled=False)
    elif change == "coverage":
        result = replace(result, schedule=())
    else:
        result = replace(result, administrative_schedule=(SimpleNamespace(kind="LUNCH"),))
    if change in {"summary", "policy", "year", "groups", "confirmation"}:
        result = replace(result, student_lunch_report=report)
    with pytest.raises((SolverInputError, TimetableValidationError)):
        validate_student_lunch_result(prepared, config, result, **original)


def test_missing_original_lunch_context_is_rejected_by_the_common_gateway():
    prepared, config, result, original = run()
    with pytest.raises(SolverInputError):
        validate_evaluation_result(prepared, config, result)
    assert validate_evaluation_result(prepared, config, result, **original) == result.validation


def test_independent_lunch_validation_never_builds_a_model(monkeypatch):
    prepared, config, result, original = run()
    monkeypatch.setattr(cp_model, "CpModel", lambda *args, **kwargs: pytest.fail("Validator built a CP model"))
    assert validate_student_lunch_result(prepared, config, result, **original) == result.validation


def test_a_missing_native_lunch_constraint_is_detected_independently(monkeypatch):
    import schedence_teaching.solver.lunch_constraints as constraints
    monkeypatch.setattr(constraints, "add_student_lunch_constraint", lambda *args, **kwargs: None)
    with pytest.raises(TimetableValidationError, match="student_lunch_violation"):
        run(source(first=750, last=780))


def test_reports_serialize_and_never_claim_real_university_readiness():
    _, _, result, _ = run()
    report = result.to_dict()["student_lunch"]
    assert json.loads(json.dumps(report)) == report
    assert report["enabled"] and report["compliant"] and report["violations"] == 0
    assert report["enforced_academic_years"] == [2]
    assert not report["lunch_resource_bookings_created"] and not report["lecturer_lunch_enforced"]
    assert not report["university_ready"] and "not implemented" not in result.to_dict()["limitations"][3].split("Travel")[0]


@pytest.mark.parametrize("selected", [True, False])
def test_unselected_delivery_does_not_inherit_a_hard_start_domain(selected):
    from schedence_teaching.solver.lunch_constraints import add_student_lunch_constraint
    _, config, _ = source(first=750, last=780)
    model = cp_model.CpModel()
    start = model.new_int_var(750, 750, "start")
    presence = model.new_bool_var("selected")
    model.add(presence == int(selected))
    add_student_lunch_constraint(model, start, [[750, 750]], 30, presence, ((0, 750, 810),), config)
    assert cp_model.CpSolver().solve(model) == (cp_model.INFEASIBLE if selected else cp_model.OPTIMAL)


def test_a_delivery_can_move_to_an_explicitly_excluded_date_without_creating_attendance():
    prepared, _, _, calendar = fixture([("G", 750, 780, "LECTURE", "TUESDAY")], years={"G": 2},
        dates={"MONDAY": FIRST, "TUESDAY": FIRST+timedelta(days=1)})
    config = SolverConfig(days=[dict(day=day, windows={"WEEKDAY": [dict(start_minute=750, end_minute=780)]})
        for day in calendar.day_dates], granularity_minutes=15, max_time_seconds=2)
    original = context(calendar)
    original["student_lunch_policy"].applicable_dates = {FIRST}
    result = run((prepared, config, calendar), **original)[2]
    assert result.schedule[0].day == "TUESDAY" and result.administrative_schedule == ()
    monday = next(row for row in result.student_lunch_report.assessment.groups if row.day == "MONDAY")
    assert not monday.direct_delivery_ids and not monday.inherited_delivery_ids


def test_lunch_only_mode_supports_posthoc_workload_and_preference_assessment():
    from schedence_teaching.workload import WeeklyWorkloadInput, confirmed_campus_policy, assess_weekly_workload
    from schedence_teaching.preferences import LecturerPreferenceInput, PreferenceScoringPolicy, prepare_lecturer_preferences, score_lecturer_preferences
    prepared, config, result, original = run()
    workload = WeeklyWorkloadInput(week_reference=WEEK, policy=confirmed_campus_policy(), profiles=[
        dict(staff_id="STAFF1", workload_band="LECTURER", evidence_reference="synthetic:classification")])
    assessment = assess_weekly_workload(prepared, config, result, workload, **original)
    assert assessment.staff[0].scheduled_teaching_minutes == 30
    preferences = prepare_lecturer_preferences(prepared.input_snapshot, LecturerPreferenceInput())
    scores = score_lecturer_preferences(prepared, config, result, preferences, original["student_lunch_calendar"],
        PreferenceScoringPolicy(policy_reference="synthetic:assessment-only"), **original)
    assert scores.assigned_delivery_count == 1


@pytest.fixture(scope="module")
def full_integration():
    fixture_api = runpy.run_path(str(Path(__file__).resolve().parents[1] / "examples" / "integration_fixture.py"))
    case = fixture_api["build_case"](time_limit=10.0)
    original = case["inputs"]
    calendar = original["consecutive_calendar"]
    lunch = context(calendar)
    enabled = {**case, "inputs": {**original, **lunch}}
    result, stats = fixture_api["run_case"](enabled)
    assert result.status is SolverStatus.OPTIMAL
    return enabled, result, stats


def test_all_existing_optimization_features_and_coverage_survive_hard_lunch(full_integration):
    case, result, stats = full_integration
    assert result.validation.assigned_occurrences == 7 and len(result.schedule) == 6
    assert result.validation.combined_deliveries == 1 and result.validation.lecturer_contact_minutes == 315
    report = result.student_lunch_report
    assert report.assessment.compliant and report.assessment.violation_count == 0
    assert result.consecutive_report.priority_order == ("DELIVERIES", "CONTACT_MINUTES", "PREFERENCE_PENALTY",
        "MAXIMUM_TARGET_DEVIATION", "AGGREGATE_TARGET_DEVIATION", "CONSECUTIVE_PENALTY")
    assert result.consecutive_report.objective_vector == (6, 315, 0, 13500, 50310, 30)
    assert stats["maximum_model_variables"] == 1666 and stats["maximum_model_constraints"] == 3759
    professor = next(row for row in result.work_period_report.staff if row.staff_id == "SYN-1-P")
    assert professor.reserved_administrative_minutes == 240 and professor.verified_completed_administrative_minutes == 0
    lab = next(row for row in result.schedule if any(item.requirement_id == "SYN-1-LAB2" for item in row.covered_sessions))
    assert lab.end_minute <= 750 or lab.start_minute >= 810
    assert case["inputs"]["staff_input"].workload_input.recognized_work == []


@pytest.mark.parametrize("name", ["validate_delivery_result", "validate_staff_policy_result", "validate_work_period_result",
    "validate_preference_result", "validate_fairness_result", "validate_consecutive_result"])
def test_every_component_result_gateway_checks_hard_lunch(full_integration, name):
    import schedence_teaching.solver as solver
    case, result, _ = full_integration
    assert getattr(solver, name)(case["prepared"], case["config"], result, **case["inputs"]) == result.validation


def test_all_postsolve_assessments_keep_the_original_lunch_context(full_integration):
    from schedence_teaching.workload import assess_weekly_workload
    from schedence_teaching.preferences import score_lecturer_preferences
    from schedence_teaching.fairness import assess_fairness
    from schedence_teaching.consecutive import assess_consecutive_sessions
    case, result, _ = full_integration
    prepared, config, original = case["prepared"], case["config"], case["inputs"]
    assert assess_weekly_workload(prepared, config, result, original["staff_input"].workload_input, **original) == result.workload_report
    score = score_lecturer_preferences(prepared, config, result, original["preference_input"], original["preference_calendar"],
        original["preference_policy"], **{key: value for key, value in original.items()
            if key not in {"preference_input", "preference_calendar", "preference_policy"}})
    assert score.total_weighted_penalty == 0
    fair = assess_fairness(prepared, config, result, original["fairness_calendar"], original["fairness_policy"], **original)
    assert fair.scheduled_teaching_minutes == 315
    consecutive = assess_consecutive_sessions(prepared, config, result, original["consecutive_calendar"], original["consecutive_policy"], **original)
    assert consecutive == result.consecutive_report.assessment


@pytest.mark.parametrize("status", [SolverStatus.UNKNOWN, SolverStatus.INFEASIBLE])
def test_full_integration_failure_preserves_mode_flags_without_partial_reports(full_integration, monkeypatch, status):
    case, _, _ = full_integration
    monkeypatch.setattr(cp_model.CpSolver, "solve", lambda *args, **kwargs: cp_model.UNKNOWN if status is SolverStatus.UNKNOWN else cp_model.INFEASIBLE)
    result = solve_timetable(case["prepared"], case["config"], **case["inputs"])
    assert result.status is status and not result.schedule and not result.administrative_schedule
    assert result.student_lunch_report is result.consecutive_report is result.fairness_report is result.preference_report is None
    assert validate_student_lunch_result(case["prepared"], case["config"], result, **case["inputs"]) is None


@pytest.mark.parametrize("first,last,status", [(720, 900, SolverStatus.OPTIMAL), (810, 885, SolverStatus.INFEASIBLE)])
def test_explicit_hard_consecutive_breaks_remain_hard_alongside_lunch(first, last, status):
    from schedence_teaching.consecutive import ConsecutivePolicy, ConfirmedMinimumBreak
    from schedence_teaching.solver import ConsecutiveOptimizationConfig
    prepared, _, _, calendar = fixture([("G", 720, 750), ("G", 810, 840)], years={"G": 2})
    config = SolverConfig(days=[dict(day="DAY", windows={"WEEKDAY": [dict(start_minute=first, end_minute=last)]})],
        granularity_minutes=15, max_time_seconds=2)
    consecutive = ConsecutivePolicy(policy_reference="synthetic:confirmed-break", week_reference=WEEK,
        provenance=dict(source="synthetic", reference="test-only"), score_lecturers=False, score_student_groups=False,
        confirmed_hard_minimum_break=ConfirmedMinimumBreak(minutes=45, confirmation="CONFIRMED",
            campus_policy_reference="synthetic:test-break", approval_reference="synthetic:approval", applies_to={"STUDENT_GROUP"}))
    result = run((prepared, config, calendar), consecutive_policy=consecutive, consecutive_calendar=calendar,
        consecutive_config=ConsecutiveOptimizationConfig(priority="EXISTING_OBJECTIVES_THEN_CONSECUTIVE",
            priority_policy_reference="synthetic:priority", enforce_confirmed_hard_break=True))[2]
    assert result.status is status
