"""Pure orchestration; the existing native solver and validation stay unchanged."""

from dataclasses import dataclass
from uuid import UUID

from schedence_teaching.solver import SolverResult, SolverStatus, solve_timetable, validate_student_lunch_result
from schedence_teaching.solver.evaluation import validate_evaluation_result

from .contracts import SchedulingSubmission
from .diagnostics import ReadinessReport
from .readiness import assess_readiness


@dataclass(frozen=True)
class SchedulingIntegrationResult:
    readiness: ReadinessReport
    timetable: SolverResult | None
    independently_validated: bool


def run_scheduling(submission: SchedulingSubmission, *, expected_institution_id: UUID,
        actual_campus: bool = True) -> SchedulingIntegrationResult:
    assessment = assess_readiness(submission, expected_institution_id=expected_institution_id,
        actual_campus=actual_campus)
    if not assessment.report.ready_for_generation:
        return SchedulingIntegrationResult(assessment.report, None, False)
    context = assessment.preparation
    result = solve_timetable(context.prepared, context.config, **context.solver_inputs)
    complete = result.status in {SolverStatus.OPTIMAL, SolverStatus.FEASIBLE}
    if complete:
        validate_evaluation_result(context.prepared, context.config, result, **context.solver_inputs)
    else:
        validate_student_lunch_result(context.prepared, context.config, result, **context.solver_inputs)
    return SchedulingIntegrationResult(assessment.report, result, complete)
