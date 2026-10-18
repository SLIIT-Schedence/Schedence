"""Storage of the admin scheduling settings and their history.

:class:`SchedulingSettingsStore` is the interface; :class:`DatabaseSchedulingSettingsStore`
implements it on the ``examinations.exam_scheduling_settings*`` tables. The caller
owns the session and its transaction; only :meth:`commit` / :meth:`rollback` end it.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Literal, Protocol
from uuid import UUID

from sqlalchemy import insert, select, text, update
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from app.db.scheduling_settings_models import ExamSchedulingSettings, ExamSchedulingSettingsHistory
from app.scheduling_settings.errors import SchedulingSettingsConflictError, SchedulingSettingsNotConfiguredError

Action = Literal["create", "update", "repair"]  # repair = replaced an invalid stored document


@dataclass(frozen=True)
class SavedSettings:
    settings_id: int
    institution_id: UUID
    format: str
    version: int
    settings: dict[str, Any]
    reason: str
    updated_by: UUID
    updated_at: dt.datetime


@dataclass(frozen=True)
class SettingsHistoryEntry:
    history_id: int
    institution_id: UUID
    action: Action
    version: int
    format: str
    old_format: str | None
    old_settings: dict[str, Any] | None  # exactly as stored before the change
    new_settings: dict[str, Any]
    changed_fields: list[str]
    changed_by: UUID
    changed_at: dt.datetime
    reason: str


class SchedulingSettingsStore(Protocol):
    async def get(self, institution_id: UUID, *, lock: bool = False) -> SavedSettings | None:
        """The institution's saved settings, or ``None``. ``lock`` = SELECT ... FOR UPDATE."""

    async def insert(self, *, institution_id: UUID, format: str, settings: dict[str, Any], reason: str,
                     updated_by: UUID) -> SavedSettings:
        """Version 1. Raises :class:`SchedulingSettingsConflictError` if the institution already has settings."""

    async def update(self, settings_id: int, *, expected_version: int, format: str, settings: dict[str, Any],
                     reason: str, updated_by: UUID) -> SavedSettings | None:
        """``None`` when the stored version is not ``expected_version``."""

    async def add_history(self, *, institution_id: UUID, action: Action, version: int, format: str,
                          old_format: str | None, old_settings: dict[str, Any] | None, new_settings: dict[str, Any],
                          changed_fields: list[str], changed_by: UUID, reason: str) -> SettingsHistoryEntry: ...

    async def list_history(self, institution_id: UUID, *, limit: int) -> list[SettingsHistoryEntry]:
        """Newest first."""

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


_SETTINGS_COLUMNS = tuple(ExamSchedulingSettings.__table__.columns)
_HISTORY_COLUMNS = tuple(ExamSchedulingSettingsHistory.__table__.columns)


def _sqlstate(exc: Exception) -> str | None:
    return getattr(getattr(exc, "orig", None), "sqlstate", None)


class DatabaseSchedulingSettingsStore:
    def __init__(self, session: AsyncSession, *, lock_timeout_ms: int = 5_000):
        self._session = session
        # A change waits at most this long for another admin's change to finish.
        self._lock_timeout_ms = int(lock_timeout_ms)

    async def _execute(self, statement):
        try:
            return await self._session.execute(statement)
        except ProgrammingError as exc:
            if _sqlstate(exc) == "42P01":  # undefined_table: migration 0006 not applied
                raise SchedulingSettingsNotConfiguredError() from None
            raise
        except OperationalError as exc:
            if _sqlstate(exc) == "55P03":  # lock_not_available (lock_timeout)
                raise SchedulingSettingsConflictError("The settings are being changed by someone else.") from None
            raise

    async def get(self, institution_id: UUID, *, lock: bool = False) -> SavedSettings | None:
        stmt = select(*_SETTINGS_COLUMNS).where(ExamSchedulingSettings.institution_id == institution_id)
        if lock:
            await self._execute(text(f"SET LOCAL lock_timeout = {self._lock_timeout_ms}"))
            stmt = stmt.with_for_update()
        row = (await self._execute(stmt)).one_or_none()
        return SavedSettings(**row._asdict()) if row else None

    async def insert(self, *, institution_id: UUID, format: str, settings: dict[str, Any], reason: str,
                     updated_by: UUID) -> SavedSettings:
        stmt = (
            insert(ExamSchedulingSettings)
            .values(institution_id=institution_id, format=format, version=1, settings=settings, reason=reason,
                    updated_by=updated_by)
            .returning(*_SETTINGS_COLUMNS)
        )
        try:
            row = (await self._execute(stmt)).one()
        except IntegrityError:
            # Another admin saved the first settings at the same time.
            raise SchedulingSettingsConflictError("The settings were created by someone else.") from None
        return SavedSettings(**row._asdict())

    async def update(self, settings_id: int, *, expected_version: int, format: str, settings: dict[str, Any],
                     reason: str, updated_by: UUID) -> SavedSettings | None:
        stmt = (
            update(ExamSchedulingSettings)
            .where(ExamSchedulingSettings.settings_id == settings_id,
                   ExamSchedulingSettings.version == expected_version)
            .values(format=format, settings=settings, version=expected_version + 1, reason=reason,
                    updated_by=updated_by, updated_at=func.now())
            .returning(*_SETTINGS_COLUMNS)
        )
        row = (await self._execute(stmt)).one_or_none()
        return SavedSettings(**row._asdict()) if row else None

    async def add_history(self, **values: Any) -> SettingsHistoryEntry:
        stmt = insert(ExamSchedulingSettingsHistory).values(**values).returning(*_HISTORY_COLUMNS)
        return SettingsHistoryEntry(**(await self._execute(stmt)).one()._asdict())

    async def list_history(self, institution_id: UUID, *, limit: int) -> list[SettingsHistoryEntry]:
        stmt = (
            select(*_HISTORY_COLUMNS)
            .where(ExamSchedulingSettingsHistory.institution_id == institution_id)
            .order_by(ExamSchedulingSettingsHistory.history_id.desc())
            .limit(limit)
        )
        return [SettingsHistoryEntry(**row._asdict()) for row in await self._execute(stmt)]

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()
