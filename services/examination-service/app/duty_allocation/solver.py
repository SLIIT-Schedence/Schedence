"""Validate inputs, solve the duty model offline, and independently check every roster.

Mirrors app.scheduling.service: OR-Tools is imported only when solving, settings reuse the
scheduler's SolverSettings, and a roster the independent validator rejects is never returned.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from importlib.metadata import version

from app.duty_allocation.allocation_policy import DutyLimitPolicy
from app.duty_allocation.eligibility import build_eligibility
from app.duty_allocation.eligibility_policy import EligibilityPolicy
from app.duty_allocation.types import DUTY_INPUT, DutyAllocationInput, DutyAllocationResult, DutyAllocationStatus
from app.duty_allocation.validation import validate_allocation
from app.scheduling.contracts import Violation
from app.scheduling.policy import SolverSettings

_STATUS_MESSAGES = {
    DutyAllocationStatus.INFEASIBLE: "No duty roster satisfies the staff, policies and hard constraints.",
    DutyAllocationStatus.UNKNOWN: "Search stopped without a roster or proof of infeasibility; consider a longer limit.",
    DutyAllocationStatus.MODEL_INVALID: "CP-SAT rejected the duty model.",
}


def allocate_duties(data: DutyAllocationInput, eligibility: EligibilityPolicy, limits: DutyLimitPolicy,
                    settings: SolverSettings = SolverSettings()) -> DutyAllocationResult:
    """Step 4: any roster satisfying the hard constraints (no objective)."""
    return solve_duties(data, eligibility, limits, settings)


def solve_duties(data: DutyAllocationInput, eligibility: EligibilityPolicy, limits: DutyLimitPolicy,
                 settings: SolverSettings = SolverSettings(), *,
                 extend_model: Callable | None = None,
                 extra_checks: Callable | None = None) -> DutyAllocationResult:
    """Shared pipeline. extend_model(duty_model) may add an objective or opt-in constraints;
    extra_checks(assignments) returns further violations for the rules it added."""
    fingerprint = data.timetable.input_fingerprint if type(data) is DutyAllocationInput else "unknown"
    bad = [name for name, value, kind in (("data", data, DutyAllocationInput), ("eligibility", eligibility, EligibilityPolicy),
                                          ("limits", limits, DutyLimitPolicy), ("settings", settings, SolverSettings))
           if type(value) is not kind]
    problems = tuple(Violation(DUTY_INPUT, f"{name} has the wrong type.") for name in bad)
    if not bad:
        problems = tuple(Violation(DUTY_INPUT, message) for message in settings.errors())
    if problems:
        return DutyAllocationResult(DutyAllocationStatus.MODEL_INVALID, fingerprint, violations=problems,
                                    diagnostics=("Invalid duty allocation input or settings.",))

    started = time.perf_counter()
    report = build_eligibility(data, eligibility)
    if report.violations:  # proven shortages: no roster can exist, so skip the solver
        return DutyAllocationResult(DutyAllocationStatus.INFEASIBLE, fingerprint, violations=report.violations,
                                    diagnostics=("Eligibility pre-check proved a staff shortage.",),
                                    wall_time_seconds=time.perf_counter() - started)
    try:
        from ortools.sat.python import cp_model
        from app.duty_allocation.model import build_duty_model, decode_assignments
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.startswith("ortools"):
            raise RuntimeError("Offline solver is not installed. Install this service's [solver] extra.") from exc
        raise

    duty_model = build_duty_model(data, report, limits)
    if extend_model is not None:
        extend_model(duty_model)
    solver_version = version("ortools")
    model_error = duty_model.model.validate()
    if model_error:
        return DutyAllocationResult(DutyAllocationStatus.MODEL_INVALID, fingerprint, diagnostics=(model_error,),
                                    solver_version=solver_version)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = settings.time_limit_seconds
    solver.parameters.random_seed = settings.random_seed
    solver.parameters.num_search_workers = settings.num_search_workers
    status = DutyAllocationStatus(solver.status_name(solver.solve(duty_model.model)))
    info = dict(wall_time_seconds=solver.wall_time, solver_version=solver_version)
    if status not in (DutyAllocationStatus.FEASIBLE, DutyAllocationStatus.OPTIMAL):
        return DutyAllocationResult(status, fingerprint, diagnostics=(_STATUS_MESSAGES[status],), **info)

    assignments = decode_assignments(duty_model, solver)
    violations = validate_allocation(data, eligibility, limits, assignments)
    if extra_checks is not None:
        violations += tuple(extra_checks(assignments))
    if violations:
        return DutyAllocationResult(DutyAllocationStatus.MODEL_INVALID, fingerprint, violations=violations,
                                    diagnostics=("Independent validation rejected the solver output; no roster returned.",),
                                    **info)
    return DutyAllocationResult(status, fingerprint, assignments, **info)
