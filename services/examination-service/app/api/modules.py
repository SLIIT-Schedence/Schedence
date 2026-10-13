"""Module endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, status

from app.api.deps import MODULE_CODE, AcademicYear, ExamType, SourceDep, Specialization
from app.api.schemas import ModuleOut

router = APIRouter(prefix="/modules", tags=["modules"])


@router.get("", response_model=list[ModuleOut])
async def list_modules(
    source: SourceDep,
    academic_year: AcademicYear = None,
    specialization: Specialization = None,
    exam_type: Annotated[ExamType | None, Query()] = None,
) -> list[ModuleOut]:
    """All in-scope modules, sorted by code, with their candidate counts."""
    modules = await source.list_modules(
        academic_year=academic_year, specialization=specialization, exam_type=exam_type
    )
    return [ModuleOut.model_validate(m) for m in modules]


@router.get("/{module_code}", response_model=ModuleOut)
async def get_module(source: SourceDep, module_code: Annotated[str, Path(pattern=MODULE_CODE)]) -> ModuleOut:
    module = await source.get_module(module_code)
    if module is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Module {module_code} not found")
    return ModuleOut.model_validate(module)
