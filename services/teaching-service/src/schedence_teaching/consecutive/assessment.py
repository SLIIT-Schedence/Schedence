"""Independent consecutive-session assessment, retaining all original solver checks."""

from collections import Counter, defaultdict
from dataclasses import asdict, replace
import json

from pydantic import ValidationError

from schedence_teaching.preferences.contracts import PreferenceCalendar
from schedence_teaching.preferences.scoring import _calendar
from schedence_teaching.solver.evaluation import _validate_evaluation_projection as validate_evaluation_result
from schedence_teaching.solver.inputs import checked_inputs
from schedence_teaching.solver.results import ScheduledDelivery
from schedence_teaching.workload.metrics import teaching_metrics

from .contracts import AttendanceKind, ConsecutivePolicy
from .metrics import delivery_id, detect_consecutive_sequences, missing_scoring_configuration, score_sequences, scoring_requested
from .results import ConsecutiveReport, EntityConsecutiveAssessment, json_value


class ConsecutiveInputError(ValueError):
    pass


def _snapshot(value, model):
    if not isinstance(value, model):
        raise ConsecutiveInputError(f"Expected {model.__name__}.")
    try:
        return model.model_validate(value.model_dump(round_trip=True))
    except ValidationError as error:
        raise ConsecutiveInputError("; ".join(row["msg"] for row in error.errors(
            include_input=False, include_context=False, include_url=False))) from None


def _entity(kind, identity, direct, inherited, calendar, policy):
    deliveries = {**inherited, **direct}
    inherited_ids = inherited.keys() - direct.keys()
    hard_applies = policy.confirmed_hard_minimum_break is not None and kind in policy.confirmed_hard_minimum_break.applies_to
    sequences, transitions = detect_consecutive_sequences(deliveries.values(), calendar, policy,
        apply_confirmed_hard_break=hard_applies)
    scoring = policy.score_lecturers if kind is AttendanceKind.LECTURER else policy.score_student_groups
    components, known, total, complete = score_sequences(sequences, policy, scoring_enabled=scoring)
    gaps = Counter(gap.gap_minutes for row in sequences for gap in row.gaps)
    violations = sum(len(row.confirmed_hard_break_violations) for row in sequences)
    diagnostics = list(missing_scoring_configuration(policy)) if scoring and scoring_requested(policy) else []
    if transitions:
        diagnostics.append("stream_transitions_not_compared")
    if not deliveries:
        diagnostics.append("no_scheduled_teaching")
    if hard_applies:
        diagnostics.append("confirmed_hard_break_assessed_not_enforced_by_existing_solver")
    maximum_continuous = max((row.maximum_continuous_minutes for row in sequences), default=0)
    # Reuse physical totals/daily distribution, retaining this assessment's
    # same-stream definition for the shared consecutive-duration descriptor.
    teaching = replace(teaching_metrics(deliveries.values(), calendar.day_dates),
        longest_consecutive_teaching_minutes=maximum_continuous)
    return EntityConsecutiveAssessment(kind.value, identity, scoring,
        teaching, tuple(sorted(direct)), tuple(sorted(inherited_ids)),
        sequences, sum(row.back_to_back_pairs for row in sequences), sum(row.back_to_back_lecture_pairs for row in sequences),
        max((row.maximum_consecutive_sessions for row in sequences), default=0),
        maximum_continuous, tuple(sorted(gaps.items())), transitions,
        components, known, total, complete, violations, violations == 0 if hard_applies else None, tuple(diagnostics))


def assess_consecutive_sessions(prepared, config, result, calendar: PreferenceCalendar, policy: ConsecutivePolicy, *,
        venue_input=None, combination_input=None, combination_config=None, staff_input=None,
        work_periods=None, work_period_config=None, preference_input=None, preference_calendar=None,
        preference_policy=None, preference_config=None, fairness_policy=None, fairness_calendar=None, fairness_config=None,
        consecutive_policy=None, consecutive_calendar=None, consecutive_config=None,
        student_lunch_policy=None, student_lunch_calendar=None, student_lunch_config=None):
    """Evaluate complete validated selected bookings without changing any solver behavior.

    Each subgroup attends its direct requirements and ancestor-group bookings.
    A parent does not inherit all child lab bookings; sibling attendance or
    unknown cross-group membership is never invented. Combined events are
    deduplicated by physical delivery ID for every lecturer/group independently.
    """
    prepared, config = checked_inputs(prepared, config)
    validation = validate_evaluation_result(prepared, config, result, venue_input=venue_input,
        combination_input=combination_input, combination_config=combination_config,
        staff_input=staff_input, work_periods=work_periods, work_period_config=work_period_config,
        preference_input=preference_input, preference_calendar=preference_calendar,
        preference_policy=preference_policy, preference_config=preference_config,
        fairness_policy=fairness_policy, fairness_calendar=fairness_calendar, fairness_config=fairness_config,
        consecutive_policy=consecutive_policy, consecutive_calendar=consecutive_calendar, consecutive_config=consecutive_config,
            student_lunch_policy=student_lunch_policy, student_lunch_calendar=student_lunch_calendar, student_lunch_config=student_lunch_config)
    policy = _snapshot(policy, ConsecutivePolicy)
    from schedence_teaching.policy.resolution import verify_component
    verify_component(config, 'consecutive_policy', policy)
    calendar = _calendar(config, _snapshot(calendar, PreferenceCalendar), staff_input, work_periods)
    if policy.week_reference != calendar.week_reference:
        raise ConsecutiveInputError("Consecutive policy and calendar must reference the same applicable academic week.")
    if any(other is not None and calendar != other for other in (preference_calendar, fairness_calendar)):
        raise ConsecutiveInputError("Consecutive, preference and fairness calendars must agree.")
    lecturers, direct, inherited = defaultdict(dict), defaultdict(dict), defaultdict(dict)
    for row in result.schedule:
        identity = delivery_id(row)
        lecturers[row.assigned_staff_id][identity] = row
        groups = row.group_ids if isinstance(row, ScheduledDelivery) else (row.session.group_id,)
        for group in groups:
            direct[group][identity] = row
            for descendant in prepared.hierarchy.descendants(group):
                inherited[descendant][identity] = row
    staff_rows = tuple(_entity(AttendanceKind.LECTURER, row.staff_id, lecturers[row.staff_id], {}, calendar, policy)
                       for row in sorted(prepared.input_snapshot.staff_members, key=lambda row: row.staff_id))
    group_rows = tuple(_entity(AttendanceKind.STUDENT_GROUP, group, direct[group], inherited[group], calendar, policy)
                       for group in sorted(prepared.hierarchy.parents))
    diagnostics = list(missing_scoring_configuration(policy))
    if policy.preferred_minimum_break_minutes is None:
        diagnostics.append("preferred_minimum_break_not_supplied")
    if policy.maximum_preferred_continuous_minutes is None:
        diagnostics.append("maximum_preferred_continuous_duration_not_supplied")
    if not scoring_requested(policy):
        diagnostics.append("descriptive_only_no_soft_criteria_selected")
    if not policy.score_lecturers and not policy.score_student_groups:
        diagnostics.append("soft_scoring_disabled_for_all_entities")
    active = scoring_requested(policy) and (policy.score_lecturers or policy.score_student_groups)
    known = sum(row.known_soft_penalty for row in (*staff_rows, *group_rows))
    complete = not missing_scoring_configuration(policy) if active else None
    violations = sum(row.confirmed_hard_break_violation_count for row in (*staff_rows, *group_rows))
    if policy.confirmed_hard_minimum_break is not None:
        diagnostics.append("confirmed_hard_break_assessed_not_enforced_by_existing_solver")
    payload = policy.model_dump(mode="json")
    payload["soft_criteria"] = sorted(payload["soft_criteria"])
    if payload["confirmed_hard_minimum_break"] is not None:
        payload["confirmed_hard_minimum_break"]["applies_to"] = sorted(payload["confirmed_hard_minimum_break"]["applies_to"])
    report = ConsecutiveReport(json.dumps(payload, sort_keys=True), json.dumps(calendar.model_dump(mode="json"), sort_keys=True),
        len(result.schedule), sum(row.end_minute - row.start_minute for row in result.schedule), staff_rows, group_rows,
        known, known if complete else None, complete, violations,
        violations == 0 if policy.confirmed_hard_minimum_break is not None else None,
        tuple(validation.checks), tuple(dict.fromkeys(diagnostics)))
    if result.consecutive_optimization_enabled:
        from schedence_teaching.solver.consecutive_results import optimized_assessment
        return optimized_assessment(report, consecutive_config.enforce_confirmed_hard_break and policy == consecutive_policy)
    return report


def validate_consecutive_report(report, *args, **kwargs):
    """Regenerate from original contexts; no cached workload or report totals are trusted."""
    expected = assess_consecutive_sessions(*args, **kwargs)
    try:
        matches = (type(report) is ConsecutiveReport
            and json.dumps(json_value(asdict(report)), sort_keys=True) == json.dumps(json_value(asdict(expected)), sort_keys=True))
    except (AttributeError, TypeError, ValueError):
        matches = False
    if not matches:
        raise ConsecutiveInputError("Consecutive report differs from independently recomputed bookings, attendance or policy.")
    return expected
