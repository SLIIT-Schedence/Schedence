"""configurable examination seating capacity

Replaces the fixed 50% database rule with configurable seating rules.
Touches only the examinations schema. No existing row is changed or deleted.

* Drops the check ck_seed_venues_exam_capacity_half (exam_capacity = normal / 2).
  seed_venues.exam_capacity stays (compatibility); its other check
  (0 < exam_capacity <= normal_capacity) stays too.
* New table exam_seating_rules: one default rule + optional per-venue overrides,
  whole percentages 1-100, a version number for safe concurrent updates.
* New table exam_seating_rule_history: who changed a rule, when, why.
* Inserts the initial default rule (50%) and its history row.

Downgrade drops both tables and restores the 50% check. It refuses to run
when users have changed rules (their history would be lost) unless
``alembic -x drop_seating_history=true downgrade 0004`` is used after the
history was exported.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "examinations"
EXAM_CAPACITY_HALF = op.f("ck_seed_venues_exam_capacity_half")
SCOPE_VALID = "(scope = 'default' AND venue_id IS NULL) OR (scope = 'venue' AND venue_id IS NOT NULL)"
INITIAL_DEFAULT = 50
INITIAL_REASON = "Initial default: 50 percent of full capacity (migration 0005)"


def upgrade() -> None:
    op.drop_constraint(EXAM_CAPACITY_HALF, "seed_venues", type_="check", schema=SCHEMA)

    op.create_table(
        "exam_seating_rules",
        sa.Column("rule_id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("scope", sa.String(10), nullable=False),
        sa.Column("venue_id", sa.String(16), nullable=True),
        sa.Column("percentage", sa.SmallInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("rule_id", name="pk_exam_seating_rules"),
        sa.ForeignKeyConstraint(
            ["venue_id"],
            [f"{SCHEMA}.seed_venues.venue_id"],
            name="fk_exam_seating_rules_venue_id_seed_venues",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("venue_id", name="uq_exam_seating_rules_venue_id"),
        sa.CheckConstraint("percentage BETWEEN 1 AND 100", name="percentage_range"),
        sa.CheckConstraint(SCOPE_VALID, name="scope_valid"),
        sa.CheckConstraint("version >= 1", name="version_positive"),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_exam_seating_rules_default",
        "exam_seating_rules",
        ["scope"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("scope = 'default'"),
    )

    op.create_table(
        "exam_seating_rule_history",
        sa.Column("history_id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("rule_id", sa.BigInteger(), nullable=False),
        sa.Column("action", sa.String(10), nullable=False),
        sa.Column("scope", sa.String(10), nullable=False),
        sa.Column("venue_id", sa.String(16), nullable=True),
        sa.Column("old_percentage", sa.SmallInteger(), nullable=True),
        sa.Column("new_percentage", sa.SmallInteger(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("changed_by", sa.Uuid(), nullable=True),
        sa.Column("changed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.PrimaryKeyConstraint("history_id", name="pk_exam_seating_rule_history"),
        sa.CheckConstraint(
            "action IN ('create', 'update', 'delete')", name="action_valid"
        ),
        sa.CheckConstraint(SCOPE_VALID, name="scope_valid"),
        sa.CheckConstraint(
            "(old_percentage IS NULL OR old_percentage BETWEEN 1 AND 100)"
            " AND (new_percentage IS NULL OR new_percentage BETWEEN 1 AND 100)",
            name="percentage_range",
        ),
        sa.CheckConstraint(
            "(action = 'create' AND old_percentage IS NULL AND new_percentage IS NOT NULL)"
            " OR (action = 'update' AND old_percentage IS NOT NULL AND new_percentage IS NOT NULL)"
            " OR (action = 'delete' AND old_percentage IS NOT NULL AND new_percentage IS NULL)",
            name="action_values",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_exam_seating_rule_history_venue_id", "exam_seating_rule_history", ["venue_id"], schema=SCHEMA
    )

    op.execute(
        f"INSERT INTO {SCHEMA}.exam_seating_rules (scope, venue_id, percentage, version, reason, updated_by)"
        f" VALUES ('default', NULL, {INITIAL_DEFAULT}, 1, '{INITIAL_REASON}', NULL)"
    )
    op.execute(
        f"INSERT INTO {SCHEMA}.exam_seating_rule_history"
        " (rule_id, action, scope, venue_id, old_percentage, new_percentage, version, changed_by, reason)"
        f" SELECT rule_id, 'create', 'default', NULL, NULL, percentage, version, NULL, reason"
        f" FROM {SCHEMA}.exam_seating_rules WHERE scope = 'default'"
    )


def downgrade() -> None:
    allow_loss = context.get_x_argument(as_dictionary=True).get("drop_seating_history", "").lower() == "true"
    if not allow_loss:
        # Stop (and change nothing) if a user has changed a rule since 0005.
        op.execute(
            "DO $$ BEGIN"
            f" IF EXISTS (SELECT 1 FROM {SCHEMA}.exam_seating_rule_history WHERE changed_by IS NOT NULL) THEN"
            " RAISE EXCEPTION 'exam seating rules were changed by users. Export"
            " examinations.exam_seating_rule_history, then run with -x drop_seating_history=true';"
            " END IF; END $$"
        )
    op.drop_index("ix_exam_seating_rule_history_venue_id", table_name="exam_seating_rule_history", schema=SCHEMA)
    op.drop_table("exam_seating_rule_history", schema=SCHEMA)
    op.drop_index("uq_exam_seating_rules_default", table_name="exam_seating_rules", schema=SCHEMA)
    op.drop_table("exam_seating_rules", schema=SCHEMA)
    # seed_venues.exam_capacity was never changed by 0005, so the old check holds again.
    op.create_check_constraint(EXAM_CAPACITY_HALF, "seed_venues", "exam_capacity = normal_capacity / 2", schema=SCHEMA)
