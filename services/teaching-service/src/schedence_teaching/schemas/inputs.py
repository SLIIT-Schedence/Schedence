"""Validation of references within a supplied teaching input snapshot."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Self

from pydantic import model_validator

from .enums import StaffType, TeachingRole
from .models import (
    InputContract,
    ModuleOffering,
    SessionRequirement,
    StaffMember,
    StudentGroup,
    TeachingTeamMember,
)


def _require_unique_ids(ids: Sequence[str], field_name: str) -> None:
    seen: set[str] = set()
    for identifier in ids:
        if identifier in seen:
            raise ValueError(f"duplicate {field_name}: {identifier}")
        seen.add(identifier)


class TeachingTimetableInput(InputContract):
    """A complete set of referenced input records, with no storage or assignments.

    Individual schemas validate fields; this optional wrapper checks relationships
    that require multiple records. Empty collections are allowed, but every
    referenced record must be supplied when validating a snapshot.
    """

    staff_members: list[StaffMember]
    module_offerings: list[ModuleOffering]
    teaching_team_members: list[TeachingTeamMember]
    student_groups: list[StudentGroup]
    session_requirements: list[SessionRequirement]

    @model_validator(mode="after")
    def validate_relationships(self) -> Self:
        _require_unique_ids([staff.staff_id for staff in self.staff_members], "staff_id")
        _require_unique_ids(
            [offering.offering_id for offering in self.module_offerings], "offering_id"
        )
        _require_unique_ids([group.group_id for group in self.student_groups], "group_id")
        _require_unique_ids(
            [requirement.requirement_id for requirement in self.session_requirements],
            "requirement_id",
        )

        staff_by_id = {staff.staff_id: staff for staff in self.staff_members}
        offering_ids = {offering.offering_id for offering in self.module_offerings}
        groups_by_id = {group.group_id: group for group in self.student_groups}

        for member in self.teaching_team_members:
            if member.offering_id not in offering_ids:
                raise ValueError(f"unknown offering_id in teaching team: {member.offering_id}")
            if member.staff_id not in staff_by_id:
                raise ValueError(f"unknown staff_id in teaching team: {member.staff_id}")
            if (
                member.role in (TeachingRole.LIC, TeachingRole.CO_LIC)
                and staff_by_id[member.staff_id].staff_type != StaffType.ACADEMIC
            ):
                raise ValueError("LIC and CO_LIC roles require ACADEMIC staff")

        for group in self.student_groups:
            if group.parent_group_id is not None and group.parent_group_id not in groups_by_id:
                raise ValueError(f"unknown parent_group_id: {group.parent_group_id}")

        # Follow each parent chain once; reject cycles without limiting group sizes.
        checked: set[str] = set()
        for group in self.student_groups:
            chain: set[str] = set()
            current_id: str | None = group.group_id
            while current_id is not None and current_id not in checked:
                if current_id in chain:
                    raise ValueError("student group parent references contain a cycle")
                chain.add(current_id)
                current_id = groups_by_id[current_id].parent_group_id
            checked.update(chain)

        for requirement in self.session_requirements:
            if requirement.offering_id not in offering_ids:
                raise ValueError(
                    f"unknown offering_id in session requirement: {requirement.offering_id}"
                )
            if requirement.group_id not in groups_by_id:
                raise ValueError(
                    f"unknown group_id in session requirement: {requirement.group_id}"
                )

        return self
