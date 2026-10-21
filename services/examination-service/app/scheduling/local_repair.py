"""Small, deterministic single-sitting moves; no solver or scoring formula here.

Rooms, candidates, seats, duration and reading time stay fixed. Split modules
remain with CP-SAT. Every accepted move passes the existing full independent
validator/scorer and the ORIGINAL baseline's configured fairness checks.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import datetime as dt

from app.scheduling import runtime_budget
from app.scheduling.availability import allowed_starts, origin
from app.scheduling.contracts import SchedulingValidationError
from app.scheduling.evaluation import TimetableEvaluation, evaluate_timetable
from app.scheduling.fairness import compare_fairness
from app.scheduling.fairness_policy import FairnessPolicy
from app.scheduling.optimization_policy import OptimizationPolicy
from app.scheduling.types import DraftTimetable, InputSnapshot

MAX_REPAIR_SECONDS = 5.0
MAX_REPAIR_CANDIDATES = 48
MAX_REPAIR_MOVES = 3


@dataclass(frozen=True)
class LocalRepairLimits:
    time_limit_seconds: float = MAX_REPAIR_SECONDS
    max_candidates: int = MAX_REPAIR_CANDIDATES
    max_moves: int = MAX_REPAIR_MOVES

    def __post_init__(self):
        if (type(self.time_limit_seconds) not in (int, float)
                or not 0 <= self.time_limit_seconds <= MAX_REPAIR_SECONDS):
            raise ValueError("Local repair time must be between 0 and 5 seconds.")
        for name, maximum in (("max_candidates", MAX_REPAIR_CANDIDATES), ("max_moves", MAX_REPAIR_MOVES)):
            if type(getattr(self, name)) is not int or not 0 <= getattr(self, name) <= maximum:
                raise ValueError(f"{name} must be between 0 and {maximum}.")


@dataclass(frozen=True)
class LocalRepairResult:
    timetable: DraftTimetable
    evaluation: TimetableEvaluation | None
    accepted_moves: int
    candidates_considered: int
    elapsed_seconds: float
    budget_exhausted: bool


def _moves(snapshot, timetable, evaluation):
    """Busy registrations first, then roomy time gaps; only an ordering heuristic.

    Final acceptance always uses the unchanged S1-S5 objective. Stable ties use
    module/session identifiers and earlier times; no randomness or CP-SAT run.
    """
    runtime_budget.check_budget()
    penalties = {s.student_id: s.objective_units for s in evaluation.student_penalties}
    seated = {s.session_id: {seat.student_id for room in s.rooms for seat in room.seats}
              for s in timetable.sessions}
    rooms = {s.session_id: {room.venue_id for room in s.rooms} for s in timetable.sessions}
    modules = {m.module_code: m for m in snapshot.modules}
    priority = lambda s: (-sum(penalties[student] for student in seated[s.session_id]), s.module_code, s.session_id)
    choices = []
    for session in sorted(timetable.sessions, key=priority):
        runtime_budget.check_budget()
        if session.session_count != 1 or not any(penalties[s] for s in seated[session.session_id]):
            continue
        others = [s for s in timetable.sessions if s.session_id != session.session_id]
        shared = [s for s in others if seated[session.session_id] & seated[s.session_id]]
        duration = session.end - session.start
        starts = []
        for offset in allowed_starts(snapshot, modules[session.module_code]):
            runtime_budget.check_budget()
            start = origin(snapshot) + dt.timedelta(minutes=offset)
            end = start + duration
            if start == session.start:
                continue
            # Cheap rejection only. The full validator still checks every candidate.
            if any(start < s.end and s.start < end and rooms[session.session_id] & rooms[s.session_id]
                   for s in others):
                continue
            gap = min((max(start - s.end, s.start - end).total_seconds() for s in shared), default=0)
            starts.append((-gap, start))
        if starts:
            choices.append((session, iter(start for _, start in sorted(starts))))
    # A short budget must explore several exams, rather than spend all of its
    # candidate checks on many unhelpful start times for one large module.
    while choices:
        pending = []
        for session, starts in choices:
            runtime_budget.check_budget()
            start = next(starts, None)
            if start is None:
                continue
            pending.append((session, starts))
            moved = replace(session, start=start, end=start + (session.end - session.start))
            yield replace(timetable, sessions=tuple(moved if s.session_id == session.session_id else s
                                                    for s in timetable.sessions))
        choices = pending


def repair_timetable(snapshot: InputSnapshot, timetable: DraftTimetable,
                     original_baseline: DraftTimetable, policy: OptimizationPolicy = OptimizationPolicy(), *,
                     fairness: FairnessPolicy = FairnessPolicy(), limits: LocalRepairLimits = LocalRepairLimits(),
                     deadline: float | None = None) -> LocalRepairResult:
    """Improve an existing feasible incumbent, within both local and caller budgets.

    Deadline checks run inside validation/scoring loops. Expiry discards the
    unfinished candidate and returns the last fully checked incumbent; there is
    no background search. A caller must supply a feasible timetable even when
    a zero budget prevents revalidation. No feasibility or optimality is inferred
    from exhausting this deliberately small neighborhood.
    """
    began = runtime_budget.clock()
    stop = began + limits.time_limit_seconds
    if deadline is not None:
        stop = min(stop, deadline)
    selected, evaluation = timetable, None
    accepted = considered = 0
    exhausted = False
    try:
        with runtime_budget.scheduling_deadline(stop):
            if not limits.max_candidates or not limits.max_moves:
                return LocalRepairResult(selected, None, 0, 0, runtime_budget.clock() - began, False)
            anchor = evaluate_timetable(snapshot, original_baseline, policy)
            evaluation = anchor if timetable is original_baseline else evaluate_timetable(snapshot, timetable, policy)
            comparison = compare_fairness(anchor, evaluation, policy, fairness)
            if comparison.violations:
                raise SchedulingValidationError(comparison.violations)
            while accepted < limits.max_moves and considered < limits.max_candidates and evaluation.objective_units:
                changed = False
                for candidate in _moves(snapshot, selected, evaluation):
                    runtime_budget.check_budget()
                    if considered >= limits.max_candidates:
                        break
                    considered += 1
                    try:
                        # Includes H1-H11 AND cross-cycle X1/X2 protection.
                        measured = evaluate_timetable(snapshot, candidate, policy)
                    except SchedulingValidationError:
                        continue
                    if measured.objective_units >= evaluation.objective_units:
                        continue
                    if compare_fairness(anchor, measured, policy, fairness).violations:
                        continue
                    runtime_budget.check_budget()  # never commit a candidate completed after the deadline
                    selected, evaluation = candidate, measured
                    accepted += 1
                    changed = True
                    break
                if not changed:
                    break
    except runtime_budget.SchedulingBudgetExpired:
        exhausted = True
    return LocalRepairResult(selected, evaluation, accepted, considered, runtime_budget.clock() - began, exhausted)
