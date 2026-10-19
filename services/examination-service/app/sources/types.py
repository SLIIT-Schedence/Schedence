"""Read-only input records for the API and the future scheduler.

These types are the stable contract of ``app.sources``. They do not depend on
where the data is stored: today the ``examinations.seed_*`` tables, later the
Academic Data and Resource service APIs.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class Module:
    module_code: str
    module_name: str
    academic_year: int
    semester: int
    specializations: tuple[str, ...]
    credits: int
    difficulty: int
    exam_type: str
    # FINAL examination duration. In a scheduling snapshot it is the duration of
    # the period's examination kind (see app.sources.exam_rules).
    duration_minutes: int
    has_extra_reading_time: bool
    reading_time_minutes: int
    has_final_exam: bool
    has_mid_exam: bool
    mid_duration_minutes: int | None  # set exactly when has_mid_exam
    delivery_mode: str  # "regular" or "block"
    block_category: str | None  # "pro_rata" / "full_module" for block modules
    # Counted from registrations, never stored.
    candidate_count: int


@dataclass(frozen=True)
class Student:
    student_id: str
    academic_year: int
    specialization: str


@dataclass(frozen=True)
class Registration:
    student_id: str
    module_code: str
    registration_type: str


@dataclass(frozen=True)
class Venue:
    venue_id: str
    venue_name: str
    venue_type: str
    building: str
    floor: int
    # Full seating capacity, never reduced.
    normal_capacity: int
    # Legacy stored seed value (always floor(full x 0.5)), kept for compatibility.
    # Scheduling uses the effective capacity from the seating rules instead:
    # app.seating.load_capacity_snapshot().
    exam_capacity: int


@dataclass(frozen=True)
class VenueBlock:
    venue_id: str
    block_date: dt.date
    start_time: dt.time
    end_time: dt.time
    # simulated_teaching / simulated_maintenance (prototype) or a real source
    # such as teaching_timetable (synced later from the Teaching Timetable).
    source: str
    reason: str
    source_ref: str | None


@dataclass(frozen=True)
class ExamPeriodExclusion:
    period_code: str
    excluded_date: dt.date
    reason: str


@dataclass(frozen=True)
class ExamPeriodModule:
    """A module examined in an examination cycle (period)."""

    period_code: str
    module_code: str
    # "all" (normal and repeat registrations) or "repeat_only".
    candidate_scope: str


@dataclass(frozen=True)
class ExamPeriod:
    period_code: str
    period_name: str
    academic_year_label: str
    semester: int
    # "MID" or "FINAL" examinations.
    exam_kind: str
    start_date: dt.date
    end_date: dt.date
    day_start_time: dt.time
    day_end_time: dt.time
    include_saturdays: bool
    include_sundays: bool
    exclusions: tuple[ExamPeriodExclusion, ...]
    # Dates on which exams may be held (weekends and exclusions removed).
    exam_days: tuple[dt.date, ...]


@dataclass(frozen=True)
class Page(Generic[T]):
    """One page of a larger result. ``limit=None`` means "all remaining rows"."""

    items: tuple[T, ...]
    total: int
    limit: int | None
    offset: int
