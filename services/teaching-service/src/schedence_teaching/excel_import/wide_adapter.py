"""Read the inspected wide layout without inventing authoritative identifiers."""

from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
from pathlib import Path
from pydantic import TypeAdapter, ValidationError

from schedence_teaching.schemas import ModuleOffering, StaffType, TeachingRole, TeachingTeamMember

from .config import ImportConfig, TableMapping
from .importer import import_excel
from .records import ImportIssue
from .source_reader import read_source as _read_source
from .staging import SourceLocation, SourceModuleRecord, SourceRecord, SourceTeamAssignment, WideImportResult
from .wide_config import (
    MODULE_COLUMNS, PREFERENCE_COLUMNS, STAFF_COLUMNS, TEAM_COLUMNS, DatasetSheet, WideImportConfig,
)


def _blank(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())



def _field(model, name: str, value: object, row: SourceRecord, column: str, result: WideImportResult):
    """Validate a supplied field with its unchanged Step 2 annotation."""
    if column in row.invalid_cells:
        return None, False
    try:
        adapter = TypeAdapter(model.model_fields[name].rebuild_annotation())
        return adapter.validate_python(value), True
    except ValidationError as error:
        message = error.errors(include_input=False, include_url=False)[0]["msg"]
        row.location.issue(result, "invalid_record", column, message)
        return None, False


def _check_staff_and_preferences(result: WideImportResult) -> None:
    ids = defaultdict(list)
    names = defaultdict(list)
    for row in result.source_staff:
        identity, name = row.source_values["Staff_ID"], row.source_values["Staff_Name"]
        if isinstance(identity, str):
            ids[identity].append(row)
        if isinstance(name, str) and not _blank(name):
            names[" ".join(name.split()).casefold()].append(row)
    for matches in ids.values():
        if len({str(row.source_values["Staff_Name"]) for row in matches}) > 1:
            for row in matches:
                row.location.issue(result, "staff_id_name_conflict", "Staff_Name",
                                   "The same Staff ID has different source names; no name is replaced.")
    for matches in names.values():
        if len({str(row.source_values["Staff_ID"]) for row in matches}) > 1:
            for row in matches:
                row.location.issue(result, "same_name_multiple_ids", "Staff_Name",
                                   "The same name occurs under different IDs; review identity without merging people.",
                                   severity="warning")
    by_id = {staff.staff_id: staff for staff in result.staff_members}
    invalid_preferences = set()
    for row in result.source_preferences:
        identity = row.source_values["Staff_ID"]
        staff = by_id.get(identity)
        if staff is None:
            continue  # Generic reference validation already reports this.
        for column, expected in (("Staff_Name", staff.staff_name), ("Position", staff.position),
                                 ("Department", staff.department)):
            if row.source_values[column] != expected:
                row.location.issue(result, "preference_staff_mismatch", column,
                                   "Preference identity/profile value differs from Staff Master; neither source is changed.")
                invalid_preferences.add(identity)
    result.lecturer_preferences = [row for row in result.lecturer_preferences if row.staff_id not in invalid_preferences]


def _modules(config: WideImportConfig, result: WideImportResult, base_dir: Path) -> None:
    columns = dict(MODULE_COLUMNS)
    if config.offering_id_column:
        columns["offering_id"] = config.offering_id_column
    if config.stream_column:
        columns["stream"] = config.stream_column
    required = set(columns.values()) | {column for pair in TEAM_COLUMNS for column in pair[:2]}
    rows = _read_source(config.modules, required, result, base_dir)
    result.rows_read["offerings"] = len(rows)
    by_id = {staff.staff_id: staff for staff in result.staff_members}
    duplicate_rows = defaultdict(list)
    by_partial_identity = defaultdict(list)
    by_code = defaultdict(list)
    seen_offering_ids = defaultdict(list)
    for row in rows:
        module = SourceModuleRecord(row.location, row.source_values, row.invalid_cells)
        result.source_modules.append(module)
        for field_name, column in columns.items():
            value = row.source_values[column]
            if field_name == "semester" and isinstance(value, str):
                value = config.semester_values.get(value, value)
            parsed, valid = _field(ModuleOffering, field_name, value, row, column, result)
            if valid:
                module.contract_fields[field_name] = parsed
            else:
                module.valid_fields = False
        module.missing_contract_fields = tuple(
            field for field in ("offering_id", "stream") if field not in module.contract_fields
        )
        # A missing source column is a conversion blocker, not a fabricated ID.
        if module.valid_fields and not module.missing_contract_fields:
            result.module_offerings.append(ModuleOffering.model_validate(module.contract_fields))
        if "offering_id" in module.contract_fields:
            seen_offering_ids[module.contract_fields["offering_id"]].append(module)
        code = module.contract_fields.get("module_code")
        if code is not None:
            by_code[code].append(module)
        # Derived formulas and the dropdown helper are not module identity fields.
        signature = tuple((key, type(value).__name__, repr(value)) for key, value in row.source_values.items()
                          if key not in {"Total_Student_Contact_Hours",
                                         "Lecturer Selection Options (90 academic + 20 DEMO instructors)"})
        duplicate_rows[signature].append(module)
        if module.valid_fields and "offering_id" in module.missing_contract_fields:
            identity = tuple(module.contract_fields[field] for field in MODULE_COLUMNS)
            by_partial_identity[identity].append(module)
        seen_staff = set()
        for id_column, name_column, role_name in TEAM_COLUMNS:
            raw_id, raw_name = row.source_values[id_column], row.source_values[name_column]
            if _blank(raw_id) and _blank(raw_name):
                if role_name in {"LIC", "CO_LIC"}:
                    row.location.issue(result, "missing_lead_assignment", id_column,
                                       "LIC/Co-LIC assignment is missing; no staff member is substituted.")
                continue
            assignment = SourceTeamAssignment(row.location, id_column, name_column, raw_id, raw_name,
                                              TeachingRole(role_name))
            result.source_assignments.append(assignment)
            identity, valid = _field(TeachingTeamMember, "staff_id", raw_id, row, id_column, result)
            assignment.valid_reference = valid and name_column not in row.invalid_cells
            if not valid:
                continue
            if identity in seen_staff:
                row.location.issue(result, "repeated_team_staff", id_column,
                                   "A staff member occurs more than once in this module team; all slots are preserved.")
                assignment.valid_reference = False
            seen_staff.add(identity)
            staff = by_id.get(identity)
            if staff is None:
                row.location.issue(result, "unknown_staff", id_column,
                                   "Assignment ID does not resolve to a valid unique Staff Master record.")
                assignment.valid_reference = False
                continue
            if raw_name != f"{staff.position} | {staff.staff_name}":
                row.location.issue(result, "assignment_name_mismatch", name_column,
                                   "Assignment display must match Position | Staff_Name for its source ID.")
                assignment.valid_reference = False
            if role_name in {"LIC", "CO_LIC"} and staff.staff_type != StaffType.ACADEMIC:
                row.location.issue(result, "invalid_role_assignment", id_column,
                                   "LIC and CO_LIC roles require ACADEMIC staff; source assignment is retained.")
                assignment.valid_reference = False
            if assignment.valid_reference and "offering_id" in module.contract_fields:
                result.teaching_team_members.append(TeachingTeamMember(
                    offering_id=module.contract_fields["offering_id"], staff_id=identity, role=role_name,
                ))
    result.rows_read["teaching_teams"] = len(result.source_assignments)
    for matches in duplicate_rows.values():
        if len(matches) > 1:
            for module in matches:
                module.location.issue(result, "duplicate_module_record", "Module_Code",
                                      "Identical source module records occur more than once; every row is retained.")
    for matches in by_code.values():
        if len(matches) > 1:
            for module in matches:
                module.location.issue(result, "shared_module_code", "Module_Code",
                                      "Module code occurs in multiple source rows; no records or assignments are merged.",
                                      severity="warning")
    for matches in by_partial_identity.values():
        if len(matches) > 1:
            for module in matches:
                module.location.issue(result, "ambiguous_module_identity", "Module_Code",
                                      "Module metadata repeats without authoritative offering IDs; resolve identity without merging rows.",
                                      severity="warning")
    duplicates = {identifier for identifier, matches in seen_offering_ids.items() if len(matches) > 1}
    for identifier in duplicates:
        for module in seen_offering_ids[identifier]:
            module.location.issue(result, "duplicate_offering_id", config.offering_id_column,
                                  "Authoritative offering ID is duplicated; source rows are retained for review.")
    result.module_offerings = [row for row in result.module_offerings if row.offering_id not in duplicates]
    valid_offerings = {row.offering_id for row in result.module_offerings}
    result.teaching_team_members = [row for row in result.teaching_team_members if row.offering_id in valid_offerings]


def import_wide_excel(config: WideImportConfig, *, base_dir: Path | str = ".") -> WideImportResult:
    """Normalize accepted development sources while preserving their original values."""
    staff = TableMapping(
        kind="staff", **config.staff.model_dump(), columns=STAFF_COLUMNS,
        value_maps={"staff_type": {key: value.value for key, value in config.staff_type_values.items()}},
    )
    preferences = TableMapping(
        kind="preferences", **config.preferences.model_dump(), columns={"staff_id": "Staff_ID"},
        preference_fields={key: {"column": key, "value_type": value, "required": key != "Special_Notes"}
                           for key, value in PREFERENCE_COLUMNS.items()},
    )
    base = import_excel(ImportConfig(tables=[staff, preferences]), base_dir=base_dir)
    result = WideImportResult(
        staff_members=base.staff_members, lecturer_preferences=base.lecturer_preferences,
        issues=base.issues, rows_read=base.rows_read,
    )
    # Generic parsing ignores these columns, but this adapter retains and checks them.
    result.issues = [
        ImportIssue(issue.severity, "source_metadata_retained", issue.workbook, issue.worksheet,
                    issue.row, issue.column, "Additional source columns are retained in staging, outside the existing contracts.")
        if issue.code == "unmapped_columns" else issue for issue in result.issues
    ]
    root = Path(base_dir)
    result.source_staff = _read_source(
        config.staff, set(STAFF_COLUMNS.values()) | {"Department_Source"}, result, root,
    )
    result.source_preferences = _read_source(
        config.preferences, {"Staff_ID", "Staff_Name", "Department", "Position"} | set(PREFERENCE_COLUMNS),
        result, root,
    )
    _check_staff_and_preferences(result)
    _modules(config, result, root)
    if any(module.missing_contract_fields for module in result.source_modules):
        result.issues.append(ImportIssue(
            "warning", "contract_conversion_blocked", config.modules.workbook.name,
            config.modules.worksheet, config.modules.header_row, None,
            "Only supplied fields are validated. Missing authoritative IDs/streams remain blockers; source-row keys are diagnostics only.",
        ))
    for source, code, column, message in (
        (config.staff, "source_provenance_unverified", "Data_Status",
         "Source staff status and department provenance are retained; roster, provisional affiliations, and demo instructors are not independently verified."),
        (config.modules, "session_requirements_not_inferred", "Lecture_Hours_Week",
         "Weekly hours and source formulas are retained; session durations, frequencies, and student groups are not inferred."),
    ):
        result.issues.append(ImportIssue("warning", code, source.workbook.name, source.worksheet,
                                        source.header_row, column, message))
    result.lecture_hours_interpretation = config.lecture_hours_interpretation
    if config.lecture_hours_interpretation:
        result.issues = [issue for issue in result.issues if issue.code != "session_requirements_not_inferred"]
        result.issues.append(ImportIssue("warning", "nonlecture_patterns_and_group_mapping_required",
            config.modules.workbook.name, config.modules.worksheet, config.modules.header_row, None,
            "Lecture hours mean one continuous weekly lecture; non-lecture patterns and offering/group mappings remain separate inputs."))
    if config.source_confirmation:
        matched = True
        for source in (config.staff, config.preferences, config.modules):
            path = source.workbook if source.workbook.is_absolute() else root / source.workbook
            try:
                actual = sha256(path.read_bytes()).hexdigest()
            except OSError:
                actual = None
            if actual is None or config.source_confirmation.workbook_sha256.get(source.workbook.name) != actual:
                matched = False
                result.issues.append(ImportIssue("error", "source_confirmation_checksum_mismatch",
                    source.workbook.name, source.worksheet, source.header_row, None,
                    "Source confirmation must match this exact workbook checksum; no source values are changed."))
        if matched:
            result.source_confirmation_reference = config.source_confirmation.reference
            result.issues = [issue for issue in result.issues
                             if issue.code not in {"development_data", "source_provenance_unverified"}]
    return result
