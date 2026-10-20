"""Read once, freeze, and check relationships before constructing a solver model.

DatasetSource is already an immutable synthetic dataset. For DatabaseSource use
load_database_snapshot: *all* reads then share one repeatable-read transaction.
Neither loader writes data or leaves a transaction open during the solve.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from collections.abc import Awaitable, Callable, Collection
from dataclasses import replace
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.seed_models import EXAM_KINDS
from app.exam_periods.rules import CALENDAR_RULES, ExamCalendar, apply_calendar
from app.scheduling.policy import SchedulingPolicy, minute_time, whole_number
from app.scheduling.protected import ProtectedSchedule
from app.scheduling.contracts import SchedulingValidationError, data_shape_errors, frozen_sequence, text_errors
from app.scheduling.sessions import required_sessions, sitting_capacity
from app.scheduling.types import ExcludedModule, InputSnapshot, Violation
from app.seating.snapshot import load_capacity_snapshot, snapshot_session
from app.seating.store import DatabaseSeatingStore, SeatingRuleStore
from app.sources import DatabaseSource, ExamDataSource
from app.sources.exam_rules import cycle_candidate, cycle_member_errors, exam_duration_minutes, module_exam_errors
from app.sources.types import ExamPeriod, ExamPeriodExclusion, ExamPeriodModule, Module, Page, Registration, Student, Venue, VenueBlock


class SnapshotError(SchedulingValidationError):
    """Structured error at the data-source loading boundary."""


# (the snapshot's period, its candidate student ids) -> the protected timetables overlapping that period
ProtectedLoader = Callable[[ExamPeriod, Collection[str]], Awaitable[ProtectedSchedule]]


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
        problems = module_exam_errors(m)
        for problem in problems:
            error(f"Module {m.module_code}: {problem}.", "H9")
        if not problems and exam_duration_minutes(m, snapshot.period.exam_kind) != m.duration_minutes:
            error(f"Module {m.module_code} has no {snapshot.period.exam_kind} examination of "
                  f"{m.duration_minutes} minutes.", "H9")
    if not snapshot.policy.errors():
        for m in snapshot.modules:
            if whole_number(m.candidate_count, 1) and m.exam_type in dict(snapshot.policy.venue_compatibility):
                needed = required_sessions(snapshot, m)
                if needed > snapshot.policy.max_sessions_per_exam:
                    error(f"Module {m.module_code} needs {needed} sessions for {m.candidate_count} candidates "
                          f"(one sitting seats at most {sitting_capacity(snapshot, m)}); the configured maximum is "
                          f"{snapshot.policy.max_sessions_per_exam} sessions per exam.", "H10")
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
    if p.exam_kind not in EXAM_KINDS:
        error(f"Examination period kind must be one of {', '.join(EXAM_KINDS)}.")
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
    if snapshot.cycle_modules:
        scopes = {c.module_code: c.candidate_scope for c in snapshot.cycle_modules}
        if (len(scopes) != len(snapshot.cycle_modules) or set(scopes) != set(modules)
                or any(c.period_code != p.period_code for c in snapshot.cycle_modules)):
            error("Cycle mapping rows must match the selected modules of this period exactly.")
        for code, scope in scopes.items():
            if code in modules:
                for problem in cycle_member_errors(p, modules[code], scope):
                    error(f"Cycle module {code} {problem}.")
        if any(not cycle_candidate(r, scopes.get(r.module_code, "all")) for r in snapshot.registrations):
            error("Only repeat registrations may be examined for a repeat_only cycle module.", "H10")
    return tuple(errors)


async def load_snapshot(
    source: ExamDataSource,
    store: SeatingRuleStore,
    *,
    period_code: str,
    policy: SchedulingPolicy = SchedulingPolicy(),
    module_codes: tuple[str, ...] | None = None,
    from_cycle: bool = False,
    calendar: ExamCalendar | None = None,
    seating_preferences: dict | None = None,
    protected: ProtectedLoader | None = None,
) -> InputSnapshot:
    """Read a coherent source. Database callers must use load_database_snapshot.

    protected (optional) reads the APPROVED / PUBLISHED timetables of other cycles that overlap the
    period's dates. They become fixed constraints of the solver and of the independent validator. Without
    it (library, command line, older tests) nothing outside this cycle is considered.

    calendar (optional) replaces the period's dates, daily hours, weekends and excluded dates for this
    snapshot only; the saved period is not changed. Every rule, venue blocks and the fingerprint use it.

    from_cycle=True (the generation API) uses the period's cycle module mapping:
    module_codes=None schedules every mapped module; listed modules must all be
    mapped (partial generation). Only registrations of the cycle are loaded:
    every registration of an ``all`` module, only repeat registrations of a
    ``repeat_only`` module. Without a mapping, listed modules of the period's
    semester are still accepted (testing), and an empty list is refused.

    from_cycle=False (library and command line): module_codes=None selects all
    modules with an examination of the period's kind (MID or FINAL).
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
        if calendar is not None:
            if type(calendar) is not ExamCalendar:
                raise SnapshotError((Violation("H11", "calendar must be an ExamCalendar."),))
            period, problems = apply_calendar(period, calendar)
            if problems:
                raise SnapshotError(tuple(Violation(CALENDAR_RULES.get(p["rule"], "H8"), f"Selected calendar: {p['message']}")
                                          for p in problems))
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
    capacities = await load_capacity_snapshot(source, store, preferences=seating_preferences)
    if len(students.items) != students.total or len(registrations.items) != registrations.total:
        raise SnapshotError((Violation("H11", "Source returned incomplete students or registrations."),))
    known = {m.module_code for m in all_modules}
    kind = period.exam_kind
    if module_codes is not None and len(module_codes) != len(set(module_codes)):
        raise SnapshotError((Violation("H11", "Selected modules contain unknown or duplicate codes."),))
    if any(r.module_code not in known for r in registrations.items):
        raise SnapshotError((Violation("H11", "Source contains a registration for an unknown module."),))
    members = ()
    if from_cycle:
        try:
            members = frozen_sequence(await source.list_exam_period_modules(period.period_code),
                                      "cycle modules", ExamPeriodModule)
        except SchedulingValidationError as exc:
            raise SnapshotError(exc.violations) from exc
        if not members and module_codes is None:
            raise SnapshotError((Violation("H11", f"Exam period {period.period_code} has no cycle module mapping. "
                                                  "Configure the cycle's modules, or list module_codes."),))
    excluded = ()
    if members:
        excluded = await _excluded_modules(source, period, all_modules, members, registrations.items)
        selected, members = _cycle_selection(period, all_modules, members, module_codes, registrations.items)
    else:
        # No cycle mapping: the listed modules (or, without a list, every module
        # with an examination of the period's kind; not used by the API).
        selected = ({m.module_code for m in all_modules if exam_duration_minutes(m, kind) is not None}
                    if module_codes is None else set(module_codes))
        if not selected <= known:
            raise SnapshotError((Violation("H11", "Selected modules contain unknown or duplicate codes."),))
        ineligible = sorted(m.module_code for m in all_modules
                            if m.module_code in selected and exam_duration_minutes(m, kind) is None)
        if ineligible:
            raise SnapshotError((Violation("H11", f"Modules without a {kind} examination cannot be scheduled in "
                                                  f"period {period.period_code}: {', '.join(ineligible)}."),))
        if from_cycle:  # listed without a mapping: only modules of the period's semester
            errors = tuple(Violation("H6", f"Module {m.module_code} is a semester {m.semester} module; exam period "
                                           f"{period.period_code} is for semester {period.semester}.")
                           for m in sorted(all_modules, key=lambda m: m.module_code)
                           if m.module_code in selected and m.semester != period.semester)
            if errors:
                raise SnapshotError(errors)
    scopes = {m.module_code: m.candidate_scope for m in members}
    included = tuple(sorted((r for r in registrations.items if r.module_code in selected
                             and cycle_candidate(r, scopes.get(r.module_code, "all"))),
                            key=lambda r: (r.student_id, r.module_code)))
    counts = Counter(r.module_code for r in included)
    protected_schedule = None
    if protected is not None:
        protected_schedule = await protected(period, {r.student_id for r in included})
        if type(protected_schedule) is not ProtectedSchedule:
            raise SnapshotError((Violation("H11", "Protected timetables must be a ProtectedSchedule."),))
    snapshot = InputSnapshot(
        period=replace(period, exclusions=tuple(period.exclusions), exam_days=tuple(period.exam_days)),
        # duration_minutes becomes the duration of the period's kind (MID or FINAL),
        # and candidate_count counts only this cycle's candidates, so the solver
        # and the H9/H10 checks use them unchanged.
        modules=tuple(replace(m, specializations=tuple(m.specializations),
                              duration_minutes=exam_duration_minutes(m, kind), candidate_count=counts[m.module_code])
                      for m in sorted(all_modules, key=lambda m: m.module_code) if m.module_code in selected),
        students=tuple(sorted(students.items, key=lambda s: s.student_id)),
        registrations=included,
        cycle_modules=members,
        excluded_modules=excluded,
        venues=tuple(sorted(venues, key=lambda v: v.venue_id)),
        blocks=tuple(sorted(blocks, key=lambda b: (b.venue_id, b.block_date, b.start_time, b.source))),
        capacities=replace(capacities, venues=tuple(capacities.venues)),
        policy=policy,
        protected=protected_schedule,
    )
    errors = validate_inputs(snapshot)
    if errors:
        raise SnapshotError(errors)
    return snapshot


async def _excluded_modules(source, period, all_modules, members, registrations) -> tuple[ExcludedModule, ...]:
    """Per module, registrations left out because the module is not in this cycle's mapping (counts only)."""
    mapped = {m.module_code for m in members}
    counts = Counter((r.module_code, r.registration_type) for r in registrations if r.module_code not in mapped)
    codes = sorted({code for code, _ in counts})
    if not codes:
        return ()
    elsewhere: dict[str, list[str]] = {code: [] for code in codes}
    try:
        for other in await source.list_exam_periods():
            if other.period_code != period.period_code:
                for row in await source.list_exam_period_modules(other.period_code):
                    if row.module_code in elsewhere:
                        elsewhere[row.module_code].append(other.period_code)
    except SchedulingValidationError as exc:
        raise SnapshotError(exc.violations) from exc
    modules = {m.module_code: m for m in all_modules}
    return tuple(ExcludedModule(code, modules[code].module_name, modules[code].academic_year, modules[code].semester,
                                counts[code, "normal"], counts[code, "repeat"], tuple(sorted(elsewhere[code])))
                 for code in codes)


def _cycle_selection(period, all_modules, members, module_codes, registrations):
    """Selected module codes and their mapping rows; raises SnapshotError listing every problem."""
    errors: list[Violation] = []
    modules = {m.module_code: m for m in all_modules}
    keys = [m.module_code for m in members]
    if len(keys) != len(set(keys)):
        errors.append(Violation("H11", f"Cycle {period.period_code} maps a module more than once."))
    if any(m.period_code != period.period_code for m in members):
        errors.append(Violation("H11", "The cycle mapping contains rows of another period."))
    unknown = sorted(set(keys) - set(modules))
    if unknown:
        errors.append(Violation("H11", f"Cycle {period.period_code} maps unknown modules: {', '.join(unknown)}."))
    if module_codes is None:
        selected = set(keys)
    else:
        selected = set(module_codes)
        outside = sorted(selected - set(keys))
        if outside:
            errors.append(Violation("H11", f"Modules not in cycle {period.period_code}: {', '.join(outside)}."))
    chosen = tuple(m for m in sorted(members, key=lambda m: m.module_code) if m.module_code in selected)
    for member in chosen:
        module = modules.get(member.module_code)
        if module is None:
            continue
        for problem in cycle_member_errors(period, module, member.candidate_scope):
            errors.append(Violation("H11", f"Cycle module {member.module_code} {problem}."))
        if not any(r.module_code == member.module_code and cycle_candidate(r, member.candidate_scope)
                   for r in registrations):
            who = "repeat registrations" if member.candidate_scope == "repeat_only" else "registrations"
            errors.append(Violation("H10", f"Cycle module {member.module_code} has no {who} to examine."))
    if errors:
        raise SnapshotError(tuple(errors))
    return selected, chosen


async def load_database_snapshot(engine: AsyncEngine, *, institution_id: UUID | None = None, **kwargs) -> InputSnapshot:
    """Read-only input adapter; not used by the synthetic CLI. Opens no other service.

    With institution_id the protected timetables of that institution's other cycles are read in the
    same REPEATABLE READ transaction as every other input.
    """
    async with snapshot_session(engine) as session:
        if institution_id is not None:
            from app.saved_timetables.store import DatabaseTimetableStore  # local: the store imports app.scheduling, whose package imports this module

            async def protected(period, students):
                return await DatabaseTimetableStore(session).protected_schedule(
                    institution_id, exclude_period_code=period.period_code, start_date=period.start_date,
                    end_date=period.end_date, student_ids=students,
                    minimum_break_minutes=kwargs.get("policy", SchedulingPolicy()).minimum_break_minutes)

            kwargs["protected"] = protected
        return await load_snapshot(DatabaseSource(session), DatabaseSeatingStore(session), **kwargs)
