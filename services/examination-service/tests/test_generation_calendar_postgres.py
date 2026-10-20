"""Selected exam calendars on a real, throw-away **local** PostgreSQL (never Neon).

Opt-in like test_timetables_postgres (SCHEDENCE_TEST_POSTGRES_URL). A draft saved with a selected calendar
keeps it in the existing ``inputs`` JSONB column (no migration); approval and publication re-check against
that calendar, and the saved period row is not changed.
"""

from __future__ import annotations

import datetime as dt

import pytest

pytest.importorskip("psycopg")
pytest.importorskip("ortools")

from test_timetables_postgres import (  # noqa: E402
    ADMIN_URL,
    DRAFT_BODY,
    _approve,
    _drop_template_afterwards,  # noqa: F401  (pytest fixture)
    _query,
    _timetable_client,
    database,  # noqa: F401  (pytest fixture)
    timetable_engine,  # noqa: F401  (pytest fixture)
)

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="set SCHEDENCE_TEST_POSTGRES_URL to a local PostgreSQL")
CALENDAR = {"start_date": "2026-11-30", "end_date": "2026-12-11", "day_start_time": "08:30", "day_end_time": "17:30",
            "include_saturdays": False, "include_sundays": False,
            "exclusions": [{"excluded_date": "2026-12-03", "reason": "University holiday"}]}


async def test_selected_calendar_draft_is_approved_and_published_on_postgres(timetable_engine, database: str) -> None:
    period_before = _query(database, "SELECT * FROM examinations.seed_exam_periods ORDER BY period_code")
    with _timetable_client() as client:
        saved = client.post("/api/v1/timetables/drafts", json={**DRAFT_BODY, "calendar": CALENDAR})
        assert saved.status_code == 201, saved.text
        draft = saved.json()
        assert draft["inputs"]["calendar_source"] == "selected"
        assert {s["date"] for s in draft["sessions"]} <= {d for d in draft["inputs"]["exam_days"]}
        assert all("2026-11-30" <= s["date"] <= "2026-12-11" and s["date"] != "2026-12-03" for s in draft["sessions"])
        approved = _approve(client, draft)
        assert approved.status_code == 200, approved.text
        published = _approve(client, approved.json()["timetable"], "publish")
        assert published.status_code == 200, published.text
    stored = _query(database, "SELECT inputs->>'calendar_source', inputs->>'start_date', inputs->'exclusions'"
                              " FROM examinations.exam_timetables")
    assert stored == [("selected", "2026-11-30", [{"excluded_date": "2026-12-03", "reason": "University holiday"}])]
    starts = _query(database, "SELECT min(starts_at), max(starts_at) FROM examinations.exam_timetable_sessions")[0]
    assert starts[0] >= dt.datetime(2026, 11, 30) and starts[1] < dt.datetime(2026, 12, 12)
    assert _query(database, "SELECT * FROM examinations.seed_exam_periods ORDER BY period_code") == period_before
