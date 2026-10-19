"""Express venue-type capacity rules through Member 2's existing seating configuration.

Member 2's seating rules have one default percentage plus per-venue overrides; there is no
per-venue-type rule. This turns CapacityRules into per-venue override calls on Member 2's
public SeatingService.set_venue_override, so their scheduler can plan with the same
capacities. dry_run=True (the default) only reports what would change. Against the real
database a non-dry run writes Member 2's seating rules (with their history and versioning),
so it needs the owner's agreement; in-memory stores are safe for offline runs and tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.duty_allocation.student_seating.policy import CapacityRules
from app.seating.service import SeatingChange, SeatingService


@dataclass(frozen=True)
class CapacityOverridePlan:
    changes: tuple[SeatingChange, ...]  # one per venue whose percentage differs
    unconfigured: tuple[tuple[str, str], ...]  # (venue_id, venue_type) with no configured percentage


async def apply_capacity_rules(service: SeatingService, rules: CapacityRules, *, reason: str, user_id: UUID,
                               dry_run: bool = True) -> CapacityOverridePlan:
    policy = await service.policy()
    changes, unconfigured = [], []
    for venue, current in await service.venues():
        percentage = rules.percentage(venue.venue_type)
        if percentage is None:
            unconfigured.append((venue.venue_id, venue.venue_type))
            continue
        if current.percentage == percentage:
            continue
        override = policy.overrides.get(venue.venue_id)
        changes.append(await service.set_venue_override(
            venue.venue_id, percentage=percentage, reason=reason,
            expected_version=override.version if override else None, user_id=user_id, dry_run=dry_run))
    return CapacityOverridePlan(tuple(changes), tuple(sorted(unconfigured)))
