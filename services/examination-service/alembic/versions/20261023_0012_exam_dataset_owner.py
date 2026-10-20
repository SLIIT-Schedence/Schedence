"""institution that owns the imported examination dataset

Touches only the examinations schema. No existing row is changed or deleted.

The service no longer has a configured institution (``SLIIT_INSTITUTION_ID``).
Saved timetables and scheduling settings already store their institution. The
imported dataset (seed tables and seating rules) has global keys, so one new
table names the institution allowed to use it:

* exam_dataset_owner: at most one row (``singleton`` is always true).

Upgrade fills it in when the existing rows name exactly one institution
(``institution_id`` of exam_timetables, exam_timetable_history,
exam_scheduling_settings and exam_scheduling_settings_history; that is the old
``SLIIT_INSTITUTION_ID``). With no such rows the table stays empty: assign the
owner later with ``python -m app.dataset_owner``. With several it stops, unless
the owner is given: ``alembic -x dataset_owner=<uuid> upgrade 0012``.

Downgrade drops the table.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-23
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "examinations"
TABLE = "exam_dataset_owner"
REASON = "Migration 0012: the institution of the existing saved timetables and settings"
SOURCES = ("exam_timetables", "exam_timetable_history", "exam_scheduling_settings", "exam_scheduling_settings_history")

# One statement, so it also works in offline (--sql) mode. The values are read from the
# database, never from configuration, and are never printed.
BACKFILL = f"""
DO $$
DECLARE
    owners uuid[];
BEGIN
    -- UNION removes duplicates: one element per institution.
    owners := ARRAY({" UNION ".join(f"SELECT institution_id FROM {SCHEMA}.{t}" for t in SOURCES)});
    IF cardinality(owners) > 1 THEN
        RAISE EXCEPTION 'Several institutions have examination data. Run: alembic -x dataset_owner=<uuid> upgrade 0012';
    ELSIF cardinality(owners) = 1 THEN
        INSERT INTO {SCHEMA}.{TABLE} (singleton, institution_id, reason) VALUES (true, owners[1], '{REASON}');
    END IF;
END $$
"""


def _explicit_owner() -> uuid.UUID | None:
    value = context.get_x_argument(as_dictionary=True).get("dataset_owner", "").strip()
    if not value:
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        raise RuntimeError("-x dataset_owner must be an institution ID (a UUID).") from None


def upgrade() -> None:
    explicit = _explicit_owner()
    op.create_table(
        TABLE,
        sa.Column("singleton", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("institution_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("singleton", name=op.f("ck_exam_dataset_owner_single_row")),
        sa.PrimaryKeyConstraint("singleton", name=op.f("pk_exam_dataset_owner")),
        schema=SCHEMA,
    )
    if explicit is None:
        op.execute(BACKFILL)
    else:
        # A parsed UUID: safe to render as a literal (offline mode has no bind parameters).
        op.execute(f"INSERT INTO {SCHEMA}.{TABLE} (singleton, institution_id, reason) "
                   f"VALUES (true, '{explicit}', 'Migration 0012: given with -x dataset_owner')")


def downgrade() -> None:
    op.drop_table(TABLE, schema=SCHEMA)
