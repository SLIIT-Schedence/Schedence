"""Exam period management (Step 11a) on a real, throw-away **local** PostgreSQL (never Neon).

Opt-in, like test_seating_postgres: set ``SCHEDENCE_TEST_POSTGRES_URL`` to an admin
URL of a local server. The tests create and drop their own databases
(``exam_periods_*``); nothing else is touched. The template is seeded at 0009,
migrated to 0011 and given a two-module FINAL cycle mapping.

Covers: create/update over HTTP, dry runs on the read-only session, data that
must never change (modules, students, registrations, venues, cycle mappings),
periods locked by released timetables, row locks shared with the approval
workflow, and the cycle-mapping check.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import uuid
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app.exam_periods import DatabaseExamPeriodStore, ExamPeriodService  # noqa: E402
from app.exam_periods.errors import ExamPeriodError  # noqa: E402
from app.exam_periods.rules import build_period, period_fingerprint  # noqa: E402
from app.saved_timetables import DatabaseTimetableStore, NewTimetable  # noqa: E402
from auth_support import INSTITUTION  # noqa: E402
from schedence_db import create_db_engine, dispose_engine, load_settings  # noqa: E402
from test_seating_postgres import (  # noqa: E402
    ADMIN_URL,
    USER,
    _admin,
    _alembic,
    _api_client,
    _db_url,
    _query,
    _seed,
    _use_database,
)

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="set SCHEDENCE_TEST_POSTGRES_URL to a local PostgreSQL")
TEMPLATE = "exam_periods_template"
FINAL = "PP1-2026-S2-FINAL"
PERIODS = "/api/v1/exam-periods"
# Tables a period change must never touch.
UNTOUCHED = ("seed_modules", "seed_module_specializations", "seed_students", "seed_registrations", "seed_venues",
             "seed_venue_blocks", "seed_exam_period_modules", "exam_seating_rules", "exam_timetables")
_template_ready = False


@pytest.fixture(scope="module", autouse=True)
def _drop_template_afterwards():
    global _template_ready
    yield
    if _template_ready:  # also used by test_module_details_postgres: rebuilt there when needed
        _admin(f"DROP DATABASE IF EXISTS {TEMPLATE} WITH (FORCE)")
        _template_ready = False


@pytest.fixture
def database(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> str:
    """A fresh database at revision 0011 with the seed dataset and a two-module FINAL cycle."""
    global _template_ready
    if not _template_ready:
        _admin(f"DROP DATABASE IF EXISTS {TEMPLATE}")
        _admin(f"CREATE DATABASE {TEMPLATE}")
        _query(_db_url(TEMPLATE), "CREATE SCHEMA examinations")
        url = _use_database(monkeypatch, tmp_path, TEMPLATE)
        _alembic("upgrade", "0009")
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:  # psycopg async needs it on Windows
            runner.run(_seed())
        _alembic("upgrade", "0011")
        _query(url, "INSERT INTO examinations.seed_exam_period_modules (period_code, module_code, candidate_scope)"
                    f" VALUES ('{FINAL}', 'IT1010', 'all'), ('{FINAL}', 'IT1030', 'all')")
        _template_ready = True
    name = f"exam_periods_{uuid.uuid4().hex[:10]}"
    _admin(f"CREATE DATABASE {name} TEMPLATE {TEMPLATE}")
    url = _use_database(monkeypatch, tmp_path, name)
    yield url
    _admin(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


@pytest.fixture
async def engine(database: str):
    engine = create_db_engine(load_settings(), poolclass=NullPool)
    yield engine
    await dispose_engine(engine)


def _untouched(url: str) -> dict[str, tuple]:
    return {t: tuple(_query(url, f"SELECT count(*), md5(coalesce(string_agg(t::text, '|' ORDER BY t::text), ''))"
                                 f" FROM examinations.{t} t")[0]) for t in UNTOUCHED}


def _periods(url: str) -> list[tuple]:
    return _query(url, "SELECT p.*, (SELECT array_agg(excluded_date || ' ' || reason ORDER BY excluded_date)"
                       " FROM examinations.seed_exam_period_exclusions e WHERE e.period_code = p.period_code)"
                       " FROM examinations.seed_exam_periods p ORDER BY period_code")


MID_BODY = {
    "period_code": "PP1-2027-S1-MID", "period_name": "Semester 1 Mid Examinations",
    "academic_year_label": "2026/2027", "semester": 1, "exam_kind": "MID",
    "start_date": "2027-03-01", "end_date": "2027-03-12", "day_start_time": "08:30", "day_end_time": "16:30",
    "include_saturdays": False, "include_sundays": False,
    "exclusions": [{"excluded_date": "2027-03-05", "reason": "Public holiday"}],
}


def _update_body(client, code=FINAL, **changes) -> dict:
    current = client.get(f"{PERIODS}/{code}").json()
    body = {k: current[k] for k in ("period_name", "academic_year_label", "semester", "exam_kind", "start_date",
                                    "end_date", "day_start_time", "day_end_time", "include_saturdays",
                                    "include_sundays", "exclusions")}
    return {**body, "expected_fingerprint": current["fingerprint"], **changes}


def test_create_and_update_over_http_on_postgres(database: str) -> None:
    before = _untouched(database)
    with _api_client() as client:
        created = client.post(PERIODS, json=MID_BODY)
        assert created.status_code == 201, created.text
        listed = client.get(PERIODS).json()
        assert [p["period_code"] for p in listed] == ["PP1-2026-S2-FINAL", "PP1-2027-S1-MID"]
        assert listed[1] == created.json()["period"] and len(listed[1]["exam_days"]) == 9
        assert client.get(f"{PERIODS}/PP1-2027-S1-MID/modules").json() == []  # no mapping is created
        assert client.post(PERIODS, json=MID_BODY).status_code == 409

        body = _update_body(client, end_date="2026-11-27", include_saturdays=True,
                            exclusions=[{"excluded_date": "2026-11-20", "reason": "Graduation"}])
        updated = client.put(f"{PERIODS}/{FINAL}", json=body)
        assert updated.status_code == 200, updated.text
        assert updated.json()["changed_fields"] == ["end_date", "include_saturdays", "exclusions"]
        assert client.get(f"{PERIODS}/{FINAL}").json() == updated.json()["period"]
        stale = client.put(f"{PERIODS}/{FINAL}", json=body)
        assert stale.status_code == 409 and stale.json()["detail"][0]["rule"] == "VERSION"

    final, mid = _periods(database)
    assert final[0] == FINAL and final[5] == dt.date(2026, 11, 27) and final[8] is True
    assert final[-1] == ["2026-11-20 Graduation"]  # the old 24 Nov exclusion was replaced
    assert mid[0] == "PP1-2027-S1-MID" and mid[-2] == "MID" and mid[7] == dt.time(16, 30)
    assert _untouched(database) == before


def test_dry_runs_and_rejected_changes_write_nothing(database: str) -> None:
    before = _periods(database), _untouched(database)
    with _api_client() as client:
        preview = client.post(PERIODS, params={"dry_run": True}, json=MID_BODY)
        assert preview.status_code == 201 and preview.json()["dry_run"] is True
        body = _update_body(client, end_date="2026-11-27")
        preview = client.put(f"{PERIODS}/{FINAL}", params={"dry_run": True}, json=body)
        assert preview.status_code == 200 and preview.json()["changed_fields"] == ["end_date"]
        bad = client.put(f"{PERIODS}/{FINAL}", json={**body, "day_end_time": "08:00"})
        assert bad.status_code == 422
        kind = client.put(f"{PERIODS}/{FINAL}", json=_update_body(client, exam_kind="MID"))
        assert kind.status_code == 422 and {p["rule"] for p in kind.json()["detail"]} == {"CYCLE_MAPPING"}
    assert (_periods(database), _untouched(database)) == before


def _one_seat_timetable() -> NewTimetable:
    header = dict(period_code=FINAL, period_name="Test cycle", exam_kind="FINAL", mode="feasibility",
                  selection="listed_modules", module_codes=None, solver_status="FEASIBLE", solver_version=None,
                  solver_run={}, input_fingerprint="a" * 64, settings_version=0, settings_fingerprint="b" * 64,
                  seating_ruleset_version=1, capacity_fingerprint="c" * 64, validation_passed=True,
                  validation={}, inputs={}, summary={}, workload=None, optimization=None,
                  excluded_registrations={}, notices=[], created_by=USER)
    session = dict(session_id="S1", module_code="IT1010", module_name="M", exam_type="written",
                   candidate_scope="all", academic_year=1, semester=2, delivery_mode="regular", session_number=1,
                   session_count=1, starts_at=dt.datetime(2026, 11, 16, 9), ends_at=dt.datetime(2026, 11, 16, 11),
                   duration_minutes=120, reading_time_minutes=0, candidates=1, normal_candidates=1,
                   repeat_candidates=0)
    room = dict(session_id="S1", venue_id="F303", venue_name="F303", venue_type="lecture_hall", seats=1,
                exam_capacity=10)
    seat = dict(session_id="S1", venue_id="F303", seat_number=1, student_id="IT10000001", registration_type="normal")
    return NewTimetable(header, [session], [room], [seat])


async def _save_draft(engine, *, approve: bool = False) -> int:
    async with AsyncSession(engine) as session:
        store = DatabaseTimetableStore(session)
        saved = await store.save(INSTITUTION, _one_seat_timetable())
        if approve:
            await store.change_status(INSTITUTION, saved["timetable_id"], from_status="DRAFT", to_status="APPROVED",
                                      expected_revision=1, user_id=USER, reason="Test approval", details={})
        await store.commit()
    return saved["timetable_id"]


async def test_drafts_are_reported_and_an_approved_timetable_locks_the_calendar(engine, database: str) -> None:
    await _save_draft(engine)
    with _api_client() as client:
        changed = client.put(f"{PERIODS}/{FINAL}", json=_update_body(client, day_end_time="17:00"))
        assert changed.status_code == 200 and changed.json()["draft_timetables"] == 1, changed.text
        assert changed.json()["notices"]

    await _save_draft(engine, approve=True)
    before = _periods(database)
    with _api_client() as client:
        refused = client.put(f"{PERIODS}/{FINAL}", json=_update_body(client, end_date="2026-11-27"))
        assert refused.status_code == 409 and refused.json()["detail"][0]["rule"] == "PERIOD_LOCKED"
        assert "APPROVED" in refused.text
        assert _periods(database) == before
        renamed = client.put(f"{PERIODS}/{FINAL}", json=_update_body(client, period_name="Renamed final exams"))
        assert renamed.status_code == 200 and renamed.json()["changed_fields"] == ["period_name"]
    assert _query(database, "SELECT status FROM examinations.exam_timetables ORDER BY timetable_id") == [
        ("DRAFT",), ("APPROVED",)]  # saved timetables are never changed


def _final(**changes):
    values = dict(period_code=FINAL, period_name="Synthetic Semester 2 Final Examinations (PP1)",
                  academic_year_label="2026/2027", semester=2, exam_kind="FINAL", start_date=dt.date(2026, 11, 16),
                  end_date=dt.date(2026, 11, 28), day_start_time=dt.time(8, 30), day_end_time=dt.time(17, 30),
                  include_saturdays=False, include_sundays=False)
    return build_period(**{**values, **changes})


@pytest.mark.parametrize("held", [
    f"SELECT 1 FROM examinations.seed_exam_periods WHERE period_code = '{FINAL}' FOR UPDATE",
    # An approval in progress holds its timetable row (app.saved_timetables.workflow).
    f"SELECT 1 FROM examinations.exam_timetables WHERE period_code = '{FINAL}' FOR UPDATE",
])
async def test_a_locked_period_or_timetable_makes_the_update_wait_then_busy(engine, database: str,
                                                                            held: str) -> None:
    await _save_draft(engine)
    async with AsyncSession(engine) as session:
        current = await DatabaseExamPeriodStore(session).get(FINAL)
    with psycopg.connect(database) as other:
        other.execute(held)  # the transaction stays open until the block ends
        async with AsyncSession(engine) as session:
            service = ExamPeriodService(DatabaseExamPeriodStore(session, lock_timeout_ms=200))
            with pytest.raises(ExamPeriodError) as refused:
                await service.update(_final(end_date=dt.date(2026, 11, 27), exclusions=[]),
                                     expected_fingerprint=period_fingerprint(current), user_id=USER)
            assert refused.value.status_code == 409 and refused.value.rule == "BUSY"
    assert _query(database, f"SELECT end_date FROM examinations.seed_exam_periods WHERE period_code = '{FINAL}'") == [
        (dt.date(2026, 11, 28),)]


async def test_insert_of_an_existing_code_changes_nothing(engine, database: str) -> None:
    before = _periods(database)
    async with AsyncSession(engine) as session:
        store = DatabaseExamPeriodStore(session)
        assert await store.insert(_final(period_name="Duplicate")) is False
        await store.commit()
    assert _periods(database) == before
