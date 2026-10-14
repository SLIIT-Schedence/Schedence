"""Independent Step 10A recomputation of selected-delivery preference objectives."""

import json
from math import isfinite

from .deliveries import checked_combinations
from .inputs import checked_inputs
from .preference_constraints import ModeledPreferencePenalty
from .preference_inputs import checked_preference_inputs, objective_plan
from .preference_results import PreferenceOptimizationReport, preference_report
from .results import ScheduleIssue, SolverResult, SolverStatus, TimetableValidationError


def _issue(code, message):
    raise TimetableValidationError([ScheduleIssue(code, message)])


def validate_preference_outputs(prepared, config, result, *, preference_input=None, preference_calendar=None,
                                 preference_policy=None, preference_config=None, combination_config=None,
                                 staff_input=None, work_periods=None):
    """Called only after original hard validation; model predicates are never used."""
    if type(result.preference_optimization_enabled) is not bool or result.preference_optimization_enabled != (preference_input is not None):
        _issue("preference_mode_changed", "Preference mode and original preference validation inputs must agree.")
    if preference_input is None:
        if any(row is not None for row in (preference_calendar, preference_policy, preference_config, result.preference_report)):
            _issue("unexpected_preference_output", "Disabled preference mode cannot contain a preference report or optimization inputs.")
        return
    context = checked_preference_inputs(prepared, config, preference_input, preference_calendar, preference_policy,
                                        preference_config, staff_input=staff_input, work_periods=work_periods)
    report = result.preference_report
    if not isinstance(report, PreferenceOptimizationReport):
        _issue("missing_preference_report", "Successful preference optimization requires its independently checkable report.")
    if type(result.runtime_seconds) not in {int, float} or not isfinite(result.runtime_seconds) or result.runtime_seconds < 0 or result.failure_reason is not None:
        _issue("preference_result_metadata", "Successful optimization needs a finite runtime and no failure reason.")
    if not result.combination_enabled and any(value is not None for value in
        (result.objective_mode, result.objective_value, result.objective_status)):
        _issue("preference_result_metadata", "Non-combination optimization reports its objective only in the preference report.")
    from schedence_teaching.preferences.scoring import _score_validated_timetable
    from schedence_teaching.preferences.results import PreferenceSatisfaction
    score = _score_validated_timetable(prepared, config, result, context.preferences, context.calendar,
                                      context.policy, staff_input=staff_input, work_periods=work_periods)
    if score.unresolved_count or score.weighted_scoring_complete is not True:
        _issue("incomplete_preference_score", "An unresolved or incompletely weighted preference score cannot claim optimization.")
    expected = tuple(ModeledPreferencePenalty(row.delivery_id, row.staff_id, row.preference_ids,
        row.satisfaction is PreferenceSatisfaction.VIOLATED, row.penalty) for row in score.evaluations)
    try:
        key = lambda row: (row.delivery_id, row.staff_id, row.preference_ids)
        matches = type(report.modeled_evaluations) is tuple and all(type(row) is ModeledPreferencePenalty for row in report.modeled_evaluations)
        matches = matches and json.dumps([vars(row) for row in sorted(report.modeled_evaluations, key=key)], sort_keys=True) == json.dumps(
            [vars(row) for row in sorted(expected, key=key)], sort_keys=True)
    except (TypeError, AttributeError):
        matches = False
    if not matches:
        _issue("modeled_preference_penalties_changed", "Modeled staff, applicability, alternatives, violations or penalties differ from selected-delivery scoring.")
    canonical = preference_report(prepared, config, result, context, report.modeled_evaluations,
                                  combination_config, staff_input=staff_input, work_periods=work_periods)
    try:
        matches = json.dumps(report.to_dict(include_staff_details=True), sort_keys=True) == json.dumps(
            canonical.to_dict(include_staff_details=True), sort_keys=True)
    except (AttributeError, TypeError, ValueError):
        matches = False
    if not matches:
        _issue("preference_summary_changed", "Preference score, confirmation counts, priorities or bounded objective differ from independent recomputation.")


def validate_preference_result(prepared, config, result, *, preference_input, preference_calendar,
                                preference_policy, preference_config, venue_input=None, combination_input=None,
                                combination_config=None, staff_input=None, work_periods=None, work_period_config=None,
                                fairness_policy=None, fairness_calendar=None, fairness_config=None,
                                consecutive_policy=None, consecutive_calendar=None, consecutive_config=None,
        student_lunch_policy=None, student_lunch_calendar=None, student_lunch_config=None):
    """Validate complete successes or empty failures, retaining every prior hard check."""
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
    from .staff_inputs import checked_staff_inputs
    from .staff_validation import validate_staff_policy_result, validate_workload_report
    from .delivery_validation import validate_delivery_result
    from .venue_inputs import checked_venue_inputs
    from .work_period_inputs import checked_work_period_inputs
    from .work_period_validation import validate_work_period_outputs, validate_work_period_result
    from .validator import validate_timetable
    from .results import SolverInputError

    prepared, config = checked_inputs(prepared, config)
    if combination_config is not None and combination_input is None:
        raise SolverInputError("Combination config requires original prepared combination input.")
    if combination_input is not None:
        combination_input, combination_config = checked_combinations(prepared, combination_input, combination_config)
    if venue_input is not None:
        checked_venue_inputs(prepared, config, venue_input)
    if staff_input is not None:
        staff_input = checked_staff_inputs(prepared, config, staff_input)
    if work_periods is not None:
        work_periods, work_period_config = checked_work_period_inputs(prepared, config, staff_input, work_periods, work_period_config)
    elif work_period_config is not None:
        raise SolverInputError("Work-period config requires original prepared periods.")
    context = checked_preference_inputs(prepared, config, preference_input, preference_calendar, preference_policy,
                                        preference_config, staff_input=staff_input, work_periods=work_periods)
    objective_plan(prepared, context, combination_config)
    if not isinstance(result, SolverResult) or type(result.status) is not SolverStatus:
        _issue("invalid_result", "Expected typed SolverResult.")
    if result.status not in {SolverStatus.OPTIMAL, SolverStatus.FEASIBLE}:
        if (result.schedule != () or result.administrative_schedule != () or result.validation is not None
                or result.workload_report is not None or result.work_period_report is not None or result.preference_report is not None
                or result.objective_value is not None or result.objective_status is not None
                or result.preference_optimization_enabled is not True or type(result.failure_reason) is not str
                or not result.failure_reason.strip() or type(result.runtime_seconds) not in {int, float}
                or not isfinite(result.runtime_seconds) or result.runtime_seconds < 0
                or result.combination_enabled is not (combination_input is not None)
                or result.venue_enabled is not (venue_input is not None) or result.staff_policy_enabled is not (staff_input is not None)
                or result.work_period_enabled is not (work_periods is not None)
                or type(result.original_occurrence_count) is not int or result.original_occurrence_count != len(prepared.sessions)
                or result.objective_mode != (combination_config.objective.value if combination_input is not None else None)):
            _issue("partial_preference_failure", "Unsuccessful optimization must emit no partial timetable, reservations, score or objective value.")
        return None
    preference_args = dict(preference_input=preference_input, preference_calendar=preference_calendar,
                           preference_policy=preference_policy, preference_config=preference_config)
    base_args = dict(venue_input=venue_input, combination_input=combination_input, combination_config=combination_config)
    if work_periods is not None or result.work_period_enabled:
        return validate_work_period_result(prepared, config, result, staff_input=staff_input, work_periods=work_periods,
            work_period_config=work_period_config, **base_args, **preference_args)
    if staff_input is not None or result.staff_policy_enabled:
        return validate_staff_policy_result(prepared, config, result, staff_input=staff_input, **base_args, **preference_args)
    if combination_input is not None:
        return validate_delivery_result(prepared, config, result, **base_args, **preference_args)
    summary = validate_timetable(prepared, config, result.schedule, venue_input=venue_input)
    if (result.validation != summary or any(type(getattr(result.validation, key)) is not type(value) for key, value in vars(summary).items())
            or result.combination_enabled is not False or result.venue_enabled is not (venue_input is not None)
            or type(result.original_occurrence_count) is not int or result.original_occurrence_count != len(prepared.sessions)
            or result.objective_mode is not None or result.objective_value is not None or result.objective_status is not None):
        _issue("preference_result_metadata", "Result mode, coverage or legacy component metadata differs from the validated timetable.")
    validate_workload_report(prepared, result, None)
    validate_work_period_outputs(prepared, config, result, staff_input=None)
    validate_preference_outputs(prepared, config, result, **preference_args)
    return summary
