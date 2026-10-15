"""Create new CSV review copies; never edit input workbooks or existing packs."""

import csv
from pathlib import Path

from .specification import OFFERING_FIELDS, POLICY_KEYS, SOURCE_COLUMNS, TABLES, headers


def write_collection_pack(directory: Path, *, academic_source=None, venue_source=None):
    """Header-only templates or private known-value prefill; unknowns stay blank.

    Prefilled files contain private development records and belong in ignored
    local-data. This library never marks those records confirmed automatically.
    """
    directory = Path(directory)
    if directory.exists() and any(directory.iterdir()):
        raise FileExistsError("Use a new or empty collection directory; existing files are never overwritten.")
    if academic_source is not None and not academic_source.valid:
        raise ValueError("Academic prefill requires a valid source import.")
    if venue_source is not None and not venue_source.valid:
        raise ValueError("Venue prefill requires a valid source import.")
    directory.mkdir(parents=True, exist_ok=True)
    rows = {key: [] for key in TABLES}
    if academic_source is not None:
        for record in academic_source.source_modules:
            known = {field.header: record.contract_fields[field.field] for field in OFFERING_FIELDS
                     if field.field in record.contract_fields}
            known.update({"Source Workbook": record.location.workbook, "Source Worksheet": record.location.worksheet,
                          "Source Row": record.location.row})
            rows["offerings"].append(known)
        for staff in academic_source.staff_members:
            rows["staff-crosswalk"].append({"Staff ID": staff.staff_id})
            rows["staff-availability"].append({"Staff ID": staff.staff_id})
        if academic_source.source_preferences:
            from schedence_teaching.excel_import.preference_adapter import map_lecturer_preferences
            from schedence_teaching.excel_import.preference_config import LecturerPreferenceMapping
            preferences = map_lecturer_preferences(academic_source, LecturerPreferenceMapping(
                layout="department-aligned-preferences-v1", source_reference="collection:retained-source-preferences"))
            rows["preference-approvals"] = [{"Preference ID": row.preference_id, "Staff ID": row.staff_id}
                                          for row in preferences.candidates]
    if venue_source is not None:
        rows["venue-calendars"] = [{"Venue ID": venue.venue_id} for venue in venue_source.venues]
    rows["policy-confirmations"] = [{"Policy Key": key} for key in POLICY_KEYS]
    for table, records in rows.items():
        with (directory / f"{table}.csv").open("x", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=headers(table), lineterminator="\n")
            writer.writeheader()
            writer.writerows(records)
    with (directory / "data-dictionary.csv").open("x", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["Template", "Column", "Contract Field", "Format", "Requirement", "Explanation"])
        for table, fields in TABLES.items():
            for field in fields + SOURCE_COLUMNS:
                writer.writerow([f"{table}.csv", field.header, field.field, field.format, field.requirement, field.explanation])
    return {key: len(records) for key, records in rows.items()}
