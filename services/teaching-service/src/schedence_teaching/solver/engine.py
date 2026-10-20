"""One global CP-SAT interval model for all prepared stream demands."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from time import perf_counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from schedence_teaching.preferences import PreparedLecturerPreferences, PreferenceCalendar, PreferenceScoringPolicy
    from .preference_inputs import PreferenceOptimizationConfig
    from .fairness_inputs import FairnessOptimizationConfig
    from schedence_teaching.fairness import FairnessPolicy
    from schedence_teaching.consecutive import ConsecutivePolicy
    from .consecutive_inputs import ConsecutiveOptimizationConfig
    from schedence_teaching.lunch import StudentLunchPolicy
    from .lunch_inputs import StudentLunchEnforcementConfig

from ortools.sat.python import cp_model

from schedence_teaching.preparation import PreparedTimetableInput, PreparedGroupCombinations

from .config import MINUTES_PER_DAY, SolverConfig
from .eligibility import candidate_pools
from .deliveries import (
    CombinationSolverConfig, attendance_conflicts, checked_combinations, delivery_alternatives,
    known_overlap_pairs, objective_cost,
)
from .delivery_validation import validate_delivery_result, validate_delivery_timetable
from .inputs import checked_inputs
from .results import ScheduledDelivery, ScheduledSession, SolverInputError, SolverResult, SolverStatus
from .validator import validate_timetable
from .venue_inputs import VenueSchedulingInput, checked_venue_inputs, venue_footprints
from .venue_constraints import eligible_venue, venue_start_domains
from .staff_inputs import StaffSchedulingInput, checked_staff_inputs
from .staff_constraints import add_teaching_caps, staff_start_domains
from .staff_validation import validate_staff_policy_result
from schedence_teaching.workload.assessment import _assess_validated_schedule
from schedence_teaching.workload.period_preparation import PreparedWorkPeriods
from .work_period_inputs import WorkPeriodSolverConfig, checked_work_period_inputs
from .work_period_constraints import add_work_period_intervals, decode_administrative_periods
from .work_period_results import work_period_report
from .attendance_inputs import DeliveryMode, mode_on_day, permitted_day


def solve_timetable(prepared: PreparedTimetableInput, config: SolverConfig, *,
                    venue_input: VenueSchedulingInput | None = None,
                    combination_input: PreparedGroupCombinations | None = None,
                    combination_config: CombinationSolverConfig | None = None,
                    staff_input: StaffSchedulingInput | None = None,
                    work_periods: PreparedWorkPeriods | None = None,
                    work_period_config: WorkPeriodSolverConfig | None = None,
                    preference_input: PreparedLecturerPreferences | None = None,
                    preference_calendar: PreferenceCalendar | None = None,
                    preference_policy: PreferenceScoringPolicy | None = None,
                    preference_config: PreferenceOptimizationConfig | None = None,
                    fairness_policy: FairnessPolicy | None = None,
                    fairness_calendar: PreferenceCalendar | None = None,
                    fairness_config: FairnessOptimizationConfig | None = None,
                    consecutive_policy: ConsecutivePolicy | None = None,
                    consecutive_calendar: PreferenceCalendar | None = None,
                    consecutive_config: ConsecutiveOptimizationConfig | None = None,
                    student_lunch_policy: StudentLunchPolicy | None = None,
                    student_lunch_calendar: PreferenceCalendar | None = None,
                    student_lunch_config: StudentLunchEnforcementConfig | None = None,
                    schedule_hints: tuple[ScheduledSession, ...] = (),
                    diagnostics: dict | None = None) -> SolverResult:
    """Joint delivery/time/staff/venue decisions; legacy callers remain feasibility-only."""
    started = perf_counter()
    prepared, config = checked_inputs(prepared, config)
    from schedence_teaching.policy.resolution import verify_runtime_contexts
    try:
        verify_runtime_contexts(config, locals().copy(), require_all=True)
    except ValueError as error:
        raise SolverInputError(str(error)) from None
    if schedule_hints and combination_input is not None:
        raise SolverInputError('Separate-delivery search hints cannot describe combined alternatives.')
    if staff_input is not None:
        staff_input = checked_staff_inputs(prepared, config, staff_input)
    work_period_enabled = work_periods is not None
    if work_period_config is not None and not work_period_enabled:
        raise SolverInputError("Work-period config requires prepared work periods.")
    if work_period_enabled:
        work_periods, work_period_config = checked_work_period_inputs(
            prepared, config, staff_input, work_periods, work_period_config)
    staff_calendars = {row.staff_id: row for row in staff_input.availability} if staff_input is not None else {}
    staff_assignments = defaultdict(list)
    combination_enabled = combination_input is not None
    if combination_enabled and config.delivery_calendar is not None:
        raise SolverInputError("Online/physical delivery calendars currently support separate deliveries only; combining was not enabled.")
    if combination_config is not None and not combination_enabled:
        raise SolverInputError("Combination config requires prepared combination input.")
    online_days = None
    if config.batch_online_days is not None:
        if combination_enabled or venue_input is None:
            raise SolverInputError("Batch online days require separate deliveries with venue allocation.")
        from .online_day_inputs import add_batch_online_day, booking_mode, checked_batch_online_days
        online_days = checked_batch_online_days(prepared, config, staff_input.day_dates if staff_input is not None else None)
    if combination_enabled:
        combination_input, combination_config = checked_combinations(prepared, combination_input, combination_config)
    venue_enabled = venue_input is not None
    if venue_enabled:
        venue_input = checked_venue_inputs(prepared, config, venue_input)
    preference_enabled = preference_input is not None
    if not preference_enabled and any(row is not None for row in (preference_calendar, preference_policy, preference_config)):
        raise SolverInputError("Preference optimization options require prepared preference input.")
    preference_context = None
    if preference_enabled:
        from .preference_inputs import checked_preference_inputs, objective_plan
        from .preference_constraints import add_preference_penalties, decode_preference_penalties
        from .preference_results import preference_report
        from .preference_validation import validate_preference_result
        preference_context = checked_preference_inputs(prepared, config, preference_input, preference_calendar,
            preference_policy, preference_config, staff_input=staff_input, work_periods=work_periods)
        preference_order, _, preference_coefficients = objective_plan(prepared, preference_context, combination_config)
    fairness_enabled = fairness_config is not None
    if (staff_input is not None and staff_input.assignment_policy is not None
            and staff_input.assignment_policy.require_target_optimization and not fairness_enabled):
        raise SolverInputError('Current teaching policy requires explicit teaching-target fairness optimization; use a labelled feasibility-only policy for diagnostics.')
    if not fairness_enabled and any(value is not None for value in (fairness_policy, fairness_calendar)):
        raise SolverInputError("Fairness policy/calendar require explicit FairnessOptimizationConfig.")
    fairness_context = None
    if fairness_enabled:
        from .fairness_inputs import checked_fairness_inputs, fairness_objective_plan
        from .fairness_constraints import add_fairness_variables, decode_fairness_workloads
        from .lexicographic import staged_minimize
        fairness_context = checked_fairness_inputs(prepared, config, fairness_policy, fairness_calendar,
            fairness_config, staff_input=staff_input, work_periods=work_periods)
        if preference_enabled and fairness_context.calendar != preference_context.calendar:
            raise SolverInputError("Fairness and preference optimization must use the same academic week calendar.")
        fairness_order, _ = fairness_objective_plan(prepared, fairness_context, preference_context, combination_config)
    footprints = venue_footprints(venue_input) if venue_enabled else {}
    alternatives = delivery_alternatives(prepared, combination_input, pools=candidate_pools(prepared.input_snapshot))
    if config.lab_variables_first:
        # Search/model ordering only: all alternatives, constraints and objective
        # terms are retained. No lab is fixed before the global solve.
        alternatives = tuple(sorted(alternatives, key=lambda row: (
            row.covered_sessions[0].session_type.value != "LAB", row.delivery_id)))
    consecutive_enabled = consecutive_config is not None
    if not consecutive_enabled and any(value is not None for value in (consecutive_policy, consecutive_calendar)):
        raise SolverInputError("Consecutive policy/calendar require explicit ConsecutiveOptimizationConfig.")
    consecutive_context = None
    if consecutive_enabled:
        from .consecutive_inputs import checked_consecutive_inputs, consecutive_objective_plan
        from .consecutive_constraints import add_consecutive_variables, decode_consecutive_entities
        from .lexicographic import staged_minimize
        consecutive_context = checked_consecutive_inputs(prepared, config, consecutive_policy, consecutive_calendar,
            consecutive_config, alternatives, staff_input=staff_input, work_periods=work_periods,
            preference_calendar=preference_calendar, fairness_calendar=fairness_calendar)
        consecutive_order, _ = consecutive_objective_plan(prepared, consecutive_context, fairness_context, preference_context, combination_config)
    compactness_enabled = config.teaching_day_compactness is not None
    daily_enabled = config.daily_load_distribution is not None
    quality_enabled = compactness_enabled or daily_enabled
    if quality_enabled:
        from .compactness_inputs import add_thin_day_flags, compactness_report
        from .daily_load_inputs import (STAFF as DAILY_STAFF, STUDENT as DAILY_STUDENT, add_staff_daily_maximum,
                                        add_student_daily_maximum, daily_load_report, quality_order)
        from .deliveries import DeliveryObjective
        from .lexicographic import staged_minimize
        daily_criteria = {row.value for row in config.daily_load_distribution.criteria} if daily_enabled else set()
        if staff_input is None and (compactness_enabled or DAILY_STAFF in daily_criteria):
            raise SolverInputError("Teaching-day compactness and staff daily load require original staff scheduling input.")
        if config.objective_stage_plan is None and (consecutive_enabled or (preference_enabled and not fairness_enabled)):
            raise SolverInputError("Teaching-day compactness and daily-load stages are staged with delivery/workload objectives; "
                                   "they are not yet combined with consecutive or preference-only optimization.")
        mode = combination_config.objective if combination_config else DeliveryObjective.DELIVERIES_THEN_MINUTES
        base_order = fairness_order if fairness_enabled else {
            DeliveryObjective.DELIVERIES_THEN_MINUTES: ("DELIVERIES", "CONTACT_MINUTES"),
            DeliveryObjective.DELIVERIES: ("DELIVERIES",), DeliveryObjective.CONTACT_MINUTES: ("CONTACT_MINUTES",),
            DeliveryObjective.FEASIBILITY: ()}[mode]
        try:
            if config.objective_stage_plan is None:
                thin_order = quality_order(base_order, compactness=config.teaching_day_compactness,
                                           daily=config.daily_load_distribution, fairness_enabled=fairness_enabled)
        except ValueError as error:
            raise SolverInputError(str(error)) from None
    stage_plan_enabled = config.objective_stage_plan is not None
    if stage_plan_enabled:
        # P3.1: one checked shared order; module orders are its projections, never re-derived independently.
        from .deliveries import DeliveryObjective
        from .lexicographic import staged_minimize
        from .objective_stage_plan import project, stage_report, stage_report_issues, unified_order
        plan = config.objective_stage_plan
        mode = combination_config.objective if combination_config else DeliveryObjective.DELIVERIES_THEN_MINUTES
        plan_base = (fairness_order if fairness_enabled else preference_order if preference_enabled else {
            DeliveryObjective.DELIVERIES_THEN_MINUTES: ("DELIVERIES", "CONTACT_MINUTES"),
            DeliveryObjective.DELIVERIES: ("DELIVERIES",), DeliveryObjective.CONTACT_MINUTES: ("CONTACT_MINUTES",),
            DeliveryObjective.FEASIBILITY: ()}[mode])
        extra = ({"THIN_TEACHING_DAYS"} if compactness_enabled else set()) | (set(daily_criteria) if daily_enabled else set()) | (
            {"CONSECUTIVE_PENALTY"} if consecutive_enabled else set())
        try:
            thin_order = unified_order(plan, plan_base, extra, compactness=config.teaching_day_compactness,
                                       daily=config.daily_load_distribution, fairness_enabled=fairness_enabled)
            if fairness_enabled:
                fairness_order = project(plan, fairness_order)
            if consecutive_enabled:
                consecutive_order = project(plan, consecutive_order)
        except ValueError as error:
            raise SolverInputError(str(error)) from None
    lunch_enabled = student_lunch_config is not None
    if not lunch_enabled and (student_lunch_policy is not None or student_lunch_calendar is not None):
        raise SolverInputError("Student lunch policy/calendar require explicit StudentLunchEnforcementConfig.")
    lunch_context = None
    lunch_rules = {}
    if lunch_enabled:
        from .lunch_inputs import checked_student_lunch_inputs
        from .lunch_constraints import add_student_lunch_constraint
        lunch_context = checked_student_lunch_inputs(prepared, config, student_lunch_policy, student_lunch_calendar,
            student_lunch_config, alternatives, staff_input=staff_input, work_periods=work_periods,
            preference_calendar=preference_calendar, fairness_calendar=fairness_calendar, consecutive_calendar=consecutive_calendar)
        lunch_rules = dict(lunch_context.delivery_rules)
    overlaps = known_overlap_pairs(combination_input)
    membership_students = {row.group_id: row.student_ids for row in config.attendance_memberships}
    intervals_by_student = defaultdict(dict)
    model = cp_model.CpModel()
    granularity = config.granularity_minutes
    ticks_per_day = (MINUTES_PER_DAY + granularity - 1) // granularity
    variables = {}
    intervals_by_group = defaultdict(dict)
    intervals_by_staff = defaultdict(list)
    intervals_by_venue_resource = defaultdict(list)
    coverage = defaultdict(list)
    selected_alternatives = {}
    resource_choice_count = 0
    preference_terms = []
    start_variables = {}
    staff_start_ticks = {}  # (delivery, staff) -> start ticks already enforced when that staff member is selected
    online_choice, lectures_on = {}, defaultdict(lambda: defaultdict(list))
    for batch, indices in (online_days.candidates.items() if online_days is not None else ()):
        online_choice[batch] = {index: model.new_bool_var(
            f"batch_online_day:{batch[0]}:{batch[1]}:{batch[2].value}:{config.days[index].day}") for index in indices}
        model.add_exactly_one(list(online_choice[batch].values()))

    def failed(reason, status=SolverStatus.INFEASIBLE):
        return SolverResult(status, (), perf_counter() - started, None, reason,
                            venue_enabled=venue_enabled, combination_enabled=combination_enabled,
                            original_occurrence_count=len(prepared.sessions),
                            objective_mode=combination_config.objective.value if combination_enabled else None,
                            staff_policy_enabled=staff_input is not None, work_period_enabled=work_period_enabled,
                            preference_optimization_enabled=preference_enabled, fairness_optimization_enabled=fairness_enabled,
                            consecutive_optimization_enabled=consecutive_enabled, student_lunch_enabled=lunch_enabled)

    def guard_model():
        if lunch_enabled and (len(model.proto.variables) > lunch_context.config.max_model_variables
                              or len(model.proto.constraints) > lunch_context.config.max_model_constraints):
            raise SolverInputError("Student lunch model-size limit exceeded; no original requirements were dropped.")
        if consecutive_enabled and (len(model.proto.variables) > consecutive_context.config.max_model_variables
                                    or len(model.proto.constraints) > consecutive_context.config.max_model_constraints):
            raise SolverInputError("Consecutive model-size limit exceeded; no sessions or participants were dropped.")
        if fairness_enabled and (len(model.proto.variables) > fairness_context.config.max_model_variables
                                 or len(model.proto.constraints) > fairness_context.config.max_model_constraints):
            raise SolverInputError("Fairness CP-SAT model size limit exceeded; no cohort members were dropped.")
        if preference_enabled and (len(model.proto.variables) > preference_context.config.max_model_variables
                                    or len(model.proto.constraints) > preference_context.config.max_model_constraints):
            raise SolverInputError("Preference CP-SAT model size limit exceeded; no criteria were dropped.")
        if work_period_enabled and (len(model.proto.variables) > work_period_config.max_model_variables
                                    or len(model.proto.constraints) > work_period_config.max_model_constraints):
            raise SolverInputError("Work-period CP-SAT model size limit exceeded; no reservations were dropped.")
        if combination_enabled and (len(model.proto.variables) > combination_config.max_model_variables
                                    or len(model.proto.constraints) > combination_config.max_model_constraints):
            raise SolverInputError("CP-SAT model size limit exceeded; no alternatives were dropped.")

    def guard_resource_choices(count):
        nonlocal resource_choice_count
        resource_choice_count += count
        if combination_enabled and resource_choice_count > combination_config.max_resource_choices:
            raise SolverInputError("Resource choice limit exceeded; no alternatives were dropped.")

    for alternative in alternatives:
        session = alternative.covered_sessions[0]
        identity = alternative.delivery_id
        candidates = alternative.eligible_staff_ids
        policy_days={}
        if staff_input is not None and staff_input.assignment_policy is not None:
            from .teaching_policy import eligible_assignment_days
            policy_days={sid:eligible_assignment_days(prepared.input_snapshot,staff_input,
                staff_input.assignment_policy,sid,alternative.covered_sessions) for sid in candidates}
            candidates=tuple(sid for sid in candidates if policy_days[sid])
        presence = model.new_bool_var(f"delivery:{identity}") if combination_enabled else None
        if presence is not None:
            selected_alternatives[identity] = presence
            for covered in alternative.covered_sessions:
                coverage[covered.occurrence_id].append(presence)
        if not candidates:
            if combination_enabled:
                model.add(presence == 0)
                continue
            return failed(f"Occurrence {identity} (offering {session.offering_id}) has no eligible teaching staff.")
        duration = session.duration_minutes // granularity
        domains = []
        for index, day in enumerate(config.days):
            if session.stream not in day.windows or not permitted_day(config, session, day.day):
                continue
            for window in day.windows.get(session.stream, []):
                if window.end_minute - window.start_minute >= session.duration_minutes:
                    base = index * ticks_per_day
                    domains.append([base + window.start_minute // granularity,
                                    base + (window.end_minute - session.duration_minutes) // granularity])
        if not domains:
            if combination_enabled:
                model.add(presence == 0)
                continue
            return failed(f"Occurrence {identity} has no configured window that can contain its duration.")
        start = model.new_int_var_from_domain(cp_model.Domain.from_intervals(domains), f"start:{identity}")
        start_variables[identity] = start
        end = model.new_int_var(min(item[0] for item in domains) + duration,
                                max(item[1] for item in domains) + duration, f"end:{identity}")
        day_variable = model.new_int_var(0, len(config.days) - 1, f"day:{identity}")
        local_start = model.new_int_var(0, ticks_per_day - 1, f"local_start:{identity}")
        model.add_division_equality(day_variable, start, ticks_per_day)
        model.add_modulo_equality(local_start, start, ticks_per_day)
        online_lecture = None
        if online_days is not None and (batch := online_days.session_batches[session.occurrence_id]) in online_choice:
            online_lecture = add_batch_online_day(model, config, session, day_variable, online_choice[batch],
                                                  lectures_on[batch], identity)
        if lunch_enabled:
            add_student_lunch_constraint(model, start, domains, alternative.duration_minutes, presence, lunch_rules[identity], config)
        interval = (model.new_optional_interval_var(start, duration, end, presence, f"interval:{identity}")
                    if combination_enabled else model.new_interval_var(start, duration, end, f"interval:{identity}"))
        for group_id in alternative.group_ids:
            intervals_by_group[group_id][identity] = interval
            for student in membership_students.get(group_id, ()):
                intervals_by_student[student][identity] = interval
        choices = {}
        available_staff = 0
        guard_resource_choices(len(candidates))
        for staff_id in candidates:
            selected = model.new_bool_var(f"staff:{identity}:{staff_id}")
            choices[staff_id] = selected
            if staff_input is not None:
                staff_domains = (staff_start_domains(session, staff_calendars[staff_id], config, staff_input.day_dates,
                    allowed_days=policy_days[staff_id]) if staff_id in policy_days else
                    staff_start_domains(session, staff_calendars[staff_id], config, staff_input.day_dates))
                if staff_domains:
                    available_staff += 1
                    staff_start_ticks[identity, staff_id] = staff_domains
                    model.add_linear_expression_in_domain(start, cp_model.Domain.from_intervals(staff_domains)).only_enforce_if(selected)
                else:
                    model.add(selected == 0)
                staff_assignments[staff_id].append((identity, selected, day_variable, alternative.duration_minutes))
            staff_interval = model.new_optional_interval_var(
                start, duration, end, selected, f"staff_interval:{identity}:{staff_id}",
            )
            intervals_by_staff[staff_id].append(staff_interval)
        if combination_enabled:
            model.add(sum(choices.values()) == presence)
        else:
            if staff_input is not None and not available_staff:
                # Pre-solve proof: hard availability (windows, blocked periods, eligible dates) leaves no lecturer.
                return failed(f"Occurrence {identity} (offering {session.offering_id}, {session.session_type.value}, "
                              f"group {session.group_id}) has no eligible lecturer available in any configured window; "
                              f"hard availability and blocked periods are not relaxed.")
            model.add_exactly_one(list(choices.values()))
        venue_choices = {}
        if venue_enabled:
            physical = None
            if online_lecture is not None:
                physical = model.new_bool_var(f"physical:{identity}")
                model.add(physical + online_lecture == 1)
            elif config.delivery_calendar is not None:
                physical = model.new_bool_var(f"physical:{identity}")
                model.add_allowed_assignments([day_variable, physical], [
                    [index, int(mode_on_day(config, day.day, session.stream) == DeliveryMode.PHYSICAL)]
                    for index, day in enumerate(config.days) if session.stream in day.windows and permitted_day(config, session, day.day)])
            for venue in sorted(venue_input.venues, key=lambda row: row.venue_id):
                if ((alternative.is_combined and venue.capacity < alternative.student_count)
                        or not all(eligible_venue(covered, venue, prepared, venue_input)
                                   for covered in alternative.covered_sessions)):
                    continue
                venue_domains = venue_start_domains(session, venue.venue_id, venue_input, config)
                if not venue_domains:
                    continue
                guard_resource_choices(1)
                selected = model.new_bool_var(f"venue:{identity}:{venue.venue_id}")
                venue_choices[venue.venue_id] = selected
                model.add_linear_expression_in_domain(start, cp_model.Domain.from_intervals(venue_domains)).only_enforce_if(selected)
                venue_interval = model.new_optional_interval_var(
                    start, duration, end, selected, f"venue_interval:{identity}:{venue.venue_id}",
                )
                for resource in footprints[venue.venue_id]:
                    intervals_by_venue_resource[resource].append(venue_interval)
            if not venue_choices and physical is None:
                if combination_enabled:
                    model.add(presence == 0)
                else:
                    return failed(f"Occurrence {identity} has no eligible venue with sufficient capacity, policy compatibility and available time.")
            if physical is not None:
                model.add(sum(venue_choices.values()) == physical)
            elif combination_enabled:
                model.add(sum(venue_choices.values()) == presence)
            else:
                model.add_exactly_one(list(venue_choices.values()))
        variables[identity] = (day_variable, local_start, choices, venue_choices)
        if preference_enabled:
            add_preference_penalties(model, alternative, start, choices, preference_context,
                                      config, preference_terms, guard_model)
        guard_model()

    if combination_enabled:
        for session in prepared.sessions:
            model.add_exactly_one(coverage[session.occurrence_id])
        original_minutes = sum(row.duration_minutes for row in prepared.sessions)
        count_expression = sum(selected_alternatives.values())
        minutes_expression = sum(row.duration_minutes * selected_alternatives[row.delivery_id] for row in alternatives)
        objective = objective_cost(combination_config.objective, count_expression, minutes_expression, original_minutes)
        # CP-SAT integer arithmetic must remain comfortably within signed 64-bit bounds.
        maximum = objective_cost(combination_config.objective, len(alternatives),
                                 sum(row.duration_minutes for row in alternatives), original_minutes)
        if maximum is not None and maximum > 2**60:
            raise SolverInputError("Objective magnitude exceeds the safe integer bound; narrow the input.")
        if objective is not None:
            model.minimize(objective)

    administrative_blocks = (add_work_period_intervals(model, config, staff_input, work_periods,
                                work_period_config, intervals_by_staff, guard_model) if work_period_enabled else [])

    for resource in sorted(intervals_by_venue_resource):
        resource_intervals = intervals_by_venue_resource[resource]
        if len(resource_intervals) > 1:
            model.add_no_overlap(resource_intervals)

    # One global staff pool covers every offering and stream, including instructors.
    # Unselected optional intervals consume no staff time; touching endpoints are legal.
    for staff_intervals in intervals_by_staff.values():
        if len(staff_intervals) > 1:
            model.add_no_overlap(staff_intervals)

    # Each chain includes one group's intervals and all ancestors, never sibling branches.
    # Repeated ancestor intervals across constraints impose no sibling-to-sibling exclusion.
    for group_id in prepared.hierarchy.parents:
        chain_intervals = {}
        current = group_id
        while current is not None:
            chain_intervals.update(intervals_by_group[current])
            current = prepared.hierarchy.parents[current]
        if len(chain_intervals) > 1:
            model.add_no_overlap(list(chain_intervals.values()))
            guard_model()

    # Exact registrations impose one non-overlap chain per distinct attendance
    # pattern. Repeated students with identical patterns need no duplicate rule.
    seen_attendance = set()
    for attendance in intervals_by_student.values():
        signature = tuple(sorted(attendance))
        if len(signature) > 1 and signature not in seen_attendance:
            model.add_no_overlap(list(attendance.values()))
            seen_attendance.add(signature)
            guard_model()

    # Additional overlap is evidence-driven, with upward hierarchy propagation.
    # Deduplicate deliveries shared by multiple groups before NoOverlap.
    if overlaps:
        group_ids = sorted(intervals_by_group)
        for index, first in enumerate(group_ids):
            for second in group_ids[index + 1:]:
                if attendance_conflicts(prepared.hierarchy, first, second, overlaps):
                    pair_intervals = {**intervals_by_group[first], **intervals_by_group[second]}
                    if len(pair_intervals) > 1:
                        model.add_no_overlap(list(pair_intervals.values()))
                        guard_model()
    for batch, choices in online_choice.items():
        for index, chosen in choices.items():
            model.add(sum(lectures_on[batch][index]) >= chosen)  # Decision 3: never an empty online day.
    if schedule_hints:
        expected={s.occurrence_id:s for s in prepared.sessions};seen_hints=set()
        day_indexes={d.day:i for i,d in enumerate(config.days)}
        for hint in schedule_hints:
            if not isinstance(hint,ScheduledSession) or hint.session!=expected.get(hint.session.occurrence_id):
                raise SolverInputError('Search hints must preserve original occurrence metadata.')
            identity=hint.session.occurrence_id
            if identity in seen_hints:raise SolverInputError('Duplicate occurrence search hint.')
            seen_hints.add(identity)
            if identity not in variables or hint.day not in day_indexes:continue
            day_var,local_var,choices,venue_choices=variables[identity]
            if hint.assigned_staff_id not in choices:continue
            model.add_hint(day_var,day_indexes[hint.day])
            model.add_hint(local_var,hint.start_minute//granularity)
            for sid,chosen in choices.items():model.add_hint(chosen,int(sid==hint.assigned_staff_id))
            for vid,chosen in venue_choices.items():model.add_hint(chosen,int(vid==hint.assigned_venue_id))
    staff_days_needed = compactness_enabled or (daily_enabled and DAILY_STAFF in daily_criteria)
    day_usage, delivery_day_flags = ({} if staff_days_needed else None), {}
    if staff_input is not None:
        # The legacy call is unchanged unless P2.1/P2.2 ask for the existing staff-day variables.
        add_teaching_caps(model, config, staff_input, staff_assignments, guard_model,
                          **({"day_usage": day_usage, "day_flags": delivery_day_flags} if quality_enabled else {}))
    thin_total = add_thin_day_flags(model, config, day_usage) if compactness_enabled else None
    daily_maxima = {}
    if daily_enabled:
        from .staff_constraints import delivery_day_flag
        if DAILY_STUDENT in daily_criteria:
            daily_maxima[DAILY_STUDENT] = add_student_daily_maximum(model, config, prepared, [
                (identity, alternative.group_ids, alternative.duration_minutes,
                 selected_alternatives.get(identity) if combination_enabled else None, variables[identity][0],
                 {covered.stream for covered in alternative.covered_sessions})
                for alternative in alternatives if (identity := alternative.delivery_id) in variables],
                lambda identity, index, day_variable: delivery_day_flag(
                    model, delivery_day_flags, identity, index, day_variable, config.days[index].day))
        if DAILY_STAFF in daily_criteria:
            daily_maxima[DAILY_STAFF] = add_staff_daily_maximum(model, day_usage, staff_assignments)
        guard_model()
    if preference_enabled:
        components = {
            "DELIVERIES": count_expression if combination_enabled else len(prepared.sessions),
            "CONTACT_MINUTES": minutes_expression if combination_enabled else sum(row.duration_minutes for row in prepared.sessions),
            "PREFERENCE_PENALTY": sum(row[-1] for row in preference_terms),
        }
        preference_objective = sum(components[key] * preference_coefficients[key] for key in preference_order)
        model.minimize(preference_objective)
    guard_model()

    if fairness_enabled:
        fairness_variables, fairness_components = add_fairness_variables(model, fairness_context, staff_assignments, guard_model)
        components = {
            "DELIVERIES": count_expression if combination_enabled else len(prepared.sessions),
            "CONTACT_MINUTES": minutes_expression if combination_enabled else sum(row.duration_minutes for row in prepared.sessions),
            "PREFERENCE_PENALTY": sum(row[-1] for row in preference_terms), **fairness_components,
        }
    if consecutive_enabled:
        decisions = {identity: (day, local_start, choices,
            selected_alternatives[identity] if combination_enabled else model.new_constant(1))
            for identity, (day, local_start, choices, _) in variables.items()}
        consecutive_variables, consecutive_penalty = add_consecutive_variables(model, prepared, config,
            consecutive_context, alternatives, decisions, guard_model, start_variables=start_variables,
            staff_start_ticks=staff_start_ticks if staff_input is not None else None)
        if not fairness_enabled:
            components = {"DELIVERIES": count_expression if combination_enabled else len(prepared.sessions),
                "CONTACT_MINUTES": minutes_expression if combination_enabled else sum(row.duration_minutes for row in prepared.sessions),
                "PREFERENCE_PENALTY": sum(row[-1] for row in preference_terms)}
        components["CONSECUTIVE_PENALTY"] = consecutive_penalty
    if stage_plan_enabled:
        if not fairness_enabled and not consecutive_enabled:
            components = {"DELIVERIES": count_expression if combination_enabled else len(prepared.sessions),
                "CONTACT_MINUTES": minutes_expression if combination_enabled else sum(row.duration_minutes for row in prepared.sessions),
                "PREFERENCE_PENALTY": sum(row[-1] for row in preference_terms)}
        if compactness_enabled:
            components["THIN_TEACHING_DAYS"] = thin_total
        components.update(daily_maxima if daily_enabled else {})
        plan_model_size = (len(model.proto.variables), len(model.proto.constraints))
        solver, status, compactness_stages = staged_minimize(model, config, thin_order, components,
            skip_constant_stages=True, carry_incumbent_hints=True)
        if fairness_enabled:
            fairness_stages = tuple(row for row in compactness_stages if row.criterion in set(fairness_order))
        if consecutive_enabled:
            consecutive_stages = tuple(row for row in compactness_stages if row.criterion in set(consecutive_order))
    elif consecutive_enabled:
        solver, status, consecutive_stages = staged_minimize(model, config, consecutive_order, components)
        if fairness_enabled:
            fairness_stages = tuple(row for row in consecutive_stages if row.criterion != "CONSECUTIVE_PENALTY")
    elif quality_enabled:
        if not fairness_enabled:
            components = {"DELIVERIES": count_expression if combination_enabled else len(prepared.sessions),
                "CONTACT_MINUTES": minutes_expression if combination_enabled else sum(row.duration_minutes for row in prepared.sessions)}
        if compactness_enabled:
            components["THIN_TEACHING_DAYS"] = thin_total
        components.update(daily_maxima)
        from .fairness_inputs import FairnessPriority
        peak = fairness_enabled and fairness_context.config.priority is FairnessPriority.DELIVERY_COST_THEN_PEAK_OVERAGE_TOTAL_OVERAGE_FAIRNESS_PREFERENCES
        solver, status, compactness_stages = staged_minimize(model, config, thin_order, components,
            skip_constant_stages=peak, carry_incumbent_hints=peak)
        if fairness_enabled:
            quality_criteria = {"THIN_TEACHING_DAYS", *daily_criteria}
            fairness_stages = tuple(row for row in compactness_stages if row.criterion not in quality_criteria)
    elif fairness_enabled:
        from .fairness_inputs import FairnessPriority
        peak = fairness_context.config.priority is FairnessPriority.DELIVERY_COST_THEN_PEAK_OVERAGE_TOTAL_OVERAGE_FAIRNESS_PREFERENCES
        solver, status, fairness_stages = staged_minimize(model, config, fairness_order, components,
            skip_constant_stages=peak, carry_incumbent_hints=peak)
    else:
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = config.max_time_seconds
        solver.parameters.num_search_workers = config.num_search_workers
        if config.random_seed is not None:
            solver.parameters.random_seed = config.random_seed
        native_status = solver.solve(model)
        status = {
            cp_model.OPTIMAL: SolverStatus.OPTIMAL, cp_model.FEASIBLE: SolverStatus.FEASIBLE,
            cp_model.INFEASIBLE: SolverStatus.INFEASIBLE, cp_model.UNKNOWN: SolverStatus.UNKNOWN,
            cp_model.MODEL_INVALID: SolverStatus.MODEL_INVALID,
        }[native_status]
    if diagnostics is not None:
        diagnostics.update(model_variables=len(model.proto.variables),model_constraints=len(model.proto.constraints),
            status=status.value,soft_preference_optimization=preference_enabled,
            target_optimization=fairness_enabled,search_hints=len(schedule_hints),
            max_time_seconds=config.max_time_seconds,num_search_workers=config.num_search_workers)
        if solver is not None:
            diagnostics.update(native_response_stats=solver.response_stats(),native_wall_seconds=solver.wall_time,
                branches=solver.num_branches,conflicts=solver.num_conflicts)
    if status not in {SolverStatus.OPTIMAL, SolverStatus.FEASIBLE}:
        reason = "No complete feasible solution was returned; no timetable is emitted."
        if consecutive_enabled and consecutive_context.config.enforce_confirmed_hard_break:
            reason += " Explicit confirmed minimum-break restrictions were enabled."
        if lunch_enabled:
            reason += " Fixed student lunch restrictions were enabled."
        return failed(reason, status)
    schedule = []
    selection = {batch: config.days[next(index for index, chosen in choices.items() if solver.value(chosen))].day
                 for batch, choices in online_choice.items()}
    selected_online_days = (tuple((year, semester, stream.value, day) for (year, semester, stream), day in sorted(selection.items()))
                            if online_days is not None else None)
    for alternative in alternatives:
        identity = alternative.delivery_id
        if combination_enabled and not solver.value(selected_alternatives[identity]):
            continue
        session = alternative.covered_sessions[0]
        day_variable, local_start, choices, venue_choices = variables[identity]
        day = config.days[solver.value(day_variable)].day
        start_minute = solver.value(local_start) * granularity
        assigned_staff_id = next(staff_id for staff_id, selected in choices.items() if solver.value(selected))
        assigned_venue_id = next((venue_id for venue_id, selected in venue_choices.items() if solver.value(selected)), None)
        if combination_enabled:
            schedule.append(ScheduledDelivery(identity, alternative.is_combined, alternative.covered_sessions,
                                               alternative.group_ids, alternative.student_count, day, start_minute,
                                               start_minute + alternative.duration_minutes, assigned_staff_id, assigned_venue_id))
        else:
            schedule.append(ScheduledSession(session, day, start_minute, start_minute + session.duration_minutes,
                                             assigned_staff_id, assigned_venue_id,
                                             (booking_mode(config, online_days, selection, session, day) if online_days is not None
                                              else mode_on_day(config, day, session.stream)) if config.delivery_calendar else None))

    def attach_work_periods(result):
        if not work_period_enabled:
            return result
        administrative_schedule = decode_administrative_periods(solver, administrative_blocks, config, staff_input)
        return replace(result, work_period_enabled=True, administrative_schedule=administrative_schedule,
                       work_period_report=work_period_report(work_periods, administrative_schedule, result.workload_report))

    def attach_preferences(result):
        if not preference_enabled:
            return result
        result = replace(result, preference_optimization_enabled=True,
            objective_status="EVALUATED_COMPONENT" if result.objective_value is not None else None)
        modeled = decode_preference_penalties(solver, preference_terms)
        report = preference_report(prepared, config, result, preference_context, modeled, combination_config,
                                    staff_input=staff_input, work_periods=work_periods)
        return replace(result, preference_report=replace(report, objective_value=solver.value(preference_objective)))

    work_validation_inputs = ({"work_periods": work_periods, "work_period_config": work_period_config}
                              if work_period_enabled else {})
    preference_validation_inputs = ({"preference_input": preference_context.preferences,
        "preference_calendar": preference_context.calendar, "preference_policy": preference_context.policy,
        "preference_config": preference_context.config} if preference_enabled else {})
    def finish_fairness(result):
        from schedence_teaching.fairness import assess_fairness
        from .fairness_results import fairness_report
        from .fairness_validation import assessment_projection, validate_fairness_result
        result = replace(result, fairness_optimization_enabled=True,
            objective_status="EVALUATED_COMPONENT" if result.objective_value is not None else None)
        if result.preference_report is not None:
            result = replace(result, preference_report=replace(result.preference_report, objective_status="EVALUATED_COMPONENT"))
        original = dict(venue_input=venue_input, combination_input=combination_input,
            combination_config=combination_config, staff_input=staff_input,
            **work_validation_inputs, **preference_validation_inputs)
        assessment = assess_fairness(prepared, config, assessment_projection(result), fairness_context.calendar,
            fairness_context.policy, **original)
        report = fairness_report(prepared, result, fairness_context, assessment,
            decode_fairness_workloads(solver, fairness_variables), fairness_stages, preference_context, combination_config,
            stage_plan=config.objective_stage_plan)
        result = replace(result, fairness_report=report)
        validate_fairness_result(prepared, config, result, fairness_policy=fairness_context.policy,
            fairness_calendar=fairness_context.calendar, fairness_config=fairness_context.config, **original)
        return result

    def finish_consecutive(result):
        from schedence_teaching.consecutive import assess_consecutive_sessions
        from .consecutive_results import consecutive_report
        from .consecutive_validation import assessment_projection, validate_consecutive_result
        overall_status = result.status
        if fairness_enabled:
            prefix_proven = len(fairness_stages) == len(fairness_order) and all(row.status == "OPTIMAL" for row in fairness_stages)
            result = finish_fairness(replace(result, status=SolverStatus.OPTIMAL if prefix_proven else SolverStatus.FEASIBLE))
            result = replace(result, status=overall_status,
                fairness_report=replace(result.fairness_report, objective_status="EVALUATED_COMPONENT"))
        result = replace(result, consecutive_optimization_enabled=True,
            objective_status="EVALUATED_COMPONENT" if result.objective_value is not None else None)
        if result.preference_report is not None:
            result = replace(result, preference_report=replace(result.preference_report, objective_status="EVALUATED_COMPONENT"))
        original = dict(venue_input=venue_input, combination_input=combination_input,
            combination_config=combination_config, staff_input=staff_input,
            **work_validation_inputs, **preference_validation_inputs)
        if fairness_enabled:
            original.update(fairness_policy=fairness_context.policy, fairness_calendar=fairness_context.calendar,
                fairness_config=fairness_context.config)
        assessment = assess_consecutive_sessions(prepared, config, assessment_projection(result),
            consecutive_context.calendar, consecutive_context.policy, **original)
        report = consecutive_report(prepared, result, consecutive_context, assessment,
            decode_consecutive_entities(solver, consecutive_variables), consecutive_stages, fairness_context, preference_context, combination_config,
            stage_plan=config.objective_stage_plan)
        result = replace(result, consecutive_report=report)
        validate_consecutive_result(prepared, config, result, consecutive_policy=consecutive_context.policy,
            consecutive_calendar=consecutive_context.calendar, consecutive_config=consecutive_context.config, **original)
        return result

    def finish_fairness_staged(result):
        if not (quality_enabled or stage_plan_enabled):
            return finish_fairness(result)
        # Fairness reports only its own stages; the extra P2.1/P2.2 stages keep the overall status.
        overall = result.status
        proven = len(fairness_stages) == len(fairness_order) and all(row.status == "OPTIMAL" for row in fairness_stages)
        result = finish_fairness(replace(result, status=SolverStatus.OPTIMAL if proven else SolverStatus.FEASIBLE))
        # The report keeps its own proof flag; its objective status follows the overall result, as recomputed.
        return replace(result, status=overall, fairness_report=replace(result.fairness_report, objective_status=overall.value))

    def finish_lunch(result):
        if compactness_enabled:
            modeled = thin_total if isinstance(thin_total, int) else solver.value(thin_total)
            result = replace(result, teaching_day_compactness_report=compactness_report(
                prepared, config, result.schedule, thin_order, compactness_stages, modeled))
        if daily_enabled:
            modeled = {key: value if isinstance(value, int) else solver.value(value) for key, value in daily_maxima.items()}
            result = replace(result, daily_load_report=daily_load_report(
                prepared, config, result.schedule, thin_order, compactness_stages, modeled))
        if stage_plan_enabled:
            if result.preference_report is not None and result.preference_report.objective_status != "EVALUATED_COMPONENT":
                # The composite preference objective is one evaluated stage of the shared plan, not the final objective.
                result = replace(result, preference_report=replace(result.preference_report, objective_status="EVALUATED_COMPONENT"))
            result = replace(result, objective_stage_report=stage_report(config, result, thin_order, compactness_stages, *plan_model_size))
            issues = stage_report_issues(result, thin_order, result.objective_stage_report)
            if issues:
                from .results import ScheduleIssue, TimetableValidationError
                raise TimetableValidationError([ScheduleIssue("objective_stage_plan_changed", issue) for issue in issues])
        if not lunch_enabled:
            return result
        from schedence_teaching.lunch import assess_student_lunch
        from .lunch_results import student_lunch_report
        from .lunch_validation import validate_student_lunch_result
        original = dict(venue_input=venue_input, combination_input=combination_input, combination_config=combination_config,
            staff_input=staff_input, work_periods=work_periods, work_period_config=work_period_config,
            preference_input=preference_input, preference_calendar=preference_calendar, preference_policy=preference_policy,
            preference_config=preference_config, fairness_policy=fairness_policy, fairness_calendar=fairness_calendar,
            fairness_config=fairness_config, consecutive_policy=consecutive_policy, consecutive_calendar=consecutive_calendar,
            consecutive_config=consecutive_config)
        assessment = assess_student_lunch(prepared, config, result, lunch_context.calendar, lunch_context.policy, **original)
        result = replace(result, student_lunch_enabled=True, student_lunch_report=student_lunch_report(assessment, lunch_context))
        validate_student_lunch_result(prepared, config, result, student_lunch_policy=lunch_context.policy,
            student_lunch_calendar=lunch_context.calendar, student_lunch_config=lunch_context.config, **original)
        return result

    if combination_enabled:
        validation = validate_delivery_timetable(prepared, config, schedule, combination_input=combination_input,
                                                 combination_config=combination_config, venue_input=venue_input, staff_input=staff_input)
        value = objective_cost(combination_config.objective, validation.delivery_count,
                               validation.lecturer_contact_minutes, original_minutes)
        result = SolverResult(status, tuple(schedule), perf_counter() - started, validation,
                              venue_enabled=venue_enabled, combination_enabled=True,
                              original_occurrence_count=len(prepared.sessions), objective_mode=combination_config.objective.value,
                              objective_value=value, objective_status=status.value if value is not None else None,
                              staff_policy_enabled=staff_input is not None)
        if staff_input is not None:
            result = replace(result, workload_report=_assess_validated_schedule(
                prepared, result, staff_input.workload_input, staff_constraints_enforced=True))
        result = attach_work_periods(result)
        result = attach_preferences(result)
        if consecutive_enabled:
            return finish_lunch(finish_consecutive(result))
        if fairness_enabled:
            return finish_lunch(finish_fairness_staged(result))
        validate_delivery_result(prepared, config, result, combination_input=combination_input,
                                  combination_config=combination_config, venue_input=venue_input, staff_input=staff_input,
                                  **work_validation_inputs, **preference_validation_inputs)
        return finish_lunch(result)
    if staff_input is not None:
        validation = validate_timetable(prepared, config, schedule, venue_input=venue_input, staff_input=staff_input)
        result = SolverResult(status, tuple(schedule), perf_counter() - started, validation, venue_enabled=venue_enabled,
                              original_occurrence_count=len(prepared.sessions), staff_policy_enabled=True,
                              batch_online_days=selected_online_days)
        result = replace(result, workload_report=_assess_validated_schedule(
            prepared, result, staff_input.workload_input, staff_constraints_enforced=True))
        result = attach_work_periods(result)
        result = attach_preferences(result)
        if consecutive_enabled:
            return finish_lunch(finish_consecutive(result))
        if fairness_enabled:
            return finish_lunch(finish_fairness_staged(result))
        validate_staff_policy_result(prepared, config, result, staff_input=staff_input, venue_input=venue_input,
                                     **work_validation_inputs, **preference_validation_inputs)
        return finish_lunch(result)
    validation = (validate_timetable(prepared, config, schedule, venue_input=venue_input) if venue_enabled
                  else validate_timetable(prepared, config, schedule))
    result = SolverResult(status, tuple(schedule), perf_counter() - started, validation, venue_enabled=venue_enabled,
                          original_occurrence_count=len(prepared.sessions), batch_online_days=selected_online_days)
    result = attach_preferences(result)
    if consecutive_enabled:
        return finish_lunch(finish_consecutive(result))
    if preference_enabled:
        validate_preference_result(prepared, config, result, venue_input=venue_input, **preference_validation_inputs)
    return finish_lunch(result)
