"""Restricted Teaching runtime role assumed with SET ROLE; no new login or credential."""
from pathlib import Path

from alembic import op

revision = "20261021_0005"
down_revision = "20261021_0004"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))


def downgrade():
    op.execute("DROP OWNED BY schedence_teaching_runtime")
    op.execute("DROP ROLE schedence_teaching_runtime")
