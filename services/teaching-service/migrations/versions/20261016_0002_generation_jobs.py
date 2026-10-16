"""Durable queue and attempt evidence; prepared for production review only."""
from pathlib import Path
from alembic import op

revision = "20261016_0002"
down_revision = "20261016_0001"
branch_labels = None
depends_on = None


def upgrade():
    for statement in Path(__file__).with_suffix('.sql').read_text(encoding='utf-8').split(';\n'):
        if statement.strip():
            op.execute(statement.strip())


def downgrade():
    # No automatic deletion of accepted jobs or audit history, even in rollback.
    raise RuntimeError('Durable job rollback requires a reviewed retention/recovery plan')
