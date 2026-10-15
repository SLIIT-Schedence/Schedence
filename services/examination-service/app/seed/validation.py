"""Validation run before any seed data is written.

``validate_dataset`` returns problems instead of raising, so the seed script
can print all of them at once. ``errors`` block seeding; ``warnings`` are
facts worth knowing (e.g. a module needs several rooms or sessions).
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from app.db.seed_models import BLOCK_SOURCES, EXAM_TYPES, REGISTRATION_TYPES, SIMULATED_BLOCK_SOURCES, VENUE_TYPES
from app.seed.assumptions import DEFAULT_ASSUMPTIONS, SeedAssumptions
from app.seed.generator import exam_capacity, exam_days
from app.seed.records import Dataset


@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _duplicates(values) -> list:
    return sorted(v for v, n in Counter(values).items() if n > 1)


def validate_dataset(data: Dataset, assumptions: SeedAssumptions = DEFAULT_ASSUMPTIONS) -> ValidationReport:
    report = ValidationReport()
    err = report.errors.append

    # --- Uniqueness ------------------------------------------------------------
    checks = {
        "module_code": [m.module_code for m in data.modules],
        "student_id": [s.student_id for s in data.students],
        "venue_id": [v.venue_id for v in data.venues],
        "venue_name": [v.venue_name for v in data.venues],
        "period_code": [p.period_code for p in data.exam_periods],
        "module specialization": [(x.module_code, x.specialization) for x in data.module_specializations],
        "registration (student_id, module_code)": [(r.student_id, r.module_code) for r in data.registrations],
        "venue block (venue_id, block_date, start_time)": [
            (b.venue_id, b.block_date, b.start_time) for b in data.venue_blocks
        ],
        "exclusion (period_code, excluded_date)": [
            (e.period_code, e.excluded_date) for e in data.exam_period_exclusions
        ],
    }
    for label, values in checks.items():
        for dup in _duplicates(values):
            err(f"duplicate {label}: {dup}")

    # --- Modules ---------------------------------------------------------------
    specs = data.specializations_by_module()
    for m in data.modules:
        if not 1 <= m.academic_year <= 4:
            err(f"module {m.module_code}: academic_year {m.academic_year} not in 1-4")
        if m.semester not in (1, 2):
            err(f"module {m.module_code}: semester {m.semester} not 1 or 2")
        if not 1 <= m.credits <= 10:
            err(f"module {m.module_code}: credits {m.credits} not in 1-10")
        if not 1 <= m.difficulty <= 5:
            err(f"module {m.module_code}: difficulty {m.difficulty} not in 1-5")
        if m.exam_type not in EXAM_TYPES:
            err(f"module {m.module_code}: unknown exam_type {m.exam_type!r}")
        if m.duration_minutes <= 0:
            err(f"module {m.module_code}: duration must be positive")
        if m.has_extra_reading_time != (m.reading_time_minutes > 0) or m.reading_time_minutes < 0:
            err(f"module {m.module_code}: reading time minutes do not match the reading-time flag")
        if not specs.get(m.module_code):
            err(f"module {m.module_code}: has no specialization")
    module_codes = {m.module_code for m in data.modules}
    for link in data.module_specializations:
        if link.module_code not in module_codes:
            err(f"specialization link {link.module_code}/{link.specialization}: unknown module")

    # --- Students --------------------------------------------------------------
    id_pattern = re.compile(rf"^{re.escape(assumptions.student_id_prefix)}\d{{8}}$")
    for s in data.students:
        if not id_pattern.match(s.student_id):
            err(f"student {s.student_id}: ID does not match the synthetic format")
        if not 1 <= s.academic_year <= 4:
            err(f"student {s.student_id}: academic_year {s.academic_year} not in 1-4")
        if s.specialization not in assumptions.specializations:
            err(f"student {s.student_id}: specialization {s.specialization!r} not in scope")

    # --- Registrations -----------------------------------------------------------
    modules = {m.module_code: m for m in data.modules}
    students = {s.student_id: s for s in data.students}
    for r in data.registrations:
        label = f"registration {r.student_id}/{r.module_code}"
        student, module = students.get(r.student_id), modules.get(r.module_code)
        if student is None:
            err(f"{label}: unknown student")
        if module is None:
            err(f"{label}: unknown module")
        if r.registration_type not in REGISTRATION_TYPES:
            err(f"{label}: unknown type {r.registration_type!r}")
        if student is None or module is None:
            continue
        if student.specialization not in specs.get(module.module_code, ()):
            err(f"{label}: module is not offered to the student's specialization")
        if r.registration_type == "normal" and module.academic_year != student.academic_year:
            err(f"{label}: normal registration must be in the student's own year")
        if r.registration_type == "repeat" and module.academic_year >= student.academic_year:
            err(f"{label}: repeat registration must be for an earlier year")

    counts = data.registration_counts()
    for code, n in counts.items():
        if n == 0:
            report.warnings.append(f"module {code} has no registrations")

    # --- Venues ----------------------------------------------------------------
    for v in data.venues:
        if v.venue_type not in VENUE_TYPES:
            err(f"venue {v.venue_id}: unknown venue_type {v.venue_type!r}")
        if v.normal_capacity <= 0:
            err(f"venue {v.venue_id}: normal capacity must be positive")
        if not 0 < v.exam_capacity <= v.normal_capacity:
            err(f"venue {v.venue_id}: exam capacity must be > 0 and <= normal capacity")
        if v.exam_capacity != exam_capacity(v.normal_capacity, assumptions):
            err(f"venue {v.venue_id}: exam capacity {v.exam_capacity} must be floor("
                f"{v.normal_capacity} x {assumptions.exam_seating_ratio}) = {exam_capacity(v.normal_capacity, assumptions)}")
        if not v.building or not v.building.strip():
            err(f"venue {v.venue_id}: building is missing")
        if not isinstance(v.floor, int):
            err(f"venue {v.venue_id}: floor must be a whole number")

    # --- Exam periods ------------------------------------------------------------
    periods = {p.period_code: p for p in data.exam_periods}
    if not periods:
        err("no examination period defined")
    for p in data.exam_periods:
        if p.end_date < p.start_date:
            err(f"period {p.period_code}: end_date is before start_date")
        if p.day_end_time <= p.day_start_time:
            err(f"period {p.period_code}: daily end time must be after start time")
        if not exam_days(p, data.exam_period_exclusions):
            err(f"period {p.period_code}: has no examination days")
    for e in data.exam_period_exclusions:
        p = periods.get(e.period_code)
        if p is None:
            err(f"exclusion {e.excluded_date}: unknown period {e.period_code}")
        elif not p.start_date <= e.excluded_date <= p.end_date:
            err(f"exclusion {e.excluded_date}: outside period {e.period_code}")

    # --- Venue blocks --------------------------------------------------------------
    venue_ids = {v.venue_id for v in data.venues}
    for b in data.venue_blocks:
        label = f"block {b.venue_id} {b.block_date} {b.start_time}"
        if b.venue_id not in venue_ids:
            err(f"{label}: unknown venue")
        if b.end_time <= b.start_time:
            err(f"{label}: end time must be after start time")
        if b.source not in BLOCK_SOURCES:
            err(f"{label}: unknown source {b.source!r}")
        if b.source in SIMULATED_BLOCK_SOURCES and not b.reason.startswith("SIMULATED"):
            err(f"{label}: simulated block must be labelled 'SIMULATED' in its reason")
        if not any(p.start_date <= b.block_date <= p.end_date for p in data.exam_periods):
            err(f"{label}: date is outside every examination period")
    by_slot: dict[tuple, list] = defaultdict(list)
    for b in data.venue_blocks:
        by_slot[(b.venue_id, b.block_date)].append(b)
    for (venue_id, day), blocks in by_slot.items():
        blocks.sort(key=lambda b: b.start_time)
        for first, second in zip(blocks, blocks[1:]):
            if second.start_time < first.end_time:
                err(f"overlapping blocks for {venue_id} on {day}: {first.start_time} and {second.start_time}")

    # --- Venue compatibility and capacity ------------------------------------------
    for code, n in sorted(counts.items()):
        module = modules[code]
        allowed = assumptions.venue_types_for(module.exam_type, code)
        capacities = [v.exam_capacity for v in data.venues if v.venue_type in allowed]
        if not capacities:
            err(f"module {code}: no venue of a compatible type ({', '.join(allowed)})")
        elif n > sum(capacities):
            err(f"module {code}: {n} candidates exceed all compatible capacity {sum(capacities)}")
        else:
            rooms = rooms_needed(n, capacities)
            limit = assumptions.max_rooms_per_exam
            if limit is not None and rooms > limit:
                err(f"module {code}: {n} candidates need {rooms} rooms, more than max_rooms_per_exam={limit}")
            elif rooms > 1:
                report.warnings.append(f"module {code} ({module.exam_type}): {n} candidates need at least {rooms} rooms")

    return report


def rooms_needed(candidates: int, capacities: list[int]) -> int:
    """Fewest rooms that can seat ``candidates`` (largest rooms first)."""
    seated = 0
    for rooms, capacity in enumerate(sorted(capacities, reverse=True), start=1):
        seated += capacity
        if seated >= candidates:
            return rooms
    return len(capacities) + 1  # cannot be seated at all
