"""Read-only views: current user's principal, roles/permissions catalogue, and audit logs."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.core.errors import PermissionDeniedError
from app.core.principal import Principal
from app.models import IdentityAuditLog, Permission, Role
from app.repositories.base import AuditQuery, IdentityUnitOfWork

PERM_AUDIT_READ = "identity:audit:read"


class DirectoryService:
    def __init__(self, uow: IdentityUnitOfWork) -> None:
        self.uow = uow

    async def list_roles(self) -> list[tuple[Role, frozenset[str]]]:
        grants = await self.uow.roles.role_permission_codes()
        return [(role, grants.get(role.code, frozenset())) for role in await self.uow.roles.list_roles()]

    async def list_permissions(self) -> list[Permission]:
        return await self.uow.roles.list_permissions()

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
