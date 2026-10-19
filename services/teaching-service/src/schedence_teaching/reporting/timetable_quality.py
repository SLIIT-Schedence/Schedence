"""Read-only human-aware quality metrics over an already validated in-memory schedule.

Descriptive only: no campus thresholds, weights or policies are defaulted, and
nothing here validates hard constraints. Saved database versions use the
sibling workload/teaching-audit reports instead.
"""

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class AttendeeDay:
    kind: str  # LECTURER or STUDENT_GROUP (lowest-level groups, including inherited parent sessions)
    entity_id: str
    day: str
    sessions: int
    teaching_minutes: int
    back_to_back_pairs: int
    idle_gap_minutes: int
    longest_continuous_minutes: int
    span_minutes: int


@dataclass(frozen=True)
class BatchLectureMix:
    academic_year: int
    semester: int
    stream: str
    lectures: int
    online_lectures: int
    online_lecture_days: tuple[str, ...]
    offerings_with_lectures: int
    offerings_without_physical_lecture: int


@dataclass(frozen=True)
class TimetableQualityReport:
    lecturer_days: tuple[AttendeeDay, ...]
    student_days: tuple[AttendeeDay, ...]
    lecturer_teaching_days: tuple[tuple[str, int], ...]
    stream_day_mode_minutes: tuple[tuple[str, str, str, int], ...]
    batch_lectures: tuple[BatchLectureMix, ...]

    def summary(self, *, thin_day_minutes=None, long_continuous_minutes=None, long_span_minutes=None,
                long_student_day_minutes=None, preferred_minimum_minutes=None):
        """Threshold counts appear only for caller-supplied values."""
        def describe(rows, thin=None, heavy=None):
            result = {"attendee_days": len(rows), "back_to_back_pairs": sum(r.back_to_back_pairs for r in rows),
                "idle_gap_minutes": sum(r.idle_gap_minutes for r in rows),
                "maximum_day_minutes": max((r.teaching_minutes for r in rows), default=0),
                "maximum_continuous_minutes": max((r.longest_continuous_minutes for r in rows), default=0),
                "maximum_span_minutes": max((r.span_minutes for r in rows), default=0)}
            for key, limit, value in (("days_at_or_below_thin_minutes", thin, lambda r: r.teaching_minutes <= limit),
                    ("days_above_day_minutes", heavy, lambda r: r.teaching_minutes > limit),
                    ("days_above_continuous_minutes", long_continuous_minutes, lambda r: r.longest_continuous_minutes > limit),
                    ("days_above_span_minutes", long_span_minutes, lambda r: r.span_minutes > limit)):
                if limit is not None:
                    result[key] = sum(1 for r in rows if value(r))
            return result

        histogram = defaultdict(int)
        for _, days in self.lecturer_teaching_days:
            histogram[days] += 1
        lecturer_summary = describe(self.lecturer_days, thin_day_minutes)
        lecturer_summary["daily_teaching_minutes_distribution"] = dict(sorted(
            Counter(r.teaching_minutes for r in self.lecturer_days).items()))
        if preferred_minimum_minutes is not None:  # P2.1 definition: active and strictly below the minimum.
            lecturer_summary["thin_days_below_preferred_minimum"] = sum(
                1 for r in self.lecturer_days if 0 < r.teaching_minutes < preferred_minimum_minutes)
        student_summary = describe(self.student_days, heavy=long_student_day_minutes)
        student_summary["mean_day_minutes"] = (round(sum(r.teaching_minutes for r in self.student_days) / len(self.student_days), 2)
                                               if self.student_days else 0.0)
        student_summary["daily_teaching_minutes_distribution"] = dict(sorted(
            Counter(r.teaching_minutes for r in self.student_days).items()))
        lectures = sum(r.lectures for r in self.batch_lectures)
        online = sum(r.online_lectures for r in self.batch_lectures)
        return {"lecturers": {**lecturer_summary, "teaching_lecturers": len(self.lecturer_teaching_days),
                              "teaching_days_histogram": dict(sorted(histogram.items()))},
                "student_groups": student_summary,
                "thresholds_are_diagnostic_only_not_university_policy": True,
                "lectures": {"total": lectures, "online": online,
                             "batches_with_multiple_online_days": sum(len(r.online_lecture_days) > 1 for r in self.batch_lectures),
                             "offerings_without_physical_lecture": sum(r.offerings_without_physical_lecture for r in self.batch_lectures)}}

    def to_dict(self):
        return {"lecturer_days": [asdict(r) for r in self.lecturer_days], "student_days": [asdict(r) for r in self.student_days],
                "lecturer_teaching_days": dict(self.lecturer_teaching_days),
                "stream_day_mode_minutes": [dict(stream=s, day=d, mode=m, minutes=v) for s, d, m, v in self.stream_day_mode_minutes],
                "batch_lectures": [asdict(r) for r in self.batch_lectures], "descriptive_only": True, "university_ready": False}


def _day(kind, entity_id, day, spans):
    spans = sorted(spans)
    back_to_back = idle = longest = run = 0
    for index, (start, end) in enumerate(spans):
        if index and start == spans[index - 1][1]:
            back_to_back += 1
            run += end - start
        else:
            if index:
                idle += max(0, start - spans[index - 1][1])
            run = end - start
        longest = max(longest, run)
    return AttendeeDay(kind, entity_id, day, len(spans), sum(end - start for start, end in spans), back_to_back, idle,
                       longest, max(end for _, end in spans) - spans[0][0])


def _booking(row):
    """(sessions, groups, mode) for a ScheduledSession or a combined ScheduledDelivery counted once."""
    if hasattr(row, "covered_sessions"):
        mode = getattr(row, "delivery_mode", None)
        return row.covered_sessions, tuple(row.group_ids), mode.value if mode is not None else "UNSPECIFIED"
    mode = row.delivery_mode.value if row.delivery_mode is not None else "UNSPECIFIED"
    return (row.session,), (row.session.group_id,), mode


def timetable_quality(prepared, schedule, *, config=None):
    """config (optional) only orders reported online days chronologically."""
    hierarchy = prepared.hierarchy
    groups = {row.group_id: row for row in prepared.input_snapshot.student_groups}
    offerings = {row.offering_id: row for row in prepared.input_snapshot.module_offerings}
    leaves = {identity for identity in groups if not hierarchy.children.get(identity)}
    lecturer, students = defaultdict(list), defaultdict(list)
    stream_minutes = defaultdict(int)
    batches = defaultdict(lambda: {"lectures": 0, "online": 0, "days": set(), "offerings": defaultdict(bool)})
    for row in schedule:
        sessions, group_ids, mode = _booking(row)
        span = (row.start_minute, row.end_minute)
        lecturer[(row.assigned_staff_id, row.day)].append(span)
        attending = set()
        for group in group_ids:
            attending.update(leaf for leaf in (group, *hierarchy.descendants(group)) if leaf in leaves)
        for leaf in attending:
            students[(leaf, row.day)].append(span)
        stream_minutes[(sessions[0].stream.value, row.day, mode)] += row.end_minute - row.start_minute
        if sessions[0].session_type.value == "LECTURE":
            for session in sessions:
                offering = offerings[session.offering_id]
                batch = batches[(offering.academic_year, offering.semester, offering.stream.value)]
                batch["lectures"] += 1
                batch["online"] += mode == "ONLINE"
                if mode == "ONLINE":
                    batch["days"].add(row.day)
                batch["offerings"][session.offering_id] |= mode != "ONLINE"
    order = {day.day: index for index, day in enumerate(config.days)} if config is not None else {}
    teaching_days = defaultdict(set)
    for staff, day in lecturer:
        teaching_days[staff].add(day)
    return TimetableQualityReport(
        tuple(_day("LECTURER", staff, day, spans) for (staff, day), spans in sorted(lecturer.items())),
        tuple(_day("STUDENT_GROUP", group, day, spans) for (group, day), spans in sorted(students.items())),
        tuple(sorted((staff, len(days)) for staff, days in teaching_days.items())),
        tuple((s, d, m, v) for (s, d, m), v in sorted(stream_minutes.items())),
        tuple(BatchLectureMix(year, semester, stream, data["lectures"], data["online"],
                              tuple(sorted(data["days"], key=lambda day: (order.get(day, 0), day))),
                              len(data["offerings"]), sum(not physical for physical in data["offerings"].values()))
              for (year, semester, stream), data in sorted(batches.items())))
