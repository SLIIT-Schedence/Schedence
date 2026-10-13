"""The hand-written migration must produce exactly the schema the ORM models describe.

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


@pytest.fixture
def migration_sql(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("DATABASE_URL", "postgresql://offline:offline@localhost:1/offline")
    monkeypatch.chdir(SERVICE_DIR)
    buffer = io.StringIO()
    command.upgrade(Config(str(SERVICE_DIR / "alembic.ini"), output_buffer=buffer), "head", sql=True)
    return buffer.getvalue()


def _tables(sql: str) -> dict[str, set[str]]:
    return {name: {line.strip().rstrip(",").strip() for line in body.strip().splitlines()}
            for name, body in _TABLE.findall(sql) if name != "alembic_version"}


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


def test_migration_only_touches_the_academic_schema(migration_sql: str) -> None:
    assert "CREATE SCHEMA IF NOT EXISTS academic" in migration_sql
    assert "CREATE TABLE academic.alembic_version" in migration_sql
    statements = [s.strip() for s in migration_sql.split(";") if s.strip() and not s.strip().startswith("--")]
    for statement in statements:
        assert re.match(r"(DROP|TRUNCATE|DELETE|ALTER)\b", statement) is None, statement
        if statement.startswith(("CREATE TABLE", "CREATE INDEX", "CREATE UNIQUE INDEX", "INSERT INTO", "UPDATE")):
            assert f" {SCHEMA}." in statement, statement
