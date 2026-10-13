"""Examination period endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, status

from app.api.deps import PERIOD_CODE, SourceDep
from app.api.schemas import ExamPeriodOut

router = APIRouter(prefix="/exam-periods", tags=["exam periods"])


@router.get("", response_model=list[ExamPeriodOut])
async def list_exam_periods(source: SourceDep) -> list[ExamPeriodOut]:
    """Periods with their excluded dates and the resulting exam days."""
    return [ExamPeriodOut.model_validate(p) for p in await source.list_exam_periods()]


@router.get("/{period_code}", response_model=ExamPeriodOut)
async def get_exam_period(
    source: SourceDep, period_code: Annotated[str, Path(pattern=PERIOD_CODE)]
) -> ExamPeriodOut:
    period = await source.get_exam_period(period_code)
    if period is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Exam period {period_code} not found")
    return ExamPeriodOut.model_validate(period)
