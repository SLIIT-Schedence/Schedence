"""Independent staff checks in original minutes, without model domain/cap builders."""

import json
from collections import defaultdict

from schedence_teaching.preparation import UnscheduledSession
from schedence_teaching.workload.assessment import WeeklyWorkloadReport, _assess_validated_schedule

from .results import ScheduledSession, ScheduleIssue, SolverResult, SolverStatus, TimetableValidationError
from .staff_inputs import checked_staff_inputs

STAFF_CHECKS = (
    "staff_allowed_teaching_days", "staff_policy_dates", "staff_availability",
    "staff_blocked_periods", "explicit_daily_teaching_caps", "explicit_weekly_teaching_caps",
)


def staff_schedule_issues(prepared, inputs, schedule, *, delivery_ids=None):
    calendars = {row.staff_id: row for row in inputs.availability}
    expected = {row.occurrence_id: row for row in prepared.sessions}
    daily, weekly, seen = defaultdict(int), defaultdict(int), set()
    teaching_dates=defaultdict(set)
    issues = []
    for item in schedule:
        if (not isinstance(item, ScheduledSession) or not isinstance(item.session, UnscheduledSession)
                or type(item.session.occurrence_id) is not str or type(item.assigned_staff_id) is not str):
            continue  # Existing validators report malformed entries first.
        session = expected.get(item.session.occurrence_id)
        if session is None:
            continue
        identity = session.occurrence_id
        calendar = calendars.get(item.assigned_staff_id)
        if calendar is None:
            issues.append(ScheduleIssue("missing_staff_availability", "Assigned staff has no supplied calendar.", (identity,)))
            continue
        if type(item.day) is not str or item.day not in inputs.day_dates:
            continue
        if inputs.assignment_policy is not None:
            from .teaching_policy import assignment_issues
            for code in assignment_issues(prepared.input_snapshot,inputs,inputs.assignment_policy,
                    item.assigned_staff_id,session,inputs.day_dates[item.day]):
                issues.append(ScheduleIssue(code,'Assigned staff fails the versioned academic teaching policy.',(identity,)))
        if item.day not in calendar.allowed_teaching_days:
            issues.append(ScheduleIssue("staff_disallowed_day", "Assigned staff cannot teach on this supplied day.", (identity,)))
        if not calendar.effective_from <= inputs.day_dates[item.day] <= calendar.effective_until:
            issues.append(ScheduleIssue("staff_inactive_policy", "Staff availability is not effective on the assigned date.", (identity,)))
        if type(item.start_minute) is not int or type(item.end_minute) is not int:
            continue
        if not any(window.start_minute <= item.start_minute and item.end_minute <= window.end_minute
                   for window in calendar.available_windows.get(item.day, [])):
            issues.append(ScheduleIssue("outside_staff_availability", "Delivery must fit one explicit staff availability window.", (identity,)))
        if any(block.day == item.day and item.start_minute < block.end_minute and block.start_minute < item.end_minute
               for block in calendar.blocked_periods):
            issues.append(ScheduleIssue("staff_blocked_period", "Delivery overlaps a supplied staff blocked interval.", (identity,)))
        delivery_id = delivery_ids[identity] if delivery_ids is not None else identity
        key = (delivery_id, item.assigned_staff_id)
        if key not in seen:
            seen.add(key)
            daily[(item.assigned_staff_id, item.day)] += session.duration_minutes
            weekly[item.assigned_staff_id] += session.duration_minutes
            teaching_dates[item.assigned_staff_id].add(inputs.day_dates[item.day])
    for staff_id, calendar in calendars.items():
        from .teaching_policy import teaching_day_limit
        limit=teaching_day_limit(inputs,staff_id)
        if limit is not None and len(teaching_dates[staff_id])>limit:
            issues.append(ScheduleIssue('weekly_teaching_day_limit','Distinct teaching dates across both streams and modes exceed the explicit weekly limit.'))
        if calendar.weekly_teaching_max_minutes is not None and weekly[staff_id] > calendar.weekly_teaching_max_minutes:
            issues.append(ScheduleIssue("weekly_teaching_cap", "Selected teaching deliveries exceed an explicit weekly maximum."))
        if calendar.daily_teaching_max_minutes is not None and any(
            minutes > calendar.daily_teaching_max_minutes for (identity, _), minutes in daily.items() if identity == staff_id
        ):
            issues.append(ScheduleIssue("daily_teaching_cap", "Selected teaching deliveries exceed an explicit daily maximum."))
    return issues


def validate_workload_report(prepared, result, inputs):
    if type(result.staff_policy_enabled) is not bool or result.staff_policy_enabled != (inputs is not None):
        raise TimetableValidationError([ScheduleIssue("staff_policy_mode_changed", "Staff policy mode does not match the validation inputs.")])
    if inputs is None:
        if result.workload_report is not None:
            raise TimetableValidationError([ScheduleIssue("unexpected_workload_report", "Workload report requires original staff policy inputs.")])
        return
    canonical = _assess_validated_schedule(prepared, result, inputs.workload_input, staff_constraints_enforced=True)
    try:
        matches = (isinstance(result.workload_report, WeeklyWorkloadReport)
                   and json.dumps(result.workload_report.to_dict(), sort_keys=True) == json.dumps(canonical.to_dict(), sort_keys=True))
    except (AttributeError, TypeError, ValueError):
        matches = False
    if not matches:
        raise TimetableValidationError([ScheduleIssue("workload_summary_changed", "Workload, daily totals, attendance or evidence summary differs from independently recomputed inputs.")])


def validate_staff_policy_result(prepared, config, result, *, staff_input, venue_input=None,
                                 combination_input=None, combination_config=None,
                                 work_periods=None, work_period_config=None, preference_input=None,
                                 preference_calendar=None, preference_policy=None, preference_config=None,
                                 fairness_policy=None, fairness_calendar=None, fairness_config=None,
                                 consecutive_policy=None, consecutive_calendar=None, consecutive_config=None,
        student_lunch_policy=None, student_lunch_calendar=None, student_lunch_config=None):
    """Validate both legacy and combined schedules plus their attached policy report."""
    from .lunch_validation import student_lunch_validation_requested, validate_student_lunch_result
    if student_lunch_validation_requested(result, student_lunch_policy, student_lunch_calendar, student_lunch_config):
        return validate_student_lunch_result(prepared, config, result, student_lunch_policy=student_lunch_policy,
            student_lunch_calendar=student_lunch_calendar, student_lunch_config=student_lunch_config,
            venue_input=venue_input, combination_input=combination_input, combination_config=combination_config,
            staff_input=staff_input, work_periods=work_periods, work_period_config=work_period_config,
            preference_input=preference_input, preference_calendar=preference_calendar, preference_policy=preference_policy,
            preference_config=preference_config, fairness_policy=fairness_policy, fairness_calendar=fairness_calendar,
            fairness_config=fairness_config, consecutive_policy=consecutive_policy, consecutive_calendar=consecutive_calendar,
            consecutive_config=consecutive_config)
    from .consecutive_validation import consecutive_validation_requested, validate_consecutive_result
    if consecutive_validation_requested(result, consecutive_policy, consecutive_calendar, consecutive_config):
        return validate_consecutive_result(prepared, config, result, consecutive_policy=consecutive_policy,
            consecutive_calendar=consecutive_calendar, consecutive_config=consecutive_config,
            venue_input=venue_input, combination_input=combination_input, combination_config=combination_config,
            staff_input=staff_input, work_periods=work_periods, work_period_config=work_period_config,
            preference_input=preference_input, preference_calendar=preference_calendar,
            preference_policy=preference_policy, preference_config=preference_config,
            fairness_policy=fairness_policy, fairness_calendar=fairness_calendar, fairness_config=fairness_config)
    from .fairness_validation import fairness_validation_requested, validate_fairness_result
    if fairness_validation_requested(result, fairness_policy, fairness_calendar, fairness_config):
        return validate_fairness_result(prepared, config, result, fairness_policy=fairness_policy,
            fairness_calendar=fairness_calendar, fairness_config=fairness_config,
            venue_input=venue_input, combination_input=combination_input, combination_config=combination_config,
            staff_input=staff_input, work_periods=work_periods, work_period_config=work_period_config,
            preference_input=preference_input, preference_calendar=preference_calendar,
            preference_policy=preference_policy, preference_config=preference_config)
    from .inputs import checked_inputs
    from .validator import validate_timetable
    from .delivery_validation import validate_delivery_result

    prepared, config = checked_inputs(prepared, config)
    if (not isinstance(result, SolverResult) or type(result.status) is not SolverStatus
            or result.status not in {SolverStatus.OPTIMAL, SolverStatus.FEASIBLE}):
        raise TimetableValidationError([ScheduleIssue("invalid_result", "A complete feasible policy-enabled result is required.")])
    inputs = checked_staff_inputs(prepared, config, staff_input)
    if combination_input is not None:
        return validate_delivery_result(prepared, config, result, combination_input=combination_input,
                                        combination_config=combination_config, venue_input=venue_input, staff_input=inputs,
                                        work_periods=work_periods, work_period_config=work_period_config,
                                        preference_input=preference_input, preference_calendar=preference_calendar,
                                        preference_policy=preference_policy, preference_config=preference_config)
    if result.combination_enabled or combination_config is not None:
        raise TimetableValidationError([ScheduleIssue("missing_combination_input", "Combined result requires original combination evidence.")])
    summary = validate_timetable(prepared, config, result.schedule, venue_input=venue_input, staff_input=inputs)
    if (result.validation != summary
            or any(type(getattr(result.validation, key)) is not type(value) for key, value in vars(summary).items())
            or type(result.original_occurrence_count) is not int
            or result.original_occurrence_count != summary.expected_occurrences
            or type(result.combination_enabled) is not bool
            or type(result.venue_enabled) is not bool or result.venue_enabled != (venue_input is not None)):
        raise TimetableValidationError([ScheduleIssue("result_summary_changed", "Reported validation/counts do not match the policy-enabled schedule.")])
    validate_workload_report(prepared, result, inputs)
    from .work_period_validation import validate_work_period_outputs
    validate_work_period_outputs(prepared, config, result, staff_input=inputs,
                                 work_periods=work_periods, work_period_config=work_period_config)
    from .preference_validation import validate_preference_outputs
    validate_preference_outputs(prepared, config, result, preference_input=preference_input,
        preference_calendar=preference_calendar, preference_policy=preference_policy, preference_config=preference_config,
        staff_input=inputs, work_periods=work_periods)
    return summary
