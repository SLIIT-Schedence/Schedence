"""Feasibility-only entry point using the shared H1-H11 model (with capacity-based sessions)."""
from importlib.metadata import version
from ortools.sat.python import cp_model
from app.scheduling.model import build_hard_model, decode_draft
from app.scheduling.policy import SolverSettings
from app.scheduling.types import InputSnapshot, SolveResult, SolveStatus


def solve(snapshot: InputSnapshot, settings: SolverSettings) -> SolveResult:
    model, layout, _ = build_hard_model(snapshot)
    model_error = model.validate()
    if model_error:
        return SolveResult(SolveStatus.MODEL_INVALID, diagnostics=(model_error,), settings=settings,
                           solver_version=version("ortools"))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = settings.time_limit_seconds
    solver.parameters.random_seed = settings.random_seed
    solver.parameters.num_search_workers = settings.num_search_workers
    status = SolveStatus(solver.status_name(solver.solve(model)))
    info = dict(wall_time_seconds=solver.wall_time, solver_version=version("ortools"), settings=settings)
    if status not in (SolveStatus.FEASIBLE, SolveStatus.OPTIMAL):
        messages = {
            SolveStatus.INFEASIBLE: "No timetable satisfies the selected inputs and hard constraints.",
            SolveStatus.UNKNOWN: "Search stopped without a solution or proof of infeasibility; consider a longer limit.",
            SolveStatus.MODEL_INVALID: "CP-SAT rejected the model.",
        }
        return SolveResult(status, diagnostics=(messages[status],), **info)
    return SolveResult(status, timetable=decode_draft(snapshot, solver, layout), **info)
