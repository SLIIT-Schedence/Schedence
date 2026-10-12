"""Identity schema: users, roles, permissions, role assignments, provisioning, audit log.

Creates objects only inside the ``identity`` schema. Nothing outside it is touched.

Revision ID: 0001_identity_initial
Revises:
Create Date: 2026-10-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001_identity_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "identity"


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    ]


def _fk(table: str, column: str, target: str, ondelete: str = "RESTRICT") -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint([column], [f"{SCHEMA}.{target}.id"], name=op.f(f"fk_{table}_{column}_{target}"), ondelete=ondelete)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("cognito_sub", sa.String(length=128), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=False),
        sa.Column("account_status", sa.String(length=20), server_default="ACTIVE", nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("cognito_sub", name=op.f("uq_users_cognito_sub")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
        sa.CheckConstraint("account_status IN ('ACTIVE', 'DISABLED')", name=op.f("ck_users_account_status")),
        sa.CheckConstraint("email = lower(email)", name=op.f("ck_users_email_lowercase")),
        schema=SCHEMA,
    )

    op.create_table(
        "roles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("is_system_role", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_roles")),
        sa.UniqueConstraint("code", name=op.f("uq_roles_code")),
        sa.CheckConstraint("code ~ '^[A-Z][A-Z0-9_]*$'", name=op.f("ck_roles_code_format")),
        schema=SCHEMA,
    )

    op.create_table(
        "permissions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_permissions")),
        sa.UniqueConstraint("code", name=op.f("uq_permissions_code")),
        sa.CheckConstraint("code ~ '^[a-z][a-z0-9_]*(:[a-z][a-z0-9_]*)+$'", name=op.f("ck_permissions_code_format")),
        schema=SCHEMA,
    )

    op.create_table(
        "role_permissions",
        sa.Column("role_id", sa.Uuid(), nullable=False),
        sa.Column("permission_id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("role_id", "permission_id", name=op.f("pk_role_permissions")),
        _fk("role_permissions", "role_id", "roles", ondelete="CASCADE"),
        _fk("role_permissions", "permission_id", "permissions", ondelete="CASCADE"),
        schema=SCHEMA,
    )
    op.create_index(op.f("ix_role_permissions_permission_id"), "role_permissions", ["permission_id"], schema=SCHEMA)

    op.create_table(
        "user_roles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role_id", sa.Uuid(), nullable=False),
        sa.Column("institution_id", sa.Uuid(), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("assigned_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by_user_id", sa.Uuid(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_roles")),
        _fk("user_roles", "user_id", "users"),
        _fk("user_roles", "role_id", "roles"),
        _fk("user_roles", "assigned_by_user_id", "users"),
        _fk("user_roles", "revoked_by_user_id", "users"),
        sa.CheckConstraint("revoked_at IS NULL OR revoked_at >= assigned_at", name=op.f("ck_user_roles_revoked_after_assigned")),
        schema=SCHEMA,
    )
    op.create_index(op.f("ix_user_roles_user_id"), "user_roles", ["user_id"], schema=SCHEMA)
    op.create_index(op.f("ix_user_roles_institution_id_role_id"), "user_roles", ["institution_id", "role_id"], schema=SCHEMA)
    op.create_index(op.f("uq_user_roles_active_assignment"), "user_roles", ["user_id", "role_id", "institution_id"], unique=True,
                    schema=SCHEMA, postgresql_where=sa.text("revoked_at IS NULL"))

    op.create_table(
        "user_provisioning",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=False),
        sa.Column("requested_role_id", sa.Uuid(), nullable=False),
        sa.Column("institution_id", sa.Uuid(), nullable=False),
        sa.Column("cognito_sub", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error_category", sa.String(length=50), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("requested_by_user_id", sa.Uuid(), nullable=True),
        *_timestamps(),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_provisioning")),
        _fk("user_provisioning", "user_id", "users"),
        _fk("user_provisioning", "requested_role_id", "roles"),
        _fk("user_provisioning", "requested_by_user_id", "users"),
        sa.CheckConstraint("status IN ('PENDING', 'COGNITO_CREATED', 'FAILED', 'COMPLETED', 'CANCELLED')",
                           name=op.f("ck_user_provisioning_status")),
        sa.CheckConstraint("email = lower(email)", name=op.f("ck_user_provisioning_email_lowercase")),
        sa.CheckConstraint("status <> 'COMPLETED' OR user_id IS NOT NULL", name=op.f("ck_user_provisioning_completed_has_user")),
        schema=SCHEMA,
    )
    op.create_index(op.f("ix_user_provisioning_user_id"), "user_provisioning", ["user_id"], schema=SCHEMA)
    op.create_index(op.f("ix_user_provisioning_email"), "user_provisioning", ["email"], schema=SCHEMA)
    op.create_index(op.f("ix_user_provisioning_status_created_at"), "user_provisioning", ["status", "created_at"], schema=SCHEMA)
    op.create_index(op.f("uq_user_provisioning_open_email"), "user_provisioning", ["email"], unique=True, schema=SCHEMA,
                    postgresql_where=sa.text("status IN ('PENDING', 'COGNITO_CREATED', 'FAILED')"))

    op.create_table(
        "identity_audit_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("actor_type", sa.String(length=20), nullable=False),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("target_user_id", sa.Uuid(), nullable=True),
        sa.Column("institution_id", sa.Uuid(), nullable=True),
        sa.Column("safe_metadata", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_identity_audit_logs")),
        _fk("identity_audit_logs", "actor_user_id", "users"),
        _fk("identity_audit_logs", "target_user_id", "users"),
        sa.CheckConstraint("actor_type IN ('USER', 'OPERATOR', 'SYSTEM')", name=op.f("ck_identity_audit_logs_actor_type")),
        schema=SCHEMA,
    )
    op.create_index(op.f("ix_identity_audit_logs_occurred_at"), "identity_audit_logs", ["occurred_at"], schema=SCHEMA)
    op.create_index(op.f("ix_identity_audit_logs_target_user_id_occurred_at"), "identity_audit_logs", ["target_user_id", "occurred_at"],
                    schema=SCHEMA)
    op.create_index(op.f("ix_identity_audit_logs_actor_user_id_occurred_at"), "identity_audit_logs", ["actor_user_id", "occurred_at"],
                    schema=SCHEMA)
    op.create_index(op.f("ix_identity_audit_logs_institution_id_occurred_at"), "identity_audit_logs",
                    ["institution_id", "occurred_at"], schema=SCHEMA)
    op.create_index(op.f("ix_identity_audit_logs_action"), "identity_audit_logs", ["action"], schema=SCHEMA)


def downgrade() -> None:
    # Destructive: drops every Identity table and its data. The schema itself is left in place.
    for table in ("identity_audit_logs", "user_provisioning", "user_roles", "role_permissions", "permissions", "roles", "users"):
        op.drop_table(table, schema=SCHEMA)
