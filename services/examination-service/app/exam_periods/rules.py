"""Rules for examination period definitions (pure functions, no database).

A period definition is an :class:`app.sources.types.ExamPeriod` built with
:func:`build_period`, so the API, the scheduler and these checks all compute
``exam_days`` the same way. Every problem is returned (not only the first) as a
``{"rule", "message"}`` pair, the same shape as the other 422 answers.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, replace

from app.seed.generator import exam_days
from app.sources.types import ExamPeriod, ExamPeriodExclusion

# Safety limits (not settings).
MAX_PERIOD_DAYS = 120
MAX_EXCLUSIONS = 120
MIN_YEAR, MAX_YEAR = 2000, 2099
# New period codes: upper-case letters, digits and single hyphens inside, e.g. PP1-2027-S1-MID.
# (Reads still accept every stored code: app.api.deps.PERIOD_CODE.)
NEW_PERIOD_CODE = r"^[A-Z0-9]+(?:-[A-Z0-9]+)*$"  # at most 32 characters (checked separately)
YEAR_LABEL = re.compile(r"^([0-9]{4})/([0-9]{4})$")  # e.g. 2026/2027

# Every stored field of a period, in a fixed order. ``exclusions`` is compared as a whole.
EDITABLE_FIELDS = ("period_name", "academic_year_label", "semester", "exam_kind", "start_date", "end_date",
                   "day_start_time", "day_end_time", "include_saturdays", "include_sundays", "exclusions")
# Fields that may still change once a timetable of the period has left DRAFT.
LOCKED_PERIOD_EDITABLE = frozenset({"period_name"})
# Fields the cycle mapping depends on (app.sources.exam_rules.cycle_member_errors).
CYCLE_FIELDS = frozenset({"exam_kind", "semester"})

Problem = dict[str, str]


def _problem(rule: str, message: str) -> Problem:
    return {"rule": rule, "message": message}


def build_period(*, period_code: str, period_name: str, academic_year_label: str, semester: int, exam_kind: str,
                 start_date: dt.date, end_date: dt.date, day_start_time: dt.time, day_end_time: dt.time,
                 include_saturdays: bool, include_sundays: bool,
                 exclusions: Iterable[tuple[dt.date, str]] = ()) -> ExamPeriod:
    """A period with its exclusions sorted by date and its exam days computed (none for an invalid range)."""
    excluded = tuple(sorted((ExamPeriodExclusion(period_code, day, reason) for day, reason in exclusions),
                            key=lambda e: e.excluded_date))
    period = ExamPeriod(period_code=period_code, period_name=period_name, academic_year_label=academic_year_label,
                        semester=semester, exam_kind=exam_kind, start_date=start_date, end_date=end_date,
                        day_start_time=day_start_time, day_end_time=day_end_time,
                        include_saturdays=include_saturdays, include_sundays=include_sundays,
                        exclusions=excluded, exam_days=())
    # Never walk an absurd range day by day: such a period is rejected anyway.
    if 0 <= (end_date - start_date).days < MAX_PERIOD_DAYS:
        period = replace(period, exam_days=exam_days(period, excluded))
    return period


def _weekend_off(period: ExamPeriod, day: dt.date) -> bool:
    return (day.weekday() == 5 and not period.include_saturdays) or (day.weekday() == 6 and not period.include_sundays)


def period_problems(period: ExamPeriod) -> list[Problem]:
    """Why this period definition cannot be saved (empty when it can)."""
    problems: list[Problem] = []

    match = YEAR_LABEL.match(period.academic_year_label)
    if match is None:
        problems.append(_problem("PERIOD_YEAR", "academic_year_label must look like 2026/2027."))
    else:
        first, second = int(match[1]), int(match[2])
        if second != first + 1 or not MIN_YEAR <= first < MAX_YEAR:
            problems.append(_problem("PERIOD_YEAR", f"academic_year_label must be two consecutive years between "
                                                    f"{MIN_YEAR} and {MAX_YEAR}, e.g. 2026/2027."))
        elif not {period.start_date.year, period.end_date.year} <= {first, second}:
            problems.append(_problem("PERIOD_YEAR", f"The exam dates must fall in academic year "
                                                    f"{period.academic_year_label}."))

    days = (period.end_date - period.start_date).days + 1
    if days < 1:
        problems.append(_problem("PERIOD_DATES", "end_date must be on or after start_date."))
    elif days > MAX_PERIOD_DAYS:
        problems.append(_problem("PERIOD_DATES", f"An exam period may last at most {MAX_PERIOD_DAYS} days "
                                                 f"(this one lasts {days})."))

    for name in ("day_start_time", "day_end_time"):
        value: dt.time = getattr(period, name)
        if value.second or value.microsecond or value.tzinfo is not None:
            problems.append(_problem("PERIOD_HOURS", f"{name} must be a whole minute without a time zone (HH:MM)."))
    if period.day_end_time <= period.day_start_time:
        problems.append(_problem("PERIOD_HOURS", "day_end_time must be after day_start_time."))

    if len(period.exclusions) > MAX_EXCLUSIONS:
        problems.append(_problem("PERIOD_EXCLUSIONS", f"At most {MAX_EXCLUSIONS} excluded dates are allowed."))
    counts = Counter(e.excluded_date for e in period.exclusions)
    for day in sorted(d for d, n in counts.items() if n > 1):
        problems.append(_problem("PERIOD_EXCLUSIONS", f"{day} is excluded more than once."))
    for day in sorted(counts):
        if not period.start_date <= day <= period.end_date:
            problems.append(_problem("PERIOD_EXCLUSIONS", f"Excluded date {day} is outside the exam period."))
        elif _weekend_off(period, day):
            problems.append(_problem("PERIOD_EXCLUSIONS", f"Excluded date {day} is a weekend day that is already "
                                                          "not an exam day; remove it."))

    if 1 <= days <= MAX_PERIOD_DAYS and not period.exam_days:
        problems.append(_problem("PERIOD_CALENDAR", "The period has no exam day left after weekends and "
                                                    "excluded dates."))
    return problems


def _plain(value):
    return value.isoformat() if isinstance(value, dt.date | dt.time) else value


def period_fingerprint(period: ExamPeriod) -> str:
    """SHA-256 of the stored definition: the version to send back with an update (409 if it changed)."""
    document = {name: _plain(getattr(period, name)) for name in ("period_code", *EDITABLE_FIELDS[:-1])}
    document["exclusions"] = [[e.excluded_date.isoformat(), e.reason] for e in period.exclusions]
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def changed_fields(old: ExamPeriod, new: ExamPeriod) -> list[str]:
    def comparable(period: ExamPeriod, name: str):
        if name == "exclusions":
            return [(e.excluded_date, e.reason) for e in period.exclusions]
        return getattr(period, name)

    return [name for name in EDITABLE_FIELDS if comparable(old, name) != comparable(new, name)]


# --- Per-generation exam calendar -------------------------------------------------------------------------------
# An admin may generate with dates, hours, weekends and excluded dates chosen for that run, without editing the
# saved period. The calendar replaces those fields of the period inside the generation snapshot only, so the solver
# (H6-H8), the independent validator, venue blocks and the input fingerprint all use exactly the chosen calendar.

@dataclass(frozen=True)
class ExamCalendar:
    start_date: dt.date
    end_date: dt.date
    day_start_time: dt.time
    day_end_time: dt.time
    include_saturdays: bool
    include_sundays: bool
    exclusions: tuple[tuple[dt.date, str], ...] = ()


# Calendar problems reported with the hard rule they protect.
CALENDAR_RULES = {"PERIOD_YEAR": "H6", "PERIOD_DATES": "H6", "PERIOD_HOURS": "H7",
                  "PERIOD_EXCLUSIONS": "H8", "PERIOD_CALENDAR": "H8"}


def apply_calendar(period: ExamPeriod, calendar: ExamCalendar) -> tuple[ExamPeriod, list[Problem]]:
    """The period with the selected calendar, and every problem with it (the same rules as a saved period)."""
    selected = build_period(
        period_code=period.period_code, period_name=period.period_name, academic_year_label=period.academic_year_label,
        semester=period.semester, exam_kind=period.exam_kind, start_date=calendar.start_date, end_date=calendar.end_date,
        day_start_time=calendar.day_start_time, day_end_time=calendar.day_end_time,
        include_saturdays=calendar.include_saturdays, include_sundays=calendar.include_sundays,
        exclusions=calendar.exclusions,
    )
    return selected, period_problems(selected)


def calendar_document(calendar: ExamCalendar) -> dict:
    return {"start_date": calendar.start_date.isoformat(), "end_date": calendar.end_date.isoformat(),
            "day_start_time": calendar.day_start_time.isoformat(), "day_end_time": calendar.day_end_time.isoformat(),
            "include_saturdays": calendar.include_saturdays, "include_sundays": calendar.include_sundays,
            "exclusions": [{"excluded_date": day.isoformat(), "reason": reason} for day, reason in calendar.exclusions]}


def saved_calendar(inputs: dict | None) -> ExamCalendar | None:
    """The calendar a saved timetable was generated with; None when it used its saved period (older timetables too).

    Raises ValueError when the stored calendar cannot be read.
    """
    if not isinstance(inputs, dict) or inputs.get("calendar_source", "period") == "period":
        return None
    if inputs.get("calendar_source") != "selected":
        raise ValueError("unknown calendar source")
    try:
        flags = (inputs["include_saturdays"], inputs["include_sundays"])
        if any(type(flag) is not bool for flag in flags):
            raise ValueError("weekend flags must be booleans")
        return ExamCalendar(
            start_date=dt.date.fromisoformat(inputs["start_date"]), end_date=dt.date.fromisoformat(inputs["end_date"]),
            day_start_time=dt.time.fromisoformat(inputs["day_start_time"]),
            day_end_time=dt.time.fromisoformat(inputs["day_end_time"]),
            include_saturdays=flags[0], include_sundays=flags[1],
            exclusions=tuple((dt.date.fromisoformat(e["excluded_date"]), str(e["reason"])) for e in inputs["exclusions"]),
        )
    except (KeyError, TypeError) as exc:
        raise ValueError("incomplete stored calendar") from exc
