"""Independent check of a seating plan against Member 2's draft, the halls and capacities.

    S1  every student Member 2 placed in a hall has exactly one seat, in that same session
        and hall (none unseated, moved, invented or seated twice)
    S2  the seat exists in the hall (number and label match)
    S3  the seat is not blocked
    S4  no seat is used twice in one session and hall
    S5  students in a hall do not exceed its exam capacity or its usable seats
    S6  module and exam times match Member 2's session
    S7  a seat is marked verified only if its hall layout is VERIFIED
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping

from app.duty_allocation.student_seating.policy import CapacityRules
from app.duty_allocation.student_seating.types import Hall, LayoutStatus, SeatingPlan
from app.scheduling.contracts import Violation
from app.scheduling.types import DraftTimetable
from app.sources.types import Venue


def validate_seating(draft: DraftTimetable, halls: Mapping[str, Hall], venues: Mapping[str, Venue],
                     rules: CapacityRules, plan: SeatingPlan) -> tuple[Violation, ...]:
    errors: list[Violation] = []

    def error(code: str, message: str) -> None:
        errors.append(Violation(code, message))

    sessions = {s.session_id: s for s in draft.sessions}
    expected = {(s.session_id, r.venue_id, seat.student_id) for s in draft.sessions for r in s.rooms for seat in r.seats}
    seen = Counter((a.session_id, a.venue_id, a.student_id) for a in plan.assignments)
    for key, count in sorted(seen.items()):
        if key not in expected:
            error("S1", f"Student {key[2]} is seated in {key[0]}/{key[1]}, where Member 2 did not place them.")
        elif count > 1:
            error("S1", f"Student {key[2]} has {count} seats in {key[0]}/{key[1]}.")
    for key in sorted(expected - set(seen)):
        error("S1", f"Student {key[2]} in {key[0]}/{key[1]} has no seat.")

    rooms: dict[tuple[str, str], list] = defaultdict(list)
    for a in plan.assignments:
        rooms[a.session_id, a.venue_id].append(a)
        s = sessions.get(a.session_id)
        if s is not None and (a.module_code, a.exam_start, a.exam_end) != (s.module_code, s.start, s.end):
            error("S6", f"Student {a.student_id}: module or exam time differs from Member 2's session {a.session_id}.")

    for (session_id, venue_id), placed in sorted(rooms.items()):
        hall, venue = halls.get(venue_id), venues.get(venue_id)
        if hall is None or venue is None:
            error("S2", f"No hall or venue record for {venue_id} used in {session_id}.")
            continue
        seats = {s.number: s for s in hall.seats}
        blocked = set(hall.blocked)
        for number, count in Counter(a.seat_number for a in placed).items():
            if count > 1:
                error("S4", f"Seat {number} in {session_id}/{venue_id} is used {count} times.")
        for a in placed:
            seat = seats.get(a.seat_number)
            if seat is None or seat.label != a.seat_label:
                error("S2", f"Seat {a.seat_number} ({a.seat_label}) does not exist in hall {venue_id}.")
            elif a.seat_number in blocked:
                error("S3", f"Student {a.student_id} sits in blocked seat {a.seat_label} of {venue_id}.")
            if a.seat_verified != (hall.status is LayoutStatus.VERIFIED):
                error("S7", f"Seat {a.seat_label} of {venue_id} is marked verified={a.seat_verified}, "
                            f"but the hall layout is {hall.status.value}.")
        capacity = rules.exam_capacity(venue.venue_type, venue.normal_capacity)
        usable = len(hall.seats) - len(blocked)
        if capacity is None or len(placed) > min(capacity, usable):
            error("S5", f"{session_id}/{venue_id} seats {len(placed)} students; exam capacity "
                        f"{capacity}, usable seats {usable}.")
    return tuple(errors)
