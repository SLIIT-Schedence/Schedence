"""Independent fairness checks on independently scored timetables. No solver imports."""

from dataclasses import dataclass
from fractions import Fraction

from app.scheduling.contracts import SchedulingValidationError, Violation
from app.scheduling.evaluation import StudentPenalty, TimetableEvaluation
from app.scheduling.fairness_policy import FairnessPolicy
from app.scheduling.optimization_policy import OptimizationPolicy


@dataclass(frozen=True)
class FairnessComparison:
    protection_applied: bool
    limits_satisfied: bool | None
    students: int
    students_improved: int
    students_worsened: int
    students_unchanged: int
    baseline_worst_normalized_penalty: float
    selected_worst_normalized_penalty: float
    maximum_individual_increase_points: float
    worst_student_increase_points: float
    individual_limit_exceedances: int
    worst_limit_exceeded: bool
    violations: tuple[Violation, ...] = ()


def compare_fairness(baseline: TimetableEvaluation, selected: TimetableEvaluation,
                     scoring: OptimizationPolicy, fairness: FairnessPolicy = FairnessPolicy()) -> FairnessComparison:
    """Use exact rational comparisons, independently of the solver's integer caps.

    Normalized workload score = 100 * per-student objective / (score_scale * sum(weights)).
    A zero-weight objective has zero fairness scores, never a division by zero.
    """
    if type(scoring) is not OptimizationPolicy or type(fairness) is not FairnessPolicy:
        raise SchedulingValidationError((Violation("FAIRNESS", "Expected OptimizationPolicy and FairnessPolicy settings."),))
    if type(baseline) is not TimetableEvaluation or type(selected) is not TimetableEvaluation:
        raise SchedulingValidationError((Violation("FAIRNESS", "Fairness requires two independently evaluated timetables."),))
    if (not baseline.input_fingerprint or baseline.input_fingerprint != selected.input_fingerprint
            or baseline.configuration_fingerprint != scoring.fingerprint
            or selected.configuration_fingerprint != scoring.fingerprint):
        raise SchedulingValidationError((Violation("FAIRNESS", "Fairness comparisons require the same frozen input and scoring configuration."),))
    for report in (baseline, selected):
        if (type(report.students) is not int or report.students < 1 or type(report.objective_units) is not int
                or type(report.student_penalties) is not tuple or any(type(s) is not StudentPenalty
                or type(s.student_id) is not str or not s.student_id.strip() or type(s.objective_units) is not int
                for s in report.student_penalties)):
            raise SchedulingValidationError((Violation("FAIRNESS", "Student penalties require immutable, typed student records."),))
    before = {s.student_id: s.objective_units for s in baseline.student_penalties}
    after = {s.student_id: s.objective_units for s in selected.student_penalties}
    if (not before or before.keys() != after.keys()
            or len(before) != baseline.students or len(after) != selected.students
            or len(before) != len(baseline.student_penalties) or len(after) != len(selected.student_penalties)
            or sum(before.values()) != baseline.objective_units or sum(after.values()) != selected.objective_units):
        raise SchedulingValidationError((Violation("FAIRNESS", "Student penalties must be complete, unique and consistent."),))
    denominator = scoring.score_scale * sum(scoring.weights)
    if any(type(value) is not int or not 0 <= value <= denominator for value in (*before.values(), *after.values())):
        raise SchedulingValidationError((Violation("FAIRNESS", "Student objective values are outside their valid integer bounds."),))
    denominator = denominator or 1
    individual_allowance = Fraction(str(fairness.max_individual_increase_points))
    worst_allowance = Fraction(str(fairness.max_worst_increase_points))
    violations = []
    increases = [Fraction(100 * (after[student] - value), denominator) for student, value in before.items()]
    exceedances = sum(value > individual_allowance for value in increases)
    worst_increase = Fraction(100 * (max(after.values()) - max(before.values())), denominator)
    worst_exceeded = fairness.protect_worst_student and worst_increase > worst_allowance
    if fairness.enabled:
        if exceedances:
            violations.append(Violation("FAIRNESS", f"{exceedances} student(s) exceed the baseline-relative increase limit."))
        if worst_exceeded:
            violations.append(Violation("FAIRNESS", "Worst-student penalty exceeds its baseline-relative limit."))
    return FairnessComparison(
        fairness.enabled, not violations if fairness.enabled else None, len(before),
        sum(after[s] < value for s, value in before.items()),
        sum(after[s] > value for s, value in before.items()),
        sum(after[s] == value for s, value in before.items()),
        float(Fraction(100 * max(before.values()), denominator)),
        float(Fraction(100 * max(after.values()), denominator)),
        float(max(Fraction(0), *increases)), float(max(Fraction(0), worst_increase)),
        exceedances, worst_exceeded, tuple(violations),
    )
