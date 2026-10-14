"""Independent fairness evaluation; no optimization, allocation or policy inference."""

from collections import defaultdict
from fractions import Fraction

from pydantic import ValidationError

from schedence_teaching.preferences.contracts import PreferenceCalendar
from schedence_teaching.preferences.scoring import _calendar
from schedence_teaching.schemas import StaffType
from schedence_teaching.solver.eligibility import candidate_pools
from schedence_teaching.solver.evaluation import _validate_evaluation_projection as validate_evaluation_result
from schedence_teaching.solver.inputs import checked_inputs
from schedence_teaching.solver.staff_constraints import staff_start_domains
from schedence_teaching.solver.staff_inputs import checked_staff_inputs
from schedence_teaching.workload.assessment import (
    WorkloadInputError, _assess_validated_schedule, resolve_work_policy,
)
from schedence_teaching.workload.contracts import RecognizedWorkType, WeeklyWorkloadInput, WorkloadBand
from schedence_teaching.workload.metrics import teaching_metrics
from schedence_teaching.workload.period_contracts import AdministrativeAggregateStatus, PeriodProtection

from .contracts import (
    BalanceMetric, ComparisonGroup, ComparisonScope, FairnessPolicy, MissingEvidenceBehavior, Normalization,
)
from .metrics import distribution, teaching_target_metrics
from .results import AssignmentOpportunities, FairnessReport, GroupFairnessAssessment, StaffFairnessAssessment


class FairnessInputError(ValueError):
    pass


def _snapshot(value, model):
    if not isinstance(value, model):
        raise FairnessInputError(f"Expected {model.__name__}.")
    try:
        return model.model_validate(value.model_dump(round_trip=True))
    except ValidationError as error:
        raise FairnessInputError("; ".join(row["msg"] for row in error.errors(
            include_input=False, include_context=False, include_url=False))) from None


def _workload(prepared, source, staff_input):
    if source is None:
        source = staff_input.workload_input if staff_input is not None else None
    if source is None:
        return None, {}, {}
    source = _snapshot(source, WeeklyWorkloadInput)
    if staff_input is not None and source != staff_input.workload_input:
        raise FairnessInputError("Fairness requires the original scheduling workload input.")
    people = {row.staff_id: row for row in prepared.input_snapshot.staff_members}
    referenced = ({row.staff_id for row in source.profiles} | {row.staff_id for row in source.recognized_work}
        | {row.staff_id for row in source.attendance} | {row.staff_id for row in source.administrative_reservations}
        | source.recognized_work_complete_for)
    if referenced - people.keys():
        raise FairnessInputError("Workload evidence references unknown staff IDs.")
    resolved, unresolved = {}, {}
    for profile in source.profiles:
        if (people[profile.staff_id].staff_type is StaffType.INSTRUCTOR) != (profile.workload_band is WorkloadBand.INSTRUCTOR):
            raise FairnessInputError("Workload classification must preserve the academic/instructor distinction.")
        try:
            resolved[profile.staff_id] = resolve_work_policy(people[profile.staff_id], profile, source.policy)
        except WorkloadInputError:
            # Report an unresolved explicit override, never fall back to the rank.
            unresolved[profile.staff_id] = "unresolved_workload_classification_or_override"
    return source, resolved, unresolved


def _appointment_evidence(identity, periods, calendar):
    """Administrative aggregation never establishes a competing teaching override."""
    if periods is None:
        return ()
    source = periods.input_snapshot
    missing = set()
    from datetime import timedelta
    days = (calendar.week_start + timedelta(days=index)
            for index in range((calendar.week_end - calendar.week_start).days + 1))
    for day in days:
        active = [row for row in source.appointments if row.staff_id == identity
            and row.effective_from <= day and (row.effective_until is None or day <= row.effective_until)]
        if any(not (row.policy_effective_from <= day and
                (row.policy_effective_until is None or day <= row.policy_effective_until)) for row in active):
            missing.add("appointment_policy_not_applicable")
        if len(active) > 1:
            missing.add("multiple_appointments_teaching_override_requires_reconciliation")
            resolutions = [row for row in source.administrative_resolutions if row.staff_id == identity
                and row.effective_from <= day and (row.effective_until is None or day <= row.effective_until)]
            if (len(resolutions) != 1 or resolutions[0].status is not AdministrativeAggregateStatus.COMPUTED
                    or set(resolutions[0].appointment_ids) != {row.appointment_id for row in active}):
                missing.add("unresolved_administrative_appointment_aggregation")
    return tuple(sorted(missing))


def _fits(session, availability, config, calendar, protected):
    """Single-occurrence fit only; no residual timetable, venue or student claim.

    Reuse the solver's staff calendar intersection, then subtract protected start
    ranges in integer ticks. Do not enumerate minutes or run another optimizer.
    """
    if any(cap is not None and cap < session.duration_minutes for cap in
           (availability.daily_teaching_max_minutes, availability.weekly_teaching_max_minutes)):
        return False
    granularity = config.granularity_minutes
    stride = (1440 + granularity - 1) // granularity
    for first, last in staff_start_domains(session, availability, config, calendar.day_dates):
        day_index = first // stride
        on_date = calendar.day_dates[config.days[day_index].day]
        base = day_index * stride
        remaining = [(first - base, last - base)]
        for block_date, start, end in protected:
            if block_date != on_date:
                continue
            # A start s overlaps iff s < end and s + duration > start.
            lower = (start - session.duration_minutes) // granularity + 1
            upper = (end + granularity - 1) // granularity - 1
            remaining = [(left, right) for a, b in remaining
                for left, right in ((a, min(b, lower - 1)), (max(a, upper + 1), b)) if left <= right]
        if remaining:
            return True
    return False


def _opportunities(identity, prepared, config, calendar, pools, scope, availability, periods, result):
    sessions = tuple(row for row in prepared.sessions if row.offering_id in scope and identity in pools[row.offering_id])
    fits = None
    evidence_scope = "TEACHING_TEAM_MEMBERSHIP_ONLY"
    if availability is not None:
        protected = []
        if periods is not None:
            protected.extend((row.on_date, row.start_minute, row.end_minute)
                for row in periods.protected_intervals if row.staff_id == identity)
            protected.extend((row.on_date, row.start_minute, row.end_minute)
                for row in (result.administrative_schedule if result is not None else ())
                if row.staff_id == identity and row.protection is PeriodProtection.HARD)
        fits = tuple(sorted(row.occurrence_id for row in sessions if
            _fits(row, availability, config, calendar, protected)))
        evidence_scope = ("STAFF_CALENDAR_AND_SELECTED_HARD_PROTECTIONS_SINGLE_OCCURRENCE" if periods is not None
                          else "STAFF_CALENDAR_SINGLE_OCCURRENCE_PROTECTIONS_NOT_SUPPLIED")
        if periods is not None and result is None:
            evidence_scope = "STAFF_CALENDAR_AND_FIXED_HARD_PROTECTIONS_SINGLE_OCCURRENCE"
    return AssignmentOpportunities(tuple(sorted({row.offering_id for row in sessions})),
        tuple(sorted({row.requirement_id for row in sessions})),
        tuple(sorted(row.occurrence_id for row in sessions)), fits, evidence_scope)


def _groups(policy, opportunities):
    if policy.comparison_scope is ComparisonScope.EXPLICIT_GROUPS:
        return policy.comparison_groups
    buckets = defaultdict(list)
    for identity, row in opportunities.items():
        if row.eligible_offering_ids:
            buckets[row.eligible_offering_ids].append(identity)
    return [ComparisonGroup(group_id=f"eligibility-pool:{index}", staff_ids=tuple(sorted(ids)),
                reason="Identical explicit offering eligibility in the supplied demand scope; not proof of equal availability.")
            for index, (_, ids) in enumerate(sorted(buckets.items()), 1)]


def _group_metrics(group, staff, policy):
    members = [staff[identity] for identity in sorted(group.staff_ids)]
    included = [row for row in members if row.included_in_comparison]
    values = {row.staff_id: (row.target.utilization if policy.normalization is Normalization.TEACHING_TARGET
                            else Fraction(row.teaching.scheduled_teaching_minutes)) for row in included}
    weights = {identity: policy.staff_weights.get(identity, Fraction(1)) for identity in values}
    mean, variance, mad, maximum, relative = distribution(values, weights)
    balance = {BalanceMetric.MAXIMUM_DEVIATION: maximum, BalanceMetric.MEAN_ABSOLUTE_DEVIATION: mad,
               BalanceMetric.VARIANCE: variance}[policy.balance_metric]
    flagged = tuple(identity for identity, value in values.items() if policy.relative_deviation_threshold is not None
                    and mean and abs(value - mean) / mean > policy.relative_deviation_threshold)
    diagnostics = []
    if len(included) < 2:
        diagnostics.append("fewer_than_two_comparable_staff")
    if mean == 0:
        diagnostics.append("relative_deviation_undefined_for_zero_mean")
    return GroupFairnessAssessment(group.group_id, group.reason, tuple(sorted(group.staff_ids)), tuple(values),
        tuple((row.staff_id, row.comparison_reasons) for row in members if not row.included_in_comparison),
        tuple(values.items()), tuple(weights.items()), mean, variance if policy.include_variance else None, mad,
        maximum if policy.include_maximum_deviation else None, relative, balance, flagged, tuple(diagnostics))


def assess_fairness(prepared, config, result, calendar: PreferenceCalendar, policy: FairnessPolicy, *,
        workload_input: WeeklyWorkloadInput | None = None, venue_input=None,
        combination_input=None, combination_config=None, staff_input=None,
        work_periods=None, work_period_config=None, preference_input=None,
        preference_calendar=None, preference_policy=None, preference_config=None,
        fairness_policy=None, fairness_calendar=None, fairness_config=None,
        consecutive_policy=None, consecutive_calendar=None, consecutive_config=None,
        student_lunch_policy=None, student_lunch_calendar=None, student_lunch_config=None) -> FairnessReport:
    """Revalidate a complete selected timetable and independently recompute metrics.

    Original enabled solver contexts are mandatory. Explicit fairness inclusion
    cannot override eligibility, missing targets or unavailable calendar evidence.
    Above-target diagnostics never become hard caps or proof of unfairness.
    """
    prepared, config = checked_inputs(prepared, config)
    if staff_input is not None:
        staff_input = checked_staff_inputs(prepared, config, staff_input)
    validation = validate_evaluation_result(prepared, config, result, venue_input=venue_input,
        combination_input=combination_input, combination_config=combination_config,
        staff_input=staff_input, work_periods=work_periods, work_period_config=work_period_config,
        preference_input=preference_input, preference_calendar=preference_calendar,
        preference_policy=preference_policy, preference_config=preference_config,
        fairness_policy=fairness_policy, fairness_calendar=fairness_calendar, fairness_config=fairness_config,
        consecutive_policy=consecutive_policy, consecutive_calendar=consecutive_calendar, consecutive_config=consecutive_config,
            student_lunch_policy=student_lunch_policy, student_lunch_calendar=student_lunch_calendar, student_lunch_config=student_lunch_config)
    policy = _snapshot(policy, FairnessPolicy)
    from schedence_teaching.policy.resolution import verify_component
    verify_component(config, 'fairness_policy', policy)
    calendar = _calendar(config, _snapshot(calendar, PreferenceCalendar), staff_input, work_periods)
    if policy.week_reference != calendar.week_reference:
        raise FairnessInputError("Fairness policy and calendar must reference the same academic week.")
    if preference_calendar is not None and calendar != preference_calendar:
        raise FairnessInputError("Fairness and preference assessment must use the same academic week calendar.")
    people = {row.staff_id: row for row in prepared.input_snapshot.staff_members}
    offerings = {row.offering_id for row in prepared.input_snapshot.module_offerings}
    referenced = ({identity for group in policy.comparison_groups for identity in group.staff_ids}
                  | {row.staff_id for row in policy.staff_decisions} | policy.staff_weights.keys())
    if referenced - people.keys():
        raise FairnessInputError("Fairness policy references unknown staff IDs.")
    scope = policy.eligibility_offering_ids if policy.eligibility_offering_ids is not None else offerings
    if scope - offerings:
        raise FairnessInputError("Fairness eligibility scope references unknown offering IDs.")
    workload, resolved, unresolved = _workload(prepared, workload_input, staff_input)
    if workload is not None and workload.week_reference != calendar.week_reference:
        raise FairnessInputError("Workload evidence and fairness calendar must reference the same academic week.")
    # Reuse the existing assessment for separations and evidence; never read caller totals.
    workload_assessments = {}
    if workload is not None:
        partial = workload.model_copy(update={
            "profiles": [row for row in workload.profiles if row.staff_id in resolved],
            "administrative_reservations": [row for row in workload.administrative_reservations if row.staff_id in resolved]})
        assessed = _assess_validated_schedule(prepared, result, partial)
        workload_assessments = {row.policy.staff_id: row for row in assessed.staff}
    profiles = {row.staff_id: row for row in workload.profiles} if workload else {}
    availability = {row.staff_id: row for row in staff_input.availability} if staff_input else {}
    pools = candidate_pools(prepared.input_snapshot)
    opportunities = {identity: _opportunities(identity, prepared, config, calendar, pools, scope,
        availability.get(identity), work_periods, result) for identity in people}
    groups = _groups(policy, opportunities)
    compared = {identity for group in groups for identity in group.staff_ids}
    decisions = {row.staff_id: row for row in policy.staff_decisions}
    selected = defaultdict(list)
    for row in result.schedule:
        selected[row.assigned_staff_id].append(row)
    staff = []
    for identity in sorted(people):
        metrics = teaching_metrics(selected[identity], calendar.day_dates)
        resolved_policy, profile = resolved.get(identity), profiles.get(identity)
        target = teaching_target_metrics(metrics.scheduled_teaching_minutes,
            resolved_policy.required_teaching_minutes if resolved_policy else None)
        assessment, available = workload_assessments.get(identity), availability.get(identity)
        missing, blocking, reasons = [], [], []
        if profile is None:
            blocking.append("missing_authoritative_workload_classification")
        if identity in unresolved:
            blocking.append(unresolved[identity])
        if target.target_minutes is None:
            blocking.append("missing_separate_teaching_target")
        elif target.target_minutes == 0:
            blocking.append("zero_target_not_normalizable")
        blocking.extend(_appointment_evidence(identity, work_periods, calendar))
        if available is None:
            missing.append("staff_availability_not_supplied")
            if policy.require_availability_evidence:
                blocking.append("staff_availability_required_for_comparison")
        if work_periods is None:
            missing.append("administrative_research_protection_evidence_not_supplied")
        if assessment is not None:
            missing.extend(assessment.missing_evidence)
        else:
            missing.extend(("administrative_allocation_not_resolved", "campus_attendance_policy_not_resolved"))
        missing.extend(blocking)
        if identity not in compared:
            reasons.append("outside_comparison_groups")
        decision = decisions.get(identity)
        if decision is not None and not decision.include:
            reasons.append(f"explicit_exclusion: {decision.reason}")
        opportunity = opportunities[identity]
        if not opportunity.eligible_occurrence_ids:
            reasons.append("no_eligible_demand_in_scope")
        elif opportunity.individually_available_occurrence_ids == ():
            reasons.append("no_individual_fit_under_supplied_hard_restrictions")
        if (blocking and policy.missing_evidence_behavior is MissingEvidenceBehavior.ERROR
                and identity in compared and not (decision is not None and not decision.include)):
            raise FairnessInputError("Missing fairness comparison evidence: " + ", ".join(sorted(set(blocking))))
        reasons.extend(blocking)
        include = not reasons
        if include:
            reasons.append(f"explicit_inclusion: {decision.reason}" if decision is not None else "eligible_under_configured_comparison_scope")
        reserved = research = None
        if work_periods is not None:
            reserved = sum(row.end_minute - row.start_minute for row in work_periods.protected_intervals
                if row.staff_id == identity and row.kind == "ADMINISTRATIVE")
            reserved += sum(row.end_minute - row.start_minute for row in result.administrative_schedule if row.staff_id == identity)
            research = sum(row.end_minute - row.start_minute for row in work_periods.protected_intervals
                if row.staff_id == identity and row.kind == "RESEARCH")
        credited_admin = sum(row.duration_minutes for row in workload.recognized_work
            if row.staff_id == identity and row.work_type is RecognizedWorkType.ADMINISTRATIVE) if workload else 0
        attendance = next((row for row in workload.attendance if row.staff_id == identity), None) if workload else None
        staff.append(StaffFairnessAssessment(identity, profile.workload_band.value if profile else None,
            profile.appointment.value if profile and profile.appointment else None,
            resolved_policy.policy_reference if resolved_policy else None,
            resolved_policy.rule_source if resolved_policy else None, metrics, target, opportunity, include,
            tuple(dict.fromkeys(reasons)), tuple(sorted(set(missing))),
            available.weekly_teaching_max_minutes if available else None,
            available.daily_teaching_max_minutes if available else None,
            resolved_policy.administrative_allocation_minutes if resolved_policy else None,
            credited_admin, identity in workload.recognized_work_complete_for if workload else False,
            reserved, research, tuple(sorted(attendance.working_days)) if attendance else (),
            attendance.complete if attendance else False,
            resolved_policy.compulsory_campus_days if resolved_policy else None,
            resolved_policy.campus_day_exempt if resolved_policy else None))
    indexed = {row.staff_id: row for row in staff}
    return FairnessReport(policy.model_dump_json(), calendar.model_dump_json(),
        workload.policy.policy_reference if workload else None, tuple(staff),
        tuple(_group_metrics(group, indexed, policy) for group in groups), len(result.schedule),
        sum(row.teaching.scheduled_teaching_minutes for row in staff), tuple(validation.checks),
        staff_input is not None, work_periods is not None, result.preference_optimization_enabled,
        result.fairness_optimization_enabled)


def validate_fairness_report(report, *args, **kwargs):
    """Reject tampering by regenerating from original contracts and selected bookings."""
    expected = assess_fairness(*args, **kwargs)
    import json
    from .results import _json
    if (not isinstance(report, FairnessReport)
            or json.dumps(_json(report), sort_keys=True) != json.dumps(_json(expected), sort_keys=True)):
        raise FairnessInputError("Fairness report differs from independently recomputed selected deliveries and policy.")
    return expected
