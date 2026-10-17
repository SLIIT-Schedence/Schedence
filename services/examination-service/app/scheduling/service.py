"""Validate inputs, solve offline, and independently check every successful draft."""

from __future__ import annotations

from dataclasses import replace

from app.scheduling.inputs import validate_inputs
from app.scheduling.policy import SolverSettings
from app.scheduling.types import InputSnapshot, SolveResult, SolveStatus, Violation
from app.scheduling.validation import validate_timetable


def generate_timetable(snapshot: InputSnapshot, settings: SolverSettings = SolverSettings()) -> SolveResult:
    violations = validate_inputs(snapshot) + tuple(Violation("H11", msg) for msg in settings.errors())
    if violations:
        return SolveResult(SolveStatus.MODEL_INVALID, violations=violations,
                           diagnostics=("Invalid scheduling input or settings.",), settings=settings)
    try:
        from app.scheduling.cp_sat import solve
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.startswith("ortools"):
            raise RuntimeError("Offline solver is not installed. Install this service's [solver] extra.") from exc
        raise
    result = solve(snapshot, settings)
    if result.timetable is not None:
        violations = validate_timetable(snapshot, result.timetable)
        if violations:
            return replace(result, status=SolveStatus.MODEL_INVALID, timetable=None, violations=violations,
                           diagnostics=("Independent validation rejected the solver output; no draft returned.",))
    return result
