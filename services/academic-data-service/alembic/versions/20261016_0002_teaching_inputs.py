"""Additive owner-managed input inventory, with forced institution isolation."""
from pathlib import Path
from alembic import op
revision = '0002_teaching_inputs'
down_revision = '0001_academic_initial'
branch_labels = None
depends_on = None
TABLES = ('source_datasets', 'cohort_totals', 'departments', 'lecturer_preferences', 'modules', 'specializations', 'module_classification_reviews', 'module_specializations', 'module_teaching_teams', 'pp1_specialization_allocations', 'staff_teaching_profiles', 'pp1_student_groups')

def upgrade():
    for statement in Path(__file__).with_suffix(".sql").read_text(encoding="utf-8").split(";\n"):
        if statement.strip():
            op.execute(statement.strip())
    predicate = "institution_id = NULLIF(current_setting('schedence.institution_id', true), '')::uuid"
    for name in TABLES:
        op.execute(f"ALTER TABLE academic.{name} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE academic.{name} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY institution_isolation ON academic.{name} USING ({predicate}) WITH CHECK ({predicate})")

def downgrade():
    raise RuntimeError("Owner input records require an explicitly reviewed data-preserving rollback.")
