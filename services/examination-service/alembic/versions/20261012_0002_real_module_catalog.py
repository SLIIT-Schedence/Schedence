"""support the real module catalogue

Non-destructive: existing SYN rows stay valid and no data is deleted.

* seed_modules.exam_type now allows written / computer_based / hybrid
  (legacy 'practical' is still allowed for the SYN rows).
* seed_modules.has_extra_reading_time: the source Yes/No flag. Back-filled
  for existing rows from reading_time_minutes > 0.
* seed_modules.specialization becomes nullable (legacy; new rows use the link table).
* New table seed_module_specializations (module_code, specialization).

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "examinations"
EXAM_TYPE_CHECK = op.f("ck_seed_modules_exam_type_valid")


def upgrade() -> None:
    op.add_column(
        "seed_modules",
        sa.Column("has_extra_reading_time", sa.Boolean(), server_default=sa.false(), nullable=False),
        schema=SCHEMA,
    )
    op.execute(f"UPDATE {SCHEMA}.seed_modules SET has_extra_reading_time = (reading_time_minutes > 0)")

    op.alter_column("seed_modules", "specialization", existing_type=sa.String(60), nullable=True, schema=SCHEMA)

    op.drop_constraint(EXAM_TYPE_CHECK, "seed_modules", type_="check", schema=SCHEMA)
    op.create_check_constraint(
        EXAM_TYPE_CHECK,
        "seed_modules",
        "exam_type IN ('written', 'computer_based', 'hybrid', 'practical')",
        schema=SCHEMA,
    )

    op.create_table(
        "seed_module_specializations",
        sa.Column("module_code", sa.String(16), nullable=False),
        sa.Column("specialization", sa.String(30), nullable=False),
        sa.PrimaryKeyConstraint("module_code", "specialization", name="pk_seed_module_specializations"),
        sa.ForeignKeyConstraint(
            ["module_code"],
            [f"{SCHEMA}.seed_modules.module_code"],
            name="fk_seed_module_specializations_module_code_seed_modules",
            ondelete="RESTRICT",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_seed_module_specializations_specialization",
        "seed_module_specializations",
        ["specialization"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    # Only valid while no new-catalogue rows exist (they use the new exam types
    # and a NULL specialization).
    op.drop_index("ix_seed_module_specializations_specialization", table_name="seed_module_specializations", schema=SCHEMA)
    op.drop_table("seed_module_specializations", schema=SCHEMA)
    op.drop_constraint(EXAM_TYPE_CHECK, "seed_modules", type_="check", schema=SCHEMA)
    op.create_check_constraint(
        EXAM_TYPE_CHECK, "seed_modules", "exam_type IN ('written', 'practical')", schema=SCHEMA
    )
    op.alter_column("seed_modules", "specialization", existing_type=sa.String(60), nullable=False, schema=SCHEMA)
    op.drop_column("seed_modules", "has_extra_reading_time", schema=SCHEMA)
