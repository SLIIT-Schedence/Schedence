"""Public Pydantic v2 contracts for teaching timetable inputs."""

from .enums import SessionType, StaffType, Stream, TeachingRole
from .inputs import TeachingTimetableInput
from .combinations import (
    DeliverySelection, GroupCombinationInput, GroupCombinationPolicy,
    GroupMembershipEvidence, GroupPopulationEvidence, PopulationRelationship,
    RequirementCombinationPermission,
)
from .venues import Venue, VenueType, VenueVerification
from .models import (
    ModuleOffering,
    SessionRequirement,
    StaffMember,
    StudentGroup,
    TeachingTeamMember,
)

__all__ = [
    "ModuleOffering",
    "SessionRequirement",
    "SessionType",
    "StaffMember",
    "StaffType",
    "Stream",
    "StudentGroup",
    "TeachingRole",
    "TeachingTeamMember",
    "TeachingTimetableInput",
    "Venue", "VenueType", "VenueVerification",
    "DeliverySelection", "GroupCombinationInput", "GroupCombinationPolicy",
    "GroupMembershipEvidence", "GroupPopulationEvidence", "PopulationRelationship",
    "RequirementCombinationPermission",
]
