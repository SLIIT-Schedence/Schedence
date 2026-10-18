"""Admin scheduling settings: starts, breaks, rooms, solver, S1-S5 weights and fairness.

GET requires ``examinations:read``; PUT requires ``examinations:manage`` in this
service's institution. Hard rules H1-H11 are always enforced and are listed in
every response; no setting can switch them off.

An invalid saved document is reported by GET (``status: invalid`` and its
problems), never replaced by the defaults. PUT repairs it only with a complete
valid document and ``replace_invalid=true``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import SchedulingSettingsReaderDep, SchedulingSettingsWriterDep
from app.api.schemas import (
    SchedulingSettingsChangeOut,
    SchedulingSettingsHistoryOut,
    SchedulingSettingsOut,
    SetSchedulingSettingsIn,
    SettingsProblemOut,
)
from app.auth.dependencies import require_exam_data_access, require_exam_settings_admin
from app.auth.principal import Principal
from app.scheduling.configuration import MANDATORY_RULES, SETTINGS_FORMAT

router = APIRouter(prefix="/settings/scheduling", tags=["settings"])

SettingsAdmin = Annotated[Principal, Depends(require_exam_settings_admin)]
DryRun = Annotated[bool, Query(description="Validate and preview the change without saving it")]
ReplaceInvalid = Annotated[bool, Query(
    description="Required (and only allowed) when the saved settings are invalid: replace them with this document")]


@router.get("", response_model=SchedulingSettingsOut, dependencies=[Depends(require_exam_data_access)])
async def get_scheduling_settings(
    service: SchedulingSettingsReaderDep,
    history_limit: Annotated[int, Query(ge=0, le=500, description="Newest history rows to return")] = 20,
) -> SchedulingSettingsOut:
    """Current settings (built-in defaults until an admin saves them), validity, mandatory rules and history."""
    view = await service.view(history_limit=history_limit)
    saved, configuration = view.saved, view.configuration
    return SchedulingSettingsOut(
        institution_id=view.institution_id,
        source=view.source,
        status=view.status,
        problems=[SettingsProblemOut(rule=p.constraint, message=p.message) for p in view.problems],
        version=view.version,
        format=saved.format if saved else SETTINGS_FORMAT,
        fingerprint=configuration.fingerprint if configuration else None,
        reason=saved.reason if saved else None,
        updated_by=saved.updated_by if saved else None,
        updated_at=saved.updated_at if saved else None,
        settings=configuration.to_document() if configuration else saved.settings,
        mandatory_rules=list(MANDATORY_RULES),
        history=[
            SchedulingSettingsHistoryOut(
                history_id=h.history_id, action=h.action, version=h.version, changed_fields=h.changed_fields,
                changed_by=h.changed_by, changed_at=h.changed_at, reason=h.reason,
            )
            for h in view.history
        ],
    )


@router.put("", response_model=SchedulingSettingsChangeOut)
async def set_scheduling_settings(
    admin: SettingsAdmin, body: SetSchedulingSettingsIn, service: SchedulingSettingsWriterDep,
    dry_run: DryRun = False, replace_invalid: ReplaceInvalid = False,
) -> SchedulingSettingsChangeOut:
    """Replace the settings with a complete, validated document (``dry_run`` = validate and preview only)."""
    change = await service.update(body.settings, reason=body.reason, expected_version=body.expected_version,
                                  user_id=admin.user_id, dry_run=dry_run, replace_invalid=replace_invalid)
    return SchedulingSettingsChangeOut(
        dry_run=change.dry_run, changed=change.changed, action=change.action, version=change.version,
        history_id=change.history_id, changed_fields=list(change.changed_fields), changed_by=change.changed_by,
        fingerprint=change.configuration.fingerprint, settings=change.configuration.to_document(),
    )
