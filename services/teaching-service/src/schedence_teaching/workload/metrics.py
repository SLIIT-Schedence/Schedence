"""Reusable physical-delivery metrics; callers must validate the timetable first."""

from collections import Counter, defaultdict
from dataclasses import dataclass


@dataclass(frozen=True)
class TeachingMetrics:
    assigned_delivery_count: int
    scheduled_teaching_minutes: int
    teaching_days: tuple[str, ...]
    daily_teaching_minutes: tuple[tuple[str, int], ...]
    maximum_daily_teaching_minutes: int
    longest_consecutive_teaching_minutes: int

    @property
    def teaching_day_count(self):
        return len(self.teaching_days)


def teaching_metrics(deliveries, day_dates) -> TeachingMetrics:
    """Count each selected booking once; include zeroes for supplied calendar days.

    Adjacent intervals on one day form a consecutive run. Any positive gap or
    change of day ends the run. Neither attendance nor opening hours is inferred.
    """
    daily, blocks = Counter(), defaultdict(list)
    count = 0
    for row in deliveries:
        count += 1
        daily[row.day] += row.end_minute - row.start_minute
        blocks[row.day].append((row.start_minute, row.end_minute))
    longest = 0
    for windows in blocks.values():
        previous_end, run = None, 0
        for start, end in sorted(windows):
            run = run + end - start if previous_end == start else end - start
            longest, previous_end = max(longest, run), end
    ordered = tuple(sorted(day_dates, key=day_dates.get))
    return TeachingMetrics(count, sum(daily.values()), tuple(day for day in ordered if daily[day]),
        tuple((day, daily[day]) for day in ordered), max(daily.values(), default=0), longest)
