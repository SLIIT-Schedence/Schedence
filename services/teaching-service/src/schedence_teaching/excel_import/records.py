"""Transient imported records and reports; summaries exclude cell values."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictFloat, StrictInt, model_validator

from schedence_teaching.schemas import ModuleOffering, StaffMember, StaffType, TeachingTeamMember
from schedence_teaching.schemas.models import InputContract, RequiredText

PreferenceValue = RequiredText | StrictBool | StrictInt | Annotated[StrictFloat, Field(allow_inf_nan=False)]


class LecturerPreferenceRecord(InputContract):
    """Mapped source values only; their scheduling semantics remain unverified."""

    staff_id: RequiredText
    values: dict[RequiredText, PreferenceValue]

    @model_validator(mode="after")
    def require_values(self) -> LecturerPreferenceRecord:
        if not self.values:
            raise ValueError("a preference record must contain at least one supplied value")
        return self


@dataclass(frozen=True)
class ImportIssue:
    severity: Literal["error", "warning"]
    code: str
    workbook: str
    worksheet: str | None
    row: int | None
    column: str | None
    message: str


@dataclass
class ImportResult:
    staff_members: list[StaffMember] = field(default_factory=list)
    lecturer_preferences: list[LecturerPreferenceRecord] = field(default_factory=list)
    module_offerings: list[ModuleOffering] = field(default_factory=list)
    teaching_team_members: list[TeachingTeamMember] = field(default_factory=list)
    issues: list[ImportIssue] = field(default_factory=list)
    rows_read: dict[str, int] = field(default_factory=lambda: {
        "staff": 0, "preferences": 0, "offerings": 0, "teaching_teams": 0,
    })

    @property
    def academic_staff(self) -> list[StaffMember]:
        return [staff for staff in self.staff_members if staff.staff_type == StaffType.ACADEMIC]

    @property
    def instructors(self) -> list[StaffMember]:
        return [staff for staff in self.staff_members if staff.staff_type == StaffType.INSTRUCTOR]

    @property
    def valid(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)

    def summary(self) -> dict:
        """CLI-safe summary; raw staff names, IDs and preferences stay in memory."""
        return {
            "valid": self.valid,
            "data_status": "development_unverified",
            "rows_read": self.rows_read,
            "imported_counts": {
                "staff_members": len(self.staff_members),
                "academic_staff": len(self.academic_staff),
                "instructors": len(self.instructors),
                "lecturer_preferences": len(self.lecturer_preferences),
                "module_offerings": len(self.module_offerings),
                "teaching_team_members": len(self.teaching_team_members),
            },
            "errors": [asdict(issue) for issue in self.issues if issue.severity == "error"],
            "warnings": [asdict(issue) for issue in self.issues if issue.severity == "warning"],
        }
