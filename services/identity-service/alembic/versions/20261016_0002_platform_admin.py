"""Platform administration: institution registry, platform role assignments, SUPER_ADMIN -> INSTITUTION_ADMIN.

Upgrade, in one transaction, touching only the ``identity`` schema:

1. creates ``identity.institutions`` and registers every institution ID already
   used by a role assignment or provisioning record, under a placeholder code
   and name (``INST-<first 8 hex digits>``) that a System Admin renames later.
   The IDs are kept, so existing assignments stay valid;
2. adds foreign keys from ``user_roles.institution_id`` and
   ``user_provisioning.institution_id`` to it;
3. creates ``identity.platform_role_assignments`` for SYSTEM_ADMIN grants;
4. renames the SUPER_ADMIN role row to INSTITUTION_ADMIN. Assignments
   reference the role by ID, so every current Super Admin keeps the role.

Nothing is dropped or deleted on upgrade. Run ``scripts.seed_roles`` afterwards
to add the SYSTEM_ADMIN role and the ``platform:*`` permissions.

Revision ID: 0002_platform_admin
Revises: 0001_identity_initial
Create Date: 2026-10-16
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_platform_admin"
down_revision: str | None = "0001_identity_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "identity"


def _fk(table: str, column: str, target: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint([column], [f"{SCHEMA}.{target}.id"], name=op.f(f"fk_{table}_{column}_{target}"), ondelete="RESTRICT")


def upgrade() -> None:
    op.create_table(
        "institutions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=30), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="ACTIVE", nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_institutions")),
        sa.CheckConstraint("status IN ('ACTIVE', 'DISABLED')", name=op.f("ck_institutions_status")),
        sa.CheckConstraint("code ~ '^[A-Z][A-Z0-9_-]*$'", name=op.f("ck_institutions_code_format")),
        sa.UniqueConstraint("code", name=op.f("uq_institutions_code")),
        _fk("institutions", "created_by_user_id", "users"),
        schema=SCHEMA,
    )

    # Register the institutions already in use, keeping their IDs.
    op.execute(
        f"""
        INSERT INTO {SCHEMA}.institutions (id, code, name, status)
        SELECT used.institution_id,
               'INST-' || upper(left(replace(used.institution_id::text, '-', ''), 8)),
               'Unnamed institution ' || left(used.institution_id::text, 8),
               'ACTIVE'
        FROM (
            SELECT institution_id FROM {SCHEMA}.user_roles
            UNION
            SELECT institution_id FROM {SCHEMA}.user_provisioning
        ) AS used
        """
    )

    op.create_foreign_key(op.f("fk_user_roles_institution_id_institutions"), "user_roles", "institutions",
                          ["institution_id"], ["id"], source_schema=SCHEMA, referent_schema=SCHEMA, ondelete="RESTRICT")
    op.create_foreign_key(op.f("fk_user_provisioning_institution_id_institutions"), "user_provisioning", "institutions",
                          ["institution_id"], ["id"], source_schema=SCHEMA, referent_schema=SCHEMA, ondelete="RESTRICT")

    op.create_table(
        "platform_role_assignments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role_id", sa.Uuid(), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("assigned_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by_user_id", sa.Uuid(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_platform_role_assignments")),
        _fk("platform_role_assignments", "user_id", "users"),
        _fk("platform_role_assignments", "role_id", "roles"),
        _fk("platform_role_assignments", "assigned_by_user_id", "users"),
        _fk("platform_role_assignments", "revoked_by_user_id", "users"),
        sa.CheckConstraint("revoked_at IS NULL OR revoked_at >= assigned_at",
                           name=op.f("ck_platform_role_assignments_revoked_after_assigned")),
        schema=SCHEMA,
    )
    op.create_index(op.f("ix_platform_role_assignments_user_id"), "platform_role_assignments", ["user_id"], schema=SCHEMA)
    op.create_index(op.f("uq_platform_role_assignments_active_assignment"), "platform_role_assignments", ["user_id", "role_id"],
                    unique=True, schema=SCHEMA, postgresql_where=sa.text("revoked_at IS NULL"))

    op.execute(
        f"""
        UPDATE {SCHEMA}.roles
        SET code = 'INSTITUTION_ADMIN',
            name = 'Institution Admin',
            description = 'Manages the institution''s accounts, system roles, and administrative position definitions.',
            updated_at = now()
        WHERE code = 'SUPER_ADMIN'
        """
    )


def downgrade() -> None:
    # Destructive: drops the institution registry and every SYSTEM_ADMIN grant, and removes the
    # seeded SYSTEM_ADMIN role and platform permissions. Institution IDs stay on existing rows.
    op.drop_table("platform_role_assignments", schema=SCHEMA)
    op.drop_constraint(op.f("fk_user_provisioning_institution_id_institutions"), "user_provisioning", schema=SCHEMA, type_="foreignkey")
    op.drop_constraint(op.f("fk_user_roles_institution_id_institutions"), "user_roles", schema=SCHEMA, type_="foreignkey")
    op.drop_table("institutions", schema=SCHEMA)
    op.execute(f"DELETE FROM {SCHEMA}.roles WHERE code = 'SYSTEM_ADMIN'")
    op.execute(f"DELETE FROM {SCHEMA}.permissions WHERE code LIKE 'platform:%'")
    op.execute(
        f"""
        UPDATE {SCHEMA}.roles
        SET code = 'SUPER_ADMIN',
            name = 'Administrative Admin',
            description = 'Super Admin: manages accounts, system roles, and administrative position definitions.',
            updated_at = now()
        WHERE code = 'INSTITUTION_ADMIN'
        """
    )
