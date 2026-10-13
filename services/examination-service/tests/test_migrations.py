"""The Alembic migrations, checked offline (SQL generation only, no database)."""

from __future__ import annotations

import io
import re

import pytest
from alembic import command
from alembic.config import Config

from app.db.base import Base
from app.db import seed_models  # noqa: F401

from conftest import SERVICE_DIR

FAKE_URL = "postgresql://fake_user:fake_password@ep-example-123456.eu-central-1.aws.neon.tech/neondb"


def _upgrade_sql(monkeypatch: pytest.MonkeyPatch, revisions: str) -> str:
    monkeypatch.setenv("DATABASE_URL", FAKE_URL)
    buffer = io.StringIO()
    config = Config(str(SERVICE_DIR / "alembic.ini"), stdout=buffer)
    config.output_buffer = buffer
    command.upgrade(config, revisions, sql=True)
    return buffer.getvalue()


@pytest.fixture
def upgrade_sql(monkeypatch: pytest.MonkeyPatch) -> str:
    return _upgrade_sql(monkeypatch, "head")


def test_migrations_create_every_model_table_in_examinations(upgrade_sql: str) -> None:
    created = set(re.findall(r"CREATE TABLE examinations\.(\w+)", upgrade_sql))
    assert created == {t.name for t in Base.metadata.sorted_tables} | {"alembic_version"}


def test_migration_columns_match_models(upgrade_sql: str) -> None:
    for table in Base.metadata.sorted_tables:
        block = re.search(rf"CREATE TABLE examinations\.{table.name} \((.*?)\n\);", upgrade_sql, re.S)
        assert block, table.name
        added = re.findall(rf"ALTER TABLE examinations\.{table.name} ADD COLUMN (\w+)", upgrade_sql)
        for column in table.columns:
            in_create = re.search(rf"^\s+{column.name} ", block.group(1), re.M)
            assert in_create or column.name in added, f"{table.name}.{column.name}"


def test_migration_nullability_matches_models(upgrade_sql: str) -> None:
    assert "ALTER TABLE examinations.seed_modules ALTER COLUMN specialization DROP NOT NULL" in upgrade_sql
    assert Base.metadata.tables["examinations.seed_modules"].c.specialization.nullable


def test_migrations_are_not_destructive(upgrade_sql: str) -> None:
    sql = upgrade_sql.upper()
    for forbidden in ("DROP TABLE", "DROP COLUMN", "DROP SCHEMA", "DROP INDEX", "TRUNCATE", "DELETE FROM",
                      "CREATE SCHEMA", "RENAME"):
        assert forbidden not in sql
    # The only dropped constraint is the exam-type check, which is re-created at once.
    dropped = re.findall(r"DROP CONSTRAINT (\w+)", upgrade_sql)
    assert dropped == ["ck_seed_modules_exam_type_valid"]
    assert "ADD CONSTRAINT ck_seed_modules_exam_type_valid CHECK" in upgrade_sql


def test_migrations_touch_only_examinations(upgrade_sql: str) -> None:
    pattern = r"(?:CREATE TABLE|CREATE INDEX \w+ ON|REFERENCES|ALTER TABLE|UPDATE) (\S+)"
    for name in re.findall(pattern, upgrade_sql):
        assert name.startswith("examinations."), name


def test_existing_rows_stay_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    sql = _upgrade_sql(monkeypatch, "0001:0002")
    # Legacy SYN 'practical' rows still pass the new check; reading flag is back-filled.
    assert "'practical'" in re.search(r"ADD CONSTRAINT ck_seed_modules_exam_type_valid CHECK \((.*?)\);", sql).group(1)
    assert "SET has_extra_reading_time = (reading_time_minutes > 0)" in sql


def test_constraint_names_match_models(upgrade_sql: str) -> None:
    expected = {c.name for t in Base.metadata.sorted_tables for c in t.constraints}
    in_migration = set(re.findall(r"CONSTRAINT (\w+)", upgrade_sql)) - {"alembic_version_pkc"}
    assert in_migration == expected


def test_url_is_never_written_to_output(upgrade_sql: str) -> None:
    assert "fake_password" not in upgrade_sql
