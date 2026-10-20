"""P3.1: one explicit, validated lexicographic stage plan for every enabled objective.

The protected prefix is the existing delivery/Priority 1 order up to its last
protected criterion (deliveries, contact minutes, peak/total overage, maximum
deviation), kept exactly as each priority mode defines it. Every other enabled
criterion must be listed once in ``order``; nothing is placed implicitly.
Module reports and validators see the same plan projected onto their own
criteria, so their arithmetic and checks are reused unchanged.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Self

from pydantic import Field, model_validator

from schedence_teaching.schemas.models import InputContract, RequiredText

PROTECTED = ("DELIVERIES", "CONTACT_MINUTES", "MAXIMUM_OVER_TARGET_MINUTES", "TOTAL_OVER_TARGET_MINUTES",
             "MAXIMUM_TARGET_DEVIATION")


class StageCriterion(StrEnum):
    MAX_STUDENT_DAILY_MINUTES = "MAX_STUDENT_DAILY_MINUTES"
    MAX_STAFF_DAILY_MINUTES = "MAX_STAFF_DAILY_MINUTES"
    THIN_TEACHING_DAYS = "THIN_TEACHING_DAYS"
    CONSECUTIVE_PENALTY = "CONSECUTIVE_PENALTY"
    PREFERENCE_PENALTY = "PREFERENCE_PENALTY"
    AGGREGATE_TARGET_DEVIATION = "AGGREGATE_TARGET_DEVIATION"


class ObjectiveStagePlan(InputContract):
    plan_reference: RequiredText
    # Every enabled non-protected criterion, highest priority first.
    order: Annotated[tuple[StageCriterion, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def distinct(self) -> Self:
        if len(set(self.order)) != len(self.order):
            raise ValueError("stage plan criteria must be distinct")
        return self


def _protected(legacy):
    last = max((index for index, key in enumerate(legacy) if key in PROTECTED), default=-1)
    return tuple(legacy[:last + 1])


def project(plan, legacy_order):
    """One module's legacy order seen through the shared plan (identity when no plan is set)."""
    legacy_order = tuple(legacy_order)
    if plan is None:
        return legacy_order
    protected = _protected(legacy_order)
    rest = tuple(key.value for key in plan.order if key.value in legacy_order)
    if set(protected) | set(rest) != set(legacy_order) or len(protected) + len(rest) != len(legacy_order):
        raise ValueError("Stage plan does not cover this objective module's criteria exactly once.")
    return protected + rest


def unified_order(plan, base_order, extra, *, compactness=None, daily=None, fairness_enabled):
    """Full checked order: protected prefix of the base order plus the explicit plan."""
    base_order = tuple(base_order)
    protected = _protected(base_order)
    if any(key in StageCriterion.__members__ for key in protected):
        raise ValueError("A stage plan requires preferences and other quality criteria after the protected workload stages; "
                         "this fairness/preference priority places them inside that prefix.")
    required = set(base_order[len(protected):]) | set(extra)
    listed = [key.value for key in plan.order]
    if set(listed) != required:
        missing, unexpected = sorted(required - set(listed)), sorted(set(listed) - required)
        raise ValueError(f"Stage plan must list exactly the enabled criteria; missing {missing}, not enabled {unexpected}.")
    position = {key: index for index, key in enumerate(listed)}
    aggregate = position.get("AGGREGATE_TARGET_DEVIATION")
    for config, keys in ((compactness, ("THIN_TEACHING_DAYS",)),
                         (daily, tuple(row.value for row in daily.criteria) if daily else ())):
        if config is None:
            continue
        if [position[key] for key in keys] != sorted(position[key] for key in keys):
            raise ValueError("Stage plan reorders the explicitly configured daily-load criteria.")
        before = config.placement.value == "BEFORE_AGGREGATE_TARGET_DEVIATION"
        if before and not fairness_enabled:
            raise ValueError("BEFORE_AGGREGATE_TARGET_DEVIATION placement requires teaching-target fairness optimization.")
        if aggregate is not None and any((position[key] < aggregate) != before for key in keys):
            raise ValueError("Stage plan contradicts the configured placement relative to aggregate deviation.")
    if compactness is not None and daily is not None and compactness.placement is daily.placement:
        if daily.relative_to_compactness is None:
            raise ValueError("Daily-load and compactness stages share a placement; choose relative_to_compactness explicitly.")
        daily_first = max(position[row.value] for row in daily.criteria) < position["THIN_TEACHING_DAYS"]
        if daily_first != (daily.relative_to_compactness == "DAILY_LOAD_THEN_COMPACTNESS"):
            raise ValueError("Stage plan contradicts relative_to_compactness.")
    return protected + tuple(listed)


@dataclass(frozen=True)
class ObjectiveStageReport:
    plan_reference: str
    priority_order: tuple[str, ...]
    stages: tuple
    stage_values: tuple[tuple[str, int], ...]
    proven_stage_count: int
    all_stages_proven_optimal: bool
    overall_status: str
    model_variables: int
    model_constraints: int
    runtime_seconds: float

    def to_dict(self):
        from dataclasses import asdict
        return {"plan_reference": self.plan_reference, "priority_order": list(self.priority_order),
                "stages": [asdict(row) for row in self.stages], "stage_values": dict(self.stage_values),
                "proven_stage_count": self.proven_stage_count, "proven_stages": list(self.priority_order[:self.proven_stage_count]),
                "all_stages_proven_optimal": self.all_stages_proven_optimal, "overall_status": self.overall_status,
                "model_variables": self.model_variables, "model_constraints": self.model_constraints,
                "runtime_seconds": self.runtime_seconds, "university_ready": False}


def final_stage_values(result, order):
    """Every criterion recomputed from the final schedule or its independently validated component report."""
    from .results import ScheduledDelivery
    values = {"DELIVERIES": len(result.schedule),
              "CONTACT_MINUTES": sum(row.duration_minutes if isinstance(row, ScheduledDelivery) else row.session.duration_minutes
                                     for row in result.schedule)}
    if result.fairness_report is not None:
        values.update(dict(result.fairness_report.objective_components))
    if result.preference_report is not None:
        values["PREFERENCE_PENALTY"] = result.preference_report.score.total_weighted_penalty
    if result.consecutive_report is not None:
        values["CONSECUTIVE_PENALTY"] = result.consecutive_report.total_weighted_penalty
    if result.teaching_day_compactness_report is not None:
        values["THIN_TEACHING_DAYS"] = result.teaching_day_compactness_report.thin_staff_days
    if result.daily_load_report is not None:
        values["MAX_STUDENT_DAILY_MINUTES"] = result.daily_load_report.maximum_student_daily_minutes
        values["MAX_STAFF_DAILY_MINUTES"] = result.daily_load_report.maximum_staff_daily_minutes
    return tuple((key, values[key]) for key in order)


def stage_report(config, result, order, stages, model_variables, model_constraints):
    proven = 0
    for row in stages:
        if row.status != "OPTIMAL":
            break
        proven += 1
    return ObjectiveStageReport(config.objective_stage_plan.plan_reference, tuple(order), tuple(stages),
        final_stage_values(result, order), proven, proven == len(order), result.status.value,
        model_variables, model_constraints, result.runtime_seconds)


def stage_report_issues(result, order, report):
    """Independent consistency of the shared stage history with the final schedule."""
    issues = []
    if tuple(report.priority_order) != tuple(order):
        issues.append("Stage plan order differs from the configured plan.")
    stages = report.stages
    values = dict(final_stage_values(result, order))
    if len(stages) > len(order) or any(row.criterion != order[index] for index, row in enumerate(stages)):
        issues.append("Stages must follow the configured plan.")
    for index, row in enumerate(stages):
        if row.status == "OPTIMAL" and row.objective_value != values[row.criterion]:
            issues.append(f"Proven stage {row.criterion} differs from the final timetable.")
        if row.status != "OPTIMAL" and index != len(stages) - 1:
            issues.append("Only the final run stage may be unproven.")
        if row.status == "FEASIBLE" and row.objective_value != values[row.criterion]:
            issues.append(f"Feasible stage {row.criterion} differs from the final timetable.")
    proven = len(stages) == len(order) and all(row.status == "OPTIMAL" for row in stages)
    if (result.status.value == "OPTIMAL") != proven or report.all_stages_proven_optimal != proven:
        issues.append("Overall OPTIMAL requires every planned stage to be proven optimal.")
    if dict(report.stage_values) != values:
        issues.append("Reported stage values differ from independent recomputation.")
    return issues


def validate_combined_result(prepared, config, result, **contexts):
    """Independent P3.1 validation of a combined solve; recomputes every enabled component.

    Existing validators recheck hard constraints (including batch online days and
    hard staff blocks), fairness, consecutive scoring and preferences. P2.1/P2.2
    reports and the shared stage history are recomputed here from the schedule.
    """
    from dataclasses import replace
    from .compactness_inputs import compactness_report
    from .daily_load_inputs import daily_load_report
    from .evaluation import validate_evaluation_result
    from .results import ScheduleIssue, SolverInputError, TimetableValidationError
    plan = config.objective_stage_plan
    if plan is None:
        raise SolverInputError("Combined validation requires an explicit objective stage plan.")
    report = result.objective_stage_report
    if not isinstance(report, ObjectiveStageReport):
        raise TimetableValidationError([ScheduleIssue("objective_stage_plan_changed", "A combined solve needs its stage report.")])
    view = result
    if result.preference_report is not None and result.fairness_report is None and result.consecutive_report is None:
        # Without fairness/consecutive owners the preference validator expects the overall status label.
        view = replace(result, preference_report=replace(result.preference_report, objective_status=result.status.value))
    summary = validate_evaluation_result(prepared, config, view, **contexts)
    order = tuple(report.priority_order)
    head, tail = order[:len(order) - len(plan.order)], order[len(order) - len(plan.order):]
    issues = []
    if tail != tuple(key.value for key in plan.order) or any(key not in PROTECTED for key in head):
        issues.append("Stage order is not the protected prefix followed by the configured plan.")
    if result.fairness_report is not None and tuple(result.fairness_report.priority_order) != project(plan, result.fairness_report.priority_order):
        issues.append("Fairness order is not the plan projection.")
    if config.teaching_day_compactness is not None:
        actual = result.teaching_day_compactness_report
        expected = compactness_report(prepared, config, result.schedule, order, report.stages, actual.modeled_thin_staff_days)
        if actual != expected or actual.modeled_thin_staff_days != actual.thin_staff_days:
            issues.append("Thin teaching days differ from independent recomputation.")
    if config.daily_load_distribution is not None:
        actual = result.daily_load_report
        expected = daily_load_report(prepared, config, result.schedule, order, report.stages, dict(actual.modeled_maxima))
        measured = {"MAX_STUDENT_DAILY_MINUTES": actual.maximum_student_daily_minutes,
                    "MAX_STAFF_DAILY_MINUTES": actual.maximum_staff_daily_minutes}
        if actual != expected or any(measured[key] != value for key, value in actual.modeled_maxima):
            issues.append("Daily loads differ from independent recomputation.")
    issues.extend(stage_report_issues(result, order, report))
    if issues:
        raise TimetableValidationError([ScheduleIssue("objective_stage_plan_changed", issue) for issue in issues])
    return summary
