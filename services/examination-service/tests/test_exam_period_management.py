"""Exam period management API (Step 11a): RBAC, validation, dry run, fingerprints and safety rules.

Real RS256 tokens and a fake Identity Service (as in test_seating_api.py); the
periods live in an in-memory store with real transaction behaviour. The same
flows run against PostgreSQL in test_exam_periods_postgres.py.
"""

from __future__ import annotations

import asyncio
import dataclasses
import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_exam_period_writer, get_source
from app.exam_periods import ExamPeriodService, MemoryExamPeriodStore
from app.exam_periods.rules import build_period, period_fingerprint, period_problems
from app.main import create_app
from app.seed.generator import generate_dataset
from app.seed.records import ExamPeriodModule
from app.sources import DatasetSource
from auth_support import INSTITUTION, ROLE_PERMISSIONS, FakeIdentityService, make_auth_context, own_dataset
from schedence_auth.testing import TokenFactory

PERIODS = "/api/v1/exam-periods"
FINAL = "PP1-2026-S2-FINAL"
DATA = generate_dataset()
# Two modules in the FINAL cycle mapping, so cycle checks have something to check.
DATA = dataclasses.replace(DATA, exam_period_modules=(ExamPeriodModule(FINAL, "IT1010"),
                                                      ExamPeriodModule(FINAL, "IT1030")))
SOURCE = DatasetSource(DATA)


def _mid_body(**changes) -> dict:
    body = {
        "period_code": "PP1-2027-S1-MID", "period_name": "Semester 1 Mid Examinations",
        "academic_year_label": "2026/2027", "semester": 1, "exam_kind": "MID",
        "start_date": "2027-03-01", "end_date": "2027-03-12", "day_start_time": "08:30", "day_end_time": "17:30",
        "include_saturdays": False, "include_sundays": False,
        "exclusions": [{"excluded_date": "2027-03-05", "reason": "Public holiday"}],
    }
    return {**body, **changes}


@pytest.fixture
def tokens() -> TokenFactory:
    return TokenFactory()


@pytest.fixture
def identity() -> FakeIdentityService:
    return FakeIdentityService()


@pytest.fixture
def store() -> MemoryExamPeriodStore:
    return MemoryExamPeriodStore(DATA)


@pytest.fixture
def client(tokens, identity, store) -> TestClient:
    app = own_dataset(create_app(make_auth_context(tokens, identity)))
    app.dependency_overrides[get_source] = lambda: SOURCE
    app.dependency_overrides[get_exam_period_writer] = lambda: ExamPeriodService(store)
    return TestClient(app)


def _headers(tokens, identity, permissions: list[str]) -> dict[str, str]:
    sub = identity.register({INSTITUTION: (["EXAMINATION_ADMIN"], permissions)})
    return {"Authorization": f"Bearer {tokens.access_token(sub)}"}


@pytest.fixture
def admin(tokens, identity) -> dict[str, str]:
    return _headers(tokens, identity, ROLE_PERMISSIONS["EXAMINATION_ADMIN"])


def _update_body(client, admin, code=FINAL, **changes) -> dict:
    current = client.get(f"{PERIODS}/{code}", headers=admin).json()
    body = {k: current[k] for k in ("period_name", "academic_year_label", "semester", "exam_kind", "start_date",
                                    "end_date", "day_start_time", "day_end_time", "include_saturdays",
                                    "include_sundays", "exclusions")}
    return {**body, "expected_fingerprint": current["fingerprint"], **changes}


def _rules(response) -> list[str]:
    return [p["rule"] for p in response.json()["detail"]]


# --- Reading stays backward-compatible --------------------------------------------------------


def test_read_endpoints_keep_their_fields_and_add_a_fingerprint(client, admin) -> None:
    [period] = client.get(PERIODS, headers=admin).json()
    assert {"period_code", "period_name", "academic_year_label", "semester", "exam_kind", "start_date", "end_date",
            "day_start_time", "day_end_time", "include_saturdays", "include_sundays", "exclusions",
            "exam_days"} < set(period)
    assert period["period_code"] == FINAL and len(period["fingerprint"]) == 64
    assert client.get(f"{PERIODS}/{FINAL}", headers=admin).json() == period
    assert [m["module_code"] for m in client.get(f"{PERIODS}/{FINAL}/modules", headers=admin).json()] == [
        "IT1010", "IT1030"]


# --- RBAC -------------------------------------------------------------------------------------


def test_writes_need_a_token_and_examinations_manage(client, tokens, identity) -> None:
    assert client.post(PERIODS, json=_mid_body()).status_code == 401
    read_only = _headers(tokens, identity, ["examinations:read", "examinations:generate", "examinations:approve"])
    assert client.post(PERIODS, headers=read_only, json=_mid_body()).status_code == 403
    assert client.put(f"{PERIODS}/{FINAL}", headers=read_only, json={}).status_code == 403
    manage_only = _headers(tokens, identity, ["examinations:manage"])
    assert client.get(PERIODS, headers=manage_only).status_code == 403  # reads still need examinations:read
    assert client.post(PERIODS, headers=manage_only, json=_mid_body()).status_code == 201


def test_manage_in_another_institution_is_not_enough(client, tokens, identity) -> None:
    import uuid

    sub = identity.register({uuid.uuid4(): (["EXAMINATION_ADMIN"], ROLE_PERMISSIONS["EXAMINATION_ADMIN"])})
    headers = {"Authorization": f"Bearer {tokens.access_token(sub)}"}
    assert client.post(PERIODS, headers=headers, json=_mid_body()).status_code == 403


# --- Create -----------------------------------------------------------------------------------


def test_create_a_mid_period(client, admin, store) -> None:
    response = client.post(PERIODS, headers=admin, json=_mid_body())
    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["action"], body["changed"], body["dry_run"]) == ("create", True, False)
    period = body["period"]
    assert period["exam_kind"] == "MID" and period["day_start_time"] == "08:30:00"
    # 1-12 Mar 2027: ten weekdays, minus the excluded Friday 5 Mar.
    assert len(period["exam_days"]) == 9 and "2027-03-05" not in period["exam_days"]
    assert period["fingerprint"] == period_fingerprint(store.committed()["PP1-2027-S1-MID"])
    assert set(store.committed()) == {FINAL, "PP1-2027-S1-MID"}  # the FINAL period is untouched

    again = client.post(PERIODS, headers=admin, json=_mid_body())
    assert again.status_code == 409 and _rules(again) == ["PERIOD_EXISTS"]


def test_create_dry_run_saves_nothing(client, admin, store) -> None:
    response = client.post(PERIODS, headers=admin, params={"dry_run": True}, json=_mid_body())
    assert response.status_code == 201 and response.json()["dry_run"] is True
    assert set(store.committed()) == {FINAL} and store.commits == 0


def test_every_calendar_problem_is_listed(client, admin, store) -> None:
    response = client.post(PERIODS, headers=admin, json=_mid_body(
        academic_year_label="2026/2028", start_date="2027-03-12", end_date="2027-03-01",
        day_start_time="17:30", day_end_time="08:30",
        exclusions=[{"excluded_date": "2027-04-01", "reason": "Outside"}]))
    assert response.status_code == 422
    assert set(_rules(response)) == {"PERIOD_YEAR", "PERIOD_DATES", "PERIOD_HOURS", "PERIOD_EXCLUSIONS"}
    assert store.commits == 0


@pytest.mark.parametrize("changes, rule", [
    ({"day_start_time": "08:30:15"}, "PERIOD_HOURS"),
    ({"start_date": "2027-01-01", "end_date": "2027-06-30"}, "PERIOD_DATES"),  # longer than 120 days
    ({"start_date": "2028-03-01", "end_date": "2028-03-03"}, "PERIOD_YEAR"),  # outside 2026/2027
    ({"exclusions": [{"excluded_date": "2027-03-06", "reason": "Saturday"}]}, "PERIOD_EXCLUSIONS"),
    ({"exclusions": [{"excluded_date": "2027-03-05", "reason": "One"},
                     {"excluded_date": "2027-03-05", "reason": "Two"}]}, "PERIOD_EXCLUSIONS"),
    ({"start_date": "2027-03-06", "end_date": "2027-03-07"}, "PERIOD_CALENDAR"),  # a weekend only
])
def test_calendar_rules(client, admin, changes, rule) -> None:
    response = client.post(PERIODS, headers=admin, json=_mid_body(**changes))
    assert response.status_code == 422 and rule in _rules(response), response.text


def test_weekends_can_be_exam_days(client, admin) -> None:
    response = client.post(PERIODS, headers=admin, json=_mid_body(
        include_saturdays=True, exclusions=[{"excluded_date": "2027-03-06", "reason": "Saturday holiday"}]))
    assert response.status_code == 201, response.text
    days = response.json()["period"]["exam_days"]
    assert "2027-03-06" not in days and "2027-03-13" not in days and len(days) == 10


@pytest.mark.parametrize("changes", [
    {"period_code": "pp1-mid"}, {"period_code": "-MID"}, {"period_code": "A--B"}, {"period_code": "A" * 33},
    {"exam_kind": "RESIT"}, {"semester": 3}, {"semester": "1"}, {"include_saturdays": "no"},
    {"period_name": " padded "}, {"start_date": "01/03/2027"}, {"unknown": 1},
])
def test_malformed_input_is_rejected_before_any_check(client, admin, store, changes) -> None:
    assert client.post(PERIODS, headers=admin, json=_mid_body(**changes)).status_code == 422
    assert store.commits == 0


def test_exclusions_are_required(client, admin) -> None:
    body = _mid_body()
    del body["exclusions"]
    assert client.post(PERIODS, headers=admin, json=body).status_code == 422


# --- Update -----------------------------------------------------------------------------------


def test_update_dates_hours_weekends_and_exclusions(client, admin, store) -> None:
    body = _update_body(client, admin, end_date="2026-11-30", day_end_time="16:30", include_saturdays=True,
                        exclusions=[{"excluded_date": "2026-11-25", "reason": "Graduation"}])
    response = client.put(f"{PERIODS}/{FINAL}", headers=admin, json=body)
    assert response.status_code == 200, response.text
    out = response.json()
    assert out["changed_fields"] == ["end_date", "day_end_time", "include_saturdays", "exclusions"]
    period = store.committed()[FINAL]
    assert period.end_date == dt.date(2026, 11, 30) and period.day_end_time == dt.time(16, 30)
    assert [e.excluded_date for e in period.exclusions] == [dt.date(2026, 11, 25)]
    assert dt.date(2026, 11, 21) in period.exam_days and dt.date(2026, 11, 24) in period.exam_days
    assert out["period"]["fingerprint"] == period_fingerprint(period) != body["expected_fingerprint"]


def test_update_needs_the_current_fingerprint(client, admin, store) -> None:
    body = _update_body(client, admin, period_name="Renamed final examinations")
    assert client.put(f"{PERIODS}/{FINAL}", headers=admin, json=body).status_code == 200
    stale = client.put(f"{PERIODS}/{FINAL}", headers=admin, json={**body, "period_name": "Second rename"})
    assert stale.status_code == 409 and _rules(stale) == ["VERSION"]
    assert store.committed()[FINAL].period_name == "Renamed final examinations"


def test_unchanged_update_and_dry_run_write_nothing(client, admin, store) -> None:
    body = _update_body(client, admin)
    same = client.put(f"{PERIODS}/{FINAL}", headers=admin, json=body).json()
    assert same["changed"] is False and same["changed_fields"] == []
    preview = client.put(f"{PERIODS}/{FINAL}", headers=admin, params={"dry_run": True},
                         json={**body, "end_date": "2026-11-27"}).json()
    assert preview["dry_run"] is True and preview["changed_fields"] == ["end_date"]
    assert preview["period"]["end_date"] == "2026-11-27"
    assert store.commits == 0 and store.committed()[FINAL].end_date == dt.date(2026, 11, 28)


def test_update_unknown_period_is_404(client, admin) -> None:
    body = _update_body(client, admin)
    response = client.put(f"{PERIODS}/NO-SUCH-PERIOD", headers=admin, json=body)
    assert response.status_code == 404 and _rules(response) == ["NOT_FOUND"]


def test_update_validates_like_create(client, admin) -> None:
    body = _update_body(client, admin, exclusions=[{"excluded_date": "2026-12-01", "reason": "After the end"}])
    response = client.put(f"{PERIODS}/{FINAL}", headers=admin, json=body)
    assert response.status_code == 422 and _rules(response) == ["PERIOD_EXCLUSIONS"]


@pytest.mark.parametrize("status", ["APPROVED", "PUBLISHED", "ARCHIVED"])
def test_released_timetables_lock_the_calendar(client, admin, store, status) -> None:
    store.timetables[FINAL] = ["DRAFT", status]
    body = _update_body(client, admin)
    refused = client.put(f"{PERIODS}/{FINAL}", headers=admin, json={**body, "end_date": "2026-11-27",
                                                                      "period_name": "New name"})
    assert refused.status_code == 409 and _rules(refused) == ["PERIOD_LOCKED", "PERIOD_LOCKED"]
    assert status in refused.json()["detail"][0]["message"] and "end_date" in refused.text
    renamed = client.put(f"{PERIODS}/{FINAL}", headers=admin, json={**body, "period_name": "New name"})
    assert renamed.status_code == 200 and renamed.json()["changed_fields"] == ["period_name"]
    assert store.committed()[FINAL].end_date == dt.date(2026, 11, 28)


def test_drafts_do_not_lock_but_are_reported_stale(client, admin, store) -> None:
    store.timetables[FINAL] = ["DRAFT", "DRAFT"]
    body = _update_body(client, admin, end_date="2026-11-27")
    out = client.put(f"{PERIODS}/{FINAL}", headers=admin, json=body).json()
    assert out["changed"] is True and out["draft_timetables"] == 2
    assert "2 saved draft timetable(s)" in out["notices"][0]


def test_changing_kind_or_semester_keeps_the_cycle_mapping_valid(client, admin, store) -> None:
    # No module in the mapping has a MID examination, and both are semester 2 modules.
    mid = client.put(f"{PERIODS}/{FINAL}", headers=admin, json=_update_body(client, admin, exam_kind="MID"))
    assert mid.status_code == 422 and set(_rules(mid)) == {"CYCLE_MAPPING"}
    assert "IT1010 has no MID examination" in mid.text and "IT1030" in mid.text
    semester = client.put(f"{PERIODS}/{FINAL}", headers=admin, json=_update_body(client, admin, semester=1))
    assert semester.status_code == 422 and "only as repeat_only" in semester.text
    assert store.commits == 0 and store.committed()[FINAL].exam_kind == "FINAL"


def test_a_failed_write_is_rolled_back(client, admin, store) -> None:
    store.fail_on = "commit"
    with pytest.raises(RuntimeError):
        client.put(f"{PERIODS}/{FINAL}", headers=admin, json=_update_body(client, admin, end_date="2026-11-27"))
    store.fail_on = None
    assert store.committed()[FINAL].end_date == dt.date(2026, 11, 28)
    assert asyncio.run(store.get(FINAL)) == store.committed()[FINAL]  # uncommitted work thrown away


# --- Rules ------------------------------------------------------------------------------------


def test_fingerprint_covers_every_stored_field() -> None:
    base = dict(period_code="P1", period_name="Name", academic_year_label="2026/2027", semester=1,
                exam_kind="MID", start_date=dt.date(2027, 3, 1), end_date=dt.date(2027, 3, 12),
                day_start_time=dt.time(8, 30), day_end_time=dt.time(17, 30), include_saturdays=False,
                include_sundays=False, exclusions=[(dt.date(2027, 3, 5), "Holiday")])
    period = build_period(**base)
    assert period_problems(period) == []
    variants = [{"period_name": "Other"}, {"academic_year_label": "2027/2028"}, {"semester": 2},
                {"exam_kind": "FINAL"}, {"start_date": dt.date(2027, 3, 2)}, {"end_date": dt.date(2027, 3, 11)},
                {"day_start_time": dt.time(9)}, {"day_end_time": dt.time(17)}, {"include_saturdays": True},
                {"include_sundays": True}, {"exclusions": [(dt.date(2027, 3, 5), "Other reason")]},
                {"exclusions": []}]
    prints = {period_fingerprint(build_period(**{**base, **v})) for v in variants}
    assert len(prints) == len(variants) and period_fingerprint(period) not in prints
