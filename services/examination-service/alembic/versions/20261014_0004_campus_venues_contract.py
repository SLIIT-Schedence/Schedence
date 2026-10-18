"""campus venues: contract (run only AFTER the venue replacement)

Tightens the schema to the campus venue catalogue. PostgreSQL runs this whole
revision in one transaction: if any old fictional venue or block is still
present, a check fails and nothing changes.

* seed_venues.building and seed_venues.floor become NOT NULL.
* seed_venues.venue_type allows only lecture_hall, smart_classroom,
  computer_lab and byod_lab.
* New check: exam_capacity = normal_capacity / 2 (the 50% examination rule,
  integer division = floor).
* The block natural key includes the source, so a real booking and a
  maintenance block may start at the same time in the same venue.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.sql.elements import conv
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "examinations"
VENUE_TYPE_CHECK = conv("ck_seed_venues_venue_type_valid")
EXAM_CAPACITY_HALF = conv("ck_seed_venues_exam_capacity_half")
OLD_BLOCK_KEY = conv("uq_seed_venue_blocks_venue_id_block_date_start_time")
NEW_BLOCK_KEY = conv("uq_seed_venue_blocks_source_venue_id_block_date_start_time")

EXPANDED_VENUE_TYPES = (
    "venue_type IN ('auditorium', 'exam_hall', 'lecture_hall', 'smart_classroom', 'computer_lab', 'byod_lab')"
)
CAMPUS_VENUE_TYPES = "venue_type IN ('lecture_hall', 'smart_classroom', 'computer_lab', 'byod_lab')"


def upgrade() -> None:
    op.alter_column("seed_venues", "building", existing_type=sa.String(8), nullable=False, schema=SCHEMA)
    op.alter_column("seed_venues", "floor", existing_type=sa.SmallInteger(), nullable=False, schema=SCHEMA)

    op.drop_constraint(VENUE_TYPE_CHECK, "seed_venues", type_="check", schema=SCHEMA)
    op.create_check_constraint(VENUE_TYPE_CHECK, "seed_venues", CAMPUS_VENUE_TYPES, schema=SCHEMA)
    op.create_check_constraint(EXAM_CAPACITY_HALF, "seed_venues", "exam_capacity = normal_capacity / 2", schema=SCHEMA)

    op.drop_constraint(OLD_BLOCK_KEY, "seed_venue_blocks", type_="unique", schema=SCHEMA)
    op.create_unique_constraint(
        NEW_BLOCK_KEY, "seed_venue_blocks", ["source", "venue_id", "block_date", "start_time"], schema=SCHEMA
    )


def downgrade() -> None:
    # Fails (and changes nothing) if two blocks of different sources share a
    # venue and start time; remove one of them first.
    op.drop_constraint(NEW_BLOCK_KEY, "seed_venue_blocks", type_="unique", schema=SCHEMA)
    op.create_unique_constraint(
        OLD_BLOCK_KEY, "seed_venue_blocks", ["venue_id", "block_date", "start_time"], schema=SCHEMA
    )
    op.drop_constraint(EXAM_CAPACITY_HALF, "seed_venues", type_="check", schema=SCHEMA)
    op.drop_constraint(VENUE_TYPE_CHECK, "seed_venues", type_="check", schema=SCHEMA)
    op.create_check_constraint(VENUE_TYPE_CHECK, "seed_venues", EXPANDED_VENUE_TYPES, schema=SCHEMA)
    op.alter_column("seed_venues", "floor", existing_type=sa.SmallInteger(), nullable=True, schema=SCHEMA)
    op.alter_column("seed_venues", "building", existing_type=sa.String(8), nullable=True, schema=SCHEMA)
