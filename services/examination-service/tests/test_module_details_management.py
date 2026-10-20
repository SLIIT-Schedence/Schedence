"""Module examination details API (Step 11b): RBAC, validation, dry run, fingerprints and safety rules.

Real RS256 tokens and a fake Identity Service (as in test_seating_api.py); the
details live in an in-memory store with real transaction behaviour. The same
flows run against PostgreSQL in test_module_details_postgres.py.
"""

from __future__ import annotations

import asyncio
import dataclasses
import datetime as dt
import uuid

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_module_details_writer, get_source
from app.main import create_app
from app.module_details import MemoryModuleDetailsStore, ModuleDetailsService, SavedTimetableRef
from app.module_details.rules import EDITABLE_FIELDS, details_fingerprint, details_of
from app.seed.generator import generate_dataset
from app.seed.records import ExamPeriodModule
from app.sources import DatasetSource
from auth_support import INSTITUTION, ROLE_PERMISSIONS, FakeIdentityService, make_auth_context
from schedence_auth.testing import TokenFactory

MODULES = "/api/v1/modules"
FINAL = "PP1-2026-S2-FINAL"
DATA = generate_dataset()
# A short exam day (08:30-13:30 = 300 min) and two mapped modules, so cycle checks have something to check.
DATA = dataclasses.replace(
    DATA,
    exam_periods=tuple(dataclasses.replace(p, day_end_time=dt.time(13, 30)) for p in DATA.exam_periods),
    exam_period_modules=(ExamPeriodModule(FINAL, "IT1010"), ExamPeriodModule(FINAL, "IT1030")),
)
SOURCE = DatasetSource(DATA)
UNMAPPED = "IE2012"


@pytest.fixture
def tokens() -> TokenFactory:
    return TokenFactory()


@pytest.fixture
def identity() -> FakeIdentityService:
    return FakeIdentityService()


@pytest.fixture
def store() -> MemoryModuleDetailsStore:
    return MemoryModuleDetailsStore(SOURCE)


@pytest.fixture
def client(tokens, identity, store) -> TestClient:
    app = create_app(make_auth_context(tokens, identity))
    app.dependency_overrides[get_source] = lambda: SOURCE
    app.dependency_overrides[get_module_details_writer] = lambda: ModuleDetailsService(store)
    return TestClient(app)


def _headers(tokens, identity, permissions: list[str], institution=INSTITUTION) -> dict[str, str]:
    sub = identity.register({institution: (["EXAMINATION_ADMIN"], permissions)})
    return {"Authorization": f"Bearer {tokens.access_token(sub)}"}


@pytest.fixture
def admin(tokens, identity) -> dict[str, str]:
    return _headers(tokens, identity, ROLE_PERMISSIONS["EXAMINATION_ADMIN"])


def _body(client, admin, code="IT1010", store=None, **changes) -> dict:
    """The current details (from the store when given: GET reads the unchanged dataset) with ``changes``."""
    if store is None:
        current = client.get(f"{MODULES}/{code}", headers=admin).json()
    else:
        details = details_of(asyncio.run(store.get(code)))
        current = {**dataclasses.asdict(details), "fingerprint": details_fingerprint(code, details)}
    return {**{k: current[k] for k in EDITABLE_FIELDS}, "expected_fingerprint": current["fingerprint"], **changes}


def _put(client, admin, code="IT1010", params=None, store=None, **changes):
    return client.put(f"{MODULES}/{code}/exam-details", headers=admin, params=params,
                      json=_body(client, admin, code, store, **changes))


def _rules(response) -> list[str]:
    return [p["rule"] for p in response.json()["detail"]]


# --- Reading stays backward-compatible --------------------------------------------------------


def test_read_endpoints_keep_their_fields_and_add_a_fingerprint(client, admin) -> None:
    modules = client.get(MODULES, headers=admin).json()
    assert len(modules) == 42 and all(len(m["fingerprint"]) == 64 for m in modules)
    one = client.get(f"{MODULES}/IT1010", headers=admin).json()
    assert one == next(m for m in modules if m["module_code"] == "IT1010")
    assert {"module_code", "module_name", "academic_year", "semester", "specializations", "credits", "difficulty",
            "exam_type", "duration_minutes", "has_extra_reading_time", "reading_time_minutes", "has_final_exam",
            "has_mid_exam", "mid_duration_minutes", "delivery_mode", "block_category", "candidate_count"} < set(one)
    assert client.get(MODULES, headers=admin, params={"exam_type": "written"}).status_code == 200


# --- RBAC -------------------------------------------------------------------------------------


def test_updates_need_a_token_and_examinations_manage(client, tokens, identity, admin) -> None:
    body = _body(client, admin, difficulty=2)
    url = f"{MODULES}/IT1010/exam-details"
    assert client.put(url, json=body).status_code == 401
    others = _headers(tokens, identity, ["examinations:read", "examinations:generate", "examinations:approve"])
    assert client.put(url, headers=others, json=body).status_code == 403
    elsewhere = _headers(tokens, identity, ROLE_PERMISSIONS["EXAMINATION_ADMIN"], institution=uuid.uuid4())
    assert client.put(url, headers=elsewhere, json=body).status_code == 403
    manage_only = _headers(tokens, identity, ["examinations:manage"])
    assert client.get(f"{MODULES}/IT1010", headers=manage_only).status_code == 403  # reads need examinations:read
    assert client.put(url, headers=manage_only, json=body).status_code == 200


# --- Updates ----------------------------------------------------------------------------------


def test_update_final_details_keeps_everything_else(client, admin, store) -> None:
    before = client.get(f"{MODULES}/IT1010", headers=admin).json()
    response = _put(client, admin, exam_type="written", duration_minutes=120, reading_time_minutes=0, difficulty=3)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["changed_fields"] == ["exam_type", "duration_minutes", "reading_time_minutes", "difficulty"]
    module = body["module"]
    assert (module["exam_type"], module["duration_minutes"], module["reading_time_minutes"]) == ("written", 120, 0)
    assert module["has_extra_reading_time"] is False  # the flag follows the minutes
    for kept in ("module_code", "module_name", "academic_year", "semester", "specializations", "credits",
                 "candidate_count"):
        assert module[kept] == before[kept]
    assert store.committed("IT1010").duration_minutes == 120 and store.commits == 1
    assert module["fingerprint"] == details_fingerprint("IT1010", store.committed("IT1010"))
    assert module["fingerprint"] != before["fingerprint"]


def test_add_a_mid_exam_and_block_mode(client, admin, store) -> None:
    response = _put(client, admin, has_mid_exam=True, mid_duration_minutes=60, delivery_mode="block",
                    block_category="pro_rata")
    assert response.status_code == 200, response.text
    assert response.json()["changed_fields"] == ["has_mid_exam", "mid_duration_minutes", "delivery_mode",
                                                 "block_category"]
    saved = store.committed("IT1010")
    assert (saved.has_mid_exam, saved.mid_duration_minutes, saved.block_category) == (True, 60, "pro_rata")


@pytest.mark.parametrize("changes, rule", [
    ({"has_mid_exam": True, "mid_duration_minutes": None}, "MODULE_ELIGIBILITY"),
    ({"has_mid_exam": False, "mid_duration_minutes": 60}, "MODULE_ELIGIBILITY"),
    ({"delivery_mode": "block", "block_category": None}, "MODULE_ELIGIBILITY"),
    ({"delivery_mode": "regular", "block_category": "full_module"}, "MODULE_ELIGIBILITY"),
    ({"has_final_exam": False, "has_mid_exam": False}, "MODULE_ELIGIBILITY"),
    ({"duration_minutes": 10}, "MODULE_DETAILS"),
    ({"duration_minutes": 481}, "MODULE_DETAILS"),
    ({"has_mid_exam": True, "mid_duration_minutes": 500}, "MODULE_DETAILS"),
    ({"reading_time_minutes": -1}, "MODULE_DETAILS"),
    ({"reading_time_minutes": 61}, "MODULE_DETAILS"),
    ({"difficulty": 0}, "MODULE_DETAILS"),
    ({"difficulty": 6}, "MODULE_DETAILS"),
])
def test_field_and_eligibility_rules(client, admin, store, changes, rule) -> None:
    response = _put(client, admin, UNMAPPED, **changes)
    assert response.status_code == 422 and rule in _rules(response), response.text
    assert store.commits == 0


def test_every_problem_is_listed(client, admin) -> None:
    response = _put(client, admin, UNMAPPED, duration_minutes=5, reading_time_minutes=90, difficulty=9,
                    has_mid_exam=True, mid_duration_minutes=None)
    assert response.status_code == 422
    assert _rules(response) == ["MODULE_DETAILS"] * 3 + ["MODULE_ELIGIBILITY"]


@pytest.mark.parametrize("changes", [
    {"exam_type": "practical"}, {"exam_type": "oral"}, {"duration_minutes": "60"}, {"duration_minutes": 60.0},
    {"has_mid_exam": "true"}, {"delivery_mode": "BLOCK"}, {"block_category": "half"}, {"module_code": "IT9999"},
    {"module_name": "Renamed"}, {"credits": 3}, {"expected_fingerprint": "x"},
])
def test_malformed_input_and_other_fields_are_rejected(client, admin, store, changes) -> None:
    assert _put(client, admin, **changes).status_code == 422
    assert store.commits == 0


def test_fields_are_required(client, admin) -> None:
    body = _body(client, admin)
    del body["mid_duration_minutes"]
    assert client.put(f"{MODULES}/IT1010/exam-details", headers=admin, json=body).status_code == 422


def test_unknown_module_is_404(client, admin) -> None:
    body = _body(client, admin)
    response = client.put(f"{MODULES}/IT9999/exam-details", headers=admin, json=body)
    assert response.status_code == 404 and _rules(response) == ["NOT_FOUND"]
    assert client.put(f"{MODULES}/it1010/exam-details", headers=admin, json=body).status_code == 422


def test_update_needs_the_current_fingerprint(client, admin, store) -> None:
    body = _body(client, admin, difficulty=2)
    assert client.put(f"{MODULES}/IT1010/exam-details", headers=admin, json=body).status_code == 200
    stale = client.put(f"{MODULES}/IT1010/exam-details", headers=admin, json={**body, "difficulty": 3})
    assert stale.status_code == 409 and _rules(stale) == ["VERSION"]
    assert store.committed("IT1010").difficulty == 2


def test_dry_run_and_unchanged_updates_write_nothing(client, admin, store) -> None:
    same = _put(client, admin).json()
    assert same["changed"] is False and same["changed_fields"] == []
    preview = _put(client, admin, params={"dry_run": True}, difficulty=5).json()
    assert preview["dry_run"] is True and preview["module"]["difficulty"] == 5
    assert store.commits == 0 and store.committed("IT1010") is None


# --- Cycle mappings ---------------------------------------------------------------------------


def test_changes_that_break_a_cycle_mapping_are_refused(client, admin, store) -> None:
    no_final = _put(client, admin, has_final_exam=False, has_mid_exam=True, mid_duration_minutes=60)
    assert no_final.status_code == 422 and _rules(no_final) == ["CYCLE_MAPPING"]
    assert "has no FINAL examination" in no_final.text
    too_long = _put(client, admin, duration_minutes=300)  # 300 + 10 reading > 300-minute exam day
    assert too_long.status_code == 422 and "longer than the exam day" in too_long.text
    block = _put(client, admin, delivery_mode="block", block_category="full_module")
    assert block.status_code == 200  # still a valid member of its own semester's cycle
    assert store.commits == 1
    # A module outside every cycle may drop its FINAL exam.
    assert _put(client, admin, UNMAPPED, has_final_exam=False, has_mid_exam=True,
                mid_duration_minutes=90).status_code == 200


# --- Saved timetables -------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["APPROVED", "PUBLISHED", "ARCHIVED"])
def test_released_final_timetables_lock_the_final_details_only(client, admin, store, status) -> None:
    store.timetable_refs["IT1010"] = [SavedTimetableRef("DRAFT", FINAL, "FINAL"),
                                      SavedTimetableRef(status, FINAL, "FINAL")]
    refused = _put(client, admin, duration_minutes=90, difficulty=4)
    assert refused.status_code == 409 and _rules(refused) == ["MODULE_LOCKED"] * 3
    assert status in refused.text and "duration_minutes" in refused.text and "difficulty" in refused.text
    mid = _put(client, admin, has_mid_exam=True, mid_duration_minutes=60)  # MID details are not used by it
    assert mid.status_code == 200, mid.text
    assert store.committed("IT1010").duration_minutes == 60


def test_released_mid_timetables_lock_the_mid_details(client, admin, store) -> None:
    assert _put(client, admin, has_mid_exam=True, mid_duration_minutes=60).status_code == 200
    store.timetable_refs["IT1010"] = [SavedTimetableRef("PUBLISHED", "PP1-2027-S1-MID", "MID")]
    refused = _put(client, admin, store=store, mid_duration_minutes=90)
    assert refused.status_code == 409 and "mid_duration_minutes" in refused.text
    assert _put(client, admin, store=store, duration_minutes=90).status_code == 200  # FINAL-only field


def test_drafts_do_not_lock_but_are_reported_stale(client, admin, store) -> None:
    store.timetable_refs["IT1010"] = [SavedTimetableRef("DRAFT", FINAL, "FINAL")] * 2
    out = _put(client, admin, difficulty=4).json()
    assert out["changed"] is True and out["draft_timetables"] == 2
    assert "2 saved draft timetable(s)" in out["notices"][0]
    assert _put(client, admin, store=store).json()["notices"] == []  # nothing changed: nothing becomes stale


def test_a_failed_write_is_rolled_back(client, admin, store) -> None:
    store.fail_on = "commit"
    with pytest.raises(RuntimeError):
        _put(client, admin, difficulty=4)
    store.fail_on = None
    assert store.committed("IT1010") is None
    assert asyncio.run(store.get("IT1010")).difficulty == details_of(SOURCE._modules_by_code["IT1010"]).difficulty \
        if hasattr(SOURCE, "_modules_by_code") else asyncio.run(store.get("IT1010")).difficulty == 1
