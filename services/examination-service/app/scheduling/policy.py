"""Approved prototype settings, not institutional policy. All are configurable.

Times use the selected period's local calendar, like the existing source records.
Phase 1 has no objective: one exam per student per day is a future soft preference.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass

from app.scheduling.contracts import SchedulingValidationError, Violation, frozen_sequence, text_errors

# Server-side resource limits, applied wherever these settings are used (API, saved
# settings, command line, library calls). They bound one solver run and keep breaks
# within a day; they never relax a hard rule.
MAX_TIME_LIMIT_SECONDS = 3600  # one hour per solver run
MAX_SEARCH_WORKERS = 16
MAX_MINIMUM_BREAK_MINUTES = 1440  # one day
MAX_SESSIONS_PER_EXAM = 10  # resource bound on the per-student session choices
DEFAULT_MAX_SESSIONS_PER_EXAM = 3  # prototype default, not an institutional rule

DEFAULT_COMPATIBILITY = (
    ("written", ("lecture_hall", "smart_classroom")),
    ("computer_based", ("computer_lab",)),
    ("hybrid", ("lecture_hall", "smart_classroom", "computer_lab", "byod_lab")),
)


def whole_number(value: object, minimum: int = 0) -> bool:
    # Keep settings/counts within the solver's supported signed integer fields.
    return type(value) is int and minimum <= value <= 2**31 - 1


def minute_time(value: object) -> bool:
    return isinstance(value, dt.time) and value.tzinfo is None and value.second == value.microsecond == 0


@dataclass(frozen=True)
class SchedulingPolicy:
    permitted_start_times: tuple[dt.time, ...] = (dt.time(8, 30), dt.time(13, 30))
    minimum_break_minutes: int = 60
    venue_compatibility: tuple[tuple[str, tuple[str, ...]], ...] = DEFAULT_COMPATIBILITY
    module_venue_overrides: tuple[tuple[str, tuple[str, ...]], ...] = ()
    max_rooms_per_exam: int | None = None
    # Most sessions (sittings at different times) one module may be split into when
    # its candidates do not fit into one sitting. 1 = never split.
    max_sessions_per_exam: int = DEFAULT_MAX_SESSIONS_PER_EXAM

    def __post_init__(self) -> None:
        # Copy caller-owned containers so the policy is deeply immutable.
        object.__setattr__(self, "permitted_start_times", frozen_sequence(
            self.permitted_start_times, "permitted_start_times", dt.time))
        for name in ("venue_compatibility", "module_venue_overrides"):
            rows = []
            for row in frozen_sequence(getattr(self, name), name):
                pair = frozen_sequence(row, name + " entry")
                if len(pair) != 2:
                    raise SchedulingValidationError((Violation("H11", f"{name} entries must be key/value pairs."),))
                key, values = pair
                errors = text_errors(key, name + " key")
                if errors:
                    raise SchedulingValidationError(errors)
                rows.append((key, frozen_sequence(values, name + " venue types", str)))
            object.__setattr__(self, name, tuple(rows))

    def allowed_venue_types(self, exam_type: str, module_code: str) -> tuple[str, ...]:
        return dict(self.module_venue_overrides).get(module_code, dict(self.venue_compatibility).get(exam_type, ()))

    def errors(self) -> tuple[str, ...]:
        errors = []
        if not self.permitted_start_times or not all(minute_time(t) for t in self.permitted_start_times):
            errors.append("Permitted starts must be non-empty local times with whole minutes.")
        if all(minute_time(t) for t in self.permitted_start_times) and len(set(self.permitted_start_times)) != len(self.permitted_start_times):
            errors.append("Permitted start times must be unique.")
        if not whole_number(self.minimum_break_minutes):
            errors.append("Minimum break must be a whole number of minutes >= 0.")
        elif self.minimum_break_minutes > MAX_MINIMUM_BREAK_MINUTES:
            errors.append(f"Minimum break must be at most {MAX_MINIMUM_BREAK_MINUTES} minutes (one day).")
        if self.max_rooms_per_exam is not None and not whole_number(self.max_rooms_per_exam, 1):
            errors.append("Maximum rooms must be a positive whole number or None.")
        if not whole_number(self.max_sessions_per_exam, 1) or self.max_sessions_per_exam > MAX_SESSIONS_PER_EXAM:
            errors.append(f"Maximum sessions per exam must be a whole number from 1 to {MAX_SESSIONS_PER_EXAM}.")
        for name in ("venue_compatibility", "module_venue_overrides"):
            rows = getattr(self, name)
            if len({key for key, _ in rows}) != len(rows):
                errors.append(f"Duplicate keys in {name}.")
            if any(not isinstance(key, str) or not key or not values
                   or any(not isinstance(v, str) or not v for v in values)
                   or len(set(values)) != len(values) for key, values in rows):
                errors.append(f"{name} must map identifiers to non-empty, unique venue types.")
        return tuple(errors)


@dataclass(frozen=True)
class SolverSettings:
    time_limit_seconds: float = 60.0
    random_seed: int = 20261019
    num_search_workers: int = 1

    def errors(self) -> tuple[str, ...]:
        errors = []
        finite_limit = False
        if type(self.time_limit_seconds) in (int, float):
            try:
                finite_limit = math.isfinite(self.time_limit_seconds) and self.time_limit_seconds > 0
            except OverflowError:
                pass  # A finite Python integer may still exceed the solver's double range.
        if not finite_limit:
            errors.append("Solver time limit must be finite and greater than zero.")
        elif self.time_limit_seconds > MAX_TIME_LIMIT_SECONDS:
            errors.append(f"Solver time limit must be at most {MAX_TIME_LIMIT_SECONDS} seconds.")
        if not whole_number(self.random_seed) or self.random_seed > 2**31 - 1:
            errors.append("Random seed must be an integer between 0 and 2147483647.")
        if not whole_number(self.num_search_workers, 1):
            errors.append("Solver workers must be a positive whole number; use 1 for reproducibility.")
        elif self.num_search_workers > MAX_SEARCH_WORKERS:
            errors.append(f"Solver workers must be at most {MAX_SEARCH_WORKERS}.")
        return tuple(errors)
