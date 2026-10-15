"""Initial teaching-owned immutable history. Prepared, never applied in Step 12B.1."""

from pathlib import Path

from alembic import op

revision = "20261016_0001"
down_revision = None
branch_labels = None
depends_on = None

# Frozen revision assets, independent of subsequent ORM model changes.
TABLES = (
    "input_snapshots", "timetable_series", "generation_runs", "snapshot_scopes",
    "snapshot_sources", "snapshot_issues", "validation_reports", "timetable_versions",
    "deliveries", "delivery_coverage",
)


def upgrade():
    sql = Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    for statement in sql.split(";\n"):
        if statement.strip():
            op.execute(statement.strip())
    op.execute("""CREATE FUNCTION teaching.reject_history_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            RAISE EXCEPTION 'Teaching history is append-only' USING ERRCODE = '55000';
        END; $$""")
    predicate = "institution_id = NULLIF(current_setting('schedence.institution_id', true), '')::uuid"
    for name in TABLES:
        op.execute(f"ALTER TABLE teaching.{name} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE teaching.{name} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY institution_isolation ON teaching.{name} USING ({predicate}) WITH CHECK ({predicate})")
        op.execute(f"CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON teaching.{name} "
                   "FOR EACH ROW EXECUTE FUNCTION teaching.reject_history_mutation()")


def downgrade():
    # Explicit review-only rollback. Never drop another service's schema or data.
    for name in reversed(TABLES):
        op.drop_table(name, schema="teaching")
    op.execute("DROP FUNCTION teaching.reject_history_mutation()")
