"""Independent Step 11A recomputation of consecutive scheduling output."""

from dataclasses import asdict, replace
import json
from math import isfinite

from .results import ScheduleIssue, SolverInputError, SolverResult, SolverStatus, TimetableValidationError


def consecutive_validation_requested(result, policy, calendar, config):
    return (getattr(result, "consecutive_optimization_enabled", False) is not False
        or getattr(result, "consecutive_report", None) is not None
        or any(value is not None for value in (policy, calendar, config)))


def assessment_projection(result):
    """Private earlier-objective validation view; public status remains the full solve.

    A proven fairness prefix may be OPTIMAL while the new last stage is FEASIBLE.
    Earlier validators retain their original proof/metadata checks on that prefix.
    """
    projected = replace(result, consecutive_optimization_enabled=False, consecutive_report=None)
    if result.fairness_optimization_enabled:
        from .fairness_results import FairnessOptimizationReport
        if not isinstance(result.fairness_report, FairnessOptimizationReport):
            _issue("consecutive_prefix_report", "Expected original typed fairness report.")
        prefix_status = SolverStatus.OPTIMAL if result.fairness_report.all_stages_proven_optimal is True else SolverStatus.FEASIBLE
        projected = replace(projected, status=prefix_status,
            fairness_report=replace(result.fairness_report, objective_status=prefix_status.value))
    if result.preference_report is not None:
        from .preference_results import PreferenceOptimizationReport
        if not isinstance(result.preference_report, PreferenceOptimizationReport):
            _issue("consecutive_prefix_report", "Expected original typed preference report.")
        projected = replace(projected, preference_report=replace(result.preference_report,
            objective_status="EVALUATED_COMPONENT" if result.fairness_optimization_enabled else projected.status.value))
    if result.objective_value is not None:
        projected = replace(projected, objective_status="EVALUATED_COMPONENT" if
            result.fairness_optimization_enabled or result.preference_optimization_enabled else projected.status.value)
    return projected


def _issue(code, message):
    raise TimetableValidationError([ScheduleIssue(code, message)])


def validate_consecutive_result(prepared, config, result, *, consecutive_policy, consecutive_calendar, consecutive_config,
        venue_input=None, combination_input=None, combination_config=None, staff_input=None,
        work_periods=None, work_period_config=None, preference_input=None, preference_calendar=None,
        preference_policy=None, preference_config=None, fairness_policy=None, fairness_calendar=None, fairness_config=None,
        student_lunch_policy=None, student_lunch_calendar=None, student_lunch_config=None):
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
    from schedence_teaching.consecutive import assess_consecutive_sessions
    from schedence_teaching.consecutive.results import json_value
    from .evaluation import _validate_evaluation_projection as validate_evaluation_result
    from .inputs import checked_inputs
    from .deliveries import checked_combinations, delivery_alternatives
    from .eligibility import candidate_pools
    from .staff_inputs import checked_staff_inputs
    from .venue_inputs import checked_venue_inputs
    from .work_period_inputs import checked_work_period_inputs
    from .preference_inputs import checked_preference_inputs
    from .fairness_inputs import checked_fairness_inputs
    from .consecutive_inputs import checked_consecutive_inputs, consecutive_objective_plan
    from .consecutive_constraints import ModeledConsecutiveEntity
    from .consecutive_results import ConsecutiveOptimizationReport, consecutive_report, expected_consecutive_entities
    from .lexicographic import OptimizationStage

    prepared, config = checked_inputs(prepared, config)
    if staff_input is not None:
        staff_input = checked_staff_inputs(prepared, config, staff_input)
    if combination_input is not None:
        combination_input, combination_config = checked_combinations(prepared, combination_input, combination_config)
    elif combination_config is not None:
        raise SolverInputError("Combination config requires original prepared combination input.")
    if venue_input is not None:
        checked_venue_inputs(prepared, config, venue_input)
    if work_periods is not None or work_period_config is not None:
        work_periods, work_period_config = checked_work_period_inputs(prepared, config, staff_input, work_periods, work_period_config)
    preference_context = None
    if preference_input is not None or any(row is not None for row in (preference_calendar, preference_policy, preference_config)):
        preference_context = checked_preference_inputs(prepared, config, preference_input, preference_calendar,
            preference_policy, preference_config, staff_input=staff_input, work_periods=work_periods)
    fairness_context = None
    if fairness_config is not None:
        fairness_context = checked_fairness_inputs(prepared, config, fairness_policy, fairness_calendar, fairness_config,
            staff_input=staff_input, work_periods=work_periods)
    elif any(row is not None for row in (fairness_policy, fairness_calendar)):
        raise SolverInputError("Fairness policy/calendar require original explicit optimization config.")
    alternatives = delivery_alternatives(prepared, combination_input, pools=candidate_pools(prepared.input_snapshot))
    context = checked_consecutive_inputs(prepared, config, consecutive_policy, consecutive_calendar, consecutive_config,
        alternatives, staff_input=staff_input, work_periods=work_periods,
        preference_calendar=preference_calendar, fairness_calendar=fairness_calendar)
    order, _ = consecutive_objective_plan(prepared, context, fairness_context, preference_context, combination_config)
    if not isinstance(result, SolverResult) or type(result.status) is not SolverStatus or result.consecutive_optimization_enabled is not True:
        _issue("consecutive_mode_changed", "Consecutive validation requires a typed explicitly enabled solver result.")
    if (type(result.runtime_seconds) not in {int, float} or not isfinite(result.runtime_seconds) or result.runtime_seconds < 0
            or result.venue_enabled is not (venue_input is not None) or result.combination_enabled is not (combination_input is not None)
            or result.staff_policy_enabled is not (staff_input is not None) or result.work_period_enabled is not (work_periods is not None)
            or result.preference_optimization_enabled is not (preference_input is not None)
            or result.fairness_optimization_enabled is not (fairness_config is not None)
            or type(result.original_occurrence_count) is not int or result.original_occurrence_count != len(prepared.sessions)):
        _issue("consecutive_result_metadata", "Result modes, runtime or coverage differ from original inputs.")
    if result.status not in {SolverStatus.OPTIMAL, SolverStatus.FEASIBLE}:
        if (result.schedule != () or result.administrative_schedule != () or result.validation is not None
                or any(row is not None for row in (result.workload_report, result.work_period_report, result.preference_report,
                    result.fairness_report, result.consecutive_report, result.objective_value, result.objective_status))
                or type(result.failure_reason) is not str or not result.failure_reason.strip()
                or result.objective_mode != (combination_config.objective.value if combination_input is not None else None)):
            _issue("partial_consecutive_failure", "Unsuccessful optimization must not emit partial bookings, reports or objective values.")
        return None
    if result.failure_reason is not None:
        _issue("consecutive_result_metadata", "Successful result cannot have a failure reason.")
    if (result.objective_value is not None and result.objective_status != "EVALUATED_COMPONENT"
            or result.preference_report is not None and getattr(result.preference_report, "objective_status", None) != "EVALUATED_COMPONENT"
            or result.fairness_report is not None and getattr(result.fairness_report, "objective_status", None) != "EVALUATED_COMPONENT"):
        _issue("consecutive_prefix_status", "Earlier objectives must be labelled evaluated components in consecutive mode.")
    original = dict(venue_input=venue_input, combination_input=combination_input, combination_config=combination_config,
        staff_input=staff_input, work_periods=work_periods, work_period_config=work_period_config,
        preference_input=preference_input, preference_calendar=preference_calendar, preference_policy=preference_policy,
        preference_config=preference_config, fairness_policy=fairness_policy, fairness_calendar=fairness_calendar, fairness_config=fairness_config)
    projected = assessment_projection(result)
    summary = validate_evaluation_result(prepared, config, projected, **original)
    assessment = assess_consecutive_sessions(prepared, config, projected, context.calendar, context.policy, **original)
    if context.config.enforce_confirmed_hard_break and assessment.confirmed_hard_break_violation_count:
        _issue("confirmed_hard_break_violation", "Selected teaching violates the explicitly enabled confirmed hard minimum break.")
    report = result.consecutive_report
    if not isinstance(report, ConsecutiveOptimizationReport):
        _issue("missing_consecutive_report", "Successful optimization requires its independently checkable consecutive report.")
    expected = expected_consecutive_entities(result, context, assessment)
    try:
        modeled_ok = (type(report.modeled_entities) is tuple and all(type(row) is ModeledConsecutiveEntity for row in report.modeled_entities)
            and json.dumps(json_value([asdict(row) for row in report.modeled_entities]), sort_keys=True)
            == json.dumps(json_value([asdict(row) for row in expected]), sort_keys=True))
    except (AttributeError, TypeError, ValueError):
        modeled_ok = False
    if not modeled_ok:
        _issue("modeled_consecutive_values_changed", "Modeled successors, gaps, runs, attendance or penalties differ from Step 11A selected-delivery assessment.")
    stages = report.stages
    if (type(stages) is not tuple or not stages or len(stages) > len(order)
            or any(type(row) is not OptimizationStage or row.criterion != order[index] for index, row in enumerate(stages))):
        _issue("consecutive_stage_history_changed", "Stages must follow the exact configured objective order.")
    canonical = consecutive_report(prepared, result, context, assessment, expected, stages, fairness_context, preference_context, combination_config)
    components = dict(canonical.objective_components)
    for index, stage in enumerate(stages):
        if stage.status == "OPTIMAL":
            if type(stage.objective_value) is not int or stage.objective_value != components[stage.criterion]:
                _issue("consecutive_stage_history_changed", "A proven earlier objective differs from the final selected timetable.")
        elif (index != len(stages)-1 or stage.status not in {"FEASIBLE", "UNKNOWN", "NOT_RUN_TIME_LIMIT"}
                or stage.status == "FEASIBLE" and (type(stage.objective_value) is not int or stage.objective_value != components[stage.criterion])
                or stage.status != "FEASIBLE" and (stage.objective_value is not None or index == 0)):
            _issue("consecutive_stage_history_changed", "Only a final feasible/timed-out stage may stop optimization.")
    if (result.status is SolverStatus.OPTIMAL) != canonical.all_stages_proven_optimal:
        _issue("consecutive_optimality_changed", "Overall lexicographic optimality requires every configured stage to be proven optimal.")
    if fairness_context is not None:
        prefix_history = tuple(row for row in stages if row.criterion != "CONSECUTIVE_PENALTY")
        if result.fairness_report.stages != prefix_history:
            _issue("consecutive_stage_history_changed", "Fairness prefix history must match the full optimization history.")
    try:
        matches = json.dumps(json_value(asdict(report)), sort_keys=True) == json.dumps(json_value(asdict(canonical)), sort_keys=True)
    except (AttributeError, TypeError, ValueError):
        matches = False
    if not matches:
        _issue("consecutive_summary_changed", "Consecutive policy, metrics, weighted penalties, spans or objective report differs from independent recomputation.")
    return summary
