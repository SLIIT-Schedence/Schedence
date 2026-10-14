"""Immutable assessment results, serialized as exact numerator/denominator pairs."""

from dataclasses import asdict, dataclass, is_dataclass
from datetime import date
from fractions import Fraction

from schedence_teaching.workload.metrics import TeachingMetrics
from .metrics import TeachingTargetMetrics


def _json(value):
    if isinstance(value, Fraction):
        return {"numerator": value.numerator, "denominator": value.denominator}
    if is_dataclass(value):
        return _json(asdict(value))
    if isinstance(value, dict):
        return {key: _json(row) for key, row in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json(row) for row in value]
    if isinstance(value, date):
        return value.isoformat()
    return value


@dataclass(frozen=True)
class AssignmentOpportunities:
    eligible_offering_ids: tuple[str, ...]
    eligible_requirement_ids: tuple[str, ...]
    eligible_occurrence_ids: tuple[str, ...]
    individually_available_occurrence_ids: tuple[str, ...] | None
    evidence_scope: str
    # Counts are not additive across staff or combination alternatives.
    joint_feasibility_established: bool = False


@dataclass(frozen=True)
class StaffFairnessAssessment:
    staff_id: str
    workload_band: str | None
    appointment: str | None
    workload_policy_reference: str | None
    rule_source: str | None
    teaching: TeachingMetrics
    target: TeachingTargetMetrics
    opportunities: AssignmentOpportunities
    included_in_comparison: bool
    comparison_reasons: tuple[str, ...]
    missing_evidence: tuple[str, ...]
    weekly_teaching_cap_minutes: int | None
    daily_teaching_cap_minutes: int | None
    administrative_allocation_minutes: int | None
    verified_administrative_minutes: int
    recognized_work_ledger_complete: bool
    reserved_administrative_minutes: int | None
    hard_research_protection_minutes: int | None
    evidenced_campus_days: tuple[str, ...]
    campus_attendance_complete: bool
    compulsory_campus_days: int | None
    campus_day_exempt: bool | None


@dataclass(frozen=True)
class GroupFairnessAssessment:
    group_id: str
    reason: str
    requested_staff_ids: tuple[str, ...]
    included_staff_ids: tuple[str, ...]
    excluded_staff: tuple[tuple[str, tuple[str, ...]], ...]
    distribution: tuple[tuple[str, Fraction], ...]
    weights: tuple[tuple[str, Fraction], ...]
    mean: Fraction | None
    variance: Fraction | None
    mean_absolute_deviation: Fraction | None
    maximum_deviation: Fraction | None
    maximum_relative_deviation: Fraction | None
    balance_value: Fraction | None
    relative_threshold_exceeded_staff_ids: tuple[str, ...]
    diagnostics: tuple[str, ...]


@dataclass(frozen=True)
class FairnessReport:
    policy_snapshot_json: str
    week_calendar_json: str
    workload_policy_reference: str | None
    staff: tuple[StaffFairnessAssessment, ...]
    groups: tuple[GroupFairnessAssessment, ...]
    assigned_delivery_count: int
    scheduled_teaching_minutes: int
    hard_validation_checks: tuple[str, ...]
    availability_constraints_enforced: bool
    protected_period_constraints_enforced: bool
    preference_optimization_enabled: bool
    cp_sat_fairness_objective_enabled: bool = False

    def to_dict(self, *, include_staff_details=False):
        import json
        output = {"policy": json.loads(self.policy_snapshot_json), "academic_week": json.loads(self.week_calendar_json),
            "workload_policy_reference": self.workload_policy_reference,
            "assigned_delivery_count": self.assigned_delivery_count,
            "scheduled_teaching_minutes": self.scheduled_teaching_minutes,
            "included_staff_count": sum(row.included_in_comparison for row in self.staff),
            "missing_evidence_counts": {code: sum(code in row.missing_evidence for row in self.staff)
                                       for code in sorted({code for row in self.staff for code in row.missing_evidence})},
            "hard_validation_checks": list(self.hard_validation_checks),
            "availability_constraints_enforced": self.availability_constraints_enforced,
            "protected_period_constraints_enforced": self.protected_period_constraints_enforced,
            "preference_optimization_enabled": self.preference_optimization_enabled,
            "cp_sat_fairness_objective_enabled": self.cp_sat_fairness_objective_enabled, "hard_constraints_overridden": False,
            "university_ready": False,
            "comparison_groups": [{"group_id": row.group_id, "included_staff_count": len(row.included_staff_ids),
                "mean": _json(row.mean), "variance": _json(row.variance),
                "maximum_deviation": _json(row.maximum_deviation), "balance_value": _json(row.balance_value),
                "diagnostics": list(row.diagnostics)} for row in self.groups]}
        if include_staff_details:
            output.update(staff=_json(self.staff), comparison_groups=_json(self.groups))
        return output
