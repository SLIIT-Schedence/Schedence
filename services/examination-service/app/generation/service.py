"""Generate an examination timetable on request. Nothing is saved or published.

This module only orchestrates existing code:

* inputs: :func:`app.scheduling.inputs.load_snapshot` (frozen, validated snapshot of
  the period, modules, registrations, venues, blocks and effective seat capacities);
* settings: the active admin scheduling settings (saved, or the defaults);
* solving: :func:`generate_timetable` (feasibility, H1-H11) or
  :func:`optimize_timetable` (H1-H11 plus S1-S5 and the configured fairness);
* checking: :func:`validate_timetable`, run again here before anything is returned.

Safety and resources:

* Modules come from the period's configured cycle mapping
  (``seed_exam_period_modules``), never from the whole catalogue or the semester
  alone. Without ``module_codes`` the full cycle is scheduled; with them, only
  those mapped modules (partial generation). A period without a mapping accepts
  listed modules of its own semester only, and refuses an empty list.
* Only the cycle's registrations are loaded: all of an ``all`` module, only the
  repeat registrations of a ``repeat_only`` module.
* All inputs are read (and their transaction closed) before the solver starts.
* The solver runs in a worker thread, so the server keeps answering other
  requests. One generation runs at a time per process; another request gets 429.
* API runs are limited to :data:`MAX_API_TIME_LIMIT_SECONDS`, plus the input
  size limits below and the solver limits of :class:`SolverSettings`.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Literal

from fastapi import HTTPException, status

from app.exam_periods.rules import ExamCalendar
from app.generation.preferences import effective_configuration, saved_preferences, snapshot_document
from app.scheduling.configuration import SchedulingConfiguration
from app.scheduling.contracts import SchedulingValidationError, Violation
from app.scheduling.evaluation import TimetableEvaluation, evaluate_timetable
from app.scheduling.inputs import SnapshotError
from app.scheduling.optimization import OptimizationResult, optimize_timetable
from app.scheduling.policy import SchedulingPolicy, SolverSettings
from app.scheduling.service import generate_timetable
from app.scheduling.types import DraftTimetable, InputSnapshot, SolveResult, SolveStatus
from app.scheduling.validation import validate_timetable

logger = logging.getLogger(__name__)

Mode = Literal["feasibility", "optimized"]

# A synchronous HTTP request should not hold a solver for longer than this; longer
# runs belong to a future background job (see README). Saved settings may allow
# up to MAX_TIME_LIMIT_SECONDS for offline runs.
MAX_API_TIME_LIMIT_SECONDS = 300
MAX_MODULES = 200
MAX_REGISTRATIONS = 20_000
MAX_VENUES = 500

# (period_code, policy, module_codes or None); must read with from_cycle=True.
# (period_code, policy, module_codes[, calendar=...]); the calendar is passed only when one was selected.
SnapshotLoader = Callable[..., Awaitable[InputSnapshot]]
SettingsLoader = Callable[[], Awaitable[tuple[SchedulingConfiguration, int]]]


class GenerationInputError(HTTPException):
    """The request or its inputs cannot be scheduled; every problem is listed (422)."""

    def __init__(self, violations: tuple[Violation, ...]) -> None:
        super().__init__(status.HTTP_422_UNPROCESSABLE_CONTENT,
                         [{"rule": v.constraint, "message": v.message} for v in violations])


class GenerationBusyError(HTTPException):
    def __init__(self) -> None:
        super().__init__(status.HTTP_429_TOO_MANY_REQUESTS,
                         "A timetable is already being generated. Try again when it has finished.",
                         headers={"Retry-After": "30"})


class SolverUnavailableError(HTTPException):
    def __init__(self) -> None:
        super().__init__(status.HTTP_503_SERVICE_UNAVAILABLE,
                         "The timetable solver is not installed on this server.")


@dataclass(frozen=True)
class GenerationRequest:
    period_code: str
    module_codes: tuple[str, ...] | None  # None = the full configured cycle
    mode: Mode = "feasibility"
    time_limit_seconds: float | None = None  # may only lower the saved limit
    calendar: ExamCalendar | None = None  # None = the saved period's calendar
    scheduling_preferences: dict | None = None  # None retains the legacy active-default contract
    stored_preferences: dict | None = None  # trusted saved snapshot, never accepted from an API request
    stored_settings_version: int = 0


@dataclass(frozen=True)
class GenerationOutcome:
    request: GenerationRequest
    snapshot: InputSnapshot
    configuration: SchedulingConfiguration
    settings_version: int
    solver_settings: SolverSettings
    status: SolveStatus
    timetable: DraftTimetable | None  # only an independently validated timetable
    result: SolveResult | OptimizationResult
    validation_violations: tuple[Violation, ...]
    evaluation: TimetableEvaluation | None  # S1-S5 scores of the returned timetable
    wall_time_seconds: float
    preferences_snapshot: dict | None = None


def _solver_settings(saved: SolverSettings, request: GenerationRequest) -> SolverSettings:
    limit = saved.time_limit_seconds if request.time_limit_seconds is None else request.time_limit_seconds
    errors = []
    if request.time_limit_seconds is not None and request.time_limit_seconds > saved.time_limit_seconds:
        errors.append(Violation("SOLVER", f"time_limit_seconds may only lower the saved limit "
                                          f"({saved.time_limit_seconds:g} s)."))
    if limit > MAX_API_TIME_LIMIT_SECONDS:
        errors.append(Violation("SOLVER", f"API generation is limited to {MAX_API_TIME_LIMIT_SECONDS} seconds. "
                                          "Send a lower time_limit_seconds or lower the saved solver limit."))
    if errors:
        raise GenerationInputError(tuple(errors))
    return replace(saved, time_limit_seconds=float(limit))


def _scope_errors(snapshot: InputSnapshot) -> tuple[Violation, ...]:
    # Semester and cycle membership are checked while loading (load_snapshot, from_cycle=True).
    errors = []
    if len(snapshot.modules) > MAX_MODULES:
        errors.append(Violation("LIMIT", f"At most {MAX_MODULES} modules per generation."))
    if len(snapshot.registrations) > MAX_REGISTRATIONS:
        errors.append(Violation("LIMIT", f"At most {MAX_REGISTRATIONS} registrations per generation."))
    if len(snapshot.venues) > MAX_VENUES:
        errors.append(Violation("LIMIT", f"At most {MAX_VENUES} venues per generation."))
    return tuple(errors)


class TimetableGenerator:
    def __init__(self, load_snapshot: SnapshotLoader, load_settings: SettingsLoader, lock: asyncio.Lock):
        self._load_snapshot = load_snapshot
        self._load_settings = load_settings
        self._lock = lock

    async def generate(self, request: GenerationRequest) -> GenerationOutcome:
        if self._lock.locked():  # checked and taken without an await in between
            raise GenerationBusyError()
        async with self._lock:
            return await self._generate(request)

    async def current_inputs(self, period_code: str, module_codes: tuple[str, ...] | None,
                             calendar: ExamCalendar | None = None, *, configuration: SchedulingConfiguration | None = None,
                             seating_preferences: dict | None = None) -> tuple[SchedulingConfiguration, int, InputSnapshot]:
        """The active settings and a fresh input snapshot, loaded as for a generation (no solver, no lock).

        Used to check that a saved timetable still matches its inputs. Raises
        :class:`SnapshotError` when the inputs can no longer be loaded.
        """
        version = 0
        if configuration is None:
            configuration, version = await self._load_settings()
        snapshot = await self._snapshot(period_code, configuration.scheduling, module_codes, calendar, seating_preferences)
        return configuration, version, snapshot

    async def _snapshot(self, period_code, policy, module_codes, calendar, seating_preferences=None) -> InputSnapshot:
        extra = {} if calendar is None else {"calendar": calendar}
        if seating_preferences is not None:
            extra["seating_preferences"] = seating_preferences
        return await self._load_snapshot(period_code, policy, module_codes, **extra)

    async def _generate(self, request: GenerationRequest) -> GenerationOutcome:
        custom = request.scheduling_preferences is not None or request.stored_preferences is not None
        seating_preferences = None
        if request.stored_preferences is not None:
            restored = saved_preferences({"scheduling_preferences": request.stored_preferences})
            configuration, stored = restored
            version = request.stored_settings_version
            seating_preferences = {"default_percentage": stored["seating"]["default_percentage"]}
        else:
            configuration, version = await self._load_settings()
            if request.scheduling_preferences is not None:
                try:
                    configuration = effective_configuration(configuration, request.scheduling_preferences)
                except ValueError as exc:
                    raise GenerationInputError((Violation("PREFERENCES", str(exc)),)) from None
                seating_preferences = request.scheduling_preferences.get("seating", {})
        solver_settings = _solver_settings(configuration.solver, request)
        if custom:
            configuration = replace(configuration, solver=solver_settings)
        try:
            snapshot = await self._snapshot(request.period_code, configuration.scheduling, request.module_codes,
                                            request.calendar, seating_preferences)
        except SnapshotError as exc:
            raise GenerationInputError(exc.violations) from None
        errors = _scope_errors(snapshot)
        if errors:
            raise GenerationInputError(errors)

        def solve() -> SolveResult | OptimizationResult:
            if request.mode == "optimized":
                return optimize_timetable(snapshot, solver_settings, configuration.optimization,
                                          fairness=configuration.fairness)
            return generate_timetable(snapshot, solver_settings)

        began = time.perf_counter()
        try:
            result = await asyncio.to_thread(solve)  # keeps the event loop free during the solve
        except RuntimeError as exc:
            if "solver is not installed" in str(exc):
                raise SolverUnavailableError() from None
            raise
        wall = time.perf_counter() - began
        timetable = result.timetable
        status_ = result.status
        violations = validate_timetable(snapshot, timetable) if timetable is not None else ()
        if violations:  # defence in depth: never return a timetable the validator rejects
            logger.error("Generated timetable for %s failed independent validation: %d violation(s)",
                         request.period_code, len(violations))
            timetable, status_ = None, SolveStatus.MODEL_INVALID
        evaluation = None
        if timetable is not None:
            evaluation = getattr(result, "selected_evaluation", None)
            if evaluation is None:
                try:
                    evaluation = evaluate_timetable(snapshot, timetable, configuration.optimization)
                except SchedulingValidationError:
                    evaluation = None  # module metadata cannot be scored; the timetable is still valid
        logger.info("Generated exam timetable: period=%s mode=%s modules=%d status=%s seconds=%.1f settings_version=%s",
                    request.period_code, request.mode, len(snapshot.modules), status_.value, wall, version)
        return GenerationOutcome(request, snapshot, configuration, version, solver_settings, status_, timetable,
                                 result, violations, evaluation, wall,
                                 snapshot_document(configuration, snapshot.capacities) if custom else None)
