"""Immutable lunch assessment: teaching-free is not evidence of taking lunch."""

from dataclasses import asdict, dataclass
from datetime import date
from enum import StrEnum
import json

from schedence_teaching.consecutive.results import json_value
from schedence_teaching.schemas import Stream


class LunchStatus(StrEnum):
    CLEAR = "CLEAR"
    VIOLATED = "VIOLATED"
    APPLICABILITY_UNKNOWN = "APPLICABILITY_UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True)
class LunchOverlap:
    delivery_id: str
    covered_occurrence_ids: tuple[str, ...]
    start_minute: int
    end_minute: int
    overlap_minutes: int


@dataclass(frozen=True)
class GroupDateLunchAssessment:
    group_id: str
    academic_year: int
    stream: Stream
    day: str
    on_date: date
    lunch_start_minute: int
    lunch_end_minute: int
    status: LunchStatus
    direct_delivery_ids: tuple[str, ...]
    inherited_delivery_ids: tuple[str, ...]
    observed_overlaps: tuple[LunchOverlap, ...]
    diagnostics: tuple[str, ...]


@dataclass(frozen=True)
class StudentLunchReport:
    policy_snapshot_json: str
    calendar_snapshot_json: str
    physical_delivery_count: int
    covered_original_occurrence_count: int
    groups: tuple[GroupDateLunchAssessment, ...]
    hard_validation_checks: tuple[str, ...]
    diagnostics: tuple[str, ...]
    hard_lunch_constraints_enforced_by_solver: bool = False

    @property
    def applicable_group_date_count(self):
        return sum(row.status in {LunchStatus.CLEAR, LunchStatus.VIOLATED} for row in self.groups)

    @property
    def unknown_group_date_count(self):
        return sum(row.status is LunchStatus.APPLICABILITY_UNKNOWN for row in self.groups)

    @property
    def violation_count(self):
        """Group/delivery associations; not additional lecturer bookings/workload."""
        return sum(len(row.observed_overlaps) for row in self.groups if row.status is LunchStatus.VIOLATED)

    @property
    def violating_delivery_ids(self):
        return tuple(sorted({overlap.delivery_id for row in self.groups if row.status is LunchStatus.VIOLATED
            for overlap in row.observed_overlaps}))

    @property
    def compliant(self):
        if self.violation_count:
            return False
        if self.unknown_group_date_count or not self.applicable_group_date_count:
            return None
        return True

    def to_dict(self, *, include_group_details=False):
        policy = json.loads(self.policy_snapshot_json)
        output = dict(policy=policy, academic_week=json.loads(self.calendar_snapshot_json),
            physical_delivery_count=self.physical_delivery_count,
            covered_original_occurrence_count=self.covered_original_occurrence_count,
            student_group_count=len({row.group_id for row in self.groups}),
            applicable_group_date_count=self.applicable_group_date_count,
            unknown_group_date_count=self.unknown_group_date_count,
            not_applicable_group_date_count=sum(row.status is LunchStatus.NOT_APPLICABLE for row in self.groups),
            violating_group_date_count=sum(row.status is LunchStatus.VIOLATED for row in self.groups),
            group_delivery_violation_count=self.violation_count,
            observed_group_delivery_overlap_count=sum(len(row.observed_overlaps) for row in self.groups),
            unique_violating_physical_delivery_count=len(self.violating_delivery_ids), compliant=self.compliant,
            applicability_resolved=policy["applicable_dates"] is not None and policy["applicable_streams"] is not None,
            diagnostics=list(self.diagnostics), hard_validation_checks=list(self.hard_validation_checks),
            fixed_lunch_periods=True, assessment_mode="INDEPENDENT_VALIDATION_OF_ENFORCED_RESULT" if self.hard_lunch_constraints_enforced_by_solver else "INDEPENDENT_ONLY",
            hard_lunch_constraints_enforced_by_solver=self.hard_lunch_constraints_enforced_by_solver,
            lecturer_lunch_assessed=False, lunch_consumption_verified=False, attendance_inferred=False,
            university_ready=False)
        if include_group_details:
            output["groups"] = json_value([asdict(row) for row in self.groups])
            output["violating_delivery_ids"] = list(self.violating_delivery_ids)
        return output
