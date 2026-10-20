"""Both-semester persistence on a fresh local-only database per test. Never uses repository .env."""
import pytest

from app.generation.service import TimetableGenerator
from app.saved_timetables import DatabaseTimetableStore
from test_cross_timetable_postgres import S1, S2, cross_database  # noqa: F401
from test_seating_postgres import ADMIN_URL, _query
from test_timetables_postgres import _approve, _timetable_client

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="set SCHEDENCE_TEST_POSTGRES_URL to local PostgreSQL")
GENERATE = "/api/v1/timetables/generate/both-semesters"
SAVE = "/api/v1/timetables/drafts/both-semesters"
BODY = {"cycles": [{"period_code": S1}, {"period_code": S2}], "time_limit_seconds": 5,
        "scheduling_preferences": {"scheduling": {"minimum_break_minutes": 120}, "seating": {"default_percentage": 75}}}


@pytest.mark.parametrize("cross_database", ["rooms", "repeat"], indirect=True)
async def test_both_drafts_are_atomic_and_independently_approved_published(cross_database, monkeypatch):
    case = cross_database
    snapshots = {}
    differences = []
    original_generate = TimetableGenerator.generate_both
    original_inputs = TimetableGenerator.current_inputs

    async def capture(self, requests):
        result = await original_generate(self, requests)
        snapshots.update({o.request.period_code: o.snapshot for o in result.outcomes})
        return result

    async def compare(self, period_code, *args, **kwargs):
        result = await original_inputs(self, period_code, *args, **kwargs)
        before, after = snapshots.get(period_code), result[2]
        if before is not None and before.fingerprint != after.fingerprint:
            differences.append((period_code, [key for key in before.__dataclass_fields__
                                             if key not in ("protected", "fingerprint") and getattr(before, key) != getattr(after, key)]))
        return result

    monkeypatch.setattr(TimetableGenerator, "generate_both", capture)
    monkeypatch.setattr(TimetableGenerator, "current_inputs", compare)
    with _timetable_client() as client:
        generated = client.post(GENERATE, json=BODY)
        assert generated.status_code == 200, generated.text
        assert generated.json()["generated"] and generated.json()["pair_validation"]["passed"]
        assert _query(case.url, "SELECT count(*) FROM examinations.exam_timetables") == [(0,)]
        response = client.post(SAVE, json=BODY)
        assert response.status_code == 201, response.text
        rows = response.json()["timetables"]
        assert len(rows) == 2 and all(t["status"] == "DRAFT" for t in rows)
        assert _query(case.url, "SELECT count(*) FROM examinations.exam_timetable_history") == [(2,)]
        assert _query(case.url, "SELECT count(DISTINCT inputs->'batch'->>'batch_id') FROM examinations.exam_timetables") == [(1,)]
        assert rows[0]["inputs"]["scheduling_preferences"] == rows[1]["inputs"]["scheduling_preferences"]
        for row in rows:
            approved = _approve(client, row)
            assert approved.status_code == 200, (row["period_code"], approved.text, differences)
            published = _approve(client, approved.json()["timetable"], "publish")
            assert published.status_code == 200, published.text
        assert _query(case.url, "SELECT status FROM examinations.exam_timetables ORDER BY timetable_id") == [("PUBLISHED",), ("PUBLISHED",)]
        assert differences == []  # JSONB round trips must retain the original policy/input identity.


async def test_second_insert_failure_rolls_back_both_headers_allocations_and_history(cross_database, monkeypatch):
    case = cross_database
    original = DatabaseTimetableStore.save
    calls = 0

    async def failing(self, institution, timetable):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("Isolated second insert failure")
        return await original(self, institution, timetable)

    monkeypatch.setattr(DatabaseTimetableStore, "save", failing)
    with _timetable_client() as client, pytest.raises(RuntimeError, match="Isolated second insert failure"):
        client.post(SAVE, json=BODY)
    for table in ("exam_timetables", "exam_timetable_sessions", "exam_timetable_rooms", "exam_timetable_seats", "exam_timetable_history"):
        assert _query(case.url, f"SELECT count(*) FROM examinations.{table}") == [(0,)]


async def test_current_postgres_inputs_are_checked_before_saving_either_cycle(cross_database, monkeypatch):
    case = cross_database
    original = TimetableGenerator.generate_both

    async def changed(self, requests):
        result = await original(self, requests)
        _query(case.url, "UPDATE examinations.seed_modules SET module_name = 'Changed in isolated test' WHERE module_code = 'IT2001'")
        return result

    monkeypatch.setattr(TimetableGenerator, "generate_both", changed)
    with _timetable_client() as client:
        response = client.post(SAVE, json=BODY)
        assert response.status_code == 409, response.text
        assert "STALE_INPUTS" in response.text
    assert _query(case.url, "SELECT count(*) FROM examinations.exam_timetables") == [(0,)]
