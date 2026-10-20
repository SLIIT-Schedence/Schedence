"""Unified main-group views over existing validated booking contracts.

The same booking is shown once even when it covers several attendance groups.
Parallel sibling teaching remains separate bookings on the same main-group page.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from schedence_teaching.preparation import build_group_hierarchy
from schedence_teaching.schemas import StudentGroup


def booking_identity(booking):
    return booking.delivery_id if hasattr(booking, 'covered_sessions') else booking.session.occurrence_id


def booking_groups(booking):
    return tuple(booking.group_ids) if hasattr(booking, 'covered_sessions') else (booking.session.group_id,)


@dataclass(frozen=True)
class MainGroupTimetable:
    main_group_id: str
    subgroup_ids: tuple[str, ...]
    labels: dict[str, str]
    bookings: tuple


def main_group_timetables(groups: Sequence[StudentGroup], bookings: Sequence) -> tuple[MainGroupTimetable, ...]:
    """All descendants are visible; unknown/duplicate booking references fail.

    This is a presentation index, not a replacement for independent validation.
    It deliberately allows simultaneous, independently valid sibling sessions.
    """
    hierarchy = build_group_hierarchy(groups)
    bookings = tuple(bookings)
    identities = [booking_identity(b) for b in bookings]
    if len(set(identities)) != len(identities):
        raise ValueError('Duplicate actual booking identities are not valid presentation input.')
    for booking in bookings:
        attendees = booking_groups(booking)
        if not attendees or len(set(attendees)) != len(attendees) or set(attendees) - set(hierarchy.parents):
            raise ValueError('Booking references unknown or duplicate attendance groups.')
    # Number within period/stream/academic cohort namespace supplied by caller's
    # ordering, while authoritative identities remain unchanged in labels/exports.
    answer = []
    for index, main_id in enumerate(hierarchy.main_group_ids, 1):
        descendants = hierarchy.descendants(main_id)
        labels = {main_id: f'G{index}'}
        stack = [main_id]
        while stack:
            current = stack.pop()
            for child_index, child in enumerate(hierarchy.children[current], 1):
                labels[child] = f'{labels[current]}.{child_index}'
                stack.append(child)
        population = {main_id, *descendants}
        selected = tuple(sorted((b for b in bookings if population.intersection(booking_groups(b))),
            key=lambda b: (b.day, b.start_minute, b.end_minute, booking_identity(b))))
        answer.append(MainGroupTimetable(main_id, descendants, labels, selected))
    return tuple(answer)


def subgroup_timetable(groups: Sequence[StudentGroup], bookings: Sequence, group_id: str) -> tuple:
    """Optional individual attendance view includes ancestors, excludes siblings."""
    hierarchy = build_group_hierarchy(groups)
    if group_id not in hierarchy.parents:
        raise ValueError('Unknown subgroup timetable filter.')
    ancestors, current = set(), group_id
    while current is not None:
        ancestors.add(current)
        current = hierarchy.parents[current]
    return tuple(b for b in bookings if ancestors.intersection(booking_groups(b)))


def parallel_booking_lanes(bookings: Sequence) -> tuple[tuple[object, int, int], ...]:
    """Allocate non-overlapping lanes per connected time cluster, per date/day.

    Each tuple is (original booking, lane index, cluster lane count). The PDF can
    place simultaneous sibling blocks side by side without changing any times.
    """
    by_day = {}
    for booking in bookings:
        if booking.end_minute <= booking.start_minute:
            raise ValueError('A displayed booking must have a positive time interval.')
        by_day.setdefault(booking.day, []).append(booking)
    result = []
    for day in sorted(by_day):
        ordered = sorted(by_day[day], key=lambda b: (b.start_minute, b.end_minute, booking_identity(b)))
        clusters, cluster, end = [], [], -1
        for booking in ordered:
            if cluster and booking.start_minute >= end:
                clusters.append(cluster)
                cluster = []
            cluster.append(booking)
            end = max(end, booking.end_minute) if len(cluster) > 1 else booking.end_minute
        if cluster:
            clusters.append(cluster)
        for cluster in clusters:
            ends, placed = [], []
            for booking in cluster:
                lane = next((i for i, value in enumerate(ends) if value <= booking.start_minute), len(ends))
                if lane == len(ends):
                    ends.append(booking.end_minute)
                else:
                    ends[lane] = booking.end_minute
                placed.append((booking, lane))
            result.extend((booking, lane, len(ends)) for booking, lane in placed)
    return tuple(result)
