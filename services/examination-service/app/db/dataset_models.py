"""Which institution owns the imported examination dataset (``examinations.exam_dataset_owner``).

Owned by the Examination Service. Created by migration 0012.

The seed tables (modules, students, registrations, venues, exam periods) and the
seating rules hold one imported dataset with global keys (``module_code``,
``period_code``, ``venue_id``). This table names the one institution allowed to
read and change it. It has at most one row; without a row no institution may use
the dataset (deny by default). Saved timetables and scheduling settings are
already stored per institution and do not use this table.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import Boolean, CheckConstraint, DateTime, String, Uuid, func, true
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ExamDatasetOwner(Base):
    __tablename__ = "exam_dataset_owner"
    __table_args__ = (CheckConstraint("singleton", name="single_row"),)

    singleton: Mapped[bool] = mapped_column(Boolean, primary_key=True, server_default=true())
    institution_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    reason: Mapped[str] = mapped_column(String(500))
    assigned_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


DATASET_MODELS = (ExamDatasetOwner,)
