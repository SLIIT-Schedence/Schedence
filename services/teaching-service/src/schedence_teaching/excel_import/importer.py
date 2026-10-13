"""Resolve references and duplicates in transient Excel input records."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from schedence_teaching.schemas import StaffType, TeachingRole

from .config import ImportConfig
from .reader import IDENTITY_FIELDS, SourceRow, read_table
from .records import ImportIssue, ImportResult


def _unique_rows(rows: list[SourceRow], result: ImportResult) -> list[SourceRow]:
    by_key: dict[tuple, list[SourceRow]] = defaultdict(list)
    for row in rows:
        if row.identity is not None:
            by_key[row.identity].append(row)
    for matches in by_key.values():
        if len(matches) > 1:
            for row in matches:
                row.issue(result, "duplicate_record", IDENTITY_FIELDS[row.table.kind][0],
                          "Duplicate record identity; all occurrences are excluded to avoid ambiguous data.")
    return [row for row in rows if row.record is not None and len(by_key[row.identity]) == 1]


def import_excel(config: ImportConfig, *, base_dir: Path | str = ".") -> ImportResult:
    """Validate a set of mapped tables without modifying files or storing records."""
    result = ImportResult()
    result.issues.append(ImportIssue(
        "warning", "development_data", "<import>", None, None, None,
        "Imported records and proposed teaching allocations are unverified development data.",
    ))
    tables: dict[str, list[SourceRow]] = defaultdict(list)
    for table in config.tables:
        tables[table.kind].extend(read_table(table, Path(base_dir), result))
    for kind in tuple(tables):
        tables[kind] = _unique_rows(tables[kind], result)

    staff_by_id = {row.record.staff_id: row.record for row in tables["staff"]}
    offering_by_id = {row.record.offering_id: row.record for row in tables["offerings"]}
    result.staff_members = list(staff_by_id.values())
    result.module_offerings = list(offering_by_id.values())

    preference_staff = set()
    for row in tables["preferences"]:
        staff = staff_by_id.get(row.record.staff_id)
        if staff is None:
            row.issue(result, "unknown_staff", "staff_id", "Staff ID does not resolve to a valid unique Staff Master record.")
        elif staff.staff_type != StaffType.ACADEMIC:
            row.issue(result, "instructor_preference", "staff_id", "Lecturer preference records must refer to ACADEMIC staff.")
        else:
            result.lecturer_preferences.append(row.record)
            preference_staff.add(staff.staff_id)
    if any(table.kind == "preferences" for table in config.tables):
        for row in tables["staff"]:
            if row.record.staff_type == StaffType.ACADEMIC and row.record.staff_id not in preference_staff:
                row.issue(result, "missing_lecturer_preference", "staff_id",
                          "No valid preference record supplied for this academic; no preference is inferred.",
                          severity="warning")

    for row in tables["teaching_teams"]:
        member = row.record
        staff = staff_by_id.get(member.staff_id)
        valid = True
        if staff is None:
            row.issue(result, "unknown_staff", "staff_id", "Staff ID does not resolve to a valid unique Staff Master record.")
            valid = False
        if member.offering_id not in offering_by_id:
            row.issue(result, "unknown_offering", "offering_id", "Offering ID does not resolve to a valid unique offering record.")
            valid = False
        if staff is not None and member.role in {TeachingRole.LIC, TeachingRole.CO_LIC} and staff.staff_type != StaffType.ACADEMIC:
            row.issue(result, "invalid_role_assignment", "role", "LIC and CO_LIC roles require ACADEMIC staff.")
            valid = False
        if valid:
            result.teaching_team_members.append(member)

    by_module_code: dict[str, list[SourceRow]] = defaultdict(list)
    for row in tables["offerings"]:
        by_module_code[row.record.module_code].append(row)
    for matches in by_module_code.values():
        if len(matches) > 1:
            for row in matches:
                row.issue(result, "shared_module_code", "module_code",
                          "Module code is shared by distinct offering IDs; all distinct offerings are preserved.",
                          severity="warning")
    return result
