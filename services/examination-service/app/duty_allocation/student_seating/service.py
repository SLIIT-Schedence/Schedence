"""Seat every student of a Member 2 draft inside the hall Member 2 chose for them.

Inputs are read only: Member 2's DraftTimetable and Venue records, plus optionally their
CapacitySnapshot to report where the capacity they scheduled with differs from this
component's capacity rules. Room occupancy and the timetable are never changed.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.duty_allocation.student_seating.allocator import MISSING_VENUE, seat_room
from app.duty_allocation.student_seating.layout import provisional_hall
from app.duty_allocation.student_seating.policy import CapacityRules
from app.duty_allocation.student_seating.types import (
    SEATING_INPUT, Hall, LayoutStatus, RoomCapacity, SeatingPlan, SeatingResult, SeatingStatus,
)
from app.duty_allocation.student_seating.validation import validate_seating
from app.scheduling.contracts import SchedulingValidationError, Violation, frozen_sequence
from app.scheduling.types import DraftTimetable
from app.seating.snapshot import CapacitySnapshot
from app.sources.types import Venue


def _invalid(*messages: str) -> SeatingResult:
    return SeatingResult(SeatingStatus.INVALID, violations=tuple(Violation(SEATING_INPUT, m) for m in messages),
                         diagnostics=("Invalid seating input.",))


def plan_student_seating(draft: DraftTimetable, venues: Iterable[Venue], rules: CapacityRules,
                         halls: Iterable[Hall] = (), member2_capacities: CapacitySnapshot | None = None) -> SeatingResult:
    """halls: layouts where known; other venues get PROVISIONAL seats numbered 1..full capacity."""
    if type(draft) is not DraftTimetable or type(rules) is not CapacityRules:
        return _invalid("plan_student_seating needs a DraftTimetable and CapacityRules.")
    if member2_capacities is not None and type(member2_capacities) is not CapacitySnapshot:
        return _invalid("member2_capacities must be Member 2's CapacitySnapshot.")
    try:
        venues = {v.venue_id: v for v in frozen_sequence(venues, "venues", Venue)}
        halls = {h.venue_id: h for h in frozen_sequence(halls, "halls", Hall)}
    except SchedulingValidationError as exc:
        return _invalid(*(v.message for v in exc.violations))

    diagnostics, violations, rooms, assignments = [], [], [], []
    used = sorted({r.venue_id for s in draft.sessions for r in s.rooms})
    for venue_id in used:
        if venue_id in venues and venue_id not in halls:
            halls[venue_id] = provisional_hall(venue_id, venues[venue_id].normal_capacity)
    provisional = tuple(v for v in used if v in halls and halls[v].status is LayoutStatus.PROVISIONAL)
    synthetic = tuple(v for v in used if v in halls and halls[v].status is LayoutStatus.SYNTHETIC)
    if provisional:
        diagnostics.append(f"PROVISIONAL seat numbers (auto-numbered, unverified; no physical layout) for "
                           f"{', '.join(provisional)}.")
    if synthetic:
        diagnostics.append(f"SYNTHETIC hall layouts used for {', '.join(synthetic)}: not real venue layouts.")

    for session in sorted(draft.sessions, key=lambda s: (s.start, s.session_id)):
        for room in sorted(session.rooms, key=lambda r: r.venue_id):
            venue = venues.get(room.venue_id)
            if venue is None:
                violations.append(Violation(MISSING_VENUE, f"session {session.session_id}: no venue record for "
                                                           f"{room.venue_id}."))
                continue
            hall = halls[room.venue_id]
            if len(hall.seats) != venue.normal_capacity:
                diagnostics.append(f"{venue.venue_id}: hall has {len(hall.seats)} seats; Member 2's full capacity "
                                   f"is {venue.normal_capacity}.")
            capacity = RoomCapacity(session.session_id, venue.venue_id, venue.venue_type, venue.normal_capacity,
                                    rules.percentage(venue.venue_type),
                                    rules.exam_capacity(venue.venue_type, venue.normal_capacity),
                                    len(hall.usable_seats), len(room.seats))
            rooms.append(capacity)
            if member2_capacities is not None and capacity.exam_capacity is not None:
                try:
                    theirs = member2_capacities.exam_capacity(venue.venue_id)
                except KeyError:
                    theirs = None
                if theirs != capacity.exam_capacity:
                    diagnostics.append(f"{session.session_id}/{venue.venue_id} ({venue.venue_type}): Member 2 scheduled "
                                       f"with exam capacity {theirs}, configured {capacity.exam_capacity}; "
                                       f"{len(room.seats)} students kept as allocated.")
            seated, problems = seat_room(session, room, hall, capacity)
            assignments += seated
            violations += problems

    if violations:
        return SeatingResult(SeatingStatus.INCOMPLETE, rooms=tuple(rooms), violations=tuple(violations),
                             diagnostics=tuple(dict.fromkeys(diagnostics)) + ("No seating plan until every hall is seated.",))
    plan = SeatingPlan(draft.period_code, rules.source, provisional, synthetic, tuple(assignments))
    problems = validate_seating(draft, halls, venues, rules, plan)
    if problems:
        return SeatingResult(SeatingStatus.INVALID, rooms=tuple(rooms), violations=problems,
                             diagnostics=tuple(dict.fromkeys(diagnostics)) + ("Independent validation rejected the plan.",))
    return SeatingResult(SeatingStatus.COMPLETE, plan, tuple(rooms), diagnostics=tuple(dict.fromkeys(diagnostics)))
