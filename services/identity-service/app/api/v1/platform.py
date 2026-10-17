"""System Admin endpoints: the institution registry and each institution's Institution Admins.

Every route needs a ``platform:*`` permission, which only SYSTEM_ADMIN holds.
Institution users get 403 here, and a System Admin gets 403 on every
institution route (users, roles, audit logs, academic data).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.dependencies import PaginationDep, PlatformServiceDep, enforce_write_rate_limit, require_platform_permissions
from app.api.v1.mappers import audit_out, institution_out, provisioning_out, user_out
from app.core.principal import Principal
from app.schemas.common import ErrorResponse, Page
from app.schemas.platform import CreateInstitutionAdminIn, CreateInstitutionIn, InstitutionOut, UpdateInstitutionIn
from app.schemas.rbac import AuditLogOut
from app.schemas.users import ChangeOut, CreateUserOut, StatusChangeIn, UserOut

router = APIRouter(
    prefix="/platform",
    tags=["platform (System Admin)"],
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}, 404: {"model": ErrorResponse},
               422: {"model": ErrorResponse}},
)

CanCreateInstitutions = Annotated[Principal, Depends(require_platform_permissions("platform:institutions:create"))]
CanReadInstitutions = Annotated[Principal, Depends(require_platform_permissions("platform:institutions:read"))]
CanUpdateInstitutions = Annotated[Principal, Depends(require_platform_permissions("platform:institutions:update"))]
CanDisableInstitutions = Annotated[Principal, Depends(require_platform_permissions("platform:institutions:disable"))]
CanCreateAdmins = Annotated[Principal, Depends(require_platform_permissions("platform:institution_admins:create"))]
CanReadAdmins = Annotated[Principal, Depends(require_platform_permissions("platform:institution_admins:read"))]
CanRevokeAdmins = Annotated[Principal, Depends(require_platform_permissions("platform:institution_admins:revoke"))]
CanDisableAdmins = Annotated[Principal, Depends(require_platform_permissions("platform:institution_admins:disable"))]
CanReadAudit = Annotated[Principal, Depends(require_platform_permissions("platform:audit:read"))]
RATE_LIMITED = [Depends(enforce_write_rate_limit)]
PROVIDER_ERRORS = {409: {"model": ErrorResponse}, 502: {"model": ErrorResponse}, 503: {"model": ErrorResponse}}

# ---------------------------------------------------------------------------- institutions


@router.post("/institutions", status_code=status.HTTP_201_CREATED, response_model=InstitutionOut, dependencies=RATE_LIMITED,
             responses={409: {"model": ErrorResponse}}, summary="Register an institution")
async def create_institution(body: CreateInstitutionIn, principal: CanCreateInstitutions, platform: PlatformServiceDep) -> InstitutionOut:
    return institution_out(await platform.create_institution(principal, code=body.code, name=body.name))


@router.get("/institutions", response_model=Page[InstitutionOut], summary="List and search institutions")
async def list_institutions(
    principal: CanReadInstitutions,
    platform: PlatformServiceDep,
    pagination: PaginationDep,
    search: Annotated[str | None, Query(max_length=100, description="Matches code or name")] = None,
    institution_status: Annotated[Literal["ACTIVE", "DISABLED"] | None, Query(alias="status")] = None,
) -> Page[InstitutionOut]:
    institutions, total = await platform.list_institutions(principal, query=search, status=institution_status,
                                                           offset=pagination.offset, limit=pagination.page_size)
    return Page(items=[institution_out(i) for i in institutions], total=total, page=pagination.page, page_size=pagination.page_size)


@router.get("/institutions/{institution_id}", response_model=InstitutionOut, summary="View an institution")
async def get_institution(institution_id: UUID, principal: CanReadInstitutions, platform: PlatformServiceDep) -> InstitutionOut:
    return institution_out(await platform.get_institution(principal, institution_id))


@router.patch("/institutions/{institution_id}", response_model=InstitutionOut, dependencies=RATE_LIMITED,
              responses={409: {"model": ErrorResponse}}, summary="Rename an institution or change its code")
async def update_institution(institution_id: UUID, body: UpdateInstitutionIn, principal: CanUpdateInstitutions,
                             platform: PlatformServiceDep) -> InstitutionOut:
    return institution_out(await platform.update_institution(principal, institution_id, code=body.code, name=body.name))


@router.post("/institutions/{institution_id}/disable", response_model=InstitutionOut, dependencies=RATE_LIMITED,
             summary="Disable an institution (roles held in it stop granting permissions at once)")
async def disable_institution(institution_id: UUID, principal: CanDisableInstitutions, platform: PlatformServiceDep,
                              body: StatusChangeIn | None = None) -> InstitutionOut:
    return institution_out(await platform.set_institution_status(principal, institution_id, active=False,
                                                                 reason=body.reason if body else None))


@router.post("/institutions/{institution_id}/enable", response_model=InstitutionOut, dependencies=RATE_LIMITED,
             summary="Re-enable an institution")
async def enable_institution(institution_id: UUID, principal: CanDisableInstitutions, platform: PlatformServiceDep) -> InstitutionOut:
    return institution_out(await platform.set_institution_status(principal, institution_id, active=True))


# ---------------------------------------------------------------------------- institution admins


@router.get("/institutions/{institution_id}/admins", response_model=Page[UserOut], summary="List the institution's Institution Admins")
async def list_institution_admins(institution_id: UUID, principal: CanReadAdmins, platform: PlatformServiceDep,
                                  pagination: PaginationDep) -> Page[UserOut]:
    admins, total = await platform.list_institution_admins(principal, institution_id, offset=pagination.offset,
                                                           limit=pagination.page_size)
    return Page(items=[user_out(a) for a in admins], total=total, page=pagination.page, page_size=pagination.page_size)


@router.post("/institutions/{institution_id}/admins", status_code=status.HTTP_201_CREATED, response_model=CreateUserOut,
             dependencies=RATE_LIMITED, responses=PROVIDER_ERRORS,
             summary="Create an Institution Admin: Cognito invitation + local account + INSTITUTION_ADMIN role")
async def create_institution_admin(institution_id: UUID, body: CreateInstitutionAdminIn, principal: CanCreateAdmins,
                                   platform: PlatformServiceDep) -> CreateUserOut:
    """If Cognito or the database fails part-way, send the same request again: it resumes the same onboarding."""
    result = await platform.create_institution_admin(principal, institution_id, email=str(body.email), full_name=body.full_name)
    return CreateUserOut(user=user_out(result.detail), provisioning=provisioning_out(result.provisioning),
                         linked_existing_identity=result.linked_existing_identity)


@router.post("/institutions/{institution_id}/admins/{user_id}/resend-invitation", status_code=status.HTTP_204_NO_CONTENT,
             dependencies=RATE_LIMITED, responses=PROVIDER_ERRORS, summary="Resend an Institution Admin's Cognito invitation")
async def resend_admin_invitation(institution_id: UUID, user_id: UUID, principal: CanCreateAdmins, platform: PlatformServiceDep) -> None:
    await platform.resend_admin_invitation(principal, institution_id, user_id)


@router.delete("/institutions/{institution_id}/admins/{user_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=RATE_LIMITED,
               summary="Revoke the INSTITUTION_ADMIN role (the account and its other roles stay)")
async def revoke_institution_admin(institution_id: UUID, user_id: UUID, principal: CanRevokeAdmins, platform: PlatformServiceDep) -> None:
    await platform.revoke_institution_admin(principal, institution_id, user_id)


@router.post("/institutions/{institution_id}/admins/{user_id}/disable", response_model=ChangeOut, dependencies=RATE_LIMITED,
             responses=PROVIDER_ERRORS, summary="Disable an Institution Admin's account")
async def disable_institution_admin(institution_id: UUID, user_id: UUID, principal: CanDisableAdmins, platform: PlatformServiceDep,
                                    body: StatusChangeIn | None = None) -> ChangeOut:
    detail, sync = await platform.set_admin_account_status(principal, institution_id, user_id, active=False,
                                                           reason=body.reason if body else None)
    return ChangeOut(user=user_out(detail), cognito_sync=sync)


@router.post("/institutions/{institution_id}/admins/{user_id}/enable", response_model=ChangeOut, dependencies=RATE_LIMITED,
             responses=PROVIDER_ERRORS, summary="Re-enable an Institution Admin's account")
async def enable_institution_admin(institution_id: UUID, user_id: UUID, principal: CanDisableAdmins,
                                   platform: PlatformServiceDep) -> ChangeOut:
    detail, sync = await platform.set_admin_account_status(principal, institution_id, user_id, active=True)
    return ChangeOut(user=user_out(detail), cognito_sync=sync)


# ---------------------------------------------------------------------------- audit


@router.get("/audit-logs", response_model=Page[AuditLogOut], summary="Platform audit log (platform.* events, newest first)")
async def list_platform_audit_logs(
    principal: CanReadAudit,
    platform: PlatformServiceDep,
    pagination: PaginationDep,
    institution_id: UUID | None = None,
    action: Annotated[str | None, Query(max_length=100)] = None,
    actor_user_id: UUID | None = None,
    target_user_id: UUID | None = None,
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
) -> Page[AuditLogOut]:
    entries, total = await platform.list_audit_logs(
        principal, institution_id=institution_id, action=action, actor_user_id=actor_user_id, target_user_id=target_user_id,
        occurred_from=occurred_from, occurred_to=occurred_to, offset=pagination.offset, limit=pagination.page_size,
    )
    return Page(items=[audit_out(e) for e in entries], total=total, page=pagination.page, page_size=pagination.page_size)
