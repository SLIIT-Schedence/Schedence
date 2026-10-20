"""Independent assessment of actual student attendance against fixed lunches."""

from collections import defaultdict
from dataclasses import asdict
import json

from pydantic import ValidationError

from schedence_teaching.consecutive.metrics import delivery_id
from schedence_teaching.consecutive.results import json_value
from schedence_teaching.preferences import PreferenceCalendar
from schedence_teaching.preferences.scoring import _calendar
from schedence_teaching.solver.evaluation import _validate_evaluation_projection as validate_evaluation_result
from schedence_teaching.solver.inputs import checked_inputs
from schedence_teaching.solver.results import ScheduledDelivery

from .contracts import StudentLunchPolicy
from .applicability import attending_group_ids, lunch_applicability
from .results import GroupDateLunchAssessment, LunchOverlap, LunchStatus, StudentLunchReport


class StudentLunchInputError(ValueError):
    pass


def _snapshot(value, model):
    if not isinstance(value, model):
        raise StudentLunchInputError(f"Expected {model.__name__}.")
    try:
        return model.model_validate(value.model_dump(round_trip=True))
    except ValidationError as error:
        raise StudentLunchInputError("; ".join(row["msg"] for row in error.errors(
            include_input=False, include_context=False, include_url=False))) from None


def assess_student_lunch(prepared, config, result, calendar: PreferenceCalendar, policy: StudentLunchPolicy, *,
        venue_input=None, combination_input=None, combination_config=None, staff_input=None,
        work_periods=None, work_period_config=None, preference_input=None, preference_calendar=None,
        preference_policy=None, preference_config=None, fairness_policy=None, fairness_calendar=None, fairness_config=None,
        consecutive_policy=None, consecutive_calendar=None, consecutive_config=None,
        student_lunch_policy=None, student_lunch_calendar=None, student_lunch_config=None):
    """Keep all existing hard/report checks, then assess without changing bookings.

    Half-open teaching windows touching a lunch boundary are permitted. Every
    session type attended by a group is considered, including ancestor teaching.
    A lunch violation does not retroactively claim CP-SAT enforced lunch in D.1.
    """
    prepared, config = checked_inputs(prepared, config)
    validation = validate_evaluation_result(prepared, config, result, venue_input=venue_input,
        combination_input=combination_input, combination_config=combination_config, staff_input=staff_input,
        work_periods=work_periods, work_period_config=work_period_config, preference_input=preference_input,
        preference_calendar=preference_calendar, preference_policy=preference_policy, preference_config=preference_config,
        fairness_policy=fairness_policy, fairness_calendar=fairness_calendar, fairness_config=fairness_config,
        consecutive_policy=consecutive_policy, consecutive_calendar=consecutive_calendar, consecutive_config=consecutive_config,
        student_lunch_policy=student_lunch_policy, student_lunch_calendar=student_lunch_calendar, student_lunch_config=student_lunch_config)
    policy = _snapshot(policy, StudentLunchPolicy)
    from schedence_teaching.policy.resolution import verify_component
    verify_component(config, 'student_lunch_policy', policy)
    calendar = _calendar(config, _snapshot(calendar, PreferenceCalendar), staff_input, work_periods)
    if policy.week_reference != calendar.week_reference:
        raise StudentLunchInputError("Student lunch policy and calendar must reference the same academic week.")
    if any(other is not None and calendar != other for other in (preference_calendar, fairness_calendar, consecutive_calendar)):
        raise StudentLunchInputError("Lunch and original scheduling assessment calendars must agree.")
    if policy.applicable_dates is not None and any(not calendar.week_start <= day <= calendar.week_end for day in policy.applicable_dates):
        raise StudentLunchInputError("Lunch applicability dates must be inside the supplied academic week.")
    direct, inherited = defaultdict(dict), defaultdict(dict)
    for booking in result.schedule:
        identity = delivery_id(booking)
        groups = booking.group_ids if isinstance(booking, ScheduledDelivery) else (booking.session.group_id,)
        for group in groups:
            direct[group][identity] = booking
        for descendant in set(attending_group_ids(prepared, groups)) - set(groups):
            inherited[descendant][identity] = booking
    periods = {row.academic_year: row.window for row in policy.year_periods}
    streams = {row.day: set(row.windows) for row in config.days}
    assessments, diagnostics = [], []
    if policy.applicable_dates is None:
        diagnostics.append("applicable_dates_not_supplied")
    if policy.applicable_streams is None:
        diagnostics.append("weekday_weekend_applicability_not_supplied")
    for group in sorted(prepared.input_snapshot.student_groups, key=lambda row: row.group_id):
        # academic_year is the validated study year, never on_date.year.
        lunch = periods[group.academic_year]
        deliveries = {**inherited[group.group_id], **direct[group.group_id]}
        for day, on_date in sorted(calendar.day_dates.items(), key=lambda pair: pair[1]):
            day_rows = {identity: row for identity, row in deliveries.items() if row.day == day}
            overlaps = tuple(LunchOverlap(identity,
                tuple(sorted(item.occurrence_id for item in row.covered_sessions)) if isinstance(row, ScheduledDelivery) else (row.session.occurrence_id,),
                row.start_minute, row.end_minute, min(row.end_minute, lunch.end_minute)-max(row.start_minute, lunch.start_minute))
                for identity, row in sorted(day_rows.items())
                if row.start_minute < lunch.end_minute and lunch.start_minute < row.end_minute)
            applies, reasons = lunch_applicability(policy, on_date, group.stream, streams[day])
            reasons = list(reasons)
            if applies is False:
                status = LunchStatus.NOT_APPLICABLE
            elif applies is None:
                status = LunchStatus.APPLICABILITY_UNKNOWN
            else:
                status = LunchStatus.VIOLATED if overlaps else LunchStatus.CLEAR
            if not day_rows:
                reasons.append("no_scheduled_teaching_attendance_on_date")
            assessments.append(GroupDateLunchAssessment(group.group_id, group.academic_year, group.stream, day, on_date,
                lunch.start_minute, lunch.end_minute, status, tuple(sorted(direct[group.group_id].keys() & day_rows.keys())),
                tuple(sorted((inherited[group.group_id].keys() - direct[group.group_id].keys()) & day_rows.keys())), overlaps, tuple(reasons)))
    if not any(row.status in {LunchStatus.CLEAR, LunchStatus.VIOLATED} for row in assessments):
        diagnostics.append("no_resolved_applicable_group_dates")
    diagnostics.extend(("fixed_periods_assessed_not_enforced_by_solver", "lecturer_lunch_policy_not_supplied"))
    payload = policy.model_dump(mode="json")
    for key in ("applicable_dates", "applicable_streams"):
        if payload[key] is not None:
            payload[key] = sorted(payload[key])
    payload["year_periods"] = sorted(payload["year_periods"], key=lambda row: row["academic_year"])
    report = StudentLunchReport(json.dumps(payload, sort_keys=True), json.dumps(calendar.model_dump(mode="json"), sort_keys=True),
        len(result.schedule), validation.assigned_occurrences, tuple(assessments), tuple(validation.checks), tuple(diagnostics))
    if getattr(result, "student_lunch_enabled", False) and policy == student_lunch_policy and calendar == student_lunch_calendar:
        from schedence_teaching.solver.lunch_results import enforced_lunch_assessment
        return enforced_lunch_assessment(report)
    return report


def validate_student_lunch_report(report, *args, **kwargs):
    """Regenerate every attendance, overlap and provenance value from original inputs."""
    expected = assess_student_lunch(*args, **kwargs)
    try:
        matches = (type(report) is StudentLunchReport
            and json.dumps(json_value(asdict(report)), sort_keys=True) == json.dumps(json_value(asdict(expected)), sort_keys=True))
    except (AttributeError, TypeError, ValueError):
        matches = False
    if not matches:
        raise StudentLunchInputError("Student lunch report differs from independently recomputed attendance, periods or applicability.")
    return expected
