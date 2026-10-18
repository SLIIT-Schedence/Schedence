"""Deterministic synthetic dataset generator.

Modules come from the cleaned real catalogue and venues from the campus venue
list. Students, registrations, venue blocks and the exam period are synthetic
(blocks are labelled "simulated"). The same
``SeedAssumptions`` (and catalogue file) always produce exactly the same
dataset: all randomness comes from one ``random.Random(random_seed)`` and
inputs are sorted before sampling. Nothing here touches the database.
"""

from __future__ import annotations

import datetime as dt
import math
import random
from collections.abc import Iterable

from app.seed.assumptions import DEFAULT_ASSUMPTIONS, SeedAssumptions
from app.seed.catalog import CatalogModule, CatalogVenue, load_catalog, load_venues
from app.seed.records import (
    Dataset,
    ExamPeriod,
    ExamPeriodExclusion,
    Module,
    ModuleSpecialization,
    Registration,
    Student,
    Venue,
    VenueBlock,
)


def generate_dataset(
    assumptions: SeedAssumptions = DEFAULT_ASSUMPTIONS,
    catalog: tuple[CatalogModule, ...] | None = None,
    venue_catalog: tuple[CatalogVenue, ...] | None = None,
) -> Dataset:
    a = assumptions
    rng = random.Random(a.random_seed)
    catalog = catalog if catalog is not None else load_catalog(a.catalog_path)
    venue_catalog = venue_catalog if venue_catalog is not None else load_venues(a.venue_catalog_path)

    in_scope = sorted(
        (m for m in catalog if m.semester == a.semester and set(m.specializations) & set(a.specializations)),
        key=lambda m: m.module_code,
    )
    modules = tuple(_module(m, a) for m in in_scope)
    # Keep every specialization of each module, including ones outside the PP1 scope.
    links = tuple(ModuleSpecialization(m.module_code, s) for m in in_scope for s in sorted(m.specializations))

    students = _students(a, rng)
    registrations = _registrations(a, rng, in_scope, students)
    venues = _venues(a, venue_catalog)  # no randomness
    period, exclusions = _exam_period(a)
    blocks = _venue_blocks(a, rng, venues, exam_days(period, exclusions))

    return Dataset(
        modules=modules,
        module_specializations=links,
        students=students,
        registrations=registrations,
        venues=venues,
        venue_blocks=blocks,
        exam_periods=(period,),
        exam_period_exclusions=exclusions,
    )


def exam_days(period: ExamPeriod, exclusions: Iterable[ExamPeriodExclusion]) -> tuple[dt.date, ...]:
    """Dates in the period on which exams may be held."""
    excluded = {e.excluded_date for e in exclusions if e.period_code == period.period_code}
    days = []
    day = period.start_date
    while day <= period.end_date:
        weekday = day.weekday()  # Monday = 0
        allowed = (
            weekday < 5
            or (weekday == 5 and period.include_saturdays)
            or (weekday == 6 and period.include_sundays)
        )
        if allowed and day not in excluded:
            days.append(day)
        day += dt.timedelta(days=1)
    return tuple(days)


def _module(m: CatalogModule, a: SeedAssumptions) -> Module:
    return Module(
        module_code=m.module_code,
        module_name=m.module_name,
        academic_year=m.academic_year,
        semester=m.semester,
        credits=m.credits,
        difficulty=m.difficulty,
        exam_type=m.exam_type,
        duration_minutes=m.duration_minutes,
        has_extra_reading_time=m.has_extra_reading_time,
        reading_time_minutes=a.reading_time_minutes if m.has_extra_reading_time else 0,
    )


def _students(a: SeedAssumptions, rng: random.Random) -> tuple[Student, ...]:
    total = sum(size for _, _, size in a.cohort_sizes)
    numbers = rng.sample(range(100_000, 1_000_000), total)  # unique 6-digit serials
    students = []
    for year, specialization, size in a.cohort_sizes:
        intake = a.student_intake_base + (4 - year)  # year 4 = oldest intake
        for _ in range(size):
            student_id = f"{a.student_id_prefix}{intake:02d}{numbers.pop():06d}"
            students.append(Student(student_id, year, specialization))
    return tuple(sorted(students, key=lambda s: s.student_id))


def _registrations(
    a: SeedAssumptions,
    rng: random.Random,
    modules: list[CatalogModule],
    students: tuple[Student, ...],
) -> tuple[Registration, ...]:
    registrations = [
        Registration(s.student_id, m.module_code, "normal")
        for s in students
        for m in modules
        if m.academic_year == s.academic_year and s.specialization in m.specializations
    ]

    # Repeat students also re-sit earlier-year modules of their specialization.
    # These cross-year registrations create the hardest student clashes.
    eligible = [s for s in students if s.academic_year >= 2]
    chosen = rng.sample(eligible, round(len(eligible) * a.repeat_student_fraction))
    for student in sorted(chosen, key=lambda s: s.student_id):
        candidates = [
            m for m in modules
            if m.academic_year < student.academic_year and student.specialization in m.specializations
        ]
        count = min(rng.randint(1, a.max_repeat_modules_per_student), len(candidates))
        for module in rng.sample(candidates, count):
            registrations.append(Registration(student.student_id, module.module_code, "repeat"))

    return tuple(sorted(registrations, key=lambda r: (r.student_id, r.module_code)))


def exam_capacity(full_capacity: int, a: SeedAssumptions = DEFAULT_ASSUMPTIONS) -> int:
    """Seats usable in an exam: floor(full capacity x ratio), for every venue type."""
    return math.floor(full_capacity * a.exam_seating_ratio)


def _venues(a: SeedAssumptions, catalog: tuple[CatalogVenue, ...]) -> tuple[Venue, ...]:
    return tuple(
        sorted(
            (
                Venue(
                    venue_id=v.venue_id,
                    venue_name=v.venue_name,
                    venue_type=v.venue_type,
                    building=v.building,
                    floor=v.floor,
                    normal_capacity=v.full_capacity,
                    exam_capacity=exam_capacity(v.full_capacity, a),
                )
                for v in catalog
            ),
            key=lambda v: v.venue_id,
        )
    )


def _exam_period(a: SeedAssumptions) -> tuple[ExamPeriod, tuple[ExamPeriodExclusion, ...]]:
    period = ExamPeriod(
        period_code=a.period_code,
        period_name=a.period_name,
        academic_year_label=a.academic_year_label,
        semester=a.semester,
        start_date=a.period_start,
        end_date=a.period_end,
        day_start_time=a.day_start,
        day_end_time=a.day_end,
        include_saturdays=a.include_saturdays,
        include_sundays=a.include_sundays,
    )
    exclusions = tuple(ExamPeriodExclusion(a.period_code, day, reason) for day, reason in a.excluded_dates)
    return period, exclusions


def _overlaps(block: VenueBlock, others: Iterable[VenueBlock]) -> bool:
    return any(
        o.venue_id == block.venue_id
        and o.block_date == block.block_date
        and block.start_time < o.end_time
        and o.start_time < block.end_time
        for o in others
    )


def _venue_blocks(
    a: SeedAssumptions, rng: random.Random, venues: tuple[Venue, ...], days: tuple[dt.date, ...]
) -> tuple[VenueBlock, ...]:
    # Simulated teaching sessions that keep running in some rooms during exams.
    # Real sessions will come from the Teaching Timetable (source "teaching_timetable").
    teaching_candidates = [
        VenueBlock(v.venue_id, day, start, end, "simulated_teaching", a.simulated_teaching_reason)
        for v in venues
        if v.venue_type in a.teaching_venue_types
        for day in days
        for start, end in a.teaching_block_slots
    ]
    # Teaching slots never overlap each other, so a plain sample is conflict-free.
    blocks = rng.sample(teaching_candidates, min(a.teaching_block_count, len(teaching_candidates)))

    maintenance_candidates = [
        VenueBlock(v.venue_id, day, start, end, "simulated_maintenance", a.simulated_maintenance_reason)
        for v in venues
        for day in days
        for start, end in a.maintenance_block_slots
    ]
    rng.shuffle(maintenance_candidates)
    added = 0
    for candidate in maintenance_candidates:
        if added == a.maintenance_block_count:
            break
        if not _overlaps(candidate, blocks):
            blocks.append(candidate)
            added += 1

    return tuple(sorted(blocks, key=lambda b: (b.venue_id, b.block_date, b.start_time)))
