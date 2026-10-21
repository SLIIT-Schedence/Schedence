"""Institution-scoped draft form definitions; no distribution or responses."""
from pathlib import Path

from alembic import op

revision = "20261021_0003"
down_revision = "20261016_0002"
branch_labels = None
depends_on = None


def upgrade():
    for statement in Path(__file__).with_suffix(".sql").read_text(encoding="utf-8").split(";\n"):
        if statement.strip():
            op.execute(statement.strip())


def downgrade():
    raise RuntimeError("Draft form rollback requires an explicit retention/recovery plan")
