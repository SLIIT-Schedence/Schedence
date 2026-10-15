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


def _sql(monkeypatch: pytest.MonkeyPatch, revisions: str, *, downgrade: bool = False) -> str:
    monkeypatch.setenv("DATABASE_URL", FAKE_URL)
    buffer = io.StringIO()
    config = Config(str(SERVICE_DIR / "alembic.ini"), stdout=buffer)
    config.output_buffer = buffer
    (command.downgrade if downgrade else command.upgrade)(config, revisions, sql=True)
    return buffer.getvalue()


def _upgrade_sql(monkeypatch: pytest.MonkeyPatch, revisions: str) -> str:
    return _sql(monkeypatch, revisions)


def _check(sql: str, name: str) -> list[str]:
    """Bodies of every ``ADD CONSTRAINT <name> CHECK (...)`` in order."""
    return re.findall(rf"ADD CONSTRAINT {name} CHECK \((.*?)\);", sql)


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
    # Dropped checks are re-created at once (wider or tighter); the old block key
    # is replaced by one that includes the source.
    dropped = re.findall(r"DROP CONSTRAINT (\w+)", upgrade_sql)
    assert dropped == [
        "ck_seed_modules_exam_type_valid",  # 0002
        "ck_seed_venues_venue_type_valid",  # 0003 (expand)
        "ck_seed_venue_blocks_source_valid",  # 0003
        "ck_seed_venues_venue_type_valid",  # 0004 (contract)
        "uq_seed_venue_blocks_venue_id_block_date_start_time",  # 0004
    ]
    for name in set(dropped) - {"uq_seed_venue_blocks_venue_id_block_date_start_time"}:
        assert f"ADD CONSTRAINT {name} CHECK" in upgrade_sql
    assert "ADD CONSTRAINT uq_seed_venue_blocks_source_venue_id_block_date_start_time UNIQUE" in upgrade_sql


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
    alive: set[str] = set()
    for dropped, created in re.findall(r"DROP CONSTRAINT (\w+)|CONSTRAINT (\w+)", upgrade_sql):
        if dropped:
            alive.discard(dropped)
        else:
            alive.add(created)
    assert alive - {"alembic_version_pkc"} == expected


def test_0003_keeps_the_old_venue_rows_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    sql = _upgrade_sql(monkeypatch, "0002:0003")
    [venue_types] = _check(sql, "ck_seed_venues_venue_type_valid")
    for venue_type in ("auditorium", "exam_hall", "lecture_hall", "computer_lab", "smart_classroom", "byod_lab"):
        assert f"'{venue_type}'" in venue_types
    [sources] = _check(sql, "ck_seed_venue_blocks_source_valid")
    for source in ("teaching_timetable", "maintenance", "simulated_teaching", "simulated_maintenance"):
        assert f"'{source}'" in sources
    # New columns start nullable, so the 9 old venues need no values.
    assert re.search(r"ADD COLUMN building VARCHAR\(8\);", sql) and re.search(r"ADD COLUMN floor SMALLINT;", sql)
    assert "ADD CONSTRAINT uq_seed_venue_blocks_source_source_ref UNIQUE (source, source_ref)" in sql
    assert "NOT NULL" not in sql.split("BEGIN;", 1)[1].split("UPDATE examinations.alembic_version")[0]


def test_0004_enforces_the_campus_venue_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    sql = _upgrade_sql(monkeypatch, "0003:0004")
    assert "ALTER COLUMN building SET NOT NULL" in sql and "ALTER COLUMN floor SET NOT NULL" in sql
    [venue_types] = _check(sql, "ck_seed_venues_venue_type_valid")
    assert "'auditorium'" not in venue_types and "'exam_hall'" not in venue_types
    assert _check(sql, "ck_seed_venues_exam_capacity_half") == ["exam_capacity = normal_capacity / 2"]
    assert sql.count("BEGIN;") == 1  # one transaction: all or nothing


def test_downgrade_order_restores_the_old_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    sql = _sql(monkeypatch, "0004:0002", downgrade=True)
    # 0004 is undone first (contract), then 0003 (expand).
    assert sql.index("DROP CONSTRAINT ck_seed_venues_exam_capacity_half") < sql.index("DROP COLUMN source_ref")
    assert "ADD CONSTRAINT uq_seed_venue_blocks_venue_id_block_date_start_time UNIQUE" in sql
    assert _check(sql, "ck_seed_venues_venue_type_valid")[-1] == (
        "venue_type IN ('auditorium', 'exam_hall', 'lecture_hall', 'computer_lab')")
    assert _check(sql, "ck_seed_venue_blocks_source_valid")[-1] == "source IN ('teaching_timetable', 'maintenance')"
    for name in re.findall(r"(?:ALTER TABLE|REFERENCES) (\S+)", sql):
        assert name.startswith("examinations."), name


def test_url_is_never_written_to_output(upgrade_sql: str) -> None:
    assert "fake_password" not in upgrade_sql
