"""Candidate starts and normalized fixed room blocks. Half-open time intervals."""

from __future__ import annotations

import datetime as dt
from collections import defaultdict

from app.scheduling.types import InputSnapshot
from app.sources.types import Module


def origin(snapshot: InputSnapshot) -> dt.datetime:
    return dt.datetime.combine(snapshot.period.start_date, dt.time())


def minute_offset(snapshot: InputSnapshot, instant: dt.datetime) -> int:
    return int((instant - origin(snapshot)).total_seconds() // 60)


def allowed_starts(snapshot: InputSnapshot, module: Module) -> list[int]:
    duration = dt.timedelta(minutes=module.duration_minutes + module.reading_time_minutes)
    return sorted(minute_offset(snapshot, start)
                  for day in snapshot.period.exam_days
                  for clock in snapshot.policy.permitted_start_times
                  for start in (dt.datetime.combine(day, clock),)
                  if clock >= snapshot.period.day_start_time
                  and duration <= dt.datetime.combine(day, snapshot.period.day_end_time) - start)


def merged_blocks(snapshot: InputSnapshot) -> dict[str, list[tuple[int, int]]]:
    """Union overlapping blocks; overlapping fixed intervals must not imply infeasibility."""
    rows: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for b in snapshot.blocks:
        rows[b.venue_id].append((minute_offset(snapshot, dt.datetime.combine(b.block_date, b.start_time)),
                                 minute_offset(snapshot, dt.datetime.combine(b.block_date, b.end_time))))
    result = {}
    for venue, blocks in rows.items():
        merged: list[tuple[int, int]] = []
        for start, end in sorted(blocks):
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            else:
                merged.append((start, end))
        result[venue] = merged
    return result
