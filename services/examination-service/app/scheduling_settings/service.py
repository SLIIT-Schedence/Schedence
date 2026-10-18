"""Read and change the admin scheduling settings of this service's institution.

Every change:

* is validated as a complete document by the existing policy classes; hard
  rules H1-H11 have no settings and cannot be switched off;
* needs the version the caller last saw (``expected_version``; 0 = nothing
  saved yet); a different stored version means someone else changed the
  settings -> 409, nothing saved;
* locks the settings row (``SELECT ... FOR UPDATE``) and updates it only if
  the version still matches, so two admins can never overwrite each other;
* writes the settings and their history row in **one** transaction;
* with ``dry_run`` returns the same preview but writes nothing.

A stored document can become invalid (for example after a validation rule is
tightened). It is never replaced by the defaults: reads report it as
``invalid`` with its problems, the scheduler refuses to use it, and an admin
repairs it with a complete valid document and ``replace_invalid=True``. The
invalid document is kept exactly in the history.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from app.scheduling.configuration import SETTINGS_FORMAT, SchedulingConfiguration, changed_fields
from app.scheduling.contracts import SchedulingValidationError, Violation
from app.scheduling_settings.errors import (
    SchedulingSettingsConflictError,
    SchedulingSettingsInvalidError,
    SchedulingSettingsInvalidStoredError,
)
from app.scheduling_settings.store import Action, SavedSettings, SchedulingSettingsStore, SettingsHistoryEntry

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SettingsView:
    institution_id: UUID
    source: Literal["defaults", "saved"]
    status: Literal["valid", "invalid"]
    version: int  # 0 = nothing saved yet (built-in defaults)
    configuration: SchedulingConfiguration | None  # None when the stored document is invalid
    problems: tuple[Violation, ...]  # why the stored document is invalid
    saved: SavedSettings | None
    history: tuple[SettingsHistoryEntry, ...]


@dataclass(frozen=True)
class SettingsChange:
    dry_run: bool
    changed: bool
    action: Action | None  # None = nothing to change
    version: int  # version after the change (dry run: the version it would get)
    history_id: int | None  # None for dry runs and no-ops
    changed_fields: tuple[str, ...]
    configuration: SchedulingConfiguration
    changed_by: UUID | None


def check_saved(saved: SavedSettings) -> tuple[SchedulingConfiguration | None, tuple[Violation, ...]]:
    """The saved configuration, or ``None`` and every reason it cannot be used."""
    if saved.format != SETTINGS_FORMAT:
        return None, (Violation("SETTINGS", f"Saved settings use format {saved.format!r}; "
                                            f"this service supports {SETTINGS_FORMAT}."),)
    try:
        return SchedulingConfiguration.from_document(saved.settings), ()
    except SchedulingValidationError as exc:
        return None, exc.violations


class SchedulingSettingsService:
    def __init__(self, store: SchedulingSettingsStore, institution_id: UUID):
        self.store = store
        self.institution_id = institution_id

    async def configuration(self) -> SchedulingConfiguration:
        """The settings the scheduler must use: the saved settings, or the defaults if none were saved.

        Raises :class:`SchedulingSettingsInvalidStoredError` (503) if the saved document is invalid:
        the scheduler never falls back to the defaults on its own.
        """
        saved = await self.store.get(self.institution_id)
        if saved is None:
            return SchedulingConfiguration()
        configuration, problems = check_saved(saved)
        if configuration is None:
            logger.error("Saved scheduling settings (institution %s, version %s) are invalid: %d problem(s)",
                         self.institution_id, saved.version, len(problems))
            raise SchedulingSettingsInvalidStoredError()
        return configuration

    async def view(self, *, history_limit: int) -> SettingsView:
        saved = await self.store.get(self.institution_id)
        history = await self.store.list_history(self.institution_id, limit=history_limit) if history_limit else []
        configuration, problems = check_saved(saved) if saved else (SchedulingConfiguration(), ())
        return SettingsView(
            institution_id=self.institution_id,
            source="saved" if saved else "defaults",
            status="invalid" if configuration is None else "valid",
            version=saved.version if saved else 0,
            configuration=configuration,
            problems=problems,
            saved=saved,
            history=tuple(history),
        )

    async def update(self, document: object, *, reason: str, expected_version: int, user_id: UUID,
                     dry_run: bool, replace_invalid: bool = False) -> SettingsChange:
        try:
            configuration = SchedulingConfiguration.from_document(document)
        except SchedulingValidationError as exc:
            raise SchedulingSettingsInvalidError(exc.violations) from None
        try:
            change = await self._update(configuration, reason, expected_version, user_id, dry_run, replace_invalid)
            if dry_run or not change.changed:
                await self.store.rollback()
            else:
                await self.store.commit()
        except BaseException:
            await self.store.rollback()
            raise
        if change.changed and not dry_run:
            logger.info("Exam scheduling settings %s: version=%s fields=%s history=%s user=%s institution=%s",
                        change.action, change.version, ",".join(change.changed_fields), change.history_id,
                        user_id, self.institution_id)
        return change

    async def _update(self, configuration: SchedulingConfiguration, reason: str, expected_version: int,
                      user_id: UUID, dry_run: bool, replace_invalid: bool) -> SettingsChange:
        current = await self.store.get(self.institution_id, lock=not dry_run)
        current_version = current.version if current else 0
        if current_version != expected_version:
            raise SchedulingSettingsConflictError("The scheduling settings were changed by someone else.",
                                                  current_version)
        before, _ = check_saved(current) if current else (SchedulingConfiguration(), ())
        invalid = current is not None and before is None
        if invalid and not replace_invalid:
            raise SchedulingSettingsConflictError(
                "The saved scheduling settings are invalid. Review them and send replace_invalid=true to replace them.")
        if replace_invalid and not invalid:
            raise SchedulingSettingsConflictError(
                "replace_invalid=true is only allowed when the saved scheduling settings are invalid.")
        if current is not None and before == configuration:
            return SettingsChange(dry_run, False, None, current.version, None, (), configuration, None)

        new = configuration.to_document()
        action: Action = "repair" if invalid else "update" if current else "create"
        fields = tuple(changed_fields(current.settings if current else None, new))
        if dry_run:
            return SettingsChange(True, True, action, current_version + 1, None, fields, configuration, user_id)
        if current is None:
            saved = await self.store.insert(institution_id=self.institution_id, format=SETTINGS_FORMAT,
                                            settings=new, reason=reason, updated_by=user_id)
        else:
            saved = await self.store.update(current.settings_id, expected_version=current.version,
                                            format=SETTINGS_FORMAT, settings=new, reason=reason, updated_by=user_id)
            if saved is None:  # only possible without the row lock
                raise SchedulingSettingsConflictError("The scheduling settings were changed by someone else.")
        entry = await self.store.add_history(
            institution_id=self.institution_id, action=action, version=saved.version, format=SETTINGS_FORMAT,
            old_format=current.format if current else None, old_settings=current.settings if current else None,
            new_settings=new, changed_fields=list(fields), changed_by=user_id, reason=reason,
        )
        return SettingsChange(False, True, action, saved.version, entry.history_id, fields, configuration, user_id)
