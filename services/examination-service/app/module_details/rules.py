"""Rules for a module's examination details (pure functions, no database).

Only these fields can be changed: the exam type, the FINAL duration and reading
time, difficulty, MID/FINAL eligibility (with the MID duration) and the delivery
mode (with the block category). The module code, name, year, semester, credits,
specializations and registrations are never changed here.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
from dataclasses import dataclass

from app.db.seed_models import EXAM_TYPES
from app.sources.exam_rules import cycle_member_errors, exam_duration_minutes, module_exam_errors
from app.sources.types import ExamPeriod, Module

# Safety limits (not settings).
MIN_DURATION, MAX_DURATION = 15, 480
MAX_READING_TIME = 60
MIN_DIFFICULTY, MAX_DIFFICULTY = 1, 5

Problem = dict[str, str]


@dataclass(frozen=True)
class ExamDetails:
    exam_type: str
    duration_minutes: int  # FINAL examination
    reading_time_minutes: int
    difficulty: int
    has_final_exam: bool
    has_mid_exam: bool
    mid_duration_minutes: int | None
    delivery_mode: str
    block_category: str | None


EDITABLE_FIELDS = tuple(f.name for f in dataclasses.fields(ExamDetails))
# Fields a saved timetable of each examination kind depends on. Once a timetable of
# that kind has left DRAFT, these fields are locked for its modules.
KIND_FIELDS = {
    "FINAL": frozenset({"exam_type", "duration_minutes", "reading_time_minutes", "difficulty", "has_final_exam",
                        "delivery_mode", "block_category"}),
    "MID": frozenset({"exam_type", "mid_duration_minutes", "reading_time_minutes", "difficulty", "has_mid_exam",
                      "delivery_mode", "block_category"}),
}


def _problem(rule: str, message: str) -> Problem:
    return {"rule": rule, "message": message}


def details_of(module: Module) -> ExamDetails:
    return ExamDetails(**{name: getattr(module, name) for name in EDITABLE_FIELDS})


def apply_details(module: Module, details: ExamDetails) -> Module:
    """The module with new details. The reading-time flag follows the minutes (as in the catalogue import)."""
    return dataclasses.replace(module, **dataclasses.asdict(details),
                               has_extra_reading_time=details.reading_time_minutes > 0)


def details_fingerprint(module_code: str, details: ExamDetails) -> str:
    """SHA-256 of the editable details: the version to send back with an update (409 if it changed)."""
    document = {"module_code": module_code, **dataclasses.asdict(details)}
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def changed_fields(old: ExamDetails, new: ExamDetails) -> list[str]:
    return [name for name in EDITABLE_FIELDS if getattr(old, name) != getattr(new, name)]


def details_problems(details: ExamDetails) -> list[Problem]:
    """Why these details cannot be saved (empty when they can)."""
    problems = []
    if details.exam_type not in EXAM_TYPES:
        problems.append(_problem("MODULE_DETAILS", f"exam_type must be one of {', '.join(EXAM_TYPES)}."))
    for name in ("duration_minutes", "mid_duration_minutes"):
        value = getattr(details, name)
        if value is not None and not MIN_DURATION <= value <= MAX_DURATION:
            problems.append(_problem("MODULE_DETAILS", f"{name} must be {MIN_DURATION}-{MAX_DURATION} minutes."))
    if not 0 <= details.reading_time_minutes <= MAX_READING_TIME:
        problems.append(_problem("MODULE_DETAILS", f"reading_time_minutes must be 0-{MAX_READING_TIME}."))
    if not MIN_DIFFICULTY <= details.difficulty <= MAX_DIFFICULTY:
        problems.append(_problem("MODULE_DETAILS", f"difficulty must be {MIN_DIFFICULTY}-{MAX_DIFFICULTY}."))
    if not details.has_final_exam and not details.has_mid_exam:
        problems.append(_problem("MODULE_ELIGIBILITY", "A module needs a FINAL or a MID examination (or both)."))
    # Same eligibility and delivery rules as the seed data and the scheduler inputs.
    problems.extend(_problem("MODULE_ELIGIBILITY", f"{e[0].upper()}{e[1:]}.") for e in module_exam_errors(details))
    return problems


def _minutes(value: dt.time) -> int:
    return value.hour * 60 + value.minute


def cycle_problems(module: Module, cycles: list[tuple[ExamPeriod, str]]) -> list[Problem]:
    """Why the changed module would break a cycle mapping it belongs to (empty when it would not)."""
    problems = []
    for period, scope in cycles:
        for error in cycle_member_errors(period, module, scope):
            problems.append(_problem("CYCLE_MAPPING", f"Cycle {period.period_code}: the module {error}."))
        duration = exam_duration_minutes(module, period.exam_kind)
        day = _minutes(period.day_end_time) - _minutes(period.day_start_time)
        if duration is not None and duration + module.reading_time_minutes > day:
            problems.append(_problem("CYCLE_MAPPING", f"Cycle {period.period_code}: the {period.exam_kind} sitting "
                                                      f"({duration} + {module.reading_time_minutes} min) is longer "
                                                      f"than the exam day ({day} min)."))
    return problems
