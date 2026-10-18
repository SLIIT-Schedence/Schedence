"""admin scheduling settings

Adds versioned, admin-configurable scheduling settings. Touches only the
examinations schema. No existing row is changed or deleted, and no row is
inserted: until an admin saves settings, the built-in defaults apply.

* New table exam_scheduling_settings: at most one row per institution (the
  institution in which the admin's examinations:manage permission is checked),
  the settings document (JSONB), and a version for safe concurrent updates.
* New table exam_scheduling_settings_history: who changed the settings, when,
  why, the changed fields, and the old and new document. Action ``repair``
  replaced an invalid stored document, which the row keeps exactly.

Downgrade drops both tables. It refuses to run when settings were saved (their
history would be lost) unless ``alembic -x drop_scheduling_settings=true
downgrade 0005`` is used after the history was exported.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import context, op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "examinations"


def upgrade() -> None:
    op.create_table(
        "exam_scheduling_settings",
        sa.Column("settings_id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("institution_id", sa.Uuid(), nullable=False),
        sa.Column("format", sa.String(40), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("settings", postgresql.JSONB(), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("settings_id", name="pk_exam_scheduling_settings"),
        sa.UniqueConstraint("institution_id", name="uq_exam_scheduling_settings_institution_id"),
        sa.CheckConstraint("version >= 1", name="version_positive"),
        sa.CheckConstraint("jsonb_typeof(settings) = 'object'", name="settings_object"),
        schema=SCHEMA,
    )
    op.create_table(
        "exam_scheduling_settings_history",
        sa.Column("history_id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("institution_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(10), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("format", sa.String(40), nullable=False),
        sa.Column("old_format", sa.String(40), nullable=True),
        sa.Column("old_settings", postgresql.JSONB(), nullable=True),
        sa.Column("new_settings", postgresql.JSONB(), nullable=False),
        sa.Column("changed_fields", postgresql.JSONB(), nullable=False),
        sa.Column("changed_by", sa.Uuid(), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.PrimaryKeyConstraint("history_id", name="pk_exam_scheduling_settings_history"),
        sa.CheckConstraint("action IN ('create', 'update', 'repair')", name="action_valid"),
        sa.CheckConstraint("version >= 1", name="version_positive"),
        sa.CheckConstraint(
            "(action = 'create' AND old_settings IS NULL AND old_format IS NULL)"
            " OR (action IN ('update', 'repair') AND old_settings IS NOT NULL AND old_format IS NOT NULL)",
            name="action_values",
        ),
        sa.CheckConstraint("jsonb_typeof(changed_fields) = 'array'", name="changed_fields_array"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_exam_scheduling_settings_history_institution_id", "exam_scheduling_settings_history",
        ["institution_id"], schema=SCHEMA,
    )


def downgrade() -> None:
    allow_loss = context.get_x_argument(as_dictionary=True).get("drop_scheduling_settings", "").lower() == "true"
    if not allow_loss:
        # Stop (and change nothing) if an admin has saved settings since 0006.
        op.execute(
            "DO $$ BEGIN"
            f" IF EXISTS (SELECT 1 FROM {SCHEMA}.exam_scheduling_settings_history) THEN"
            " RAISE EXCEPTION 'exam scheduling settings were saved by admins. Export"
            " examinations.exam_scheduling_settings_history, then run with -x drop_scheduling_settings=true';"
            " END IF; END $$"
        )
    op.drop_index("ix_exam_scheduling_settings_history_institution_id",
                  table_name="exam_scheduling_settings_history", schema=SCHEMA)
    op.drop_table("exam_scheduling_settings_history", schema=SCHEMA)
    op.drop_table("exam_scheduling_settings", schema=SCHEMA)
