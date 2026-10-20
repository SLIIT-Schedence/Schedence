"""The read-only data source interface used by the API and the future scheduler.

Code that needs examination input depends on :class:`ExamDataSource`, never on
the ``seed_*`` models, so the storage can be swapped (seed tables today,
Academic Data / Resource service APIs later) without changing callers.

All list results are sorted by their natural key, so pages are stable.
"""

from __future__ import annotations

import datetime as dt
from typing import Protocol

from app.sources.types import ExamPeriod, ExamPeriodModule, Module, Page, Registration, Student, Venue, VenueBlock


class ExamDataSource(Protocol):
    async def list_modules(
        self,
        *,
        academic_year: int | None = None,
        specialization: str | None = None,
        exam_type: str | None = None,
    ) -> list[Module]: ...

    async def get_module(self, module_code: str) -> Module | None: ...

    async def list_students(
        self,
        *,
        academic_year: int | None = None,
        specialization: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> Page[Student]: ...

    async def get_student(self, student_id: str) -> Student | None: ...

    async def list_registrations(
        self,
        *,
        student_id: str | None = None,
        module_code: str | None = None,
        registration_type: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> Page[Registration]: ...

    async def list_venues(self, *, venue_type: str | None = None) -> list[Venue]: ...

    async def list_venue_blocks(
        self,
        *,
        venue_id: str | None = None,
        source: str | None = None,
        date_from: dt.date | None = None,
        date_to: dt.date | None = None,
    ) -> list[VenueBlock]: ...

    async def list_exam_periods(self) -> list[ExamPeriod]: ...

    async def get_exam_period(self, period_code: str) -> ExamPeriod | None: ...

    async def list_exam_period_modules(self, period_code: str) -> list[ExamPeriodModule]:
        """The cycle mapping of one period, sorted by module code (empty when not configured)."""
        ...
