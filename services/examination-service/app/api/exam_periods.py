"""Examination period endpoints.

GET routes require ``examinations:read``. Creating and updating a period
(``POST``/``PUT``) require ``examinations:manage``. ``dry_run=true`` validates and
previews a change without saving it. See :mod:`app.exam_periods.service` for the
safety rules (fingerprint check, periods locked by approved/published timetables,
cycle mapping kept valid).
"""

from __future__ import annotations

import dataclasses
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from app.api.deps import PERIOD_CODE, ExamPeriodWriterDep, SourceDep
from app.api.schemas import (
    CreateExamPeriodIn,
    ExamPeriodChangeOut,
    ExamPeriodModuleOut,
    ExamPeriodOut,
    UpdateExamPeriodIn,
)
from app.auth.dependencies import require_exam_data_access, require_exam_settings_admin
from app.auth.principal import Principal
from app.exam_periods import PeriodChange
from app.exam_periods.rules import period_fingerprint
from app.sources.types import ExamPeriod

router = APIRouter(prefix="/exam-periods", tags=["exam periods"])

ReadAccess = [Depends(require_exam_data_access)]
PeriodAdmin = Annotated[Principal, Depends(require_exam_settings_admin)]
DryRun = Annotated[bool, Query(description="Validate and preview the change without saving it")]
PeriodCodePath = Annotated[str, Path(pattern=PERIOD_CODE)]


def _out(period: ExamPeriod) -> ExamPeriodOut:
    return ExamPeriodOut.model_validate({**dataclasses.asdict(period), "fingerprint": period_fingerprint(period)})


def _change_out(change: PeriodChange) -> ExamPeriodChangeOut:
    return ExamPeriodChangeOut(dry_run=change.dry_run, action=change.action, changed=change.changed,
                               changed_fields=list(change.changed_fields), period=_out(change.period),
                               draft_timetables=change.draft_timetables, notices=list(change.notices))


@router.get("", response_model=list[ExamPeriodOut], dependencies=ReadAccess)
async def list_exam_periods(source: SourceDep) -> list[ExamPeriodOut]:
    """Periods with their excluded dates and the resulting exam days."""
    return [_out(p) for p in await source.list_exam_periods()]


@router.get("/{period_code}", response_model=ExamPeriodOut, dependencies=ReadAccess)
async def get_exam_period(source: SourceDep, period_code: PeriodCodePath) -> ExamPeriodOut:
    period = await source.get_exam_period(period_code)
    if period is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Exam period {period_code} not found")
    return _out(period)


@router.get("/{period_code}/modules", response_model=list[ExamPeriodModuleOut], dependencies=ReadAccess)
async def list_exam_period_modules(source: SourceDep, period_code: PeriodCodePath) -> list[ExamPeriodModuleOut]:
    """The examination cycle mapping: modules examined in this period, and who sits them."""
    if await source.get_exam_period(period_code) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Exam period {period_code} not found")
    return [ExamPeriodModuleOut.model_validate(m) for m in await source.list_exam_period_modules(period_code)]


@router.post("", response_model=ExamPeriodChangeOut, status_code=status.HTTP_201_CREATED)
async def create_exam_period(admin: PeriodAdmin, body: CreateExamPeriodIn, service: ExamPeriodWriterDep,
                             dry_run: DryRun = False) -> ExamPeriodChangeOut:
    """Create a MID or FINAL period. Its cycle mapping (modules) is not created here."""
    period = body.to_period(body.period_code)
    return _change_out(await service.create(period, user_id=admin.user_id, dry_run=dry_run))


@router.put("/{period_code}", response_model=ExamPeriodChangeOut)
async def update_exam_period(admin: PeriodAdmin, period_code: PeriodCodePath, body: UpdateExamPeriodIn,
                             service: ExamPeriodWriterDep, dry_run: DryRun = False) -> ExamPeriodChangeOut:
    """Replace a period's name, dates, daily hours, weekend settings and excluded dates (complete document)."""
    return _change_out(await service.update(body.to_period(period_code), expected_fingerprint=body.expected_fingerprint,
                                            user_id=admin.user_id, dry_run=dry_run))
