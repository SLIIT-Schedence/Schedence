"""Independent source-minute recomputation of optional fairness optimization."""

from dataclasses import replace
import json
from math import isfinite

from .results import ScheduleIssue, SolverResult, SolverStatus, TimetableValidationError


def fairness_validation_requested(result, policy, calendar, config):
    return (getattr(result, "fairness_optimization_enabled", False) is not False
        or getattr(result, "fairness_report", None) is not None
        or any(row is not None for row in (policy, calendar, config)))


def assessment_projection(result):
    """Private validation view for prior component validators, not a public result.

    Older validators check component status labels against the overall status.
    Their existing arithmetic is reused here; the public fairness result keeps
    those components explicitly EVALUATED_COMPONENT instead of claiming that
    their old composite objective was the final optimized objective.
    """
    projected = replace(result, fairness_optimization_enabled=False, fairness_report=None)
    if projected.combination_enabled and projected.objective_value is not None:
        projected = replace(projected, objective_status="EVALUATED_COMPONENT" if projected.preference_optimization_enabled else projected.status.value)
    if projected.preference_report is not None:
        projected = replace(projected, preference_report=replace(projected.preference_report, objective_status=projected.status.value))
    return projected


def _issue(code, message):
    raise TimetableValidationError([ScheduleIssue(code, message)])


def validate_fairness_result(prepared, config, result, *, fairness_policy, fairness_calendar, fairness_config,
        venue_input=None, combination_input=None, combination_config=None, staff_input=None,
        work_periods=None, work_period_config=None, preference_input=None, preference_calendar=None,
        preference_policy=None, preference_config=None,
        consecutive_policy=None, consecutive_calendar=None, consecutive_config=None,
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
    from .consecutive_validation import consecutive_validation_requested, validate_consecutive_result
    if consecutive_validation_requested(result, consecutive_policy, consecutive_calendar, consecutive_config):
        return validate_consecutive_result(prepared, config, result, consecutive_policy=consecutive_policy,
            consecutive_calendar=consecutive_calendar, consecutive_config=consecutive_config,
            venue_input=venue_input, combination_input=combination_input, combination_config=combination_config,
            staff_input=staff_input, work_periods=work_periods, work_period_config=work_period_config,
            preference_input=preference_input, preference_calendar=preference_calendar,
            preference_policy=preference_policy, preference_config=preference_config,
            fairness_policy=fairness_policy, fairness_calendar=fairness_calendar, fairness_config=fairness_config)
    from schedence_teaching.fairness import assess_fairness
    from schedence_teaching.fairness.results import _json
    from .evaluation import _validate_evaluation_projection as validate_evaluation_result
    from .inputs import checked_inputs
    from .staff_inputs import checked_staff_inputs
    from .venue_inputs import checked_venue_inputs
    from .deliveries import checked_combinations
    from .work_period_inputs import checked_work_period_inputs
    from .preference_inputs import checked_preference_inputs
    from .fairness_inputs import checked_fairness_inputs, fairness_objective_plan
    from .fairness_constraints import ModeledFairnessWorkload
    from .fairness_results import FairnessOptimizationReport, fairness_report
    from .lexicographic import OptimizationStage

    prepared, config = checked_inputs(prepared, config)
    if staff_input is not None:
        staff_input = checked_staff_inputs(prepared, config, staff_input)
    if combination_input is not None:
        combination_input, combination_config = checked_combinations(prepared, combination_input, combination_config)
    elif combination_config is not None:
        from .results import SolverInputError
        raise SolverInputError("Combination config requires original prepared combination input.")
    if venue_input is not None:
        checked_venue_inputs(prepared, config, venue_input)
    if work_periods is not None or work_period_config is not None:
        work_periods, work_period_config = checked_work_period_inputs(prepared, config, staff_input, work_periods, work_period_config)
    context = checked_fairness_inputs(prepared, config, fairness_policy, fairness_calendar, fairness_config,
        staff_input=staff_input, work_periods=work_periods)
    preference_context = None
    if preference_input is not None or any(row is not None for row in (preference_calendar, preference_policy, preference_config)):
        preference_context = checked_preference_inputs(prepared, config, preference_input, preference_calendar,
            preference_policy, preference_config, staff_input=staff_input, work_periods=work_periods)
        if context.calendar != preference_context.calendar:
            _issue("fairness_week_changed", "Fairness and preference optimization calendars differ.")
    stage_plan = getattr(config, "objective_stage_plan", None)
    order, _ = fairness_objective_plan(prepared, context, preference_context, combination_config, stage_plan=stage_plan)
    if not isinstance(result, SolverResult) or type(result.status) is not SolverStatus or result.fairness_optimization_enabled is not True:
        _issue("fairness_mode_changed", "Fairness validation requires a typed, explicitly enabled result.")
    if (type(result.runtime_seconds) not in {int, float} or not isfinite(result.runtime_seconds) or result.runtime_seconds < 0
            or result.venue_enabled is not (venue_input is not None)
            or result.combination_enabled is not (combination_input is not None)
            or result.staff_policy_enabled is not True or result.work_period_enabled is not (work_periods is not None)
            or result.preference_optimization_enabled is not (preference_input is not None)
            or type(result.original_occurrence_count) is not int or result.original_occurrence_count != len(prepared.sessions)):
        _issue("fairness_result_metadata", "Fairness result modes, coverage or runtime differ from original inputs.")
    if result.status not in {SolverStatus.OPTIMAL, SolverStatus.FEASIBLE}:
        if (result.schedule != () or result.administrative_schedule != () or result.validation is not None
                or any(row is not None for row in (result.workload_report, result.work_period_report,
                    result.preference_report, result.fairness_report, result.objective_value, result.objective_status))
                or type(result.failure_reason) is not str or not result.failure_reason.strip()
                or result.objective_mode != (combination_config.objective.value if combination_input is not None else None)):
            _issue("partial_fairness_failure", "Unsuccessful fairness optimization must not emit partial bookings, scores or objective values.")
        return None
    if result.failure_reason is not None:
        _issue("fairness_result_metadata", "Successful fairness result cannot contain a failure reason.")
    if result.preference_report is not None:
        from .preference_results import PreferenceOptimizationReport
        if not isinstance(result.preference_report, PreferenceOptimizationReport):
            _issue("fairness_preference_report", "Expected typed original preference optimization report.")
    if result.objective_value is not None and result.objective_status != "EVALUATED_COMPONENT":
        _issue("fairness_component_status", "Legacy teaching objective is an evaluated component in fairness mode.")
    if result.preference_report is not None and result.preference_report.objective_status != "EVALUATED_COMPONENT":
        _issue("fairness_component_status", "Preference objective is an evaluated prefix component in fairness mode.")
    original = dict(venue_input=venue_input, combination_input=combination_input, combination_config=combination_config,
        staff_input=staff_input, work_periods=work_periods, work_period_config=work_period_config,
        preference_input=preference_input, preference_calendar=preference_calendar,
        preference_policy=preference_policy, preference_config=preference_config)
    projected = assessment_projection(result)
    summary = validate_evaluation_result(prepared, config, projected, **original)
    assessment = assess_fairness(prepared, config, projected, context.calendar, context.policy, **original)
    report = result.fairness_report
    if not isinstance(report, FairnessOptimizationReport):
        _issue("missing_fairness_report", "Successful fairness optimization requires an independently checkable report.")
    actual = {row.staff_id: row.teaching.scheduled_teaching_minutes for row in assessment.staff}
    expected_modeled = tuple(ModeledFairnessWorkload(identity, actual[identity],
        actual[identity] * (context.normalization_scale // target),
        abs(actual[identity] * (context.normalization_scale // target) - context.normalization_scale))
        for identity, target in context.targets)
    try:
        valid_modeled = (type(report.modeled_workloads) is tuple and all(type(row) is ModeledFairnessWorkload for row in report.modeled_workloads)
            and json.dumps(_json(report.modeled_workloads), sort_keys=True) == json.dumps(_json(expected_modeled), sort_keys=True))
    except (AttributeError, TypeError, ValueError):
        valid_modeled = False
    if not valid_modeled:
        _issue("fairness_workload_changed", "Modeled teaching minutes, utilization or absolute target deviation differ from actual selected bookings.")
    stages = report.stages
    if (type(stages) is not tuple or not stages or len(stages) > len(order)
            or any(type(row) is not OptimizationStage or row.criterion != order[index] for index, row in enumerate(stages))):
        _issue("fairness_stage_history_changed", "Fairness stages must follow the original objective order.")
    canonical = fairness_report(prepared, result, context, assessment, expected_modeled, stages, preference_context, combination_config,
                                stage_plan=stage_plan)
    components = dict(canonical.objective_components)
    for index, stage in enumerate(stages):
        if stage.status == "OPTIMAL":
            if type(stage.objective_value) is not int or stage.objective_value != components[stage.criterion]:
                _issue("fairness_stage_history_changed", "A proven earlier objective was not preserved in the final selected timetable.")
        elif (index != len(stages) - 1 or stage.status not in {"FEASIBLE", "UNKNOWN", "NOT_RUN_TIME_LIMIT"}
                or (stage.status == "FEASIBLE" and (type(stage.objective_value) is not int or stage.objective_value != components[stage.criterion]))
                or (stage.status != "FEASIBLE" and (stage.objective_value is not None or index == 0))):
            _issue("fairness_stage_history_changed", "Only a final feasible/timed-out stage may stop lexicographic progress.")
    # Opt-in P2.1/P2.2 stages may follow proven fairness stages; only they may then leave the result FEASIBLE.
    # P2.1/P2.2 or P3.1 stages may follow proven fairness stages; only they may then leave the result FEASIBLE.
    later_quality = (getattr(config, "teaching_day_compactness", None) is not None
                     or getattr(config, "daily_load_distribution", None) is not None or stage_plan is not None)
    if ((result.status is SolverStatus.OPTIMAL) != canonical.all_stages_proven_optimal
            and not (later_quality and result.status is SolverStatus.FEASIBLE and canonical.all_stages_proven_optimal)):
        _issue("fairness_optimality_changed", "Overall lexicographic optimality requires all objective stages to be proven optimal.")
    try:
        matches = json.dumps(_json(report), sort_keys=True) == json.dumps(_json(canonical), sort_keys=True)
    except (AttributeError, TypeError, ValueError):
        matches = False
    if not matches:
        _issue("fairness_summary_changed", "Fairness cohorts, policy, targets, metrics, scales or objective vector differ from independent recomputation.")
    return summary
