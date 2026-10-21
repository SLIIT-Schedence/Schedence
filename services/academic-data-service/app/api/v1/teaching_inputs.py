from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import CurrentUser, TeachingInputServiceDep, require_permissions
from app.core.principal import Principal
from app.schemas.academic import ErrorResponse
from app.services.common import PERM_STAFF_READ

router = APIRouter(tags=["teaching integration"], responses={
    401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 503: {"model": ErrorResponse},
})

CanReadStaff = Annotated[Principal, Depends(require_permissions(PERM_STAFF_READ))]


@router.get("/teaching-inputs", summary="Complete generation-input export for the Teaching service (read-only)")
async def teaching_inputs(principal: CanReadStaff, service: TeachingInputServiceDep, institution_id: UUID,
                          semester: Annotated[int | None, Query(ge=1, le=3)] = None) -> dict:
    # Every table is exported whole; scope/semester selection stays Teaching's responsibility.
    return await service.generation_inputs(principal, institution_id)


@router.get("/form-recipients", summary="Eligible lecturers and their account-link status, for Teaching forms")
async def form_recipients(principal: CanReadStaff, service: TeachingInputServiceDep, institution_id: UUID) -> dict:
    return await service.form_recipients(principal, institution_id)


@router.get("/form-recipients/me", summary="The caller's own eligible-lecturer record (zero or one)")
async def my_form_recipient(principal: CurrentUser, service: TeachingInputServiceDep, institution_id: UUID) -> dict:
    return await service.my_recipient_link(principal, institution_id)
