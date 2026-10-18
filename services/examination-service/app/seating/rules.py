"""Examination seating rules: pure logic, no database.

Only part of a venue may be used in an exam. The share is a whole percentage
(1-100): one default for every venue, plus optional per-venue overrides.

    effective exam capacity = floor(full capacity x percentage / 100)

Integer arithmetic only (no floats), so 99 seats at 33% is always 32. A venue
whose effective capacity is 0 cannot be used for examinations.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import Literal
from uuid import UUID

MIN_PERCENTAGE = 1
MAX_PERCENTAGE = 100
INITIAL_DEFAULT_PERCENTAGE = 50

Scope = Literal["default", "venue"]
Action = Literal["create", "update", "delete"]


def effective_exam_capacity(full_capacity: int, percentage: int) -> int:
    """floor(full_capacity x percentage / 100) for whole numbers."""
    if isinstance(full_capacity, bool) or not isinstance(full_capacity, int) or full_capacity < 0:
        raise ValueError("full capacity must be a whole number >= 0")
    check_percentage(percentage)
    return full_capacity * percentage // 100


def check_percentage(percentage: int) -> int:
    if isinstance(percentage, bool) or not isinstance(percentage, int):
        raise ValueError("percentage must be a whole number")
    if not MIN_PERCENTAGE <= percentage <= MAX_PERCENTAGE:
        raise ValueError(f"percentage must be between {MIN_PERCENTAGE} and {MAX_PERCENTAGE}")
    return percentage


@dataclass(frozen=True)
class SeatingRule:
    """One row of ``examinations.exam_seating_rules``."""

    rule_id: int
    scope: Scope
    venue_id: str | None
    percentage: int
    # Grows by one on every change and never restarts for a venue, so an old
    # version number can never match a newer rule (no lost updates).
    version: int
    reason: str
    updated_by: UUID | None  # Identity Service user_id; None = set by a migration
    updated_at: dt.datetime


@dataclass(frozen=True)
class HistoryEntry:
    """One row of ``examinations.exam_seating_rule_history`` (append-only)."""

    history_id: int
    rule_id: int
    action: Action
    scope: Scope
    venue_id: str | None
    old_percentage: int | None
    new_percentage: int | None
    version: int
    changed_by: UUID | None
    changed_at: dt.datetime
    reason: str


@dataclass(frozen=True)
class VenueCapacity:
    """A venue with the seating rule that applies to it."""

    venue_id: str
    normal_capacity: int
    percentage: int
    rule_scope: Scope
    exam_capacity: int

    @property
    def available_for_exams(self) -> bool:
        return self.exam_capacity > 0


@dataclass(frozen=True)
class SeatingPolicy:
    default: SeatingRule
    overrides: Mapping[str, SeatingRule] = field(default_factory=dict)

    @classmethod
    def from_rules(cls, rules: Iterable[SeatingRule]) -> SeatingPolicy | None:
        """``None`` when no default rule exists (migration 0005 not applied)."""
        rules = list(rules)
        default = next((r for r in rules if r.scope == "default"), None)
        if default is None:
            return None
        return cls(default, {r.venue_id: r for r in rules if r.scope == "venue" and r.venue_id})

    def rule_for(self, venue_id: str) -> SeatingRule:
        return self.overrides.get(venue_id, self.default)

    def capacity(self, venue_id: str, normal_capacity: int) -> VenueCapacity:
        rule = self.rule_for(venue_id)
        return VenueCapacity(
            venue_id=venue_id,
            normal_capacity=normal_capacity,
            percentage=rule.percentage,
            rule_scope=rule.scope,
            exam_capacity=effective_exam_capacity(normal_capacity, rule.percentage),
        )

    def with_default(self, percentage: int) -> SeatingPolicy:
        return SeatingPolicy(replace(self.default, percentage=percentage), self.overrides)

    def with_override(self, venue_id: str, percentage: int | None) -> SeatingPolicy:
        """``percentage=None`` removes the override."""
        overrides = dict(self.overrides)
        if percentage is None:
            overrides.pop(venue_id, None)
        else:
            base = overrides.get(venue_id) or replace(self.default, rule_id=0, scope="venue", venue_id=venue_id)
            overrides[venue_id] = replace(base, percentage=percentage)
        return SeatingPolicy(self.default, overrides)
