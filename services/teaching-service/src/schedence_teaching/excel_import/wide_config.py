"""Mappings for the inspected Department_Aligned workbook layout."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from schedence_teaching.schemas import StaffType
from schedence_teaching.schemas.models import InputContract, RequiredText, Semester

STAFF_COLUMNS = {
    "staff_id": "Staff_ID", "staff_name": "Staff_Name", "position": "Position",
    "department": "Department", "staff_type": "Data_Status",
}
MODULE_COLUMNS = {
    "module_code": "Module_Code", "module_name": "Module_Name", "academic_year": "Year",
    "semester": "Semester", "specialization": "Specialization", "degree_type": "Module_Degree_Type",
}
TEAM_COLUMNS = (
    ("LIC_Staff_ID", "LIC", "LIC"),
    ("Co_LIC_Staff_ID", "Co_LIC", "CO_LIC"),
    *((f"Team_Staff_ID_{i}", f"Teaching_Team_Member_{i}", "TEAM_MEMBER") for i in range(1, 6)),
)
PREFERENCE_COLUMNS = {
    "Expected_Working_Days": "integer", "Weekend_Work_Pattern": "text",
    "Preferred_Free_Day": "text", "Can_Work_Both_Weekend_Days": "text",
    **{f"{day}_{part}": "text" for day in (
        "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
    ) for part in ("Morning", "Afternoon")},
    "Special_Notes": "text",
}


class DatasetSheet(InputContract):
    workbook: Path
    worksheet: RequiredText
    header_row: Annotated[int, Field(strict=True, ge=1)]


class AcademicSourceConfirmation(InputContract):
    """User confirmation of source facts, separate from calendar/policy scope."""

    reference: RequiredText
    workbook_sha256: Annotated[dict[RequiredText,
        Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9a-f]{64}$")]], Field(min_length=1)]


class WideImportConfig(InputContract):
    """Classification and semester translations must be explicit source mappings."""

    layout: Literal["wide-teaching-v1"]
    staff: DatasetSheet
    preferences: DatasetSheet
    modules: DatasetSheet
    staff_type_values: Annotated[dict[RequiredText, StaffType], Field(min_length=1)]
    semester_values: Annotated[dict[RequiredText, Semester], Field(min_length=1)]
    # Only map these if authoritative source columns actually exist.
    offering_id_column: RequiredText | None = None
    stream_column: RequiredText | None = None
    source_confirmation: AcademicSourceConfirmation | None = None
    lecture_hours_interpretation: Literal["ONE_CONTINUOUS_WEEKLY_LECTURE"] | None = None
