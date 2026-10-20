"""saved examination timetables (drafts)

Adds the tables that store generated and independently validated examination
timetables. Touches only the examinations schema. No existing row is changed or
deleted, and no row is inserted.

* exam_timetables: one row per saved timetable (always a new DRAFT with the next
  draft_number of its institution and cycle): cycle, MID/FINAL kind, solver
  status, input fingerprint, scheduling settings version, seating rule version
  and validation result. approved_* / published_* and the one-published index
  are prepared for a later Approve / Publish step.
* exam_timetable_sessions, exam_timetable_rooms, exam_timetable_seats: the
  sessions, their venues and every candidate's logical seat (deleted with the
  timetable).

Downgrade drops the four tables. It refuses to run when timetables were saved
unless ``alembic -x drop_saved_timetables=true downgrade 0009`` is used.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import context, op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "examinations"

STATUS_COLUMNS = (
    "(status = 'DRAFT' AND approved_at IS NULL AND approved_by IS NULL"
    " AND published_at IS NULL AND published_by IS NULL)"
    " OR (status = 'APPROVED' AND approved_at IS NOT NULL AND approved_by IS NOT NULL"
    " AND published_at IS NULL AND published_by IS NULL)"
    " OR (status = 'PUBLISHED' AND approved_at IS NOT NULL AND approved_by IS NOT NULL"
    " AND published_at IS NOT NULL AND published_by IS NOT NULL)"
    " OR status = 'ARCHIVED'"
)


def upgrade() -> None:
    op.create_table(
        "exam_timetables",
        sa.Column("timetable_id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("institution_id", sa.Uuid(), nullable=False),
        sa.Column("period_code", sa.String(32), nullable=False),
        sa.Column("period_name", sa.String(120), nullable=False),
        sa.Column("exam_kind", sa.String(5), nullable=False),
        sa.Column("draft_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(10), server_default="DRAFT", nullable=False),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("mode", sa.String(12), nullable=False),
        sa.Column("selection", sa.String(16), nullable=False),
        sa.Column("module_codes", postgresql.JSONB(), nullable=True),
        sa.Column("solver_status", sa.String(16), nullable=False),
        sa.Column("solver_version", sa.String(40), nullable=True),
        sa.Column("solver_run", postgresql.JSONB(), nullable=False),
        sa.Column("input_fingerprint", sa.String(128), nullable=False),
        sa.Column("settings_version", sa.Integer(), nullable=False),
        sa.Column("settings_fingerprint", sa.String(128), nullable=False),
        sa.Column("seating_ruleset_version", sa.Integer(), nullable=False),
        sa.Column("capacity_fingerprint", sa.String(128), nullable=False),
        sa.Column("validation_passed", sa.Boolean(), nullable=False),
        sa.Column("validation", postgresql.JSONB(), nullable=False),
        sa.Column("inputs", postgresql.JSONB(), nullable=False),
        sa.Column("summary", postgresql.JSONB(), nullable=False),
        sa.Column("workload", postgresql.JSONB(), nullable=True),
        sa.Column("optimization", postgresql.JSONB(), nullable=True),
        sa.Column("excluded_registrations", postgresql.JSONB(), nullable=False),
        sa.Column("notices", postgresql.JSONB(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("approved_by", sa.Uuid(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_by", sa.Uuid(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("timetable_id", name="pk_exam_timetables"),
        sa.UniqueConstraint("institution_id", "period_code", "draft_number",
                            name="uq_exam_timetables_institution_id_period_code_draft_number"),
        sa.CheckConstraint("status IN ('DRAFT', 'APPROVED', 'PUBLISHED', 'ARCHIVED')", name="status_valid"),
        sa.CheckConstraint(STATUS_COLUMNS, name="status_columns"),
        sa.CheckConstraint("exam_kind IN ('MID', 'FINAL')", name="exam_kind_valid"),
        sa.CheckConstraint("mode IN ('feasibility', 'optimized')", name="mode_valid"),
        sa.CheckConstraint("selection IN ('full_cycle', 'partial_cycle', 'listed_modules')", name="selection_valid"),
        sa.CheckConstraint("solver_status IN ('OPTIMAL', 'FEASIBLE')", name="solver_status_valid"),
        sa.CheckConstraint("validation_passed", name="validation_passed"),
        sa.CheckConstraint("draft_number >= 1", name="draft_number_positive"),
        sa.CheckConstraint("settings_version >= 0", name="settings_version_valid"),
        sa.CheckConstraint("seating_ruleset_version >= 0", name="seating_ruleset_version_valid"),
        sa.CheckConstraint("revision >= 1", name="revision_positive"),
        schema=SCHEMA,
    )
    op.create_index(
        "ux_exam_timetables_one_published", "exam_timetables", ["institution_id", "period_code"], unique=True,
        postgresql_where=sa.text("status = 'PUBLISHED'"), schema=SCHEMA,
    )
    op.create_table(
        "exam_timetable_sessions",
        sa.Column("timetable_id", sa.BigInteger(), nullable=False),
        sa.Column("session_id", sa.String(64), nullable=False),
        sa.Column("module_code", sa.String(16), nullable=False),
        sa.Column("module_name", sa.String(200), nullable=False),
        sa.Column("exam_type", sa.String(20), nullable=False),
        sa.Column("candidate_scope", sa.String(12), nullable=False),
        sa.Column("academic_year", sa.SmallInteger(), nullable=False),
        sa.Column("semester", sa.SmallInteger(), nullable=False),
        sa.Column("delivery_mode", sa.String(10), nullable=False),
        sa.Column("session_number", sa.SmallInteger(), nullable=False),
        sa.Column("session_count", sa.SmallInteger(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=False), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=False), nullable=False),
        sa.Column("duration_minutes", sa.SmallInteger(), nullable=False),
        sa.Column("reading_time_minutes", sa.SmallInteger(), nullable=False),
        sa.Column("candidates", sa.Integer(), nullable=False),
        sa.Column("normal_candidates", sa.Integer(), nullable=False),
        sa.Column("repeat_candidates", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("timetable_id", "session_id", name="pk_exam_timetable_sessions"),
        sa.ForeignKeyConstraint(
            ["timetable_id"], [f"{SCHEMA}.exam_timetables.timetable_id"],
            name="fk_exam_timetable_sessions_timetable_id_exam_timetables", ondelete="CASCADE",
        ),
        sa.CheckConstraint("ends_at > starts_at", name="times_ordered"),
        sa.CheckConstraint("session_number BETWEEN 1 AND session_count", name="session_number_valid"),
        sa.CheckConstraint("candidates = normal_candidates + repeat_candidates", name="candidates_sum"),
        schema=SCHEMA,
    )
    op.create_table(
        "exam_timetable_rooms",
        sa.Column("timetable_id", sa.BigInteger(), nullable=False),
        sa.Column("session_id", sa.String(64), nullable=False),
        sa.Column("venue_id", sa.String(16), nullable=False),
        sa.Column("venue_name", sa.String(120), nullable=False),
        sa.Column("venue_type", sa.String(20), nullable=False),
        sa.Column("seats", sa.Integer(), nullable=False),
        sa.Column("exam_capacity", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("timetable_id", "session_id", "venue_id", name="pk_exam_timetable_rooms"),
        sa.ForeignKeyConstraint(
            ["timetable_id", "session_id"],
            [f"{SCHEMA}.exam_timetable_sessions.timetable_id", f"{SCHEMA}.exam_timetable_sessions.session_id"],
            name="fk_exam_timetable_rooms_timetable_id_exam_timetable_sessions", ondelete="CASCADE",
        ),
        sa.CheckConstraint("seats >= 1 AND seats <= exam_capacity", name="seats_within_capacity"),
        schema=SCHEMA,
    )
    op.create_table(
        "exam_timetable_seats",
        sa.Column("timetable_id", sa.BigInteger(), nullable=False),
        sa.Column("session_id", sa.String(64), nullable=False),
        sa.Column("venue_id", sa.String(16), nullable=False),
        sa.Column("seat_number", sa.Integer(), nullable=False),
        sa.Column("student_id", sa.String(16), nullable=False),
        sa.Column("registration_type", sa.String(10), nullable=False),
        sa.PrimaryKeyConstraint("timetable_id", "session_id", "venue_id", "seat_number",
                                name="pk_exam_timetable_seats"),
        sa.ForeignKeyConstraint(
            ["timetable_id", "session_id", "venue_id"],
            [f"{SCHEMA}.exam_timetable_rooms.timetable_id", f"{SCHEMA}.exam_timetable_rooms.session_id",
             f"{SCHEMA}.exam_timetable_rooms.venue_id"],
            name="fk_exam_timetable_seats_timetable_id_exam_timetable_rooms", ondelete="CASCADE",
        ),
        sa.UniqueConstraint("timetable_id", "session_id", "student_id",
                            name="uq_exam_timetable_seats_timetable_id_session_id_student_id"),
        sa.CheckConstraint("seat_number >= 1", name="seat_number_positive"),
        sa.CheckConstraint("registration_type IN ('normal', 'repeat')", name="registration_type_valid"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    allow_loss = context.get_x_argument(as_dictionary=True).get("drop_saved_timetables", "").lower() == "true"
    if not allow_loss:
        # Stop (and change nothing) if any timetable was saved since 0010.
        op.execute(
            "DO $$ BEGIN"
            f" IF EXISTS (SELECT 1 FROM {SCHEMA}.exam_timetables) THEN"
            " RAISE EXCEPTION 'saved examination timetables exist. Export examinations.exam_timetables"
            " (and its sessions, rooms and seats), then run with -x drop_saved_timetables=true';"
            " END IF; END $$"
        )
    op.drop_table("exam_timetable_seats", schema=SCHEMA)
    op.drop_table("exam_timetable_rooms", schema=SCHEMA)
    op.drop_table("exam_timetable_sessions", schema=SCHEMA)
    op.drop_index("ux_exam_timetables_one_published", table_name="exam_timetables", schema=SCHEMA)
    op.drop_table("exam_timetables", schema=SCHEMA)
