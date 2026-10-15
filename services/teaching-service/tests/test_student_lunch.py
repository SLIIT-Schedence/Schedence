"""Fixed year-specific lunches; no solver lunch enforcement or real data."""

from dataclasses import replace
from datetime import date, timedelta
import json
from pathlib import Path
import runpy
import subprocess
import sys

from ortools.sat.python import cp_model
from pydantic import ValidationError
import pytest

from schedence_teaching.lunch import (
    LunchStatus, StudentLunchInputError, StudentLunchPeriod, StudentLunchPolicy,
    assess_student_lunch, confirmed_student_lunch_policy, validate_student_lunch_report,
)
from schedence_teaching.preferences import PreferenceCalendar
from schedence_teaching.preparation import prepare_group_combinations, prepare_timetable_input
from schedence_teaching.schemas import TeachingTimetableInput
from schedence_teaching.solver import (
    ScheduledSession, SolverConfig, SolverInputError, SolverResult, SolverStatus,
    TimetableValidationError, solve_timetable, validate_timetable,
)


FIRST, LAST, WEEK = date(2026, 10, 19), date(2026, 10, 25), "SYNTHETIC-LUNCH-WEEK"
PROVENANCE = dict(source="synthetic", reference="synthetic:confirmed-period-test")


def fixture(bookings=(), *, years=None, parents=None, streams=None, dates=None, day_streams=None):
    """Construct independently valid FEASIBLE bookings, not a fake optimal solve."""
    years, parents, streams = years or {"G": 1}, parents or {}, streams or {}
    dates = dates or {"DAY": FIRST}
    day_streams = day_streams or {day: {streams.get(group, "WEEKDAY") for group in years} for day in dates}
    people, offerings, teams, requirements, assignments = [], [], [], [], []
    for number, (group, start, end, *extra) in enumerate(bookings, 1):
        identity, offering, staff = f"REQ{number}", f"OFFER{number}", f"STAFF{number}"
        kind, day = extra[0] if extra else "LECTURE", extra[1] if len(extra) > 1 else next(iter(dates))
        people.append(dict(staff_id=staff, staff_name=f"Synthetic {staff}", position="Lecturer", staff_type="ACADEMIC"))
        offerings.append(dict(offering_id=offering, module_code="SYNTHETIC-SHARED-CODE", module_name=offering,
            academic_year=years[group], semester=1, specialization="Synthetic", degree_type="Synthetic",
            stream=streams.get(group, "WEEKDAY")))
        teams.append(dict(offering_id=offering, staff_id=staff, role="LIC"))
        requirements.append(dict(requirement_id=identity, offering_id=offering, group_id=group,
            session_type=kind, duration_minutes=end-start, sessions_per_week=1))
        assignments.append((identity, staff, day, start, end))
    prepared = prepare_timetable_input(TeachingTimetableInput(staff_members=people, module_offerings=offerings,
        teaching_team_members=teams, session_requirements=requirements, student_groups=[dict(group_id=identity,
            academic_year=year, semester=1, stream=streams.get(identity, "WEEKDAY"), student_count=20,
            parent_group_id=parents.get(identity)) for identity, year in years.items()]))
    config = SolverConfig(days=[dict(day=day, windows={stream: [dict(start_minute=0, end_minute=1440)]
        for stream in day_streams[day]}) for day in dates], granularity_minutes=1, max_time_seconds=1)
    sessions = {row.requirement_id: row for row in prepared.sessions}
    schedule = tuple(ScheduledSession(sessions[identity], day, start, end, staff) for identity, staff, day, start, end in assignments)
    result = SolverResult(SolverStatus.FEASIBLE, schedule, 0.0, validate_timetable(prepared, config, schedule),
        original_occurrence_count=len(prepared.sessions))
    calendar = PreferenceCalendar(week_reference=WEEK, week_start=FIRST, week_end=LAST, day_dates=dates, synthetic_data=True)
    return prepared, config, result, calendar


def policy(calendar=None, **updates):
    return confirmed_student_lunch_policy(policy_reference="synthetic:fixed-student-lunch", week_reference=WEEK,
        provenance=PROVENANCE, **{ "applicable_dates": set(calendar.day_dates.values()) if calendar else {FIRST},
            "applicable_streams": {"WEEKDAY", "WEEKEND"}, "applicability_reference": "synthetic:explicit-date-stream-scope", **updates})


def assess(source=None, settings=None, **original):
    source = source or fixture()
    prepared, config, result, calendar = source
    settings = settings or policy(calendar)
    before = repr((prepared, config, result, calendar, settings, original))
    report = assess_student_lunch(prepared, config, result, calendar, settings, **original)
    assert repr((prepared, config, result, calendar, settings, original)) == before
    assert validate_student_lunch_report(report, prepared, config, result, calendar, settings, **original) == report
    return report


def test_confirmed_factory_contains_exact_periods_without_inventing_applicability():
    settings = confirmed_student_lunch_policy(policy_reference="owner:confirmed-student-lunch", week_reference=WEEK,
        provenance=dict(source="project-owner", reference="confirmed-year-specific-lunch-correction"))
    assert [(row.academic_year, row.window.start_minute, row.window.end_minute) for row in settings.year_periods] == [
        (1, 750, 810), (2, 750, 810), (3, 810, 870), (4, 810, 870)]
    assert settings.applicable_dates is settings.applicable_streams is settings.applicability_reference is None
    assert all(row.window.end_minute-row.window.start_minute == 60 for row in settings.year_periods)


@pytest.mark.parametrize("year", [1, 2, 3, 4])
@pytest.mark.parametrize("placement", ["before", "after", "inside", "one_minute_start", "one_minute_end", "spanning"])
def test_half_open_boundaries_and_true_overlaps_for_every_study_year(year, placement):
    start = 750 if year <= 2 else 810
    end = start+60
    windows = {"before": (start-30, start, 0), "after": (end, end+30, 0), "inside": (start+15, end-15, 30),
        "one_minute_start": (start-29, start+1, 1), "one_minute_end": (end-1, end+29, 1), "spanning": (start-15, end+15, 60)}
    first, last, overlap = windows[placement]
    report = assess(fixture([("G", first, last)], years={"G": year}))
    row = report.groups[0]
    assert row.academic_year == year and row.on_date.year == 2026
    assert (row.lunch_start_minute, row.lunch_end_minute) == (start, end)
    assert report.compliant is (not overlap)
    assert sum(item.overlap_minutes for item in row.observed_overlaps) == overlap
    assert row.status is (LunchStatus.VIOLATED if overlap else LunchStatus.CLEAR)


@pytest.mark.parametrize("year", [1, 2])
def test_year_one_and_two_can_have_teaching_until_1230(year):
    assert assess(fixture([("G", 720, 750)], years={"G": year})).compliant is True


@pytest.mark.parametrize("year", [3, 4])
def test_year_three_and_four_are_clear_at_1230_but_protected_after_1400(year):
    assert assess(fixture([("G", 750, 810)], years={"G": year})).compliant is True
    assert assess(fixture([("G", 840, 870)], years={"G": year})).compliant is False


@pytest.mark.parametrize("kind", ["LECTURE", "TUTORIAL", "WORKSHOP", "LAB"])
def test_every_attended_session_type_counts(kind):
    report = assess(fixture([("G", 750, 780, kind)]))
    assert report.violation_count == 1 and report.violating_delivery_ids == ("REQ1:1",)


def test_nested_ancestor_attendance_does_not_make_parent_or_sibling_inherit_labs():
    source = fixture([("P", 720, 750), ("CHILD", 750, 780, "LAB"), ("SIBLING", 810, 840, "LAB")],
        years={"P": 1, "CHILD": 1, "LEAF": 1, "SIBLING": 1}, parents={"CHILD": "P", "LEAF": "CHILD", "SIBLING": "P"})
    report = assess(source)
    groups = {row.group_id: row for row in report.groups}
    assert groups["P"].status is groups["SIBLING"].status is LunchStatus.CLEAR
    assert groups["CHILD"].status is groups["LEAF"].status is LunchStatus.VIOLATED
    assert groups["LEAF"].inherited_delivery_ids == ("REQ1:1", "REQ2:1")
    assert groups["P"].direct_delivery_ids == ("REQ1:1",) and not groups["P"].inherited_delivery_ids
    assert report.violation_count == 2 and report.violating_delivery_ids == ("REQ2:1",)


@pytest.mark.parametrize("allow", [True, False])
def test_combined_and_separate_delivery_coverage_and_booking_counts_are_preserved(allow):
    from test_combination_solver import inputs
    source = inputs(frequency=1)
    source.policy.allow_combination = allow
    combinations = prepare_group_combinations(source)
    config = SolverConfig(days=[dict(day="DAY", windows={"WEEKDAY": [dict(start_minute=750, end_minute=810)]})],
        granularity_minutes=15, max_time_seconds=2, random_seed=7)
    result = solve_timetable(combinations.prepared, config, combination_input=combinations)
    calendar = PreferenceCalendar(week_reference=WEEK, week_start=FIRST, week_end=LAST, day_dates={"DAY": FIRST}, synthetic_data=True)
    report = assess((combinations.prepared, config, result, calendar), combination_input=combinations)
    assert report.covered_original_occurrence_count == 2 and report.violation_count == 2
    assert report.physical_delivery_count == len(report.violating_delivery_ids) == (1 if allow else 2)
    assert all(len(row.direct_delivery_ids) == 1 for row in report.groups)


@pytest.mark.parametrize("updates", [{"applicable_dates": None}, {"applicable_streams": None},
    {"applicable_dates": None, "applicable_streams": None, "applicability_reference": None}])
def test_unknown_applicability_never_becomes_a_compliance_claim(updates):
    source = fixture([("G", 750, 780)])
    report = assess(source, policy(source[3], **updates))
    assert report.compliant is None and report.violation_count == 0
    assert report.groups[0].status is LunchStatus.APPLICABILITY_UNKNOWN
    assert report.groups[0].observed_overlaps[0].overlap_minutes == 30
    assert not report.to_dict()["applicability_resolved"]


@pytest.mark.parametrize("scope", ["date", "stream", "no_dates", "no_streams"])
def test_explicit_scope_exclusions_remain_distinct_from_unknown(scope):
    updates = {"date": {"applicable_dates": {FIRST+timedelta(days=1)}},
        "stream": {"applicable_streams": {"WEEKEND"}}, "no_dates": {"applicable_dates": set()},
        "no_streams": {"applicable_streams": set()}}[scope]
    report = assess(fixture([("G", 750, 780)]), policy(**updates))
    assert report.compliant is None and report.violation_count == report.unknown_group_date_count == 0
    assert report.groups[0].status is LunchStatus.NOT_APPLICABLE
    assert report.groups[0].observed_overlaps and report.to_dict()["applicability_resolved"]


def test_weekend_is_not_inferred_from_weekday_scope_or_the_weekday_name():
    source = fixture([("G", 750, 780, "LECTURE", "SATURDAY")], streams={"G": "WEEKEND"},
        dates={"SATURDAY": date(2026, 10, 24)})
    assert assess(source, policy(source[3], applicable_streams={"WEEKDAY"})).groups[0].status is LunchStatus.NOT_APPLICABLE
    assert assess(source, policy(source[3])).compliant is False
    assert assess(source, policy(source[3], applicable_streams=None)).compliant is None


def test_unscheduled_stream_dates_are_not_teaching_or_attendance_days():
    source = fixture([], years={"G": 2}, dates={"DAY": FIRST}, day_streams={"DAY": {"WEEKEND"}})
    report = assess(source)
    assert report.groups[0].status is LunchStatus.NOT_APPLICABLE
    assert "group_stream_not_scheduled_on_date" in report.groups[0].diagnostics


def test_no_teaching_overlap_does_not_prove_lunch_consumption_or_campus_attendance():
    report = assess(fixture())
    output = report.to_dict()
    assert report.compliant is True and report.physical_delivery_count == 0
    assert not output["lunch_consumption_verified"] and not output["attendance_inferred"]
    assert not output["lecturer_lunch_assessed"] and not output["hard_lunch_constraints_enforced_by_solver"]
    assert not output["university_ready"] and "groups" not in output


def test_empty_group_input_does_not_hide_unknown_configuration():
    source = fixture()
    payload = source[0].input_snapshot.model_dump(round_trip=True)
    payload["student_groups"] = []
    prepared = prepare_timetable_input(TeachingTimetableInput.model_validate(payload))
    result = replace(source[2], validation=validate_timetable(prepared, source[1], ()))
    report = assess((prepared, source[1], result, source[3]), policy(applicable_dates=None, applicable_streams=None, applicability_reference=None))
    assert report.compliant is None and report.groups == () and not report.to_dict()["applicability_resolved"]


@pytest.mark.parametrize("year", [0, 5, 2026, True, "2", 2.0])
def test_invalid_study_years_are_rejected(year):
    with pytest.raises(ValidationError):
        StudentLunchPeriod(academic_year=year, window=dict(start_minute=750, end_minute=810))


@pytest.mark.parametrize("start,end", [(750, 780), (750, 809), (750, 811), (720, 840), (810, 750), (1410, 1470), (True, 810), (750.0, 810)])
def test_fixed_continuous_hour_and_strict_time_values_are_required(start, end):
    with pytest.raises(ValidationError):
        StudentLunchPeriod(academic_year=1, window=dict(start_minute=start, end_minute=end))


@pytest.mark.parametrize("change", ["missing_year", "duplicate_year", "unknown_year", "unconfirmed", "blank_reference", "no_provenance",
    "unreferenced_streams", "unreferenced_dates", "unreferenced_scope", "unknown_stream", "flexible_slots"])
def test_policy_rejects_missing_or_ambiguous_contract_information(change):
    payload = policy().model_dump(round_trip=True)
    if change == "missing_year":
        payload["year_periods"] = payload["year_periods"][:3]
    elif change == "duplicate_year":
        payload["year_periods"][3]["academic_year"] = 3
    elif change == "unknown_year":
        payload["year_periods"][3]["academic_year"] = 5
    elif change == "unconfirmed":
        payload["period_confirmation"] = "UNCONFIRMED"
    elif change == "blank_reference":
        payload["policy_reference"] = " "
    elif change == "no_provenance":
        del payload["provenance"]
    elif change.startswith("unreferenced"):
        payload["applicability_reference"] = None
        if change == "unreferenced_streams":
            payload["applicable_dates"] = None
        elif change == "unreferenced_dates":
            payload["applicable_streams"] = None
    elif change == "unknown_stream":
        payload["applicable_streams"] = {"EVENING"}
    else:
        payload["flexible_slots"] = [dict(start_minute=720, end_minute=840)]
    with pytest.raises(ValidationError):
        StudentLunchPolicy.model_validate(payload)


@pytest.mark.parametrize("change", ["week", "outside_dates", "mutated_year", "mutated_duration", "wrong_calendar_type"])
def test_assessment_revalidates_policy_and_calendar_snapshots(change):
    source, settings = fixture([("G", 750, 780)]), policy()
    if change == "week":
        settings.week_reference = "OTHER-WEEK"
    elif change == "outside_dates":
        settings.applicable_dates = {FIRST-timedelta(days=1)}
    elif change == "mutated_year":
        settings.year_periods[0].academic_year = 2026
    elif change == "mutated_duration":
        settings.year_periods[0].window.end_minute = 840
    else:
        source = (*source[:3], {})
    with pytest.raises(StudentLunchInputError):
        assess_student_lunch(*source, settings)


@pytest.mark.parametrize("status", [SolverStatus.UNKNOWN, SolverStatus.INFEASIBLE, SolverStatus.MODEL_INVALID])
def test_unsuccessful_timetables_cannot_receive_lunch_compliance(status):
    source = fixture()
    with pytest.raises(SolverInputError, match="complete feasible"):
        assess_student_lunch(source[0], source[1], replace(source[2], status=status), source[3], policy())


@pytest.mark.parametrize("change", ["missing_booking", "wrong_stream", "wrong_duration", "false_summary"])
def test_original_timetable_corruption_is_rejected_before_lunch_scoring(change):
    prepared, config, result, calendar = fixture([("G", 750, 780)])
    first = result.schedule[0]
    if change == "missing_booking":
        result = replace(result, schedule=())
    elif change == "wrong_stream":
        result = replace(result, schedule=(replace(first, session=replace(first.session, stream="WEEKEND")),))
    elif change == "wrong_duration":
        result = replace(result, schedule=(replace(first, end_minute=810),))
    else:
        result = replace(result, validation=replace(result.validation, assigned_occurrences=999))
    with pytest.raises((SolverInputError, TimetableValidationError)):
        assess_student_lunch(prepared, config, result, calendar, policy())


@pytest.mark.parametrize("change", ["group_year", "window", "overlap", "identity", "coverage", "policy", "calendar", "status", "boolean_count"])
def test_independent_report_validation_rejects_tampering(change):
    source = fixture([("G", 750, 780)])
    settings, report = policy(), assess(source)
    group = report.groups[0]
    if change == "group_year":
        report = replace(report, groups=(replace(group, academic_year=4),))
    elif change == "window":
        report = replace(report, groups=(replace(group, lunch_start_minute=720),))
    elif change in {"overlap", "identity"}:
        overlap = group.observed_overlaps[0]
        update = dict(overlap_minutes=1) if change == "overlap" else dict(delivery_id="UNKNOWN")
        report = replace(report, groups=(replace(group, observed_overlaps=(replace(overlap, **update),)),))
    elif change == "coverage":
        report = replace(report, covered_original_occurrence_count=999)
    elif change == "policy":
        report = replace(report, policy_snapshot_json="{}")
    elif change == "calendar":
        report = replace(report, calendar_snapshot_json="{}")
    elif change == "status":
        report = replace(report, groups=(replace(group, status=LunchStatus.CLEAR),))
    else:
        report = replace(report, physical_delivery_count=True)
    with pytest.raises(StudentLunchInputError, match="differs"):
        validate_student_lunch_report(report, *source, settings)


def test_assessment_and_report_validation_never_construct_a_cp_model(monkeypatch):
    source = fixture([("G", 750, 780)])
    def unexpected(*args, **kwargs):
        raise AssertionError("Lunch assessment must not construct a CP model")
    monkeypatch.setattr(cp_model, "CpModel", unexpected)
    assert assess(source).compliant is False


def test_serialization_preserves_exact_types_and_canonical_policy_order():
    source, settings = fixture([("G", 750, 780)]), policy()
    before = assess(source, settings)
    settings.year_periods = tuple(reversed(settings.year_periods))
    after = assess(source, settings)
    assert after == before
    output = after.to_dict(include_group_details=True)
    assert json.loads(json.dumps(output)) == output
    assert output["groups"][0]["on_date"] == FIRST.isoformat()
    assert output["group_delivery_violation_count"] == output["unique_violating_physical_delivery_count"] == 1


def test_all_existing_optimization_modes_remain_valid_and_lunch_is_assessed_separately():
    example = runpy.run_path(str(Path(__file__).resolve().parents[1] / "examples" / "integration_fixture.py"))
    case = example["build_case"](time_limit=10.0)
    result, _ = example["run_case"](case)
    original = case["inputs"]
    calendar = original["consecutive_calendar"]
    settings = policy(calendar)
    settings.week_reference = calendar.week_reference
    source = case["prepared"], case["config"], result, calendar
    report = assess(source, settings, **original)
    assert result.status is SolverStatus.OPTIMAL
    assert report.compliant is False and report.violation_count == 1
    row = next(row for row in report.groups if row.status is LunchStatus.VIOLATED)
    assert row.group_id == "SYN-1-G2-LAB" and row.observed_overlaps[0].overlap_minutes == 30
    assert not report.to_dict()["hard_lunch_constraints_enforced_by_solver"]
    assert result.validation.assigned_occurrences == 7 and len(result.schedule) == 6
    with pytest.raises(SolverInputError):
        assess_student_lunch(*source, settings)  # Original enabled contexts cannot be omitted.
    other_calendar = calendar.model_copy(update={"day_dates": {**calendar.day_dates, "MONDAY": FIRST+timedelta(days=2)}})
    with pytest.raises(ValueError):
        assess_student_lunch(*source[:3], other_calendar, settings, **original)


def test_fresh_lunch_preferences_and_solver_import_orders_work():
    for statement in ("import schedence_teaching.lunch; import schedence_teaching.preferences; import schedence_teaching.solver",
            "import schedence_teaching.preferences; import schedence_teaching.solver; import schedence_teaching.lunch"):
        result = subprocess.run([sys.executable, "-B", "-c", statement], capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
