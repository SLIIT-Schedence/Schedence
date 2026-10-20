"""``ExamDataSource`` backed by the ``examinations.seed_*`` tables.

Runs only ``SELECT`` statements on the session it is given. The caller owns
the session (one per request or job) and decides the transaction mode; the
API opens every transaction ``READ ONLY`` (see ``app.database``).
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from collections.abc import Callable, Sequence
from typing import Any, TypeVar

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.seed_models import (
    SeedExamPeriod,
    SeedExamPeriodExclusion,
    SeedExamPeriodModule,
    SeedModule,
    SeedModuleSpecialization,
    SeedRegistration,
    SeedStudent,
    SeedVenue,
    SeedVenueBlock,
)
from app.seed.generator import exam_days
from app.sources.types import (
    ExamPeriod,
    ExamPeriodExclusion,
    ExamPeriodModule,
    Module,
    Page,
    Registration,
    Student,
    Venue,
    VenueBlock,
)

T = TypeVar("T")

# Columns read from seed_modules; the legacy ``specialization`` column is never read.
_MODULE_COLUMNS = (
    SeedModule.module_code,
    SeedModule.module_name,
    SeedModule.academic_year,
    SeedModule.semester,
    SeedModule.credits,
    SeedModule.difficulty,
    SeedModule.exam_type,
    SeedModule.duration_minutes,
    SeedModule.has_extra_reading_time,
    SeedModule.reading_time_minutes,
    SeedModule.has_final_exam,
    SeedModule.has_mid_exam,
    SeedModule.mid_duration_minutes,
    SeedModule.delivery_mode,
    SeedModule.block_category,
)
_STUDENT_COLUMNS = (SeedStudent.student_id, SeedStudent.academic_year, SeedStudent.specialization)
_REGISTRATION_COLUMNS = (
    SeedRegistration.student_id,
    SeedRegistration.module_code,
    SeedRegistration.registration_type,
)
_VENUE_COLUMNS = (
    SeedVenue.venue_id,
    SeedVenue.venue_name,
    SeedVenue.venue_type,
    SeedVenue.building,
    SeedVenue.floor,
    SeedVenue.normal_capacity,
    SeedVenue.exam_capacity,
)
_BLOCK_COLUMNS = (
    SeedVenueBlock.venue_id,
    SeedVenueBlock.block_date,
    SeedVenueBlock.start_time,
    SeedVenueBlock.end_time,
    SeedVenueBlock.source,
    SeedVenueBlock.reason,
    SeedVenueBlock.source_ref,
)
_PERIOD_COLUMNS = tuple(c for c in SeedExamPeriod.__table__.columns)
_EXCLUSION_COLUMNS = (
    SeedExamPeriodExclusion.period_code,
    SeedExamPeriodExclusion.excluded_date,
    SeedExamPeriodExclusion.reason,
)


class DatabaseSource:
    def __init__(self, session: AsyncSession):
        self._session = session

    # --- Modules -------------------------------------------------------------------

    async def list_modules(
        self,
        *,
        academic_year: int | None = None,
        specialization: str | None = None,
        exam_type: str | None = None,
    ) -> list[Module]:
        stmt = select(*_MODULE_COLUMNS)
        if academic_year is not None:
            stmt = stmt.where(SeedModule.academic_year == academic_year)
        if exam_type is not None:
            stmt = stmt.where(SeedModule.exam_type == exam_type)
        if specialization is not None:
            stmt = stmt.where(
                SeedModule.module_code.in_(
                    select(SeedModuleSpecialization.module_code).where(
                        SeedModuleSpecialization.specialization == specialization
                    )
                )
            )
        return await self._modules(stmt.order_by(SeedModule.module_code))

    async def get_module(self, module_code: str) -> Module | None:
        modules = await self._modules(select(*_MODULE_COLUMNS).where(SeedModule.module_code == module_code))
        return modules[0] if modules else None

    async def _modules(self, stmt: Select) -> list[Module]:
        rows = (await self._session.execute(stmt)).all()
        if not rows:
            return []
        codes = [row.module_code for row in rows]

        specs: dict[str, list[str]] = defaultdict(list)
        spec_rows = await self._session.execute(
            select(SeedModuleSpecialization.module_code, SeedModuleSpecialization.specialization)
            .where(SeedModuleSpecialization.module_code.in_(codes))
            .order_by(SeedModuleSpecialization.module_code, SeedModuleSpecialization.specialization)
        )
        for code, specialization in spec_rows:
            specs[code].append(specialization)

        count_rows = await self._session.execute(
            select(SeedRegistration.module_code, func.count())
            .where(SeedRegistration.module_code.in_(codes))
            .group_by(SeedRegistration.module_code)
        )
        counts = {code: count for code, count in count_rows}

        return [
            Module(
                **row._asdict(),
                specializations=tuple(specs.get(row.module_code, ())),
                candidate_count=counts.get(row.module_code, 0),
            )
            for row in rows
        ]

    # --- Students and registrations -----------------------------------------------------

    async def list_students(
        self,
        *,
        academic_year: int | None = None,
        specialization: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> Page[Student]:
        stmt = select(*_STUDENT_COLUMNS)
        if academic_year is not None:
            stmt = stmt.where(SeedStudent.academic_year == academic_year)
        if specialization is not None:
            stmt = stmt.where(SeedStudent.specialization == specialization)
        return await self._page(stmt, (SeedStudent.student_id,), limit, offset, lambda r: Student(**r._asdict()))

    async def get_student(self, student_id: str) -> Student | None:
        row = (
            await self._session.execute(select(*_STUDENT_COLUMNS).where(SeedStudent.student_id == student_id))
        ).one_or_none()
        return Student(**row._asdict()) if row else None

    async def list_registrations(
        self,
        *,
        student_id: str | None = None,
        module_code: str | None = None,
        registration_type: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> Page[Registration]:
        stmt = select(*_REGISTRATION_COLUMNS)
        if student_id is not None:
            stmt = stmt.where(SeedRegistration.student_id == student_id)
        if module_code is not None:
            stmt = stmt.where(SeedRegistration.module_code == module_code)
        if registration_type is not None:
            stmt = stmt.where(SeedRegistration.registration_type == registration_type)
        return await self._page(
            stmt,
            (SeedRegistration.student_id, SeedRegistration.module_code),
            limit,
            offset,
            lambda r: Registration(**r._asdict()),
        )

    # --- Venues --------------------------------------------------------------------------

    async def list_venues(self, *, venue_type: str | None = None) -> list[Venue]:
        stmt = select(*_VENUE_COLUMNS)
        if venue_type is not None:
            stmt = stmt.where(SeedVenue.venue_type == venue_type)
        rows = await self._session.execute(stmt.order_by(SeedVenue.venue_id))
        return [Venue(**row._asdict()) for row in rows]

    async def list_venue_blocks(
        self,
        *,
        venue_id: str | None = None,
        source: str | None = None,
        date_from: dt.date | None = None,
        date_to: dt.date | None = None,
    ) -> list[VenueBlock]:
        stmt = select(*_BLOCK_COLUMNS)
        if venue_id is not None:
            stmt = stmt.where(SeedVenueBlock.venue_id == venue_id)
        if source is not None:
            stmt = stmt.where(SeedVenueBlock.source == source)
        if date_from is not None:
            stmt = stmt.where(SeedVenueBlock.block_date >= date_from)
        if date_to is not None:
            stmt = stmt.where(SeedVenueBlock.block_date <= date_to)
        rows = await self._session.execute(
            stmt.order_by(
                SeedVenueBlock.venue_id, SeedVenueBlock.block_date, SeedVenueBlock.start_time, SeedVenueBlock.source
            )
        )
        return [VenueBlock(**row._asdict()) for row in rows]

    # --- Exam periods ----------------------------------------------------------------------

    async def list_exam_periods(self) -> list[ExamPeriod]:
        return await self._periods(select(*_PERIOD_COLUMNS).order_by(SeedExamPeriod.period_code))

    async def get_exam_period(self, period_code: str) -> ExamPeriod | None:
        periods = await self._periods(select(*_PERIOD_COLUMNS).where(SeedExamPeriod.period_code == period_code))
        return periods[0] if periods else None

    async def _periods(self, stmt: Select) -> list[ExamPeriod]:
        rows = (await self._session.execute(stmt)).all()
        if not rows:
            return []
        exclusion_rows = await self._session.execute(
            select(*_EXCLUSION_COLUMNS)
            .where(SeedExamPeriodExclusion.period_code.in_([r.period_code for r in rows]))
            .order_by(SeedExamPeriodExclusion.period_code, SeedExamPeriodExclusion.excluded_date)
        )
        exclusions: dict[str, list[ExamPeriodExclusion]] = defaultdict(list)
        for row in exclusion_rows:
            exclusions[row.period_code].append(ExamPeriodExclusion(**row._asdict()))
        return [_period(row, tuple(exclusions.get(row.period_code, ()))) for row in rows]

    async def list_exam_period_modules(self, period_code: str) -> list[ExamPeriodModule]:
        rows = await self._session.execute(
            select(SeedExamPeriodModule.period_code, SeedExamPeriodModule.module_code,
                   SeedExamPeriodModule.candidate_scope)
            .where(SeedExamPeriodModule.period_code == period_code)
            .order_by(SeedExamPeriodModule.module_code)
        )
        return [ExamPeriodModule(**row._asdict()) for row in rows]

    # --- Helpers ---------------------------------------------------------------------------

    async def _page(
        self,
        stmt: Select,
        order_by: Sequence[Any],
        limit: int | None,
        offset: int,
        convert: Callable[[Any], T],
    ) -> Page[T]:
        total = (await self._session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
        stmt = stmt.order_by(*order_by).offset(offset)
        if limit is not None:
            stmt = stmt.limit(limit)
        rows = await self._session.execute(stmt)
        return Page(items=tuple(convert(row) for row in rows), total=total, limit=limit, offset=offset)


def _period(row: Any, exclusions: tuple[ExamPeriodExclusion, ...]) -> ExamPeriod:
    return ExamPeriod(
        **row._asdict(),
        exclusions=exclusions,
        exam_days=exam_days(row, exclusions),
    )
