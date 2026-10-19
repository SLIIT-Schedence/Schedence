"""examination cycle modules (explicit period-to-module mapping)

Adds ``seed_exam_period_modules``: which modules are examined in which
examination cycle (period), and which candidates sit them. Touches only the
examinations schema. No existing row is changed or deleted, and no row is
inserted: no module is mapped to any cycle until the mapping is configured.

* (period_code, module_code) primary key; both are foreign keys (RESTRICT).
* candidate_scope: 'all' (normal and repeat registrations) or 'repeat_only'.

Downgrade drops the table. It refuses to run when mappings exist (they would be
lost) unless ``alembic -x drop_exam_cycle_modules=true downgrade 0008`` is used.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "examinations"


def upgrade() -> None:
    op.create_table(
        "seed_exam_period_modules",
        sa.Column("period_code", sa.String(32), nullable=False),
        sa.Column("module_code", sa.String(16), nullable=False),
        sa.Column("candidate_scope", sa.String(12), server_default="all", nullable=False),
        sa.PrimaryKeyConstraint("period_code", "module_code", name="pk_seed_exam_period_modules"),
        sa.ForeignKeyConstraint(
            ["period_code"],
            [f"{SCHEMA}.seed_exam_periods.period_code"],
            name="fk_seed_exam_period_modules_period_code_seed_exam_periods",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["module_code"],
            [f"{SCHEMA}.seed_modules.module_code"],
            name="fk_seed_exam_period_modules_module_code_seed_modules",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("candidate_scope IN ('all', 'repeat_only')", name="candidate_scope_valid"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_seed_exam_period_modules_module_code", "seed_exam_period_modules", ["module_code"], schema=SCHEMA,
    )


def downgrade() -> None:
    allow_loss = context.get_x_argument(as_dictionary=True).get("drop_exam_cycle_modules", "").lower() == "true"
    if not allow_loss:
        # Stop (and change nothing) if any cycle mapping was configured since 0009.
        op.execute(
            "DO $$ BEGIN"
            f" IF EXISTS (SELECT 1 FROM {SCHEMA}.seed_exam_period_modules) THEN"
            " RAISE EXCEPTION 'examination cycle module mappings exist. Export"
            " examinations.seed_exam_period_modules, then run with -x drop_exam_cycle_modules=true';"
            " END IF; END $$"
        )
    op.drop_index("ix_seed_exam_period_modules_module_code", table_name="seed_exam_period_modules", schema=SCHEMA)
    op.drop_table("seed_exam_period_modules", schema=SCHEMA)
