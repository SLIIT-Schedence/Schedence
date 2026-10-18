"""Read once, freeze, and check relationships before constructing a solver model.

DatasetSource is already an immutable synthetic dataset. For DatabaseSource use
load_database_snapshot: *all* reads then share one repeatable-read transaction.
Neither loader writes data or leaves a transaction open during the solve.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from dataclasses import replace

from sqlalchemy.ext.asyncio import AsyncEngine

from app.scheduling.policy import SchedulingPolicy, minute_time, whole_number
from app.scheduling.contracts import SchedulingValidationError, data_shape_errors, frozen_sequence, text_errors
from app.scheduling.types import InputSnapshot, Violation
from app.seating.snapshot import load_capacity_snapshot, snapshot_session
from app.seating.store import DatabaseSeatingStore, SeatingRuleStore
from app.sources import DatabaseSource, ExamDataSource
from app.sources.types import ExamPeriod, ExamPeriodExclusion, Module, Page, Registration, Student, Venue, VenueBlock


class SnapshotError(SchedulingValidationError):
    """Structured error at the data-source loading boundary."""


def permitted_days(period: ExamPeriod) -> tuple[dt.date, ...]:
    excluded = {e.excluded_date for e in period.exclusions}
    return tuple(day for offset in range((period.end_date - period.start_date).days + 1)
                 for day in (period.start_date + dt.timedelta(days=offset),)
                 if day not in excluded and (day.weekday() < 5
                    or (day.weekday() == 5 and period.include_saturdays)
                    or (day.weekday() == 6 and period.include_sundays)))


def validate_inputs(snapshot: InputSnapshot) -> tuple[Violation, ...]:
    if type(snapshot) is not InputSnapshot:
        return (Violation("H11", "Scheduling input must be an InputSnapshot."),)
    shapes = data_shape_errors(snapshot.period, snapshot.modules, snapshot.students, snapshot.registrations,
                               snapshot.venues, snapshot.blocks, snapshot.capacities)
    if shapes:
        return shapes
    errors: list[Violation] = []

    def error(message: str, constraint: str = "H11") -> None:
        errors.append(Violation(constraint, message))

    for message in snapshot.policy.errors():
        error(message)
    for label, rows, key in (
        ("module", snapshot.modules, lambda r: r.module_code),
        ("student", snapshot.students, lambda r: r.student_id),
        ("registration", snapshot.registrations, lambda r: (r.student_id, r.module_code)),
        ("venue", snapshot.venues, lambda r: r.venue_id),
        ("capacity", snapshot.capacities.venues, lambda r: r.venue_id),
    ):
        ids = [key(row) for row in rows]
        if len(ids) != len(set(ids)):
            error(f"Duplicate {label} identifiers.")
        if any(not value or (isinstance(value, str) and not value.strip()) for value in ids):
            error(f"Empty {label} identifier.")
    modules = {m.module_code: m for m in snapshot.modules}
    students = {s.student_id: s for s in snapshot.students}
    venues = {v.venue_id: v for v in snapshot.venues}
    if not modules:
        error("Select at least one module.")
    counts = Counter(r.module_code for r in snapshot.registrations)
    for r in snapshot.registrations:
        if r.module_code not in modules or r.student_id not in students:
            error(f"Registration {r.student_id}/{r.module_code} has an unknown module or student.")
        if r.registration_type not in ("normal", "repeat"):
            error(f"Unknown registration type for {r.student_id}/{r.module_code}.")
    for m in snapshot.modules:
        if not whole_number(m.duration_minutes, 1) or not whole_number(m.reading_time_minutes):
            error(f"Module {m.module_code} needs a positive duration and non-negative whole reading minutes.", "H9")
        if (type(m.has_extra_reading_time) is not bool or (whole_number(m.reading_time_minutes)
                and m.has_extra_reading_time != (m.reading_time_minutes > 0))):
            error(f"Module {m.module_code} has inconsistent reading-time fields.", "H9")
        if not whole_number(m.candidate_count, 1) or m.candidate_count != counts[m.module_code]:
            error(f"Module {m.module_code} candidate count must match its non-empty registrations.", "H10")
        if m.exam_type not in dict(snapshot.policy.venue_compatibility):
            error(f"No venue compatibility policy for exam type {m.exam_type}.", "H3")
    if any(code not in modules for code, _ in snapshot.policy.module_venue_overrides):
        error("A module venue override refers to an unselected module.")
    cap_ids = {v.venue_id for v in snapshot.capacities.venues}
    if cap_ids != set(venues):
        error("Capacity snapshot and source must contain the same venues.", "H2")
    for c in snapshot.capacities.venues:
        v = venues.get(c.venue_id)
        if (not whole_number(c.normal_capacity, 1) or not whole_number(c.percentage, 1)
                or c.percentage > 100 or not whole_number(c.exam_capacity)
                or c.exam_capacity != c.normal_capacity * c.percentage // 100
                or c.rule_scope not in ("default", "venue")
                or (v is not None and (v.normal_capacity != c.normal_capacity or v.venue_type != c.venue_type))):
            error(f"Invalid effective capacity for venue {c.venue_id}.", "H2")
    p = snapshot.period
    dates_valid = type(p.start_date) is dt.date and type(p.end_date) is dt.date and p.end_date >= p.start_date
    if not p.period_code or not dates_valid:
        error("Examination period identifier or date range is invalid.", "H6")
    if not minute_time(p.day_start_time) or not minute_time(p.day_end_time) or p.day_end_time <= p.day_start_time:
        error("Examination hours must be local whole-minute times, with end after start.", "H7")
    if type(p.include_saturdays) is not bool or type(p.include_sundays) is not bool:
        error("Weekend flags must be boolean.", "H8")
    if len({e.excluded_date for e in p.exclusions}) != len(p.exclusions):
        error("Duplicate excluded dates.", "H8")
    for e in p.exclusions:
        if e.period_code != p.period_code or type(e.excluded_date) is not dt.date:
            error("Exclusion refers to an invalid date or a different period.", "H8")
        elif dates_valid and not p.start_date <= e.excluded_date <= p.end_date:
            error("Excluded date is outside the selected period.", "H8")
    if dates_valid and all(type(e.excluded_date) is dt.date for e in p.exclusions):
        if tuple(p.exam_days) != permitted_days(p):
            error("Cached exam days disagree with the period, weekend flags, or exclusions.", "H8")
    for b in snapshot.blocks:
        if b.venue_id not in venues:
            error(f"Availability block refers to unknown venue {b.venue_id}.")
        if (type(b.block_date) is not dt.date or not minute_time(b.start_time)
                or not minute_time(b.end_time) or b.end_time <= b.start_time):
            error(f"Invalid availability block for {b.venue_id}.", "H4")
        if not b.source or not b.reason:
            error(f"Availability block for {b.venue_id} needs its source and reason.")
    return tuple(errors)


async def load_snapshot(
    source: ExamDataSource,
    store: SeatingRuleStore,
    *,
    period_code: str,
    policy: SchedulingPolicy = SchedulingPolicy(),
    module_codes: tuple[str, ...] | None = None,
) -> InputSnapshot:
    """Read a coherent source. Database callers must use load_database_snapshot.

    module_codes=None selects all offered modules. Explicit selection is used
    because current source records have no registration-to-period foreign key.
    """
    errors = text_errors(period_code, "period_code")
    if errors:
        raise SnapshotError(errors)
    if type(policy) is not SchedulingPolicy:
        raise SnapshotError((Violation("H11", "policy must be a SchedulingPolicy."),))
    try:
        if module_codes is not None:
            module_codes = frozen_sequence(module_codes, "module_codes", str)
        period = await source.get_exam_period(period_code)
        if period is None:
            raise SnapshotError((Violation("H6", f"Unknown exam period {period_code}."),))
        if type(period) is not ExamPeriod:
            raise SnapshotError((Violation("H11", "Source period must be an ExamPeriod."),))
        period = replace(period,
            exclusions=frozen_sequence(period.exclusions, "period exclusions", ExamPeriodExclusion),
            exam_days=frozen_sequence(period.exam_days, "exam_days", dt.date))
        all_modules = frozen_sequence(await source.list_modules(), "modules", Module)
        all_modules = tuple(replace(m, specializations=frozen_sequence(m.specializations, "module specializations", str))
                            for m in all_modules)
        students = await source.list_students(limit=None)
        registrations = await source.list_registrations(limit=None)
        venues = frozen_sequence(await source.list_venues(), "venues", Venue)
        if type(students) is not Page or type(registrations) is not Page:
            raise SnapshotError((Violation("H11", "Source students and registrations must be Page results."),))
        students = replace(students, items=frozen_sequence(students.items, "students", Student))
        registrations = replace(registrations, items=frozen_sequence(registrations.items, "registrations", Registration))
    except SchedulingValidationError as exc:
        raise SnapshotError(exc.violations) from exc
    errors = data_shape_errors(period, all_modules, students.items, registrations.items, venues, ())
    if errors:
        raise SnapshotError(errors)
    try:
        blocks = frozen_sequence(await source.list_venue_blocks(date_from=period.start_date, date_to=period.end_date),
                                 "blocks", VenueBlock)
    except SchedulingValidationError as exc:
        raise SnapshotError(exc.violations) from exc
    errors = data_shape_errors(period, (), (), (), (), blocks)
    if errors:
        raise SnapshotError(errors)
    capacities = await load_capacity_snapshot(source, store)
    if len(students.items) != students.total or len(registrations.items) != registrations.total:
        raise SnapshotError((Violation("H11", "Source returned incomplete students or registrations."),))
    known = {m.module_code for m in all_modules}
    selected = known if module_codes is None else set(module_codes)
    if not selected <= known or (module_codes is not None and len(module_codes) != len(selected)):
        raise SnapshotError((Violation("H11", "Selected modules contain unknown or duplicate codes."),))
    if any(r.module_code not in known for r in registrations.items):
        raise SnapshotError((Violation("H11", "Source contains a registration for an unknown module."),))
    snapshot = InputSnapshot(
        period=replace(period, exclusions=tuple(period.exclusions), exam_days=tuple(period.exam_days)),
        modules=tuple(replace(m, specializations=tuple(m.specializations))
                      for m in sorted(all_modules, key=lambda m: m.module_code) if m.module_code in selected),
        students=tuple(sorted(students.items, key=lambda s: s.student_id)),
        registrations=tuple(sorted((r for r in registrations.items if r.module_code in selected),
                                   key=lambda r: (r.student_id, r.module_code))),
        venues=tuple(sorted(venues, key=lambda v: v.venue_id)),
        blocks=tuple(sorted(blocks, key=lambda b: (b.venue_id, b.block_date, b.start_time, b.source))),
        capacities=replace(capacities, venues=tuple(capacities.venues)),
        policy=policy,
    )
    errors = validate_inputs(snapshot)
    if errors:
        raise SnapshotError(errors)
    return snapshot


async def load_database_snapshot(engine: AsyncEngine, **kwargs) -> InputSnapshot:
    """Read-only input adapter; not used by the synthetic CLI. Opens no other service."""
    async with snapshot_session(engine) as session:
        return await load_snapshot(DatabaseSource(session), DatabaseSeatingStore(session), **kwargs)
