"""Administrative positions, workload policies, staff references and appointments.

Nothing here is an access-control concept. An appointment such as Head of
Department records *responsibility and workload*; it never grants a Schedence
login role. ``institution_id`` and ``identity_user_id`` are opaque identifiers
owned by other services, so they carry no foreign keys.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, timestamp_column, uuid_pk

UNIT_TYPES = ("INSTITUTION", "FACULTY", "DEPARTMENT", "PROGRAM", "OTHER")
COMBINATION_RULES = ("UNSPECIFIED", "ADDITIVE", "CAPPED", "REPLACEMENT")
EMPLOYMENT_ACTIVE, EMPLOYMENT_INACTIVE = "ACTIVE", "INACTIVE"
_UNIT_TYPE_CHECK = "unit_type IS NULL OR unit_type IN ('INSTITUTION', 'FACULTY', 'DEPARTMENT', 'PROGRAM', 'OTHER')"
Hours = Numeric(5, 2)


class Staff(Base):
    """Minimal reference to an academic staff member. A staff member need not have a Schedence login."""

    __tablename__ = "staff"
    __table_args__ = (
        UniqueConstraint("institution_id", "staff_number"),
        CheckConstraint("employment_status IN ('ACTIVE', 'INACTIVE')", name="employment_status"),
        CheckConstraint("email IS NULL OR email = lower(email)", name="email_lowercase"),
        Index("ix_staff_institution_id_full_name", "institution_id", "full_name"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    institution_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    staff_number: Mapped[str] = mapped_column(String(50), nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320))
    academic_rank: Mapped[str | None] = mapped_column(String(100))
    employment_status: Mapped[str] = mapped_column(String(20), nullable=False, default=EMPLOYMENT_ACTIVE, server_default=EMPLOYMENT_ACTIVE)
    identity_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    created_at: Mapped[datetime] = timestamp_column()
    updated_at: Mapped[datetime] = timestamp_column()


class AdministrativePositionType(Base):
    """A configurable position definition, e.g. HOD or PROGRAM_COORDINATOR, per institution."""

    __tablename__ = "administrative_position_types"
    __table_args__ = (
        UniqueConstraint("institution_id", "code"),
        CheckConstraint("code ~ '^[A-Z][A-Z0-9_]*$'", name="code_format"),
        CheckConstraint(_UNIT_TYPE_CHECK, name="unit_type"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    institution_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    code: Mapped[str] = mapped_column(String(60), nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    unit_type: Mapped[str | None] = mapped_column(String(20))
    is_exclusive_per_unit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = timestamp_column()
    updated_at: Mapped[datetime] = timestamp_column()
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)


class AdministrativeWorkloadPolicy(Base):
    """A versioned administrative-hour policy for one position type.

    Changing the hours adds a new version and closes the previous one, so the
    policy that applied on any past date can always be reconstructed.
    ``combination_rule`` declares how this position's hours combine with other
    concurrent appointments; ``UNSPECIFIED`` means "not decided by the institution".
    """

    __tablename__ = "administrative_workload_policies"
    __table_args__ = (
        UniqueConstraint("position_type_id", "version"),
        CheckConstraint("admin_hours_per_week >= 0 AND admin_hours_per_week <= 168", name="hours_range"),
        CheckConstraint("combination_rule IN ('UNSPECIFIED', 'ADDITIVE', 'CAPPED', 'REPLACEMENT')", name="combination_rule"),
        CheckConstraint("combination_rule <> 'CAPPED' OR cap_hours_per_week IS NOT NULL", name="cap_required"),
        CheckConstraint("cap_hours_per_week IS NULL OR (cap_hours_per_week >= 0 AND cap_hours_per_week <= 168)", name="cap_range"),
        CheckConstraint("effective_until IS NULL OR effective_until >= effective_from", name="effective_dates"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    position_type_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("academic.administrative_position_types.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    admin_hours_per_week: Mapped[Decimal] = mapped_column(Hours, nullable=False)
    combination_rule: Mapped[str] = mapped_column(String(20), nullable=False, default="UNSPECIFIED", server_default="UNSPECIFIED")
    cap_hours_per_week: Mapped[Decimal | None] = mapped_column(Hours)
    policy_reference: Mapped[str | None] = mapped_column(String(200))
    notes: Mapped[str | None] = mapped_column(Text)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_until: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = timestamp_column()
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    def applies_on(self, day: date) -> bool:
        return self.effective_from <= day and (self.effective_until is None or day <= self.effective_until)


class StaffAdministrativeAppointment(Base):
    """A staff member holding an administrative position for a period (``effective_until`` is inclusive)."""

    __tablename__ = "staff_administrative_appointments"
    __table_args__ = (
        CheckConstraint("effective_until IS NULL OR effective_until >= effective_from", name="effective_dates"),
        CheckConstraint("admin_hours_override IS NULL OR (admin_hours_override >= 0 AND admin_hours_override <= 168)",
                        name="override_range"),
        CheckConstraint("admin_hours_override IS NULL OR override_reason IS NOT NULL", name="override_reason"),
        CheckConstraint(_UNIT_TYPE_CHECK, name="unit_type"),
        Index("ix_appointments_staff_id_effective_from", "staff_id", "effective_from"),
        Index("ix_appointments_position_type_id_unit_code", "position_type_id", "unit_code"),
        Index("ix_appointments_institution_id", "institution_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    institution_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    staff_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("academic.staff.id", ondelete="RESTRICT"), nullable=False)
    position_type_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("academic.administrative_position_types.id", ondelete="RESTRICT"), nullable=False
    )
    unit_type: Mapped[str | None] = mapped_column(String(20))
    unit_code: Mapped[str | None] = mapped_column(String(60))
    unit_name: Mapped[str | None] = mapped_column(String(150))
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_until: Mapped[date | None] = mapped_column(Date)
    admin_hours_override: Mapped[Decimal | None] = mapped_column(Hours)
    override_reason: Mapped[str | None] = mapped_column(String(500))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = timestamp_column()
    updated_at: Mapped[datetime] = timestamp_column()
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    end_reason: Mapped[str | None] = mapped_column(String(500))

    def active_on(self, day: date) -> bool:
        return self.effective_from <= day and (self.effective_until is None or day <= self.effective_until)

    def status_on(self, day: date) -> str:
        if day < self.effective_from:
            return "SCHEDULED"
        if self.effective_until is not None and day > self.effective_until:
            return "ENDED"
        return "ACTIVE"


class AppointmentAuditLog(Base):
    __tablename__ = "appointment_audit_logs"
    __table_args__ = (
        CheckConstraint("entity_type IN ('STAFF', 'POSITION_TYPE', 'WORKLOAD_POLICY', 'APPOINTMENT')", name="entity_type"),
        Index("ix_appointment_audit_logs_entity", "entity_type", "entity_id"),
        Index("ix_appointment_audit_logs_staff_id_occurred_at", "staff_id", "occurred_at"),
        Index("ix_appointment_audit_logs_institution_id_occurred_at", "institution_id", "occurred_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    institution_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(30), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    staff_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    safe_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    request_id: Mapped[str | None] = mapped_column(String(64))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
