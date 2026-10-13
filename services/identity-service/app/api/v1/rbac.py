from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import DirectoryServiceDep, PaginationDep, require_permissions
from app.api.v1.mappers import audit_out
from app.core.principal import Principal
from app.schemas.common import ErrorResponse, Page
from app.schemas.rbac import AuditLogOut, PermissionOut, RoleOut

router = APIRouter(responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}})

CanReadRoles = Annotated[Principal, Depends(require_permissions("identity:roles:read"))]
CanReadPermissions = Annotated[Principal, Depends(require_permissions("identity:permissions:read"))]
CanReadAudit = Annotated[Principal, Depends(require_permissions("identity:audit:read"))]


@router.get("/roles", response_model=list[RoleOut], tags=["roles and permissions"], summary="System access roles")
async def list_roles(principal: CanReadRoles, directory: DirectoryServiceDep) -> list[RoleOut]:
    return [
        RoleOut(id=r.id, code=r.code, name=r.name, description=r.description, is_system_role=r.is_system_role,
                permissions=sorted(perms))
        for r, perms in await directory.list_roles()
    ]


@router.get("/permissions", response_model=list[PermissionOut], tags=["roles and permissions"], summary="All permissions")
async def list_permissions(principal: CanReadPermissions, directory: DirectoryServiceDep) -> list[PermissionOut]:
    return [PermissionOut.model_validate(p) for p in await directory.list_permissions()]


@router.get("/audit-logs", response_model=Page[AuditLogOut], tags=["audit"], summary="Identity audit log (newest first)")
async def list_audit_logs(
    principal: CanReadAudit,
    directory: DirectoryServiceDep,
    pagination: PaginationDep,
    institution_id: UUID | None = None,
    action: Annotated[str | None, Query(max_length=100)] = None,
    actor_user_id: UUID | None = None,
    target_user_id: UUID | None = None,
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
) -> Page[AuditLogOut]:
    entries, total = await directory.list_audit_logs(
        principal, institution_id=institution_id, action=action, actor_user_id=actor_user_id, target_user_id=target_user_id,
        occurred_from=occurred_from, occurred_to=occurred_to, offset=pagination.offset, limit=pagination.page_size,
    )
    return Page(items=[audit_out(e) for e in entries], total=total, page=pagination.page, page_size=pagination.page_size)
