"""Staged minimization with a shared search budget and honest incumbent retention."""

from dataclasses import dataclass
from time import perf_counter

from ortools.sat.python import cp_model

from .results import SolverStatus


@dataclass(frozen=True)
class OptimizationStage:
    criterion: str
    status: str
    objective_value: int | None


def staged_minimize(model, config, order, components):
    """Only proven OPTIMAL stages are fixed. No lower stage follows FEASIBLE.

    All variables exist before the first solve. A later UNKNOWN/time-limit can
    retain an earlier complete incumbent; its final status is then FEASIBLE.
    """
    started = perf_counter()
    incumbent, stages = None, []
    names = {cp_model.OPTIMAL: SolverStatus.OPTIMAL, cp_model.FEASIBLE: SolverStatus.FEASIBLE,
             cp_model.INFEASIBLE: SolverStatus.INFEASIBLE, cp_model.UNKNOWN: SolverStatus.UNKNOWN,
             cp_model.MODEL_INVALID: SolverStatus.MODEL_INVALID}
    for criterion in order:
        remaining = config.max_time_seconds - (perf_counter() - started)
        if remaining <= 0:
            stages.append(OptimizationStage(criterion, "NOT_RUN_TIME_LIMIT", None))
            return incumbent, SolverStatus.FEASIBLE if incumbent is not None else SolverStatus.UNKNOWN, tuple(stages)
        model.minimize(components[criterion])
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = remaining
        solver.parameters.num_search_workers = config.num_search_workers
        if config.random_seed is not None:
            solver.parameters.random_seed = config.random_seed
        status = names[solver.solve(model)]
        if status in {SolverStatus.OPTIMAL, SolverStatus.FEASIBLE}:
            incumbent = solver
            value = solver.value(components[criterion])
            stages.append(OptimizationStage(criterion, status.value, value))
            if status is SolverStatus.FEASIBLE:
                return incumbent, status, tuple(stages)
            model.add(components[criterion] == value)
        else:
            stages.append(OptimizationStage(criterion, status.value, None))
            # A prior optimal stage proves these equalities have an incumbent.
            # INFEASIBLE/MODEL_INVALID here indicates an inconsistency, not a timeout.
            if incumbent is not None and status not in {SolverStatus.UNKNOWN}:
                from .results import SolverInputError
                raise SolverInputError("A later fairness stage contradicted the retained feasible incumbent.")
            return incumbent, SolverStatus.FEASIBLE if incumbent is not None else status, tuple(stages)
    return incumbent, SolverStatus.OPTIMAL, tuple(stages)
