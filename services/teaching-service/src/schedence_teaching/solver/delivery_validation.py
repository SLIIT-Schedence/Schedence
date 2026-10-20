"""Canonical coverage and booking validation, independent of CP-SAT choices."""

from dataclasses import replace

from schedence_teaching.preparation import PreparationError, validate_delivery_selection
from schedence_teaching.schemas import DeliverySelection

from .deliveries import checked_combinations, delivery_alternatives, known_overlap_pairs, objective_cost
from .inputs import checked_inputs
from .results import ScheduledDelivery, ScheduledSession, ScheduleIssue, SolverResult, SolverStatus, TimetableValidationError
from .validator import _validate_timetable
from .staff_inputs import checked_staff_inputs
from .staff_validation import validate_workload_report


def validate_delivery_timetable(prepared, config, schedule, *, combination_input,
                                combination_config=None, venue_input=None, staff_input=None):
    """Reject fabricated metadata before sharing the existing time/resource checks."""
    prepared, config = checked_inputs(prepared, config)
    combinations, _ = checked_combinations(prepared, combination_input, combination_config)
    catalog = {row.delivery_id: row for row in delivery_alternatives(prepared, combinations)}
    issues, combined_ids, separate_ids, expanded = [], [], [], []
    delivery_ids, delivery_counts = {}, {}
    for item in schedule:
        if not isinstance(item, ScheduledDelivery) or type(item.delivery_id) is not str:
            issues.append(ScheduleIssue("invalid_delivery", "Combination schedules require ScheduledDelivery records."))
            continue
        alternative = catalog.get(item.delivery_id)
        if alternative is None:
            issues.append(ScheduleIssue("unknown_delivery", "Selected delivery is not a validated original or combined alternative."))
            continue
        if (type(item.is_combined) is not bool or item.is_combined != alternative.is_combined
                or type(item.combined_student_count) is not int or item.combined_student_count != alternative.student_count
                or type(item.group_ids) is not tuple or item.group_ids != alternative.group_ids
                or type(item.covered_sessions) is not tuple or item.covered_sessions != alternative.covered_sessions
                or any(type(getattr(actual, key)) is not type(value)
                       for actual, original in zip(item.covered_sessions, alternative.covered_sessions)
                       for key, value in vars(original).items())):
            issues.append(ScheduleIssue("delivery_metadata_changed", "Coverage, group IDs, capacity and original metadata must match the canonical alternative."))
            continue
        (combined_ids if alternative.is_combined else separate_ids).append(item.delivery_id)
        for session in alternative.covered_sessions:
            expanded.append(ScheduledSession(session, item.day, item.start_minute, item.end_minute,
                                             item.assigned_staff_id, item.assigned_venue_id))
            delivery_ids[session.occurrence_id] = alternative.delivery_id
            delivery_counts[session.occurrence_id] = alternative.student_count
    if issues:
        raise TimetableValidationError(issues)
    try:
        coverage = validate_delivery_selection(combinations, DeliverySelection(
            combined_occurrence_ids=combined_ids, separate_occurrence_ids=separate_ids))
    except PreparationError as error:
        raise TimetableValidationError([ScheduleIssue(row.code, row.message) for row in error.issues]) from None
    summary = _validate_timetable(prepared, config, expanded, venue_input=venue_input,
                                   delivery_ids=delivery_ids, delivery_counts=delivery_counts,
                                   overlap_pairs=known_overlap_pairs(combinations), staff_input=staff_input)
    return replace(summary, delivery_count=coverage.delivery_count, combined_deliveries=coverage.combined_deliveries,
                   separate_deliveries=coverage.separate_deliveries,
                   lecturer_contact_minutes=coverage.delivery_contact_minutes,
                   checks=summary.checks + ("eligible_combination", "disjoint_population_capacity",
                                            "exact_delivery_coverage", "known_population_clashes", "actual_delivery_workload"))


def validate_delivery_result(prepared, config, result, *, combination_input,
                             combination_config=None, venue_input=None, staff_input=None,
                             work_periods=None, work_period_config=None, preference_input=None,
                             preference_calendar=None, preference_policy=None, preference_config=None,
                             fairness_policy=None, fairness_calendar=None, fairness_config=None,
                             consecutive_policy=None, consecutive_calendar=None, consecutive_config=None,
        student_lunch_policy=None, student_lunch_calendar=None, student_lunch_config=None):
    """Also verify reported counts/workload/objective, not only schedule entries."""
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
    if not isinstance(result, SolverResult):
        raise TimetableValidationError([ScheduleIssue("invalid_result", "Expected SolverResult.")])
    combinations, options = checked_combinations(prepared, combination_input, combination_config)
    summary = validate_delivery_timetable(prepared, config, result.schedule,
                                          combination_input=combinations, combination_config=options,
                                          venue_input=venue_input, staff_input=staff_input)
    value = objective_cost(options.objective, summary.delivery_count, summary.lecturer_contact_minutes,
                           sum(row.duration_minutes for row in combinations.separate_sessions))
    component_status = "EVALUATED_COMPONENT" if result.preference_optimization_enabled else getattr(result.status, "value", None)
    if (type(result.status) is not SolverStatus or result.status not in {SolverStatus.OPTIMAL, SolverStatus.FEASIBLE}
            or result.validation != summary
            or any(type(getattr(result.validation, key)) is not type(value) for key, value in vars(summary).items())
            or type(result.combination_enabled) is not bool or not result.combination_enabled
            or type(result.original_occurrence_count) is not int
            or result.original_occurrence_count != summary.expected_occurrences
            or type(result.venue_enabled) is not bool or result.venue_enabled != (venue_input is not None)
            or result.objective_mode != options.objective.value or result.objective_value != value
            or (value is not None and type(result.objective_value) is not int)
            or result.objective_status != (component_status if value is not None else None)):
        raise TimetableValidationError([ScheduleIssue("result_summary_changed", "Result counts, workload, mode or objective do not match independently validated deliveries.")])
    if staff_input is not None:
        staff_input = checked_staff_inputs(combinations.prepared, config, staff_input)
    validate_workload_report(combinations.prepared, result, staff_input)
    from .work_period_validation import validate_work_period_outputs
    validate_work_period_outputs(combinations.prepared, config, result, staff_input=staff_input,
                                 work_periods=work_periods, work_period_config=work_period_config)
    from .preference_validation import validate_preference_outputs
    validate_preference_outputs(combinations.prepared, config, result, preference_input=preference_input,
        preference_calendar=preference_calendar, preference_policy=preference_policy, preference_config=preference_config,
        combination_config=options, staff_input=staff_input, work_periods=work_periods)
    return summary
