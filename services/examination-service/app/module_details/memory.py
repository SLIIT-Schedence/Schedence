"""In-memory ``ModuleDetailsStore`` for tests and offline tools.

Reads modules, periods and cycle mappings from a :class:`DatasetSource`. Behaves
like the database store, including transactions: changes are visible only to
this store until :meth:`commit`; :meth:`rollback` throws them away.
"""

from __future__ import annotations

import dataclasses

from app.module_details.rules import ExamDetails, apply_details
from app.module_details.store import SavedTimetableRef
from app.sources import DatasetSource
from app.sources.types import ExamPeriod, Module


class MemoryModuleDetailsStore:
    def __init__(self, source: DatasetSource):
        self._source = source
        self._committed: dict[str, ExamDetails] = {}
        self._work: dict[str, ExamDetails] = {}
        # Saved timetables that depend on each module, set by tests.
        self.timetable_refs: dict[str, list[SavedTimetableRef]] = {}
        self.commits = 0
        self.fail_on: str | None = None  # name of a method that raises, to test rollback

    def _maybe_fail(self, name: str) -> None:
        if self.fail_on == name:
            raise RuntimeError(f"simulated failure in {name}")

    async def get(self, module_code: str, *, lock: bool = False) -> Module | None:
        module = await self._source.get_module(module_code)
        if module is not None and module_code in self._work:
            module = apply_details(module, self._work[module_code])
        return module

    async def update(self, module_code: str, details: ExamDetails) -> None:
        self._maybe_fail("update")
        self._work[module_code] = details

    async def cycles(self, module_code: str) -> list[tuple[ExamPeriod, str]]:
        result = []
        for period in await self._source.list_exam_periods():
            for member in await self._source.list_exam_period_modules(period.period_code):
                if member.module_code == module_code:
                    result.append((period, member.candidate_scope))
        return result

    async def timetables(self, module_code: str, *, lock: bool = False) -> list[SavedTimetableRef]:
        return list(self.timetable_refs.get(module_code, ()))

    async def commit(self) -> None:
        self._maybe_fail("commit")
        self._committed = dict(self._work)
        self.commits += 1

    async def rollback(self) -> None:
        self._work = dict(self._committed)

    def committed(self, module_code: str) -> ExamDetails | None:
        details = self._committed.get(module_code)
        return dataclasses.replace(details) if details else None
