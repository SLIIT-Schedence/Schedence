from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from app.schemas.common import ApiModel
from app.schemas.platform import InstitutionSummaryOut
from app.schemas.users import RoleAssignmentOut


class RoleOut(ApiModel):
    id: UUID
    code: str
    name: str
    description: str
    is_system_role: bool
    permissions: list[str]


class PermissionOut(ApiModel):
    id: UUID
    code: str
    description: str


class InstitutionPermissionsOut(ApiModel):
    institution_id: UUID
    roles: list[str]
    permissions: list[str]


class PlatformPermissionsOut(ApiModel):
    roles: list[str]
    permissions: list[str]


class MePermissionsOut(ApiModel):
    """Contract consumed by other Schedence services to authorise a caller.

    Services authorise from ``institutions`` only. ``platform`` lists System Admin
    permissions, which grant nothing inside an institution.
    """

    user_id: UUID
    cognito_sub: str
    account_status: str
    institutions: list[InstitutionPermissionsOut]
    platform: PlatformPermissionsOut


class MeOut(ApiModel):
    id: UUID
    email: str
    full_name: str
    account_status: str
    roles: list[RoleAssignmentOut]
    platform_roles: list[str]
    institutions: list[InstitutionSummaryOut]


class AuditLogOut(ApiModel):
    id: UUID
    actor_user_id: UUID | None
    actor_type: str
    action: str
    target_user_id: UUID | None
    institution_id: UUID | None
    metadata: dict[str, Any]
    request_id: str | None
    occurred_at: datetime
