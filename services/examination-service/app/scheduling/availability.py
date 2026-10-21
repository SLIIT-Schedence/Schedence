"""Candidate starts and normalized fixed room blocks. Half-open time intervals."""

from __future__ import annotations

import datetime as dt
from collections import defaultdict

from app.scheduling.types import InputSnapshot
from app.sources.types import Module
from app.scheduling.runtime_budget import check_budget


def origin(snapshot: InputSnapshot) -> dt.datetime:
    return dt.datetime.combine(snapshot.period.start_date, dt.time())


def minute_offset(snapshot: InputSnapshot, instant: dt.datetime) -> int:
    return int((instant - origin(snapshot)).total_seconds() // 60)


def allowed_starts(snapshot: InputSnapshot, module: Module) -> list[int]:
    duration = dt.timedelta(minutes=module.duration_minutes + module.reading_time_minutes)
    starts = []
    for day in snapshot.period.exam_days:
        for clock in snapshot.policy.permitted_start_times:
            check_budget()
            start = dt.datetime.combine(day, clock)
            if (clock >= snapshot.period.day_start_time
                    and duration <= dt.datetime.combine(day, snapshot.period.day_end_time) - start):
                starts.append(minute_offset(snapshot, start))
    return sorted(starts)


def _union(spans) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def merged_blocks(snapshot: InputSnapshot, *, protected: bool = False) -> dict[str, list[tuple[int, int]]]:
    """Union overlapping blocks; overlapping fixed intervals must not imply infeasibility.

    protected=True also holds the venues of the protected timetables of other cycles, as fixed intervals.
    Session counts (H10) deliberately use protected=False, exactly like the independent validator.
    """
    rows: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for b in snapshot.blocks:
        rows[b.venue_id].append((minute_offset(snapshot, dt.datetime.combine(b.block_date, b.start_time)),
                                 minute_offset(snapshot, dt.datetime.combine(b.block_date, b.end_time))))
    if protected and snapshot.protected is not None:
        known = {v.venue_id for v in snapshot.venues}
        for _, session in snapshot.protected.sessions:
            span = _clipped(snapshot, session.start, session.end)
            if span is not None:
                for venue in session.venue_ids:
                    if venue in known:
                        rows[venue].append(span)
    return {venue: _union(spans) for venue, spans in rows.items()}


def protected_student_spans(snapshot: InputSnapshot, break_minutes: int) -> dict[str, list[tuple[int, int]]]:
    """Per candidate: the minutes held by their protected exams, each extended by the minimum break.

    A new exam of the same student may neither overlap these spans nor start inside them, which is the
    break rule of H1 applied across cycles.
    """
    rows: dict[str, list[tuple[int, int]]] = defaultdict(list)
    if snapshot.protected is None:
        return {}
    for _, session in snapshot.protected.sessions:
        span = _clipped(snapshot, session.start, session.end + dt.timedelta(minutes=break_minutes))
        if span is not None:
            for student in session.student_ids:
                rows[student].append(span)
    return {student: _union(spans) for student, spans in rows.items()}


def _clipped(snapshot: InputSnapshot, start: dt.datetime, end: dt.datetime) -> tuple[int, int] | None:
    """Minute span inside the model's horizon, or None when it cannot touch any candidate start."""
    horizon = ((snapshot.period.end_date - snapshot.period.start_date).days + 1) * 1440
    begin, finish = max(minute_offset(snapshot, start), 0), minute_offset(snapshot, end)
    return (begin, finish) if finish > 0 and begin < horizon + 1440 else None
