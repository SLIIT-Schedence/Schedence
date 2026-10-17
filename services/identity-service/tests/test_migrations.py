"""The hand-written migrations must produce exactly the schema the ORM models describe.

Runs Alembic in *offline* mode (``--sql``): it renders SQL text and never opens a
database connection. The URL below points at a closed local port only so that
the PostgreSQL dialect can be selected.
"""

from __future__ import annotations

import io
import re
from pathlib import Path

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from alembic import command
from alembic.config import Config
from app.models import SCHEMA, Base

SERVICE_DIR = Path(__file__).resolve().parents[1]
_TABLE = re.compile(rf"CREATE TABLE {SCHEMA}\.(\w+) \((.*?)\n\)", re.DOTALL)
_INDEX = re.compile(rf"CREATE (?:UNIQUE )?INDEX \w+ ON {SCHEMA}\.\w+ [^;]+")
_ADD_CONSTRAINT = re.compile(rf"ALTER TABLE {SCHEMA}\.(\w+) ADD (CONSTRAINT [^;]+);")


def _render(monkeypatch: pytest.MonkeyPatch, operation, revision: str) -> str:
    monkeypatch.setenv("DATABASE_URL", "postgresql://offline:offline@localhost:1/offline")
    monkeypatch.chdir(SERVICE_DIR)
    buffer = io.StringIO()
    operation(Config(str(SERVICE_DIR / "alembic.ini"), output_buffer=buffer), revision, sql=True)
    return buffer.getvalue()


@pytest.fixture
def migration_sql(monkeypatch: pytest.MonkeyPatch) -> str:
    return _render(monkeypatch, command.upgrade, "head")


def _tables(sql: str) -> dict[str, set[str]]:
    """Each table's column and constraint lines, including constraints added later by ``ALTER TABLE ... ADD``."""
    tables = {name: {line.strip().rstrip(",").strip() for line in body.strip().splitlines()}
              for name, body in _TABLE.findall(sql) if name != "alembic_version"}
    for name, constraint in _ADD_CONSTRAINT.findall(sql):
        tables[name].add(" ".join(constraint.split()))
    return tables


def _indexes(sql: str) -> set[str]:
    return {" ".join(m.split()) for m in _INDEX.findall(sql)}


def test_migration_matches_models(migration_sql: str) -> None:
    dialect = postgresql.dialect()
    model_sql = "\n".join(
        [str(CreateTable(t).compile(dialect=dialect)) for t in Base.metadata.sorted_tables]
        + [str(CreateIndex(i).compile(dialect=dialect)) + ";" for t in Base.metadata.sorted_tables for i in t.indexes]
    )
    assert _tables(migration_sql) == _tables(model_sql)
    assert _indexes(migration_sql) == _indexes(model_sql)


def test_migration_only_touches_the_identity_schema(migration_sql: str) -> None:
    assert "CREATE SCHEMA IF NOT EXISTS identity" in migration_sql
    assert "CREATE TABLE identity.alembic_version" in migration_sql
    statements = [s.strip() for s in migration_sql.split(";") if s.strip() and not s.strip().startswith("--")]
    for statement in statements:
        assert re.match(r"(DROP|TRUNCATE|DELETE)\b", statement) is None, statement
        if statement.startswith("ALTER"):  # only additive foreign keys, never column changes or drops
            assert re.match(rf"ALTER TABLE {SCHEMA}\.\w+ ADD CONSTRAINT \w+ FOREIGN KEY\b", statement), statement
        if statement.startswith(("CREATE TABLE", "CREATE INDEX", "CREATE UNIQUE INDEX", "INSERT INTO", "UPDATE", "ALTER")):
            assert f" {SCHEMA}." in statement, statement


def test_upgrade_keeps_existing_institution_ids_and_renames_super_admin(migration_sql: str) -> None:
    upgrade = migration_sql.split("0001_identity_initial -> 0002_platform_admin", 1)[1]
    backfill = upgrade.index(f"INSERT INTO {SCHEMA}.institutions")
    assert backfill < upgrade.index("ADD CONSTRAINT fk_user_roles_institution_id_institutions")  # rows exist before the FK validates
    assert backfill < upgrade.index("ADD CONSTRAINT fk_user_provisioning_institution_id_institutions")
    assert f"SELECT institution_id FROM {SCHEMA}.user_roles" in upgrade
    assert f"SELECT institution_id FROM {SCHEMA}.user_provisioning" in upgrade
    assert re.search(r"SET code = 'INSTITUTION_ADMIN'.*WHERE code = 'SUPER_ADMIN'", upgrade, re.DOTALL)


def test_downgrade_renders_and_restores_super_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    sql = _render(monkeypatch, command.downgrade, "0002_platform_admin:0001_identity_initial")
    assert f"DROP TABLE {SCHEMA}.platform_role_assignments" in sql
    assert f"DROP TABLE {SCHEMA}.institutions" in sql
    assert re.search(r"SET code = 'SUPER_ADMIN'.*WHERE code = 'INSTITUTION_ADMIN'", sql, re.DOTALL)
