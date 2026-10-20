"""Saved timetable API: save validated drafts, list, view, seats, permissions and institution scope.

Real RS256 tokens and a fake Identity Service; generation inputs come from the
synthetic dataset (see test_timetable_generation) and saved timetables from the
in-memory store. The PostgreSQL store is tested in test_seating_postgres.
"""

from __future__ import annotations

import asyncio
import dataclasses
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("ortools")

import app.api.timetables as timetables_api  # noqa: E402
import app.generation.service as generation  # noqa: E402
from app.api.deps import get_saved_timetable_reader, get_saved_timetable_writer, get_timetable_generator  # noqa: E402
from app.main import create_app  # noqa: E402
from app.saved_timetables import NewTimetable  # noqa: E402
from app.saved_timetables.memory import MemoryTimetableStore  # noqa: E402
from app.scheduling.contracts import Violation  # noqa: E402
from app.scheduling.types import SolveResult, SolveStatus  # noqa: E402
from auth_support import (  # noqa: E402
    EXAMINATION_PERMISSION_SETS,
    INSTITUTION,
    OTHER_INSTITUTION,
    ROLE_PERMISSIONS,
    FakeIdentityService,
    make_auth_context,
)
from schedence_auth.testing import TokenFactory  # noqa: E402
from test_timetable_generation import DATASET, PERIOD, SHARED, Harness, body, headers  # noqa: E402

DRAFTS = "/api/v1/timetables/drafts"
LIST = "/api/v1/timetables"
GENERATE = "/api/v1/timetables/generate"


@pytest.fixture
def tokens() -> TokenFactory:
    return TokenFactory()


@pytest.fixture
def identity() -> FakeIdentityService:
    return FakeIdentityService()


@pytest.fixture
def harness() -> Harness:
    return Harness()


@pytest.fixture
def store() -> MemoryTimetableStore:
    return MemoryTimetableStore()


@pytest.fixture
def client(tokens, identity, harness, store) -> TestClient:
    app = create_app(make_auth_context(tokens, identity))
    app.dependency_overrides[get_timetable_generator] = lambda: harness.generator(app.state.generation_lock)
    app.dependency_overrides[get_saved_timetable_reader] = lambda: store

    @asynccontextmanager
    async def open_store():
        yield store

    app.dependency_overrides[get_saved_timetable_writer] = lambda: open_store
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def admin(tokens, identity) -> dict[str, str]:
    return headers(tokens, identity, "EXAMINATION_ADMIN")


def _save(client, admin, **extra) -> dict:
    response = client.post(DRAFTS, headers=admin, json=body(**extra))
    assert response.status_code == 201, response.text
    return response.json()


# --- Save ------------------------------------------------------------------------------------------------------


def test_saved_draft_stores_cycle_versions_sessions_seats_and_validation(client, admin, harness, store) -> None:
    asyncio.run(harness.save(scheduling__minimum_break_minutes=120))
    data = _save(client, admin)
    assert (data["status"], data["draft_number"], data["revision"]) == ("DRAFT", 1, 1)
    assert (data["period_code"], data["exam_kind"], data["mode"]) == (PERIOD, "FINAL", "feasibility")
    assert data["solver_status"] in ("OPTIMAL", "FEASIBLE") and data["validation_passed"] is True
    assert data["validation"]["passed"] is True and data["validation"]["violations"] == []
    assert data["settings_version"] == 1 == data["inputs"]["settings_version"]
    assert data["input_fingerprint"] == data["inputs"]["input_fingerprint"] and len(data["input_fingerprint"]) == 64
    assert data["seating_ruleset_version"] == data["inputs"]["capacity_ruleset_version"]
    assert data["module_codes"] == SHARED and data["selection"] == "listed_modules"
    assert data["approved_at"] is None and data["published_at"] is None
    assert {s["module_code"] for s in data["sessions"]} == set(SHARED)
    assert "student_id" not in str(data) and store.commits == 1  # seat counts only in the view
    # Every registration got one stored seat, with its registration type.
    seats, total = asyncio.run(store.seats(INSTITUTION, data["timetable_id"], session_id=None, limit=10**6, offset=0))
    expected = {(r.student_id, r.module_code, r.registration_type) for r in DATASET.registrations
                if r.module_code in SHARED}
    sessions = {s["session_id"]: s["module_code"] for s in data["sessions"]}
    assert total == len(expected) == data["summary"]["seat_allocations"]
    assert {(s["student_id"], sessions[s["session_id"]], s["registration_type"]) for s in seats} == expected


def test_each_save_is_a_new_draft_and_older_drafts_are_kept(client, admin) -> None:
    first = _save(client, admin)
    second = _save(client, admin, mode="optimized")
    assert (first["draft_number"], second["draft_number"]) == (1, 2)
    assert first["timetable_id"] != second["timetable_id"]
    again = client.get(f"{LIST}/{first['timetable_id']}", headers=admin).json()
    assert again["mode"] == "feasibility" and again["sessions"] == first["sessions"]
    assert client.get(LIST, headers=admin).json()["total"] == 2


def test_unsuccessful_generation_is_not_saved(client, admin, store, monkeypatch) -> None:
    monkeypatch.setattr(generation, "generate_timetable", lambda snapshot, settings: SolveResult(
        SolveStatus.INFEASIBLE, diagnostics=("no room",)))
    response = client.post(DRAFTS, headers=admin, json=body())
    assert response.status_code == 422 and "status INFEASIBLE" in response.text
    assert store.commits == 0 and asyncio.run(store.list(INSTITUTION, timetables_api.ListFilter(), limit=10,
                                                         offset=0))[1] == 0


def test_timetable_rejected_by_the_final_validation_is_not_saved(client, admin, store, monkeypatch) -> None:
    monkeypatch.setattr(timetables_api, "validate_timetable", lambda snapshot, timetable: (Violation("H1", "x"),))
    response = client.post(DRAFTS, headers=admin, json=body())
    assert response.status_code == 422 and "MODEL_INVALID" in response.text
    assert store.commits == 0


def test_failed_save_commits_nothing(client, admin, store) -> None:
    store.fail_on = "save"
    assert client.post(DRAFTS, headers=admin, json=body()).status_code == 500
    store.fail_on = None
    assert client.get(LIST, headers=admin).json()["total"] == 0


def test_generate_api_is_unchanged_and_saves_nothing(client, admin, store) -> None:
    data = client.post(GENERATE, headers=admin, json=body()).json()
    assert data["generated"] and data["persisted"] is False
    assert data["notices"][0].startswith("Not saved")
    assert store.commits == 0


# --- List and view -----------------------------------------------------------------------------------------


def test_list_has_no_sessions_or_student_ids_and_filters(client, admin) -> None:
    first = _save(client, admin)
    _save(client, admin)
    response = client.get(LIST, headers=admin)
    data = response.json()
    assert [i["draft_number"] for i in data["items"]] == [2, 1]  # newest first
    assert "student_id" not in response.text and "sessions" not in data["items"][0]
    assert data["items"][1]["input_fingerprint"] == first["input_fingerprint"]
    assert client.get(LIST, headers=admin, params={"period_code": PERIOD, "exam_kind": "FINAL",
                                                   "status": "DRAFT"}).json()["total"] == 2
    assert client.get(LIST, headers=admin, params={"exam_kind": "MID"}).json()["total"] == 0
    assert client.get(LIST, headers=admin, params={"status": "PUBLISHED"}).json()["total"] == 0
    page = client.get(LIST, headers=admin, params={"limit": 1, "offset": 1}).json()
    assert (page["total"], [i["draft_number"] for i in page["items"]]) == (2, [1])
    for bad in ({"status": "LIVE"}, {"exam_kind": "QUIZ"}, {"period_code": "bad code"}, {"limit": 0}):
        assert client.get(LIST, headers=admin, params=bad).status_code == 422


def test_unknown_timetable_is_404(client, admin) -> None:
    assert client.get(f"{LIST}/999", headers=admin).status_code == 404
    assert client.get(f"{LIST}/999/seats", headers=admin).status_code == 404
    assert client.get(f"{LIST}/0", headers=admin).status_code == 422


def test_timetables_of_another_institution_are_invisible(client, admin, store) -> None:
    other = asyncio.run(store.save(OTHER_INSTITUTION, NewTimetable({"period_code": PERIOD, "summary": {}})))
    asyncio.run(store.commit())
    assert client.get(LIST, headers=admin).json()["total"] == 0
    assert client.get(f"{LIST}/{other['timetable_id']}", headers=admin).status_code == 404
    assert client.get(f"{LIST}/{other['timetable_id']}/seats", headers=admin).status_code == 404
    mine = _save(client, admin)
    assert mine["draft_number"] == 1  # draft numbers are counted per institution


# --- Seats -------------------------------------------------------------------------------------------------


def test_seats_are_paged_and_filtered_by_session(client, admin) -> None:
    data = _save(client, admin)
    session = data["sessions"][0]
    url = f"{LIST}/{data['timetable_id']}/seats"
    page = client.get(url, headers=admin, params={"session_id": session["session_id"], "limit": 5}).json()
    assert page["total"] == session["candidates"] and len(page["items"]) == 5
    assert {i["session_id"] for i in page["items"]} == {session["session_id"]}
    assert set(page["items"][0]) == {"session_id", "venue_id", "seat_number", "student_id", "registration_type"}
    assert client.get(url, headers=admin).json()["total"] == data["summary"]["seat_allocations"]


# --- Permissions -------------------------------------------------------------------------------------------


def test_no_token_is_401(client) -> None:
    assert client.post(DRAFTS, json=body()).status_code == 401
    assert client.get(LIST).status_code == 401
    assert client.get(f"{LIST}/1").status_code == 401


@pytest.mark.parametrize("permissions", EXAMINATION_PERMISSION_SETS)
def test_each_route_needs_its_permission(client, tokens, identity, harness, admin, permissions) -> None:
    saved = _save(client, admin)
    auth = headers(tokens, identity, "CUSTOM", permissions=permissions)
    loads = harness.loads
    save = client.post(DRAFTS, headers=auth, json=body(time_limit_seconds=5))
    assert save.status_code == (201 if "examinations:generate" in permissions else 403)
    if save.status_code == 403:
        assert harness.loads == loads  # nothing was generated
    can_read = "examinations:read" in permissions
    assert client.get(LIST, headers=auth).status_code == (200 if can_read else 403)
    assert client.get(f"{LIST}/{saved['timetable_id']}", headers=auth).status_code == (200 if can_read else 403)
    can_see_seats = bool({"examinations:generate", "examinations:approve"} & set(permissions))
    seats = client.get(f"{LIST}/{saved['timetable_id']}/seats", headers=auth)
    assert seats.status_code == (200 if can_see_seats else 403)


@pytest.mark.parametrize("role", sorted(ROLE_PERMISSIONS))
def test_only_examination_admins_use_saved_timetables(client, tokens, identity, admin, role) -> None:
    saved = _save(client, admin)
    auth = headers(tokens, identity, role)
    expected = 200 if role == "EXAMINATION_ADMIN" else 403
    assert client.get(LIST, headers=auth).status_code == expected
    assert client.get(f"{LIST}/{saved['timetable_id']}/seats", headers=auth).status_code == expected


def test_permissions_in_another_institution_do_not_count(client, tokens, identity, admin) -> None:
    saved = _save(client, admin)
    auth = headers(tokens, identity, "EXAMINATION_ADMIN", institution=OTHER_INSTITUTION)
    assert client.post(DRAFTS, headers=auth, json=body()).status_code == 403
    assert client.get(LIST, headers=auth).status_code == 403
    assert client.get(f"{LIST}/{saved['timetable_id']}", headers=auth).status_code == 403
    assert client.get(f"{LIST}/{saved['timetable_id']}/seats", headers=auth).status_code == 403


def test_saved_snapshot_is_independent_of_later_input_changes(client, admin, harness) -> None:
    saved = _save(client, admin)
    renamed = tuple(dataclasses.replace(m, module_name="Renamed") for m in DATASET.modules)
    harness.source = type(harness.source)(dataclasses.replace(DATASET, modules=renamed))
    view = client.get(f"{LIST}/{saved['timetable_id']}", headers=admin).json()
    assert view["sessions"] == saved["sessions"] and "Renamed" not in str(view)
