"""Source-row staging is separate from authoritative backend identifiers."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from schedence_teaching.schemas import TeachingRole

from .records import ImportIssue, ImportResult


@dataclass(frozen=True)
class SourceLocation:
    workbook: str
    worksheet: str
    row: int

    @property
    def diagnostic_key(self) -> str:
        """A location only. Never use this as an offering_id."""
        return f"source-row:{self.workbook}:{self.worksheet}:{self.row}"

    def issue(self, result: ImportResult, code: str, column: str | None, message: str,
              *, severity: str = "error") -> None:
        result.issues.append(ImportIssue(
            severity, code, self.workbook, self.worksheet, self.row, column, message,
        ))


@dataclass
class SourceRecord:
    location: SourceLocation
    source_values: dict[str, Any]
    invalid_cells: set[str] = field(default_factory=set)


@dataclass
class SourceModuleRecord(SourceRecord):
    contract_fields: dict[str, Any] = field(default_factory=dict)
    valid_fields: bool = True
    missing_contract_fields: tuple[str, ...] = ()


@dataclass
class SourceTeamAssignment:
    location: SourceLocation
    id_column: str
    name_column: str
    source_staff_id: Any
    source_display_name: Any
    role: TeachingRole
    valid_reference: bool = True


@dataclass
class WideImportResult(ImportResult):
    source_staff: list[SourceRecord] = field(default_factory=list)
    source_preferences: list[SourceRecord] = field(default_factory=list)
    source_modules: list[SourceModuleRecord] = field(default_factory=list)
    source_assignments: list[SourceTeamAssignment] = field(default_factory=list)
    source_confirmation_reference: str | None = None
    lecture_hours_interpretation: str | None = None

    def summary(self) -> dict:
        report = super().summary()
        academic_ids = {staff.staff_id for staff in self.academic_staff}
        instructor_ids = {staff.staff_id for staff in self.instructors}
        report["data_status"] = "accepted_development_unverified"
        if self.source_confirmation_reference:
            report["data_status"] = "user_confirmed_university_sourced"
            report["source_confirmation"] = {
                "reference": self.source_confirmation_reference,
                "scope": "Supplied academic source records, not missing scheduling calendars or policy parameters.",
                "original_source_labels_preserved": True,
            }
        report["lecture_hours_interpretation"] = self.lecture_hours_interpretation
        report["staged_counts"] = {
            "staff_rows": len(self.source_staff), "preference_rows": len(self.source_preferences),
            "module_records": len(self.source_modules),
            "teaching_team_assignments": len(self.source_assignments),
            "assignments_with_valid_references": sum(item.valid_reference for item in self.source_assignments),
            "academic_assignments": sum(
                item.source_staff_id in academic_ids
                for item in self.source_assignments
            ),
            "instructor_assignments": sum(
                item.source_staff_id in instructor_ids
                for item in self.source_assignments
            ),
        }
        report["issue_counts"] = dict(Counter(issue.code for issue in self.issues))
        report["duplicate_checks"] = {
            code: sum(issue.code == code for issue in self.issues)
            for code in ("duplicate_record", "staff_id_name_conflict", "same_name_multiple_ids",
                         "duplicate_module_record", "duplicate_offering_id", "repeated_team_staff",
                         "ambiguous_module_identity")
        }
        report["source_provenance"] = {
            "staff_status_counts": dict(Counter(str(row.source_values.get("Data_Status")) for row in self.source_staff)),
            "department_source_counts": dict(Counter(str(row.source_values.get("Department_Source")) for row in self.source_staff)),
        }
        missing = Counter(field for item in self.source_modules for field in item.missing_contract_fields)
        report["contract_blockers"] = [
            {"model": "ModuleOffering", "field": name, "affected_records": count}
            for name, count in sorted(missing.items())
        ]
        if missing.get("offering_id"):
            report["contract_blockers"].append({
                "model": "TeachingTeamMember", "field": "offering_id",
                "affected_records": len(self.source_assignments),
            })
        report["contract_blockers"].extend([
            {"model": "StudentGroup", "fields": ["group_id", "academic_year", "semester", "stream", "student_count"],
             "reason": "No student group dataset supplied; subgroup relationships also require source data."},
            {"model": "SessionRequirement", "fields": ["requirement_id", "offering_id", "group_id", "session_type",
                                                       "duration_minutes", "sessions_per_week"],
             "reason": "Weekly contact hours do not specify group-specific session durations and frequency."},
        ])
        if self.lecture_hours_interpretation:
            # This importer does not read the separately supplied student-count workbook.
            report["contract_blockers"][-2]["reason"] = (
                "Student counts are supplied separately; map groups and populations to specialization-specific demand.")
            report["contract_blockers"][-2]["fields"] = ["specialization_applicability", "population_evidence"]
            report["contract_blockers"][-1]["reason"] = (
                "Lecture hours specify one continuous weekly lecture. Offering/group applicability and "
                "non-lecture session duration/frequency mappings are still required.")
            report["contract_blockers"][-1]["fields"] = ["offering_id", "group_id"]
            report["contract_blockers"][-1]["additional_inputs"] = ["non-lecture duration/frequency", "session audiences"]
        report["ready_for_scheduling"] = False
        return report
