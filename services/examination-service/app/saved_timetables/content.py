"""Content identity of a saved timetable, and the scheduler timetable rebuilt from its rows.

:func:`content_fingerprint` is computed from the rows when a draft is saved and
again from the stored rows before every status change: any change to a session,
room or seat (even one made outside the API) makes approval and publication fail.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import Iterable

from app.db.timetable_models import ExamTimetableRoom, ExamTimetableSeat, ExamTimetableSession
from app.saved_timetables.store import Row
from app.scheduling.types import DraftTimetable, ExamSession, RoomAllocation, SeatAllocation


def _columns(model) -> tuple[str, ...]:
    return tuple(c.name for c in model.__table__.columns if c.name != "timetable_id")


SESSION_COLUMNS = _columns(ExamTimetableSession)
ROOM_COLUMNS = _columns(ExamTimetableRoom)
SEAT_COLUMNS = _columns(ExamTimetableSeat)


def _value(value):
    return value.isoformat() if isinstance(value, dt.datetime) else value


def _canonical(rows: Iterable[Row], columns: tuple[str, ...]) -> list[list]:
    return sorted([_value(row[c]) for c in columns] for row in rows)


def content_fingerprint(sessions: Iterable[Row], rooms: Iterable[Row], seats: Iterable[Row]) -> str:
    content = {"sessions": _canonical(sessions, SESSION_COLUMNS), "rooms": _canonical(rooms, ROOM_COLUMNS),
               "seats": _canonical(seats, SEAT_COLUMNS)}
    return hashlib.sha256(json.dumps(content, separators=(",", ":"), sort_keys=True).encode()).hexdigest()


def rebuild_timetable(header: Row, sessions: list[Row], rooms: list[Row], seats: list[Row]) -> DraftTimetable:
    """The saved rows as the scheduler's timetable, for the independent H1-H11 validator."""
    seats_by_room: dict[tuple[str, str], list[SeatAllocation]] = {}
    for seat in sorted(seats, key=lambda s: s["seat_number"]):
        seats_by_room.setdefault((seat["session_id"], seat["venue_id"]), []).append(
            SeatAllocation(seat["student_id"], seat["seat_number"]))
    rooms_by_session: dict[str, list[RoomAllocation]] = {}
    for room in sorted(rooms, key=lambda r: r["venue_id"]):
        rooms_by_session.setdefault(room["session_id"], []).append(RoomAllocation(
            room["venue_id"], tuple(seats_by_room.get((room["session_id"], room["venue_id"]), ()))))
    return DraftTimetable(header["period_code"], header["input_fingerprint"], tuple(
        ExamSession(s["session_id"], s["module_code"], header["period_code"], s["starts_at"], s["ends_at"],
                    tuple(rooms_by_session.get(s["session_id"], ())), s["session_number"], s["session_count"])
        for s in sessions))
