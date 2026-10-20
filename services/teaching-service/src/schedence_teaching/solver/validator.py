"""Independent validation using minutes and direct parent traversal, without CP-SAT."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import replace
from itertools import combinations

from schedence_teaching.preparation import PreparedTimetableInput, UnscheduledSession

from .config import MINUTES_PER_DAY, SolverConfig
from .inputs import checked_inputs
from .results import ScheduledSession, ScheduleIssue, TimetableValidationError, ValidationSummary
from .venue_inputs import VenueSchedulingInput, checked_venue_inputs
from .venue_validation import VENUE_CHECKS, venue_schedule_issues
from .staff_inputs import checked_staff_inputs
from .staff_validation import STAFF_CHECKS, staff_schedule_issues
from .attendance_inputs import DeliveryMode, mode_on_day, permitted_day


def validate_timetable(
    prepared: PreparedTimetableInput, config: SolverConfig, schedule: Sequence[ScheduledSession],
    *, venue_input: VenueSchedulingInput | None = None, staff_input=None,
) -> ValidationSummary:
    """Raise on invalid output; successful validation covers only implemented rules."""
    return _validate_timetable(prepared, config, schedule, venue_input=venue_input, staff_input=staff_input)


def _validate_timetable(prepared, config, schedule, *, venue_input=None,
                        delivery_ids=None, delivery_counts=None, overlap_pairs=(), staff_input=None):
    """Shared checks; delivery context comes only from validated canonical alternatives."""
    prepared, config = checked_inputs(prepared, config)
    if venue_input is not None:
        venue_input = checked_venue_inputs(prepared, config, venue_input)
    if staff_input is not None:
        staff_input = checked_staff_inputs(prepared, config, staff_input)
    expected = {session.occurrence_id: session for session in prepared.sessions}
    days = {day.day: day for day in config.days}
    parents = {group.group_id: group.parent_group_id for group in prepared.input_snapshot.student_groups}
    staff_ids = {staff.staff_id for staff in prepared.input_snapshot.staff_members}
    # Check source memberships directly, independently of the model's candidate pools.
    eligible_pairs = {
        (member.offering_id, member.staff_id) for member in prepared.input_snapshot.teaching_team_members
    }
    issues = []
    rosters = {row.group_id: set(row.student_ids) for row in config.attendance_memberships}
    counts = Counter()
    timed = []
    online_context, online_selection = None, {}
    if config.batch_online_days is not None:
        from .online_day_inputs import batch_online_issues, booking_mode, checked_batch_online_days
        online_context = checked_batch_online_days(prepared, config, staff_input.day_dates if staff_input is not None else None)
        online_issues, online_selection = batch_online_issues(config, online_context, [item for item in schedule
            if isinstance(item, ScheduledSession) and isinstance(item.session, UnscheduledSession)
            and expected.get(item.session.occurrence_id) == item.session])
        issues.extend(ScheduleIssue(code, message, ids) for code, message, ids in online_issues)
    for item in schedule:
        if (not isinstance(item, ScheduledSession) or not isinstance(item.session, UnscheduledSession)
                or not isinstance(item.session.occurrence_id, str)):
            issues.append(ScheduleIssue("invalid_record", "Schedule entries require ScheduledSession and original occurrence metadata."))
            continue
        identity = item.session.occurrence_id
        counts[identity] += 1
        session = expected.get(identity)
        if session is None:
            issues.append(ScheduleIssue("unknown_occurrence", "Occurrence is absent from prepared demand.", (identity,)))
            continue
        if venue_input is None and item.assigned_venue_id is not None:
            issues.append(ScheduleIssue("unexpected_venue_assignment", "Venue assignments require venue-enabled validation.", (identity,)))
        if item.session != session or any(
            type(getattr(item.session, field)) is not type(value) for field, value in vars(session).items()
        ):
            issues.append(ScheduleIssue("metadata_changed", "Original occurrence metadata must be preserved.", (identity,)))
        if type(item.assigned_staff_id) is not str or not item.assigned_staff_id.strip():
            issues.append(ScheduleIssue("invalid_staff_id", "Exactly one nonblank staff ID is required.", (identity,)))
        elif item.assigned_staff_id not in staff_ids:
            issues.append(ScheduleIssue("unknown_staff", "Assigned staff is absent from the validated staff dataset.", (identity,)))
        elif (session.offering_id, item.assigned_staff_id) not in eligible_pairs:
            issues.append(ScheduleIssue("ineligible_staff", "Assigned staff has no teaching-team membership for this offering.", (identity,)))
        if not isinstance(item.day, str) or item.day not in days:
            issues.append(ScheduleIssue("invalid_day", "Day is not configured.", (identity,)))
        elif session.stream in days[item.day].windows:
            mode = (booking_mode(config, online_context, online_selection, session, item.day) if online_context is not None
                    else mode_on_day(config, item.day, session.stream))
            if not permitted_day(config, session, item.day):
                issues.append(ScheduleIssue("delivery_type_not_allowed", "Session type is not permitted on this stream's delivery day.", (identity,)))
            if config.delivery_calendar is not None and (type(item.delivery_mode) is not DeliveryMode or item.delivery_mode != mode):
                issues.append(ScheduleIssue("incorrect_delivery_mode", "Booking mode must match the explicit delivery calendar and batch online day.", (identity,)))
            if config.delivery_calendar is None and item.delivery_mode is not None:
                issues.append(ScheduleIssue("unexpected_delivery_mode", "An explicit booking mode requires its original delivery calendar.", (identity,)))
            if mode == DeliveryMode.ONLINE and item.assigned_venue_id is not None:
                issues.append(ScheduleIssue("online_physical_venue", "Online sessions must not book a physical venue.", (identity,)))
        if type(item.start_minute) is not int or type(item.end_minute) is not int:
            issues.append(ScheduleIssue("invalid_time", "Start/end minutes must be integers.", (identity,)))
            continue
        if not 0 <= item.start_minute < item.end_minute <= MINUTES_PER_DAY:
            issues.append(ScheduleIssue("invalid_time", "Time interval must remain within one day.", (identity,)))
        if item.end_minute - item.start_minute != session.duration_minutes:
            issues.append(ScheduleIssue("duration_changed", "End minus start must equal the supplied duration.", (identity,)))
        if item.start_minute % config.granularity_minutes or item.end_minute % config.granularity_minutes:
            issues.append(ScheduleIssue("unaligned_time", "Times must align with configured granularity.", (identity,)))
        if isinstance(item.day, str) and item.day in days and not any(
            window.start_minute <= item.start_minute and item.end_minute <= window.end_minute
            for window in days[item.day].windows.get(session.stream, [])
        ):
            issues.append(ScheduleIssue("outside_window", "Session must fit inside one allowed stream/day window.", (identity,)))
        timed.append((item, session))
    for identity in expected:
        if counts[identity] == 0:
            issues.append(ScheduleIssue("missing_occurrence", "Required occurrence is missing.", (identity,)))
    for identity, count in counts.items():
        if count > 1:
            issues.append(ScheduleIssue("duplicate_occurrence", "Occurrence must appear exactly once.", (identity,)))

    def ancestor(first, second):
        current = second
        while current is not None:
            if current == first:
                return True
            current = parents[current]
        return False

    checked_pairs = 0
    for (first, first_session), (second, second_session) in combinations(timed, 2):
        if delivery_ids is not None and delivery_ids[first_session.occurrence_id] == delivery_ids[second_session.occurrence_id]:
            continue  # Coverage copies of one booking, not simultaneous bookings.
        checked_pairs += 1
        if first.day != second.day:
            continue
        overlaps = first.start_minute < second.end_minute and second.start_minute < first.end_minute
        if (overlaps and type(first.assigned_staff_id) is str
                and first.assigned_staff_id in staff_ids and first.assigned_staff_id == second.assigned_staff_id):
            issues.append(ScheduleIssue("staff_clash", "One staff member is assigned overlapping sessions.",
                                        (first_session.occurrence_id, second_session.occurrence_id)))
        related = ancestor(first_session.group_id, second_session.group_id) or ancestor(second_session.group_id, first_session.group_id)
        if overlaps and rosters.get(first_session.group_id, set()) & rosters.get(second_session.group_id, set()):
            issues.append(ScheduleIssue("student_membership_clash", "Overlapping sessions share explicitly enrolled students.",
                                        (first_session.occurrence_id, second_session.occurrence_id)))
        if overlaps and related:
            code = "same_group_clash" if first_session.group_id == second_session.group_id else "ancestor_clash"
            issues.append(ScheduleIssue(code, "Overlapping sessions share a group or ancestor/descendant population.",
                                        (first_session.occurrence_id, second_session.occurrence_id)))
        elif overlaps and overlap_pairs:
            # Independently traverse the source hierarchy for explicit overlap evidence.
            known = any((ancestor(first_session.group_id, left) and ancestor(second_session.group_id, right))
                        or (ancestor(first_session.group_id, right) and ancestor(second_session.group_id, left))
                        for left, right in overlap_pairs)
            if known:
                issues.append(ScheduleIssue("known_population_clash", "Sessions share an explicitly known overlapping population.",
                                            (first_session.occurrence_id, second_session.occurrence_id)))
    if venue_input is not None:
        issues.extend(venue_schedule_issues(prepared, venue_input, schedule,
                                           delivery_ids=delivery_ids, delivery_counts=delivery_counts, config=config))
    if staff_input is not None:
        issues.extend(staff_schedule_issues(prepared, staff_input, schedule, delivery_ids=delivery_ids))
    if issues:
        raise TimetableValidationError(issues)
    summary = ValidationSummary(len(expected), len(schedule), checked_pairs)
    if config.attendance_memberships:
        summary = replace(summary, checks=summary.checks + ("explicit_student_membership_clashes",))
    if config.delivery_calendar is not None:
        summary = replace(summary, checks=summary.checks + ("online_physical_delivery_calendar", "online_no_physical_venue"))
    if online_context is not None:
        summary = replace(summary, checks=summary.checks + ("batch_online_day_selection",))
    if venue_input is not None:
        summary = replace(summary, checks=summary.checks + VENUE_CHECKS, venue_enabled=True)
    if staff_input is not None:
        summary = replace(summary, checks=summary.checks + STAFF_CHECKS, staff_policy_enabled=True)
        if staff_input.assignment_policy is not None:
            summary=replace(summary,checks=summary.checks+('rank_appointment_session_eligibility',
                'explicit_session_type_permissions','weekly_teaching_day_limit_across_streams_and_modes'))
    return summary
