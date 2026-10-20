"""Module endpoints.

GET routes require ``examinations:read``. Changing a module's examination details
(``PUT /modules/{module_code}/exam-details``) requires ``examinations:manage``;
``dry_run=true`` validates and previews the change without saving it. See
:mod:`app.module_details.service` for the safety rules.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from app.api.deps import MODULE_CODE, AcademicYear, ExamType, ModuleDetailsWriterDep, SourceDep, Specialization
from app.api.schemas import ModuleExamDetailsChangeOut, ModuleOut, UpdateModuleExamDetailsIn
from app.auth.dependencies import ExamAccess, require_dataset_admin, require_dataset_read
from app.module_details.rules import details_fingerprint, details_of
from app.sources.types import Module

router = APIRouter(prefix="/modules", tags=["modules"])

ReadAccess = [Depends(require_dataset_read)]
ModuleAdmin = Annotated[ExamAccess, Depends(require_dataset_admin)]
DryRun = Annotated[bool, Query(description="Validate and preview the change without saving it")]
ModuleCodePath = Annotated[str, Path(pattern=MODULE_CODE)]


def _out(module: Module) -> ModuleOut:
    out = ModuleOut.model_validate(module)
    out.fingerprint = details_fingerprint(module.module_code, details_of(module))
    return out


@router.get("", response_model=list[ModuleOut], dependencies=ReadAccess)
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
    return [_out(m) for m in modules]


@router.get("/{module_code}", response_model=ModuleOut, dependencies=ReadAccess)
async def get_module(source: SourceDep, module_code: ModuleCodePath) -> ModuleOut:
    module = await source.get_module(module_code)
    if module is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Module {module_code} not found")
    return _out(module)


@router.put("/{module_code}/exam-details", response_model=ModuleExamDetailsChangeOut)
async def update_module_exam_details(admin: ModuleAdmin, module_code: ModuleCodePath, body: UpdateModuleExamDetailsIn,
                                     service: ModuleDetailsWriterDep,
                                     dry_run: DryRun = False) -> ModuleExamDetailsChangeOut:
    """Replace an existing module's examination details (complete document). Nothing else of the module changes."""
    change = await service.update(module_code, body.to_details(), expected_fingerprint=body.expected_fingerprint,
                                  user_id=admin.user_id, dry_run=dry_run)
    return ModuleExamDetailsChangeOut(dry_run=change.dry_run, changed=change.changed,
                                      changed_fields=list(change.changed_fields), module=_out(change.module),
                                      draft_timetables=change.draft_timetables, notices=list(change.notices))
