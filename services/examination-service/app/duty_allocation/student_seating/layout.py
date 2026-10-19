"""Build halls. No real venue layout is built in.

numbered_hall: seats 1..n labelled by number; needs only the seat count.
grid_hall:     rows x columns, labelled "{row_label}{column}", numbered row by row;
               use it only when a venue's real row layout is known.
Every hall states its LayoutStatus; only a confirmed real layout may be VERIFIED.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from app.duty_allocation.student_seating.types import Hall, LayoutStatus, Seat, _fail


def numbered_hall(venue_id: str, seat_count: int, *, source: str, status: LayoutStatus,
                  blocked: Iterable[int] = ()) -> Hall:
    if type(seat_count) is not int or seat_count < 1:
        _fail(f"hall {venue_id} seat_count must be a positive int.")
    return Hall(venue_id, tuple(Seat(n, str(n)) for n in range(1, seat_count + 1)), tuple(blocked), source, status)


def provisional_hall(venue_id: str, full_capacity: int) -> Hall:
    """Seats 1..full capacity when no layout exists. The numbers are unverified."""
    return numbered_hall(venue_id, full_capacity, status=LayoutStatus.PROVISIONAL,
                         source="PROVISIONAL: seats numbered 1..full capacity automatically; "
                                "no physical layout available, numbers not verified against the room")


def grid_hall(venue_id: str, row_labels: Iterable[str], columns: int, *, source: str, status: LayoutStatus,
              blocked: Iterable[str] = ()) -> Hall:
    rows = list(row_labels)
    if not rows or type(columns) is not int or columns < 1:
        _fail(f"hall {venue_id} needs row labels and a positive column count.")
    seats = tuple(Seat(i * columns + c, f"{label}{c}", i + 1, c) for i, label in enumerate(rows)
                  for c in range(1, columns + 1))
    numbers = {s.label: s.number for s in seats}
    blocked = list(blocked)
    unknown = [b for b in blocked if b not in numbers]
    if unknown:
        _fail(f"hall {venue_id} blocked seats are not in the layout: {', '.join(unknown)}.")
    return Hall(venue_id, seats, tuple(numbers[b] for b in blocked), source, status)


def hall_from_mapping(data: Mapping) -> Hall:
    """{"venue_id": "...", "seat_count": 60, "blocked": [3, 4], "source": "...", "status": "VERIFIED"}
    or {"venue_id": "...", "row_labels": ["A", "B"], "columns": 10, "blocked": ["A1"], ...}"""
    if not isinstance(data, Mapping) or not {"venue_id", "source", "status"} <= set(data):
        _fail("hall config needs venue_id, source and status.")
    try:
        status = LayoutStatus(data["status"])
    except ValueError:
        _fail(f"hall {data['venue_id']} status must be VERIFIED, PROVISIONAL or SYNTHETIC.")
    common = dict(source=data["source"], status=status, blocked=data.get("blocked", []))
    if set(data) <= {"venue_id", "seat_count", "blocked", "source", "status"} and "seat_count" in data:
        return numbered_hall(data["venue_id"], data["seat_count"], **common)
    if set(data) <= {"venue_id", "row_labels", "columns", "blocked", "source", "status"} and "columns" in data:
        return grid_hall(data["venue_id"], data.get("row_labels", []), data["columns"], **common)
    _fail("hall config needs either seat_count, or row_labels and columns (not both).")
