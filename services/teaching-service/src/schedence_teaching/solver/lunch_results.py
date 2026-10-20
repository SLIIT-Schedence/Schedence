"""Hard lunch compliance metadata, regenerated from the Step 11D.1 assessment."""

from dataclasses import dataclass, replace
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from schedence_teaching.lunch import StudentLunchReport


def enforced_lunch_assessment(assessment):
    return replace(assessment, hard_lunch_constraints_enforced_by_solver=True,
        diagnostics=tuple("fixed_periods_enforced_by_solver" if code == "fixed_periods_assessed_not_enforced_by_solver"
            else code for code in assessment.diagnostics))


@dataclass(frozen=True)
class StudentLunchEnforcementReport:
    assessment: "StudentLunchReport"
    enforcement_config_json: str
    enforced_academic_years: tuple[int, ...]
    evaluated_group_ids: tuple[str, ...]

    def to_dict(self, *, include_details=False):
        source = json.loads(self.assessment.policy_snapshot_json)
        return dict(enabled=True, hard_lunch_constraints_enforced=True, policy_reference=source["policy_reference"],
            enforced_academic_years=list(self.enforced_academic_years), evaluated_group_ids=list(self.evaluated_group_ids),
            applicable_dates=source["applicable_dates"], applicable_streams=source["applicable_streams"],
            applied_fixed_windows=source["year_periods"], enforcement_config=json.loads(self.enforcement_config_json),
            assessment=self.assessment.to_dict(include_group_details=include_details),
            compliant=self.assessment.compliant, violations=self.assessment.violation_count,
            lunch_resource_bookings_created=False, lecturer_lunch_enforced=False, university_ready=False)


def student_lunch_report(assessment, context):
    return StudentLunchEnforcementReport(enforced_lunch_assessment(assessment), context.config.model_dump_json(),
        tuple(sorted({row.academic_year for row in assessment.groups if row.status in {"CLEAR", "VIOLATED"}})),
        tuple(sorted({row.group_id for row in assessment.groups})))
