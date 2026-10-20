"""Module examination details (Step 11b) on a real, throw-away **local** PostgreSQL (never Neon).

Opt-in, like test_seating_postgres: set ``SCHEDENCE_TEST_POSTGRES_URL`` to an admin
URL of a local server. Reuses the databases of test_exam_periods_postgres (seed
data at 0011 with a two-module FINAL cycle: IT1010 and IT1030).

Covers: updates over HTTP that change only the examination columns of one module,
dry runs on the read-only session, the cycle-mapping check, timetables that lock
details by examination kind, and row locks shared with the approval workflow.
"""

from __future__ import annotations

import pytest

psycopg = pytest.importorskip("psycopg")

from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.module_details import DatabaseModuleDetailsStore, ModuleDetailsService  # noqa: E402
from app.module_details.errors import ModuleDetailsError  # noqa: E402
from app.module_details.rules import EDITABLE_FIELDS, details_fingerprint, details_of  # noqa: E402
from test_exam_periods_postgres import (  # noqa: E402
    ADMIN_URL,
    FINAL,
    USER,
    _api_client,
    _drop_template_afterwards,  # noqa: F401  (pytest fixture)
    _query,
    _save_draft,
    database,  # noqa: F401  (pytest fixture)
    engine,  # noqa: F401  (pytest fixture)
)

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="set SCHEDENCE_TEST_POSTGRES_URL to a local PostgreSQL")
MODULES = "/api/v1/modules"
UNTOUCHED = ("seed_module_specializations", "seed_students", "seed_registrations", "seed_venues",
             "seed_exam_periods", "seed_exam_period_exclusions", "seed_exam_period_modules", "exam_timetables",
             "exam_timetable_sessions")


def _fingerprints(url: str, where: str = "true") -> dict[str, tuple]:
    tables = {t: f"SELECT count(*), md5(coalesce(string_agg(t::text, '|' ORDER BY t::text), ''))"
                 f" FROM examinations.{t} t" for t in UNTOUCHED}
    tables["other modules"] = ("SELECT count(*), md5(string_agg(t::text, '|' ORDER BY t::text))"
                               f" FROM examinations.seed_modules t WHERE {where}")
    return {name: tuple(_query(url, sql)[0]) for name, sql in tables.items()}


def _module_row(url: str, code: str = "IT1010") -> tuple:
    return _query(url, "SELECT module_code, module_name, academic_year, semester, credits, exam_type,"
                       " duration_minutes, reading_time_minutes, has_extra_reading_time, difficulty, has_final_exam,"
                       " has_mid_exam, mid_duration_minutes, delivery_mode, block_category"
                       " FROM examinations.seed_modules WHERE module_code = %s", code)[0]


def _put(client, code="IT1010", params=None, **changes):
    current = client.get(f"{MODULES}/{code}").json()
    body = {**{k: current[k] for k in EDITABLE_FIELDS}, "expected_fingerprint": current["fingerprint"], **changes}
    return client.put(f"{MODULES}/{code}/exam-details", params=params, json=body)


def test_update_over_http_changes_only_the_exam_columns_of_one_module(database: str) -> None:
    before = _fingerprints(database, "module_code <> 'IT1010'")
    old = _module_row(database)
    with _api_client() as client:
        response = _put(client, exam_type="written", duration_minutes=90, reading_time_minutes=0, difficulty=2,
                        has_mid_exam=True, mid_duration_minutes=45, delivery_mode="block", block_category="pro_rata")
        assert response.status_code == 200, response.text
        assert client.get(f"{MODULES}/IT1010").json() == response.json()["module"]
        stale = client.put(f"{MODULES}/IT1010/exam-details", json={**{k: response.json()["module"][k]
                                                                       for k in EDITABLE_FIELDS},
                                                                    "expected_fingerprint": "0" * 64})
        assert stale.status_code == 409 and stale.json()["detail"][0]["rule"] == "VERSION"
    assert _module_row(database) == (*old[:5], "written", 90, 0, False, 2, True, True, 45, "block", "pro_rata")
    assert _fingerprints(database, "module_code <> 'IT1010'") == before


def test_dry_runs_and_rejected_changes_write_nothing(database: str) -> None:
    before = _fingerprints(database), _module_row(database)
    with _api_client() as client:
        preview = _put(client, params={"dry_run": True}, difficulty=5)
        assert preview.status_code == 200 and preview.json()["module"]["difficulty"] == 5
        broken = _put(client, has_final_exam=False, has_mid_exam=True, mid_duration_minutes=60)
        assert broken.status_code == 422 and broken.json()["detail"][0]["rule"] == "CYCLE_MAPPING"
        invalid = _put(client, delivery_mode="block", block_category=None)
        assert invalid.status_code == 422
        body = {**{k: preview.json()["module"][k] for k in EDITABLE_FIELDS}, "expected_fingerprint": "0" * 64}
        assert client.put(f"{MODULES}/IT9999/exam-details", json=body).status_code == 404
    assert (_fingerprints(database), _module_row(database)) == before


async def test_saved_timetables_warn_or_lock_by_examination_kind(engine, database: str) -> None:
    await _save_draft(engine)  # a FINAL draft with a session of IT1010
    with _api_client() as client:
        changed = _put(client, difficulty=2)
        assert changed.status_code == 200 and changed.json()["draft_timetables"] == 1, changed.text
        assert changed.json()["notices"]

    await _save_draft(engine, approve=True)
    with _api_client() as client:
        refused = _put(client, duration_minutes=90)
        assert refused.status_code == 409 and refused.json()["detail"][0]["rule"] == "MODULE_LOCKED"
        assert "FINAL APPROVED timetable" in refused.text
        assert _module_row(database)[6] == 60
        mid = _put(client, has_mid_exam=True, mid_duration_minutes=60)  # the FINAL timetable does not use it
        assert mid.status_code == 200, mid.text
    assert _query(database, "SELECT status FROM examinations.exam_timetables ORDER BY timetable_id") == [
        ("DRAFT",), ("APPROVED",)]  # saved timetables are never changed


@pytest.mark.parametrize("held", [
    "SELECT 1 FROM examinations.seed_modules WHERE module_code = 'IT1010' FOR UPDATE",
    # An approval in progress holds its timetable row (app.saved_timetables.workflow).
    f"SELECT 1 FROM examinations.exam_timetables WHERE period_code = '{FINAL}' FOR UPDATE",
])
async def test_a_locked_module_or_timetable_makes_the_update_busy(engine, database: str, held: str) -> None:
    await _save_draft(engine)
    async with AsyncSession(engine) as session:
        current = details_of(await DatabaseModuleDetailsStore(session).get("IT1010"))
    with psycopg.connect(database) as other:
        other.execute(held)  # the transaction stays open until the block ends
        async with AsyncSession(engine) as session:
            service = ModuleDetailsService(DatabaseModuleDetailsStore(session, lock_timeout_ms=200))
            with pytest.raises(ModuleDetailsError) as refused:
                await service.update("IT1010", current.__class__(**{**current.__dict__, "difficulty": 4}),
                                     expected_fingerprint=details_fingerprint("IT1010", current), user_id=USER)
            assert refused.value.status_code == 409 and refused.value.rule == "BUSY"
    assert _module_row(database)[9] == current.difficulty
