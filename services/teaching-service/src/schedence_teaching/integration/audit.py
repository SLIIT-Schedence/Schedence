"""Read-only staging audit, reusing source-bound workbook importers."""

from collections import Counter
from pathlib import Path

from schedence_teaching.excel_import import (
    VenueImportConfig, WideImportConfig, import_venue_excel, import_wide_excel,
)
from schedence_teaching.excel_import.preference_adapter import map_lecturer_preferences
from schedence_teaching.excel_import.preference_config import LecturerPreferenceMapping


def audit_development_datasets(local_data: Path, *, include_row_diagnostics: bool = False) -> dict:
    """Never write source files/reports or return staff names, IDs or preferences.

    Missing mapping/workbook files raise, rather than masquerading as a valid
    zero-record dataset. Import errors remain visible as aggregate issue codes.
    """
    local_data = Path(local_data)
    academic = import_wide_excel(WideImportConfig.model_validate_json(
        (local_data / "department-aligned.mapping.json").read_text(encoding="utf-8")), base_dir=local_data)
    venues = import_venue_excel(VenueImportConfig.model_validate_json(
        (local_data / "venues.mapping.json").read_text(encoding="utf-8")), base_dir=local_data)
    preferences = map_lecturer_preferences(academic, LecturerPreferenceMapping(
        layout="department-aligned-preferences-v1", source_reference="step12a:read-only-source-audit"))
    academic_summary, venue_summary, preference_summary = academic.summary(), venues.summary(), preferences.summary()
    report = dict(data_status=academic_summary["data_status"],
        source_confirmation=academic_summary.get("source_confirmation"),
        ready_for_actual_campus=False, university_ready=False,
        counts=dict(staff=len(academic.staff_members), academic_lecturers=len(academic.academic_staff),
            instructors=len(academic.instructors), modules=len(academic.source_modules),
            teaching_team_entries=len(academic.source_assignments),
            valid_teaching_team_references=sum(row.valid_reference for row in academic.source_assignments),
            lecturer_preference_rows=len(academic.source_preferences), venues=len(venues.venues)),
        errors=dict(academic=sum(row.severity == "error" for row in academic.issues),
            venues=sum(row.severity == "error" for row in venues.issues),
            preferences=sum(row.severity == "error" for row in preferences.issues)),
        academic_issue_counts=academic_summary["issue_counts"],
        duplicate_checks=academic_summary["duplicate_checks"],
        contract_blockers=academic_summary["contract_blockers"],
        venue_inventory=dict(campus_confirmation_applied=venues.verification_applied,
            missing_location_count=venue_summary["missing_location_count"],
            type_counts=venue_summary["venue_type_counts"], issue_counts=venue_summary["issue_counts"]),
        preferences={key: preference_summary[key] for key in (
            "soft_preference_candidates", "scoped_preference_contracts", "unscoped_candidates",
            "candidates_without_explicit_weight", "candidates_without_stream_scope", "unconfirmed_candidates",
            "candidate_categories", "issue_counts")})
    if include_row_diagnostics:
        report["row_diagnostics"] = [dict(status="MISSING", field=field,
            workbook=row.location.workbook, worksheet=row.location.worksheet,
            row=row.location.row, column=None,
            explanation="No authoritative source column configured; obtain a stable offering identifier or confirmed stream.")
            for row in academic.source_modules for field in row.missing_contract_fields]
        report["row_diagnostics"].extend(dict(status="INVALID" if row.severity == "error" else "MISSING", code=row.code,
            workbook=row.workbook, worksheet=row.worksheet, row=row.row, column=row.column)
            for row in preferences.issues)
    return report
