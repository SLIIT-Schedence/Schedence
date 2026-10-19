"""In-memory ``ExamPeriodStore`` for tests and offline tools.

Behaves like the database store, including transactions: changes are visible
only to this store until :meth:`commit`; :meth:`rollback` throws them away.
"""

from __future__ import annotations

import copy
from collections import defaultdict

from app.exam_periods.rules import build_period
from app.seed.records import Dataset
from app.sources.types import ExamPeriod


class MemoryExamPeriodStore:
    def __init__(self, data: Dataset):
        self._committed: dict[str, ExamPeriod] = {
            p.period_code: build_period(
                **{f: getattr(p, f) for f in ("period_code", "period_name", "academic_year_label", "semester",
                                              "exam_kind", "start_date", "end_date", "day_start_time",
                                              "day_end_time", "include_saturdays", "include_sundays")},
                exclusions=[(e.excluded_date, e.reason) for e in data.exam_period_exclusions
                            if e.period_code == p.period_code],
            )
            for p in data.exam_periods
        }
        self._work = dict(self._committed)
        modules = {m.module_code: m for m in data.modules}
        self._cycle = [(m.period_code, modules[m.module_code], m.candidate_scope)
                       for m in sorted(data.exam_period_modules, key=lambda m: m.module_code)]
        # Saved timetable statuses per period code, set by tests.
        self.timetables: dict[str, list[str]] = defaultdict(list)
        self.commits = 0
        self.fail_on: str | None = None  # name of a method that raises, to test rollback

    def _maybe_fail(self, name: str) -> None:
        if self.fail_on == name:
            raise RuntimeError(f"simulated failure in {name}")

    async def get(self, period_code: str, *, lock: bool = False) -> ExamPeriod | None:
        return self._work.get(period_code)

    async def insert(self, period: ExamPeriod) -> bool:
        self._maybe_fail("insert")
        if period.period_code in self._work:
            return False
        self._work[period.period_code] = period
        return True

    async def update(self, period: ExamPeriod) -> None:
        self._maybe_fail("update")
        self._work[period.period_code] = period

    async def timetable_statuses(self, period_code: str, *, lock: bool = False) -> list[str]:
        return list(self.timetables.get(period_code, ()))

    async def cycle_modules(self, period_code: str):
        return [(module, scope) for code, module, scope in self._cycle if code == period_code]

    async def commit(self) -> None:
        self._maybe_fail("commit")
        self._committed = dict(self._work)
        self.commits += 1

    async def rollback(self) -> None:
        self._work = dict(self._committed)

    def committed(self) -> dict[str, ExamPeriod]:
        return copy.copy(self._committed)
