"""Read-only views: current user's principal, roles/permissions catalogue, and audit logs."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.core.errors import PermissionDeniedError
from app.core.principal import Principal
from app.models import IdentityAuditLog, Permission, Role
from app.rbac.catalog import PLATFORM_PERMISSION_PREFIX, PLATFORM_ROLE_CODES
from app.repositories.base import AuditQuery, IdentityUnitOfWork

PERM_AUDIT_READ = "identity:audit:read"


class DirectoryService:
    def __init__(self, uow: IdentityUnitOfWork) -> None:
        self.uow = uow

    async def list_roles(self) -> list[tuple[Role, frozenset[str]]]:
        """Roles that can be granted within an institution (platform roles are left out)."""
        grants = await self.uow.roles.role_permission_codes()
        return [(role, grants.get(role.code, frozenset())) for role in await self.uow.roles.list_roles()
                if role.code not in PLATFORM_ROLE_CODES]

    async def list_permissions(self) -> list[Permission]:
        """Permissions that institution roles can carry (``platform:*`` permissions are left out)."""
        return [p for p in await self.uow.roles.list_permissions() if not p.code.startswith(PLATFORM_PERMISSION_PREFIX)]

    async def list_audit_logs(
        self,
        actor: Principal,
        *,
        institution_id: UUID | None,
        action: str | None,
        actor_user_id: UUID | None,
        target_user_id: UUID | None,
        occurred_from: datetime | None,
        occurred_to: datetime | None,
        offset: int,
        limit: int,
    ) -> tuple[list[IdentityAuditLog], int]:
        institutions = actor.institutions_with(PERM_AUDIT_READ)
        if institution_id is not None:
            institutions = institutions & {institution_id}
        if not institutions:
            raise PermissionDeniedError(details={"required_permission": PERM_AUDIT_READ})
        return await self.uow.audit.list(
            AuditQuery(frozenset(institutions), action, actor_user_id, target_user_id, occurred_from, occurred_to, offset, limit)
        )
