"""A frozen, consistent view of every venue's exam capacity for one scheduler run.

The future CP-SAT scheduler must not read seating rules while it solves: an
admin may change them at any time. Instead it takes one snapshot first and
uses only that:

    async with snapshot_session(engine) as session:
        snapshot = await load_capacity_snapshot(DatabaseSource(session), DatabaseSeatingStore(session))
    capacity = snapshot.exam_capacity("F1301")

``snapshot_session`` opens a REPEATABLE READ, READ ONLY transaction, so the
venues and the rules come from the same moment. ``ruleset_version`` and
``fingerprint`` can be stored with the run to show which capacities it used.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.seating.errors import SeatingNotConfiguredError
from app.seating.rules import Scope, SeatingPolicy, check_percentage, effective_exam_capacity
from app.seating.store import SeatingRuleStore
from app.sources import ExamDataSource


@dataclass(frozen=True)
class SnapshotVenue:
    venue_id: str
    venue_type: str
    normal_capacity: int
    percentage: int
    rule_scope: Scope
    exam_capacity: int

    @property
    def available_for_exams(self) -> bool:
        return self.exam_capacity > 0


@dataclass(frozen=True)
class CapacitySnapshot:
    ruleset_version: int
    taken_at: dt.datetime
    default_percentage: int
    venues: tuple[SnapshotVenue, ...]
    # SHA-256 of the capacities only: equal fingerprints = identical capacities.
    fingerprint: str
    # Only populated for per-timetable seating; keeps source changes detectable.
    source_fingerprint: str | None = None

    def exam_capacity(self, venue_id: str) -> int:
        """Effective exam capacity; ``KeyError`` for a venue not in the snapshot."""
        return self._by_id()[venue_id].exam_capacity

    def available_venues(self) -> tuple[SnapshotVenue, ...]:
        """Venues the scheduler may use (effective capacity > 0)."""
        return tuple(v for v in self.venues if v.available_for_exams)

    def _by_id(self) -> dict[str, SnapshotVenue]:
        return {v.venue_id: v for v in self.venues}


def _fingerprint(venues: tuple[SnapshotVenue, ...]) -> str:
    rows = [[v.venue_id, v.venue_type, v.normal_capacity, v.percentage, v.rule_scope, v.exam_capacity] for v in venues]
    return hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode()).hexdigest()


async def load_capacity_snapshot(
    source: ExamDataSource,
    store: SeatingRuleStore,
    *,
    clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
    preferences: dict | None = None,
) -> CapacitySnapshot:
    """Read venues and rules once. Use one transaction for both (see ``snapshot_session``)."""
    policy = SeatingPolicy.from_rules(await store.list_rules())
    if policy is None:
        raise SeatingNotConfiguredError()
    ruleset_version = await store.ruleset_version()
    venues = tuple(
        SnapshotVenue(
            venue_id=v.venue_id,
            venue_type=v.venue_type,
            normal_capacity=c.normal_capacity,
            percentage=c.percentage,
            rule_scope=c.rule_scope,
            exam_capacity=c.exam_capacity,
        )
        for v in sorted(await source.list_venues(), key=lambda v: v.venue_id)
        for c in (policy.capacity(v.venue_id, v.normal_capacity),)
    )
    source_fingerprint = None
    if preferences is not None:
        percentage = check_percentage(preferences.get("default_percentage", policy.default.percentage))
        source_fingerprint = _fingerprint(venues)
        venues = tuple(replace(v, percentage=percentage, exam_capacity=effective_exam_capacity(v.normal_capacity, percentage))
                       if v.rule_scope == "default" else v for v in venues)
    return CapacitySnapshot(
        ruleset_version=ruleset_version,
        taken_at=clock(),
        default_percentage=policy.default.percentage if preferences is None else percentage,
        venues=venues,
        fingerprint=_fingerprint(venues),
        source_fingerprint=source_fingerprint,
    )


@asynccontextmanager
async def snapshot_session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """One REPEATABLE READ, READ ONLY transaction: every query sees the same data."""
    bind = engine.execution_options(isolation_level="REPEATABLE READ", postgresql_readonly=True)
    async with AsyncSession(bind=bind, expire_on_commit=False) as session:
        try:
            yield session
        finally:
            await session.rollback()
