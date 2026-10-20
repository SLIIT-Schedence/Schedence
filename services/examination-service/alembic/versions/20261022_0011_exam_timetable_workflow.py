"""examination timetable approval and publishing workflow

Touches only the examinations schema. No existing row is changed or deleted, and
no row is inserted.

* exam_timetables: new nullable columns content_fingerprint (SHA-256 of the
  saved sessions, rooms and seats, set when a draft is saved) and
  source_timetable_id (the timetable a draft was regenerated from).
  Timetables saved before 0011 have no content fingerprint and cannot be
  approved: regenerate them.
* exam_timetable_history: append-only log of every status change (create,
  approve, publish, archive): who, when, why and what was checked.
* Triggers (the database enforces the workflow, not only the API):
  - a timetable is inserted only as DRAFT with revision 1;
  - its content columns never change; only DRAFT -> APPROVED -> PUBLISHED ->
    ARCHIVED, each with revision + 1; approval and publication details never
    change once set; only a DRAFT may be deleted;
  - saved sessions, rooms and seats are never updated, and are inserted or
    deleted only while their timetable is a DRAFT;
  - history rows are never updated.

Downgrade removes all of this. It refuses to run when a timetable left DRAFT or
history exists (it would be lost) unless ``alembic -x drop_timetable_workflow=true
downgrade 0010`` is used.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import context, op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "examinations"
ALLOCATION_TABLES = ("exam_timetable_sessions", "exam_timetable_rooms", "exam_timetable_seats")

HISTORY_ACTIONS = (
    "(action = 'create' AND from_status IS NULL AND to_status = 'DRAFT')"
    " OR (action = 'approve' AND from_status = 'DRAFT' AND to_status = 'APPROVED')"
    " OR (action = 'publish' AND from_status = 'APPROVED' AND to_status = 'PUBLISHED')"
    " OR (action = 'archive' AND from_status = 'PUBLISHED' AND to_status = 'ARCHIVED')"
)

# Columns that only a status change may set. Everything else is fixed when the draft is saved.
TIMETABLE_GUARD = f"""
CREATE FUNCTION {SCHEMA}.exam_timetables_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    workflow text[] := ARRAY['status', 'revision', 'approved_by', 'approved_at', 'published_by', 'published_at'];
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'DRAFT' OR NEW.revision <> 1 THEN
            RAISE EXCEPTION 'a saved timetable starts as DRAFT with revision 1' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
    IF TG_OP = 'DELETE' THEN
        IF OLD.status <> 'DRAFT' THEN
            RAISE EXCEPTION 'only a DRAFT timetable may be deleted' USING ERRCODE = '23514';
        END IF;
        RETURN OLD;
    END IF;
    IF (to_jsonb(NEW) - workflow) <> (to_jsonb(OLD) - workflow) THEN
        RAISE EXCEPTION 'saved timetable content cannot be changed' USING ERRCODE = '23514';
    END IF;
    IF NEW.status <> OLD.status THEN
        IF (OLD.status, NEW.status) NOT IN (('DRAFT', 'APPROVED'), ('APPROVED', 'PUBLISHED'),
                                            ('PUBLISHED', 'ARCHIVED')) THEN
            RAISE EXCEPTION 'invalid timetable status change % -> %', OLD.status, NEW.status
                USING ERRCODE = '23514';
        END IF;
        IF NEW.revision <> OLD.revision + 1 THEN
            RAISE EXCEPTION 'a status change must increase the revision by 1' USING ERRCODE = '23514';
        END IF;
    ELSIF NEW.revision <> OLD.revision THEN
        RAISE EXCEPTION 'the revision changes only with the status' USING ERRCODE = '23514';
    END IF;
    IF (OLD.approved_at IS NOT NULL AND jsonb_build_array(NEW.approved_at, NEW.approved_by)
                                        <> jsonb_build_array(OLD.approved_at, OLD.approved_by))
       OR (OLD.published_at IS NOT NULL AND jsonb_build_array(NEW.published_at, NEW.published_by)
                                            <> jsonb_build_array(OLD.published_at, OLD.published_by)) THEN
        RAISE EXCEPTION 'approval and publication details cannot be changed' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$
"""

ALLOCATION_GUARD = f"""
CREATE FUNCTION {SCHEMA}.exam_timetable_allocations_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION 'saved sessions, rooms and seats cannot be changed' USING ERRCODE = '23514';
    END IF;
    IF EXISTS (SELECT 1 FROM {SCHEMA}.exam_timetables t
               WHERE t.timetable_id = CASE WHEN TG_OP = 'INSERT' THEN NEW.timetable_id ELSE OLD.timetable_id END
                 AND t.status <> 'DRAFT') THEN
        RAISE EXCEPTION 'sessions, rooms and seats are locked once a timetable is approved'
            USING ERRCODE = '23514';
    END IF;
    RETURN CASE WHEN TG_OP = 'INSERT' THEN NEW ELSE OLD END;
END $$
"""

HISTORY_GUARD = f"""
CREATE FUNCTION {SCHEMA}.exam_timetable_history_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'timetable history cannot be changed' USING ERRCODE = '23514';
END $$
"""


def upgrade() -> None:
    op.add_column("exam_timetables", sa.Column("content_fingerprint", sa.String(64), nullable=True), schema=SCHEMA)
    op.add_column("exam_timetables", sa.Column("source_timetable_id", sa.BigInteger(), nullable=True), schema=SCHEMA)
    op.create_foreign_key(
        "fk_exam_timetables_source_timetable_id_exam_timetables", "exam_timetables", "exam_timetables",
        ["source_timetable_id"], ["timetable_id"], source_schema=SCHEMA, referent_schema=SCHEMA,
        ondelete="RESTRICT",
    )
    op.create_table(
        "exam_timetable_history",
        sa.Column("history_id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("timetable_id", sa.BigInteger(), nullable=False),
        sa.Column("institution_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(10), nullable=False),
        sa.Column("from_status", sa.String(10), nullable=True),
        sa.Column("to_status", sa.String(10), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("changed_by", sa.Uuid(), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("history_id", name="pk_exam_timetable_history"),
        sa.ForeignKeyConstraint(
            ["timetable_id"], [f"{SCHEMA}.exam_timetables.timetable_id"],
            name="fk_exam_timetable_history_timetable_id_exam_timetables", ondelete="CASCADE",
        ),
        sa.CheckConstraint(HISTORY_ACTIONS, name="action_statuses"),
        sa.CheckConstraint("revision >= 1", name="revision_positive"),
        sa.CheckConstraint("jsonb_typeof(details) = 'object'", name="details_object"),
        schema=SCHEMA,
    )
    op.create_index("ix_exam_timetable_history_timetable_id", "exam_timetable_history", ["timetable_id"],
                    schema=SCHEMA)
    for function in (TIMETABLE_GUARD, ALLOCATION_GUARD, HISTORY_GUARD):
        op.execute(function)
    op.execute(f"CREATE TRIGGER exam_timetables_guard BEFORE INSERT OR UPDATE OR DELETE ON {SCHEMA}.exam_timetables"
               f" FOR EACH ROW EXECUTE FUNCTION {SCHEMA}.exam_timetables_guard()")
    for table in ALLOCATION_TABLES:
        op.execute(f"CREATE TRIGGER {table}_guard BEFORE INSERT OR UPDATE OR DELETE ON {SCHEMA}.{table}"
                   f" FOR EACH ROW EXECUTE FUNCTION {SCHEMA}.exam_timetable_allocations_guard()")
    op.execute(f"CREATE TRIGGER exam_timetable_history_guard BEFORE UPDATE ON {SCHEMA}.exam_timetable_history"
               f" FOR EACH ROW EXECUTE FUNCTION {SCHEMA}.exam_timetable_history_guard()")


def downgrade() -> None:
    allow_loss = context.get_x_argument(as_dictionary=True).get("drop_timetable_workflow", "").lower() == "true"
    if not allow_loss:
        # Stop (and change nothing) if the workflow was used since 0011.
        op.execute(
            "DO $$ BEGIN"
            f" IF EXISTS (SELECT 1 FROM {SCHEMA}.exam_timetable_history)"
            f" OR EXISTS (SELECT 1 FROM {SCHEMA}.exam_timetables WHERE status <> 'DRAFT') THEN"
            " RAISE EXCEPTION 'examination timetable workflow history exists. Export"
            " examinations.exam_timetable_history, then run with -x drop_timetable_workflow=true';"
            " END IF; END $$"
        )
    op.execute(f"DROP TRIGGER exam_timetable_history_guard ON {SCHEMA}.exam_timetable_history")
    for table in ALLOCATION_TABLES:
        op.execute(f"DROP TRIGGER {table}_guard ON {SCHEMA}.{table}")
    op.execute(f"DROP TRIGGER exam_timetables_guard ON {SCHEMA}.exam_timetables")
    for function in ("exam_timetable_history_guard", "exam_timetable_allocations_guard", "exam_timetables_guard"):
        op.execute(f"DROP FUNCTION {SCHEMA}.{function}()")
    op.drop_index("ix_exam_timetable_history_timetable_id", table_name="exam_timetable_history", schema=SCHEMA)
    op.drop_table("exam_timetable_history", schema=SCHEMA)
    op.drop_constraint("fk_exam_timetables_source_timetable_id_exam_timetables", "exam_timetables",
                       schema=SCHEMA, type_="foreignkey")
    op.drop_column("exam_timetables", "source_timetable_id", schema=SCHEMA)
    op.drop_column("exam_timetables", "content_fingerprint", schema=SCHEMA)
