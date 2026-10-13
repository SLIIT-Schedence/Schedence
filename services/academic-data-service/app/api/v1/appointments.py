from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.dependencies import AppointmentServiceDep, enforce_write_rate_limit, require_permissions
from app.api.v1.mappers import appointment_detail_out, appointment_out
from app.core.principal import Principal
from app.schemas.academic import AppointmentDetailOut, AppointmentEndIn, AppointmentOut, AppointmentUpdateIn, ErrorResponse

router = APIRouter(prefix="/appointments", tags=["staff appointments"], responses={
    401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
})

CanRead = Annotated[Principal, Depends(require_permissions("academic:appointments:read"))]
CanUpdate = Annotated[Principal, Depends(require_permissions("academic:appointments:update"))]
CanEnd = Annotated[Principal, Depends(require_permissions("academic:appointments:end"))]
RATE_LIMITED = [Depends(enforce_write_rate_limit)]


@router.get("/{appointment_id}", response_model=AppointmentDetailOut, summary="Appointment with its audit trail")
async def get_appointment(appointment_id: UUID, principal: CanRead, appointments: AppointmentServiceDep) -> AppointmentDetailOut:
    return appointment_detail_out(await appointments.get(principal, appointment_id))


@router.patch("/{appointment_id}", response_model=AppointmentOut, dependencies=RATE_LIMITED, summary="Update permitted appointment details")
async def update_appointment(appointment_id: UUID, body: AppointmentUpdateIn, principal: CanUpdate,
                             appointments: AppointmentServiceDep) -> AppointmentOut:
    return appointment_out(await appointments.update(principal, appointment_id, body.model_dump(exclude_unset=True)))


@router.post("/{appointment_id}/end", response_model=AppointmentOut, dependencies=RATE_LIMITED, summary="End an appointment")
async def end_appointment(appointment_id: UUID, principal: CanEnd, appointments: AppointmentServiceDep,
                          body: AppointmentEndIn | None = None) -> AppointmentOut:
    body = body or AppointmentEndIn()
    return appointment_out(await appointments.end(principal, appointment_id, end_date=body.end_date, reason=body.reason))
