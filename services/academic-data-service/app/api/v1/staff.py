from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.dependencies import AppointmentServiceDep, PaginationDep, StaffServiceDep, enforce_write_rate_limit, require_permissions
from app.api.v1.mappers import appointment_out, staff_out, workload_out
from app.core.principal import Principal
from app.schemas.academic import (
    AdministrativeWorkloadOut,
    AppointmentCreateIn,
    AppointmentOut,
    ErrorResponse,
    Page,
    StaffCreateIn,
    StaffOut,
)
from app.services.appointments import AppointmentInput

router = APIRouter(prefix="/staff", responses={
    401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
})

CanCreateStaff = Annotated[Principal, Depends(require_permissions("academic:staff:create"))]
CanReadStaff = Annotated[Principal, Depends(require_permissions("academic:staff:read"))]
CanAssign = Annotated[Principal, Depends(require_permissions("academic:appointments:assign"))]
CanReadAppointments = Annotated[Principal, Depends(require_permissions("academic:appointments:read"))]
CanReadWorkload = Annotated[Principal, Depends(require_permissions("academic:workload:read"))]
RATE_LIMITED = [Depends(enforce_write_rate_limit)]


@router.post("", status_code=status.HTTP_201_CREATED, response_model=StaffOut, dependencies=RATE_LIMITED, tags=["staff"],
             summary="Register a minimal staff reference (no login required)")
async def create_staff(body: StaffCreateIn, principal: CanCreateStaff, staff: StaffServiceDep) -> StaffOut:
    return staff_out(await staff.create(principal, institution_id=body.institution_id, staff_number=body.staff_number,
                                        full_name=body.full_name, email=str(body.email) if body.email else None,
                                        academic_rank=body.academic_rank, identity_user_id=body.identity_user_id))


@router.get("", response_model=Page[StaffOut], tags=["staff"], summary="List staff references")
async def list_staff(principal: CanReadStaff, staff: StaffServiceDep, pagination: PaginationDep, institution_id: UUID | None = None,
                     search: Annotated[str | None, Query(max_length=100)] = None) -> Page[StaffOut]:
    rows, total = await staff.list(principal, institution_id=institution_id, search=search, offset=pagination.offset,
                                   limit=pagination.page_size)
    return Page(items=[staff_out(s) for s in rows], total=total, page=pagination.page, page_size=pagination.page_size)


@router.get("/{staff_id}", response_model=StaffOut, tags=["staff"], summary="View a staff reference")
async def get_staff(staff_id: UUID, principal: CanReadStaff, staff: StaffServiceDep) -> StaffOut:
    return staff_out(await staff.get(principal, staff_id))


@router.post("/{staff_id}/appointments", status_code=status.HTTP_201_CREATED, response_model=AppointmentOut, dependencies=RATE_LIMITED,
             tags=["staff appointments"], summary="Assign an administrative position to a staff member")
async def assign_appointment(staff_id: UUID, body: AppointmentCreateIn, principal: CanAssign,
                             appointments: AppointmentServiceDep) -> AppointmentOut:
    data = AppointmentInput(**body.model_dump())
    return appointment_out(await appointments.assign(principal, staff_id, data))


@router.get("/{staff_id}/appointments", response_model=list[AppointmentOut], tags=["staff appointments"],
            summary="Current appointments, or full history with include_ended=true")
async def list_appointments(staff_id: UUID, principal: CanReadAppointments, appointments: AppointmentServiceDep,
                            include_ended: bool = False) -> list[AppointmentOut]:
    return [appointment_out(v) for v in await appointments.list_for_staff(principal, staff_id, include_ended=include_ended)]


@router.get("/{staff_id}/administrative-workload", response_model=AdministrativeWorkloadOut, tags=["administrative workload"],
            summary="Administrative workload contract for scheduling services")
async def get_administrative_workload(staff_id: UUID, principal: CanReadWorkload, appointments: AppointmentServiceDep,
                                      as_of: date | None = None) -> AdministrativeWorkloadOut:
    return workload_out(await appointments.administrative_workload(principal, staff_id, as_of=as_of))
