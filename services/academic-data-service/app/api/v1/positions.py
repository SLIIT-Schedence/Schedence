from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.api.dependencies import PaginationDep, PositionServiceDep, enforce_write_rate_limit, require_permissions
from app.api.v1.mappers import policy_out, position_detail_out, position_out
from app.core.principal import Principal
from app.schemas.academic import ErrorResponse, Page, PolicyFields, PolicyOut, PositionCreateIn, PositionDetailOut, PositionOut, PositionUpdateIn
from app.services.positions import PolicyInput

router = APIRouter(prefix="/positions", tags=["administrative positions"], responses={
    401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
})

CanCreate = Annotated[Principal, Depends(require_permissions("academic:positions:create"))]
CanRead = Annotated[Principal, Depends(require_permissions("academic:positions:read"))]
CanUpdate = Annotated[Principal, Depends(require_permissions("academic:positions:update"))]
CanDisable = Annotated[Principal, Depends(require_permissions("academic:positions:disable"))]
RATE_LIMITED = [Depends(enforce_write_rate_limit)]


def _policy(body: PolicyFields) -> PolicyInput:
    return PolicyInput(body.admin_hours_per_week, body.combination_rule, body.cap_hours_per_week, body.policy_reference, body.notes,
                       body.effective_from)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=PositionDetailOut, dependencies=RATE_LIMITED,
             summary="Create a position type with its first administrative-hour policy")
async def create_position(body: PositionCreateIn, principal: CanCreate, positions: PositionServiceDep) -> PositionDetailOut:
    view = await positions.create(principal, institution_id=body.institution_id, code=body.code, name=body.name,
                                  description=body.description, unit_type=body.unit_type,
                                  is_exclusive_per_unit=body.is_exclusive_per_unit, policy=_policy(body))
    return position_detail_out(view)


@router.get("", response_model=Page[PositionOut], summary="List position types")
async def list_positions(principal: CanRead, positions: PositionServiceDep, pagination: PaginationDep,
                         institution_id: UUID | None = None, include_inactive: bool = False) -> Page[PositionOut]:
    views, total = await positions.list(principal, institution_id=institution_id, include_inactive=include_inactive,
                                        offset=pagination.offset, limit=pagination.page_size)
    return Page(items=[position_out(v) for v in views], total=total, page=pagination.page, page_size=pagination.page_size)


@router.get("/{position_id}", response_model=PositionDetailOut, summary="Position details with policy history")
async def get_position(position_id: UUID, principal: CanRead, positions: PositionServiceDep) -> PositionDetailOut:
    return position_detail_out(await positions.get(principal, position_id))


@router.patch("/{position_id}", response_model=PositionDetailOut, dependencies=RATE_LIMITED, summary="Update a position definition")
async def update_position(position_id: UUID, body: PositionUpdateIn, principal: CanUpdate, positions: PositionServiceDep) -> PositionDetailOut:
    changes = body.model_dump(exclude_unset=True)
    for key in ("name", "description", "is_exclusive_per_unit"):
        if key in changes and changes[key] is None:
            changes.pop(key)
    return position_detail_out(await positions.update(principal, position_id, changes))


@router.post("/{position_id}/disable", response_model=PositionDetailOut, dependencies=RATE_LIMITED,
             summary="Disable an unused position type")
async def disable_position(position_id: UUID, principal: CanDisable, positions: PositionServiceDep) -> PositionDetailOut:
    return position_detail_out(await positions.set_active(principal, position_id, False))


@router.post("/{position_id}/enable", response_model=PositionDetailOut, dependencies=RATE_LIMITED, summary="Re-enable a position type")
async def enable_position(position_id: UUID, principal: CanDisable, positions: PositionServiceDep) -> PositionDetailOut:
    return position_detail_out(await positions.set_active(principal, position_id, True))


@router.post("/{position_id}/workload-policies", status_code=status.HTTP_201_CREATED, response_model=PositionDetailOut,
             dependencies=RATE_LIMITED, summary="Configure administrative hours (adds a new policy version)")
async def add_workload_policy(position_id: UUID, body: PolicyFields, principal: CanUpdate, positions: PositionServiceDep) -> PositionDetailOut:
    return position_detail_out(await positions.add_policy_version(principal, position_id, _policy(body)))


@router.get("/{position_id}/workload-policies", response_model=list[PolicyOut], summary="Administrative-hour policy history")
async def list_workload_policies(position_id: UUID, principal: CanRead, positions: PositionServiceDep) -> list[PolicyOut]:
    return [policy_out(p) for p in (await positions.get(principal, position_id)).policies]
