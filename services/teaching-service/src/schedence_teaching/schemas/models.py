"""Teaching input records supplied by the Academic Data Service."""

from __future__ import annotations

from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .enums import SessionType, StaffType, Stream, TeachingRole

# Preserve supplied text, especially authoritative IDs; reject blank values.
RequiredText = Annotated[str, StringConstraints(strict=True, min_length=1, pattern=r"\S")]
AcademicYear = Annotated[int, Field(strict=True, ge=1, le=4)]
Semester = Annotated[int, Field(strict=True, ge=1, le=2)]
PositiveInteger = Annotated[int, Field(strict=True, gt=0)]


class InputContract(BaseModel):
    """Reject unknown fields so misspelled contract fields are visible."""

    model_config = ConfigDict(extra="forbid")


class StaffMember(InputContract):
    """An existing staff record; identifiers are never generated or replaced."""

    staff_id: RequiredText
    staff_name: RequiredText
    position: RequiredText
    staff_type: StaffType
    department: RequiredText | None = None


class ModuleOffering(InputContract):
    """An offering identified by offering_id, independently of module_code."""

    offering_id: RequiredText
    module_code: RequiredText
    module_name: RequiredText
    academic_year: AcademicYear
    semester: Semester
    specialization: RequiredText
    degree_type: RequiredText
    stream: Stream


class TeachingTeamMember(InputContract):
    """Candidate eligibility, rather than a confirmed session assignment.

    TeachingTimetableInput checks LIC/Co-LIC eligibility using staff records;
    this individual record contains only references and a role.
    """

    offering_id: RequiredText
    staff_id: RequiredText
    role: TeachingRole


class StudentGroup(InputContract):
    """An offering-independent main group or subgroup of a supplied size."""

    group_id: RequiredText
    academic_year: AcademicYear
    semester: Semester
    stream: Stream
    student_count: PositiveInteger
    parent_group_id: RequiredText | None = None

    @model_validator(mode="after")
    def reject_self_parent(self) -> Self:
        if self.parent_group_id == self.group_id:
            raise ValueError("a student group cannot be its own parent")
        return self


class SessionRequirement(InputContract):
    """Explicit weekly requirements; no hours or counts are inferred."""

    requirement_id: RequiredText
    offering_id: RequiredText
    group_id: RequiredText
    session_type: SessionType
    duration_minutes: PositiveInteger
    sessions_per_week: PositiveInteger
