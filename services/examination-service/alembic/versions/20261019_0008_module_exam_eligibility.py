"""module examination eligibility (MID / FINAL) and Block Mode

Adds examination eligibility and delivery fields to ``seed_modules``. Touches
only the examinations schema. No row is deleted or changed beyond the new
columns' defaults.

* has_final_exam (default true), has_mid_exam (default false): every existing
  module keeps exactly its old behaviour, a final examination only.
* mid_duration_minutes (nullable): set exactly when has_mid_exam. The existing
  duration_minutes stays the FINAL duration.
* delivery_mode (default 'regular'): 'regular' or 'block'.
* block_category (nullable): 'pro_rata' or 'full_module', only for block modules.
* Checks keep these fields consistent.

No eligibility, duration or block classification is assumed for real modules.

Downgrade drops the checks and columns. It refuses to run when a module has
non-default values (they would be lost) unless
``alembic -x drop_module_exam_settings=true downgrade 0007`` is used.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.sql.elements import conv

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "examinations"
# NULL-safe conditions: a CHECK whose result is NULL passes, so "IS NOT NULL" is
# tested first (has_mid_exam with a NULL duration, or a block module with a NULL
# category, must be rejected).
CHECKS = (
    (conv("ck_seed_modules_mid_exam_duration_valid"),
     "(has_mid_exam AND mid_duration_minutes IS NOT NULL AND mid_duration_minutes > 0)"
     " OR (NOT has_mid_exam AND mid_duration_minutes IS NULL)"),
    (conv("ck_seed_modules_delivery_mode_valid"), "delivery_mode IN ('regular', 'block')"),
    (conv("ck_seed_modules_block_category_valid"),
     "(delivery_mode = 'regular' AND block_category IS NULL)"
     " OR (delivery_mode = 'block' AND block_category IS NOT NULL AND block_category IN ('pro_rata', 'full_module'))"),
)
COLUMNS = ("has_final_exam", "has_mid_exam", "mid_duration_minutes", "delivery_mode", "block_category")


def upgrade() -> None:
    op.add_column("seed_modules", sa.Column("has_final_exam", sa.Boolean(), server_default=sa.true(),
                                            nullable=False), schema=SCHEMA)
    op.add_column("seed_modules", sa.Column("has_mid_exam", sa.Boolean(), server_default=sa.false(),
                                            nullable=False), schema=SCHEMA)
    op.add_column("seed_modules", sa.Column("mid_duration_minutes", sa.SmallInteger(), nullable=True), schema=SCHEMA)
    op.add_column("seed_modules", sa.Column("delivery_mode", sa.String(10), server_default="regular",
                                            nullable=False), schema=SCHEMA)
    op.add_column("seed_modules", sa.Column("block_category", sa.String(20), nullable=True), schema=SCHEMA)
    for name, condition in CHECKS:
        op.create_check_constraint(name, "seed_modules", condition, schema=SCHEMA)


def downgrade() -> None:
    allow_loss = context.get_x_argument(as_dictionary=True).get("drop_module_exam_settings", "").lower() == "true"
    if not allow_loss:
        # Stop (and change nothing) if any module has a MID exam, no FINAL exam, or Block Mode.
        op.execute(
            "DO $$ BEGIN"
            f" IF EXISTS (SELECT 1 FROM {SCHEMA}.seed_modules"
            " WHERE has_mid_exam OR NOT has_final_exam OR delivery_mode <> 'regular') THEN"
            " RAISE EXCEPTION 'modules have MID, no-FINAL or Block Mode settings. Export them, then run with"
            " -x drop_module_exam_settings=true';"
            " END IF; END $$"
        )
    for name, _ in reversed(CHECKS):
        op.drop_constraint(name, "seed_modules", type_="check", schema=SCHEMA)
    for column in reversed(COLUMNS):
        op.drop_column("seed_modules", column, schema=SCHEMA)
