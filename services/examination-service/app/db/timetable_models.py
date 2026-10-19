"""Saved examination timetables (``examinations.exam_timetable*``), migration 0010.

Only a timetable that the solver produced (OPTIMAL or FEASIBLE) and that passed
the independent H1-H11 validation is saved, always as a new ``DRAFT``. Every
save gets the next ``draft_number`` of its institution and examination cycle, so
older drafts are never overwritten.

* ``exam_timetables``: one row per saved timetable: cycle, MID/FINAL kind,
  solver status and run, input fingerprint, scheduling settings version, seating
  rule version, validation result and summaries. The approval and publication
  columns are prepared for a later workflow and stay empty for drafts.
* ``exam_timetable_sessions`` / ``_rooms`` / ``_seats``: the sessions, the venues
  of each session and the individual logical seat of every candidate.
* ``exam_timetable_history`` (0011): append-only log of every status change.

Workflow (0011): DRAFT -> APPROVED -> PUBLISHED -> ARCHIVED. Database triggers
(migration 0011, not modelled here) keep the saved content fixed, allow only
these status changes, and lock sessions, rooms and seats once a timetable is no
longer a DRAFT. ``content_fingerprint`` is checked again before every change.

Module, venue and period names are copied (not foreign keys): the seed tables
are temporary and may be replaced, but a saved timetable must not change.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

TIMETABLE_STATUSES = ("DRAFT", "APPROVED", "PUBLISHED", "ARCHIVED")
# Allowed status changes and their history action.
STATUS_CHANGES = {("DRAFT", "APPROVED"): "approve", ("APPROVED", "PUBLISHED"): "publish",
                  ("PUBLISHED", "ARCHIVED"): "archive"}
HISTORY_ACTIONS = ("create", *STATUS_CHANGES.values())
SAVED_SOLVER_STATUSES = ("OPTIMAL", "FEASIBLE")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


STATUS_COLUMNS_CHECK = (
    "(status = 'DRAFT' AND approved_at IS NULL AND approved_by IS NULL"
    " AND published_at IS NULL AND published_by IS NULL)"
    " OR (status = 'APPROVED' AND approved_at IS NOT NULL AND approved_by IS NOT NULL"
    " AND published_at IS NULL AND published_by IS NULL)"
    " OR (status = 'PUBLISHED' AND approved_at IS NOT NULL AND approved_by IS NOT NULL"
    " AND published_at IS NOT NULL AND published_by IS NOT NULL)"
    " OR status = 'ARCHIVED'"
)


class ExamTimetable(Base):
    __tablename__ = "exam_timetables"
    __table_args__ = (
        UniqueConstraint("institution_id", "period_code", "draft_number"),
        ForeignKeyConstraint(["source_timetable_id"], ["exam_timetables.timetable_id"], ondelete="RESTRICT"),
        CheckConstraint(_in("status", TIMETABLE_STATUSES), name="status_valid"),
        CheckConstraint(STATUS_COLUMNS_CHECK, name="status_columns"),
        CheckConstraint(_in("exam_kind", ("MID", "FINAL")), name="exam_kind_valid"),
        CheckConstraint(_in("mode", ("feasibility", "optimized")), name="mode_valid"),
        CheckConstraint(_in("selection", ("full_cycle", "partial_cycle", "listed_modules")), name="selection_valid"),
        CheckConstraint(_in("solver_status", SAVED_SOLVER_STATUSES), name="solver_status_valid"),
        CheckConstraint("validation_passed", name="validation_passed"),
        CheckConstraint("draft_number >= 1", name="draft_number_positive"),
        CheckConstraint("settings_version >= 0", name="settings_version_valid"),
        CheckConstraint("seating_ruleset_version >= 0", name="seating_ruleset_version_valid"),
        CheckConstraint("revision >= 1", name="revision_positive"),
        # At most one published timetable per examination cycle (for the later Publish step).
        Index("ux_exam_timetables_one_published", "institution_id", "period_code", unique=True,
              postgresql_where=text("status = 'PUBLISHED'")),
    )

    timetable_id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    institution_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    period_code: Mapped[str] = mapped_column(String(32))
    period_name: Mapped[str] = mapped_column(String(120))
    exam_kind: Mapped[str] = mapped_column(String(5))
    draft_number: Mapped[int] = mapped_column(Integer)  # 1, 2, 3 ... per institution and cycle
    status: Mapped[str] = mapped_column(String(10), server_default="DRAFT")
    revision: Mapped[int] = mapped_column(Integer, server_default="1")  # for safe status changes later
    mode: Mapped[str] = mapped_column(String(12))
    selection: Mapped[str] = mapped_column(String(16))
    module_codes: Mapped[list[str] | None] = mapped_column(JSONB(none_as_null=True))  # requested; null = full cycle
    solver_status: Mapped[str] = mapped_column(String(16))
    solver_version: Mapped[str | None] = mapped_column(String(40))
    solver_run: Mapped[dict[str, Any]] = mapped_column(JSONB)
    input_fingerprint: Mapped[str] = mapped_column(String(128))
    settings_version: Mapped[int] = mapped_column(Integer)
    settings_fingerprint: Mapped[str] = mapped_column(String(128))
    seating_ruleset_version: Mapped[int] = mapped_column(Integer)
    capacity_fingerprint: Mapped[str] = mapped_column(String(128))
    validation_passed: Mapped[bool] = mapped_column(Boolean)
    validation: Mapped[dict[str, Any]] = mapped_column(JSONB)
    inputs: Mapped[dict[str, Any]] = mapped_column(JSONB)
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB)
    workload: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    optimization: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    excluded_registrations: Mapped[dict[str, Any]] = mapped_column(JSONB)  # counts only
    notices: Mapped[list[str]] = mapped_column(JSONB)
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    approved_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    approved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    published_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    published_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    # 0011: SHA-256 of the saved sessions, rooms and seats (null for drafts saved before 0011).
    content_fingerprint: Mapped[str | None] = mapped_column(String(64))
    source_timetable_id: Mapped[int | None] = mapped_column(BigInteger)  # regenerated from


class ExamTimetableSession(Base):
    __tablename__ = "exam_timetable_sessions"
    __table_args__ = (
        ForeignKeyConstraint(["timetable_id"], ["exam_timetables.timetable_id"], ondelete="CASCADE"),
        CheckConstraint("ends_at > starts_at", name="times_ordered"),
        CheckConstraint("session_number BETWEEN 1 AND session_count", name="session_number_valid"),
        CheckConstraint("candidates = normal_candidates + repeat_candidates", name="candidates_sum"),
    )

    timetable_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    module_code: Mapped[str] = mapped_column(String(16))
    module_name: Mapped[str] = mapped_column(String(200))
    exam_type: Mapped[str] = mapped_column(String(20))
    candidate_scope: Mapped[str] = mapped_column(String(12))
    academic_year: Mapped[int] = mapped_column(SmallInteger)
    semester: Mapped[int] = mapped_column(SmallInteger)
    delivery_mode: Mapped[str] = mapped_column(String(10))
    session_number: Mapped[int] = mapped_column(SmallInteger)
    session_count: Mapped[int] = mapped_column(SmallInteger)
    starts_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=False))  # local time of the period
    ends_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=False))
    duration_minutes: Mapped[int] = mapped_column(SmallInteger)
    reading_time_minutes: Mapped[int] = mapped_column(SmallInteger)
    candidates: Mapped[int] = mapped_column(Integer)
    normal_candidates: Mapped[int] = mapped_column(Integer)
    repeat_candidates: Mapped[int] = mapped_column(Integer)


class ExamTimetableRoom(Base):
    __tablename__ = "exam_timetable_rooms"
    __table_args__ = (
        ForeignKeyConstraint(["timetable_id", "session_id"],
                             ["exam_timetable_sessions.timetable_id", "exam_timetable_sessions.session_id"],
                             ondelete="CASCADE"),
        CheckConstraint("seats >= 1 AND seats <= exam_capacity", name="seats_within_capacity"),
    )

    timetable_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    venue_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    venue_name: Mapped[str] = mapped_column(String(120))
    venue_type: Mapped[str] = mapped_column(String(20))
    seats: Mapped[int] = mapped_column(Integer)
    exam_capacity: Mapped[int] = mapped_column(Integer)


class ExamTimetableSeat(Base):
    __tablename__ = "exam_timetable_seats"
    __table_args__ = (
        ForeignKeyConstraint(["timetable_id", "session_id", "venue_id"],
                             ["exam_timetable_rooms.timetable_id", "exam_timetable_rooms.session_id",
                              "exam_timetable_rooms.venue_id"],
                             ondelete="CASCADE"),
        UniqueConstraint("timetable_id", "session_id", "student_id"),  # one seat per candidate and session
        CheckConstraint("seat_number >= 1", name="seat_number_positive"),
        CheckConstraint(_in("registration_type", ("normal", "repeat")), name="registration_type_valid"),
    )

    timetable_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    venue_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    seat_number: Mapped[int] = mapped_column(Integer, primary_key=True)  # logical seat, not a room layout
    student_id: Mapped[str] = mapped_column(String(16))
    registration_type: Mapped[str] = mapped_column(String(10))


HISTORY_ACTIONS_CHECK = (
    "(action = 'create' AND from_status IS NULL AND to_status = 'DRAFT')"
    " OR (action = 'approve' AND from_status = 'DRAFT' AND to_status = 'APPROVED')"
    " OR (action = 'publish' AND from_status = 'APPROVED' AND to_status = 'PUBLISHED')"
    " OR (action = 'archive' AND from_status = 'PUBLISHED' AND to_status = 'ARCHIVED')"
)


class ExamTimetableHistory(Base):
    __tablename__ = "exam_timetable_history"
    __table_args__ = (
        ForeignKeyConstraint(["timetable_id"], ["exam_timetables.timetable_id"], ondelete="CASCADE"),
        CheckConstraint(HISTORY_ACTIONS_CHECK, name="action_statuses"),
        CheckConstraint("revision >= 1", name="revision_positive"),
        CheckConstraint("jsonb_typeof(details) = 'object'", name="details_object"),
        Index(None, "timetable_id"),
    )

    history_id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    timetable_id: Mapped[int] = mapped_column(BigInteger)
    institution_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    action: Mapped[str] = mapped_column(String(10))
    from_status: Mapped[str | None] = mapped_column(String(10))
    to_status: Mapped[str] = mapped_column(String(10))
    revision: Mapped[int] = mapped_column(Integer)  # timetable revision after the change
    changed_by: Mapped[uuid.UUID] = mapped_column(Uuid)
    changed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reason: Mapped[str] = mapped_column(String(500))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB)  # what was checked


TIMETABLE_MODELS = (ExamTimetable, ExamTimetableSession, ExamTimetableRoom, ExamTimetableSeat, ExamTimetableHistory)
