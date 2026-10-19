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


def staged_minimize(model, config, order, components, *, skip_constant_stages=False, carry_incumbent_hints=False):
    """Only proven OPTIMAL stages are fixed. No lower stage follows FEASIBLE.

    All variables exist before the first solve. A later UNKNOWN/time-limit can
    retain an earlier complete incumbent; its final status is then FEASIBLE.

    Opt-in: constant criteria are recorded as optimal once any feasible
    incumbent exists, without a native solve, and each proven incumbent seeds
    the next stage's search hints. Neither changes feasibility or the order.
    """
    started = perf_counter()
    incumbent, stages, pending = None, [], []
    names = {cp_model.OPTIMAL: SolverStatus.OPTIMAL, cp_model.FEASIBLE: SolverStatus.FEASIBLE,
             cp_model.INFEASIBLE: SolverStatus.INFEASIBLE, cp_model.UNKNOWN: SolverStatus.UNKNOWN,
             cp_model.MODEL_INVALID: SolverStatus.MODEL_INVALID}

    def constant(criterion):
        return isinstance(components[criterion], int) and not isinstance(components[criterion], bool)

    def flush(status):
        for criterion in pending:
            stages.append(OptimizationStage(criterion, "OPTIMAL", components[criterion]) if incumbent is not None
                          else OptimizationStage(criterion, status, None))
        pending.clear()

    for index, criterion in enumerate(order):
        if (skip_constant_stages and constant(criterion)
                and (incumbent is not None or not all(constant(key) for key in order[index + 1:]))):
            pending.append(criterion)
            continue
        remaining = config.max_time_seconds - (perf_counter() - started)
        if remaining <= 0:
            flush("NOT_RUN_TIME_LIMIT")
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
            flush(status.value)
            value = solver.value(components[criterion])
            stages.append(OptimizationStage(criterion, status.value, value))
            if status is SolverStatus.FEASIBLE:
                return incumbent, status, tuple(stages)
            model.add(components[criterion] == value)
            if carry_incumbent_hints:
                solution = solver.response_proto.solution
                model.clear_hints()
                for position, chosen in enumerate(solution):
                    model.add_hint(model.get_int_var_from_proto_index(position), chosen)
        else:
            flush(status.value)
            stages.append(OptimizationStage(criterion, status.value, None))
            # A prior optimal stage proves these equalities have an incumbent.
            # INFEASIBLE/MODEL_INVALID here indicates an inconsistency, not a timeout.
            if incumbent is not None and status not in {SolverStatus.UNKNOWN}:
                from .results import SolverInputError
                raise SolverInputError("A later fairness stage contradicted the retained feasible incumbent.")
            return incumbent, SolverStatus.FEASIBLE if incumbent is not None else status, tuple(stages)
    flush("OPTIMAL")
    return incumbent, SolverStatus.OPTIMAL, tuple(stages)
