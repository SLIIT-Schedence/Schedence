"""Seating rules on a real, throw-away **local** PostgreSQL (never Neon).

Opt-in: set ``SCHEDENCE_TEST_POSTGRES_URL`` to an admin URL of a local server,
e.g. ``postgresql://postgres@127.0.0.1:55432/postgres``. Only localhost URLs are
accepted. The tests create and drop their own databases (``exam_seating_*``)
on that server; nothing else is touched.

Covers: migration 0001-0004 + seed data -> 0005 -> downgrade -> upgrade again
(seed data unchanged throughout), database constraints, row locking between two
real transactions, rollback on failure, read-only dry runs, the REPEATABLE READ
capacity snapshot and the HTTP API on a real database.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

psycopg = pytest.importorskip("psycopg")

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.auth.dependencies import get_current_user, require_exam_data_access, require_exam_settings_admin  # noqa: E402
from app.main import create_app  # noqa: E402
from app.seating.errors import SeatingConflictError  # noqa: E402
from app.seating.memory import MemorySeatingStore  # noqa: E402
from app.seating.service import SeatingService  # noqa: E402
from app.seating.snapshot import load_capacity_snapshot, snapshot_session  # noqa: E402
from app.seating.store import DatabaseSeatingStore  # noqa: E402
from app.seed.backup import SEED_TABLES, RestoreError, export_tables, restore_tables, verify_backup  # noqa: E402
from app.seed.generator import generate_dataset  # noqa: E402
from app.seed.seeder import seed_dataset  # noqa: E402
from app.sources import DatabaseSource, DatasetSource  # noqa: E402
from auth_support import EXAM_ADMIN, make_auth_context  # noqa: E402
from conftest import SERVICE_DIR  # noqa: E402
from schedence_db import create_db_engine, dispose_engine, load_settings  # noqa: E402

ADMIN_URL = os.environ.get("SCHEDENCE_TEST_POSTGRES_URL", "")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
TEMPLATE = "exam_seating_template"
SEED_TABLES = ("seed_modules", "seed_module_specializations", "seed_students", "seed_registrations",
               "seed_venues", "seed_venue_blocks", "seed_exam_periods", "seed_exam_period_exclusions")
USER = uuid.uuid4()
REASON = "Local PostgreSQL test"

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="set SCHEDENCE_TEST_POSTGRES_URL to a local PostgreSQL")
_template_ready = False


def _admin_url() -> str:
    url = make_url(ADMIN_URL)
    if url.host not in LOCAL_HOSTS:  # safety: these tests drop databases
        pytest.fail("SCHEDENCE_TEST_POSTGRES_URL must point to localhost")
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


def _db_url(name: str) -> str:
    return make_url(_admin_url()).set(database=name).render_as_string(hide_password=False)


def _admin(sql: str) -> None:
    with psycopg.connect(_admin_url(), autocommit=True) as conn:
        conn.execute(sql)


def _use_database(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str) -> str:
    """Point db-core (and so Alembic and the API) at the local test database only."""
    url = _db_url(name)
    empty_env = tmp_path / "empty.env"
    empty_env.write_text("", encoding="utf-8")
    monkeypatch.setenv("SCHEDENCE_ENV_FILE", str(empty_env))  # never read the developer's .env
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("DIRECT_DATABASE_URL", url)
    monkeypatch.setenv("APP_ENV", "testing")
    for direct in (False, True):
        assert load_settings().get_url(direct=direct).host in LOCAL_HOSTS
    return url


def _alembic(direction: str, revision: str, *x: str) -> None:
    config = Config(str(SERVICE_DIR / "alembic.ini"), cmd_opts=argparse.Namespace(x=list(x)))
    getattr(command, direction)(config, revision)


def _query(url: str, sql: str, *params):
    """Run ``sql`` in one committed transaction; rows of the last statement (if any)."""
    with psycopg.connect(url) as conn:
        cursor = conn.execute(sql, params or None)
        return cursor.fetchall() if cursor.description else []


def _seed_fingerprint(url: str) -> dict[str, tuple[int, str]]:
    """Row count and an MD5 of every row of every seed table."""
    return {
        table: tuple(_query(url, f"SELECT count(*), md5(coalesce(string_agg(t::text, '|' ORDER BY t::text), '')) "
                                 f"FROM examinations.{table} t")[0])
        for table in SEED_TABLES
    }


async def _seed() -> None:
    engine = create_db_engine(load_settings(), poolclass=NullPool)
    try:
        async with AsyncSession(engine) as session:
            await seed_dataset(session, generate_dataset(), dry_run=False)
            await session.commit()
    finally:
        await dispose_engine(engine)


@pytest.fixture(scope="module", autouse=True)
def _drop_template_afterwards():
    yield
    if _template_ready:
        _admin(f"DROP DATABASE IF EXISTS {TEMPLATE} WITH (FORCE)")


@pytest.fixture
async def database(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> str:
    """A fresh database at revision 0004 with the full seed dataset (2,314 rows)."""
    global _template_ready
    if not _template_ready:
        _admin(f"DROP DATABASE IF EXISTS {TEMPLATE}")
        _admin(f"CREATE DATABASE {TEMPLATE}")
        _query(_db_url(TEMPLATE), "CREATE SCHEMA examinations")
        _use_database(monkeypatch, tmp_path, TEMPLATE)
        _alembic("upgrade", "0004")
        await _seed()
        _template_ready = True
    name = f"exam_seating_{uuid.uuid4().hex[:10]}"
    _admin(f"CREATE DATABASE {name} TEMPLATE {TEMPLATE}")
    url = _use_database(monkeypatch, tmp_path, name)
    yield url
    _admin(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


@pytest.fixture
async def migrated(database: str) -> str:
    _alembic("upgrade", "0005")
    return database


@pytest.fixture
async def engine(migrated: str):
    engine = create_db_engine(load_settings(), poolclass=NullPool)
    yield engine
    await dispose_engine(engine)


def _service(session: AsyncSession) -> SeatingService:
    return SeatingService(DatabaseSeatingStore(session), DatabaseSource(session))


# --- Migration ------------------------------------------------------------------------------------------


async def test_upgrade_keeps_every_seed_row_and_adds_the_default(database: str) -> None:
    before = _seed_fingerprint(database)
    assert sum(count for count, _ in before.values()) == 2314
    _alembic("upgrade", "0005")
    assert _seed_fingerprint(database) == before
    assert _query(database, "SELECT version_num FROM examinations.alembic_version") == [("0005",)]
    assert _query(database, "SELECT scope, venue_id, percentage, version, updated_by FROM "
                            "examinations.exam_seating_rules") == [("default", None, 50, 1, None)]
    assert _query(database, "SELECT action, new_percentage, changed_by FROM "
                            "examinations.exam_seating_rule_history") == [("create", 50, None)]
    checks = {r[0] for r in _query(database, "SELECT conname FROM pg_constraint WHERE conrelid = "
                                             "'examinations.seed_venues'::regclass AND contype = 'c'")}
    assert "ck_seed_venues_exam_capacity_half" not in checks
    assert "ck_seed_venues_exam_capacity_range" in checks


async def test_downgrade_without_user_changes_restores_0004(migrated: str) -> None:
    before = _seed_fingerprint(migrated)
    _alembic("downgrade", "0004")
    assert _seed_fingerprint(migrated) == before
    assert _query(migrated, "SELECT to_regclass('examinations.exam_seating_rules')") == [(None,)]
    assert _query(migrated, "SELECT count(*) FROM pg_constraint WHERE conname = "
                            "'ck_seed_venues_exam_capacity_half'") == [(1,)]
    _alembic("upgrade", "0005")  # and up again
    assert _query(migrated, "SELECT percentage FROM examinations.exam_seating_rules") == [(50,)]


async def test_downgrade_refuses_to_drop_user_history(migrated: str, engine) -> None:
    async with AsyncSession(engine) as session:
        await _service(session).set_default(percentage=60, reason=REASON, expected_version=1, user_id=USER,
                                            dry_run=False)
    with pytest.raises(Exception, match="changed by users"):
        _alembic("downgrade", "0004")
    # Nothing changed: one transaction, rolled back.
    assert _query(migrated, "SELECT version_num FROM examinations.alembic_version") == [("0005",)]
    assert _query(migrated, "SELECT percentage, version FROM examinations.exam_seating_rules") == [(60, 2)]

    before = _seed_fingerprint(migrated)
    _alembic("downgrade", "0004", "drop_seating_history=true")
    assert _seed_fingerprint(migrated) == before
    assert _query(migrated, "SELECT version_num FROM examinations.alembic_version") == [("0004",)]


# --- Database constraints -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE examinations.exam_seating_rules SET percentage = 0",
        "UPDATE examinations.exam_seating_rules SET percentage = 101",
        "UPDATE examinations.exam_seating_rules SET version = 0",
        "INSERT INTO examinations.exam_seating_rules (scope, percentage, version, reason) VALUES ('default', 40, 1, 'x')",
        "INSERT INTO examinations.exam_seating_rules (scope, percentage, version, reason) VALUES ('venue', 40, 1, 'x')",
        "INSERT INTO examinations.exam_seating_rules (scope, venue_id, percentage, version, reason) "
        "VALUES ('venue', 'NOPE', 40, 1, 'x')",
        "INSERT INTO examinations.exam_seating_rules (scope, venue_id, percentage, version, reason) "
        "VALUES ('default', 'F303', 40, 1, 'x')",
        "INSERT INTO examinations.exam_seating_rule_history (rule_id, action, scope, old_percentage, "
        "new_percentage, version, reason) VALUES (1, 'update', 'default', NULL, 40, 2, 'x')",
        "INSERT INTO examinations.exam_seating_rule_history (rule_id, action, scope, old_percentage, "
        "new_percentage, version, reason) VALUES (1, 'rename', 'default', 50, 40, 2, 'x')",
    ],
)
async def test_database_rejects_invalid_rules(migrated: str, sql: str) -> None:
    with pytest.raises(psycopg.errors.IntegrityError):
        _query(migrated, sql + " RETURNING 1")


async def test_one_override_per_venue_and_venue_with_override_cannot_be_deleted(migrated: str) -> None:
    insert = ("INSERT INTO examinations.exam_seating_rules (scope, venue_id, percentage, version, reason) "
              "VALUES ('venue', 'F303', 40, 1, 'x') RETURNING 1")
    _query(migrated, insert)
    with pytest.raises(psycopg.errors.UniqueViolation):
        _query(migrated, insert)
    # ON DELETE RESTRICT raises restrict_violation (23001), not foreign_key_violation (23503). E.g. a seed replacement.
    with pytest.raises(psycopg.errors.RestrictViolation) as blocked:
        _query(migrated, "DELETE FROM examinations.seed_venue_blocks WHERE venue_id = 'F303'; "
                         "DELETE FROM examinations.seed_venues WHERE venue_id = 'F303' RETURNING 1")
    assert blocked.value.diag.constraint_name == "fk_exam_seating_rules_venue_id_seed_venues"
    assert _query(migrated, "SELECT count(*) FROM examinations.seed_venues WHERE venue_id = 'F303'") == [(1,)]


# --- Service on PostgreSQL ------------------------------------------------------------------------------------------


async def test_changes_and_history_are_stored(engine, migrated: str) -> None:
    async with AsyncSession(engine) as session:
        service = _service(session)
        await service.set_default(percentage=60, reason=REASON, expected_version=1, user_id=USER, dry_run=False)
        await service.set_venue_override("F303", percentage=33, reason=REASON, expected_version=None, user_id=USER,
                                         dry_run=False)
        await service.set_venue_override("F303", percentage=34, reason=REASON, expected_version=1, user_id=USER,
                                         dry_run=False)
        await service.delete_venue_override("F303", reason=REASON, expected_version=2, user_id=USER, dry_run=False)
        created = await service.set_venue_override("F303", percentage=70, reason=REASON, expected_version=None,
                                                   user_id=USER, dry_run=False)
        assert created.version == 3  # never reuses version 1 or 2

    rows = _query(migrated, "SELECT action, scope, venue_id, old_percentage, new_percentage, version, changed_by "
                            "FROM examinations.exam_seating_rule_history ORDER BY history_id")
    assert rows == [
        ("create", "default", None, None, 50, 1, None),
        ("update", "default", None, 50, 60, 2, USER),
        ("create", "venue", "F303", None, 33, 1, USER),
        ("update", "venue", "F303", 33, 34, 2, USER),
        ("delete", "venue", "F303", 34, None, 2, USER),
        ("create", "venue", "F303", None, 70, 3, USER),
    ]
    assert _query(migrated, "SELECT count(*) FROM examinations.exam_seating_rule_history "
                            "WHERE changed_at IS NULL OR reason <> %s AND changed_by IS NOT NULL", REASON) == [(0,)]


async def test_dry_run_runs_in_a_read_only_transaction(engine, migrated: str) -> None:
    read_only = engine.execution_options(postgresql_readonly=True)
    async with AsyncSession(read_only) as session:
        change = await _service(session).set_default(percentage=10, reason=REASON, expected_version=1,
                                                     user_id=USER, dry_run=True)
    assert change.dry_run and change.total_exam_capacity_after < change.total_exam_capacity_before
    assert _query(migrated, "SELECT percentage, version FROM examinations.exam_seating_rules") == [(50, 1)]
    assert _query(migrated, "SELECT count(*) FROM examinations.exam_seating_rule_history") == [(1,)]


async def test_failure_after_the_update_rolls_everything_back(engine, migrated: str) -> None:
    class FailingStore(DatabaseSeatingStore):
        async def add_history(self, **values):
            raise RuntimeError("history insert failed")

    async with AsyncSession(engine) as session:
        service = SeatingService(FailingStore(session), DatabaseSource(session))
        with pytest.raises(RuntimeError):
            await service.set_default(percentage=60, reason=REASON, expected_version=1, user_id=USER, dry_run=False)
    assert _query(migrated, "SELECT percentage, version FROM examinations.exam_seating_rules") == [(50, 1)]
    assert _query(migrated, "SELECT count(*) FROM examinations.exam_seating_rule_history") == [(1,)]


async def test_concurrent_update_waits_for_the_lock_then_conflicts(engine, migrated: str) -> None:
    async with AsyncSession(engine) as first, AsyncSession(engine) as second:
        first_store = DatabaseSeatingStore(first)
        locked = await first_store.get_rule(None, lock=True)  # admin 1 holds the row lock

        other = asyncio.create_task(_service(second).set_default(
            percentage=70, reason=REASON, expected_version=1, user_id=USER, dry_run=False))
        await asyncio.sleep(0.5)
        assert not other.done()  # admin 2 waits instead of overwriting

        await first_store.update_rule(locked.rule_id, expected_version=1, percentage=60, reason=REASON,
                                      updated_by=USER)
        await first.commit()
        with pytest.raises(SeatingConflictError) as error:
            await other
        assert error.value.current_version == 2
    assert _query(migrated, "SELECT percentage, version FROM examinations.exam_seating_rules") == [(60, 2)]


async def test_lock_wait_is_limited(engine, migrated: str) -> None:
    async with AsyncSession(engine) as first, AsyncSession(engine) as second:
        await DatabaseSeatingStore(first).get_rule(None, lock=True)
        service = SeatingService(DatabaseSeatingStore(second, lock_timeout_ms=200), DatabaseSource(second))
        with pytest.raises(SeatingConflictError, match="being changed"):
            await service.set_default(percentage=70, reason=REASON, expected_version=1, user_id=USER, dry_run=False)
        await first.rollback()
    assert _query(migrated, "SELECT percentage, version FROM examinations.exam_seating_rules") == [(50, 1)]


async def test_concurrent_override_creation_has_one_winner(engine, migrated: str) -> None:
    async def create(percentage: int):
        async with AsyncSession(engine) as session:
            return await _service(session).set_venue_override(
                "F303", percentage=percentage, reason=REASON, expected_version=None, user_id=USER, dry_run=False)

    results = await asyncio.gather(create(40), create(45), return_exceptions=True)
    assert sorted(type(r).__name__ for r in results) == ["SeatingChange", "SeatingConflictError"]
    assert _query(migrated, "SELECT count(*) FROM examinations.exam_seating_rules WHERE venue_id = 'F303'") == [(1,)]
    assert _query(migrated, "SELECT count(*) FROM examinations.exam_seating_rule_history "
                            "WHERE venue_id = 'F303'") == [(1,)]


# --- Capacity snapshot ------------------------------------------------------------------------------------------


async def test_snapshot_is_consistent_while_rules_change(engine, migrated: str) -> None:
    async with snapshot_session(engine) as snap:
        assert (await snap.execute(text("SHOW transaction_isolation"))).scalar_one() == "repeatable read"
        assert (await snap.execute(text("SHOW transaction_read_only"))).scalar_one() == "on"
        # PostgreSQL fixes the snapshot at the first data read of the transaction.
        await snap.execute(text("SELECT 1 FROM examinations.exam_seating_rules LIMIT 1"))
        # Another admin changes the default after that.
        async with AsyncSession(engine) as session:
            await _service(session).set_default(percentage=10, reason=REASON, expected_version=1, user_id=USER,
                                                dry_run=False)
        snapshot = await load_capacity_snapshot(DatabaseSource(snap), DatabaseSeatingStore(snap))
    assert snapshot.default_percentage == 50 and snapshot.ruleset_version == 1
    assert snapshot.exam_capacity("F303") == 120

    async with snapshot_session(engine) as snap:
        fresh = await load_capacity_snapshot(DatabaseSource(snap), DatabaseSeatingStore(snap))
    assert fresh.default_percentage == 10 and fresh.exam_capacity("F303") == 24
    assert fresh.ruleset_version == 2 and fresh.fingerprint != snapshot.fingerprint


# --- HTTP API on PostgreSQL ------------------------------------------------------------------------------------------


def _api_client() -> TestClient:
    app = create_app(make_auth_context())
    app.dependency_overrides[require_exam_data_access] = lambda: EXAM_ADMIN
    app.dependency_overrides[require_exam_settings_admin] = lambda: EXAM_ADMIN
    app.dependency_overrides[get_current_user] = lambda: EXAM_ADMIN
    return TestClient(app, backend_options={"loop_factory": asyncio.SelectorEventLoop})


def test_http_api_on_postgres(migrated: str) -> None:
    body = {"percentage": 60, "reason": REASON, "expected_version": 1}
    with _api_client() as client:
        preview = client.put("/api/v1/settings/exam-seating/default", params={"dry_run": True}, json=body)
        assert preview.status_code == 200 and preview.json()["dry_run"] is True
        assert _query(migrated, "SELECT version FROM examinations.exam_seating_rules") == [(1,)]

        saved = client.put("/api/v1/settings/exam-seating/default", json=body)
        assert saved.status_code == 200 and saved.json()["version"] == 2
        assert saved.json()["changed_by"] == str(EXAM_ADMIN.user_id)
        assert client.put("/api/v1/settings/exam-seating/default", json=body).status_code == 409

        venues = {v["venue_id"]: v for v in client.get("/api/v1/venues").json()}
        assert venues["F303"]["exam_capacity"] == 144 and venues["F303"]["seed_exam_capacity"] == 120

        override = client.put("/api/v1/settings/exam-seating/venues/Robotics%20Lab",
                              json={"percentage": 1, "reason": REASON, "expected_version": None})
        assert override.status_code == 200
        settings = client.get("/api/v1/settings/exam-seating").json()
        assert "Robotics Lab" in settings["unavailable_venues"]
        assert [h["action"] for h in settings["history"]] == ["create", "update", "create"]
        removed = client.delete("/api/v1/settings/exam-seating/venues/Robotics%20Lab",
                                params={"expected_version": 1, "reason": REASON})
        assert removed.status_code == 200
    assert _query(migrated, "SELECT count(*) FROM examinations.exam_seating_rule_history") == [(4,)]


def test_http_api_without_migration_0005_is_503(database: str) -> None:
    with _api_client() as client:
        response = client.get("/api/v1/settings/exam-seating")
        assert response.status_code == 503 and "not configured" in response.json()["detail"]
        assert client.get("/api/v1/venues").status_code == 503
        assert client.get("/api/v1/modules").status_code == 200  # other routes keep working


# --- Backup and restore with seating rules ------------------------------------------------------------------------


def _alembic_at(url: str, direction: str, revision: str, *x: str) -> None:
    """Run Alembic on another local test database; the fixture's settings are put back afterwards."""
    old = os.environ["DATABASE_URL"]
    os.environ["DATABASE_URL"] = os.environ["DIRECT_DATABASE_URL"] = url
    try:
        assert load_settings().get_url(direct=True).host in LOCAL_HOSTS
        _alembic(direction, revision, *x)
    finally:
        os.environ["DATABASE_URL"] = os.environ["DIRECT_DATABASE_URL"] = old


@pytest.fixture
def new_database(database: str):
    """Factory for more databases from the template (0004 + seed data), optionally upgraded to 0005."""
    names: list[str] = []

    def make(revision: str = "0004") -> str:
        name = f"exam_seating_{uuid.uuid4().hex[:10]}"
        _admin(f"CREATE DATABASE {name} TEMPLATE {TEMPLATE}")
        names.append(name)
        url = _db_url(name)
        if revision == "0005":
            _alembic_at(url, "upgrade", "0005")
        return url

    yield make
    for name in names:
        _admin(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


def _async_engine(url: str):
    return create_async_engine(make_url(url).set(drivername="postgresql+psycopg"), poolclass=NullPool)


async def _export(url: str, out_dir: Path) -> dict:
    engine = _async_engine(url)
    try:
        async with engine.connect() as conn:
            await conn.execution_options(isolation_level="REPEATABLE READ")
            async with conn.begin():
                return await export_tables(conn, out_dir)
    finally:
        await engine.dispose()


async def _restore(url: str, backup: Path, *, dry_run: bool = False, skip_seating: bool = False):
    engine = _async_engine(url)
    try:
        async with engine.connect() as conn:
            try:
                result = await restore_tables(conn, backup, dry_run=dry_run, skip_seating=skip_seating)
            except BaseException:
                await conn.rollback()
                raise
            if not dry_run:
                await conn.commit()
            return result
    finally:
        await engine.dispose()


def _seating_rows(url: str) -> dict[str, list[tuple]]:
    return {
        "rules": _query(url, "SELECT * FROM examinations.exam_seating_rules ORDER BY rule_id"),
        "history": _query(url, "SELECT * FROM examinations.exam_seating_rule_history ORDER BY history_id"),
    }


async def _make_changes(url: str) -> None:
    engine = _async_engine(url)
    try:
        async with AsyncSession(engine) as session:
            s = _service(session)
            await s.set_default(percentage=60, reason=REASON, expected_version=1, user_id=USER, dry_run=False)
            await s.set_venue_override("F303", percentage=70, reason=REASON, expected_version=None, user_id=USER,
                                       dry_run=False)
            await s.set_venue_override("F303", percentage=75, reason=REASON, expected_version=1, user_id=USER,
                                       dry_run=False)
            await s.set_venue_override("Robotics Lab", percentage=1, reason=REASON, expected_version=None,
                                       user_id=USER, dry_run=False)
            await s.delete_venue_override("Robotics Lab", reason=REASON, expected_version=1, user_id=USER,
                                          dry_run=False)
    finally:
        await engine.dispose()


async def _change_default(url: str, percentage: int, expected_version: int) -> None:
    engine = _async_engine(url)
    try:
        async with AsyncSession(engine) as session:
            await _service(session).set_default(percentage=percentage, reason=REASON,
                                                expected_version=expected_version, user_id=USER, dry_run=False)
    finally:
        await engine.dispose()


async def test_backup_at_0004_has_no_seating_tables(database: str, new_database, tmp_path: Path) -> None:
    manifest = await _export(database, tmp_path / "b")
    assert manifest["alembic_revision"] == "0004" and manifest["total_rows"] == 2314
    assert list(manifest["tables"]) == list(SEED_TABLES)
    assert verify_backup(tmp_path / "b") == []

    # Into a 0004 database: the seed rows are already there, nothing is inserted.
    result, seating = await _restore(new_database("0004"), tmp_path / "b")
    assert seating.mode == "not_in_backup", seating
    assert all(inserted == 0 for _, inserted in result.values()), result

    # Into a 0005 database: the seating rules are left exactly as they are.
    target = new_database("0005")
    before = _seating_rows(target)
    _, seating = await _restore(target, tmp_path / "b")
    assert seating.mode == "not_in_backup" and _seating_rows(target) == before


async def test_backup_restores_seating_after_downgrade_and_upgrade(migrated: str, tmp_path: Path) -> None:
    await _make_changes(migrated)
    source = _seating_rows(migrated)
    seed = _seed_fingerprint(migrated)
    manifest = await _export(migrated, tmp_path / "b")
    assert manifest["alembic_revision"] == "0005"
    assert manifest["tables"]["exam_seating_rules"]["rows"] == 2
    assert manifest["tables"]["exam_seating_rule_history"]["rows"] == 6
    assert manifest["total_rows"] == 2314 + 8 and verify_backup(tmp_path / "b") == []

    # Rollback of 0005 (history exported first), then upgrade again: only the 50% default exists.
    _alembic("downgrade", "0004", "drop_seating_history=true")
    _alembic("upgrade", "0005")
    assert len(_seating_rows(migrated)["history"]) == 1

    _, seating = await _restore(migrated, tmp_path / "b")
    assert seating.mode == "replaced_initial"
    assert seating.counts == {"exam_seating_rules": (2, 2), "exam_seating_rule_history": (6, 6)}
    assert _seating_rows(migrated) == source  # same IDs, users, times, reasons
    assert _seed_fingerprint(migrated) == seed

    # New IDs continue after the restored ones; versions continue too.
    engine = _async_engine(migrated)
    try:
        async with AsyncSession(engine) as session:
            change = await _service(session).set_venue_override(
                "Robotics Lab", percentage=5, reason=REASON, expected_version=None, user_id=USER, dry_run=False)
    finally:
        await engine.dispose()
    assert change.history_id == 7 and change.version == 2

    # Restoring the same backup again is refused: the database now has a newer change.
    with pytest.raises(RestoreError, match="newer than the backup"):
        await _restore(migrated, tmp_path / "b")


async def test_restore_is_idempotent_and_adds_only_missing_rows(migrated: str, tmp_path: Path) -> None:
    await _make_changes(migrated)
    source = _seating_rows(migrated)
    await _export(migrated, tmp_path / "b")

    _, seating = await _restore(migrated, tmp_path / "b")
    assert seating.mode == "merged" and seating.counts == {
        "exam_seating_rules": (2, 0), "exam_seating_rule_history": (6, 0)}

    _query(migrated, "DELETE FROM examinations.exam_seating_rules WHERE venue_id = 'F303'; "
                     "DELETE FROM examinations.exam_seating_rule_history WHERE history_id = 6")
    _, seating = await _restore(migrated, tmp_path / "b")
    assert seating.counts == {"exam_seating_rules": (2, 1), "exam_seating_rule_history": (6, 1)}
    assert _seating_rows(migrated) == source


async def test_restore_into_a_fresh_0005_database(migrated: str, new_database, tmp_path: Path) -> None:
    await _make_changes(migrated)
    await _export(migrated, tmp_path / "b")
    target = new_database("0005")

    await _restore(target, tmp_path / "b", dry_run=True)
    assert len(_seating_rows(target)["history"]) == 1  # dry run: nothing kept

    _, seating = await _restore(target, tmp_path / "b")
    assert seating.mode == "replaced_initial" and _seating_rows(target) == _seating_rows(migrated)


async def test_restore_refuses_newer_changes_and_changes_nothing(migrated: str, tmp_path: Path) -> None:
    await _make_changes(migrated)
    await _export(migrated, tmp_path / "b")
    await _change_default(migrated, 65, expected_version=2)
    # A seed row is missing too: a successful restore would add it back.
    _query(migrated, "DELETE FROM examinations.seed_exam_period_exclusions")
    seating, seed = _seating_rows(migrated), _seed_fingerprint(migrated)

    with pytest.raises(RestoreError, match="newer than the backup"):
        await _restore(migrated, tmp_path / "b")
    assert _seating_rows(migrated) == seating and _seed_fingerprint(migrated) == seed  # all rolled back


async def test_0005_backup_into_0004_database_needs_skip_seating(migrated: str, new_database, tmp_path: Path) -> None:
    await _make_changes(migrated)
    await _export(migrated, tmp_path / "b")
    target = new_database("0004")
    _query(target, "DELETE FROM examinations.seed_exam_period_exclusions")

    with pytest.raises(RestoreError, match="upgrade 0005"):
        await _restore(target, tmp_path / "b")
    assert _query(target, "SELECT count(*) FROM examinations.seed_exam_period_exclusions") == [(0,)]

    result, seating = await _restore(target, tmp_path / "b", skip_seating=True)
    assert seating.mode == "skipped" and result["seed_exam_period_exclusions"] == (1, 1)
    assert _query(target, "SELECT to_regclass('examinations.exam_seating_rules')") == [(None,)]


# --- DatasetSource and DatabaseSource agree -----------------------------------------------------------------------


async def _apply_same_rules(service: SeatingService) -> None:
    await service.set_default(percentage=37, reason=REASON, expected_version=1, user_id=USER, dry_run=False)
    await service.set_venue_override("F303", percentage=99, reason=REASON, expected_version=None, user_id=USER,
                                     dry_run=False)
    await service.set_venue_override("Robotics Lab", percentage=1, reason=REASON, expected_version=None,
                                     user_id=USER, dry_run=False)


async def test_database_and_dataset_sources_give_the_same_effective_capacities(engine, migrated: str) -> None:
    memory_store = MemorySeatingStore()
    memory = SeatingService(memory_store, DatasetSource(generate_dataset()))
    await _apply_same_rules(memory)
    async with AsyncSession(engine) as session:
        await _apply_same_rules(_service(session))

    async with snapshot_session(engine) as snap:
        from_db = await load_capacity_snapshot(DatabaseSource(snap), DatabaseSeatingStore(snap))
        api_db = await SeatingService(DatabaseSeatingStore(snap), DatabaseSource(snap)).venues()
    from_memory = await load_capacity_snapshot(DatasetSource(generate_dataset()), memory_store)
    assert from_db.venues == from_memory.venues and from_db.fingerprint == from_memory.fingerprint
    assert [c for _, c in api_db] == [c for _, c in await memory.venues()]
    assert from_db.exam_capacity("Robotics Lab") == 0 and from_db.exam_capacity("F303") == 237


# --- Scheduling settings (migration 0006) --------------------------------------------------------------------

from app.scheduling.configuration import SchedulingConfiguration  # noqa: E402
from app.scheduling_settings.errors import (  # noqa: E402
    SchedulingSettingsConflictError,
    SchedulingSettingsInvalidStoredError,
    SchedulingSettingsNotConfiguredError,
)
from app.scheduling_settings.service import SchedulingSettingsService  # noqa: E402
from app.scheduling_settings.store import DatabaseSchedulingSettingsStore  # noqa: E402

SETTINGS_INSTITUTION = uuid.UUID("33333333-3333-4333-8333-333333333333")  # test value, not a real institution


def _settings_document(minimum_break: int) -> dict:
    document = SchedulingConfiguration().to_document()
    document["scheduling"]["minimum_break_minutes"] = minimum_break
    return document


def _settings_service(session: AsyncSession, institution=SETTINGS_INSTITUTION) -> SchedulingSettingsService:
    return SchedulingSettingsService(DatabaseSchedulingSettingsStore(session), institution)


@pytest.fixture
async def settings_engine(migrated: str):
    _alembic("upgrade", "0006")
    engine = create_db_engine(load_settings(), poolclass=NullPool)
    yield engine
    await dispose_engine(engine)


async def test_0006_inserts_no_rows_and_leaves_seed_and_seating_data_alone(database: str) -> None:
    _alembic("upgrade", "0005")
    before = _seed_fingerprint(database), _query(database, "SELECT * FROM examinations.exam_seating_rule_history")
    _alembic("upgrade", "0006")
    assert (_seed_fingerprint(database), _query(database, "SELECT * FROM examinations.exam_seating_rule_history")) == before
    assert _query(database, "SELECT count(*) FROM examinations.exam_scheduling_settings") == [(0,)]
    assert _query(database, "SELECT count(*) FROM examinations.exam_scheduling_settings_history") == [(0,)]


async def test_0006_settings_are_saved_versioned_and_read_back(settings_engine, migrated: str) -> None:
    async with AsyncSession(settings_engine) as session:
        service = _settings_service(session)
        assert (await service.view(history_limit=5)).version == 0
        change = await service.update(_settings_document(90), reason=REASON, expected_version=0, user_id=USER,
                                      dry_run=False)
        assert (change.action, change.version) == ("create", 1)
    async with AsyncSession(settings_engine) as session:
        service = _settings_service(session)
        view = await service.view(history_limit=5)
        assert view.saved.settings == _settings_document(90)  # JSONB round trip
        assert (await service.configuration()).scheduling.minimum_break_minutes == 90
        with pytest.raises(SchedulingSettingsConflictError):
            await service.update(_settings_document(30), reason=REASON, expected_version=0, user_id=USER,
                                 dry_run=False)
        change = await service.update(_settings_document(30), reason=REASON, expected_version=1, user_id=USER,
                                      dry_run=False)
        assert change.version == 2
    rows = _query(migrated, "SELECT action, version, old_settings IS NULL, changed_by FROM "
                            "examinations.exam_scheduling_settings_history ORDER BY history_id")
    assert rows == [("create", 1, True, USER), ("update", 2, False, USER)]
    assert _query(migrated, "SELECT institution_id, version FROM examinations.exam_scheduling_settings") == [
        (SETTINGS_INSTITUTION, 2)]


async def test_0006_dry_run_writes_nothing(settings_engine, migrated: str) -> None:
    async with AsyncSession(settings_engine) as session:
        await _settings_service(session).update(_settings_document(90), reason=REASON, expected_version=0,
                                                user_id=USER, dry_run=True)
    assert _query(migrated, "SELECT count(*) FROM examinations.exam_scheduling_settings") == [(0,)]


async def test_0006_two_first_saves_cannot_both_win(settings_engine) -> None:
    async with AsyncSession(settings_engine) as first, AsyncSession(settings_engine) as second:
        a, b = DatabaseSchedulingSettingsStore(first), DatabaseSchedulingSettingsStore(second)
        await a.insert(institution_id=SETTINGS_INSTITUTION, format="scheduling-settings-v1",
                       settings=_settings_document(90), reason=REASON, updated_by=USER)
        await a.commit()
        with pytest.raises(SchedulingSettingsConflictError):
            await b.insert(institution_id=SETTINGS_INSTITUTION, format="scheduling-settings-v1",
                           settings=_settings_document(30), reason=REASON, updated_by=USER)


async def test_0006_settings_of_another_institution_are_separate(settings_engine) -> None:
    async with AsyncSession(settings_engine) as session:
        await _settings_service(session).update(_settings_document(90), reason=REASON, expected_version=0,
                                                user_id=USER, dry_run=False)
    async with AsyncSession(settings_engine) as session:
        other = await _settings_service(session, uuid.uuid4()).view(history_limit=5)
        assert (other.source, other.version) == ("defaults", 0)


async def test_settings_at_0005_are_a_safe_503(engine) -> None:
    async with AsyncSession(engine) as session:
        with pytest.raises(SchedulingSettingsNotConfiguredError):
            await _settings_service(session).view(history_limit=5)


async def test_0006_downgrade_keeps_saved_settings_unless_told_otherwise(settings_engine, migrated: str) -> None:
    async with AsyncSession(settings_engine) as session:
        await _settings_service(session).update(_settings_document(90), reason=REASON, expected_version=0,
                                                user_id=USER, dry_run=False)
    await dispose_engine(settings_engine)
    with pytest.raises(Exception, match="scheduling settings were saved"):
        _alembic("downgrade", "0005")
    assert _query(migrated, "SELECT version_num FROM examinations.alembic_version") == [("0006",)]
    _alembic("downgrade", "0005", "drop_scheduling_settings=true")
    assert _query(migrated, "SELECT version_num FROM examinations.alembic_version") == [("0005",)]


async def test_0006_invalid_stored_settings_are_reported_and_repaired(settings_engine, migrated: str) -> None:
    async with AsyncSession(settings_engine) as session:
        await _settings_service(session).update(_settings_document(90), reason=REASON, expected_version=0,
                                                user_id=USER, dry_run=False)
    # As a stricter code update or a manual edit might leave it: valid JSON, invalid settings.
    _query(migrated, "UPDATE examinations.exam_scheduling_settings "
                     "SET settings = jsonb_set(settings, '{solver,num_search_workers}', '64')")
    broken = _query(migrated, "SELECT settings FROM examinations.exam_scheduling_settings")[0][0]
    async with AsyncSession(settings_engine) as session:
        service = _settings_service(session)
        view = await service.view(history_limit=5)
        assert (view.status, view.version, view.saved.settings) == ("invalid", 1, broken)
        with pytest.raises(SchedulingSettingsInvalidStoredError):
            await service.configuration()
        with pytest.raises(SchedulingSettingsConflictError):
            await service.update(_settings_document(30), reason=REASON, expected_version=1, user_id=USER,
                                 dry_run=False)
        change = await service.update(_settings_document(30), reason=REASON, expected_version=1, user_id=USER,
                                      dry_run=False, replace_invalid=True)
        assert (change.action, change.version) == ("repair", 2)
    [(action, old_settings, old_format, changed)] = _query(
        migrated, "SELECT action, old_settings, old_format, changed_fields FROM "
                  "examinations.exam_scheduling_settings_history WHERE version = 2")
    assert (action, old_settings, old_format) == ("repair", broken, "scheduling-settings-v1")
    assert changed == list(change.changed_fields)


def test_0006_settings_survive_a_service_restart(migrated: str) -> None:
    _alembic("upgrade", "0006")
    url = "/api/v1/settings/scheduling"
    with _api_client() as client:
        assert (client.get(url).json()["source"], client.get(url).json()["version"]) == ("defaults", 0)
        first = client.put(url, json={"settings": _settings_document(90), "reason": REASON, "expected_version": 0})
        assert first.status_code == 200 and (first.json()["action"], first.json()["version"]) == ("create", 1)
        second = client.put(url, json={"settings": _settings_document(30), "reason": REASON, "expected_version": 1})
        assert second.status_code == 200 and (second.json()["action"], second.json()["version"]) == ("update", 2)
    # A new app has its own engine and connection pool, as after a restart of the service.
    with _api_client() as client:
        view = client.get(url).json()
        assert (view["source"], view["status"], view["version"]) == ("saved", "valid", 2)
        assert view["settings"] == _settings_document(30) and view["fingerprint"] == second.json()["fingerprint"]
        assert [(h["action"], h["version"]) for h in view["history"]] == [("update", 2), ("create", 1)]
        stale = client.put(url, json={"settings": _settings_document(60), "reason": REASON, "expected_version": 1})
        assert stale.status_code == 409
    assert _query(migrated, "SELECT action, version, old_settings IS NULL FROM "
                            "examinations.exam_scheduling_settings_history ORDER BY history_id") == [
        ("create", 1, True), ("update", 2, False)]
