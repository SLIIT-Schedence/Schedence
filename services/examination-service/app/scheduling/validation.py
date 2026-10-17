"""Independent H1-H11 checks. No OR-Tools or solver helpers are imported here."""

from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict
from itertools import combinations

from app.scheduling.inputs import validate_inputs
from app.scheduling.policy import whole_number
from app.scheduling.contracts import SchedulingValidationError, frozen_sequence, record_errors, text_errors
from app.scheduling.types import DraftTimetable, ExamSession, InputSnapshot, RoomAllocation, SeatAllocation, Violation


def validate_timetable(snapshot: InputSnapshot, timetable: DraftTimetable) -> tuple[Violation, ...]:
    errors = list(validate_inputs(snapshot))
    if errors:
        return tuple(errors)
    if type(timetable) is not DraftTimetable:
        return (Violation("H11", "Timetable must be a DraftTimetable."),)
    # Defend this boundary even when a caller bypasses dataclass construction.
    try:
        sessions = frozen_sequence(timetable.sessions, "draft.sessions", ExamSession)
        errors.extend(text_errors(timetable.period_code, "draft.period_code"))
        errors.extend(text_errors(timetable.input_fingerprint, "draft.input_fingerprint"))
        normalized = []
        for session in sessions:
            errors.extend(record_errors((session,), ExamSession, "session", text=("session_id", "module_code", "period_code")))
            rooms = frozen_sequence(session.rooms, "session.rooms", RoomAllocation)
            normalized_rooms = []
            for room in rooms:
                errors.extend(text_errors(room.venue_id, "room.venue_id"))
                seats = frozen_sequence(room.seats, "room.seats", SeatAllocation)
                errors.extend(record_errors(seats, SeatAllocation, "seat", text=("student_id",)))
                if any(type(seat.seat_number) not in (int, float, bool) for seat in seats):
                    errors.append(Violation("H2", "Logical seat numbers must be numeric."))
                normalized_rooms.append((room, seats))
            normalized.append((session, normalized_rooms))
    except SchedulingValidationError as exc:
        return tuple(errors) + exc.violations
    if errors:
        return tuple(errors)

    def error(rule: str, message: str) -> None:
        errors.append(Violation(rule, message))

    p, policy = snapshot.period, snapshot.policy
    modules = {m.module_code: m for m in snapshot.modules}
    students = {s.student_id for s in snapshot.students}
    capacities = {v.venue_id: v for v in snapshot.capacities.venues}
    candidates: dict[str, set[str]] = defaultdict(set)
    for r in snapshot.registrations:
        candidates[r.module_code].add(r.student_id)
    if timetable.period_code != p.period_code or timetable.input_fingerprint != snapshot.fingerprint:
        error("H11", "Draft period or input fingerprint does not match the snapshot.")
    if Counter(s.module_code for s in sessions) != Counter({code: 1 for code in modules}):
        error("H10", "Every selected module must have exactly one examination session.")
    ids = [s.session_id for s in sessions]
    if any(not i for i in ids) or len(ids) != len(set(ids)):
        error("H11", "Session IDs must be non-empty and unique.")
    room_times: dict[str, list] = defaultdict(list)
    student_times: dict[str, list] = defaultdict(list)
    for s, rooms in normalized:
        m = modules.get(s.module_code)
        if m is None:
            error("H11", f"Session {s.session_id} refers to an unselected module.")
            continue
        if s.period_code != p.period_code:
            error("H11", f"Session {s.session_id} refers to the wrong period.")
        if (not isinstance(s.start, dt.datetime) or not isinstance(s.end, dt.datetime)
                or s.start.tzinfo is not None or s.end.tzinfo is not None
                or s.start.second or s.start.microsecond or s.end.second or s.end.microsecond):
            error("H11", f"Session {s.session_id} needs local whole-minute datetimes.")
            continue
        if (s.end - s.start).total_seconds() != 60 * (m.duration_minutes + m.reading_time_minutes):
            error("H9", f"Session {s.session_id} does not reserve the full duration plus reading time.")
        if not (p.start_date <= s.start.date() <= s.end.date() <= p.end_date):
            error("H6", f"Session {s.session_id} is outside the selected period.")
        if (s.start.date() != s.end.date() or s.start.time() < p.day_start_time
                or s.end.time() > p.day_end_time or s.end <= s.start
                or s.start.time() not in policy.permitted_start_times):
            error("H7", f"Session {s.session_id} violates examination hours or permitted starts.")
        # Recompute from calendar rules, not the solver's domain or cached exam_days.
        day = s.start.date()
        if (any(day == e.excluded_date for e in p.exclusions)
                or (day.weekday() == 5 and not p.include_saturdays)
                or (day.weekday() == 6 and not p.include_sundays)):
            error("H8", f"Session {s.session_id} falls on an excluded examination day.")
        room_ids = [room.venue_id for room, _ in rooms]
        if len(room_ids) != len(set(room_ids)) or not room_ids:
            error("H11", f"Session {s.session_id} needs non-empty, unique room allocations.")
        if policy.max_rooms_per_exam is not None and len(rooms) > policy.max_rooms_per_exam:
            error("H11", f"Session {s.session_id} exceeds the configured room limit.")
        seated: Counter[str] = Counter()
        for room, seats in rooms:
            cap = capacities.get(room.venue_id)
            if cap is None:
                error("H11", f"Session {s.session_id} refers to unknown venue {room.venue_id}.")
            else:
                if cap.exam_capacity <= 0 or len(seats) > cap.exam_capacity:
                    error("H2", f"Session {s.session_id} exceeds effective capacity in {room.venue_id}.")
                if cap.venue_type not in policy.allowed_venue_types(m.exam_type, m.module_code):
                    error("H3", f"Session {s.session_id} uses incompatible venue {room.venue_id}.")
                for b in snapshot.blocks:
                    if b.venue_id == room.venue_id:
                        start = dt.datetime.combine(b.block_date, b.start_time)
                        end = dt.datetime.combine(b.block_date, b.end_time)
                        if s.start < end and start < s.end:
                            error("H4", f"Session {s.session_id} overlaps {b.source} block in {room.venue_id}.")
                room_times[room.venue_id].append(s)
            numbers = [seat.seat_number for seat in seats]
            if not numbers or len(numbers) != len(set(numbers)):
                error("H11", f"Room {room.venue_id} needs non-empty, unique logical seat numbers.")
            for seat in seats:
                seated[seat.student_id] += 1
                if seat.student_id not in students:
                    error("H11", f"Seat refers to unknown student {seat.student_id}.")
                if seat.student_id not in candidates[m.module_code]:
                    error("H10", f"Student {seat.student_id} is not registered for {m.module_code}.")
                if not whole_number(seat.seat_number, 1) or (cap is not None and seat.seat_number > cap.exam_capacity):
                    error("H2", f"Logical seat is outside effective capacity in {room.venue_id}.")
            # Room times are inherited from the containing session: split rooms cannot drift.
        if seated != Counter({student: 1 for student in candidates[m.module_code]}):
            error("H10", f"Every candidate for {m.module_code} must receive exactly one seat.")
        for student in candidates[m.module_code]:
            student_times[student].append(s)
    for venue, sessions in room_times.items():
        for a, b in combinations(sessions, 2):
            if a.start < b.end and b.start < a.end:
                error("H5", f"Sessions {a.session_id} and {b.session_id} overlap in venue {venue}.")
    for student, sessions in student_times.items():
        for a, b in combinations(sorted(sessions, key=lambda s: s.start), 2):
            if b.start - a.end < dt.timedelta(minutes=policy.minimum_break_minutes):
                error("H1", f"Student {student} has overlapping exams or insufficient break.")
    return tuple(errors)
