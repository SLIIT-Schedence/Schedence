"""Create and update examination periods (MID/FINAL cycles).

Safety rules:

* Every problem with dates, daily hours, weekends and excluded dates is listed (422).
* An update must send the ``fingerprint`` it last read (409 ``VERSION`` if it changed).
* Once any saved timetable of the period has left DRAFT (APPROVED, PUBLISHED or
  ARCHIVED, in any institution), only ``period_name`` may change (409 ``PERIOD_LOCKED``).
* Changing ``exam_kind`` or ``semester`` must keep every module of the cycle
  mapping valid (422 ``CYCLE_MAPPING``); the mapping itself is never changed.
* Saved DRAFT timetables are not changed; after a change they are stale and the
  approval workflow refuses them until they are regenerated.
* ``dry_run`` validates and previews without writing (and without locking).

There is no period history table (that would need a migration): every change is
written to the service log with the user ID.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from app.exam_periods.errors import conflict, invalid, not_found
from app.exam_periods.rules import (
    CYCLE_FIELDS,
    EDITABLE_FIELDS,
    LOCKED_PERIOD_EDITABLE,
    changed_fields,
    period_fingerprint,
    period_problems,
)
from app.exam_periods.store import ExamPeriodStore
from app.sources.exam_rules import cycle_member_errors
from app.sources.types import ExamPeriod

logger = logging.getLogger(__name__)

STALE_DRAFTS_NOTICE = ("{count} saved draft timetable(s) of this period were generated from the previous definition. "
                       "They can no longer be approved; regenerate them.")


@dataclass(frozen=True)
class PeriodChange:
    dry_run: bool
    action: Literal["create", "update"]
    changed: bool
    changed_fields: tuple[str, ...]
    period: ExamPeriod
    draft_timetables: int  # saved DRAFT timetables of the period (stale after a change)
    notices: tuple[str, ...]


class ExamPeriodService:
    def __init__(self, store: ExamPeriodStore):
        self._store = store

    async def create(self, period: ExamPeriod, *, user_id: UUID, dry_run: bool = False) -> PeriodChange:
        problems = period_problems(period)
        if problems:
            raise invalid(problems)
        exists = conflict("PERIOD_EXISTS", f"Exam period {period.period_code} already exists. "
                                           "Use PUT to change it.")
        try:
            if await self._store.get(period.period_code) is not None:
                raise exists
            if dry_run:
                return PeriodChange(True, "create", True, EDITABLE_FIELDS, period, 0, ())
            if not await self._store.insert(period):  # created at the same time by someone else
                raise exists
            await self._store.commit()
        except BaseException:
            await self._store.rollback()
            raise
        logger.info("Exam period %s (%s) created by %s", period.period_code, period.exam_kind, user_id)
        return PeriodChange(False, "create", True, EDITABLE_FIELDS, await self._saved(period.period_code), 0, ())

    async def update(self, period: ExamPeriod, *, expected_fingerprint: str, user_id: UUID,
                     dry_run: bool = False) -> PeriodChange:
        code = period.period_code
        try:
            current = await self._store.get(code, lock=not dry_run)
            if current is None:
                raise not_found(code)
            current_fingerprint = period_fingerprint(current)
            if current_fingerprint != expected_fingerprint:
                raise conflict("VERSION", f"The exam period changed since you read it (current fingerprint "
                                          f"{current_fingerprint}). Reload it and try again.")
            problems = period_problems(period)
            if problems:
                raise invalid(problems)
            changed = changed_fields(current, period)
            statuses = await self._store.timetable_statuses(code, lock=not dry_run)
            released = sorted({s for s in statuses if s != "DRAFT"})
            locked = [name for name in changed if name not in LOCKED_PERIOD_EDITABLE]
            if released and locked:
                raise conflict("PERIOD_LOCKED",
                               f"This period has saved timetables with status {', '.join(released)}. "
                               "Only period_name may change.",
                               [{"rule": "PERIOD_LOCKED", "message": f"{name} cannot change."} for name in locked])
            if CYCLE_FIELDS & set(changed):
                mapping = [{"rule": "CYCLE_MAPPING", "message": f"Module {module.module_code} {error}."}
                           for module, scope in await self._store.cycle_modules(code)
                           for error in cycle_member_errors(period, module, scope)]
                if mapping:
                    raise invalid(mapping)
            drafts = statuses.count("DRAFT")
            notices = (STALE_DRAFTS_NOTICE.format(count=drafts),) if changed and drafts else ()
            if dry_run or not changed:
                await self._store.rollback()
                return PeriodChange(dry_run, "update", bool(changed), tuple(changed),
                                    period if changed else current, drafts, notices)
            await self._store.update(period)
            await self._store.commit()
        except BaseException:
            await self._store.rollback()
            raise
        logger.info("Exam period %s updated by %s: %s", code, user_id, ", ".join(changed))
        return PeriodChange(False, "update", True, tuple(changed), await self._saved(code), drafts, notices)

    async def _saved(self, period_code: str) -> ExamPeriod:
        saved = await self._store.get(period_code)
        if saved is None:  # cannot happen right after a successful commit
            raise not_found(period_code)
        return saved
