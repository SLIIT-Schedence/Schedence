"""Independent validation of hard lunch mode and every earlier scheduling feature."""

from dataclasses import asdict, replace
import json
from math import isfinite

from .results import ScheduleIssue, SolverInputError, SolverResult, SolverStatus, TimetableValidationError


def student_lunch_validation_requested(result, policy, calendar, config):
    return (getattr(result, "student_lunch_enabled", False) is not False
        or getattr(result, "student_lunch_report", None) is not None
        or any(value is not None for value in (policy, calendar, config)))


def assessment_projection(result):
    """Hard lunch adds no objective stage or changed status semantics."""
    return replace(result, student_lunch_enabled=False, student_lunch_report=None)


def _issue(code, message):
    raise TimetableValidationError([ScheduleIssue(code, message)])


def validate_student_lunch_result(prepared, config, result, *, student_lunch_policy, student_lunch_calendar, student_lunch_config,
        venue_input=None, combination_input=None, combination_config=None, staff_input=None,
        work_periods=None, work_period_config=None, preference_input=None, preference_calendar=None,
        preference_policy=None, preference_config=None, fairness_policy=None, fairness_calendar=None, fairness_config=None,
        consecutive_policy=None, consecutive_calendar=None, consecutive_config=None):
    from schedence_teaching.lunch import assess_student_lunch
    from schedence_teaching.consecutive.results import json_value
    from .inputs import checked_inputs
    from .deliveries import checked_combinations, delivery_alternatives
    from .eligibility import candidate_pools
    from .staff_inputs import checked_staff_inputs
    from .venue_inputs import checked_venue_inputs
    from .work_period_inputs import checked_work_period_inputs
    from .preference_inputs import checked_preference_inputs
    from .fairness_inputs import checked_fairness_inputs
    from .consecutive_inputs import checked_consecutive_inputs
    from .lunch_inputs import checked_student_lunch_inputs
    from .lunch_results import StudentLunchEnforcementReport, student_lunch_report
    from .evaluation import _validate_evaluation_projection as validate_evaluation_result

    prepared, config = checked_inputs(prepared, config)
    if staff_input is not None:
        staff_input = checked_staff_inputs(prepared, config, staff_input)
    if combination_input is not None:
        combination_input, combination_config = checked_combinations(prepared, combination_input, combination_config)
    elif combination_config is not None:
        raise SolverInputError("Combination config requires original combination input.")
    if venue_input is not None:
        checked_venue_inputs(prepared, config, venue_input)
    if work_periods is not None or work_period_config is not None:
        work_periods, work_period_config = checked_work_period_inputs(prepared, config, staff_input, work_periods, work_period_config)
    if any(value is not None for value in (preference_input, preference_calendar, preference_policy, preference_config)):
        checked_preference_inputs(prepared, config, preference_input, preference_calendar, preference_policy,
            preference_config, staff_input=staff_input, work_periods=work_periods)
    if fairness_config is not None:
        checked_fairness_inputs(prepared, config, fairness_policy, fairness_calendar, fairness_config,
            staff_input=staff_input, work_periods=work_periods)
    elif fairness_policy is not None or fairness_calendar is not None:
        raise SolverInputError("Fairness policy/calendar require original explicit config.")
    alternatives = delivery_alternatives(prepared, combination_input, pools=candidate_pools(prepared.input_snapshot))
    if consecutive_config is not None:
        checked_consecutive_inputs(prepared, config, consecutive_policy, consecutive_calendar, consecutive_config, alternatives,
            staff_input=staff_input, work_periods=work_periods, preference_calendar=preference_calendar, fairness_calendar=fairness_calendar)
    elif consecutive_policy is not None or consecutive_calendar is not None:
        raise SolverInputError("Consecutive policy/calendar require original explicit config.")
    context = checked_student_lunch_inputs(prepared, config, student_lunch_policy, student_lunch_calendar, student_lunch_config,
        alternatives, staff_input=staff_input, work_periods=work_periods, preference_calendar=preference_calendar,
        fairness_calendar=fairness_calendar, consecutive_calendar=consecutive_calendar)
    if not isinstance(result, SolverResult) or type(result.status) is not SolverStatus or result.student_lunch_enabled is not True:
        _issue("student_lunch_mode", "Hard lunch validation requires its typed explicitly enabled solver result.")
    if (type(result.runtime_seconds) not in {int, float} or not isfinite(result.runtime_seconds) or result.runtime_seconds < 0
            or result.venue_enabled is not (venue_input is not None) or result.combination_enabled is not (combination_input is not None)
            or result.staff_policy_enabled is not (staff_input is not None) or result.work_period_enabled is not (work_periods is not None)
            or result.preference_optimization_enabled is not (preference_input is not None)
            or result.fairness_optimization_enabled is not (fairness_config is not None)
            or result.consecutive_optimization_enabled is not (consecutive_config is not None)
            or type(result.original_occurrence_count) is not int or result.original_occurrence_count != len(prepared.sessions)):
        _issue("student_lunch_metadata", "Result modes, runtime or original coverage differ from supplied scheduling inputs.")
    if result.status not in {SolverStatus.OPTIMAL, SolverStatus.FEASIBLE}:
        if (result.schedule != () or result.administrative_schedule != () or result.validation is not None
                or any(value is not None for value in (result.workload_report, result.work_period_report, result.preference_report,
                    result.fairness_report, result.consecutive_report, result.student_lunch_report, result.objective_value, result.objective_status))
                or type(result.failure_reason) is not str or not result.failure_reason.strip()
                or result.objective_mode != (combination_config.objective.value if combination_input is not None else None)):
            _issue("partial_student_lunch_failure", "Unsuccessful lunch-enabled runs must emit no partial timetable, bookings or compliance reports.")
        return None
    if result.failure_reason is not None:
        _issue("student_lunch_metadata", "Successful timetable cannot contain a failure reason.")
    original = dict(venue_input=venue_input, combination_input=combination_input, combination_config=combination_config,
        staff_input=staff_input, work_periods=work_periods, work_period_config=work_period_config,
        preference_input=preference_input, preference_calendar=preference_calendar, preference_policy=preference_policy,
        preference_config=preference_config, fairness_policy=fairness_policy, fairness_calendar=fairness_calendar,
        fairness_config=fairness_config, consecutive_policy=consecutive_policy, consecutive_calendar=consecutive_calendar,
        consecutive_config=consecutive_config)
    projected = assessment_projection(result)
    summary = validate_evaluation_result(prepared, config, projected, **original)
    assessment = assess_student_lunch(prepared, config, projected, context.calendar, context.policy, **original)
    if assessment.unknown_group_date_count:
        _issue("student_lunch_scope_unknown", "Hard lunch compliance cannot be claimed for unresolved applicability.")
    if assessment.violation_count:
        _issue("student_lunch_violation", "Selected teaching overlaps an applicable fixed student lunch period.")
    report = result.student_lunch_report
    expected = student_lunch_report(assessment, context)
    try:
        matches = (type(report) is StudentLunchEnforcementReport
            and json.dumps(json_value(asdict(report)), sort_keys=True) == json.dumps(json_value(asdict(expected)), sort_keys=True))
    except (AttributeError, TypeError, ValueError):
        matches = False
    if not matches:
        _issue("student_lunch_report_changed", "Lunch compliance, study years, windows, groups or confirmation metadata differs from independent assessment.")
    return summary
