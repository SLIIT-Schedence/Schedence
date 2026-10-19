"""Storage of module examination details (columns of ``seed_modules``; no migration).

:class:`ModuleDetailsStore` is the interface; :class:`DatabaseModuleDetailsStore`
updates only the examination columns of one existing ``seed_modules`` row. The
module code, specializations, registrations and cycle mappings are only read.
Nothing is committed unless the caller calls :meth:`commit`.
"""

from __future__ import annotations

import dataclasses
from typing import Protocol

from sqlalchemy import or_, select, text, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.seed_models import SeedExamPeriodModule, SeedModule
from app.db.timetable_models import ExamTimetable, ExamTimetableSession
from app.module_details.errors import busy
from app.module_details.rules import ExamDetails
from app.sources import DatabaseSource
from app.sources.types import ExamPeriod, Module


@dataclasses.dataclass(frozen=True)
class SavedTimetableRef:
    """A saved timetable that depends on the module: it is in the module's cycle, or has a session of it."""

    status: str
    period_code: str
    exam_kind: str


class ModuleDetailsStore(Protocol):
    async def get(self, module_code: str, *, lock: bool = False) -> Module | None:
        """The module; ``lock`` keeps its row locked until the end of the transaction."""

    async def update(self, module_code: str, details: ExamDetails) -> None:
        """Write the examination details of an existing module."""

    async def cycles(self, module_code: str) -> list[tuple[ExamPeriod, str]]:
        """(period, candidate_scope) of every cycle mapping that contains the module."""

    async def timetables(self, module_code: str, *, lock: bool = False) -> list[SavedTimetableRef]:
        """Saved timetables (every institution) that depend on the module (``lock``: FOR UPDATE)."""

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


def _sqlstate(exc: Exception) -> str | None:
    return getattr(getattr(exc, "orig", None), "sqlstate", None)


class DatabaseModuleDetailsStore:
    def __init__(self, session: AsyncSession, *, lock_timeout_ms: int = 5_000):
        self._session = session
        self._lock_timeout_ms = int(lock_timeout_ms)

    async def _execute(self, statement, params=None):
        try:
            return await self._session.execute(statement, params)
        except OperationalError as exc:
            if _sqlstate(exc) == "55P03":  # lock_not_available (lock_timeout)
                raise busy() from None
            raise

    async def get(self, module_code: str, *, lock: bool = False) -> Module | None:
        if lock:
            await self._execute(text(f"SET LOCAL lock_timeout = {self._lock_timeout_ms}"))
            found = (await self._execute(select(SeedModule.module_code).where(SeedModule.module_code == module_code)
                                         .with_for_update())).first()
            if found is None:
                return None
        return await DatabaseSource(self._session).get_module(module_code)

    async def update(self, module_code: str, details: ExamDetails) -> None:
        await self._execute(update(SeedModule).where(SeedModule.module_code == module_code).values(
            **dataclasses.asdict(details), has_extra_reading_time=details.reading_time_minutes > 0))

    async def cycles(self, module_code: str) -> list[tuple[ExamPeriod, str]]:
        rows = (await self._execute(
            select(SeedExamPeriodModule.period_code, SeedExamPeriodModule.candidate_scope)
            .where(SeedExamPeriodModule.module_code == module_code)
            .order_by(SeedExamPeriodModule.period_code)
        )).all()
        source = DatabaseSource(self._session)
        return [(await source.get_exam_period(row.period_code), row.candidate_scope) for row in rows]

    async def timetables(self, module_code: str, *, lock: bool = False) -> list[SavedTimetableRef]:
        in_cycle = select(SeedExamPeriodModule.period_code).where(SeedExamPeriodModule.module_code == module_code)
        has_session = select(ExamTimetableSession.timetable_id).where(ExamTimetableSession.module_code == module_code)
        statement = (select(ExamTimetable.status, ExamTimetable.period_code, ExamTimetable.exam_kind)
                     .where(or_(ExamTimetable.period_code.in_(in_cycle), ExamTimetable.timetable_id.in_(has_session)))
                     .order_by(ExamTimetable.timetable_id))
        if lock:
            # The approve/publish workflow locks the same rows: an approval either finishes first
            # (and is seen here) or waits and then finds its inputs stale.
            statement = statement.with_for_update(of=ExamTimetable)
        return [SavedTimetableRef(*row) for row in await self._execute(statement)]

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()
