"""The Alembic migrations, checked offline (SQL generation only, no database)."""

from __future__ import annotations

import argparse
import io
import re

import pytest
from alembic import command
from alembic.config import Config

from app.db.base import Base
from app.db import scheduling_settings_models, seating_models, seed_models  # noqa: F401

from conftest import SERVICE_DIR

FAKE_URL = "postgresql://fake_user:fake_password@ep-example-123456.eu-central-1.aws.neon.tech/neondb"


def _sql(monkeypatch: pytest.MonkeyPatch, revisions: str, *, downgrade: bool = False, x: list[str] = ()) -> str:
    monkeypatch.setenv("DATABASE_URL", FAKE_URL)
    buffer = io.StringIO()
    config = Config(str(SERVICE_DIR / "alembic.ini"), stdout=buffer, cmd_opts=argparse.Namespace(x=list(x)))
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
    # is replaced by one that includes the source; 0005 removes the fixed 50% rule.
    dropped = re.findall(r"DROP CONSTRAINT (\w+)", upgrade_sql)
    assert dropped == [
        "ck_seed_modules_exam_type_valid",  # 0002
        "ck_seed_venues_venue_type_valid",  # 0003 (expand)
        "ck_seed_venue_blocks_source_valid",  # 0003
        "ck_seed_venues_venue_type_valid",  # 0004 (contract)
        "uq_seed_venue_blocks_venue_id_block_date_start_time",  # 0004
        "ck_seed_venues_exam_capacity_half",  # 0005 (now configurable)
    ]
    for name in set(dropped) - {"uq_seed_venue_blocks_venue_id_block_date_start_time",
                                "ck_seed_venues_exam_capacity_half"}:
        assert upgrade_sql.rindex(f"DROP CONSTRAINT {name}") < upgrade_sql.rindex(f"ADD CONSTRAINT {name} CHECK")
    assert "ADD CONSTRAINT uq_seed_venue_blocks_source_venue_id_block_date_start_time UNIQUE" in upgrade_sql


def test_migrations_touch_only_examinations(upgrade_sql: str) -> None:
    pattern = r"(?:CREATE TABLE|CREATE (?:UNIQUE )?INDEX \w+ ON|REFERENCES|ALTER TABLE|UPDATE|INSERT INTO|FROM) (\S+)"
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


# --- 0005: configurable examination seating -----------------------------------------------


def _between(sql: str) -> str:
    """Statements of one revision, without the alembic_version bookkeeping."""
    return sql.split("BEGIN;", 1)[1].split("UPDATE examinations.alembic_version")[0]


def test_0005_replaces_the_fixed_50_percent_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    sql = _upgrade_sql(monkeypatch, "0004:0005")
    body = _between(sql)
    assert re.findall(r"DROP CONSTRAINT (\w+)", body) == ["ck_seed_venues_exam_capacity_half"]
    # Nothing else in the existing tables changes: no column, row or other constraint.
    assert set(re.findall(r"ALTER TABLE (\S+)", body)) == {"examinations.seed_venues"}
    for forbidden in ("DROP TABLE", "DROP COLUMN", "DELETE FROM", "TRUNCATE", "UPDATE ", "ALTER COLUMN"):
        assert forbidden not in body.upper()
    assert set(re.findall(r"CREATE TABLE examinations\.(\w+)", body)) == {
        "exam_seating_rules", "exam_seating_rule_history"}
    assert sql.count("BEGIN;") == 1


def _inline_check(sql: str, name: str) -> list[str]:
    """Bodies of ``CONSTRAINT <name> CHECK (...)`` inside CREATE TABLE."""
    return re.findall(rf"CONSTRAINT {name} CHECK \((.*?)\),? ?\n", sql)


def test_0005_seating_rule_constraints(monkeypatch: pytest.MonkeyPatch) -> None:
    sql = _upgrade_sql(monkeypatch, "0004:0005")
    assert _inline_check(sql, "ck_exam_seating_rules_percentage_range") == ["percentage BETWEEN 1 AND 100"]
    assert _inline_check(sql, "ck_exam_seating_rules_version_positive") == ["version >= 1"]
    assert "scope = 'default' AND venue_id IS NULL" in _inline_check(sql, "ck_exam_seating_rules_scope_valid")[0]
    assert "CONSTRAINT uq_exam_seating_rules_venue_id UNIQUE (venue_id)" in sql
    assert ("CREATE UNIQUE INDEX uq_exam_seating_rules_default ON examinations.exam_seating_rules (scope) "
            "WHERE scope = 'default'") in sql
    assert "REFERENCES examinations.seed_venues (venue_id) ON DELETE RESTRICT" in sql
    [values] = _inline_check(sql, "ck_exam_seating_rule_history_action_values")
    assert "action = 'delete' AND old_percentage IS NOT NULL AND new_percentage IS NULL" in values
    # History keeps no foreign key: it must outlive deleted overrides and venues.
    history = re.search(r"CREATE TABLE examinations\.exam_seating_rule_history \((.*?)\n\);", sql, re.S).group(1)
    assert "REFERENCES" not in history


def test_0005_inserts_the_initial_50_percent_default(monkeypatch: pytest.MonkeyPatch) -> None:
    sql = _upgrade_sql(monkeypatch, "0004:0005")
    inserts = re.findall(r"INSERT INTO (\S+)", sql)
    assert inserts == ["examinations.exam_seating_rules", "examinations.exam_seating_rule_history"]
    assert "VALUES ('default', NULL, 50, 1, " in sql
    assert "SELECT rule_id, 'create', 'default', NULL, NULL, percentage, version, NULL, reason" in sql


def test_0005_downgrade_refuses_to_lose_user_history(monkeypatch: pytest.MonkeyPatch) -> None:
    sql = _sql(monkeypatch, "0005:0004", downgrade=True)
    guard = sql.index("RAISE EXCEPTION")
    assert "WHERE changed_by IS NOT NULL" in sql
    assert guard < sql.index("DROP TABLE examinations.exam_seating_rule_history")
    assert _check(sql, "ck_seed_venues_exam_capacity_half") == ["exam_capacity = normal_capacity / 2"]
    assert sql.index("DROP TABLE examinations.exam_seating_rules") < sql.index(
        "ADD CONSTRAINT ck_seed_venues_exam_capacity_half")
    assert set(re.findall(r"DROP TABLE ([\w.]+)", sql)) == {
        "examinations.exam_seating_rules", "examinations.exam_seating_rule_history"}
    assert sql.count("BEGIN;") == 1


def test_0005_downgrade_guard_can_be_skipped_explicitly(monkeypatch: pytest.MonkeyPatch) -> None:
    sql = _sql(monkeypatch, "0005:0004", downgrade=True, x=["drop_seating_history=true"])
    assert "RAISE EXCEPTION" not in sql
    assert "DROP TABLE examinations.exam_seating_rule_history" in sql


def test_0006_only_adds_the_scheduling_settings_tables(monkeypatch: pytest.MonkeyPatch) -> None:
    sql = _upgrade_sql(monkeypatch, "0005:0006")
    body = _between(sql)
    assert set(re.findall(r"CREATE TABLE examinations\.(\w+)", body)) == {
        "exam_scheduling_settings", "exam_scheduling_settings_history"}
    # No existing table, row or constraint changes, and no institution is assumed: nothing is inserted.
    for forbidden in ("ALTER TABLE", "DROP ", "DELETE FROM", "TRUNCATE", "UPDATE ", "INSERT INTO"):
        assert forbidden not in body.upper()
    assert sql.count("BEGIN;") == 1


def test_0006_scheduling_settings_constraints(monkeypatch: pytest.MonkeyPatch) -> None:
    sql = _upgrade_sql(monkeypatch, "0005:0006")
    assert "CONSTRAINT uq_exam_scheduling_settings_institution_id UNIQUE (institution_id)" in sql
    assert _inline_check(sql, "ck_exam_scheduling_settings_version_positive") == ["version >= 1"]
    assert _inline_check(sql, "ck_exam_scheduling_settings_settings_object") == ["jsonb_typeof(settings) = 'object'"]
    [values] = _inline_check(sql, "ck_exam_scheduling_settings_history_action_values")
    assert "action = 'create' AND old_settings IS NULL AND old_format IS NULL" in values
    assert "action IN ('update', 'repair') AND old_settings IS NOT NULL AND old_format IS NOT NULL" in values
    assert _inline_check(sql, "ck_exam_scheduling_settings_history_action_valid") == [
        "action IN ('create', 'update', 'repair')"]
    assert _inline_check(sql, "ck_exam_scheduling_settings_history_changed_fields_array") == [
        "jsonb_typeof(changed_fields) = 'array'"]
    assert "settings JSONB NOT NULL" in sql and "institution_id UUID NOT NULL" in sql
    assert "REFERENCES" not in _between(sql)  # no link to seed data or to other services' tables


def test_0006_downgrade_refuses_to_lose_saved_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    sql = _sql(monkeypatch, "0006:0005", downgrade=True)
    assert sql.index("RAISE EXCEPTION") < sql.index("DROP TABLE examinations.exam_scheduling_settings_history")
    assert set(re.findall(r"DROP TABLE ([\w.]+)", sql)) == {
        "examinations.exam_scheduling_settings", "examinations.exam_scheduling_settings_history"}
    skipped = _sql(monkeypatch, "0006:0005", downgrade=True, x=["drop_scheduling_settings=true"])
    assert "RAISE EXCEPTION" not in skipped and "DROP TABLE examinations.exam_scheduling_settings" in skipped


def test_url_is_never_written_to_output(upgrade_sql: str) -> None:
    assert "fake_password" not in upgrade_sql


# --- 0007: examination period kind (MID / FINAL) ---------------------------------------------


def test_revisions_form_one_chain_ending_at_0009() -> None:
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config(str(SERVICE_DIR / "alembic.ini")))
    assert script.get_heads() == ["0009"]
    assert script.get_revision("0007").down_revision == "0006"
    assert script.get_revision("0008").down_revision == "0007"
    assert script.get_revision("0009").down_revision == "0008"


def test_0007_adds_exam_kind_and_existing_periods_become_final(monkeypatch: pytest.MonkeyPatch) -> None:
    sql = _upgrade_sql(monkeypatch, "0006:0007")
    body = _between(sql)
    # One statement adds the column with its default, so every existing period is FINAL.
    assert ("ALTER TABLE examinations.seed_exam_periods ADD COLUMN exam_kind VARCHAR(10) DEFAULT 'FINAL' NOT NULL;"
            in body)
    assert _check(sql, "ck_seed_exam_periods_exam_kind_valid") == ["exam_kind IN ('MID', 'FINAL')"]
    assert re.findall(r"ALTER TABLE (\S+)", body) == ["examinations.seed_exam_periods"] * 2
    for forbidden in ("CREATE TABLE", "DROP ", "DELETE FROM", "TRUNCATE", "UPDATE ", "INSERT INTO"):
        assert forbidden not in body.upper()
    assert sql.count("BEGIN;") == 1


def test_0007_model_matches_the_migration() -> None:
    from app.db.seed_models import DEFAULT_EXAM_KIND, EXAM_KINDS

    column = Base.metadata.tables["examinations.seed_exam_periods"].c.exam_kind
    assert not column.nullable and column.server_default.arg == DEFAULT_EXAM_KIND == "FINAL"
    assert EXAM_KINDS == ("MID", "FINAL")


def test_0007_downgrade_refuses_to_hide_mid_periods(monkeypatch: pytest.MonkeyPatch) -> None:
    sql = _sql(monkeypatch, "0007:0006", downgrade=True)
    assert "exam_kind <> 'FINAL'" in sql
    assert (sql.index("RAISE EXCEPTION") < sql.index("DROP CONSTRAINT ck_seed_exam_periods_exam_kind_valid")
            < sql.index("DROP COLUMN exam_kind"))
    skipped = _sql(monkeypatch, "0007:0006", downgrade=True, x=["drop_exam_kind=true"])
    assert "RAISE EXCEPTION" not in skipped and "DROP COLUMN exam_kind" in skipped


# --- 0008: module examination eligibility and Block Mode -------------------------------------


def test_0008_adds_module_exam_fields_with_backward_compatible_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    sql = _upgrade_sql(monkeypatch, "0007:0008")
    body = _between(sql)
    add = "ALTER TABLE examinations.seed_modules ADD COLUMN "
    for column in ("has_final_exam BOOLEAN DEFAULT true NOT NULL", "has_mid_exam BOOLEAN DEFAULT false NOT NULL",
                   "mid_duration_minutes SMALLINT", "delivery_mode VARCHAR(10) DEFAULT 'regular' NOT NULL",
                   "block_category VARCHAR(20)"):
        assert f"{add}{column};" in body, column
    assert _check(sql, "ck_seed_modules_mid_exam_duration_valid") == [
        "(has_mid_exam AND mid_duration_minutes IS NOT NULL AND mid_duration_minutes > 0)"
        " OR (NOT has_mid_exam AND mid_duration_minutes IS NULL)"]
    assert _check(sql, "ck_seed_modules_delivery_mode_valid") == ["delivery_mode IN ('regular', 'block')"]
    assert _check(sql, "ck_seed_modules_block_category_valid") == [
        "(delivery_mode = 'regular' AND block_category IS NULL)"
        " OR (delivery_mode = 'block' AND block_category IS NOT NULL AND block_category IN ('pro_rata', 'full_module'))"]
    assert set(re.findall(r"ALTER TABLE (\S+)", body)) == {"examinations.seed_modules"}
    for forbidden in ("CREATE TABLE", "DROP ", "DELETE FROM", "TRUNCATE", "UPDATE ", "INSERT INTO"):
        assert forbidden not in body.upper()
    assert sql.count("BEGIN;") == 1


def test_0008_model_checks_match_the_migration(monkeypatch: pytest.MonkeyPatch) -> None:
    table = Base.metadata.tables["examinations.seed_modules"]
    model = {c.name: str(c.sqltext) for c in table.constraints if c.name and c.name.startswith("ck_")}
    sql = _upgrade_sql(monkeypatch, "0007:0008")
    for name in ("mid_exam_duration_valid", "delivery_mode_valid", "block_category_valid"):
        full = f"ck_seed_modules_{name}"
        assert _check(sql, full) == [model[full]]


def test_0008_downgrade_refuses_to_lose_module_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    sql = _sql(monkeypatch, "0008:0007", downgrade=True)
    assert "has_mid_exam OR NOT has_final_exam OR delivery_mode <> 'regular'" in sql
    assert sql.index("RAISE EXCEPTION") < sql.index("DROP COLUMN has_final_exam")
    skipped = _sql(monkeypatch, "0008:0007", downgrade=True, x=["drop_module_exam_settings=true"])
    assert "RAISE EXCEPTION" not in skipped
    for column in ("has_final_exam", "has_mid_exam", "mid_duration_minutes", "delivery_mode", "block_category"):
        assert f"DROP COLUMN {column}" in skipped


# --- 0009: examination cycle modules -----------------------------------------------------------


def test_0009_only_adds_the_cycle_module_table(monkeypatch: pytest.MonkeyPatch) -> None:
    sql = _upgrade_sql(monkeypatch, "0008:0009")
    body = _between(sql)
    assert set(re.findall(r"CREATE TABLE examinations\.(\w+)", body)) == {"seed_exam_period_modules"}
    assert "candidate_scope VARCHAR(12) DEFAULT 'all' NOT NULL" in body
    assert "PRIMARY KEY (period_code, module_code)" in body
    assert "REFERENCES examinations.seed_exam_periods (period_code) ON DELETE RESTRICT" in body
    assert "REFERENCES examinations.seed_modules (module_code) ON DELETE RESTRICT" in body
    assert _inline_check(sql, "ck_seed_exam_period_modules_candidate_scope_valid") == [
        "candidate_scope IN ('all', 'repeat_only')"]
    assert "CREATE INDEX ix_seed_exam_period_modules_module_code" in body
    for forbidden in ("ALTER TABLE", "DROP ", "DELETE FROM", "TRUNCATE", "UPDATE ", "INSERT INTO"):
        assert forbidden not in body.upper()  # no existing table or row changes, no mapping inserted
    assert sql.count("BEGIN;") == 1


def test_0009_downgrade_refuses_to_lose_mappings(monkeypatch: pytest.MonkeyPatch) -> None:
    sql = _sql(monkeypatch, "0009:0008", downgrade=True)
    assert sql.index("RAISE EXCEPTION") < sql.index("DROP TABLE examinations.seed_exam_period_modules")
    skipped = _sql(monkeypatch, "0009:0008", downgrade=True, x=["drop_exam_cycle_modules=true"])
    assert "RAISE EXCEPTION" not in skipped and "DROP TABLE examinations.seed_exam_period_modules" in skipped
