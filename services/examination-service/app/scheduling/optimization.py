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
from app.scheduling.fairness_policy import FairnessPolicy
from app.scheduling.fairness import FairnessComparison, compare_fairness


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
    fairness_policy: FairnessPolicy = FairnessPolicy()
    fairness_comparison: FairnessComparison | None = None
    fairness_configuration_fingerprint: str = ""


def optimize_timetable(snapshot: InputSnapshot, settings: SolverSettings = SolverSettings(),
                       policy: OptimizationPolicy = OptimizationPolicy(), *,
                       baseline: DraftTimetable | None = None,
                       fairness: FairnessPolicy = FairnessPolicy()) -> OptimizationResult:
    began = time.perf_counter()
    errors = validate_inputs(snapshot)
    if type(settings) is not SolverSettings:
        errors += (Violation("OPTIMIZATION", "settings must be SolverSettings."),)
    else:
        errors += tuple(Violation("OPTIMIZATION", message) for message in settings.errors())
    if type(policy) is not OptimizationPolicy:
        errors += (Violation("OPTIMIZATION", "policy must be OptimizationPolicy."),)
    if type(fairness) is not FairnessPolicy:
        errors += (Violation("FAIRNESS", "fairness must be FairnessPolicy."),)
    if not errors:
        errors += optimization_input_errors(snapshot)
    if errors:
        return OptimizationResult(SolveStatus.MODEL_INVALID, violations=errors)
    common = dict(settings=settings, policy=policy, configuration_fingerprint=policy.fingerprint,
                  fairness_policy=fairness, fairness_configuration_fingerprint=fairness.fingerprint)
    solver_time = 0.0
    deadline = began + settings.time_limit_seconds
    diagnostics = []

    def remaining_time() -> float:
        # Reported solver time and real elapsed time (model building included) both count; the stricter wins.
        return max(0.0, min(settings.time_limit_seconds - solver_time, deadline - time.perf_counter()))

    if baseline is not None:
        errors = validate_timetable(snapshot, baseline)
        if errors:
            return OptimizationResult(SolveStatus.MODEL_INVALID, violations=errors, **common)
    else:
        baseline_result = generate_timetable(snapshot, replace(settings,
            time_limit_seconds=settings.time_limit_seconds * policy.baseline_budget_percent / 100))
        solver_time = baseline_result.wall_time_seconds
        retry_limit = remaining_time()
        if baseline_result.status == SolveStatus.UNKNOWN and retry_limit > 0:
            # Feasibility search stops at its first valid timetable, so the retry only uses the time it needs.
            # Without a baseline, short limits often return no timetable at all.
            baseline_result = generate_timetable(snapshot, replace(settings, time_limit_seconds=retry_limit))
            solver_time += baseline_result.wall_time_seconds
            if baseline_result.timetable is not None:
                diagnostics.append("Baseline needed more than its reserved budget; it used part of the optimization time.")
        baseline = baseline_result.timetable
        if baseline is None and baseline_result.status != SolveStatus.UNKNOWN:
            return OptimizationResult(baseline_result.status, violations=baseline_result.violations,
                diagnostics=baseline_result.diagnostics, solver_time_seconds=solver_time,
                total_time_seconds=time.perf_counter() - began, **common)
    if fairness.enabled and baseline is None:
        return OptimizationResult(SolveStatus.UNKNOWN, optimization_status=SolveStatus.UNKNOWN,
            diagnostics=("Fairness requires a validated baseline. No protected optimization was attempted and no limits were relaxed.",),
            solver_time_seconds=solver_time, total_time_seconds=time.perf_counter() - began, **common)
    baseline_evaluation = evaluate_timetable(snapshot, baseline, policy) if baseline else None
    selected, selected_evaluation = baseline, baseline_evaluation
    attempt_status = SolveStatus.UNKNOWN
    improved = False
    bound = relative_gap = None
    violations = ()
    if remaining_time() > 0:
        try:
            # Preserve the optional dependency boundary used by Phase 1 and the API.
            from ortools.sat.python import cp_model
            from app.scheduling.model import build_hard_model, decode_draft
            from app.scheduling.objective import add_objective
        except ModuleNotFoundError as exc:
            if exc.name and exc.name.startswith("ortools"):
                raise RuntimeError("Offline solver is not installed. Install this service's [solver] extra.") from exc
            raise
        model, layout, _ = build_hard_model(snapshot)
        try:
            if fairness.enabled:
                objective = add_objective(model, layout, snapshot, policy, baseline,
                                          fairness=fairness, baseline_evaluation=baseline_evaluation)
            else:
                # Preserve the original objective/model path when fairness is off.
                objective = add_objective(model, layout, snapshot, policy, baseline)
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
            # Building the objective model takes real time; skip the search if nothing is left.
            remaining = remaining_time()
            if remaining > 0:
                solver = cp_model.CpSolver()
                solver.parameters.max_time_in_seconds = remaining
                solver.parameters.random_seed = settings.random_seed
                solver.parameters.num_search_workers = settings.num_search_workers
                attempt_status = SolveStatus(solver.status_name(solver.solve(model)))
                solver_time += solver.wall_time
                if attempt_status in (SolveStatus.FEASIBLE, SolveStatus.OPTIMAL):
                    candidate = decode_draft(snapshot, solver, layout)
                    violations = validate_timetable(snapshot, candidate)
                    if not violations:
                        measured = evaluate_timetable(snapshot, candidate, policy)
                        if measured.objective_units != solver.value(objective):
                            violations = (Violation("OPTIMIZATION", "Independent score does not match the solver objective."),)
                        elif fairness.enabled:
                            try:
                                violations = compare_fairness(baseline_evaluation, measured, policy, fairness).violations
                            except SchedulingValidationError as exc:
                                violations = exc.violations
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
    comparison = (compare_fairness(baseline_evaluation, selected_evaluation, policy, fairness)
                  if baseline_evaluation is not None and selected_evaluation is not None else None)
    return OptimizationResult(status=status, timetable=selected, baseline=baseline,
        baseline_evaluation=baseline_evaluation, selected_evaluation=selected_evaluation,
        improved=improved, optimization_status=attempt_status, violations=violations,
        diagnostics=tuple(diagnostics), solver_time_seconds=solver_time,
        total_time_seconds=time.perf_counter() - began, objective_bound_units=bound,
        relative_gap=relative_gap, solver_version=version("ortools"), fairness_comparison=comparison, **common)
