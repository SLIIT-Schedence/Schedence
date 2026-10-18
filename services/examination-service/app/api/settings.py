"""Examination settings: configurable seating capacity.

Reading needs ``examinations:read``. Changing needs ``examinations:manage``.
Every change needs the version last read (409 if it changed), a reason,
and is kept in the history.
``dry_run=true`` returns the same preview and saves nothing.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from app.api.deps import VENUE_ID, SeatingReaderDep, SeatingWriterDep
from app.api.schemas import (
    REASON_MAX,
    REASON_MIN,
    REASON_PATTERN,
    ExamSeatingSettingsOut,
    SeatingChangeOut,
    SeatingHistoryOut,
    SeatingRuleOut,
    SetDefaultSeatingIn,
    SetVenueSeatingIn,
)
from app.auth.dependencies import require_exam_data_access, require_exam_settings_admin
from app.auth.principal import Principal
from app.seating.service import SeatingChange

router = APIRouter(prefix="/settings/exam-seating", tags=["settings"])

SettingsAdmin = Annotated[Principal, Depends(require_exam_settings_admin)]
DryRun = Annotated[bool, Query(description="Preview the change without saving it")]
VenueIdPath = Annotated[str, Path(pattern=VENUE_ID)]


def _change_out(change: SeatingChange) -> SeatingChangeOut:
    return SeatingChangeOut.model_validate(change)


@router.get("", response_model=ExamSeatingSettingsOut, dependencies=[Depends(require_exam_data_access)])
async def get_exam_seating(
    service: SeatingReaderDep,
    history_limit: Annotated[int, Query(ge=0, le=500, description="Newest history rows to return")] = 20,
) -> ExamSeatingSettingsOut:
    """Default rule, venue overrides, totals and recent history."""
    overview = await service.overview(history_limit=history_limit)
    capacities = [c for _, c in overview.venues]
    return ExamSeatingSettingsOut(
        ruleset_version=overview.ruleset_version,
        default=SeatingRuleOut.model_validate(overview.policy.default),
        venue_overrides=[
            SeatingRuleOut.model_validate(r) for r in sorted(overview.policy.overrides.values(), key=lambda r: r.venue_id)
        ],
        venue_count=len(capacities),
        total_normal_capacity=sum(c.normal_capacity for c in capacities),
        total_exam_capacity=sum(c.exam_capacity for c in capacities),
        unavailable_venues=[c.venue_id for c in capacities if not c.available_for_exams],
        history=[SeatingHistoryOut.model_validate(h) for h in overview.history],
    )


@router.put("/default", response_model=SeatingChangeOut)
async def set_default_exam_seating(
    admin: SettingsAdmin, body: SetDefaultSeatingIn, service: SeatingWriterDep, dry_run: DryRun = False
) -> SeatingChangeOut:
    """Change the default percentage (used by every venue without an override)."""
    return _change_out(await service.set_default(
        percentage=body.percentage, reason=body.reason, expected_version=body.expected_version,
        user_id=admin.user_id, dry_run=dry_run,
    ))


@router.put("/venues/{venue_id}", response_model=SeatingChangeOut)
async def set_venue_exam_seating(
    admin: SettingsAdmin, venue_id: VenueIdPath, body: SetVenueSeatingIn, service: SeatingWriterDep,
    dry_run: DryRun = False,
) -> SeatingChangeOut:
    """Create (``expected_version: null``) or change one venue's override."""
    return _change_out(await service.set_venue_override(
        venue_id, percentage=body.percentage, reason=body.reason, expected_version=body.expected_version,
        user_id=admin.user_id, dry_run=dry_run,
    ))


@router.delete("/venues/{venue_id}", response_model=SeatingChangeOut)
async def delete_venue_exam_seating(
    admin: SettingsAdmin,
    venue_id: VenueIdPath,
    service: SeatingWriterDep,
    expected_version: Annotated[int, Query(ge=1, le=2_147_483_647, description="Override version last read")],
    reason: Annotated[str, Query(min_length=REASON_MIN, max_length=REASON_MAX, pattern=REASON_PATTERN)],
    dry_run: DryRun = False,
) -> SeatingChangeOut:
    """Remove one venue's override; the venue then uses the default again."""
    return _change_out(await service.delete_venue_override(
        venue_id, reason=reason, expected_version=expected_version, user_id=admin.user_id, dry_run=dry_run,
    ))
