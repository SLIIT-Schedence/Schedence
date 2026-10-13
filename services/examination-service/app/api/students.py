"""Student and registration endpoints (paginated)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, status

from app.api.deps import (
    DEFAULT_PAGE_SIZE,
    MODULE_CODE,
    STUDENT_ID,
    AcademicYear,
    Limit,
    Offset,
    RegistrationType,
    SourceDep,
    Specialization,
)
from app.api.schemas import PageOut, RegistrationOut, StudentDetailOut, StudentOut, StudentRegistrationOut

router = APIRouter(tags=["students"])


@router.get("/students", response_model=PageOut[StudentOut])
async def list_students(
    source: SourceDep,
    academic_year: AcademicYear = None,
    specialization: Specialization = None,
    limit: Limit = DEFAULT_PAGE_SIZE,
    offset: Offset = 0,
) -> PageOut[StudentOut]:
    """Students sorted by ID. Use ``limit`` and ``offset`` to page through them."""
    page = await source.list_students(
        academic_year=academic_year, specialization=specialization, limit=limit, offset=offset
    )
    return PageOut[StudentOut](
        items=[StudentOut.model_validate(s) for s in page.items], total=page.total, limit=limit, offset=offset
    )


@router.get("/students/{student_id}", response_model=StudentDetailOut)
async def get_student(
    source: SourceDep, student_id: Annotated[str, Path(pattern=STUDENT_ID)]
) -> StudentDetailOut:
    """One student with every module they are registered for."""
    student = await source.get_student(student_id)
    if student is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Student {student_id} not found")
    registrations = await source.list_registrations(student_id=student_id)
    return StudentDetailOut(
        **StudentOut.model_validate(student).model_dump(),
        registrations=[StudentRegistrationOut.model_validate(r) for r in registrations.items],
    )


@router.get("/registrations", response_model=PageOut[RegistrationOut], tags=["registrations"])
async def list_registrations(
    source: SourceDep,
    student_id: Annotated[str | None, Query(pattern=STUDENT_ID)] = None,
    module_code: Annotated[str | None, Query(pattern=MODULE_CODE)] = None,
    registration_type: Annotated[RegistrationType | None, Query()] = None,
    limit: Limit = DEFAULT_PAGE_SIZE,
    offset: Offset = 0,
) -> PageOut[RegistrationOut]:
    """Registrations sorted by (student, module). Use ``limit`` and ``offset`` to page."""
    page = await source.list_registrations(
        student_id=student_id,
        module_code=module_code,
        registration_type=registration_type,
        limit=limit,
        offset=offset,
    )
    return PageOut[RegistrationOut](
        items=[RegistrationOut.model_validate(r) for r in page.items], total=page.total, limit=limit, offset=offset
    )
