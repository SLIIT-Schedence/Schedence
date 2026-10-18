"""PostgreSQL DDL review, strictly offline: no SQLite or Neon substitution."""

from io import StringIO
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import ForeignKeyConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from schedence_teaching.persistence.models import Base, DeliveryCoverage, TimetableVersion

ROOT = Path(__file__).resolve().parents[1]
REVISION = ROOT / "migrations" / "versions" / "20261016_0001_teaching_history.sql"


def offline_sql(monkeypatch, *, downgrade=False):
    import schedence_db
    def forbidden(*args, **kwargs):
        raise AssertionError("offline migration must never load settings or create a database engine")
    monkeypatch.setattr(schedence_db, "load_settings", forbidden)
    monkeypatch.setattr(schedence_db, "create_db_engine", forbidden)
    output = StringIO()
    config = Config(str(ROOT / "alembic.ini"), output_buffer=output)
    if downgrade: command.downgrade(config, "20261016_0001:base", sql=True)
    else: command.upgrade(config, "head", sql=True)
    return output.getvalue()


def test_frozen_revision_matches_all_postgres_models_and_indexes():
    dialect = postgresql.dialect()
    statements = []
    for table in Base.metadata.sorted_tables:
        statements.append(str(CreateTable(table).compile(dialect=dialect)).strip())
        statements.extend(str(CreateIndex(index).compile(dialect=dialect)).strip()
                          for index in sorted(table.indexes, key=lambda row: row.name))
    assert REVISION.read_text(encoding="utf-8") == ";\n\n".join(statements) + ";\n"


def test_upgrade_renders_without_credentials_or_any_connection(monkeypatch):
    sql = offline_sql(monkeypatch)
    assert "CREATE SCHEMA IF NOT EXISTS teaching" in sql
    assert sql.count("CREATE TABLE teaching.") == 13  # Ten history + two queue tables + Alembic history.
    assert "neon.tech" not in sql and "postgresql://" not in sql
    assert "CREATE TABLE academic." not in sql and "CREATE TABLE identity." not in sql
    for table in Base.metadata.sorted_tables:
        assert f"ALTER TABLE teaching.{table.name} ENABLE ROW LEVEL SECURITY" in sql
        assert f"ALTER TABLE teaching.{table.name} FORCE ROW LEVEL SECURITY" in sql
        assert f"CREATE POLICY institution_isolation ON teaching.{table.name}" in sql
        assert f"BEFORE UPDATE OR DELETE ON teaching.{table.name}" in sql
    assert "current_setting('schedence.institution_id', true)" in sql
    assert "NULLIF(" in sql and "WITH CHECK" in sql
    assert "RAISE EXCEPTION 'Teaching history is append-only'" in sql
    for name in ("generation_jobs", "generation_job_attempts"):
        assert f"ALTER TABLE teaching.{name} FORCE ROW LEVEL SECURITY" in sql
        assert f"CREATE POLICY institution_isolation ON teaching.{name}" in sql
        assert f"BEFORE UPDATE OR DELETE ON teaching.{name}" not in sql


def test_offline_downgrade_is_explicitly_owned_and_never_cascades(monkeypatch):
    sql = offline_sql(monkeypatch, downgrade=True)
    assert sql.count("DROP TABLE teaching.") == 10  # Alembic retains its version table.
    assert "CASCADE" not in sql and "DROP SCHEMA" not in sql
    assert "DROP TABLE academic." not in sql and "DROP TABLE identity." not in sql


@pytest.mark.parametrize("table", Base.metadata.sorted_tables, ids=lambda table: table.name)
def test_every_table_and_relationship_is_tenant_scoped_without_cross_service_fks(table):
    assert table.schema == "teaching" and not table.c.institution_id.nullable
    constraints = list(table.constraints)
    names = [str(constraint.name) for constraint in constraints if constraint.name]
    assert len(names) == len(set(names))
    for constraint in constraints:
        if isinstance(constraint, ForeignKeyConstraint):
            assert "institution_id" in constraint.column_keys
            assert all(element.target_fullname.startswith("teaching.") for element in constraint.elements)
            assert constraint.ondelete == "RESTRICT"


def test_occurrence_coverage_is_unique_per_tenant_version():
    assert list(DeliveryCoverage.__table__.primary_key.columns.keys()) == [
        "institution_id", "version_id", "occurrence_reference"]
    checks = " ".join(str(check.sqltext) for check in TimetableVersion.__table__.constraints if hasattr(check, "sqltext"))
    assert "DRAFT_VALIDATED" in checks and "solver_success AND validation_passed" in checks
    assert "COMPLETE_SUPPORTED_SCOPE" in checks


def test_existing_solver_and_app_do_not_import_persistence():
    source = ROOT / "src" / "schedence_teaching"
    for directory in (source / "solver", source / "preparation", source / "pilot"):
        for path in directory.glob("*.py"):
            assert "schedence_teaching.persistence" not in path.read_text(encoding="utf-8")
    assert "persistence" not in (source / "main.py").read_text(encoding="utf-8")
