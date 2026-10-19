"""Student seat allocation types (Member 3): halls, seats and the seating plan.

A hall is an ordered list of physical seats. Seat numbers are 1-based positions in that
order; labels are what is printed (for example "12" or "B4"). Blocked seats exist but are
never used. Only a VERIFIED hall describes a real, checked venue layout: PROVISIONAL seats
were numbered automatically without a layout, and SYNTHETIC halls are development fixtures.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import Enum

from app.scheduling.contracts import SchedulingValidationError, Violation, frozen_sequence

SEATING_INPUT = "S_INPUT"


def _fail(message: str) -> None:
    raise SchedulingValidationError((Violation(SEATING_INPUT, message),))


def _text(value: object, label: str) -> None:
    if type(value) is not str or not value.strip():
        _fail(f"{label} must be a non-empty string.")


class LayoutStatus(str, Enum):
    VERIFIED = "VERIFIED"  # a real venue layout, confirmed by its source
    PROVISIONAL = "PROVISIONAL"  # seats numbered 1..N automatically; not checked against the room
    SYNTHETIC = "SYNTHETIC"  # development or test fixture; never a real venue


@dataclass(frozen=True)
class Seat:
    number: int  # 1-based position in the hall's seat order
    label: str
    row: int | None = None  # only for halls configured as rows x columns
    column: int | None = None

    def __post_init__(self) -> None:
        if type(self.number) is not int or self.number < 1:
            _fail("seat number must be a positive int.")
        _text(self.label, "seat label")


@dataclass(frozen=True)
class Hall:
    venue_id: str  # Member 2's Venue.venue_id
    seats: tuple[Seat, ...]
    blocked: tuple[int, ...]  # seat numbers that must not be used
    source: str
    status: LayoutStatus

    def __post_init__(self) -> None:
        _text(self.venue_id, "hall venue_id")
        _text(self.source, f"hall {self.venue_id} source")
        if type(self.status) is not LayoutStatus:
            _fail(f"hall {self.venue_id} status must be a LayoutStatus.")
        seats = frozen_sequence(self.seats, f"hall {self.venue_id} seats", Seat)
        if not seats or [s.number for s in seats] != list(range(1, len(seats) + 1)):
            _fail(f"hall {self.venue_id} seats must be numbered 1..n in order.")
        if len({s.label for s in seats}) != len(seats):
            _fail(f"hall {self.venue_id} seat labels must be unique.")
        blocked = tuple(sorted(frozen_sequence(self.blocked, f"hall {self.venue_id} blocked", int)))
        if len(set(blocked)) != len(blocked) or any(not 1 <= n <= len(seats) for n in blocked):
            _fail(f"hall {self.venue_id} blocked seats must be unique existing seat numbers.")
        object.__setattr__(self, "seats", seats)
        object.__setattr__(self, "blocked", blocked)

    @property
    def usable_seats(self) -> tuple[Seat, ...]:
        blocked = set(self.blocked)
        return tuple(s for s in self.seats if s.number not in blocked)


@dataclass(frozen=True)
class SeatAssignment:
    student_id: str  # IT number, from Member 2's SeatAllocation
    module_code: str
    session_id: str
    exam_start: dt.datetime
    exam_end: dt.datetime
    venue_id: str  # hall, exactly as Member 2 allocated it
    seat_number: int
    seat_label: str
    seat_verified: bool  # False unless the hall layout is VERIFIED: number is provisional or synthetic


@dataclass(frozen=True)
class RoomCapacity:
    """Capacity check for one room Member 2 used."""
    session_id: str
    venue_id: str
    venue_type: str
    full_capacity: int
    percentage: int | None  # None: no rule configured for this venue type
    exam_capacity: int | None  # floor(full x percentage / 100)
    usable_seats: int
    students: int  # placed there by Member 2

    @property
    def limit(self) -> int | None:
        return None if self.exam_capacity is None else min(self.exam_capacity, self.usable_seats)


class SeatingStatus(str, Enum):
    COMPLETE = "COMPLETE"  # every student seated; independent validation passed
    INCOMPLETE = "INCOMPLETE"  # some rooms could not be seated; see violations
    INVALID = "INVALID"  # bad input, or validation rejected the plan


@dataclass(frozen=True)
class SeatingPlan:
    period_code: str
    capacity_rules_source: str
    provisional_halls: tuple[str, ...]  # seat numbers generated automatically; unverified
    synthetic_halls: tuple[str, ...]  # seated from synthetic fixtures; not real venues
    assignments: tuple[SeatAssignment, ...]

    @property
    def all_seats_verified(self) -> bool:
        return all(a.seat_verified for a in self.assignments)


@dataclass(frozen=True)
class SeatingResult:
    status: SeatingStatus
    plan: SeatingPlan | None = None  # only for COMPLETE
    rooms: tuple[RoomCapacity, ...] = ()
    violations: tuple[Violation, ...] = ()
    diagnostics: tuple[str, ...] = ()
