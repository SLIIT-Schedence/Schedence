"""Solver output represents only the constraints implemented in this step."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from schedence_teaching.workload import WeeklyWorkloadReport
    from .work_period_results import ScheduledAdministrativePeriod, WorkPeriodReport
    from .preference_results import PreferenceOptimizationReport
    from .fairness_results import FairnessOptimizationReport
    from .consecutive_results import ConsecutiveOptimizationReport
    from .lunch_results import StudentLunchEnforcementReport

from schedence_teaching.preparation import UnscheduledSession
from .attendance_inputs import DeliveryMode

LIMITATIONS = (
    "Implemented constraints cover group hierarchy, configured windows and one eligible responsible staff; results are not publication-ready university timetables.",
    "Unsupplied cross-group student overlap is unknown; only supplied hierarchy and population evidence are checked.",
    "Team teaching with multiple simultaneous staff is not supported.",
    "Student lunch constraints are not implemented. Travel optimization is outside the research scope. Preferences, fairness and consecutive optimization require explicit opt-in policies; administrative/research protection requires authorization and workload/attendance fulfillment requires evidence.",
)


class SolverStatus(StrEnum):
    OPTIMAL = "OPTIMAL"
    FEASIBLE = "FEASIBLE"
    INFEASIBLE = "INFEASIBLE"
    UNKNOWN = "UNKNOWN"
    MODEL_INVALID = "MODEL_INVALID"


@dataclass(frozen=True)
class ScheduledSession:
    """Generated assignment of one responsible staff, independent of source team roles."""

    session: UnscheduledSession
    day: str
    start_minute: int
    end_minute: int
    assigned_staff_id: str
    assigned_venue_id: str | None = None
    delivery_mode: DeliveryMode | None = None

    def to_dict(self) -> dict:
        return {
            **({"delivery_mode": self.delivery_mode.value} if self.delivery_mode is not None else {}),
            **self.session.to_dict(), "day": self.day, "assigned_staff_id": self.assigned_staff_id,
            "assigned_venue_id": self.assigned_venue_id,
            "start_minute": self.start_minute, "end_minute": self.end_minute,
            "start_time": f"{self.start_minute // 60:02d}:{self.start_minute % 60:02d}",
            "end_time": f"{self.end_minute // 60:02d}:{self.end_minute % 60:02d}",
        }


@dataclass(frozen=True)
class ScheduledDelivery:
    """One physical booking fulfilling one or more preserved original occurrences."""

    delivery_id: str
    is_combined: bool
    covered_sessions: tuple[UnscheduledSession, ...]
    group_ids: tuple[str, ...]
    combined_student_count: int
    day: str
    start_minute: int
    end_minute: int
    assigned_staff_id: str
    assigned_venue_id: str | None = None

    @property
    def duration_minutes(self):
        return self.covered_sessions[0].duration_minutes

    def to_dict(self):
        return {
            "delivery_id": self.delivery_id, "is_combined": self.is_combined,
            "covered_occurrence_ids": [row.occurrence_id for row in self.covered_sessions],
            "original_requirements": [row.to_dict() for row in self.covered_sessions],
            "group_ids": list(self.group_ids), "combined_student_count": self.combined_student_count,
            "duration_minutes": self.duration_minutes, "day": self.day,
            "start_minute": self.start_minute, "end_minute": self.end_minute,
            "start_time": f"{self.start_minute // 60:02d}:{self.start_minute % 60:02d}",
            "end_time": f"{self.end_minute // 60:02d}:{self.end_minute % 60:02d}",
            "assigned_staff_id": self.assigned_staff_id, "assigned_venue_id": self.assigned_venue_id,
        }


@dataclass(frozen=True)
class ScheduleIssue:
    code: str
    message: str
    occurrence_ids: tuple[str, ...] = ()


class TimetableValidationError(ValueError):
    def __init__(self, issues: list[ScheduleIssue]) -> None:
        self.issues = tuple(issues)
        super().__init__("; ".join(f"{issue.code}: {issue.message}" for issue in issues))


class SolverInputError(ValueError):
    pass


@dataclass(frozen=True)
class ValidationSummary:
    expected_occurrences: int
    assigned_occurrences: int
    clash_pairs_checked: int
    valid_for_implemented_constraints: bool = True
    checks: tuple[str, ...] = (
        "exact_occurrence_coverage", "original_metadata", "valid_day_and_times",
        "duration_and_granularity", "stream_windows", "same_group_and_ancestor_clashes",
        "assigned_staff_exists", "offering_staff_eligibility", "staff_clashes",
    )
    venue_enabled: bool = False
    delivery_count: int | None = None
    combined_deliveries: int = 0
    separate_deliveries: int | None = None
    lecturer_contact_minutes: int | None = None
    staff_policy_enabled: bool = False

    def to_dict(self) -> dict:
        return {
            "valid_for_implemented_constraints": self.valid_for_implemented_constraints,
            "expected_occurrences": self.expected_occurrences,
            "assigned_occurrences": self.assigned_occurrences,
            "clash_pairs_checked": self.clash_pairs_checked, "checks": list(self.checks),
            "venue_enabled": self.venue_enabled,
            "delivery_count": self.delivery_count, "combined_deliveries": self.combined_deliveries,
            "separate_deliveries": self.separate_deliveries,
            "lecturer_contact_minutes": self.lecturer_contact_minutes,
            "staff_policy_enabled": self.staff_policy_enabled,
        }


@dataclass(frozen=True)
class SolverResult:
    status: SolverStatus
    schedule: tuple[ScheduledSession | ScheduledDelivery, ...]
    runtime_seconds: float
    validation: ValidationSummary | None
    failure_reason: str | None = None
    venue_enabled: bool = False
    combination_enabled: bool = False
    original_occurrence_count: int = 0
    objective_mode: str | None = None
    objective_value: int | None = None
    objective_status: str | None = None
    staff_policy_enabled: bool = False
    workload_report: WeeklyWorkloadReport | None = None
    work_period_enabled: bool = False
    administrative_schedule: tuple[ScheduledAdministrativePeriod, ...] = ()
    work_period_report: WorkPeriodReport | None = None
    preference_optimization_enabled: bool = False
    preference_report: PreferenceOptimizationReport | None = None
    fairness_optimization_enabled: bool = False
    fairness_report: FairnessOptimizationReport | None = None
    consecutive_optimization_enabled: bool = False
    consecutive_report: ConsecutiveOptimizationReport | None = None
    student_lunch_enabled: bool = False
    student_lunch_report: StudentLunchEnforcementReport | None = None

    @property
    def assigned_session_count(self) -> int:
        return len(self.schedule)

    def to_dict(self) -> dict:
        return {
            "status": self.status.value, "schedule": [item.to_dict() for item in self.schedule],
            "assigned_session_count": self.assigned_session_count, "runtime_seconds": self.runtime_seconds,
            "constraint_validation": self.validation.to_dict() if self.validation is not None else None,
            "failure_reason": self.failure_reason, "university_ready": False,
            "venue_enabled": self.venue_enabled,
            "combination_enabled": self.combination_enabled,
            "original_required_occurrence_count": self.original_occurrence_count,
            "actual_delivery_count": len(self.schedule),
            "combined_delivery_count": sum(isinstance(row, ScheduledDelivery) and row.is_combined for row in self.schedule),
            "separate_delivery_count": sum(not isinstance(row, ScheduledDelivery) or not row.is_combined for row in self.schedule),
            "actual_lecturer_contact_minutes": sum(row.duration_minutes if isinstance(row, ScheduledDelivery)
                                                  else row.session.duration_minutes for row in self.schedule),
            "objective_mode": self.objective_mode, "objective_value": self.objective_value,
            "objective_status": self.objective_status,
            "staff_policy_enabled": self.staff_policy_enabled,
            "workload_assessment": self.workload_report.to_dict() if self.workload_report is not None else None,
            "work_period_enabled": self.work_period_enabled,
            "administrative_schedule": [row.to_dict() for row in self.administrative_schedule],
            "work_period_report": self.work_period_report.to_dict() if self.work_period_report is not None else None,
            "preference_optimization_enabled": self.preference_optimization_enabled,
            "preference_optimization": self.preference_report.to_dict(include_staff_details=True) if self.preference_report is not None else None,
            **({"fairness_optimization_enabled": self.fairness_optimization_enabled,
                "fairness_optimization": self.fairness_report.to_dict(include_staff_details=True) if self.fairness_report is not None else None}
               if self.fairness_optimization_enabled or self.fairness_report is not None else {}),
            **({"consecutive_optimization_enabled": self.consecutive_optimization_enabled,
                "consecutive_optimization": self.consecutive_report.to_dict(include_details=True) if self.consecutive_report is not None else None}
               if self.consecutive_optimization_enabled or self.consecutive_report is not None else {}),
            **({"student_lunch_enabled": self.student_lunch_enabled,
                "student_lunch": self.student_lunch_report.to_dict(include_details=True) if self.student_lunch_report is not None else None}
               if self.student_lunch_enabled or self.student_lunch_report is not None else {}),
            "limitations": [*(row.replace("Student lunch constraints are not implemented.",
                "Fixed student lunch constraints are enabled under explicitly confirmed date/stream applicability.")
                if self.student_lunch_enabled else row for row in LIMITATIONS), (
                "Venue checks cover only explicitly supplied capacity, eligibility, calendars and shared-space relationships."
                if self.venue_enabled else "Venue allocation and constraints were not enabled for this result."
            ), ("Staff availability and explicit caps are enforced; workload targets and campus attendance are assessed separately."
                if self.staff_policy_enabled else "Staff availability and teaching caps were not enabled for this result.")],
        }
