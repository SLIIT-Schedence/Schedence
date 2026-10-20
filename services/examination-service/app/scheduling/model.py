"""Shared H1-H11 model, with capacity-based multiple sessions, and immutable draft decoding.

A module whose candidates fit into one sitting (app.scheduling.sessions) is
modelled exactly as before: one start, one room allocation, and pattern-level
student no-overlap. Only a module that needs N > 1 sessions gets N starts at
different, non-overlapping times (in session order), a room allocation per
session, and one session choice per candidate; those candidates' no-overlap
(with the minimum break) uses their own chosen session.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field

from ortools.sat.python import cp_model

from app.scheduling.availability import allowed_starts, merged_blocks, origin, protected_student_spans
from app.scheduling.sessions import session_counts
from app.scheduling.types import (
    DraftTimetable, ExamSession, InputSnapshot, RoomAllocation, SeatAllocation,
)


@dataclass
class SessionLayout:
    """Solver variables of the sessions; shared by the objective and the decoder."""

    sessions: dict[str, int]  # module -> number of sessions (1 = not split)
    starts: dict[tuple[str, int], object] = field(default_factory=dict)  # (module, session index)
    allocated: dict[tuple[str, int, str], object] = field(default_factory=dict)  # (module, session, venue) -> seats
    assigned: dict[tuple[str, str, int], object] = field(default_factory=dict)  # (student, module, session); split only

    @property
    def split(self) -> set[str]:
        return {code for code, count in self.sessions.items() if count > 1}


def build_hard_model(snapshot: InputSnapshot):
    """Returns (model, layout, layout.allocated)."""
    model = cp_model.CpModel()
    horizon = ((snapshot.period.end_date - snapshot.period.start_date).days + 1) * 1440
    layout = SessionLayout(session_counts(snapshot))
    student_intervals = {}
    split_intervals: dict[str, list] = defaultdict(list)  # student -> optional intervals of split modules
    room_intervals: dict[str, list] = defaultdict(list)
    # Fixed blocks and the venues held by protected timetables of other cycles (never moved by the solver).
    for venue, blocks in merged_blocks(snapshot, protected=True).items():
        for index, (start, end) in enumerate(blocks):
            room_intervals[venue].append(model.new_fixed_size_interval_var(start, end - start, f"block:{venue}:{index}"))
    candidates: dict[str, list[str]] = defaultdict(list)
    for r in snapshot.registrations:
        candidates[r.module_code].append(r.student_id)
    break_minutes = snapshot.policy.minimum_break_minutes
    for m in snapshot.modules:
        code = m.module_code
        duration = m.duration_minutes + m.reading_time_minutes
        domain = allowed_starts(snapshot, m)
        if not domain:
            model.add(False)
            domain = [0]  # retain a valid model so the result is INFEASIBLE
        count = layout.sessions[code]
        seated = []  # per session: how many candidates it seats
        for k in range(count):
            name = code if count == 1 else f"{code}#{k + 1}"
            start = model.new_int_var_from_domain(cp_model.Domain.from_values(domain), f"start:{name}")
            end = model.new_int_var(0, horizon + duration, f"end:{name}")
            model.add(end == start + duration)
            layout.starts[code, k] = start
            if count == 1:
                student_intervals[code] = model.new_fixed_size_interval_var(
                    start, duration + break_minutes, f"student:{code}")
            counts, chosen = [], []
            for v in snapshot.capacities.available_venues():
                if v.venue_type not in snapshot.policy.allowed_venue_types(m.exam_type, code):
                    continue
                use = model.new_bool_var(f"use:{name}:{v.venue_id}")
                seats = model.new_int_var(0, min(m.candidate_count, v.exam_capacity), f"seats:{name}:{v.venue_id}")
                model.add(seats <= v.exam_capacity * use)
                model.add(seats >= use)  # selected rooms cannot be empty
                room_intervals[v.venue_id].append(model.new_optional_interval_var(
                    start, duration, end, use, f"room:{name}:{v.venue_id}"))
                layout.allocated[code, k, v.venue_id] = seats
                counts.append(seats)
                chosen.append(use)
            seated.append(sum(counts))
            if snapshot.policy.max_rooms_per_exam is not None:
                model.add(sum(chosen) <= snapshot.policy.max_rooms_per_exam)
        if count == 1:
            model.add(seated[0] == m.candidate_count)
            continue
        # Sessions of one module: different times, in order, never overlapping.
        for k in range(count - 1):
            model.add(layout.starts[code, k] + duration <= layout.starts[code, k + 1])
        choices = {k: [] for k in range(count)}
        for student in sorted(candidates[code]):
            options = []
            for k in range(count):
                sits = model.new_bool_var(f"sits:{student}:{code}#{k + 1}")
                layout.assigned[student, code, k] = sits
                options.append(sits)
                choices[k].append(sits)
                split_intervals[student].append(model.new_optional_fixed_size_interval_var(
                    layout.starts[code, k], duration + break_minutes, sits, f"student:{student}:{code}#{k + 1}"))
            model.add_exactly_one(options)  # each candidate sits exactly one session
        for k in range(count):
            model.add(seated[k] == sum(choices[k]))  # each candidate gets exactly one seat in it
            model.add(seated[k] >= 1)  # no empty session
    for intervals in room_intervals.values():
        model.add_no_overlap(intervals)
    by_student: dict[str, set[str]] = defaultdict(set)
    for r in snapshot.registrations:
        by_student[r.student_id].add(r.module_code)
    split = layout.split
    # Students taking the same module set (no split module) share one no-overlap constraint.
    patterns = {tuple(sorted(codes)) for codes in by_student.values() if len(codes) > 1 and not codes & split}
    for codes in sorted(patterns):
        model.add_no_overlap([student_intervals[code] for code in codes])
    # A student with a split module: their chosen session against all their other exams.
    for student in sorted(s for s, codes in by_student.items() if len(codes) > 1 and codes & split):
        model.add_no_overlap([student_intervals[c] for c in sorted(by_student[student] - split)]
                             + split_intervals[student])
    # Candidates who also sit an exam of a protected timetable (another cycle, repeat students included):
    # their own exams may not overlap those fixed exams or break the minimum break after them.
    for student, spans in sorted(protected_student_spans(snapshot, break_minutes).items()):
        own = [student_intervals[c] for c in sorted(by_student.get(student, set()) - split)] + split_intervals.get(student, [])
        if own:
            model.add_no_overlap(own + [model.new_fixed_size_interval_var(start, end - start, f"protected:{student}:{index}")
                                        for index, (start, end) in enumerate(spans)])
    return model, layout, layout.allocated


def decode_draft(snapshot: InputSnapshot, solver, layout: SessionLayout, allocated=None) -> DraftTimetable:
    candidates: dict[str, list[str]] = defaultdict(list)
    for r in snapshot.registrations:
        candidates[r.module_code].append(r.student_id)
    sessions = []
    period = snapshot.period.period_code
    for m in snapshot.modules:
        code, count = m.module_code, layout.sessions[m.module_code]
        duration = m.duration_minutes + m.reading_time_minutes
        for k in range(count):
            if count == 1:
                students = sorted(candidates[code])
            else:
                students = sorted(s for s in candidates[code] if solver.value(layout.assigned[s, code, k]))
            offset, rooms = 0, []
            for v in sorted(snapshot.capacities.venues, key=lambda v: v.venue_id):
                seats_var = layout.allocated.get((code, k, v.venue_id))
                seats = solver.value(seats_var) if seats_var is not None else 0
                if seats:
                    rooms.append(RoomAllocation(v.venue_id, tuple(
                        SeatAllocation(student, seat) for seat, student in enumerate(students[offset:offset + seats], 1))))
                    offset += seats
            start = origin(snapshot) + dt.timedelta(minutes=solver.value(layout.starts[code, k]))
            sessions.append(ExamSession(
                session_id=f"{period}:{code}" if count == 1 else f"{period}:{code}:S{k + 1:02d}",
                module_code=code, period_code=period, start=start, end=start + dt.timedelta(minutes=duration),
                rooms=tuple(rooms), session_number=k + 1, session_count=count,
            ))
    return DraftTimetable(period, snapshot.fingerprint, tuple(sessions))
