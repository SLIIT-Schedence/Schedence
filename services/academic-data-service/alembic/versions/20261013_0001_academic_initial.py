"""Academic schema: staff references, administrative position types, workload policies, appointments, audit log.

Creates objects only inside the ``academic`` schema. Nothing outside it is touched.

Revision ID: 0001_academic_initial
Revises:
Create Date: 2026-10-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001_academic_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "academic"
UNIT_TYPE_CHECK = "unit_type IS NULL OR unit_type IN ('INSTITUTION', 'FACULTY', 'DEPARTMENT', 'PROGRAM', 'OTHER')"


def _ts(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False)


def _fk(table: str, column: str, target: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint([column], [f"{SCHEMA}.{target}.id"], name=op.f(f"fk_{table}_{column}"), ondelete="RESTRICT")


def upgrade() -> None:
    op.create_table(
        "staff",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("institution_id", sa.Uuid(), nullable=False),
        sa.Column("staff_number", sa.String(length=50), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("academic_rank", sa.String(length=100), nullable=True),
        sa.Column("employment_status", sa.String(length=20), server_default="ACTIVE", nullable=False),
        sa.Column("identity_user_id", sa.Uuid(), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_staff")),
        sa.UniqueConstraint("institution_id", "staff_number", name=op.f("uq_staff_institution_id_staff_number")),
        sa.CheckConstraint("employment_status IN ('ACTIVE', 'INACTIVE')", name=op.f("ck_staff_employment_status")),
        sa.CheckConstraint("email IS NULL OR email = lower(email)", name=op.f("ck_staff_email_lowercase")),
        schema=SCHEMA,
    )
    op.create_index(op.f("ix_staff_institution_id_full_name"), "staff", ["institution_id", "full_name"], schema=SCHEMA)
    op.create_index(op.f("ix_staff_identity_user_id"), "staff", ["identity_user_id"], schema=SCHEMA)

    op.create_table(
        "administrative_position_types",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("institution_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=60), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("unit_type", sa.String(length=20), nullable=True),
        sa.Column("is_exclusive_per_unit", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        _ts("created_at"),
        _ts("updated_at"),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("updated_by_user_id", sa.Uuid(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_administrative_position_types")),
        sa.UniqueConstraint("institution_id", "code", name=op.f("uq_administrative_position_types_institution_id_code")),
        sa.CheckConstraint("code ~ '^[A-Z][A-Z0-9_]*$'", name=op.f("ck_administrative_position_types_code_format")),
        sa.CheckConstraint(UNIT_TYPE_CHECK, name=op.f("ck_administrative_position_types_unit_type")),
        schema=SCHEMA,
    )

    op.create_table(
        "administrative_workload_policies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("position_type_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("admin_hours_per_week", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("combination_rule", sa.String(length=20), server_default="UNSPECIFIED", nullable=False),
        sa.Column("cap_hours_per_week", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("policy_reference", sa.String(length=200), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_until", sa.Date(), nullable=True),
        _ts("created_at"),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_administrative_workload_policies")),
        _fk("administrative_workload_policies", "position_type_id", "administrative_position_types"),
        sa.UniqueConstraint("position_type_id", "version", name=op.f("uq_administrative_workload_policies_position_type_id_version")),
        sa.CheckConstraint("admin_hours_per_week >= 0 AND admin_hours_per_week <= 168",
                           name=op.f("ck_administrative_workload_policies_hours_range")),
        sa.CheckConstraint("combination_rule IN ('UNSPECIFIED', 'ADDITIVE', 'CAPPED', 'REPLACEMENT')",
                           name=op.f("ck_administrative_workload_policies_combination_rule")),
        sa.CheckConstraint("combination_rule <> 'CAPPED' OR cap_hours_per_week IS NOT NULL",
                           name=op.f("ck_administrative_workload_policies_cap_required")),
        sa.CheckConstraint("cap_hours_per_week IS NULL OR (cap_hours_per_week >= 0 AND cap_hours_per_week <= 168)",
                           name=op.f("ck_administrative_workload_policies_cap_range")),
        sa.CheckConstraint("effective_until IS NULL OR effective_until >= effective_from",
                           name=op.f("ck_administrative_workload_policies_effective_dates")),
        schema=SCHEMA,
    )
    op.create_index(op.f("ix_administrative_workload_policies_position_type_id"), "administrative_workload_policies",
                    ["position_type_id"], schema=SCHEMA)

    op.create_table(
        "staff_administrative_appointments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("institution_id", sa.Uuid(), nullable=False),
        sa.Column("staff_id", sa.Uuid(), nullable=False),
        sa.Column("position_type_id", sa.Uuid(), nullable=False),
        sa.Column("unit_type", sa.String(length=20), nullable=True),
        sa.Column("unit_code", sa.String(length=60), nullable=True),
        sa.Column("unit_name", sa.String(length=150), nullable=True),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_until", sa.Date(), nullable=True),
        sa.Column("admin_hours_override", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("override_reason", sa.String(length=500), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("updated_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("end_reason", sa.String(length=500), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_staff_administrative_appointments")),
        _fk("staff_administrative_appointments", "staff_id", "staff"),
        _fk("staff_administrative_appointments", "position_type_id", "administrative_position_types"),
        sa.CheckConstraint("effective_until IS NULL OR effective_until >= effective_from",
                           name=op.f("ck_staff_administrative_appointments_effective_dates")),
        sa.CheckConstraint("admin_hours_override IS NULL OR (admin_hours_override >= 0 AND admin_hours_override <= 168)",
                           name=op.f("ck_staff_administrative_appointments_override_range")),
        sa.CheckConstraint("admin_hours_override IS NULL OR override_reason IS NOT NULL",
                           name=op.f("ck_staff_administrative_appointments_override_reason")),
        sa.CheckConstraint(UNIT_TYPE_CHECK, name=op.f("ck_staff_administrative_appointments_unit_type")),
        schema=SCHEMA,
    )
    op.create_index(op.f("ix_appointments_staff_id_effective_from"), "staff_administrative_appointments",
                    ["staff_id", "effective_from"], schema=SCHEMA)
    op.create_index(op.f("ix_appointments_position_type_id_unit_code"), "staff_administrative_appointments",
                    ["position_type_id", "unit_code"], schema=SCHEMA)
    op.create_index(op.f("ix_appointments_institution_id"), "staff_administrative_appointments", ["institution_id"], schema=SCHEMA)

    op.create_table(
        "appointment_audit_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("institution_id", sa.Uuid(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("entity_type", sa.String(length=30), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("staff_id", sa.Uuid(), nullable=True),
        sa.Column("safe_metadata", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_appointment_audit_logs")),
        sa.CheckConstraint("entity_type IN ('STAFF', 'POSITION_TYPE', 'WORKLOAD_POLICY', 'APPOINTMENT')",
                           name=op.f("ck_appointment_audit_logs_entity_type")),
        schema=SCHEMA,
    )
    op.create_index(op.f("ix_appointment_audit_logs_entity"), "appointment_audit_logs", ["entity_type", "entity_id"], schema=SCHEMA)
    op.create_index(op.f("ix_appointment_audit_logs_staff_id_occurred_at"), "appointment_audit_logs", ["staff_id", "occurred_at"],
                    schema=SCHEMA)
    op.create_index(op.f("ix_appointment_audit_logs_institution_id_occurred_at"), "appointment_audit_logs",
                    ["institution_id", "occurred_at"], schema=SCHEMA)


def downgrade() -> None:
    # Destructive: drops every Academic Data table and its data. The schema itself is left in place.
    for table in ("appointment_audit_logs", "staff_administrative_appointments", "administrative_workload_policies",
                  "administrative_position_types", "staff"):
        op.drop_table(table, schema=SCHEMA)
