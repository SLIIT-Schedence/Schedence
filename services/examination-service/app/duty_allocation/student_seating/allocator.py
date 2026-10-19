"""Give each student Member 2 placed in a room one usable seat in that same room.

Students are seated in IT number order. When the room's exam capacity is below its usable
seats, the capacity's share of seats is spread across the hall (at 50% roughly every
other seat), so the reduced capacity actually leaves gaps; otherwise seats fill in order.
A room is never filled beyond its exam capacity, and students are never moved.
"""

from __future__ import annotations

from app.duty_allocation.student_seating.types import Hall, LayoutStatus, RoomCapacity, Seat, SeatAssignment
from app.scheduling.contracts import Violation

MISSING_VENUE = "S_MISSING_VENUE"
NO_CAPACITY_RULE = "S_NO_CAPACITY_RULE"
OVER_CAPACITY = "S_OVER_CAPACITY"


def seats_to_use(hall: Hall, limit: int, students: int) -> list[Seat]:
    """students seats out of the usable ones, spaced as if the room were filled to limit."""
    usable = list(hall.usable_seats)
    if limit <= 0 or students == 0:
        return []
    positions = [i * len(usable) // limit for i in range(limit)]
    return [usable[p] for p in positions[:students]]


def seat_room(session, room, hall: Hall, capacity: RoomCapacity) -> tuple[list[SeatAssignment], list[Violation]]:
    where = f"session {session.session_id} hall {room.venue_id}"
    if capacity.limit is None:
        return [], [Violation(NO_CAPACITY_RULE, f"{where}: no exam capacity percentage configured for venue "
                                                f"type {capacity.venue_type!r}.")]
    if capacity.students > capacity.limit:
        return [], [Violation(OVER_CAPACITY, f"{where}: Member 2 placed {capacity.students} students; limit is "
                                             f"{capacity.limit} (exam capacity {capacity.exam_capacity}, "
                                             f"usable seats {capacity.usable_seats}). Not seated.")]
    students = sorted(seat.student_id for seat in room.seats)
    verified = hall.status is LayoutStatus.VERIFIED
    return [SeatAssignment(student_id, session.module_code, session.session_id, session.start, session.end,
                           room.venue_id, seat.number, seat.label, verified)
            for student_id, seat in zip(students, seats_to_use(hall, capacity.limit, len(students)))], []
