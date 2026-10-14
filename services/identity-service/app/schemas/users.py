from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import ConfigDict, EmailStr, Field, field_validator

from app.schemas.common import ApiModel, RequestModel

ROLE_CODES_HELP = ("A system role code (not an ID): SUPER_ADMIN, ACADEMIC_ADMIN, EXAMINATION_ADMIN, VENUE_MANAGER, LECTURER "
                   "or STUDENT. GET /api/v1/identity/roles lists them. Roles are built in; there is no endpoint to create them.")
INSTITUTION_HELP = "Your institution's ID (for SLIIT: the SLIIT_INSTITUTION_ID value you created at bootstrap)."
EXAMPLE_INSTITUTION = "00000000-0000-4000-8000-000000000000"
RoleCode = Field(min_length=2, max_length=50, pattern=r"^[A-Za-z][A-Za-z0-9_]*$", description=ROLE_CODES_HELP)


class RoleAssignmentOut(ApiModel):
    role_code: str
    role_name: str
    institution_id: UUID
    assigned_at: datetime
    assigned_by_user_id: UUID | None


class UserOut(ApiModel):
    id: UUID
    email: str
    full_name: str
    account_status: Literal["ACTIVE", "DISABLED"]
    created_at: datetime
    updated_at: datetime
    roles: list[RoleAssignmentOut]


class ProvisioningOut(ApiModel):
    id: UUID
    email: str
    full_name: str
    institution_id: UUID
    status: str
    error_category: str | None
    attempt_count: int
    user_id: UUID | None
    requested_by_user_id: UUID | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class CreateUserIn(RequestModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "email": "lecturer1@example.edu",
        "full_name": "Example Lecturer",
        "role_code": "LECTURER",
        "institution_id": EXAMPLE_INSTITUTION,
    }]})

    email: EmailStr = Field(max_length=320, description="Becomes the person's sign-in e-mail; Cognito sends the invitation here.")
    full_name: str = Field(min_length=1, max_length=200)
    role_code: str = RoleCode
    institution_id: UUID = Field(description=INSTITUTION_HELP)


class CreateUserOut(ApiModel):
    user: UserOut
    provisioning: ProvisioningOut
    linked_existing_identity: bool


class UpdateUserIn(RequestModel):
    """Only profile fields may change here. E-mail (the sign-in identifier), status and roles have dedicated flows."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"full_name": "Dr. Example Lecturer"}]})

    full_name: str = Field(min_length=1, max_length=200)


class StatusChangeIn(RequestModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"reason": "Left the university"}]})

    reason: str | None = Field(default=None, max_length=500)


class ChangeOut(ApiModel):
    user: UserOut
    cognito_sync: Literal["ok", "failed", "not_required"]


class SetRolesIn(RequestModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "institution_id": EXAMPLE_INSTITUTION,
        "role_codes": ["LECTURER", "ACADEMIC_ADMIN"],
    }]})

    institution_id: UUID = Field(description=INSTITUTION_HELP)
    role_codes: list[str] = Field(max_length=10, description="The complete set of roles the user should hold in this institution "
                                                             "(roles not listed are revoked). " + ROLE_CODES_HELP)

    @field_validator("role_codes")
    @classmethod
    def _unique(cls, value: list[str]) -> list[str]:
        cleaned = [v.strip().upper() for v in value]
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("role codes must be unique")
        return cleaned


class AccountStatusOut(ApiModel):
    user_id: UUID
    account_status: Literal["ACTIVE", "DISABLED"]
    cognito_available: bool
    cognito_status: str | None
    cognito_enabled: bool | None
    cognito_email_verified: bool | None
