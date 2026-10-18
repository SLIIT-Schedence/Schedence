"""Storage of seating rules and their history.

:class:`SeatingRuleStore` is the interface; :class:`DatabaseSeatingStore`
implements it on the ``examinations.exam_seating_*`` tables. The caller owns
the session and its transaction; only :meth:`commit` / :meth:`rollback` end it.
"""

from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.seating_models import ExamSeatingRule, ExamSeatingRuleHistory
from app.seating.errors import SeatingConflictError, SeatingNotConfiguredError
from app.seating.rules import Action, HistoryEntry, Scope, SeatingRule


class SeatingRuleStore(Protocol):
    async def list_rules(self) -> list[SeatingRule]: ...

    async def get_rule(self, venue_id: str | None, *, lock: bool = False) -> SeatingRule | None:
        """The default rule (``venue_id=None``) or a venue override. ``lock`` = SELECT ... FOR UPDATE."""

    async def last_version(self, venue_id: str | None) -> int:
        """Highest version ever recorded for this rule in the history (0 if none)."""

    async def insert_rule(
        self, *, scope: Scope, venue_id: str | None, percentage: int, version: int, reason: str, updated_by: UUID
    ) -> SeatingRule:
        """Raises :class:`SeatingConflictError` if the rule already exists."""

    async def update_rule(
        self, rule_id: int, *, expected_version: int, percentage: int, reason: str, updated_by: UUID
    ) -> SeatingRule | None:
        """``None`` when the stored version is not ``expected_version``."""

    async def delete_rule(self, rule_id: int, *, expected_version: int) -> bool: ...

    async def add_history(
        self,
        *,
        rule_id: int,
        action: Action,
        scope: Scope,
        venue_id: str | None,
        old_percentage: int | None,
        new_percentage: int | None,
        version: int,
        changed_by: UUID,
        reason: str,
    ) -> HistoryEntry: ...

    async def list_history(self, *, limit: int, venue_id: str | None = None) -> list[HistoryEntry]:
        """Newest first."""

    async def ruleset_version(self) -> int:
        """ID of the newest history row: changes whenever any rule changes."""

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


_RULE_COLUMNS = tuple(ExamSeatingRule.__table__.columns)
_HISTORY_COLUMNS = tuple(ExamSeatingRuleHistory.__table__.columns)


def _rule(row: Any) -> SeatingRule:
    return SeatingRule(**row._asdict())


def _history(row: Any) -> HistoryEntry:
    return HistoryEntry(**row._asdict())


def _sqlstate(exc: Exception) -> str | None:
    return getattr(getattr(exc, "orig", None), "sqlstate", None)


class DatabaseSeatingStore:
    def __init__(self, session: AsyncSession, *, lock_timeout_ms: int = 5_000):
        self._session = session
        # A change waits at most this long for another admin's change to finish.
        self._lock_timeout_ms = int(lock_timeout_ms)

    async def _execute(self, statement):
        try:
            return await self._session.execute(statement)
        except ProgrammingError as exc:
            if _sqlstate(exc) == "42P01":  # undefined_table: migration 0005 not applied
                raise SeatingNotConfiguredError() from None
            raise
        except OperationalError as exc:
            if _sqlstate(exc) == "55P03":  # lock_not_available (lock_timeout)
                raise SeatingConflictError("The seating rule is being changed by someone else.") from None
            raise

    async def list_rules(self) -> list[SeatingRule]:
        rows = await self._execute(
            select(*_RULE_COLUMNS).order_by(ExamSeatingRule.scope, ExamSeatingRule.venue_id)
        )
        return [_rule(r) for r in rows]

    async def get_rule(self, venue_id: str | None, *, lock: bool = False) -> SeatingRule | None:
        stmt = select(*_RULE_COLUMNS)
        if venue_id is None:
            stmt = stmt.where(ExamSeatingRule.scope == "default")
        else:
            stmt = stmt.where(ExamSeatingRule.scope == "venue", ExamSeatingRule.venue_id == venue_id)
        if lock:
            await self._execute(text(f"SET LOCAL lock_timeout = {self._lock_timeout_ms}"))
            stmt = stmt.with_for_update()
        row = (await self._execute(stmt)).one_or_none()
        return _rule(row) if row else None

    async def last_version(self, venue_id: str | None) -> int:
        stmt = select(func.coalesce(func.max(ExamSeatingRuleHistory.version), 0))
        if venue_id is None:
            stmt = stmt.where(ExamSeatingRuleHistory.scope == "default")
        else:
            stmt = stmt.where(ExamSeatingRuleHistory.scope == "venue", ExamSeatingRuleHistory.venue_id == venue_id)
        return (await self._execute(stmt)).scalar_one()

    async def insert_rule(
        self, *, scope: Scope, venue_id: str | None, percentage: int, version: int, reason: str, updated_by: UUID
    ) -> SeatingRule:
        stmt = (
            insert(ExamSeatingRule)
            .values(scope=scope, venue_id=venue_id, percentage=percentage, version=version, reason=reason,
                    updated_by=updated_by)
            .returning(*_RULE_COLUMNS)
        )
        try:
            row = (await self._execute(stmt)).one()
        except IntegrityError:
            # Another request created the same override first, or the venue was removed.
            raise SeatingConflictError("The seating rule was created or changed by someone else.") from None
        return _rule(row)

    async def update_rule(
        self, rule_id: int, *, expected_version: int, percentage: int, reason: str, updated_by: UUID
    ) -> SeatingRule | None:
        stmt = (
            update(ExamSeatingRule)
            .where(ExamSeatingRule.rule_id == rule_id, ExamSeatingRule.version == expected_version)
            .values(percentage=percentage, version=expected_version + 1, reason=reason, updated_by=updated_by,
                    updated_at=func.now())
            .returning(*_RULE_COLUMNS)
        )
        row = (await self._execute(stmt)).one_or_none()
        return _rule(row) if row else None

    async def delete_rule(self, rule_id: int, *, expected_version: int) -> bool:
        stmt = (
            delete(ExamSeatingRule)
            .where(ExamSeatingRule.rule_id == rule_id, ExamSeatingRule.version == expected_version,
                   ExamSeatingRule.scope == "venue")
            .returning(ExamSeatingRule.rule_id)
        )
        return (await self._execute(stmt)).one_or_none() is not None

    async def add_history(self, **values: Any) -> HistoryEntry:
        row = (await self._execute(insert(ExamSeatingRuleHistory).values(**values).returning(*_HISTORY_COLUMNS))).one()
        return _history(row)

    async def list_history(self, *, limit: int, venue_id: str | None = None) -> list[HistoryEntry]:
        stmt = select(*_HISTORY_COLUMNS)
        if venue_id is not None:
            stmt = stmt.where(ExamSeatingRuleHistory.venue_id == venue_id)
        rows = await self._execute(stmt.order_by(ExamSeatingRuleHistory.history_id.desc()).limit(limit))
        return [_history(r) for r in rows]

    async def ruleset_version(self) -> int:
        return (await self._execute(select(func.coalesce(func.max(ExamSeatingRuleHistory.history_id), 0)))).scalar_one()

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()
