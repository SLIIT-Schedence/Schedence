"""Shared Phase 1 H1-H11 model and immutable draft decoding."""

from __future__ import annotations

import datetime as dt
from collections import defaultdict

from ortools.sat.python import cp_model

from app.scheduling.availability import allowed_starts, merged_blocks, origin
from app.scheduling.types import (
    DraftTimetable, ExamSession, InputSnapshot, RoomAllocation, SeatAllocation,
)


def build_hard_model(snapshot: InputSnapshot):
    model = cp_model.CpModel()
    horizon = ((snapshot.period.end_date - snapshot.period.start_date).days + 1) * 1440
    starts, ends, student_intervals = {}, {}, {}
    room_intervals: dict[str, list] = defaultdict(list)
    allocated = {}
    for venue, blocks in merged_blocks(snapshot).items():
        for index, (start, end) in enumerate(blocks):
            room_intervals[venue].append(model.new_fixed_size_interval_var(start, end - start, f"block:{venue}:{index}"))
    for m in snapshot.modules:
        code = m.module_code
        duration = m.duration_minutes + m.reading_time_minutes
        domain = allowed_starts(snapshot, m)
        if not domain:
            model.add(False)
            domain = [0]  # retain a valid model so the result is INFEASIBLE
        starts[code] = model.new_int_var_from_domain(cp_model.Domain.from_values(domain), f"start:{code}")
        ends[code] = model.new_int_var(0, horizon + duration, f"end:{code}")
        model.add(ends[code] == starts[code] + duration)
        student_intervals[code] = model.new_fixed_size_interval_var(
            starts[code], duration + snapshot.policy.minimum_break_minutes, f"student:{code}")
        counts, chosen = [], []
        for v in snapshot.capacities.available_venues():
            if v.venue_type not in snapshot.policy.allowed_venue_types(m.exam_type, code):
                continue
            use = model.new_bool_var(f"use:{code}:{v.venue_id}")
            count = model.new_int_var(0, min(m.candidate_count, v.exam_capacity), f"seats:{code}:{v.venue_id}")
            model.add(count <= v.exam_capacity * use)
            model.add(count >= use)  # selected rooms cannot be empty
            room_intervals[v.venue_id].append(model.new_optional_interval_var(
                starts[code], duration, ends[code], use, f"room:{code}:{v.venue_id}"))
            allocated[code, v.venue_id] = count
            counts.append(count)
            chosen.append(use)
        model.add(sum(counts) == m.candidate_count)
        if snapshot.policy.max_rooms_per_exam is not None:
            model.add(sum(chosen) <= snapshot.policy.max_rooms_per_exam)
    for intervals in room_intervals.values():
        model.add_no_overlap(intervals)
    by_student: dict[str, set[str]] = defaultdict(set)
    for r in snapshot.registrations:
        by_student[r.student_id].add(r.module_code)
    # Students taking the same module set share the same no-overlap constraint.
    patterns = {tuple(sorted(codes)) for codes in by_student.values() if len(codes) > 1}
    for codes in sorted(patterns):
        model.add_no_overlap([student_intervals[code] for code in codes])
    return model, starts, allocated


def decode_draft(snapshot: InputSnapshot, solver, starts, allocated) -> DraftTimetable:
    candidates: dict[str, list[str]] = defaultdict(list)
    for r in snapshot.registrations:
        candidates[r.module_code].append(r.student_id)
    sessions = []
    for m in snapshot.modules:
        code, offset, rooms = m.module_code, 0, []
        students = sorted(candidates[code])
        for v in sorted(snapshot.capacities.venues, key=lambda v: v.venue_id):
            count_var = allocated.get((code, v.venue_id))
            count = solver.value(count_var) if count_var is not None else 0
            if count:
                rooms.append(RoomAllocation(v.venue_id, tuple(
                    SeatAllocation(student, seat) for seat, student in enumerate(students[offset:offset + count], 1))))
                offset += count
        sessions.append(ExamSession(
            session_id=f"{snapshot.period.period_code}:{code}", module_code=code, period_code=snapshot.period.period_code,
            start=origin(snapshot) + dt.timedelta(minutes=solver.value(starts[code])),
            end=origin(snapshot) + dt.timedelta(minutes=solver.value(starts[code]) + m.duration_minutes + m.reading_time_minutes), rooms=tuple(rooms),
        ))
    draft = DraftTimetable(snapshot.period.period_code, snapshot.fingerprint, tuple(sessions))
    return draft
