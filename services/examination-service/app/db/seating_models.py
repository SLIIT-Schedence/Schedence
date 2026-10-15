"""Examination seating rules (``examinations.exam_seating_rules`` + history).

Owned by the Examination Service (not seed data). Created by migration 0005.

* ``exam_seating_rules``: exactly one ``default`` row, plus at most one
  ``venue`` row (override) per venue. Whole percentages 1-100.
* ``exam_seating_rule_history``: append-only log of every change: who
  (Identity Service ``user_id``), when, why, old and new percentage. It has no
  foreign keys, so it outlives deleted overrides and venues.

The application never updates or deletes history rows.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

SCOPE_VALID = "(scope = 'default' AND venue_id IS NULL) OR (scope = 'venue' AND venue_id IS NOT NULL)"


class ExamSeatingRule(Base):
    __tablename__ = "exam_seating_rules"
    __table_args__ = (
        CheckConstraint("percentage BETWEEN 1 AND 100", name="percentage_range"),
        CheckConstraint(SCOPE_VALID, name="scope_valid"),
        CheckConstraint("version >= 1", name="version_positive"),
        # One override per venue (NULLs are distinct, so the default row is not affected).
        UniqueConstraint("venue_id"),
        # Exactly one default row can exist.
        Index("uq_exam_seating_rules_default", "scope", unique=True, postgresql_where=text("scope = 'default'")),
    )

    rule_id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    scope: Mapped[str] = mapped_column(String(10))
    # RESTRICT: a venue with an override cannot be deleted by a seed replacement.
    venue_id: Mapped[str | None] = mapped_column(ForeignKey("seed_venues.venue_id", ondelete="RESTRICT"))
    percentage: Mapped[int] = mapped_column(SmallInteger)
    version: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(500))
    updated_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExamSeatingRuleHistory(Base):
    __tablename__ = "exam_seating_rule_history"
    __table_args__ = (
        CheckConstraint("action IN ('create', 'update', 'delete')", name="action_valid"),
        CheckConstraint(SCOPE_VALID, name="scope_valid"),
        CheckConstraint(
            "(old_percentage IS NULL OR old_percentage BETWEEN 1 AND 100)"
            " AND (new_percentage IS NULL OR new_percentage BETWEEN 1 AND 100)",
            name="percentage_range",
        ),
        CheckConstraint(
            "(action = 'create' AND old_percentage IS NULL AND new_percentage IS NOT NULL)"
            " OR (action = 'update' AND old_percentage IS NOT NULL AND new_percentage IS NOT NULL)"
            " OR (action = 'delete' AND old_percentage IS NOT NULL AND new_percentage IS NULL)",
            name="action_values",
        ),
        Index(None, "venue_id"),
    )

    history_id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    rule_id: Mapped[int] = mapped_column(BigInteger)  # no FK: the rule may be deleted later
    action: Mapped[str] = mapped_column(String(10))
    scope: Mapped[str] = mapped_column(String(10))
    venue_id: Mapped[str | None] = mapped_column(String(16))  # no FK: history outlives venues
    old_percentage: Mapped[int | None] = mapped_column(SmallInteger)
    new_percentage: Mapped[int | None] = mapped_column(SmallInteger)
    version: Mapped[int] = mapped_column(Integer)  # rule version after the change (or the deleted version)
    changed_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)  # None = migration
    changed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reason: Mapped[str] = mapped_column(String(500))


SEATING_MODELS = (ExamSeatingRule, ExamSeatingRuleHistory)
