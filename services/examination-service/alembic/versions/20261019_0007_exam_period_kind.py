"""examination period kind (MID / FINAL)

Adds ``seed_exam_periods.exam_kind``. Touches only the examinations schema.
No row is deleted.

* New column exam_kind, NOT NULL, default 'FINAL'. PostgreSQL fills every
  existing period with the default, so all existing periods become FINAL
  (they were all final examinations).
* New check: exam_kind IN ('MID', 'FINAL').

Downgrade drops the check and the column. It refuses to run when a MID period
exists (it would silently look like a final period) unless
``alembic -x drop_exam_kind=true downgrade 0006`` is used.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.sql.elements import conv

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "examinations"
EXAM_KIND_CHECK = conv("ck_seed_exam_periods_exam_kind_valid")


def upgrade() -> None:
    op.add_column(
        "seed_exam_periods",
        sa.Column("exam_kind", sa.String(10), server_default="FINAL", nullable=False),
        schema=SCHEMA,
    )
    op.create_check_constraint(
        EXAM_KIND_CHECK, "seed_exam_periods", "exam_kind IN ('MID', 'FINAL')", schema=SCHEMA,
    )


def downgrade() -> None:
    allow_loss = context.get_x_argument(as_dictionary=True).get("drop_exam_kind", "").lower() == "true"
    if not allow_loss:
        # Stop (and change nothing) if a MID period was added since 0007.
        op.execute(
            "DO $$ BEGIN"
            f" IF EXISTS (SELECT 1 FROM {SCHEMA}.seed_exam_periods WHERE exam_kind <> 'FINAL') THEN"
            " RAISE EXCEPTION 'MID examination periods exist. Remove them or run with"
            " -x drop_exam_kind=true';"
            " END IF; END $$"
        )
    op.drop_constraint(EXAM_KIND_CHECK, "seed_exam_periods", type_="check", schema=SCHEMA)
    op.drop_column("seed_exam_periods", "exam_kind", schema=SCHEMA)
