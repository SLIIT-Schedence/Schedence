"""Common independent validation gateway for descriptive post-solve assessment."""

from .results import SolverResult, SolverStatus, SolverInputError


def validate_evaluation_result(prepared, config, result, *, venue_input=None,
        combination_input=None, combination_config=None, staff_input=None,
        work_periods=None, work_period_config=None, preference_input=None,
        preference_calendar=None, preference_policy=None, preference_config=None,
        fairness_policy=None, fairness_calendar=None, fairness_config=None,
        consecutive_policy=None, consecutive_calendar=None, consecutive_config=None,
        student_lunch_policy=None, student_lunch_calendar=None, student_lunch_config=None,
        _policy_projection=False):
    """Validate original contexts, coverage, hard constraints and attached reports.

    This dispatches existing validators; it builds no CP model and adds no policy.
    """
    from schedence_teaching.policy.resolution import verify_runtime_contexts
    try:
        verify_runtime_contexts(config, locals().copy(), require_all=not _policy_projection)
    except ValueError as error:
        raise SolverInputError(str(error)) from None
    if (not isinstance(result, SolverResult) or type(result.status) is not SolverStatus
            or result.status not in {SolverStatus.OPTIMAL, SolverStatus.FEASIBLE}):
        raise SolverInputError("Evaluation requires a complete feasible teaching result.")
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
    if (not isinstance(result, SolverResult) or type(result.status) is not SolverStatus
            or result.status not in {SolverStatus.OPTIMAL, SolverStatus.FEASIBLE}):
        raise SolverInputError("Evaluation requires a complete feasible teaching result.")
    from .fairness_validation import fairness_validation_requested, validate_fairness_result
    if fairness_validation_requested(result, fairness_policy, fairness_calendar, fairness_config):
        return validate_fairness_result(prepared, config, result, fairness_policy=fairness_policy,
            fairness_calendar=fairness_calendar, fairness_config=fairness_config,
            venue_input=venue_input, combination_input=combination_input, combination_config=combination_config,
            staff_input=staff_input, work_periods=work_periods, work_period_config=work_period_config,
            preference_input=preference_input, preference_calendar=preference_calendar,
            preference_policy=preference_policy, preference_config=preference_config)
    from . import (validate_delivery_result, validate_staff_policy_result,
                   validate_timetable, validate_work_period_result)
    from .staff_validation import validate_workload_report
    from .work_period_validation import validate_work_period_outputs

    if (not isinstance(result, SolverResult) or type(result.status) is not SolverStatus
            or result.status not in {SolverStatus.OPTIMAL, SolverStatus.FEASIBLE} or result.validation is None):
        raise SolverInputError("Evaluation requires a complete feasible teaching result.")
    if type(result.preference_optimization_enabled) is not bool or type(result.combination_enabled) is not bool:
        raise SolverInputError("Result resource modes must be explicit booleans.")
    if result.preference_optimization_enabled is False and result.preference_report is not None:
        raise SolverInputError("Disabled preference optimization cannot contain an optimization report.")
    if result.preference_optimization_enabled or preference_config is not None:
        from .preference_validation import validate_preference_result
        return validate_preference_result(prepared, config, result, preference_input=preference_input,
            preference_calendar=preference_calendar, preference_policy=preference_policy, preference_config=preference_config,
            venue_input=venue_input, combination_input=combination_input, combination_config=combination_config,
            staff_input=staff_input, work_periods=work_periods, work_period_config=work_period_config)
    if combination_input is None and (result.combination_enabled or combination_config is not None):
        raise SolverInputError("Combined teaching evaluation requires its original combination input.")
    if work_periods is not None or work_period_config is not None or result.work_period_enabled:
        return validate_work_period_result(prepared, config, result, staff_input=staff_input,
            work_periods=work_periods, work_period_config=work_period_config, venue_input=venue_input,
            combination_input=combination_input, combination_config=combination_config)
    if staff_input is not None or result.staff_policy_enabled:
        return validate_staff_policy_result(prepared, config, result, staff_input=staff_input, venue_input=venue_input,
            combination_input=combination_input, combination_config=combination_config)
    if combination_input is not None:
        return validate_delivery_result(prepared, config, result, combination_input=combination_input,
            combination_config=combination_config, venue_input=venue_input)
    summary = validate_timetable(prepared, config, result.schedule, venue_input=venue_input)
    if (type(result.validation) is not type(summary) or result.validation != summary
            or any(type(getattr(result.validation, key)) is not type(value) for key, value in vars(summary).items())
            or result.venue_enabled is not (venue_input is not None)
            or type(result.original_occurrence_count) is not int
            or result.original_occurrence_count != len(prepared.sessions)):
        raise SolverInputError("Result validation, resource mode or coverage differs from the actual timetable.")
    validate_workload_report(prepared, result, None)
    validate_work_period_outputs(prepared, config, result, staff_input=None)
    return summary


def _validate_evaluation_projection(prepared, config, result, **inputs):
    """Internal assessment projection, never the full policy-validation gateway."""
    return validate_evaluation_result(prepared, config, result, _policy_projection=True, **inputs)
