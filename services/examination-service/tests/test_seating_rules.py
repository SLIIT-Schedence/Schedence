"""Seating rules without a database: rounding, policy, in-memory store, snapshot."""

from __future__ import annotations

import dataclasses
import datetime as dt
import math
import uuid
from fractions import Fraction

import pytest

from app.seating.memory import MemorySeatingStore
from app.seating.rules import SeatingPolicy, effective_exam_capacity
from app.seating.service import SeatingService
from app.seating.snapshot import load_capacity_snapshot
from app.seed.generator import generate_dataset
from app.sources import DatasetSource

ADMIN = uuid.uuid4()
SOURCE = DatasetSource(generate_dataset())


# --- Rounding ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("full", "percentage", "expected"),
    [
        (240, 50, 120),
        (61, 50, 30),  # 30.5 -> 30
        (99, 33, 32),  # 32.67 -> 32 (floor, never round up)
        (101, 99, 99),  # 99.99 -> 99
        (3, 34, 1),  # 1.02 -> 1
        (1, 50, 0),  # 0.5 -> 0: venue unavailable
        (1, 99, 0),
        (1, 100, 1),
        (240, 100, 240),
        (240, 1, 2),  # 2.4 -> 2
        (0, 50, 0),
        (32767, 100, 32767),  # largest SMALLINT capacity
    ],
)
def test_effective_capacity_is_floor_of_full_times_percentage(full: int, percentage: int, expected: int) -> None:
    assert effective_exam_capacity(full, percentage) == expected


def test_rounding_matches_exact_rational_floor_for_all_percentages() -> None:
    # Integer arithmetic only: no float error such as 0.29 * 100 = 28.999...
    for full in (1, 7, 29, 30, 59, 60, 100, 240, 333):
        for percentage in range(1, 101):
            assert effective_exam_capacity(full, percentage) == math.floor(Fraction(full * percentage, 100))


@pytest.mark.parametrize("percentage", [0, 101, -5, 50.0, "50", True, None])
def test_invalid_percentages_are_rejected(percentage) -> None:
    with pytest.raises(ValueError):
        effective_exam_capacity(100, percentage)


@pytest.mark.parametrize("full", [-1, 1.5, "10", True])
def test_invalid_full_capacity_is_rejected(full) -> None:
    with pytest.raises(ValueError):
        effective_exam_capacity(full, 50)


# --- Policy ----------------------------------------------------------------------------------


async def test_policy_uses_override_before_default() -> None:
    store = MemorySeatingStore()
    policy = SeatingPolicy.from_rules(await store.list_rules())
    assert policy is not None and policy.default.percentage == 50
    changed = policy.with_override("F303", 70)
    assert changed.capacity("F303", 240).exam_capacity == 168
    assert changed.capacity("F303", 240).rule_scope == "venue"
    assert changed.capacity("F304", 240).exam_capacity == 120
    assert changed.with_override("F303", None).capacity("F303", 240).rule_scope == "default"
    assert policy.with_default(10).capacity("F304", 5).available_for_exams is False


def test_policy_without_default_is_none() -> None:
    assert SeatingPolicy.from_rules([]) is None


# --- In-memory store transactions --------------------------------------------------------------


async def test_memory_store_rolls_back_uncommitted_changes() -> None:
    store = MemorySeatingStore()
    default = await store.get_rule(None)
    await store.update_rule(default.rule_id, expected_version=1, percentage=60, reason="test", updated_by=ADMIN)
    assert (await store.get_rule(None)).percentage == 60
    await store.rollback()
    assert (await store.get_rule(None)).percentage == 50
    assert store.committed_rules[0].percentage == 50


# --- Snapshot ------------------------------------------------------------------------------------


async def test_snapshot_uses_effective_capacity_and_is_frozen() -> None:
    store = MemorySeatingStore()
    service = SeatingService(store, SOURCE)
    await service.set_venue_override("F303", percentage=75, reason="Wide desks", expected_version=None,
                                     user_id=ADMIN, dry_run=False)
    fixed = dt.datetime(2026, 10, 15, tzinfo=dt.UTC)
    snapshot = await load_capacity_snapshot(SOURCE, store, clock=lambda: fixed)
    assert snapshot.exam_capacity("F303") == 180
    assert snapshot.default_percentage == 50
    assert snapshot.ruleset_version == 2
    assert len(snapshot.venues) == 62 and snapshot.taken_at == fixed
    venues = await SOURCE.list_venues()
    assert all(snapshot.exam_capacity(v.venue_id) == (180 if v.venue_id == "F303" else v.normal_capacity // 2)
               for v in venues)

    # A later change does not alter a snapshot already taken.
    await service.set_default(percentage=1, reason="Test change", expected_version=1, user_id=ADMIN, dry_run=False)
    f304 = next(v for v in venues if v.venue_id == "F304").normal_capacity
    assert snapshot.exam_capacity("F304") == f304 // 2
    later = await load_capacity_snapshot(SOURCE, store)
    assert later.fingerprint != snapshot.fingerprint and later.ruleset_version == 3
    assert later.exam_capacity("F304") == f304 // 100
    with pytest.raises(dataclasses.FrozenInstanceError):
        snapshot.venues[0].exam_capacity = 1


async def test_snapshot_fingerprint_depends_only_on_capacities() -> None:
    first = await load_capacity_snapshot(SOURCE, MemorySeatingStore())
    second = await load_capacity_snapshot(SOURCE, MemorySeatingStore())
    assert first.fingerprint == second.fingerprint


async def test_snapshot_lists_unavailable_venues() -> None:
    store = MemorySeatingStore(default_percentage=1)
    snapshot = await load_capacity_snapshot(SOURCE, store)
    unavailable = {v.venue_id for v in snapshot.venues if not v.available_for_exams}
    assert unavailable == {v.venue_id for v in await SOURCE.list_venues() if v.normal_capacity < 100}
    assert set(v.venue_id for v in snapshot.available_venues()).isdisjoint(unavailable)



# --- No fixed 50% rule in the scheduler input ---------------------------------------------------------------


class _WrongSeedCapacity:
    """The dataset, but every venue's stored exam_capacity is nonsense."""

    async def list_venues(self, *, venue_type=None):
        return [dataclasses.replace(v, exam_capacity=1) for v in await SOURCE.list_venues(venue_type=venue_type)]


async def test_snapshot_ignores_the_stored_seed_capacity() -> None:
    store = MemorySeatingStore(default_percentage=37)
    normal = await load_capacity_snapshot(SOURCE, store)
    wrong = await load_capacity_snapshot(_WrongSeedCapacity(), store)
    assert normal.venues == wrong.venues and normal.fingerprint == wrong.fingerprint
    assert all(v.exam_capacity == v.normal_capacity * 37 // 100 for v in normal.venues)
    assert normal.default_percentage == 37


@pytest.mark.parametrize("percentage", [1, 25, 50, 66, 100])
async def test_snapshot_follows_any_default(percentage: int) -> None:
    snapshot = await load_capacity_snapshot(SOURCE, MemorySeatingStore(default_percentage=percentage))
    assert all(v.exam_capacity == v.normal_capacity * percentage // 100 for v in snapshot.venues)
    assert {v.venue_id for v in snapshot.available_venues()} == {
        v.venue_id for v in snapshot.venues if v.normal_capacity * percentage >= 100}
