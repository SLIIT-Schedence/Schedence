"""Offline optional optimization, sharing hard constraints and preserving a valid baseline."""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from importlib.metadata import version

from app.scheduling.contracts import SchedulingValidationError, Violation
from app.scheduling.evaluation import TimetableEvaluation, evaluate_timetable, optimization_input_errors
from app.scheduling.inputs import validate_inputs
from app.scheduling.optimization_policy import OptimizationPolicy, SCORING_VERSION
from app.scheduling.policy import SolverSettings
from app.scheduling.service import generate_timetable
from app.scheduling.types import DraftTimetable, InputSnapshot, SolveStatus
from app.scheduling.validation import validate_timetable


@dataclass(frozen=True)
class OptimizationResult:
    status: SolveStatus
    timetable: DraftTimetable | None = None
    baseline: DraftTimetable | None = None
    baseline_evaluation: TimetableEvaluation | None = None
    selected_evaluation: TimetableEvaluation | None = None
    improved: bool = False
    optimization_status: SolveStatus | None = None
    violations: tuple[Violation, ...] = ()
    diagnostics: tuple[str, ...] = ()
    solver_time_seconds: float = 0.0
    total_time_seconds: float = 0.0
    objective_bound_units: float | None = None
    relative_gap: float | None = None
    solver_version: str | None = None
    settings: SolverSettings = SolverSettings()
    policy: OptimizationPolicy = OptimizationPolicy()
    configuration_fingerprint: str = ""
    scoring_version: str = SCORING_VERSION


def optimize_timetable(snapshot: InputSnapshot, settings: SolverSettings = SolverSettings(),
                       policy: OptimizationPolicy = OptimizationPolicy(), *,
                       baseline: DraftTimetable | None = None) -> OptimizationResult:
    began = time.perf_counter()
    errors = validate_inputs(snapshot)
    if type(settings) is not SolverSettings:
        errors += (Violation("OPTIMIZATION", "settings must be SolverSettings."),)
    else:
        errors += tuple(Violation("OPTIMIZATION", message) for message in settings.errors())
    if type(policy) is not OptimizationPolicy:
        errors += (Violation("OPTIMIZATION", "policy must be OptimizationPolicy."),)
    if not errors:
        errors += optimization_input_errors(snapshot)
    if errors:
        return OptimizationResult(SolveStatus.MODEL_INVALID, violations=errors)
    common = dict(settings=settings, policy=policy, configuration_fingerprint=policy.fingerprint)
    solver_time = 0.0
    if baseline is not None:
        errors = validate_timetable(snapshot, baseline)
        if errors:
            return OptimizationResult(SolveStatus.MODEL_INVALID, violations=errors, **common)
    else:
        baseline_result = generate_timetable(snapshot, replace(settings,
            time_limit_seconds=settings.time_limit_seconds * policy.baseline_budget_percent / 100))
        solver_time = baseline_result.wall_time_seconds
        baseline = baseline_result.timetable
        if baseline is None and baseline_result.status != SolveStatus.UNKNOWN:
            return OptimizationResult(baseline_result.status, violations=baseline_result.violations,
                diagnostics=baseline_result.diagnostics, solver_time_seconds=solver_time,
                total_time_seconds=time.perf_counter() - began, **common)
    baseline_evaluation = evaluate_timetable(snapshot, baseline, policy) if baseline else None
    remaining = max(0.0, settings.time_limit_seconds - solver_time)
    selected, selected_evaluation = baseline, baseline_evaluation
    attempt_status = SolveStatus.UNKNOWN
    improved = False
    bound = relative_gap = None
    diagnostics = []
    violations = ()
    if remaining > 0:
        try:
            # Preserve the optional dependency boundary used by Phase 1 and the API.
            from ortools.sat.python import cp_model
            from app.scheduling.model import build_hard_model, decode_draft
            from app.scheduling.objective import add_objective
        except ModuleNotFoundError as exc:
            if exc.name and exc.name.startswith("ortools"):
                raise RuntimeError("Offline solver is not installed. Install this service's [solver] extra.") from exc
            raise
        model, starts, allocated = build_hard_model(snapshot)
        try:
            objective = add_objective(model, starts, snapshot, policy, baseline)
            # The known valid baseline remains a feasible objective bound.
            if baseline_evaluation:
                model.add(objective <= baseline_evaluation.objective_units)
            model_error = model.validate()
            if model_error:
                raise SchedulingValidationError((Violation("OPTIMIZATION", model_error),))
        except SchedulingValidationError as exc:
            attempt_status = SolveStatus.MODEL_INVALID
            violations = exc.violations
        else:
            solver = cp_model.CpSolver()
            solver.parameters.max_time_in_seconds = remaining
            solver.parameters.random_seed = settings.random_seed
            solver.parameters.num_search_workers = settings.num_search_workers
            attempt_status = SolveStatus(solver.status_name(solver.solve(model)))
            solver_time += solver.wall_time
            if attempt_status in (SolveStatus.FEASIBLE, SolveStatus.OPTIMAL):
                candidate = decode_draft(snapshot, solver, starts, allocated)
                violations = validate_timetable(snapshot, candidate)
                if not violations:
                    measured = evaluate_timetable(snapshot, candidate, policy)
                    if measured.objective_units != solver.value(objective):
                        violations = (Violation("OPTIMIZATION", "Independent score does not match the solver objective."),)
                    if not violations:
                        bound = solver.best_objective_bound
                        if baseline_evaluation is None or measured.objective_units < baseline_evaluation.objective_units:
                            selected, selected_evaluation = candidate, measured
                            improved = baseline_evaluation is not None
                if violations:
                    attempt_status = SolveStatus.MODEL_INVALID
            elif attempt_status == SolveStatus.INFEASIBLE and baseline is not None:
                violations = (Violation("OPTIMIZATION", "Optimizer rejected a known valid baseline; model consistency failure."),)
                attempt_status = SolveStatus.MODEL_INVALID
    if selected_evaluation and bound is not None:
        relative_gap = max(0.0, selected_evaluation.objective_units - bound) / max(1, abs(selected_evaluation.objective_units))
    if selected is baseline and baseline is not None:
        diagnostics.append("Kept independently validated baseline: no strictly better validated score was obtained.")
    if attempt_status == SolveStatus.UNKNOWN:
        diagnostics.append("Optimization search stopped without a new solution or proof; UNKNOWN does not mean infeasible.")
    elif attempt_status == SolveStatus.INFEASIBLE:
        diagnostics.append("No timetable satisfies the frozen inputs and H1-H11.")
    elif attempt_status == SolveStatus.MODEL_INVALID:
        diagnostics.append("Optimization model or candidate failed validation; inspect violations.")
    if selected is not None:
        status = SolveStatus.OPTIMAL if attempt_status == SolveStatus.OPTIMAL and not violations else SolveStatus.FEASIBLE
    else:
        status = attempt_status
    return OptimizationResult(status, selected, baseline, baseline_evaluation, selected_evaluation, improved,
        attempt_status, violations, tuple(diagnostics), solver_time, time.perf_counter() - began,
        bound, relative_gap, version("ortools"), **common)
