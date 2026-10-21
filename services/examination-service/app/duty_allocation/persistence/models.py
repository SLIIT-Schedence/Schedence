"""Tables of the saved duty rosters, change requests and seating drafts (schema ``examinations``).

Uses component-owned metadata and migration history. The component's migration 0013
creates these tables without changing the team's Examination Service migration history.

Rules enforced by the database as well as by the service:

* at most ONE working roster (DRAFT / VALIDATED / APPROVED) and at most ONE PUBLISHED roster
  per institution and period (partial unique indexes);
* a staff member cannot hold two duties in one session of a roster;
* a roster can only be PUBLISHED when its timetable was verified and the data is not synthetic;
* a seating draft can only contain VERIFIED seats when it is not synthetic.

No table references another service's data; nothing in ``m3_demo`` is read or changed.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Identity, Index, Integer, MetaData, String,
    UniqueConstraint, Uuid, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from app.db.base import NAMING_CONVENTION, SCHEMA

class DutyBase(DeclarativeBase):
    metadata = MetaData(schema=SCHEMA, naming_convention=NAMING_CONVENTION)


_NOW = func.now()
ROSTER_STATUSES = "'DRAFT', 'VALIDATED', 'APPROVED', 'PUBLISHED', 'REJECTED', 'SUPERSEDED'"
WORKING_STATUSES = "'DRAFT', 'VALIDATED', 'APPROVED'"


class DutyRoster(DutyBase):
    __tablename__ = "duty_rosters"
    __table_args__ = (
        CheckConstraint(f"status IN ({ROSTER_STATUSES})", name="status_valid"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("row_version >= 1", name="row_version_positive"),
        CheckConstraint("algorithm_mode IN ('BASELINE', 'FAIR', 'COMPARE')", name="mode_valid"),
        CheckConstraint("roster_label IN ('BASELINE', 'FAIR')", name="label_valid"),
        CheckConstraint("char_length(input_digest) = 64", name="digest_length"),
        CheckConstraint("jsonb_typeof(fairness_metrics) = 'object'", name="metrics_object"),
        CheckConstraint("jsonb_typeof(validation) = 'object'", name="validation_object"),
        CheckConstraint(
            "status <> 'PUBLISHED' OR (timetable_verified AND NOT synthetic AND published_timetable_reference IS NOT NULL)",
            name="published_needs_verified_timetable"),
        UniqueConstraint("institution_id", "period_code", "version", name="uq_duty_rosters_institution_id_period_code_version"),
        Index("uq_duty_rosters_one_working_per_period", "institution_id", "period_code", unique=True,
              postgresql_where=text(f"status IN ({WORKING_STATUSES})")),
        Index("uq_duty_rosters_one_published_per_period", "institution_id", "period_code", unique=True,
              postgresql_where=text("status = 'PUBLISHED'")),
        Index("ix_duty_rosters_institution_id_period_code", "institution_id", "period_code"),
    )

    roster_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    institution_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    period_code: Mapped[str] = mapped_column(String(32))
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(12))
    row_version: Mapped[int] = mapped_column(Integer)
    algorithm_mode: Mapped[str] = mapped_column(String(10))
    roster_label: Mapped[str] = mapped_column(String(10))
    timetable_reference: Mapped[str] = mapped_column(String(200))
    timetable_fingerprint: Mapped[str] = mapped_column(String(200))
    timetable_verified: Mapped[bool] = mapped_column(Boolean)
    input_digest: Mapped[str] = mapped_column(String(64))
    data_source: Mapped[str] = mapped_column(String(30))
    synthetic: Mapped[bool] = mapped_column(Boolean)
    fairness_metrics: Mapped[dict[str, Any]] = mapped_column(JSONB)
    validation: Mapped[dict[str, Any]] = mapped_column(JSONB)
    parent_roster_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(f"{SCHEMA}.duty_rosters.roster_id"))
    published_timetable_reference: Mapped[str | None] = mapped_column(String(200))
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=_NOW)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=_NOW)
    validated_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    validated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    approved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    published_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    published_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    rejected_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    rejection_reason: Mapped[str | None] = mapped_column(String(500))


class DutyRosterAssignment(DutyBase):
    __tablename__ = "duty_roster_assignments"
    __table_args__ = (
        CheckConstraint("role IN ('SUPERVISOR', 'INVIGILATOR')", name="role_valid"),
        CheckConstraint("ends_at > starts_at", name="interval_valid"),
        UniqueConstraint("roster_id", "session_id", "staff_id", name="uq_duty_roster_assignments_roster_session_staff"),
        Index("ix_duty_roster_assignments_roster_id", "roster_id"),
        Index("ix_duty_roster_assignments_staff_id_starts_at_ends_at", "staff_id", "starts_at", "ends_at"),
    )

    assignment_id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    roster_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.duty_rosters.roster_id"))
    session_id: Mapped[str] = mapped_column(String(64))
    venue_id: Mapped[str] = mapped_column(String(64))
    staff_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    role: Mapped[str] = mapped_column(String(12))
    staff_number: Mapped[str | None] = mapped_column(String(40))
    module_code: Mapped[str | None] = mapped_column(String(20))
    starts_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=False))  # local exam time, as in the timetable
    ends_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=False))


class DutyRosterEvent(DutyBase):
    """Append-only audit trail of every lifecycle action and refusal."""
    __tablename__ = "duty_roster_events"
    __table_args__ = (
        CheckConstraint("jsonb_typeof(details) = 'object'", name="details_object"),
        Index("ix_duty_roster_events_roster_id", "roster_id"),
    )

    event_id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    roster_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.duty_rosters.roster_id"))
    action: Mapped[str] = mapped_column(String(40))
    from_status: Mapped[str | None] = mapped_column(String(12))
    to_status: Mapped[str | None] = mapped_column(String(12))
    actor_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=_NOW)
    reason: Mapped[str | None] = mapped_column(String(500))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB)


class DutyChangeRequest(DutyBase):
    __tablename__ = "duty_change_requests"
    __table_args__ = (
        CheckConstraint("kind IN ('SWAP', 'REPLACEMENT')", name="kind_valid"),
        CheckConstraint("status IN ('REQUESTED', 'APPROVED', 'REJECTED', 'APPLIED', 'CANCELLED', 'STALE')",
                        name="status_valid"),
        CheckConstraint("base_row_version >= 1", name="base_row_version_positive"),
        CheckConstraint("jsonb_typeof(removed) = 'array' AND jsonb_typeof(added) = 'array'", name="changes_arrays"),
        Index("ix_duty_change_requests_roster_id", "roster_id"),
    )

    request_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    roster_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.duty_rosters.roster_id"))
    institution_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    kind: Mapped[str] = mapped_column(String(12))
    status: Mapped[str] = mapped_column(String(10))
    base_row_version: Mapped[int] = mapped_column(Integer)
    removed: Mapped[list[Any]] = mapped_column(JSONB)
    added: Mapped[list[Any]] = mapped_column(JSONB)
    reason: Mapped[str] = mapped_column(String(500))
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB)
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB)
    requested_by: Mapped[uuid.UUID] = mapped_column(Uuid)
    requested_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=_NOW)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    decided_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    decision_reason: Mapped[str | None] = mapped_column(String(500))
    applied_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    applied_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class SeatingDraft(DutyBase):
    __tablename__ = "seating_drafts"
    __table_args__ = (
        CheckConstraint("status IN ('DRAFT', 'SUPERSEDED')", name="status_valid"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("seat_state IN ('SYNTHETIC', 'PROVISIONAL', 'VERIFIED')", name="seat_state_valid"),
        CheckConstraint("seat_state <> 'VERIFIED' OR NOT synthetic", name="synthetic_never_verified"),
        CheckConstraint("jsonb_typeof(summary) = 'object'", name="summary_object"),
        UniqueConstraint("institution_id", "period_code", "version", name="uq_seating_drafts_institution_id_period_code_version"),
        Index("uq_seating_drafts_one_current_per_period", "institution_id", "period_code", unique=True,
              postgresql_where=text("status = 'DRAFT'")),
    )

    draft_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    institution_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    period_code: Mapped[str] = mapped_column(String(32))
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(12))
    timetable_reference: Mapped[str] = mapped_column(String(200))
    data_source: Mapped[str] = mapped_column(String(30))
    synthetic: Mapped[bool] = mapped_column(Boolean)
    seat_state: Mapped[str] = mapped_column(String(12))
    capacity_rules_source: Mapped[str] = mapped_column(String(200))
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=_NOW)


class SeatingDraftSeat(DutyBase):
    __tablename__ = "seating_draft_seats"
    __table_args__ = (
        CheckConstraint("verification_status IN ('SYNTHETIC', 'PROVISIONAL', 'VERIFIED')", name="verification_valid"),
        CheckConstraint("logical_seat_number >= 1 AND physical_seat_number >= 1", name="seat_numbers_positive"),
        UniqueConstraint("draft_id", "session_id", "student_id", name="uq_seating_draft_seats_draft_session_student"),
        UniqueConstraint("draft_id", "session_id", "venue_id", "physical_seat_number",
                         name="uq_seating_draft_seats_draft_session_venue_seat"),
        Index("ix_seating_draft_seats_draft_id", "draft_id"),
    )

    seat_id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    draft_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.seating_drafts.draft_id"))
    session_id: Mapped[str] = mapped_column(String(64))
    venue_id: Mapped[str] = mapped_column(String(64))
    student_id: Mapped[str] = mapped_column(String(32))
    module_code: Mapped[str] = mapped_column(String(20))
    logical_seat_number: Mapped[int] = mapped_column(Integer)
    physical_seat_number: Mapped[int] = mapped_column(Integer)
    physical_seat_label: Mapped[str] = mapped_column(String(40))
    verification_status: Mapped[str] = mapped_column(String(12))


DUTY_MODELS = (DutyRoster, DutyRosterAssignment, DutyRosterEvent, DutyChangeRequest, SeatingDraft, SeatingDraftSeat)
