"""Optional lecture-combination evidence alongside unchanged academic contracts."""

from enum import StrEnum
from typing import Annotated, Self

from pydantic import Field, StrictBool, model_validator

from .inputs import TeachingTimetableInput
from .models import InputContract, PositiveInteger, RequiredText


class PopulationRelationship(StrEnum):
    DISJOINT = "DISJOINT"
    OVERLAPPING = "OVERLAPPING"
    UNKNOWN = "UNKNOWN"


class GroupPopulationEvidence(InputContract):
    """A referenced disjoint-set assertion or an explicit overlap/unknown pair.

    DISJOINT asserts that every pair in group_ids is disjoint, e.g. a verified
    academic cohort partition. Overlap/unknown statements identify exactly two
    groups, avoiding guesses about which members of a larger set overlap.
    """

    group_ids: Annotated[tuple[RequiredText, ...], Field(min_length=2)]
    relationship: PopulationRelationship
    evidence_reference: RequiredText | None = None

    @model_validator(mode="after")
    def validate_evidence(self) -> Self:
        if len(self.group_ids) != len(set(self.group_ids)):
            raise ValueError("population evidence must contain distinct group IDs")
        if self.relationship != PopulationRelationship.DISJOINT and len(self.group_ids) != 2:
            raise ValueError("overlap/unknown evidence must identify exactly two groups")
        if self.relationship != PopulationRelationship.UNKNOWN and self.evidence_reference is None:
            raise ValueError("known population relationships require an authoritative evidence_reference")
        return self


class GroupMembershipEvidence(InputContract):
    """Supplied student membership; only complete rosters can prove disjointness.

    Student identifiers stay in the input snapshot, never in candidate reports.
    Even a partial roster can prove overlap when a shared student is present.
    """

    group_id: RequiredText
    student_ids: tuple[RequiredText, ...]
    complete: StrictBool
    evidence_reference: RequiredText

    @model_validator(mode="after")
    def unique_students(self) -> Self:
        if len(self.student_ids) != len(set(self.student_ids)):
            raise ValueError("membership evidence must not contain duplicate student IDs")
        return self


class GroupCombinationPolicy(InputContract):
    allow_combination: StrictBool = False
    max_groups: Annotated[int, Field(strict=True, ge=2)] | None = None
    max_combined_students: PositiveInteger | None = None


class RequirementCombinationPermission(InputContract):
    requirement_id: RequiredText
    # None inherits the global policy. Explicit false vetoes a global permission.
    allow_combination: StrictBool | None = None
    # Otherwise the offering ID is the content identity; module_code is never used.
    shared_content_reference: RequiredText | None = None


class GroupCombinationInput(InputContract):
    teaching_input: TeachingTimetableInput
    group_specializations: dict[RequiredText, RequiredText] = Field(default_factory=dict)
    policy: GroupCombinationPolicy = Field(default_factory=GroupCombinationPolicy)
    requirement_permissions: list[RequirementCombinationPermission] = Field(default_factory=list)
    population_evidence: list[GroupPopulationEvidence] = Field(default_factory=list)
    memberships: list[GroupMembershipEvidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def references_and_counts(self) -> Self:
        groups = {group.group_id: group for group in self.teaching_input.student_groups}
        requirements = {row.requirement_id for row in self.teaching_input.session_requirements}
        if set(self.group_specializations) - groups.keys():
            raise ValueError("unknown group_id in group_specializations")
        permission_ids = [row.requirement_id for row in self.requirement_permissions]
        if len(permission_ids) != len(set(permission_ids)):
            raise ValueError("duplicate requirement combination permission")
        if set(permission_ids) - requirements:
            raise ValueError("unknown requirement_id in combination permissions")
        membership_ids = [row.group_id for row in self.memberships]
        if len(membership_ids) != len(set(membership_ids)):
            raise ValueError("duplicate membership evidence for one group")
        for evidence in self.population_evidence:
            if set(evidence.group_ids) - groups.keys():
                raise ValueError("unknown group_id in population evidence")
        for membership in self.memberships:
            if membership.group_id not in groups:
                raise ValueError("unknown group_id in membership evidence")
            count = groups[membership.group_id].student_count
            if len(membership.student_ids) > count or (membership.complete and len(membership.student_ids) != count):
                raise ValueError("membership evidence count does not match the supplied student_count")
        return self


class DeliverySelection(InputContract):
    """Explicit hypothetical alternatives, not a solver decision or booking."""

    combined_occurrence_ids: tuple[RequiredText, ...] = ()
    separate_occurrence_ids: tuple[RequiredText, ...] = ()
