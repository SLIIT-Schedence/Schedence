"""Independent venue checks in original minutes, without CP-SAT candidate domains."""

from itertools import combinations

from schedence_teaching.preparation import UnscheduledSession

from .results import ScheduledSession, ScheduleIssue

VENUE_CHECKS = (
    "assigned_venue_exists", "venue_capacity", "venue_type_and_features", "venue_restrictions",
    "resolved_booking_relationships", "venue_availability", "venue_blocked_periods",
    "venue_clashes", "shared_space_clashes",
)


def venue_schedule_issues(prepared, inputs, schedule, *, delivery_ids=None, delivery_counts=None, config=None):
    venues = {venue.venue_id: venue for venue in inputs.venues}
    groups = {group.group_id: group for group in prepared.input_snapshot.student_groups}
    expected = {session.occurrence_id: session for session in prepared.sessions}
    rules = {rule.requirement_id: rule for rule in inputs.requirements}
    calendars = {calendar.venue_id: calendar for calendar in inputs.calendars}
    graph = {relation.combined_venue_id: relation.component_venue_ids for relation in inputs.combined_venues}
    # Traverse explicit relationships here, independently of the model footprint builder.
    dependencies, resources = {}, {}
    for venue_id in venues:
        seen, pending = set(), [venue_id]
        while pending:
            current = pending.pop()
            if current not in seen:
                seen.add(current)
                pending.extend(graph.get(current, ()))
        dependencies[venue_id] = seen
        resources[venue_id] = {("venue", component) for component in seen}
        for shared in inputs.shared_resources:
            if seen.intersection(shared.venue_ids):
                resources[venue_id].add(("shared", shared.resource_id))
    issues, timed = [], []
    for item in schedule:
        if (not isinstance(item, ScheduledSession) or not isinstance(item.session, UnscheduledSession)
                or type(item.session.occurrence_id) is not str):
            continue  # Core validator reports malformed records and coverage.
        session = expected.get(item.session.occurrence_id)
        if session is None:
            continue
        identity = session.occurrence_id

        def issue(code, message):
            issues.append(ScheduleIssue(code, message, (identity,)))

        venue_id = item.assigned_venue_id
        # Under batch online days the core validator proves each stored booking mode.
        if (config is not None and config.batch_online_days is not None and getattr(item, "delivery_mode", None) is not None
                and item.delivery_mode.value == "ONLINE") or (
                config is not None and config.delivery_calendar is not None and type(item.day) is str and any(
                row.day == item.day and row.stream == session.stream and row.mode.value == "ONLINE" for row in config.delivery_calendar.days)):
            if venue_id is not None:
                issue("online_physical_venue", "Online bookings must not consume physical venues.")
            continue
        if type(venue_id) is not str or not venue_id.strip():
            issue("invalid_venue_id", "Venue-enabled occurrences require exactly one nonblank venue ID.")
            continue
        venue = venues.get(venue_id)
        if venue is None:
            issue("unknown_venue", "Assigned venue does not exist in the supplied venue dataset.")
            continue
        if dependencies[venue_id].intersection(inputs.unresolved_venue_ids):
            issue("unresolved_venue", "Assigned venue or a component has unresolved booking relationships.")
        student_count = (delivery_counts[identity] if delivery_counts is not None
                         else groups[session.group_id].student_count)
        if venue.capacity < student_count:
            issue("venue_capacity", "Venue capacity is below the validated delivery's student count.")
        rule = rules.get(session.requirement_id)
        if rule:
            if rule.allowed_venue_types is not None and venue.venue_type not in rule.allowed_venue_types:
                issue("venue_type", "Venue type does not satisfy the explicit requirement.")
            if not rule.required_features.issubset(inputs.venue_features.get(venue_id, set())):
                issue("venue_features", "Explicit venue data does not support the required features/equipment.")
            if rule.required_lab_specialization is not None and venue.lab_specialization != rule.required_lab_specialization:
                issue("venue_specialization", "Venue lab specialization does not match the explicit requirement.")
            if ((rule.eligible_venue_ids is not None and venue_id not in rule.eligible_venue_ids)
                    or venue_id in rule.excluded_venue_ids):
                issue("venue_restriction", "Venue violates an explicit eligible/excluded venue restriction.")
        if type(item.start_minute) is not int or type(item.end_minute) is not int or type(item.day) is not str:
            continue
        for component in sorted(dependencies[venue_id]):
            calendar = calendars.get(component)
            if calendar is None or not any(
                window.start_minute <= item.start_minute and item.end_minute <= window.end_minute
                for window in (calendar.available_windows or {}).get(item.day, [])
            ):
                issue("outside_venue_availability", "Session is outside a venue/component's explicit available window.")
        for other_id, calendar in calendars.items():
            if resources[venue_id].intersection(resources[other_id]) and any(
                block.day == item.day and item.start_minute < block.end_minute and block.start_minute < item.end_minute
                for block in calendar.blocked_periods or ()
            ):
                issue("venue_blocked_period", "Session overlaps an explicit venue/shared-space blocked period.")
        timed.append((item, session))
    for (first, first_session), (second, second_session) in combinations(timed, 2):
        if delivery_ids is not None and delivery_ids[first_session.occurrence_id] == delivery_ids[second_session.occurrence_id]:
            continue
        if first.day != second.day:
            continue
        if first.start_minute < second.end_minute and second.start_minute < first.end_minute:
            if first.assigned_venue_id == second.assigned_venue_id:
                issues.append(ScheduleIssue("venue_clash", "Venue is assigned overlapping sessions.",
                                            (first_session.occurrence_id, second_session.occurrence_id)))
            elif resources[first.assigned_venue_id].intersection(resources[second.assigned_venue_id]):
                issues.append(ScheduleIssue("shared_space_clash", "Explicit shared-space resources are booked simultaneously.",
                                            (first_session.occurrence_id, second_session.occurrence_id)))
    return issues
