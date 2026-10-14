from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.dependencies import PaginationDep, UserServiceDep, enforce_write_rate_limit, require_permissions
from app.api.v1.mappers import provisioning_out, user_out
from app.core.principal import Principal
from app.schemas.common import ErrorResponse, Page
from app.schemas.users import (
    AccountStatusOut,
    ChangeOut,
    CreateUserIn,
    CreateUserOut,
    ProvisioningOut,
    SetRolesIn,
    StatusChangeIn,
    UpdateUserIn,
    UserOut,
)

router = APIRouter(
    prefix="/users",
    tags=["users"],
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}, 404: {"model": ErrorResponse},
               422: {"model": ErrorResponse}},
)

CanCreate = Annotated[Principal, Depends(require_permissions("identity:users:create"))]
CanRead = Annotated[Principal, Depends(require_permissions("identity:users:read"))]
CanUpdate = Annotated[Principal, Depends(require_permissions("identity:users:update"))]
CanDisable = Annotated[Principal, Depends(require_permissions("identity:users:disable"))]
CanAssign = Annotated[Principal, Depends(require_permissions("identity:roles:assign"))]
RATE_LIMITED = [Depends(enforce_write_rate_limit)]
PROVIDER_ERRORS = {409: {"model": ErrorResponse}, 502: {"model": ErrorResponse}, 503: {"model": ErrorResponse}}


@router.post("", status_code=status.HTTP_201_CREATED, response_model=CreateUserOut, dependencies=RATE_LIMITED,
             responses=PROVIDER_ERRORS, summary="Create a user: Cognito invitation + local account + initial role")
async def create_user(body: CreateUserIn, principal: CanCreate, users: UserServiceDep) -> CreateUserOut:
    result = await users.create_user(principal, email=str(body.email), full_name=body.full_name, role_code=body.role_code,
                                     institution_id=body.institution_id)
    return CreateUserOut(user=user_out(result.detail), provisioning=provisioning_out(result.provisioning),
                         linked_existing_identity=result.linked_existing_identity)


@router.get("", response_model=Page[UserOut], summary="List and search users in your institutions")
async def list_users(
    principal: CanRead,
    users: UserServiceDep,
    pagination: PaginationDep,
    search: Annotated[str | None, Query(max_length=100, description="Matches e-mail or full name")] = None,
    account_status: Annotated[Literal["ACTIVE", "DISABLED"] | None, Query(alias="status")] = None,
    role_code: Annotated[str | None, Query(max_length=50)] = None,
) -> Page[UserOut]:
    details, total = await users.list_users(principal, query=search, status=account_status,
                                            role_code=role_code.upper() if role_code else None,
                                            offset=pagination.offset, limit=pagination.page_size)
    return Page(items=[user_out(d) for d in details], total=total, page=pagination.page, page_size=pagination.page_size)


@router.get("/{user_id}", response_model=UserOut, summary="View a user")
async def get_user(user_id: UUID, principal: CanRead, users: UserServiceDep) -> UserOut:
    return user_out(await users.get_user(principal, user_id))


@router.patch("/{user_id}", response_model=ChangeOut, dependencies=RATE_LIMITED, summary="Update permitted profile fields")
async def update_user(user_id: UUID, body: UpdateUserIn, principal: CanUpdate, users: UserServiceDep) -> ChangeOut:
    result = await users.update_user(principal, user_id, full_name=body.full_name)
    return ChangeOut(user=user_out(result.detail), cognito_sync=result.cognito_sync)


@router.post("/{user_id}/disable", response_model=ChangeOut, dependencies=RATE_LIMITED, responses=PROVIDER_ERRORS,
             summary="Disable a user (blocks API access immediately, then sign-in in Cognito)")
async def disable_user(user_id: UUID, principal: CanDisable, users: UserServiceDep, body: StatusChangeIn | None = None) -> ChangeOut:
    result = await users.disable_user(principal, user_id, reason=body.reason if body else None)
    return ChangeOut(user=user_out(result.detail), cognito_sync=result.cognito_sync)


@router.post("/{user_id}/enable", response_model=ChangeOut, dependencies=RATE_LIMITED, responses=PROVIDER_ERRORS,
             summary="Re-enable a user")
async def enable_user(user_id: UUID, principal: CanDisable, users: UserServiceDep) -> ChangeOut:
    result = await users.enable_user(principal, user_id)
    return ChangeOut(user=user_out(result.detail), cognito_sync=result.cognito_sync)


@router.get("/{user_id}/status", response_model=AccountStatusOut, summary="Local and Cognito account status")
async def get_account_status(user_id: UUID, principal: CanRead, users: UserServiceDep) -> AccountStatusOut:
    s = await users.get_account_status(principal, user_id)
    return AccountStatusOut(user_id=s.user.id, account_status=s.user.account_status, cognito_available=s.cognito_available,
                            cognito_status=s.cognito_status, cognito_enabled=s.cognito_enabled,
                            cognito_email_verified=s.cognito_email_verified)


@router.get("/{user_id}/provisioning", response_model=list[ProvisioningOut], summary="Provisioning history of a user")
async def get_provisioning_history(user_id: UUID, principal: CanRead, users: UserServiceDep) -> list[ProvisioningOut]:
    return [provisioning_out(r) for r in await users.provisioning_history(principal, user_id)]


@router.post("/{user_id}/resend-invitation", status_code=status.HTTP_204_NO_CONTENT, dependencies=RATE_LIMITED,
             responses=PROVIDER_ERRORS, summary="Resend the Cognito invitation (new temporary password)")
async def resend_invitation(user_id: UUID, principal: CanCreate, users: UserServiceDep) -> None:
    await users.resend_invitation(principal, user_id)


@router.put("/{user_id}/roles", response_model=UserOut, dependencies=RATE_LIMITED,
            responses={409: {"model": ErrorResponse}},
            summary="Replace the user's system roles within one institution")
async def set_roles(user_id: UUID, body: SetRolesIn, principal: CanAssign, users: UserServiceDep) -> UserOut:
    return user_out(await users.set_roles(principal, user_id, institution_id=body.institution_id, role_codes=body.role_codes))
