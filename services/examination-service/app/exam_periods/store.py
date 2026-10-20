"""Storage of exam period definitions (``seed_exam_periods`` and ``seed_exam_period_exclusions``).

:class:`ExamPeriodStore` is the interface; :class:`DatabaseExamPeriodStore` uses
the existing seed tables (no migration). Only the period row and its excluded
dates are written: cycle mappings, modules and registrations are only read.
Nothing is committed unless the caller calls :meth:`commit`.
"""

from __future__ import annotations

from typing import Any, Protocol

from sqlalchemy import delete, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.seed_models import SeedExamPeriod, SeedExamPeriodExclusion, SeedExamPeriodModule, SeedModule
from app.db.timetable_models import ExamTimetable
from app.exam_periods.errors import busy
from app.sources import DatabaseSource
from app.sources.types import ExamPeriod

_PERIOD_FIELDS = tuple(c.name for c in SeedExamPeriod.__table__.columns)
# Module columns used by app.sources.exam_rules.cycle_member_errors.
_CYCLE_MODULE_COLUMNS = (SeedModule.module_code, SeedModule.semester, SeedModule.duration_minutes,
                         SeedModule.has_final_exam, SeedModule.has_mid_exam, SeedModule.mid_duration_minutes,
                         SeedModule.delivery_mode)


class ExamPeriodStore(Protocol):
    async def get(self, period_code: str, *, lock: bool = False) -> ExamPeriod | None:
        """The period; ``lock`` keeps its row locked until the end of the transaction."""

    async def insert(self, period: ExamPeriod) -> bool:
        """Insert the period and its excluded dates; False (nothing written) when the code exists."""

    async def update(self, period: ExamPeriod) -> None:
        """Replace the stored fields and the excluded dates of an existing period."""

    async def timetable_statuses(self, period_code: str, *, lock: bool = False) -> list[str]:
        """Status of every saved timetable of the period, in every institution (``lock``: FOR UPDATE)."""

    async def cycle_modules(self, period_code: str) -> list[tuple[Any, str]]:
        """(module, candidate_scope) of every module in the period's cycle mapping."""

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


def _sqlstate(exc: Exception) -> str | None:
    return getattr(getattr(exc, "orig", None), "sqlstate", None)


def _period_values(period: ExamPeriod) -> dict[str, Any]:
    return {name: getattr(period, name) for name in _PERIOD_FIELDS}


class DatabaseExamPeriodStore:
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

    async def get(self, period_code: str, *, lock: bool = False) -> ExamPeriod | None:
        if lock:
            await self._execute(text(f"SET LOCAL lock_timeout = {self._lock_timeout_ms}"))
            found = (await self._execute(select(SeedExamPeriod.period_code)
                                         .where(SeedExamPeriod.period_code == period_code)
                                         .with_for_update())).first()
            if found is None:
                return None
        return await DatabaseSource(self._session).get_exam_period(period_code)

    async def insert(self, period: ExamPeriod) -> bool:
        await self._execute(text(f"SET LOCAL lock_timeout = {self._lock_timeout_ms}"))
        created = (await self._execute(
            pg_insert(SeedExamPeriod).values(**_period_values(period)).on_conflict_do_nothing()
            .returning(SeedExamPeriod.period_code)
        )).first()
        if created is None:
            return False
        await self._insert_exclusions(period)
        return True

    async def update(self, period: ExamPeriod) -> None:
        values = _period_values(period)
        del values["period_code"]
        await self._execute(update(SeedExamPeriod).where(SeedExamPeriod.period_code == period.period_code)
                            .values(**values))
        await self._execute(delete(SeedExamPeriodExclusion)
                            .where(SeedExamPeriodExclusion.period_code == period.period_code))
        await self._insert_exclusions(period)

    async def _insert_exclusions(self, period: ExamPeriod) -> None:
        if period.exclusions:
            await self._execute(insert(SeedExamPeriodExclusion), [
                {"period_code": period.period_code, "excluded_date": e.excluded_date, "reason": e.reason}
                for e in period.exclusions
            ])

    async def timetable_statuses(self, period_code: str, *, lock: bool = False) -> list[str]:
        statement = select(ExamTimetable.status).where(ExamTimetable.period_code == period_code)
        if lock:
            # The approve/publish workflow locks the same rows: an approval either finishes first
            # (and is seen here) or waits and then finds the changed period stale.
            statement = statement.with_for_update()
        return list((await self._execute(statement)).scalars())

    async def cycle_modules(self, period_code: str) -> list[tuple[Any, str]]:
        rows = await self._execute(
            select(SeedExamPeriodModule.candidate_scope, *_CYCLE_MODULE_COLUMNS)
            .join(SeedModule, SeedModule.module_code == SeedExamPeriodModule.module_code)
            .where(SeedExamPeriodModule.period_code == period_code)
            .order_by(SeedExamPeriodModule.module_code)
        )
        return [(row, row.candidate_scope) for row in rows]

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()
