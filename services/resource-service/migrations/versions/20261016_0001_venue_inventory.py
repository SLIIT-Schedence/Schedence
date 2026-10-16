"""Additive owner-managed input inventory, with forced institution isolation."""
from pathlib import Path
from alembic import op
revision = '0001_resource_inventory'
down_revision = None
branch_labels = None
depends_on = None
TABLES = ('source_datasets', 'venues')

def upgrade():
    for statement in Path(__file__).with_suffix(".sql").read_text(encoding="utf-8").split(";\n"):
        if statement.strip():
            op.execute(statement.strip())
    predicate = "institution_id = NULLIF(current_setting('schedence.institution_id', true), '')::uuid"
    for name in TABLES:
        op.execute(f"ALTER TABLE resource.{name} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE resource.{name} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY institution_isolation ON resource.{name} USING ({predicate}) WITH CHECK ({predicate})")

def downgrade():
    raise RuntimeError("Owner input records require an explicitly reviewed data-preserving rollback.")
