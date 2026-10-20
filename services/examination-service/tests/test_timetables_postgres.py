"""Saved timetables (migration 0010) on a real, throw-away **local** PostgreSQL (never Neon).

Opt-in, like test_seating_postgres: set ``SCHEDENCE_TEST_POSTGRES_URL`` to an admin
URL of a local server. The tests create and drop their own databases
(``exam_timetables_*``); nothing else is touched. The template is migrated to 0009
and seeded, so the generator reads the real dataset from PostgreSQL.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import uuid
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")
pytest.importorskip("ortools")

from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app.auth.dependencies import require_exam_approval, require_exam_generation, require_exam_seat_access  # noqa: E402
from app.saved_timetables import DatabaseTimetableStore, ListFilter, NewTimetable  # noqa: E402
from auth_support import INSTITUTION, OTHER_INSTITUTION  # noqa: E402
from schedence_db import create_db_engine, dispose_engine, load_settings  # noqa: E402
from test_seating_postgres import (  # noqa: E402
    ADMIN_URL,
    EXAM_ADMIN,
    USER,
    TestClient,
    _admin,
    _alembic,
    _api_client,
    _db_url,
    _query,
    _seed,
    _seed_fingerprint,
    _use_database,
)

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="set SCHEDENCE_TEST_POSTGRES_URL to a local PostgreSQL")
TEMPLATE = "exam_timetables_template"
_template_ready = False


@pytest.fixture(scope="module", autouse=True)
def _drop_template_afterwards():
    yield
    if _template_ready:
        _admin(f"DROP DATABASE IF EXISTS {TEMPLATE} WITH (FORCE)")


@pytest.fixture
async def database(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> str:
    """A fresh database at revision 0009 with the full seed dataset."""
    global _template_ready
    if not _template_ready:
        _admin(f"DROP DATABASE IF EXISTS {TEMPLATE}")
        _admin(f"CREATE DATABASE {TEMPLATE}")
        _query(_db_url(TEMPLATE), "CREATE SCHEMA examinations")
        _use_database(monkeypatch, tmp_path, TEMPLATE)
        _alembic("upgrade", "0009")
        await _seed()
        _template_ready = True
    name = f"exam_timetables_{uuid.uuid4().hex[:10]}"
    _admin(f"CREATE DATABASE {name} TEMPLATE {TEMPLATE}")
    url = _use_database(monkeypatch, tmp_path, name)
    yield url
    _admin(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


TIMETABLE_TABLES = ("exam_timetables", "exam_timetable_sessions", "exam_timetable_rooms", "exam_timetable_seats")
DRAFT_BODY = {"period_code": "PP1-2026-S2-FINAL", "module_codes": ["IT1010", "IT1030"], "time_limit_seconds": 20}


def _timetable_counts(url: str) -> dict[str, int]:
    return {t: _query(url, f"SELECT count(*) FROM examinations.{t}")[0][0] for t in TIMETABLE_TABLES}


def _timetable_client() -> TestClient:
    client = _api_client()
    for dependency in (require_exam_generation, require_exam_seat_access, require_exam_approval):
        client.app.dependency_overrides[dependency] = lambda: EXAM_ADMIN
    return client


def _header(**changes) -> dict:
    header = dict(period_code="PP1-2026-S2-FINAL", period_name="Test cycle", exam_kind="FINAL", mode="feasibility",
                  selection="listed_modules", module_codes=None, solver_status="FEASIBLE", solver_version=None,
                  solver_run={}, input_fingerprint="a" * 64, settings_version=0, settings_fingerprint="b" * 64,
                  seating_ruleset_version=1, capacity_fingerprint="c" * 64, validation_passed=True,
                  validation={}, inputs={}, summary={}, workload=None, optimization=None,
                  excluded_registrations={}, notices=[], created_by=USER)
    return {**header, **changes}


def _one_seat_timetable() -> NewTimetable:
    session = dict(session_id="S1", module_code="IT1010", module_name="M", exam_type="written",
                   candidate_scope="all", academic_year=1, semester=2, delivery_mode="regular", session_number=1,
                   session_count=1, starts_at=dt.datetime(2026, 11, 2, 9), ends_at=dt.datetime(2026, 11, 2, 11),
                   duration_minutes=120, reading_time_minutes=0, candidates=1, normal_candidates=1,
                   repeat_candidates=0)
    room = dict(session_id="S1", venue_id="F303", venue_name="F303", venue_type="lecture_hall", seats=1,
                exam_capacity=10)
    seat = dict(session_id="S1", venue_id="F303", seat_number=1, student_id="IT10000001", registration_type="normal")
    return NewTimetable(_header(), [session], [room], [seat])


@pytest.fixture
async def timetable_engine(database: str):
    _alembic("upgrade", "0011")  # saving also writes the 0011 history
    engine = create_db_engine(load_settings(), poolclass=NullPool)
    yield engine
    await dispose_engine(engine)


async def test_0010_inserts_no_rows_keeps_other_data_and_downgrades_when_empty(database: str) -> None:
    _alembic("upgrade", "0009")
    before = _seed_fingerprint(database)
    _alembic("upgrade", "0010")
    assert _seed_fingerprint(database) == before
    assert _timetable_counts(database) == dict.fromkeys(TIMETABLE_TABLES, 0)
    _alembic("downgrade", "0009")
    assert _query(database, "SELECT to_regclass('examinations.exam_timetables')") == [(None,)]
    _alembic("upgrade", "0010")
    assert _query(database, "SELECT version_num FROM examinations.alembic_version") == [("0010",)]


def test_0010_drafts_are_saved_listed_viewed_and_kept(timetable_engine, database: str) -> None:
    with _timetable_client() as client:
        first = client.post("/api/v1/timetables/drafts", json=DRAFT_BODY)
        assert first.status_code == 201, first.text
        second = client.post("/api/v1/timetables/drafts", json={**DRAFT_BODY, "mode": "optimized"})
        assert second.status_code == 201, second.text
        first, second = first.json(), second.json()
        assert (first["draft_number"], second["draft_number"]) == (1, 2)
        assert first["status"] == "DRAFT" and first["seating_ruleset_version"] >= 1
        listing = client.get("/api/v1/timetables", params={"period_code": "PP1-2026-S2-FINAL"})
        assert listing.json()["total"] == 2 and "student_id" not in listing.text
        view = client.get(f"/api/v1/timetables/{first['timetable_id']}").json()
        assert view["sessions"] == first["sessions"] and view["mode"] == "feasibility"
        seats = client.get(f"/api/v1/timetables/{first['timetable_id']}/seats", params={"limit": 500}).json()
        assert seats["total"] == first["summary"]["seat_allocations"]
    seat_total = first["summary"]["seat_allocations"] + second["summary"]["seat_allocations"]
    counts = _timetable_counts(database)
    assert counts["exam_timetables"] == 2 and counts["exam_timetable_seats"] == seat_total
    assert _query(database, "SELECT DISTINCT institution_id::text, created_by::text FROM "
                            "examinations.exam_timetables") == [(str(INSTITUTION), str(EXAM_ADMIN.user_id))]
    # Saved rows and their history block a downgrade (one transaction: nothing changed).
    with pytest.raises(Exception, match="workflow history exists"):
        _alembic("downgrade", "0009")
    assert _timetable_counts(database) == counts


def test_0010_without_the_migration_is_503(database: str) -> None:
    with _timetable_client() as client:
        assert client.get("/api/v1/timetables").status_code == 503
        assert client.post("/api/v1/timetables/drafts", json=DRAFT_BODY).status_code == 503


@pytest.mark.parametrize("sql", [
    "UPDATE examinations.exam_timetables SET status = 'APPROVED'",  # approval columns missing
    "UPDATE examinations.exam_timetables SET status = 'LIVE'",
    "UPDATE examinations.exam_timetables SET validation_passed = false",
    "UPDATE examinations.exam_timetables SET solver_status = 'INFEASIBLE'",
    "UPDATE examinations.exam_timetable_rooms SET seats = exam_capacity + 1",
    "UPDATE examinations.exam_timetable_seats SET registration_type = 'audit'",
    "INSERT INTO examinations.exam_timetable_seats SELECT timetable_id, session_id, venue_id, seat_number + 1000,"
    " student_id, registration_type FROM examinations.exam_timetable_seats LIMIT 1",  # same student twice
])
async def test_0010_database_rejects_invalid_rows(timetable_engine, database: str, sql: str) -> None:
    async with AsyncSession(timetable_engine) as session:
        store = DatabaseTimetableStore(session)
        saved = await store.save(INSTITUTION, _one_seat_timetable())
        await store.commit()
    assert saved["draft_number"] == 1
    with pytest.raises(psycopg.errors.IntegrityError):
        _query(database, sql)


async def test_0010_one_published_timetable_per_cycle(timetable_engine, database: str) -> None:
    async with AsyncSession(timetable_engine) as session:
        store = DatabaseTimetableStore(session)
        for _ in range(2):
            await store.save(INSTITUTION, NewTimetable(_header()))
        await store.commit()
    approve = ("UPDATE examinations.exam_timetables SET status = 'APPROVED', revision = revision + 1,"
               " approved_by = created_by, approved_at = now()")
    publish = ("UPDATE examinations.exam_timetables SET status = 'PUBLISHED', revision = revision + 1,"
               " published_by = created_by, published_at = now() WHERE draft_number = %s")
    _query(database, approve)
    _query(database, publish, 1)
    with pytest.raises(psycopg.errors.UniqueViolation):
        _query(database, publish, 2)


async def test_0010_concurrent_saves_get_different_draft_numbers(timetable_engine) -> None:
    async def save() -> int:
        async with AsyncSession(timetable_engine) as session:
            store = DatabaseTimetableStore(session)
            header = await store.save(INSTITUTION, NewTimetable(_header()))
            await asyncio.sleep(0.2)  # hold the cycle lock while the others wait
            await store.commit()
            return header["draft_number"]

    assert sorted(await asyncio.gather(save(), save(), save())) == [1, 2, 3]


async def test_0010_store_is_scoped_to_one_institution_and_rolls_back(timetable_engine) -> None:
    async with AsyncSession(timetable_engine) as session:
        store = DatabaseTimetableStore(session)
        other = await store.save(OTHER_INSTITUTION, _one_seat_timetable())
        await store.commit()
        await store.save(INSTITUTION, NewTimetable(_header()))
        await store.rollback()  # never committed
        assert (await store.list(INSTITUTION, ListFilter(), limit=10, offset=0)) == ([], 0)
        assert await store.get(INSTITUTION, other["timetable_id"]) is None
        assert await store.seats(INSTITUTION, other["timetable_id"], session_id=None, limit=10, offset=0) is None
        assert (await store.seats(OTHER_INSTITUTION, other["timetable_id"], session_id=None, limit=10,
                                  offset=0))[1] == 1
        mine = await store.save(INSTITUTION, NewTimetable(_header()))
        assert mine["draft_number"] == 1  # numbered per institution


# --- 0011: approval and publishing workflow on PostgreSQL -----------------------------------------------------

REASON_TEXT = "Checked by the examination board"


def _approve(client, timetable: dict, action: str = "approve", **extra):
    return client.post(f"/api/v1/timetables/{timetable['timetable_id']}/{action}",
                       json={"expected_revision": timetable["revision"], "reason": REASON_TEXT, **extra})


async def _saved_one_seat(engine) -> int:
    async with AsyncSession(engine) as session:
        store = DatabaseTimetableStore(session)
        saved = await store.save(INSTITUTION, _one_seat_timetable())
        await store.commit()
    return saved["timetable_id"]


async def test_0011_keeps_0010_rows_adds_nothing_and_guards_its_downgrade(database: str) -> None:
    _alembic("upgrade", "0010")
    _query(database, "INSERT INTO examinations.exam_timetables (institution_id, period_code, period_name, exam_kind,"
                     " draft_number, mode, selection, solver_status, solver_run, input_fingerprint, settings_version,"
                     " settings_fingerprint, seating_ruleset_version, capacity_fingerprint, validation_passed,"
                     " validation, inputs, summary, excluded_registrations, notices, created_by) VALUES (%s,"
                     " 'PP1-2026-S2-FINAL', 'x', 'FINAL', 1, 'feasibility', 'listed_modules', 'FEASIBLE', '{}', 'a',"
                     " 0, 'b', 1, 'c', true, '{}', '{}', '{}', '{}', '[]', %s)", INSTITUTION, USER)
    before = _seed_fingerprint(database), _query(database, "SELECT * FROM examinations.exam_timetables")
    _alembic("upgrade", "0011")
    assert _seed_fingerprint(database) == before[0]
    assert _query(database, "SELECT timetable_id, status, content_fingerprint, source_timetable_id FROM "
                            "examinations.exam_timetables") == [(before[1][0][0], "DRAFT", None, None)]
    assert _query(database, "SELECT count(*) FROM examinations.exam_timetable_history") == [(0,)]
    _alembic("downgrade", "0010")  # drafts only, no history: allowed
    assert _query(database, "SELECT * FROM examinations.exam_timetables") == before[1]
    _alembic("upgrade", "0011")


@pytest.mark.parametrize("sql", [
    "UPDATE examinations.exam_timetables SET period_name = 'changed'",  # content is fixed
    "UPDATE examinations.exam_timetables SET status = 'PUBLISHED', revision = 2, approved_by = created_by,"
    " approved_at = now(), published_by = created_by, published_at = now()",  # skips APPROVED
    "UPDATE examinations.exam_timetables SET status = 'APPROVED', approved_by = created_by, approved_at = now()",
    "UPDATE examinations.exam_timetables SET revision = 7",
    "UPDATE examinations.exam_timetable_seats SET seat_number = 99",  # never, even as a draft
    "UPDATE examinations.exam_timetable_sessions SET module_name = 'x'",
    "UPDATE examinations.exam_timetable_history SET reason = 'rewritten'",
    "INSERT INTO examinations.exam_timetables (institution_id, period_code, period_name, exam_kind, draft_number,"
    " status, revision, mode, selection, solver_status, solver_run, input_fingerprint, settings_version,"
    " settings_fingerprint, seating_ruleset_version, capacity_fingerprint, validation_passed, validation, inputs,"
    " summary, excluded_registrations, notices, created_by, approved_by, approved_at) SELECT institution_id,"
    " period_code, period_name, exam_kind, 9, 'APPROVED', 1, mode, selection, solver_status, solver_run,"
    " input_fingerprint, settings_version, settings_fingerprint, seating_ruleset_version, capacity_fingerprint,"
    " true, validation, inputs, summary, excluded_registrations, notices, created_by, created_by, now()"
    " FROM examinations.exam_timetables",  # only drafts may be inserted
])
async def test_0011_triggers_refuse_silent_changes(timetable_engine, database: str, sql: str) -> None:
    await _saved_one_seat(timetable_engine)
    with pytest.raises(psycopg.errors.IntegrityError):
        _query(database, sql)


@pytest.mark.parametrize("sql", [
    "DELETE FROM examinations.exam_timetable_seats",
    "INSERT INTO examinations.exam_timetable_seats SELECT timetable_id, session_id, venue_id, 2, 'IT10000002',"
    " 'normal' FROM examinations.exam_timetable_seats",
    "DELETE FROM examinations.exam_timetables",
    "UPDATE examinations.exam_timetables SET approved_at = now() - interval '1 day'",
])
async def test_0011_allocations_are_locked_after_approval(timetable_engine, database: str, sql: str) -> None:
    await _saved_one_seat(timetable_engine)
    if "INSERT" in sql:  # still a draft: rows may be added (approval then fails on the content fingerprint)
        _query(database, sql.replace("2, 'IT10000002'", "3, 'IT10000003'"))
    _query(database, "UPDATE examinations.exam_timetables SET status = 'APPROVED', revision = 2,"
                     " approved_by = created_by, approved_at = now()")
    with pytest.raises(psycopg.errors.IntegrityError):
        _query(database, sql)


def test_0011_workflow_over_http_on_postgres(timetable_engine, database: str) -> None:
    with _timetable_client() as client:
        first = client.post("/api/v1/timetables/drafts", json=DRAFT_BODY).json()
        approved = _approve(client, first)
        assert approved.status_code == 200, approved.text
        published = _approve(client, approved.json()["timetable"], "publish")
        assert published.status_code == 200, published.text
        assert _approve(client, first).status_code == 409  # stale revision and status

        second = client.post(f"/api/v1/timetables/{first['timetable_id']}/regenerate", json={}).json()
        assert (second["draft_number"], second["source_timetable_id"]) == (2, first["timetable_id"])
        refused = _approve(client, second, "publish")
        assert refused.status_code == 409 and "must be approved" in refused.text
        second = _approve(client, second).json()["timetable"]
        refused = _approve(client, second, "publish")
        assert refused.status_code == 409 and refused.json()["detail"][0]["rule"] == "ALREADY_PUBLISHED"
        replaced = _approve(client, second, "publish", replace_published=True)
        assert replaced.status_code == 200, replaced.text
        history = client.get(f"/api/v1/timetables/{first['timetable_id']}/history").json()
        assert [h["action"] for h in history] == ["create", "approve", "publish", "archive"]
    assert _query(database, "SELECT draft_number, status, revision FROM examinations.exam_timetables"
                            " ORDER BY draft_number") == [(1, "ARCHIVED", 4), (2, "PUBLISHED", 3)]
    assert _query(database, "SELECT count(*) FROM examinations.exam_timetable_history") == [(7,)]
    seats = _query(database, "SELECT count(*) FROM examinations.exam_timetable_seats WHERE timetable_id = %s",
                   first["timetable_id"])
    assert seats == [(first["summary"]["seat_allocations"],)]  # unchanged by the workflow


def test_0011_changed_source_data_blocks_approval_on_postgres(timetable_engine, database: str) -> None:
    with _timetable_client() as client:
        draft = client.post("/api/v1/timetables/drafts", json=DRAFT_BODY).json()
        _query(database, "DELETE FROM examinations.seed_registrations WHERE ctid IN (SELECT ctid FROM"
                         " examinations.seed_registrations WHERE module_code = 'IT1010' LIMIT 1)")
        response = _approve(client, draft)
        assert response.status_code == 409, response.text
        rules = [p["rule"] for p in response.json()["detail"]]
        assert rules[0] == "STALE" and "STALE_INPUTS" in rules and "H11" in rules
        assert client.get(f"/api/v1/timetables/{draft['timetable_id']}").json()["status"] == "DRAFT"


async def test_0011_concurrent_publications_have_one_winner(timetable_engine, database: str) -> None:
    from types import SimpleNamespace

    from app.api.deps import get_timetable_generator
    from app.saved_timetables import workflow
    from app.saved_timetables.errors import TimetableWorkflowError
    from schedence_db import create_session_factory

    with _timetable_client() as client:
        approved = []
        for _ in range(2):
            draft = client.post("/api/v1/timetables/drafts", json=DRAFT_BODY).json()
            approved.append(_approve(client, draft).json()["timetable"])
    state = SimpleNamespace(engine=timetable_engine, generation_lock=asyncio.Lock(),
                            session_factory=create_session_factory(
                                timetable_engine.execution_options(postgresql_readonly=True)))
    generator = get_timetable_generator(SimpleNamespace(app=SimpleNamespace(state=state)),
                                        SimpleNamespace(settings=SimpleNamespace(institution_id=INSTITUTION)))

    async def publish(timetable):
        async with AsyncSession(timetable_engine) as session:
            try:
                return await workflow.publish(DatabaseTimetableStore(session), generator, INSTITUTION,
                                              timetable["timetable_id"], expected_revision=2, user_id=USER,
                                              reason=REASON_TEXT, replace_published=False)
            except TimetableWorkflowError as exc:
                await session.rollback()
                return exc.rule

    results = await asyncio.gather(*(publish(t) for t in approved))
    assert sorted(r if isinstance(r, str) else "PUBLISHED" for r in results) == ["ALREADY_PUBLISHED", "PUBLISHED"]
    assert _query(database, "SELECT count(*) FROM examinations.exam_timetables WHERE status = 'PUBLISHED'") == [(1,)]


# --- Backup and restore of saved timetables ------------------------------------------------------------------

from sqlalchemy import make_url  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from app.seed.backup import RestoreError, restore_tables  # noqa: E402
from test_seating_postgres import _alembic_at, _export  # noqa: E402

WORKFLOW_TABLES = (*TIMETABLE_TABLES, "exam_timetable_history")
APPROVE_SQL = ("UPDATE examinations.exam_timetables SET status = 'APPROVED', revision = 2, approved_by = created_by,"
               " approved_at = now() WHERE timetable_id = %s")
PUBLISH_SQL = ("UPDATE examinations.exam_timetables SET status = 'PUBLISHED', revision = 3,"
               " published_by = created_by, published_at = now() WHERE timetable_id = %s")


def _engine_for(url: str):
    return create_async_engine(make_url(url).set(drivername="postgresql+psycopg"), poolclass=NullPool)


async def _restore_into(url: str, backup: Path, **options):
    engine = _engine_for(url)
    try:
        async with engine.connect() as conn:
            try:
                result = await restore_tables(conn, backup, dry_run=False, **options)
            except BaseException:
                await conn.rollback()
                raise
            await conn.commit()
            return result
    finally:
        await engine.dispose()


async def _save_draft(url: str, **header) -> dict:
    engine = _engine_for(url)
    try:
        async with AsyncSession(engine) as session:
            store = DatabaseTimetableStore(session)
            saved = await store.save(INSTITUTION, NewTimetable(_header(**header)))
            await store.commit()
            return saved
    finally:
        await engine.dispose()


def _all_rows(url: str) -> dict[str, list[tuple]]:
    return {t: _query(url, f"SELECT * FROM examinations.{t} r ORDER BY r::text") for t in WORKFLOW_TABLES}


@pytest.fixture
def target_database():
    """Factory: another database from the template (0009 + seed data), upgraded to ``revision``."""
    names: list[str] = []

    def make(revision: str = "head") -> str:
        name = f"exam_timetables_{uuid.uuid4().hex[:10]}"
        _admin(f"CREATE DATABASE {name} TEMPLATE {TEMPLATE}")
        names.append(name)
        url = _db_url(name)
        if revision != "0009":
            _alembic_at(url, "upgrade", revision)
        return url

    yield make
    for name in names:
        _admin(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


def _workflow_history(client) -> list[dict]:
    """Draft 1 published then archived (replaced by draft 2), draft 2 published, draft 3 still a draft."""
    first = client.post("/api/v1/timetables/drafts", json=DRAFT_BODY).json()
    first = _approve(client, _approve(client, first).json()["timetable"], "publish").json()["timetable"]
    second = client.post(f"/api/v1/timetables/{first['timetable_id']}/regenerate", json={}).json()
    second = _approve(client, second).json()["timetable"]
    _approve(client, second, "publish", replace_published=True)
    third = client.post("/api/v1/timetables/drafts", json=DRAFT_BODY).json()
    return [first, second, third]


async def test_saved_timetables_round_trip_through_a_backup(timetable_engine, database: str, target_database,
                                                            tmp_path: Path) -> None:
    with _timetable_client() as client:
        _workflow_history(client)
    source = _all_rows(database)
    assert _query(database, "SELECT draft_number, status, revision, source_timetable_id IS NOT NULL FROM"
                            " examinations.exam_timetables ORDER BY draft_number") == [
        (1, "ARCHIVED", 4, False), (2, "PUBLISHED", 3, True), (3, "DRAFT", 1, False)]
    manifest = await _export(database, tmp_path / "b")
    assert manifest["alembic_revision"] == "0011"
    assert {t: manifest["tables"][t]["rows"] for t in WORKFLOW_TABLES} == {t: len(r) for t, r in source.items()}

    target = target_database()
    result, _ = await _restore_into(target, tmp_path / "b")
    assert result["exam_timetables"] == (3, 3) and result["exam_timetable_history"] == (8, 8)
    seats = len(source["exam_timetable_seats"])
    assert result["exam_timetable_seats"] == (seats, seats)
    assert _all_rows(target) == source  # same IDs, statuses, revisions, users, times, fingerprints, seats

    again, _ = await _restore_into(target, tmp_path / "b")  # safe to rerun: nothing added
    assert all(again[t][1] == 0 for t in WORKFLOW_TABLES) and _all_rows(target) == source

    # The restored rows follow the 0011 rules, and new IDs and draft numbers continue after them.
    with pytest.raises(psycopg.errors.IntegrityError):
        _query(target, "UPDATE examinations.exam_timetable_seats SET seat_number = seat_number + 1000")
    saved = await _save_draft(target)
    assert (saved["timetable_id"], saved["draft_number"]) == (max(r[0] for r in source["exam_timetables"]) + 1, 4)


async def test_restore_refuses_newer_timetable_changes_and_changes_nothing(timetable_engine, database: str,
                                                                          tmp_path: Path) -> None:
    with _timetable_client() as client:
        draft = _workflow_history(client)[2]
        await _export(database, tmp_path / "b")
        assert _approve(client, draft).status_code == 200  # newer than the backup
    before = _all_rows(database)
    with pytest.raises(RestoreError, match="differs from the backup"):
        await _restore_into(database, tmp_path / "b")
    assert _all_rows(database) == before


async def test_restore_refuses_a_second_published_timetable(timetable_engine, database: str, target_database,
                                                            tmp_path: Path) -> None:
    with _timetable_client() as client:
        _workflow_history(client)
    await _export(database, tmp_path / "b")
    target = target_database()
    # Another database with its own published timetable (another ID and draft number) for the same cycle.
    _query(target, "SELECT setval(pg_get_serial_sequence('examinations.exam_timetables', 'timetable_id'), 100)")
    for _ in range(4):
        own = await _save_draft(target)
    # Draft numbers 1-3 are used by the backup: keep only draft 4 (drafts may be deleted).
    _query(target, "DELETE FROM examinations.exam_timetables WHERE draft_number <= 3")
    assert own["draft_number"] == 4
    _query(target, APPROVE_SQL, own["timetable_id"])
    _query(target, PUBLISH_SQL, own["timetable_id"])
    before = _all_rows(target)
    with pytest.raises(RestoreError, match="already has a published timetable"):
        await _restore_into(target, tmp_path / "b")
    assert _all_rows(target) == before


async def test_old_backups_without_timetables_leave_them_alone(timetable_engine, database: str, target_database,
                                                               tmp_path: Path) -> None:
    manifest = await _export(target_database("0009"), tmp_path / "old")  # before 0010: no timetable tables
    assert not set(WORKFLOW_TABLES) & set(manifest["tables"])
    with _timetable_client() as client:
        _workflow_history(client)
    before = _all_rows(database)
    result, _ = await _restore_into(database, tmp_path / "old")
    assert not set(WORKFLOW_TABLES) & set(result) and _all_rows(database) == before
    assert all(inserted == 0 for _, inserted in result.values())


async def test_timetable_backup_needs_migration_0011_or_skip(timetable_engine, database: str, target_database,
                                                             tmp_path: Path) -> None:
    with _timetable_client() as client:
        _workflow_history(client)
    await _export(database, tmp_path / "b")
    old = target_database("0009")
    with pytest.raises(RestoreError, match="upgrade 0011"):
        await _restore_into(old, tmp_path / "b")
    result, _ = await _restore_into(old, tmp_path / "b", skip_timetables=True)
    assert not set(WORKFLOW_TABLES) & set(result)
    assert _query(old, "SELECT to_regclass('examinations.exam_timetables')") == [(None,)]
