"""Venue-type capacity rules applied through Member 2's public SeatingService.

Uses Member 2's in-memory store and synthetic dataset only: no database is touched.
"""

from __future__ import annotations

from uuid import UUID

import pytest

from app.duty_allocation.student_seating import PROJECT_CAPACITY_RULES, CapacityRules, apply_capacity_rules
from app.seating.memory import MemorySeatingStore
from app.seating.service import SeatingService
from app.seed.generator import generate_dataset
from app.sources import DatasetSource

USER = UUID(int=1)


@pytest.fixture
def service():
    return SeatingService(MemorySeatingStore(default_percentage=50), DatasetSource(generate_dataset()))


async def test_dry_run_reports_lab_overrides_without_changing_rules(service):
    before = await service.policy()
    plan = await apply_capacity_rules(service, PROJECT_CAPACITY_RULES, reason="test", user_id=USER)
    venues = await service.venues()
    labs = {v.venue_id for v, _ in venues if v.venue_type == "computer_lab"}
    assert {c.venue_id for c in plan.changes} == labs  # lecture halls already at the 50% default
    assert all(c.dry_run and c.new_percentage == 90 for c in plan.changes)
    assert await service.policy() == before
    assert {t for _, t in plan.unconfigured} == {"smart_classroom", "byod_lab"}


async def test_applied_overrides_give_member2_the_configured_capacities(service):
    await apply_capacity_rules(service, PROJECT_CAPACITY_RULES, reason="test", user_id=USER, dry_run=False)
    for venue, capacity in await service.venues():
        expected = PROJECT_CAPACITY_RULES.exam_capacity(venue.venue_type, venue.normal_capacity)
        if expected is None:
            assert capacity.percentage == 50  # unconfigured types keep Member 2's default
        else:
            assert capacity.exam_capacity == expected


async def test_reapplying_is_a_no_op_and_existing_overrides_are_updated(service):
    await apply_capacity_rules(service, PROJECT_CAPACITY_RULES, reason="test", user_id=USER, dry_run=False)
    assert (await apply_capacity_rules(service, PROJECT_CAPACITY_RULES, reason="again", user_id=USER)).changes == ()
    lower = CapacityRules((("computer_lab", 80), ("lecture_hall", 50)), "test")
    plan = await apply_capacity_rules(service, lower, reason="lower", user_id=USER, dry_run=False)
    assert plan.changes and all(c.old_percentage == 90 and c.new_percentage == 80 for c in plan.changes)
