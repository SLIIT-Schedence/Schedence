"""Approval and publishing workflow: DRAFT -> APPROVED -> PUBLISHED, stale inputs, integrity, permissions.

Real RS256 tokens and a fake Identity Service; inputs come from the synthetic
dataset through the generation harness of test_timetable_generation; saved
timetables live in the in-memory store. The database triggers and the store on
PostgreSQL are tested in test_timetables_postgres.
"""

from __future__ import annotations

import asyncio
import dataclasses

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("ortools")

from app.saved_timetables.memory import MemoryTimetableStore  # noqa: E402
from app.sources import DatasetSource  # noqa: E402
from auth_support import EXAMINATION_PERMISSION_SETS, INSTITUTION, OTHER_INSTITUTION, FakeIdentityService  # noqa: E402
from schedence_auth.testing import TokenFactory  # noqa: E402
from test_saved_timetables import DRAFTS, LIST, client  # noqa: E402, F401  (fixture)
from test_timetable_generation import DATASET, Harness, body, headers  # noqa: E402

REASON = "Checked by the examination board"


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
def admin(tokens, identity) -> dict[str, str]:
    return headers(tokens, identity, "EXAMINATION_ADMIN")


def _save(client, admin, **extra) -> dict:
    response = client.post(DRAFTS, headers=admin, json=body(**extra))
    assert response.status_code == 201, response.text
    return response.json()


def _change(client, admin, action: str, timetable: dict, revision: int | None = None, **extra):
    payload = {"expected_revision": revision or timetable["revision"], "reason": REASON, **extra}
    return client.post(f"{LIST}/{timetable['timetable_id']}/{action}", headers=admin, json=payload)


def _rules(response) -> list[str]:
    return [p["rule"] for p in response.json()["detail"]]


def _status(client, admin, timetable: dict) -> str:
    return client.get(f"{LIST}/{timetable['timetable_id']}", headers=admin).json()["status"]


# --- Happy path and history --------------------------------------------------------------------------------


def test_draft_is_approved_then_published_with_history(client, admin) -> None:
    draft = _save(client, admin)
    assert draft["content_fingerprint"] and len(draft["content_fingerprint"]) == 64
    approved = _change(client, admin, "approve", draft)
    assert approved.status_code == 200, approved.text
    data = approved.json()
    assert (data["timetable"]["status"], data["timetable"]["revision"]) == ("APPROVED", 2)
    assert data["timetable"]["approved_by"] and data["timetable"]["approved_at"]
    assert data["timetable"]["published_at"] is None and data["archived_timetable"] is None
    assert data["history"]["details"]["rules_checked"] == [f"H{i}" for i in range(1, 12)]
    assert data["history"]["details"]["input_fingerprint"] == draft["input_fingerprint"]

    published = _change(client, admin, "publish", data["timetable"])
    assert published.status_code == 200, published.text
    timetable = published.json()["timetable"]
    assert (timetable["status"], timetable["revision"]) == ("PUBLISHED", 3)
    assert timetable["approved_at"] == data["timetable"]["approved_at"] and timetable["published_by"]

    history = client.get(f"{LIST}/{draft['timetable_id']}/history", headers=admin).json()
    assert [(h["action"], h["from_status"], h["to_status"], h["revision"]) for h in history] == [
        ("create", None, "DRAFT", 1), ("approve", "DRAFT", "APPROVED", 2), ("publish", "APPROVED", "PUBLISHED", 3)]
    assert history[1]["reason"] == REASON
    # Sessions are unchanged by the workflow, and the list shows the new status.
    view = client.get(f"{LIST}/{draft['timetable_id']}", headers=admin).json()
    assert view["sessions"] == draft["sessions"]
    assert client.get(LIST, headers=admin, params={"status": "PUBLISHED"}).json()["total"] == 1


# --- Invalid status changes ---------------------------------------------------------------------------------


def test_a_draft_cannot_be_published(client, admin) -> None:
    draft = _save(client, admin)
    response = _change(client, admin, "publish", draft)
    assert response.status_code == 409 and _rules(response)[0] == "STATUS"
    assert "must be approved before it can be published" in response.text
    assert _status(client, admin, draft) == "DRAFT"


def test_approved_and_published_timetables_cannot_be_approved_again(client, admin) -> None:
    draft = _save(client, admin)
    approved = _change(client, admin, "approve", draft).json()["timetable"]
    again = _change(client, admin, "approve", approved)
    assert again.status_code == 409 and "already approved" in again.text
    published = _change(client, admin, "publish", approved).json()["timetable"]
    for action in ("approve", "publish"):
        response = _change(client, admin, action, published)
        assert response.status_code == 409 and "already published" in response.text


def test_a_stale_revision_is_refused(client, admin) -> None:
    draft = _save(client, admin)
    response = _change(client, admin, "approve", draft, revision=5)
    assert response.status_code == 409 and _rules(response) == ["REVISION"]
    assert "current revision 1" in response.text and _status(client, admin, draft) == "DRAFT"


def test_one_published_timetable_per_cycle_and_explicit_replacement(client, admin) -> None:
    first = _change(client, admin, "approve", _save(client, admin)).json()["timetable"]
    first = _change(client, admin, "publish", first).json()["timetable"]
    second = _change(client, admin, "approve", _save(client, admin)).json()["timetable"]
    refused = _change(client, admin, "publish", second)
    assert refused.status_code == 409 and _rules(refused) == ["ALREADY_PUBLISHED"]
    assert "replace_published=true" in refused.text and _status(client, admin, second) == "APPROVED"

    replaced = _change(client, admin, "publish", second, replace_published=True)
    assert replaced.status_code == 200, replaced.text
    data = replaced.json()
    assert data["timetable"]["status"] == "PUBLISHED"
    assert data["archived_timetable"]["timetable_id"] == first["timetable_id"]
    assert data["archived_timetable"]["status"] == "ARCHIVED"
    assert data["history"]["details"]["replaced_timetable_id"] == first["timetable_id"]
    assert client.get(LIST, headers=admin, params={"status": "PUBLISHED"}).json()["total"] == 1
    archived_history = client.get(f"{LIST}/{first['timetable_id']}/history", headers=admin).json()
    assert archived_history[-1]["action"] == "archive" and "Replaced by timetable" in archived_history[-1]["reason"]
    # An archived timetable never comes back.
    response = _change(client, admin, "publish", data["archived_timetable"])
    assert response.status_code == 409 and "archived timetable cannot be published" in response.text


def test_failure_during_a_status_change_changes_nothing(client, admin, store) -> None:
    draft = _save(client, admin)
    store.fail_on = "change_status"
    assert _change(client, admin, "approve", draft).status_code == 500
    store.fail_on = None
    assert _status(client, admin, draft) == "DRAFT"
    assert len(client.get(f"{LIST}/{draft['timetable_id']}/history", headers=admin).json()) == 1


# --- Stale inputs and integrity -------------------------------------------------------------------------------


def test_changed_settings_block_approval(client, admin, harness) -> None:
    draft = _save(client, admin)
    asyncio.run(harness.save(scheduling__minimum_break_minutes=45))
    response = _change(client, admin, "approve", draft)
    assert response.status_code == 409 and _rules(response)[0] == "STALE"
    assert "STALE_SETTINGS" in _rules(response) and "version 0 -> 1" in response.text
    assert _status(client, admin, draft) == "DRAFT"


def test_changed_registrations_block_approval_and_fail_validation(client, admin, harness) -> None:
    draft = _save(client, admin)
    dropped = next(r for r in DATASET.registrations if r.module_code == "IT1010")
    harness.source = DatasetSource(dataclasses.replace(
        DATASET, registrations=tuple(r for r in DATASET.registrations if r != dropped)))
    response = _change(client, admin, "approve", draft)
    assert response.status_code == 409
    rules = _rules(response)
    assert "STALE_INPUTS" in rules and "H11" in rules  # the validator rejects it on the current inputs
    assert "STALE_SETTINGS" not in rules


def test_changed_seating_capacities_block_approval(client, admin, harness) -> None:
    draft = _save(client, admin)
    harness.seating_percentage = 60
    response = _change(client, admin, "approve", draft)
    assert response.status_code == 409 and "STALE_CAPACITIES" in _rules(response)


def test_inputs_that_can_no_longer_be_loaded_block_approval(client, admin, harness) -> None:
    draft = _save(client, admin)
    harness.source = DatasetSource(dataclasses.replace(DATASET, exam_periods=(), exam_period_exclusions=()))
    response = _change(client, admin, "approve", draft)
    assert response.status_code == 409 and _rules(response)[:2] == ["STALE", "STALE_INPUTS"]
    assert "can no longer be loaded" in response.text


def test_inputs_changed_after_approval_block_publication(client, admin, harness) -> None:
    approved = _change(client, admin, "approve", _save(client, admin)).json()["timetable"]
    asyncio.run(harness.save(scheduling__minimum_break_minutes=45))
    response = _change(client, admin, "publish", approved)
    assert response.status_code == 409 and "STALE_SETTINGS" in _rules(response)
    assert _status(client, admin, approved) == "APPROVED"


def test_silently_changed_seats_block_approval(client, admin, store) -> None:
    draft = _save(client, admin)
    store.tamper_seat(draft["timetable_id"], seat_number=999)
    response = _change(client, admin, "approve", draft)
    assert response.status_code == 409 and _rules(response) == ["INTEGRITY"]


def test_timetables_without_a_content_fingerprint_cannot_be_approved(client, admin, store) -> None:
    draft = _save(client, admin)
    store._work.timetables[0][2]["content_fingerprint"] = None  # as saved before migration 0011
    response = _change(client, admin, "approve", draft)
    assert response.status_code == 409 and "saved before content checks existed" in response.text


# --- Regeneration ---------------------------------------------------------------------------------------------


def test_regeneration_makes_a_new_draft_and_keeps_the_old_one(client, admin, harness) -> None:
    published = _change(client, admin, "approve", _save(client, admin, mode="optimized")).json()["timetable"]
    published = _change(client, admin, "publish", published).json()["timetable"]
    asyncio.run(harness.save(scheduling__minimum_break_minutes=45))  # the published one is now stale
    response = client.post(f"{LIST}/{published['timetable_id']}/regenerate", headers=admin,
                           json={"time_limit_seconds": 10})
    assert response.status_code == 201, response.text
    new = response.json()
    assert (new["status"], new["draft_number"], new["revision"]) == ("DRAFT", 2, 1)
    assert new["source_timetable_id"] == published["timetable_id"]
    assert new["mode"] == "optimized" and new["module_codes"] == body()["module_codes"]
    assert new["settings_version"] == 1 and _status(client, admin, published) == "PUBLISHED"
    # The new draft matches the current inputs: it can be approved and replace the old one.
    approved = _change(client, admin, "approve", new).json()["timetable"]
    replaced = _change(client, admin, "publish", approved, replace_published=True)
    assert replaced.status_code == 200 and replaced.json()["archived_timetable"]["status"] == "ARCHIVED"


def test_regeneration_of_an_unknown_timetable_is_404(client, admin, harness) -> None:
    response = client.post(f"{LIST}/99/regenerate", headers=admin, json={})
    assert response.status_code == 404 and harness.loads == 0


# --- Requests, permissions and institution scope ---------------------------------------------------------------


@pytest.mark.parametrize("payload", [
    {}, {"reason": REASON}, {"expected_revision": 1}, {"expected_revision": 1, "reason": "ok"},
    {"expected_revision": "1", "reason": REASON}, {"expected_revision": 0, "reason": REASON},
    {"expected_revision": 1, "reason": REASON, "status": "PUBLISHED"},
])
def test_invalid_requests_are_422(client, admin, payload) -> None:
    draft = _save(client, admin)
    for action in ("approve", "publish"):
        assert client.post(f"{LIST}/{draft['timetable_id']}/{action}", headers=admin, json=payload).status_code == 422
    assert client.post(f"{LIST}/{draft['timetable_id']}/regenerate", headers=admin,
                       json={"mode": "fastest"}).status_code == 422


@pytest.mark.parametrize("permissions", EXAMINATION_PERMISSION_SETS)
def test_each_workflow_route_needs_its_permission(client, tokens, identity, admin, permissions) -> None:
    draft = _save(client, admin)
    auth = headers(tokens, identity, "CUSTOM", permissions=permissions)
    can_approve = "examinations:approve" in permissions
    approved = _change(client, auth, "approve", draft)
    assert approved.status_code == (200 if can_approve else 403), approved.text
    if can_approve:
        assert _change(client, auth, "publish", approved.json()["timetable"]).status_code == 200
    else:
        assert _change(client, auth, "publish", draft).status_code == 403
    regenerate = client.post(f"{LIST}/{draft['timetable_id']}/regenerate", headers=auth,
                             json={"time_limit_seconds": 5})
    assert regenerate.status_code == (201 if "examinations:generate" in permissions else 403)
    history = client.get(f"{LIST}/{draft['timetable_id']}/history", headers=auth)
    assert history.status_code == (200 if "examinations:read" in permissions else 403)


def test_other_institutions_cannot_change_or_see_timetables(client, tokens, identity, admin, store) -> None:
    draft = _save(client, admin)
    outsider = headers(tokens, identity, "EXAMINATION_ADMIN", institution=OTHER_INSTITUTION)
    for action in ("approve", "publish"):
        assert _change(client, outsider, action, draft).status_code == 403
    assert client.get(f"{LIST}/{draft['timetable_id']}/history", headers=outsider).status_code == 403
    # A timetable of another institution is not found here.
    other = asyncio.run(store.save(OTHER_INSTITUTION, store._work.timetables[0][1]))
    asyncio.run(store.commit())
    other_view = {**draft, "timetable_id": other["timetable_id"]}
    assert _change(client, admin, "approve", other_view).status_code == 404
    assert client.get(f"{LIST}/{other['timetable_id']}/history", headers=admin).status_code == 404
    assert _status(client, admin, draft) == "DRAFT"
    assert asyncio.run(store.lock(INSTITUTION, other["timetable_id"])) is None
