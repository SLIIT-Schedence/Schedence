"""Shared dependencies and input validation rules for the API routes."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.db.seed_models import BLOCK_SOURCES, EXAM_TYPES, REGISTRATION_TYPES, VENUE_TYPES
from app.sources import DatabaseSource, ExamDataSource

# Identifier formats. Anything else is rejected with 422 before a query runs.
MODULE_CODE = r"^[A-Z]{2,4}[0-9]{4}$"  # e.g. IT3041
STUDENT_ID = r"^[A-Z]{2}[0-9]{8}$"  # e.g. IT30315600
SPECIALIZATION = r"^[A-Z_]{2,30}$"  # e.g. SE, COMPUTER_SCIENCE
# Campus codes are kept exactly: e.g. F1301, F1301+F1302, CyberSecLab, "Robotics Lab".
VENUE_ID = r"^[A-Za-z0-9+](?:[A-Za-z0-9+ -]{0,14}[A-Za-z0-9+])?$"
PERIOD_CODE = r"^[A-Z0-9-]{1,32}$"  # e.g. PP1-2026-S2-FINAL

ExamType = Literal[EXAM_TYPES]
RegistrationType = Literal[REGISTRATION_TYPES]
VenueType = Literal[VENUE_TYPES]
BlockSource = Literal[BLOCK_SOURCES]

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 500

AcademicYear = Annotated[int | None, Query(ge=1, le=4, description="Academic year 1-4")]
Specialization = Annotated[str | None, Query(pattern=SPECIALIZATION, description="e.g. IT, SE, DS, CYBER")]
Limit = Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE, description="Page size")]
Offset = Annotated[int, Query(ge=0, le=1_000_000, description="Rows to skip")]


def get_source(session: Annotated[AsyncSession, Depends(get_session)]) -> ExamDataSource:
    return DatabaseSource(session)


SourceDep = Annotated[ExamDataSource, Depends(get_source)]
