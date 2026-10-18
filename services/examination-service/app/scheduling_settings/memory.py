"""In-memory ``SchedulingSettingsStore`` for tests and offline tools.

Behaves like the database store, including transactions: changes are visible
only to this store until :meth:`commit`; :meth:`rollback` throws them away.
Starts empty, like migration 0006 (the built-in defaults apply).
"""

from __future__ import annotations

import copy
import datetime as dt
from dataclasses import dataclass, field, replace
from typing import Any
from uuid import UUID

from app.scheduling_settings.errors import SchedulingSettingsConflictError
from app.scheduling_settings.store import Action, SavedSettings, SettingsHistoryEntry


@dataclass
class _State:
    settings: dict[UUID, SavedSettings] = field(default_factory=dict)
    history: list[SettingsHistoryEntry] = field(default_factory=list)
    next_settings_id: int = 1
    next_history_id: int = 1


class MemorySchedulingSettingsStore:
    def __init__(self, *, clock=lambda: dt.datetime.now(dt.UTC)):
        self._clock = clock
        self._committed = _State()
        self._work = copy.deepcopy(self._committed)
        self.commits = 0
        self.fail_on: str | None = None  # name of a method that raises, to test rollback

    def _maybe_fail(self, name: str) -> None:
        if self.fail_on == name:
            raise RuntimeError(f"simulated failure in {name}")

    async def get(self, institution_id: UUID, *, lock: bool = False) -> SavedSettings | None:
        return self._work.settings.get(institution_id)

    async def insert(self, *, institution_id: UUID, format: str, settings: dict[str, Any], reason: str,
                     updated_by: UUID) -> SavedSettings:
        self._maybe_fail("insert")
        if institution_id in self._work.settings:
            raise SchedulingSettingsConflictError("The settings were created by someone else.")
        saved = SavedSettings(self._work.next_settings_id, institution_id, format, 1, copy.deepcopy(settings),
                              reason, updated_by, self._clock())
        self._work.next_settings_id += 1
        self._work.settings[institution_id] = saved
        return saved

    async def update(self, settings_id: int, *, expected_version: int, format: str, settings: dict[str, Any],
                     reason: str, updated_by: UUID) -> SavedSettings | None:
        self._maybe_fail("update")
        current = next((s for s in self._work.settings.values() if s.settings_id == settings_id), None)
        if current is None or current.version != expected_version:
            return None
        saved = replace(current, format=format, settings=copy.deepcopy(settings), version=expected_version + 1,
                        reason=reason, updated_by=updated_by, updated_at=self._clock())
        self._work.settings[current.institution_id] = saved
        return saved

    async def add_history(self, *, institution_id: UUID, action: Action, version: int, format: str,
                          old_format: str | None, old_settings: dict[str, Any] | None, new_settings: dict[str, Any],
                          changed_fields: list[str], changed_by: UUID, reason: str) -> SettingsHistoryEntry:
        self._maybe_fail("add_history")
        entry = SettingsHistoryEntry(self._work.next_history_id, institution_id, action, version, format, old_format,
                                     copy.deepcopy(old_settings), copy.deepcopy(new_settings), list(changed_fields),
                                     changed_by, self._clock(), reason)
        self._work.next_history_id += 1
        self._work.history.append(entry)
        return entry

    async def list_history(self, institution_id: UUID, *, limit: int) -> list[SettingsHistoryEntry]:
        return [h for h in reversed(self._work.history) if h.institution_id == institution_id][:limit]

    async def commit(self) -> None:
        self._maybe_fail("commit")
        self._committed = self._work
        self.commits += 1
        self._work = copy.deepcopy(self._committed)

    async def rollback(self) -> None:
        self._work = copy.deepcopy(self._committed)
