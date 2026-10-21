"""Existing-Neon setup, input provenance and strict migration ownership (offline)."""

from contextlib import asynccontextmanager
import re

import pytest

from app.duty_allocation import neon_shared as shared
from app.duty_allocation.demo_database import DemoTargetRefused
from app.duty_allocation.demo_service import default_row_source
from app.duty_allocation.demo_settings import DemoSource, Member3DemoSettings
from app.duty_allocation.neon_shared import NeonConfig, DUTY_TABLES, VERSION_TABLE, render_sql
from schedence_db.config import DatabaseSettings
from test_duty_allocation_api import Env, fake_source

HOST = "ep-shared-pooler.us-east-2.aws.neon.tech"
URL = f"postgresql://user:fakepassword@{HOST}/neondb"


def config():
    return NeonConfig(Member3DemoSettings(
        source=DemoSource.NEON_SHARED, neon_host=HOST, neon_database="neondb"),
        DatabaseSettings(database_url=URL, direct_database_url=URL.replace("-pooler", "")))


def test_shared_mode_requires_matching_remote_neon_targets():
    chosen = config()
    shared.verify_shared_database(chosen.demo, chosen.database)
    assert chosen.demo.missing_setup() == ()
    for url in ("postgresql://u:p@localhost/neondb", "postgresql://u:p@ep-other.aws.neon.tech/neondb"):
        with pytest.raises(DemoTargetRefused):
            shared.verify_shared_database(chosen.demo, DatabaseSettings(database_url=url))
    with pytest.raises(DemoTargetRefused, match="local databases are refused"):
        shared.verify_shared_database(Member3DemoSettings(source=DemoSource.NEON_SHARED,
            neon_host="localhost", neon_database="neondb"), chosen.database)


def test_shared_loader_reuses_existing_urls_without_editing_the_file(tmp_path):
    path = tmp_path / ".env"
    content = f"DATABASE_URL={URL}\nDIRECT_DATABASE_URL={URL.replace('-pooler', '')}\n"
    path.write_text(content, encoding="utf-8")
    chosen = shared.load_shared_config(path)
    assert chosen.demo.source is DemoSource.NEON_SHARED
    assert chosen.demo.persistence.value == "DATABASE" and chosen.demo.persistence_problem() is None
    assert chosen.database.get_url().host == HOST
    assert path.read_text(encoding="utf-8") == content


@pytest.mark.parametrize("statement", [
    "UPDATE examinations.alembic_version SET version_num='0013'",
    "CREATE TABLE academic.staff (id integer)",
    "ALTER TABLE examinations.seed_venues ADD COLUMN x integer",
    "DROP TABLE examinations.duty_rosters",
    "DELETE FROM m3_demo.staff",
    "INSERT INTO m3_demo.staff VALUES ('new')",
    "INSERT INTO examinations.duty_rosters VALUES ('new')",
    "CREATE TABLE examinations.duty_rosters (id uuid REFERENCES identity.users(id))",
    "GRANT ALL ON SCHEMA examinations TO somebody",
    "TRUNCATE examinations.duty_rosters",
    "CREATE INDEX bad ON examinations.seed_venues (venue_id)",
])
def test_migration_guard_refuses_teammates_tables_and_non_additive_operations(statement):
    with pytest.raises(DemoTargetRefused):
        shared.assert_component_write(statement)


def test_generated_migration_passes_guard_and_only_creates_owned_tables():
    sql = render_sql(config())
    statements = []
    for statement in sql.split(";"):
        statement = re.sub(r"(?m)^--.*$", "", statement).strip()
        if statement and statement not in ("BEGIN", "COMMIT"):
            shared.assert_component_write(statement)
            statements.append(statement)
    assert len([s for s in statements if s.startswith("CREATE TABLE")]) == 7
    assert "examinations.alembic_version" not in sql


async def test_default_source_selects_shared_reader_without_local_fallback(monkeypatch):
    seen = []
    @asynccontextmanager
    async def reader(settings):
        seen.append(settings.source)
        yield "shared-reader"
    monkeypatch.setattr(shared, "shared_row_source", reader)
    async with default_row_source(config().demo) as source:
        assert source == "shared-reader"
    assert seen == [DemoSource.NEON_SHARED]


async def test_shared_reader_always_rolls_back_and_disposes(monkeypatch):
    calls = []
    class Session:
        def __init__(self, engine): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def rollback(self): calls.append("rollback")
    class Engine:
        async def dispose(self): calls.append("dispose")
    monkeypatch.setattr(shared, "load_settings", lambda: config().database)
    monkeypatch.setattr(shared, "AsyncSession", Session)
    monkeypatch.setattr(shared, "create_async_engine", lambda *a, **kw: Engine())
    with pytest.raises(RuntimeError):
        async with shared.shared_row_source(config().demo):
            raise RuntimeError("fixture failure")
    assert calls == ["rollback", "dispose"]


def test_api_identifies_shared_database_inputs_as_synthetic():
    env = Env(config().demo, factory=fake_source())
    status = env.get("/status").json()
    assert status["enabled"] is True and status["source"] == "NEON_SHARED"
    meta = env.get("/exam-periods").json()["meta"]
    assert meta["data_source"] == "NEON_SHARED" and meta["synthetic"] is True
    assert "Existing Neon database" in meta["source_description"]


def test_migration_does_not_take_over_partially_existing_component_tables(monkeypatch):
    monkeypatch.setattr(shared, "inspect_database", lambda c: {
        "revision": ["0012"], "duty_revision": [], "missing_input_tables": [],
        "missing_duty_tables": list(DUTY_TABLES[1:])})
    monkeypatch.setattr(shared, "create_engine", lambda *a, **kw: pytest.fail("must refuse before creating engine"))
    with pytest.raises(DemoTargetRefused, match="unexpected state"):
        shared.migrate_component(config())


def test_repeat_installation_is_read_only(monkeypatch):
    report = {"revision": ["0012"], "duty_revision": ["0013"], "missing_input_tables": [], "missing_duty_tables": []}
    monkeypatch.setattr(shared, "inspect_database", lambda c: report)
    monkeypatch.setattr(shared, "create_engine", lambda *a, **kw: pytest.fail("no write engine needed"))
    assert shared.migrate_component(config()) == report
