"""Admin scheduling settings (``examinations.exam_scheduling_settings`` + history).

Owned by the Examination Service. Created by migration 0006, which inserts no
rows: until an admin saves settings, the built-in defaults apply (version 0).

* ``exam_scheduling_settings``: at most one row per institution. The
  institution is the one in which the admin's ``examinations:manage``
  permission was checked; no existing data is assigned an owner.
* ``exam_scheduling_settings_history``: append-only log of every change: who
  (Identity Service ``user_id``), when, why, the changed fields, and the old and
  new document. ``repair`` replaced an invalid stored document, which is kept
  here exactly as it was (``old_settings``, ``old_format``).

The settings document is validated by the application
(:class:`app.scheduling.configuration.SchedulingConfiguration`), not by SQL.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Identity, Index, Integer, String, UniqueConstraint, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ExamSchedulingSettings(Base):
    __tablename__ = "exam_scheduling_settings"
    __table_args__ = (
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("jsonb_typeof(settings) = 'object'", name="settings_object"),
        UniqueConstraint("institution_id"),
    )

    settings_id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    institution_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    format: Mapped[str] = mapped_column(String(40))
    version: Mapped[int] = mapped_column(Integer)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB)
    reason: Mapped[str] = mapped_column(String(500))
    updated_by: Mapped[uuid.UUID] = mapped_column(Uuid)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExamSchedulingSettingsHistory(Base):
    __tablename__ = "exam_scheduling_settings_history"
    __table_args__ = (
        CheckConstraint("action IN ('create', 'update', 'repair')", name="action_valid"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            "(action = 'create' AND old_settings IS NULL AND old_format IS NULL)"
            " OR (action IN ('update', 'repair') AND old_settings IS NOT NULL AND old_format IS NOT NULL)",
            name="action_values",
        ),
        CheckConstraint("jsonb_typeof(changed_fields) = 'array'", name="changed_fields_array"),
        Index(None, "institution_id"),
    )

    history_id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    institution_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    action: Mapped[str] = mapped_column(String(10))
    version: Mapped[int] = mapped_column(Integer)  # settings version after the change
    format: Mapped[str] = mapped_column(String(40))
    old_format: Mapped[str | None] = mapped_column(String(40))
    # none_as_null: store Python None as SQL NULL (not JSON 'null'), as ck_..._action_values requires for 'create'.
    old_settings: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    new_settings: Mapped[dict[str, Any]] = mapped_column(JSONB)
    changed_fields: Mapped[list[str]] = mapped_column(JSONB)
    changed_by: Mapped[uuid.UUID] = mapped_column(Uuid)
    changed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reason: Mapped[str] = mapped_column(String(500))


SCHEDULING_SETTINGS_MODELS = (ExamSchedulingSettings, ExamSchedulingSettingsHistory)
