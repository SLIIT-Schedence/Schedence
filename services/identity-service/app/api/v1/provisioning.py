from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import PaginationDep, UserServiceDep, enforce_write_rate_limit, require_permissions
from app.api.v1.mappers import provisioning_out, user_out
from app.core.principal import Principal
from app.schemas.common import ErrorResponse, Page
from app.schemas.users import CreateUserOut, ProvisioningOut

router = APIRouter(prefix="/provisioning", tags=["provisioning"],
                   responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}})

CanCreate = Annotated[Principal, Depends(require_permissions("identity:users:create"))]
Status = Literal["PENDING", "COGNITO_CREATED", "FAILED", "COMPLETED", "CANCELLED"]


@router.get("", response_model=Page[ProvisioningOut], summary="List onboarding records (e.g. failed ones to retry)")
async def list_provisioning(principal: CanCreate, users: UserServiceDep, pagination: PaginationDep,
                            status: Annotated[list[Status] | None, Query()] = None) -> Page[ProvisioningOut]:
    records, total = await users.list_provisioning(principal, statuses=frozenset(status or ()), offset=pagination.offset,
                                                   limit=pagination.page_size)
    return Page(items=[provisioning_out(r) for r in records], total=total, page=pagination.page, page_size=pagination.page_size)


@router.post("/{provisioning_id}/retry", response_model=CreateUserOut, dependencies=[Depends(enforce_write_rate_limit)],
             responses={409: {"model": ErrorResponse}, 502: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
             summary="Retry a pending or failed onboarding (idempotent)")
async def retry_provisioning(provisioning_id: UUID, principal: CanCreate, users: UserServiceDep) -> CreateUserOut:
    result = await users.retry_provisioning(principal, provisioning_id)
    return CreateUserOut(user=user_out(result.detail), provisioning=provisioning_out(result.provisioning),
                         linked_existing_identity=result.linked_existing_identity)


@router.post("/{provisioning_id}/cancel", response_model=ProvisioningOut, dependencies=[Depends(enforce_write_rate_limit)],
             responses={409: {"model": ErrorResponse}}, summary="Cancel an onboarding that never created a Cognito identity")
async def cancel_provisioning(provisioning_id: UUID, principal: CanCreate, users: UserServiceDep) -> ProvisioningOut:
    return provisioning_out(await users.cancel_provisioning(principal, provisioning_id))
