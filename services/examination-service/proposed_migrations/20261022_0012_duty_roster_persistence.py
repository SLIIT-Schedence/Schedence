"""duty roster persistence (PROPOSED - not part of the active Alembic chain)

Fairness-Aware Staff Allocation for Examination Duties: saved duty rosters with a
DRAFT -> VALIDATED -> APPROVED -> PUBLISHED lifecycle, an audit trail, swap / emergency
replacement requests, and saved seating drafts.

STATUS: this file is a proposal. It sits OUTSIDE ``alembic/versions`` on purpose, so the
shared migration chain, its head and the teammate-owned migration tests are unchanged and
nothing can be applied by accident. After the migration has been reviewed and approved:
copy it to ``alembic/versions`` (keeping the revision ids), register
``app.duty_allocation.persistence.models`` on ``app.db.base.Base`` and run it yourself
(never against Neon without explicit approval).

Touches only the examinations schema. Adds 6 tables; changes and deletes no existing row
or table; inserts nothing; references no other service's data and no ``m3_demo`` table.

Downgrade drops the tables. It refuses to run when rosters were saved (their history would
be lost) unless ``alembic -x drop_duty_rosters=true downgrade 0011`` is used after export.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import context, op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "examinations"
ROSTER_STATUSES = "'DRAFT', 'VALIDATED', 'APPROVED', 'PUBLISHED', 'REJECTED', 'SUPERSEDED'"
WORKING_STATUSES = "'DRAFT', 'VALIDATED', 'APPROVED'"
NOW = sa.func.now()


def _ts(name: str, *, nullable: bool = True, default: bool = False, tz: bool = True) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=tz), server_default=NOW if default else None, nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "duty_rosters",
        sa.Column("roster_id", sa.Uuid(), nullable=False),
        sa.Column("institution_id", sa.Uuid(), nullable=False),
        sa.Column("period_code", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column("algorithm_mode", sa.String(10), nullable=False),
        sa.Column("roster_label", sa.String(10), nullable=False),
        sa.Column("timetable_reference", sa.String(200), nullable=False),
        sa.Column("timetable_fingerprint", sa.String(200), nullable=False),
        sa.Column("timetable_verified", sa.Boolean(), nullable=False),
        sa.Column("input_digest", sa.String(64), nullable=False),
        sa.Column("data_source", sa.String(30), nullable=False),
        sa.Column("synthetic", sa.Boolean(), nullable=False),
        sa.Column("fairness_metrics", postgresql.JSONB(), nullable=False),
        sa.Column("validation", postgresql.JSONB(), nullable=False),
        sa.Column("parent_roster_id", sa.Uuid(), nullable=True),
        sa.Column("published_timetable_reference", sa.String(200), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        _ts("created_at", nullable=False, default=True),
        _ts("updated_at", nullable=False, default=True),
        sa.Column("validated_by", sa.Uuid(), nullable=True), _ts("validated_at"),
        sa.Column("approved_by", sa.Uuid(), nullable=True), _ts("approved_at"),
        sa.Column("published_by", sa.Uuid(), nullable=True), _ts("published_at"),
        sa.Column("rejected_by", sa.Uuid(), nullable=True), _ts("rejected_at"),
        sa.Column("rejection_reason", sa.String(500), nullable=True),
        sa.PrimaryKeyConstraint("roster_id", name="pk_duty_rosters"),
        sa.ForeignKeyConstraint(["parent_roster_id"], [f"{SCHEMA}.duty_rosters.roster_id"],
                                name="fk_duty_rosters_parent_roster_id_duty_rosters"),
        sa.UniqueConstraint("institution_id", "period_code", "version",
                            name="uq_duty_rosters_institution_id_period_code_version"),
        sa.CheckConstraint(f"status IN ({ROSTER_STATUSES})", name="status_valid"),
        sa.CheckConstraint("version >= 1", name="version_positive"),
        sa.CheckConstraint("row_version >= 1", name="row_version_positive"),
        sa.CheckConstraint("algorithm_mode IN ('BASELINE', 'FAIR', 'COMPARE')", name="mode_valid"),
        sa.CheckConstraint("roster_label IN ('BASELINE', 'FAIR')", name="label_valid"),
        sa.CheckConstraint("char_length(input_digest) = 64", name="digest_length"),
        sa.CheckConstraint("jsonb_typeof(fairness_metrics) = 'object'", name="metrics_object"),
        sa.CheckConstraint("jsonb_typeof(validation) = 'object'", name="validation_object"),
        sa.CheckConstraint(
            "status <> 'PUBLISHED' OR (timetable_verified AND NOT synthetic AND published_timetable_reference IS NOT NULL)",
            name="published_needs_verified_timetable"),
        schema=SCHEMA,
    )
    op.create_index("uq_duty_rosters_one_working_per_period", "duty_rosters", ["institution_id", "period_code"],
                    unique=True, schema=SCHEMA, postgresql_where=sa.text(f"status IN ({WORKING_STATUSES})"))
    op.create_index("uq_duty_rosters_one_published_per_period", "duty_rosters", ["institution_id", "period_code"],
                    unique=True, schema=SCHEMA, postgresql_where=sa.text("status = 'PUBLISHED'"))
    op.create_index("ix_duty_rosters_institution_id_period_code", "duty_rosters", ["institution_id", "period_code"],
                    schema=SCHEMA)

    op.create_table(
        "duty_roster_assignments",
        sa.Column("assignment_id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("roster_id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.String(64), nullable=False),
        sa.Column("venue_id", sa.String(64), nullable=False),
        sa.Column("staff_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(12), nullable=False),
        sa.Column("staff_number", sa.String(40), nullable=True),
        sa.Column("module_code", sa.String(20), nullable=True),
        _ts("starts_at", nullable=False, tz=False),
        _ts("ends_at", nullable=False, tz=False),
        sa.PrimaryKeyConstraint("assignment_id", name="pk_duty_roster_assignments"),
        sa.ForeignKeyConstraint(["roster_id"], [f"{SCHEMA}.duty_rosters.roster_id"],
                                name="fk_duty_roster_assignments_roster_id_duty_rosters"),
        sa.UniqueConstraint("roster_id", "session_id", "staff_id", name="uq_duty_roster_assignments_roster_session_staff"),
        sa.CheckConstraint("role IN ('SUPERVISOR', 'INVIGILATOR')", name="role_valid"),
        sa.CheckConstraint("ends_at > starts_at", name="interval_valid"),
        schema=SCHEMA,
    )
    op.create_index("ix_duty_roster_assignments_roster_id", "duty_roster_assignments", ["roster_id"], schema=SCHEMA)
    op.create_index("ix_duty_roster_assignments_staff_id_starts_at_ends_at", "duty_roster_assignments",
                    ["staff_id", "starts_at", "ends_at"], schema=SCHEMA)

    op.create_table(
        "duty_roster_events",
        sa.Column("event_id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("roster_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("from_status", sa.String(12), nullable=True),
        sa.Column("to_status", sa.String(12), nullable=True),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        _ts("at", nullable=False, default=True),
        sa.Column("reason", sa.String(500), nullable=True),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("event_id", name="pk_duty_roster_events"),
        sa.ForeignKeyConstraint(["roster_id"], [f"{SCHEMA}.duty_rosters.roster_id"],
                                name="fk_duty_roster_events_roster_id_duty_rosters"),
        sa.CheckConstraint("jsonb_typeof(details) = 'object'", name="details_object"),
        schema=SCHEMA,
    )
    op.create_index("ix_duty_roster_events_roster_id", "duty_roster_events", ["roster_id"], schema=SCHEMA)

    op.create_table(
        "duty_change_requests",
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("roster_id", sa.Uuid(), nullable=False),
        sa.Column("institution_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("base_row_version", sa.Integer(), nullable=False),
        sa.Column("removed", postgresql.JSONB(), nullable=False),
        sa.Column("added", postgresql.JSONB(), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("parameters", postgresql.JSONB(), nullable=False),
        sa.Column("summary", postgresql.JSONB(), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        _ts("requested_at", nullable=False, default=True),
        sa.Column("decided_by", sa.Uuid(), nullable=True), _ts("decided_at"),
        sa.Column("decision_reason", sa.String(500), nullable=True),
        sa.Column("applied_by", sa.Uuid(), nullable=True), _ts("applied_at"),
        sa.PrimaryKeyConstraint("request_id", name="pk_duty_change_requests"),
        sa.ForeignKeyConstraint(["roster_id"], [f"{SCHEMA}.duty_rosters.roster_id"],
                                name="fk_duty_change_requests_roster_id_duty_rosters"),
        sa.CheckConstraint("kind IN ('SWAP', 'REPLACEMENT')", name="kind_valid"),
        sa.CheckConstraint("status IN ('REQUESTED', 'APPROVED', 'REJECTED', 'APPLIED', 'CANCELLED', 'STALE')",
                           name="status_valid"),
        sa.CheckConstraint("base_row_version >= 1", name="base_row_version_positive"),
        sa.CheckConstraint("jsonb_typeof(removed) = 'array' AND jsonb_typeof(added) = 'array'", name="changes_arrays"),
        schema=SCHEMA,
    )
    op.create_index("ix_duty_change_requests_roster_id", "duty_change_requests", ["roster_id"], schema=SCHEMA)

    op.create_table(
        "seating_drafts",
        sa.Column("draft_id", sa.Uuid(), nullable=False),
        sa.Column("institution_id", sa.Uuid(), nullable=False),
        sa.Column("period_code", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("timetable_reference", sa.String(200), nullable=False),
        sa.Column("data_source", sa.String(30), nullable=False),
        sa.Column("synthetic", sa.Boolean(), nullable=False),
        sa.Column("seat_state", sa.String(12), nullable=False),
        sa.Column("capacity_rules_source", sa.String(200), nullable=False),
        sa.Column("summary", postgresql.JSONB(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        _ts("created_at", nullable=False, default=True),
        sa.PrimaryKeyConstraint("draft_id", name="pk_seating_drafts"),
        sa.UniqueConstraint("institution_id", "period_code", "version",
                            name="uq_seating_drafts_institution_id_period_code_version"),
        sa.CheckConstraint("status IN ('DRAFT', 'SUPERSEDED')", name="status_valid"),
        sa.CheckConstraint("version >= 1", name="version_positive"),
        sa.CheckConstraint("seat_state IN ('SYNTHETIC', 'PROVISIONAL', 'VERIFIED')", name="seat_state_valid"),
        sa.CheckConstraint("seat_state <> 'VERIFIED' OR NOT synthetic", name="synthetic_never_verified"),
        sa.CheckConstraint("jsonb_typeof(summary) = 'object'", name="summary_object"),
        schema=SCHEMA,
    )
    op.create_index("uq_seating_drafts_one_current_per_period", "seating_drafts", ["institution_id", "period_code"],
                    unique=True, schema=SCHEMA, postgresql_where=sa.text("status = 'DRAFT'"))

    op.create_table(
        "seating_draft_seats",
        sa.Column("seat_id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("draft_id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.String(64), nullable=False),
        sa.Column("venue_id", sa.String(64), nullable=False),
        sa.Column("student_id", sa.String(32), nullable=False),
        sa.Column("module_code", sa.String(20), nullable=False),
        sa.Column("logical_seat_number", sa.Integer(), nullable=False),
        sa.Column("physical_seat_number", sa.Integer(), nullable=False),
        sa.Column("physical_seat_label", sa.String(40), nullable=False),
        sa.Column("verification_status", sa.String(12), nullable=False),
        sa.PrimaryKeyConstraint("seat_id", name="pk_seating_draft_seats"),
        sa.ForeignKeyConstraint(["draft_id"], [f"{SCHEMA}.seating_drafts.draft_id"],
                                name="fk_seating_draft_seats_draft_id_seating_drafts"),
        sa.UniqueConstraint("draft_id", "session_id", "student_id", name="uq_seating_draft_seats_draft_session_student"),
        sa.UniqueConstraint("draft_id", "session_id", "venue_id", "physical_seat_number",
                            name="uq_seating_draft_seats_draft_session_venue_seat"),
        sa.CheckConstraint("verification_status IN ('SYNTHETIC', 'PROVISIONAL', 'VERIFIED')", name="verification_valid"),
        sa.CheckConstraint("logical_seat_number >= 1 AND physical_seat_number >= 1", name="seat_numbers_positive"),
        schema=SCHEMA,
    )
    op.create_index("ix_seating_draft_seats_draft_id", "seating_draft_seats", ["draft_id"], schema=SCHEMA)


def downgrade() -> None:
    allow_loss = context.get_x_argument(as_dictionary=True).get("drop_duty_rosters", "").lower() == "true"
    if not allow_loss:
        # Stop (and change nothing) if rosters or seating drafts were saved since 0012.
        op.execute(
            "DO $$ BEGIN"
            f" IF EXISTS (SELECT 1 FROM {SCHEMA}.duty_rosters) OR EXISTS (SELECT 1 FROM {SCHEMA}.seating_drafts) THEN"
            " RAISE EXCEPTION 'saved duty rosters or seating drafts exist. Export them,"
            " then run with -x drop_duty_rosters=true';"
            " END IF; END $$"
        )
    op.drop_index("ix_seating_draft_seats_draft_id", table_name="seating_draft_seats", schema=SCHEMA)
    op.drop_table("seating_draft_seats", schema=SCHEMA)
    op.drop_index("uq_seating_drafts_one_current_per_period", table_name="seating_drafts", schema=SCHEMA)
    op.drop_table("seating_drafts", schema=SCHEMA)
    op.drop_index("ix_duty_change_requests_roster_id", table_name="duty_change_requests", schema=SCHEMA)
    op.drop_table("duty_change_requests", schema=SCHEMA)
    op.drop_index("ix_duty_roster_events_roster_id", table_name="duty_roster_events", schema=SCHEMA)
    op.drop_table("duty_roster_events", schema=SCHEMA)
    op.drop_index("ix_duty_roster_assignments_staff_id_starts_at_ends_at", table_name="duty_roster_assignments", schema=SCHEMA)
    op.drop_index("ix_duty_roster_assignments_roster_id", table_name="duty_roster_assignments", schema=SCHEMA)
    op.drop_table("duty_roster_assignments", schema=SCHEMA)
    op.drop_index("ix_duty_rosters_institution_id_period_code", table_name="duty_rosters", schema=SCHEMA)
    op.drop_index("uq_duty_rosters_one_published_per_period", table_name="duty_rosters", schema=SCHEMA)
    op.drop_index("uq_duty_rosters_one_working_per_period", table_name="duty_rosters", schema=SCHEMA)
    op.drop_table("duty_rosters", schema=SCHEMA)
