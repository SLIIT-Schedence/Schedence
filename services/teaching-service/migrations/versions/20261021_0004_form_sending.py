"""Immutable form issues and recipient tracking. No submissions."""
from pathlib import Path

from alembic import op

revision = "20261021_0004"
down_revision = "20261021_0003"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError("Sent form rollback requires an explicit retention/recovery plan")
