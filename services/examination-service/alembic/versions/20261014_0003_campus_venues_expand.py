"""campus venues: expand (safe while the old fictional venues still exist)

Non-destructive. No data is deleted or changed; the 9 old venues and 27 old
blocks stay valid. Run the venue replacement after this revision, then 0004.

* seed_venues.building, seed_venues.floor: new, nullable until 0004.
* seed_venues.venue_type also allows smart_classroom and byod_lab
  (old auditorium / exam_hall still allowed for the old rows).
* seed_venue_blocks.source also allows simulated_teaching / simulated_maintenance.
* seed_venue_blocks.source_ref: ID of a synced booking (e.g. a Teaching Timetable
  session), unique per source, so a sync never duplicates a booking.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.sql.elements import conv
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "examinations"
VENUE_TYPE_CHECK = conv("ck_seed_venues_venue_type_valid")
SOURCE_CHECK = conv("ck_seed_venue_blocks_source_valid")
SOURCE_REF_UNIQUE = conv("uq_seed_venue_blocks_source_source_ref")

OLD_VENUE_TYPES = "venue_type IN ('auditorium', 'exam_hall', 'lecture_hall', 'computer_lab')"
EXPANDED_VENUE_TYPES = (
    "venue_type IN ('auditorium', 'exam_hall', 'lecture_hall', 'smart_classroom', 'computer_lab', 'byod_lab')"
)
OLD_SOURCES = "source IN ('teaching_timetable', 'maintenance')"
EXPANDED_SOURCES = "source IN ('teaching_timetable', 'maintenance', 'simulated_teaching', 'simulated_maintenance')"


def upgrade() -> None:
    op.add_column("seed_venues", sa.Column("building", sa.String(8), nullable=True), schema=SCHEMA)
    op.add_column("seed_venues", sa.Column("floor", sa.SmallInteger(), nullable=True), schema=SCHEMA)

    op.drop_constraint(VENUE_TYPE_CHECK, "seed_venues", type_="check", schema=SCHEMA)
    op.create_check_constraint(VENUE_TYPE_CHECK, "seed_venues", EXPANDED_VENUE_TYPES, schema=SCHEMA)

    op.drop_constraint(SOURCE_CHECK, "seed_venue_blocks", type_="check", schema=SCHEMA)
    op.create_check_constraint(SOURCE_CHECK, "seed_venue_blocks", EXPANDED_SOURCES, schema=SCHEMA)

    op.add_column("seed_venue_blocks", sa.Column("source_ref", sa.String(64), nullable=True), schema=SCHEMA)
    op.create_unique_constraint(SOURCE_REF_UNIQUE, "seed_venue_blocks", ["source", "source_ref"], schema=SCHEMA)


def downgrade() -> None:
    # Only valid while no campus venue / simulated block rows exist: restore the
    # old data first (see the service README, "Venue replacement").
    op.drop_constraint(SOURCE_REF_UNIQUE, "seed_venue_blocks", type_="unique", schema=SCHEMA)
    op.drop_column("seed_venue_blocks", "source_ref", schema=SCHEMA)
    op.drop_constraint(SOURCE_CHECK, "seed_venue_blocks", type_="check", schema=SCHEMA)
    op.create_check_constraint(SOURCE_CHECK, "seed_venue_blocks", OLD_SOURCES, schema=SCHEMA)
    op.drop_constraint(VENUE_TYPE_CHECK, "seed_venues", type_="check", schema=SCHEMA)
    op.create_check_constraint(VENUE_TYPE_CHECK, "seed_venues", OLD_VENUE_TYPES, schema=SCHEMA)
    op.drop_column("seed_venues", "floor", schema=SCHEMA)
    op.drop_column("seed_venues", "building", schema=SCHEMA)
