"""create development-only seed tables

Creates the synthetic-data tables in the ``examinations`` schema. Touches no
other schema and no existing table. The schema must already exist.

Revision ID: 0001
Revises:
Create Date: 2026-10-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "examinations"


def upgrade() -> None:
    op.create_table(
        "seed_modules",
        sa.Column("module_code", sa.String(16), nullable=False),
        sa.Column("module_name", sa.String(120), nullable=False),
        sa.Column("academic_year", sa.SmallInteger(), nullable=False),
        sa.Column("semester", sa.SmallInteger(), nullable=False),
        sa.Column("specialization", sa.String(60), nullable=False),
        sa.Column("credits", sa.SmallInteger(), nullable=False),
        sa.Column("difficulty", sa.SmallInteger(), nullable=False),
        sa.Column("exam_type", sa.String(20), nullable=False),
        sa.Column("duration_minutes", sa.SmallInteger(), nullable=False),
        sa.Column("reading_time_minutes", sa.SmallInteger(), nullable=False),
        sa.PrimaryKeyConstraint("module_code", name="pk_seed_modules"),
        sa.CheckConstraint("academic_year BETWEEN 1 AND 4", name="academic_year_range"),
        sa.CheckConstraint("semester IN (1, 2)", name="semester_range"),
        sa.CheckConstraint("credits BETWEEN 1 AND 10", name="credits_range"),
        sa.CheckConstraint("difficulty BETWEEN 1 AND 5", name="difficulty_range"),
        sa.CheckConstraint("exam_type IN ('written', 'practical')", name="exam_type_valid"),
        sa.CheckConstraint("duration_minutes > 0", name="duration_positive"),
        sa.CheckConstraint("reading_time_minutes >= 0", name="reading_time_non_negative"),
        schema=SCHEMA,
    )

    op.create_table(
        "seed_students",
        sa.Column("student_id", sa.String(16), nullable=False),
        sa.Column("academic_year", sa.SmallInteger(), nullable=False),
        sa.Column("specialization", sa.String(60), nullable=False),
        sa.PrimaryKeyConstraint("student_id", name="pk_seed_students"),
        sa.CheckConstraint("academic_year BETWEEN 1 AND 4", name="academic_year_range"),
        schema=SCHEMA,
    )

    op.create_table(
        "seed_registrations",
        sa.Column("student_id", sa.String(16), nullable=False),
        sa.Column("module_code", sa.String(16), nullable=False),
        sa.Column("registration_type", sa.String(10), nullable=False),
        sa.PrimaryKeyConstraint("student_id", "module_code", name="pk_seed_registrations"),
        sa.ForeignKeyConstraint(
            ["student_id"],
            [f"{SCHEMA}.seed_students.student_id"],
            name="fk_seed_registrations_student_id_seed_students",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["module_code"],
            [f"{SCHEMA}.seed_modules.module_code"],
            name="fk_seed_registrations_module_code_seed_modules",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "registration_type IN ('normal', 'repeat')",
            name="registration_type_valid",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_seed_registrations_module_code", "seed_registrations", ["module_code"], schema=SCHEMA
    )

    op.create_table(
        "seed_venues",
        sa.Column("venue_id", sa.String(16), nullable=False),
        sa.Column("venue_name", sa.String(80), nullable=False),
        sa.Column("venue_type", sa.String(20), nullable=False),
        sa.Column("normal_capacity", sa.SmallInteger(), nullable=False),
        sa.Column("exam_capacity", sa.SmallInteger(), nullable=False),
        sa.PrimaryKeyConstraint("venue_id", name="pk_seed_venues"),
        sa.UniqueConstraint("venue_name", name="uq_seed_venues_venue_name"),
        sa.CheckConstraint(
            "venue_type IN ('auditorium', 'exam_hall', 'lecture_hall', 'computer_lab')",
            name="venue_type_valid",
        ),
        sa.CheckConstraint("normal_capacity > 0", name="normal_capacity_positive"),
        sa.CheckConstraint(
            "exam_capacity > 0 AND exam_capacity <= normal_capacity",
            name="exam_capacity_range",
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "seed_venue_blocks",
        sa.Column("block_id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("venue_id", sa.String(16), nullable=False),
        sa.Column("block_date", sa.Date(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.Column("source", sa.String(30), nullable=False),
        sa.Column("reason", sa.String(120), nullable=False),
        sa.PrimaryKeyConstraint("block_id", name="pk_seed_venue_blocks"),
        sa.ForeignKeyConstraint(
            ["venue_id"],
            [f"{SCHEMA}.seed_venues.venue_id"],
            name="fk_seed_venue_blocks_venue_id_seed_venues",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "venue_id", "block_date", "start_time", name="uq_seed_venue_blocks_venue_id_block_date_start_time"
        ),
        sa.CheckConstraint("end_time > start_time", name="time_range_valid"),
        sa.CheckConstraint(
            "source IN ('teaching_timetable', 'maintenance')", name="source_valid"
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "seed_exam_periods",
        sa.Column("period_code", sa.String(32), nullable=False),
        sa.Column("period_name", sa.String(120), nullable=False),
        sa.Column("academic_year_label", sa.String(16), nullable=False),
        sa.Column("semester", sa.SmallInteger(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("day_start_time", sa.Time(), nullable=False),
        sa.Column("day_end_time", sa.Time(), nullable=False),
        sa.Column("include_saturdays", sa.Boolean(), nullable=False),
        sa.Column("include_sundays", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("period_code", name="pk_seed_exam_periods"),
        sa.CheckConstraint("end_date >= start_date", name="date_range_valid"),
        sa.CheckConstraint("day_end_time > day_start_time", name="day_hours_valid"),
        schema=SCHEMA,
    )

    op.create_table(
        "seed_exam_period_exclusions",
        sa.Column("period_code", sa.String(32), nullable=False),
        sa.Column("excluded_date", sa.Date(), nullable=False),
        sa.Column("reason", sa.String(120), nullable=False),
        sa.PrimaryKeyConstraint("period_code", "excluded_date", name="pk_seed_exam_period_exclusions"),
        sa.ForeignKeyConstraint(
            ["period_code"],
            [f"{SCHEMA}.seed_exam_periods.period_code"],
            name="fk_seed_exam_period_exclusions_period_code_seed_exam_periods",
            ondelete="RESTRICT",
        ),
        schema=SCHEMA,
    )


def downgrade() -> None:
    # Drops ONLY the seed tables this revision created, in the examinations schema.
    op.drop_table("seed_exam_period_exclusions", schema=SCHEMA)
    op.drop_table("seed_exam_periods", schema=SCHEMA)
    op.drop_table("seed_venue_blocks", schema=SCHEMA)
    op.drop_table("seed_venues", schema=SCHEMA)
    op.drop_index("ix_seed_registrations_module_code", table_name="seed_registrations", schema=SCHEMA)
    op.drop_table("seed_registrations", schema=SCHEMA)
    op.drop_table("seed_students", schema=SCHEMA)
    op.drop_table("seed_modules", schema=SCHEMA)
