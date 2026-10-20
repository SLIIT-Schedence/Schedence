"""Native CP-SAT stream decomposition with shared booking reservations.

Each part sees earlier staff/venue bookings. Only a complete independently
validated union is returned as FEASIBLE, never globally OPTIMAL. A failed
restricted part proves nothing about feasibility of the original full input.
"""

from collections import Counter, defaultdict
from dataclasses import replace
from time import perf_counter
import json

from schedence_teaching.schemas import Stream
from schedence_teaching.solver import SolverResult, SolverStatus, solve_timetable, validate_timetable
from schedence_teaching.solver.staff_inputs import StaffBlockedInterval
from schedence_teaching.solver.venue_inputs import VenueDayInterval, venue_footprints

from .electives import select_enrolled_scope


def reserve_previous_bookings(enrolled, previous):
    """Temporary search commitments, not new source policy or dataset changes."""
    contexts = dict(enrolled.batch.solver_inputs)
    staff = contexts["staff_input"].model_copy(deep=True)
    venues = contexts["venue_input"].model_copy(deep=True)
    footprints = venue_footprints(venues)
    calendars = {row.venue_id: row for row in venues.calendars}
    staff_rows = {row.staff_id: row for row in staff.availability}
    weekly, daily = Counter(), Counter()
    for booking in previous:
        row = staff_rows[booking.assigned_staff_id]
        row.blocked_periods.append(StaffBlockedInterval(day=booking.day,
            start_minute=booking.start_minute, end_minute=booking.end_minute))
        weekly[row.staff_id] += booking.session.duration_minutes
        daily[(row.staff_id, booking.day)] += booking.session.duration_minutes
        if booking.assigned_venue_id is not None:
            for identity, footprint in footprints.items():
                if footprint & footprints[booking.assigned_venue_id]:
                    calendar = calendars[identity]
                    calendar.blocked_periods = [*(calendar.blocked_periods or ()),
                        VenueDayInterval(day=booking.day, start_minute=booking.start_minute, end_minute=booking.end_minute)]
    for row in staff.availability:
        if row.weekly_teaching_max_minutes is not None:
            row.weekly_teaching_max_minutes = max(0, row.weekly_teaching_max_minutes-weekly[row.staff_id])
        # The original cap is uniform across days. Use the tightest residual,
        # conservatively: never increase a cap or misclaim original infeasibility.
        if row.daily_teaching_max_minutes is not None:
            row.daily_teaching_max_minutes = max(0, row.daily_teaching_max_minutes-max(
                (minutes for (identity, _), minutes in daily.items() if identity==row.staff_id), default=0))
    contexts.update(staff_input=type(staff).model_validate(staff.model_dump()),
                    venue_input=type(venues).model_validate(venues.model_dump()))
    return replace(enrolled, batch=replace(enrolled.batch, solver_inputs=contexts))


def _supported(enrolled):
    contexts = enrolled.batch.solver_inputs
    if any(contexts.get(key) is not None for key in
           ("combination_input", "work_periods", "fairness_config", "consecutive_config")):
        raise ValueError("Decomposition does not support joint delivery/protected-period/fairness/consecutive decisions; use the monolithic solver.")
    streams_by_student = defaultdict(set)
    groups = {row.group_id: row for row in enrolled.batch.plan.groups}
    for roster in enrolled.group_memberships:
        for student in roster.student_ids:
            streams_by_student[student].add(groups[roster.group_id].stream)
    if any(len(value)>1 for value in streams_by_student.values()):
        raise ValueError("Cross-stream explicit student attendance needs the monolithic solver.")


def validated_union(enrolled, bookings, runtime):
    """Rebuild global assessments, then run the original validation gateway."""
    from schedence_teaching.workload.assessment import _assess_validated_schedule
    from schedence_teaching.preferences.scoring import _score_validated_timetable
    from schedence_teaching.preferences.results import PreferenceSatisfaction
    from schedence_teaching.solver.preference_inputs import checked_preference_inputs
    from schedence_teaching.solver.preference_constraints import ModeledPreferencePenalty
    from schedence_teaching.solver.preference_results import preference_report
    from schedence_teaching.solver.deliveries import delivery_alternatives
    from schedence_teaching.solver.eligibility import candidate_pools
    from schedence_teaching.solver.lunch_inputs import checked_student_lunch_inputs
    from schedence_teaching.solver.lunch_results import student_lunch_report
    from schedence_teaching.lunch import assess_student_lunch
    from .elective_validation import validate_elective_result

    batch = enrolled.batch
    contexts = batch.solver_inputs
    summary = validate_timetable(batch.prepared, batch.config, bookings,
        venue_input=contexts["venue_input"], staff_input=contexts["staff_input"])
    result = SolverResult(SolverStatus.FEASIBLE, tuple(bookings), runtime, summary,
        venue_enabled=True, staff_policy_enabled=True, original_occurrence_count=len(batch.prepared.sessions))
    result = replace(result, workload_report=_assess_validated_schedule(batch.prepared, result,
        contexts["staff_input"].workload_input, staff_constraints_enforced=True))
    if "preference_input" in contexts:
        context = checked_preference_inputs(batch.prepared, batch.config, contexts["preference_input"],
            contexts["preference_calendar"], contexts["preference_policy"], contexts["preference_config"],
            staff_input=contexts["staff_input"])
        score = _score_validated_timetable(batch.prepared, batch.config, result, context.preferences,
            context.calendar, context.policy, staff_input=contexts["staff_input"])
        modeled = tuple(ModeledPreferencePenalty(row.delivery_id, row.staff_id, row.preference_ids,
            row.satisfaction is PreferenceSatisfaction.VIOLATED, row.penalty) for row in score.evaluations)
        result = replace(result, preference_optimization_enabled=True)
        result = replace(result, preference_report=preference_report(batch.prepared, batch.config,
            result, context, modeled, None, staff_input=contexts["staff_input"]))
    if "student_lunch_config" in contexts:
        original = {key:value for key,value in contexts.items() if not key.startswith("student_lunch_")}
        assessment = assess_student_lunch(batch.prepared, batch.config, result,
            contexts["student_lunch_calendar"], contexts["student_lunch_policy"], **original)
        alternatives = delivery_alternatives(batch.prepared, None, pools=candidate_pools(batch.prepared.input_snapshot))
        context = checked_student_lunch_inputs(batch.prepared, batch.config, contexts["student_lunch_policy"],
            contexts["student_lunch_calendar"], contexts["student_lunch_config"], alternatives,
            staff_input=contexts["staff_input"], preference_calendar=contexts.get("preference_calendar"))
        result = replace(result, student_lunch_enabled=True, student_lunch_report=student_lunch_report(assessment, context))
    validate_elective_result(enrolled, result)
    return result


def solve_coordinated_streams(enrolled, *, stream_order=(Stream.WEEKEND, Stream.WEEKDAY), seconds_per_part=120.0):
    _supported(enrolled)
    started = perf_counter()
    bookings, stages = [], []
    streams = {row.stream for row in enrolled.batch.plan.cohorts}
    if len(set(stream_order)) != len(stream_order) or not streams.issubset(stream_order):
        raise ValueError("Stream order must cover all selected streams exactly once.")
    for stream in stream_order:
        if stream not in streams:
            continue
        keys = {(row.academic_year, row.semester, row.stream.value, row.specialization)
                for row in enrolled.batch.plan.cohorts if row.stream==stream}
        part = select_enrolled_scope(enrolled, keys, max_time_seconds=seconds_per_part)
        reserved = reserve_previous_bookings(part, bookings)
        result = solve_timetable(reserved.batch.prepared, reserved.batch.config, **reserved.batch.solver_inputs)
        stages.append(dict(stream=stream.value, native_status=result.status.value,
            required=len(part.batch.prepared.sessions), scheduled=len(result.schedule), runtime_seconds=result.runtime_seconds,
            previous_bookings_reserved=len(bookings)))
        print(json.dumps(dict(stage="COORDINATED_STREAM", **stages[-1])), flush=True)
        if result.status not in {SolverStatus.OPTIMAL, SolverStatus.FEASIBLE}:
            contexts = enrolled.batch.solver_inputs
            return SolverResult(SolverStatus.UNKNOWN, (), perf_counter()-started, None,
                "Coordinated decomposition found no complete union; restricted-part failure is not original infeasibility.",
                venue_enabled=True, staff_policy_enabled=True,
                preference_optimization_enabled="preference_input" in contexts,
                student_lunch_enabled="student_lunch_config" in contexts,
                original_occurrence_count=len(enrolled.batch.prepared.sessions)), stages
        bookings.extend(result.schedule)
    return validated_union(enrolled, bookings, perf_counter()-started), stages


def solve_coordinated_cohorts(enrolled, *, seconds_per_part=30.0):
    """Smaller native subproblems; all previous bookings remain reserved.

    Constrained Weekend cohorts and larger physical demands go first. Original
    cohort sessions are never split or omitted. This is a search heuristic,
    not a new campus policy, proof of optimality or proof of infeasibility.
    """
    _supported(enrolled)
    batch = enrolled.batch
    cohorts = {row.cohort_id: row for row in batch.plan.cohorts}
    student_cohorts = defaultdict(set)
    for roster in enrolled.group_memberships:
        for student in roster.student_ids:
            student_cohorts[student].add(batch.plan.cohort_by_group[roster.group_id])
    if any(len(value)>1 for value in student_cohorts.values()):
        raise ValueError("Cross-cohort explicit attendance needs the monolithic solver.")
    for group in batch.plan.groups:
        if group.parent_group_id and batch.plan.cohort_by_group[group.group_id]!=batch.plan.cohort_by_group[group.parent_group_id]:
            raise ValueError("Cross-cohort hierarchy needs the monolithic solver.")
    physical = Counter()
    for session in batch.prepared.sessions:
        if session.session_type.value!="LECTURE":
            physical[batch.plan.cohort_by_group[session.group_id]] += session.duration_minutes
    order = sorted(cohorts, key=lambda identity: (
        cohorts[identity].stream!=Stream.WEEKEND, -physical[identity], identity))
    started = perf_counter()
    bookings, stages = [], []
    for identity in order:
        cohort = cohorts[identity]
        keys = {(cohort.academic_year, cohort.semester, cohort.stream.value, cohort.specialization)}
        part = select_enrolled_scope(enrolled, keys, max_time_seconds=seconds_per_part)
        reserved = reserve_previous_bookings(part, bookings)
        result = solve_timetable(reserved.batch.prepared, reserved.batch.config, **reserved.batch.solver_inputs)
        stages.append(dict(cohort_id=identity, native_status=result.status.value,
            required=len(part.batch.prepared.sessions), scheduled=len(result.schedule),
            runtime_seconds=result.runtime_seconds, previous_bookings_reserved=len(bookings)))
        print(json.dumps(dict(stage="COORDINATED_COHORT", **stages[-1])), flush=True)
        if result.status not in {SolverStatus.OPTIMAL,SolverStatus.FEASIBLE}:
            contexts = batch.solver_inputs
            return SolverResult(SolverStatus.UNKNOWN, (), perf_counter()-started, None,
                "Coordinated cohort search found no complete union; restricted-part failure is not original infeasibility.",
                venue_enabled=True, staff_policy_enabled=True,
                preference_optimization_enabled="preference_input" in contexts,
                student_lunch_enabled="student_lunch_config" in contexts,
                original_occurrence_count=len(batch.prepared.sessions)), stages
        bookings.extend(result.schedule)
    return validated_union(enrolled, bookings, perf_counter()-started), stages
