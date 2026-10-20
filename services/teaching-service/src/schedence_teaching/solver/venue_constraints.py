"""CP-SAT venue candidate domains from explicit capacity, policies and calendars."""

from schedence_teaching.preparation import PreparedTimetableInput, UnscheduledSession
from schedence_teaching.schemas import Venue

from .config import MINUTES_PER_DAY, SolverConfig
from .venue_inputs import VenueSchedulingInput, venue_dependencies, venue_footprints
from .attendance_inputs import DeliveryMode, mode_on_day, permitted_day


def eligible_venue(session: UnscheduledSession, venue: Venue, prepared: PreparedTimetableInput,
                   inputs: VenueSchedulingInput) -> bool:
    dependencies = venue_dependencies(inputs)
    if dependencies[venue.venue_id].intersection(inputs.unresolved_venue_ids):
        return False
    groups = {group.group_id: group for group in prepared.input_snapshot.student_groups}
    if venue.capacity < groups[session.group_id].student_count:
        return False
    rule = next((rule for rule in inputs.requirements if rule.requirement_id == session.requirement_id), None)
    if rule is None:
        return True
    return (
        (rule.allowed_venue_types is None or venue.venue_type in rule.allowed_venue_types)
        and (rule.eligible_venue_ids is None or venue.venue_id in rule.eligible_venue_ids)
        and venue.venue_id not in rule.excluded_venue_ids
        and rule.required_features.issubset(inputs.venue_features.get(venue.venue_id, set()))
        and (rule.required_lab_specialization is None or venue.lab_specialization == rule.required_lab_specialization)
    )


def venue_start_domains(session: UnscheduledSession, venue_id: str, inputs: VenueSchedulingInput,
                        config: SolverConfig) -> list[list[int]]:
    dependencies = venue_dependencies(inputs)
    footprints = venue_footprints(inputs)
    calendars = {calendar.venue_id: calendar for calendar in inputs.calendars}
    granularity = config.granularity_minutes
    stride = (MINUTES_PER_DAY + granularity - 1) // granularity
    domains = []
    for index, day in enumerate(config.days):
        if (session.stream not in day.windows or not permitted_day(config, session, day.day)
                or mode_on_day(config, day.day, session.stream) != DeliveryMode.PHYSICAL):
            continue
        windows = [(window.start_minute, window.end_minute) for window in day.windows.get(session.stream, [])]
        # Combined bookings must fit their own and every component's availability.
        for component in sorted(dependencies[venue_id]):
            available = calendars[component].available_windows.get(day.day, [])
            windows = [(max(start, window.start_minute), min(end, window.end_minute))
                       for start, end in windows for window in available
                       if max(start, window.start_minute) < min(end, window.end_minute)]
        for other_id, calendar in calendars.items():
            if not footprints[venue_id].intersection(footprints[other_id]):
                continue
            for block in calendar.blocked_periods or ():
                if block.day != day.day:
                    continue
                remaining = []
                for start, end in windows:
                    if block.end_minute <= start or end <= block.start_minute:
                        remaining.append((start, end))
                    else:
                        if start < block.start_minute:
                            remaining.append((start, block.start_minute))
                        if block.end_minute < end:
                            remaining.append((block.end_minute, end))
                windows = remaining
        domains.extend([
            index * stride + start // granularity,
            index * stride + (end - session.duration_minutes) // granularity,
        ] for start, end in windows if end - start >= session.duration_minutes)
    return domains
