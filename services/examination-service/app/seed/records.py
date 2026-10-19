"""Plain, immutable records for the synthetic dataset.

Field names match the column names in ``app.db.seed_models``, so
``dataclasses.asdict(record)`` is directly an insertable row.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict
from dataclasses import dataclass


@dataclass(frozen=True)
class Module:
    module_code: str
    module_name: str
    academic_year: int
    semester: int
    credits: int
    difficulty: int
    exam_type: str
    duration_minutes: int  # FINAL examination
    has_extra_reading_time: bool
    reading_time_minutes: int
    # Defaults match the database defaults for older modules (final exam only, regular).
    has_final_exam: bool = True
    has_mid_exam: bool = False
    mid_duration_minutes: int | None = None
    delivery_mode: str = "regular"
    block_category: str | None = None


@dataclass(frozen=True)
class ModuleSpecialization:
    module_code: str
    specialization: str


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
    normal_capacity: int  # full capacity
    exam_capacity: int  # floor(full capacity x exam_seating_ratio)


@dataclass(frozen=True)
class VenueBlock:
    venue_id: str
    block_date: dt.date
    start_time: dt.time
    end_time: dt.time
    source: str
    reason: str
    source_ref: str | None = None  # booking ID in the owning system; None for seed rows


@dataclass(frozen=True)
class ExamPeriod:
    period_code: str
    period_name: str
    academic_year_label: str
    semester: int
    start_date: dt.date
    end_date: dt.date
    day_start_time: dt.time
    day_end_time: dt.time
    include_saturdays: bool
    include_sundays: bool
    # "MID" or "FINAL"; FINAL, like the database default for older periods.
    exam_kind: str = "FINAL"


@dataclass(frozen=True)
class ExamPeriodExclusion:
    period_code: str
    excluded_date: dt.date
    reason: str


@dataclass(frozen=True)
class ExamPeriodModule:
    """A module examined in an examination cycle (period); see SeedExamPeriodModule."""

    period_code: str
    module_code: str
    candidate_scope: str = "all"  # "all" or "repeat_only"


@dataclass(frozen=True)
class Dataset:
    modules: tuple[Module, ...]
    module_specializations: tuple[ModuleSpecialization, ...]
    students: tuple[Student, ...]
    registrations: tuple[Registration, ...]
    venues: tuple[Venue, ...]
    venue_blocks: tuple[VenueBlock, ...]
    exam_periods: tuple[ExamPeriod, ...]
    exam_period_exclusions: tuple[ExamPeriodExclusion, ...]
    # Cycle membership; empty by default (nothing is mapped unless configured).
    exam_period_modules: tuple[ExamPeriodModule, ...] = ()

    def registration_counts(self) -> dict[str, int]:
        """Candidates per module, derived from the registration records only."""
        counts = Counter(r.module_code for r in self.registrations)
        return {m.module_code: counts.get(m.module_code, 0) for m in self.modules}

    def specializations_by_module(self) -> dict[str, set[str]]:
        result: dict[str, set[str]] = defaultdict(set)
        for link in self.module_specializations:
            result[link.module_code].add(link.specialization)
        return dict(result)
