"""Independent workload scoring and metrics. Never imports OR-Tools/objective code."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from fractions import Fraction
from itertools import combinations

from app.scheduling.contracts import SchedulingValidationError, Violation
from app.scheduling.optimization_policy import OptimizationPolicy, SCORING_VERSION
from app.scheduling.types import DraftTimetable, InputSnapshot
from app.scheduling.validation import validate_timetable
from app.scheduling.runtime_budget import check_budget


@dataclass(frozen=True)
class PenaltyScore:
    constraint: str
    weight: int
    penalty_points: float  # 0..100, equal mean across participating students
    weighted_points: float  # weight * penalty_points / 100


@dataclass(frozen=True)
class StudentPenalty:
    student_id: str
    objective_units: int  # sum(weight * normalized penalty), without population multiplicity


@dataclass(frozen=True)
class TimetableEvaluation:
    penalties: tuple[PenaltyScore, ...]
    objective_units: int  # exact integer objective used for selection
    total_weighted_penalty: float
    students: int
    same_day_student_pairs: int
    student_days_with_multiple_exams: int
    short_gap_student_pairs: int
    difficult_pairs_below_target: int
    minimum_gap_minutes: int | None
    median_gap_minutes: float | None
    lower_decile_gap_minutes: int | None
    maximum_window_exams: int
    maximum_window_load_points: float
    worst_student_weighted_penalty: float
    repeat_student_mean_weighted_penalty: float | None
    scoring_version: str = SCORING_VERSION
    student_penalties: tuple[StudentPenalty, ...] = ()
    input_fingerprint: str = field(default="", compare=False)
    configuration_fingerprint: str = field(default="", compare=False)


def optimization_input_errors(snapshot: InputSnapshot) -> tuple[Violation, ...]:
    return tuple(Violation("OPTIMIZATION", f"Module {m.module_code} requires difficulty 1..5 and positive signed 32-bit credits.")
                 for m in snapshot.modules if not 1 <= m.difficulty <= 5 or not 1 <= m.credits <= 2**31 - 1)


def _independent_load(module, policy: OptimizationPolicy) -> int:
    # Recompute the formula here, rather than trusting the objective's load helper.
    weighted = sum(Fraction(coefficient * value, reference) for coefficient, value, reference in (
        (policy.difficulty_weight, module.difficulty, policy.reference_difficulty),
        (policy.credits_weight, module.credits, policy.reference_credits),
        (policy.duration_weight, module.duration_minutes + module.reading_time_minutes, policy.reference_duration_minutes),
    ))
    units = weighted * Fraction(100 * policy.load_scale,
                               policy.difficulty_weight + policy.credits_weight + policy.duration_weight)
    return (units + Fraction(1, 2)).__floor__()


def evaluate_timetable(snapshot: InputSnapshot, draft: DraftTimetable,
                       policy: OptimizationPolicy = OptimizationPolicy()) -> TimetableEvaluation:
    check_budget()
    errors = validate_timetable(snapshot, draft)
    if errors:
        raise SchedulingValidationError(errors)
    errors = optimization_input_errors(snapshot)
    if errors:
        raise SchedulingValidationError(errors)
    if type(policy) is not OptimizationPolicy:
        raise SchedulingValidationError((Violation("OPTIMIZATION", "policy must be an OptimizationPolicy."),))
    modules = {m.module_code: m for m in snapshot.modules}
    # Each student is scored on the session they actually sit (read from the seats).
    sitting = {(seat.student_id, session.module_code): session
               for session in draft.sessions for room in session.rooms for seat in room.seats}
    loads = {code: _independent_load(m, policy) for code, m in modules.items()}
    by_student = defaultdict(list)
    repeats = set()
    for row in snapshot.registrations:
        by_student[row.student_id].append(row.module_code)
        if row.registration_type == "repeat":
            repeats.add(row.student_id)
    totals = [0] * 5
    student_objectives = {}
    same = multi = short = difficult = max_count = max_load = 0
    consecutive_gaps = []
    span = (snapshot.period.end_date - snapshot.period.start_date).days + 1
    # All calendar windows intersecting the period, including partial edge windows.
    windows = sorted({(max(0, x), min(span, x + policy.window_days))
                      for x in (*range(span), *range(1 - policy.window_days, span - policy.window_days + 1))})
    for student, codes in by_student.items():
        check_budget()
        own = {code: sitting[student, code] for code in codes}
        ordered = sorted(own.values(), key=lambda s: s.start)
        count = len(codes)
        raw = [0] * 5
        for a, b in combinations(ordered, 2):
            check_budget()
            gap = int((b.start - a.end).total_seconds() // 60)
            high = min(modules[a.module_code].difficulty, modules[b.module_code].difficulty) >= policy.high_difficulty_minimum
            raw[0] += (max(0, policy.difficult_target_minutes - max(gap, policy.preparation_target_minutes))
                       * policy.score_scale // (policy.difficult_target_minutes - policy.preparation_target_minutes)) if high else 0
            raw[1] += (max(0, policy.preparation_target_minutes - max(gap, policy.short_gap_minutes))
                       * policy.score_scale // (policy.preparation_target_minutes - policy.short_gap_minutes))
            same_day = a.start.date() == b.start.date()
            raw[2] += policy.score_scale if same_day else max(0, policy.short_gap_minutes - gap) * policy.score_scale // policy.short_gap_minutes
            same += same_day
            short += gap < policy.short_gap_minutes
            difficult += high and gap < policy.difficult_target_minutes
        consecutive_gaps.extend(int((b.start - a.end).total_seconds() // 60) for a, b in zip(ordered, ordered[1:]))
        multi += sum(n > 1 for n in Counter(s.start.date() for s in ordered).values())
        total_load = sum(loads[c] for c in codes)
        deviations = {c: count * loads[c] - total_load for c in codes}
        single_excess = max(0, *deviations.values())
        load_bound = sum(max(0, value) for value in deviations.values()) - single_excess
        for low, high in windows:
            check_budget()
            in_window = [c for c in codes if low <= (own[c].start.date() - snapshot.period.start_date).days < high]
            raw[3] = max(raw[3], sum(deviations[c] for c in in_window) - single_excess)
            raw[4] = max(raw[4], len(in_window) - policy.preferred_window_exams)
            max_count = max(max_count, len(in_window))
            max_load = max(max_load, sum(loads[c] for c in in_window))
        pair_bound = count * (count - 1) // 2 * policy.score_scale
        bounds = (pair_bound, pair_bound, pair_bound, load_bound, max(0, count - policy.preferred_window_exams))
        normalized = [value * policy.score_scale // bound if bound else 0 for value, bound in zip(raw, bounds)]
        for k, value in enumerate(normalized):
            totals[k] += value
        student_objectives[student] = sum(w * p for w, p in zip(policy.weights, normalized))
    students = len(by_student)
    denominator = students * policy.score_scale
    penalties = tuple(PenaltyScore(f"S{k + 1}", weight, value * 100 / denominator, weight * value / denominator)
                      for k, (weight, value) in enumerate(zip(policy.weights, totals)))
    objective = sum(student_objectives.values())
    gaps = sorted(consecutive_gaps)
    middle = len(gaps) // 2
    median = ((gaps[middle] + gaps[(len(gaps) - 1) // 2]) / 2) if gaps else None
    repeat_scores = [student_objectives[s] for s in repeats]
    check_budget()
    return TimetableEvaluation(
        penalties, objective, objective / denominator, students, same, multi, short, difficult,
        gaps[0] if gaps else None, median, gaps[(len(gaps) - 1) // 10] if gaps else None,
        max_count, max_load / policy.load_scale, max(student_objectives.values()) / policy.score_scale,
        sum(repeat_scores) / (len(repeat_scores) * policy.score_scale) if repeat_scores else None,
        student_penalties=tuple(StudentPenalty(student, units) for student, units in sorted(student_objectives.items())),
        input_fingerprint=snapshot.fingerprint, configuration_fingerprint=policy.fingerprint,
    )
