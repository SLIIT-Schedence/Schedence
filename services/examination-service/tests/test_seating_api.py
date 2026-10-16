"""Seating settings API: RBAC, validation, dry run, history, concurrency and rollback.

Real RS256 tokens and a fake Identity Service (as in test_auth.py); the rules
live in an in-memory store with real transaction behaviour. The same flows run
against PostgreSQL in test_seating_postgres.py.
"""

from __future__ import annotations

import logging
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_seating_store, get_seating_writer, get_source
from app.main import create_app
from app.seating.memory import MemorySeatingStore
from app.seating.service import SeatingService
from app.seed.generator import generate_dataset
from app.sources import DatasetSource
from auth_support import (
    EXAMINATION_PERMISSION_SETS,
    INSTITUTION,
    OTHER_INSTITUTION,
    ROLE_PERMISSIONS,
    FakeIdentityService,
    make_auth_context,
)
from schedence_auth.testing import TokenFactory

API = "/api/v1"
SETTINGS = f"{API}/settings/exam-seating"
SOURCE = DatasetSource(generate_dataset())
REASON = "Exam board decision 2026-10"


@pytest.fixture
def tokens() -> TokenFactory:
    return TokenFactory()


@pytest.fixture
def identity() -> FakeIdentityService:
    return FakeIdentityService()


@pytest.fixture
def store() -> MemorySeatingStore:
    return MemorySeatingStore(venue_ids={v.venue_id for v in SOURCE._venues})


def _client(tokens, identity, store, **kwargs) -> TestClient:
    app = create_app(make_auth_context(tokens, identity))
    app.dependency_overrides[get_source] = lambda: SOURCE
    app.dependency_overrides[get_seating_store] = lambda: store
    app.dependency_overrides[get_seating_writer] = lambda: SeatingService(store, SOURCE)
    return TestClient(app, **kwargs)


@pytest.fixture
def client(tokens, identity, store) -> TestClient:
    return _client(tokens, identity, store)


def _headers(tokens, identity, *roles, institution=INSTITUTION, permissions=None) -> dict[str, str]:
    perms = permissions if permissions is not None else sorted({p for r in roles for p in ROLE_PERMISSIONS[r]})
    sub = identity.register({institution: (list(roles), perms)})
    return {"Authorization": f"Bearer {tokens.access_token(sub)}"}


@pytest.fixture
def admin(tokens, identity) -> dict[str, str]:
    return _headers(tokens, identity, "EXAMINATION_ADMIN")


def _user_id(identity: FakeIdentityService, headers: dict[str, str]) -> str:
    import jwt

    sub = jwt.decode(headers["Authorization"].removeprefix("Bearer "), options={"verify_signature": False})["sub"]
    return str(identity.accounts[sub]["user_id"])


def _default(percentage=60, version=1, reason=REASON) -> dict:
    return {"percentage": percentage, "reason": reason, "expected_version": version}


def _venues() -> dict[str, int]:
    return {v.venue_id: v.normal_capacity for v in SOURCE._venues}


# --- Reading -------------------------------------------------------------------------------------


def test_get_settings_shows_the_initial_50_percent_default(client, admin) -> None:
    body = client.get(SETTINGS, headers=admin).json()
    assert body["default"]["percentage"] == 50 and body["default"]["version"] == 1
    assert body["default"]["updated_by"] is None  # set by the migration
    assert body["venue_overrides"] == [] and body["unavailable_venues"] == []
    assert body["min_percentage"] == 1 and body["max_percentage"] == 100
    assert body["venue_count"] == 62
    assert body["total_normal_capacity"] == sum(_venues().values())
    assert body["total_exam_capacity"] == sum(c // 2 for c in _venues().values())
    assert [h["action"] for h in body["history"]] == ["create"] and body["ruleset_version"] == 1


def test_venues_return_effective_percentage_and_capacity(client, admin) -> None:
    client.put(f"{SETTINGS}/venues/F303", headers=admin,
               json={"percentage": 33, "reason": REASON, "expected_version": None})
    venues = {v["venue_id"]: v for v in client.get(f"{API}/venues", headers=admin).json()}
    assert venues["F303"]["exam_seating_percentage"] == 33 and venues["F303"]["exam_seating_rule"] == "venue"
    assert venues["F303"]["exam_capacity"] == 240 * 33 // 100 == 79
    assert venues["F303"]["seed_exam_capacity"] == 120  # legacy column unchanged
    other = venues["F304"]
    assert other["exam_seating_rule"] == "default" and other["exam_capacity"] == other["normal_capacity"] // 2


# --- RBAC ------------------------------------------------------------------------------------------


WRITES = [
    ("PUT", f"{SETTINGS}/default", {"json": _default()}),
    ("PUT", f"{SETTINGS}/venues/F303", {"json": {"percentage": 40, "reason": REASON, "expected_version": None}}),
    ("DELETE", f"{SETTINGS}/venues/F303", {"params": {"expected_version": 1, "reason": REASON}}),
]


@pytest.mark.parametrize(("method", "url", "kwargs"), WRITES)
def test_writes_need_a_token(client, store, method, url, kwargs) -> None:
    response = client.request(method, url, **kwargs)
    assert response.status_code == 401
    assert store.commits == 0


def test_bad_input_without_token_is_401_not_422(client) -> None:
    assert client.put(f"{SETTINGS}/default", json={"percentage": "x"}).status_code == 401


@pytest.mark.parametrize("role", sorted(ROLE_PERMISSIONS))
@pytest.mark.parametrize("permissions", EXAMINATION_PERMISSION_SETS)
@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize(("method", "url", "kwargs"), WRITES)
def test_seating_writes_require_manage_for_every_role_and_permission_set(
    tokens, identity, client, store, role, permissions, dry_run, method, url, kwargs
) -> None:
    if method == "DELETE":
        # Give DELETE an existing override so an allowed call can succeed.
        setup = _headers(tokens, identity, "EXAMINATION_ADMIN")
        assert client.put(url, headers=setup, json={
            "percentage": 40, "reason": REASON, "expected_version": None,
        }).status_code == 200
    headers = _headers(tokens, identity, role, permissions=permissions)
    rules_before = list(store.committed_rules)
    history_before = list(store.committed_history)
    commits_before = store.commits
    params = {**kwargs.get("params", {}), "dry_run": dry_run}
    response = client.request(method, url, headers=headers, **{**kwargs, "params": params})
    allowed = "examinations:manage" in permissions
    assert response.status_code == (200 if allowed else 403)
    if not allowed or dry_run:
        assert store.commits == commits_before
        assert store.committed_rules == rules_before
        assert store.committed_history == history_before
    else:
        assert store.commits == commits_before + 1


@pytest.mark.parametrize("role", ["SUPER_ADMIN", "ACADEMIC_ADMIN", "VENUE_MANAGER", "LECTURER", "STUDENT"])
def test_other_roles_cannot_read_or_change(tokens, identity, client, store, role) -> None:
    headers = _headers(tokens, identity, role)
    assert client.get(SETTINGS, headers=headers).status_code == 403
    assert client.put(f"{SETTINGS}/default", headers=headers, json=_default()).status_code == 403
    assert store.commits == 0


@pytest.mark.parametrize(("method", "url", "kwargs"), WRITES)
def test_manage_in_another_institution_does_not_count(tokens, identity, client, store, method, url, kwargs) -> None:
    headers = _headers(tokens, identity, "EXAMINATION_ADMIN", institution=OTHER_INSTITUTION)
    assert client.request(method, url, headers=headers, **kwargs).status_code == 403
    assert store.commits == 0


@pytest.mark.parametrize(("method", "url", "kwargs"), WRITES)
def test_manage_permission_cannot_be_borrowed_from_another_institution(
    tokens, identity, client, store, method, url, kwargs
) -> None:
    sub = identity.register({
        INSTITUTION: (["EXAMINATION_ADMIN"], ["examinations:read"]),
        OTHER_INSTITUTION: (["EXAMINATION_ADMIN"], ["examinations:manage"]),
    })
    headers = {"Authorization": f"Bearer {tokens.access_token(sub)}"}
    assert client.request(method, url, headers=headers, **kwargs).status_code == 403
    assert store.commits == 0


@pytest.mark.parametrize(("method", "url", "kwargs"), WRITES)
def test_expired_token_cannot_change_seating(tokens, client, store, method, url, kwargs) -> None:
    headers = {"Authorization": f"Bearer {tokens.access_token(expires_in=-120)}"}
    assert client.request(method, url, headers=headers, **kwargs).status_code == 401
    assert store.commits == 0


def test_role_headers_are_ignored(tokens, identity, client, store) -> None:
    headers = _headers(tokens, identity, "LECTURER")
    headers |= {"X-Role": "EXAMINATION_ADMIN", "X-Permissions": "examinations:manage"}
    assert client.put(f"{SETTINGS}/default", headers=headers, json=_default()).status_code == 403


def test_writes_always_recheck_permissions(client, identity, admin) -> None:
    client.get(SETTINGS, headers=admin)
    client.put(f"{SETTINGS}/default", headers=admin, json=_default(60, 1))
    client.put(f"{SETTINGS}/default", headers=admin, json=_default(70, 2))
    assert len(identity.requests) == 3  # the read may be cached; each write asks the Identity Service


def test_revoked_manage_permission_is_not_reused_from_read_cache(client, identity, admin, store) -> None:
    assert client.get(SETTINGS, headers=admin).status_code == 200
    account = next(iter(identity.accounts.values()))
    roles, permissions = account["institutions"][INSTITUTION]
    account["institutions"][INSTITUTION] = (roles, [p for p in permissions if p != "examinations:manage"])
    assert client.put(f"{SETTINGS}/default", headers=admin, json=_default()).status_code == 403
    assert len(identity.requests) == 2
    assert store.commits == 0 and len(store.committed_history) == 1


def test_identity_service_down_fails_closed(client, identity, admin, store) -> None:
    identity.down = True
    assert client.put(f"{SETTINGS}/default", headers=admin, json=_default()).status_code == 503
    assert store.commits == 0


# --- Default rule ------------------------------------------------------------------------------------


def test_dry_run_previews_without_saving(client, admin, store) -> None:
    response = client.put(f"{SETTINGS}/default", headers=admin, params={"dry_run": "true"}, json=_default(40))
    assert response.status_code == 200
    body = response.json()
    assert body["dry_run"] is True and body["changed"] is True and body["history_id"] is None
    assert body["old_percentage"] == 50 and body["new_percentage"] == 40 and body["version"] == 2
    assert len(body["affected_venues"]) == 62
    assert body["total_exam_capacity_after"] == sum(c * 40 // 100 for c in _venues().values())
    # Nothing saved.
    assert store.commits == 0 and len(store.committed_history) == 1
    assert client.get(SETTINGS, headers=admin).json()["default"]["percentage"] == 50


def test_change_default_is_saved_with_history(client, identity, admin, store) -> None:
    response = client.put(f"{SETTINGS}/default", headers=admin, json=_default(60))
    body = response.json()
    assert response.status_code == 200
    assert body["dry_run"] is False and body["action"] == "update" and body["version"] == 2
    assert body["changed_by"] == _user_id(identity, admin)
    settings = client.get(SETTINGS, headers=admin).json()
    assert settings["default"]["percentage"] == 60 and settings["default"]["version"] == 2
    assert settings["default"]["updated_by"] == _user_id(identity, admin)
    latest = settings["history"][0]
    assert latest == {
        "history_id": body["history_id"], "action": "update", "scope": "default", "venue_id": None,
        "old_percentage": 50, "new_percentage": 60, "version": 2, "changed_by": _user_id(identity, admin),
        "changed_at": latest["changed_at"], "reason": REASON,
    }
    assert settings["ruleset_version"] == body["history_id"]
    f303 = next(v for v in client.get(f"{API}/venues", headers=admin).json() if v["venue_id"] == "F303")
    assert f303["exam_capacity"] == 144 and f303["exam_seating_percentage"] == 60


def test_same_value_is_a_no_op(client, admin, store) -> None:
    body = client.put(f"{SETTINGS}/default", headers=admin, json=_default(50)).json()
    assert body["changed"] is False and body["action"] is None and body["affected_venues"] == []
    assert store.commits == 0 and len(store.committed_history) == 1


def test_stale_version_is_rejected(client, admin, store) -> None:
    assert client.put(f"{SETTINGS}/default", headers=admin, json=_default(60, 1)).status_code == 200
    # A second admin still holds version 1.
    response = client.put(f"{SETTINGS}/default", headers=admin, json=_default(70, 1))
    assert response.status_code == 409
    assert "Current version: 2" in response.json()["detail"]
    assert client.get(SETTINGS, headers=admin).json()["default"]["percentage"] == 60
    assert len(store.committed_history) == 2


def test_dry_run_also_checks_the_version(client, admin) -> None:
    response = client.put(f"{SETTINGS}/default", headers=admin, params={"dry_run": True}, json=_default(60, 5))
    assert response.status_code == 409


# --- Venue overrides -----------------------------------------------------------------------------------


def test_venue_override_lifecycle_and_history(client, admin, store) -> None:
    url = f"{SETTINGS}/venues/F303"
    created = client.put(url, headers=admin, json={"percentage": 70, "reason": REASON, "expected_version": None})
    assert created.status_code == 200 and created.json()["action"] == "create" and created.json()["version"] == 1
    assert [v["venue_id"] for v in created.json()["affected_venues"]] == ["F303"]

    # Creating again (stale "it does not exist" view) is a conflict.
    again = client.put(url, headers=admin, json={"percentage": 80, "reason": REASON, "expected_version": None})
    assert again.status_code == 409 and "Current version: 1" in again.json()["detail"]

    updated = client.put(url, headers=admin, json={"percentage": 80, "reason": REASON, "expected_version": 1})
    assert updated.json()["version"] == 2 and updated.json()["old_percentage"] == 70

    # Changing the default does not affect a venue with an override.
    default = client.put(f"{SETTINGS}/default", headers=admin, json=_default(40)).json()
    assert "F303" not in {v["venue_id"] for v in default["affected_venues"]}
    assert len(default["affected_venues"]) == 61

    stale_delete = client.delete(url, headers=admin, params={"expected_version": 1, "reason": REASON})
    assert stale_delete.status_code == 409
    deleted = client.delete(url, headers=admin, params={"expected_version": 2, "reason": "Back to default"})
    assert deleted.status_code == 200 and deleted.json()["action"] == "delete"
    assert deleted.json()["affected_venues"][0]["new_percentage"] == 40

    # A re-created override never reuses an old version number (no ABA problem).
    recreated = client.put(url, headers=admin, json={"percentage": 70, "reason": REASON, "expected_version": None})
    assert recreated.json()["version"] == 3

    history = client.get(SETTINGS, headers=admin, params={"history_limit": 500}).json()["history"]
    assert [(h["action"], h["scope"], h["old_percentage"], h["new_percentage"]) for h in history] == [
        ("create", "venue", None, 70),
        ("delete", "venue", 80, None),
        ("update", "default", 50, 40),
        ("update", "venue", 70, 80),
        ("create", "venue", None, 70),
        ("create", "default", None, 50),
    ]
    assert history[1]["reason"] == "Back to default"


def test_unknown_venue_is_404(client, admin, store) -> None:
    body = {"percentage": 70, "reason": REASON, "expected_version": None}
    assert client.put(f"{SETTINGS}/venues/NOPE1", headers=admin, json=body).status_code == 404
    params = {"expected_version": 1, "reason": REASON}
    assert client.delete(f"{SETTINGS}/venues/NOPE1", headers=admin, params=params).status_code == 404
    assert client.delete(f"{SETTINGS}/venues/F303", headers=admin, params=params).status_code == 404  # no override
    assert store.commits == 0


@pytest.mark.parametrize("code", ["F1301+F1302", "Robotics Lab", "CyberSecLab"])
def test_special_venue_codes(client, admin, code) -> None:
    response = client.put(f"{SETTINGS}/venues/{quote(code)}", headers=admin,
                          json={"percentage": 60, "reason": REASON, "expected_version": None})
    assert response.status_code == 200 and response.json()["venue_id"] == code


def test_zero_capacity_marks_venue_unavailable(client, admin) -> None:
    small = min(SOURCE._venues, key=lambda v: v.normal_capacity)
    assert small.normal_capacity < 100
    body = client.put(f"{SETTINGS}/venues/{quote(small.venue_id)}", headers=admin,
                      json={"percentage": 1, "reason": REASON, "expected_version": None}).json()
    assert body["newly_unavailable_venues"] == [small.venue_id]
    assert body["affected_venues"][0]["new_exam_capacity"] == 0
    assert body["affected_venues"][0]["available_for_exams"] is False
    venue = next(v for v in client.get(f"{API}/venues", headers=admin).json() if v["venue_id"] == small.venue_id)
    assert venue["exam_capacity"] == 0 and venue["available_for_exams"] is False
    assert client.get(SETTINGS, headers=admin).json()["unavailable_venues"] == [small.venue_id]


def test_full_capacity_is_allowed(client, admin) -> None:
    body = client.put(f"{SETTINGS}/default", headers=admin, json=_default(100)).json()
    assert body["total_exam_capacity_after"] == sum(_venues().values())


# --- Validation ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"percentage": 0, "reason": REASON, "expected_version": 1},
        {"percentage": 101, "reason": REASON, "expected_version": 1},
        {"percentage": -1, "reason": REASON, "expected_version": 1},
        {"percentage": 50.5, "reason": REASON, "expected_version": 1},
        {"percentage": 60.0, "reason": REASON, "expected_version": 1},  # whole numbers only
        {"percentage": "60", "reason": REASON, "expected_version": 1},
        {"percentage": True, "reason": REASON, "expected_version": 1},
        {"percentage": None, "reason": REASON, "expected_version": 1},
        {"reason": REASON, "expected_version": 1},
        {"percentage": 60, "expected_version": 1},
        {"percentage": 60, "reason": "abc", "expected_version": 1},
        {"percentage": 60, "reason": "x" * 501, "expected_version": 1},
        {"percentage": 60, "reason": " leading space", "expected_version": 1},
        {"percentage": 60, "reason": "line\nbreak in log", "expected_version": 1},
        {"percentage": 60, "reason": "tab\there", "expected_version": 1},
        {"percentage": 60, "reason": REASON},
        {"percentage": 60, "reason": REASON, "expected_version": 0},
        {"percentage": 60, "reason": REASON, "expected_version": None},
        {"percentage": 60, "reason": REASON, "expected_version": "1"},
        {"percentage": 60, "reason": REASON, "expected_version": 1, "venue_id": "F303"},  # unknown field
        {"percentage": 60, "reason": REASON, "expected_version": 1, "updated_by": "someone"},
    ],
)
def test_invalid_default_input_is_422(client, admin, store, payload) -> None:
    assert client.put(f"{SETTINGS}/default", headers=admin, json=payload).status_code == 422
    assert store.commits == 0


@pytest.mark.parametrize(
    ("method", "url", "kwargs"),
    [
        ("PUT", f"{SETTINGS}/venues/F303", {"json": {"percentage": 60, "reason": REASON}}),  # version key required
        ("PUT", f"{SETTINGS}/venues/bad;drop", {"json": {"percentage": 60, "reason": REASON, "expected_version": None}}),
        ("PUT", f"{SETTINGS}/venues/%20F303", {"json": {"percentage": 60, "reason": REASON, "expected_version": None}}),
        ("PUT", f"{SETTINGS}/default", {"content": "not json", "headers": {"Content-Type": "application/json"}}),
        ("DELETE", f"{SETTINGS}/venues/F303", {"params": {"reason": REASON}}),
        ("DELETE", f"{SETTINGS}/venues/F303", {"params": {"expected_version": 1}}),
        ("DELETE", f"{SETTINGS}/venues/F303", {"params": {"expected_version": 0, "reason": REASON}}),
        ("DELETE", f"{SETTINGS}/venues/F303", {"params": {"expected_version": 1, "reason": "a\nb c d e"}}),
        ("PUT", f"{SETTINGS}/default", {"json": _default(), "params": {"dry_run": "maybe"}}),
        ("GET", SETTINGS, {"params": {"history_limit": 501}}),
        ("GET", SETTINGS, {"params": {"history_limit": -1}}),
    ],
)
def test_other_invalid_input_is_422(client, admin, store, method, url, kwargs) -> None:
    headers = admin | kwargs.pop("headers", {})
    assert client.request(method, url, headers=headers, **kwargs).status_code == 422
    assert store.commits == 0


# --- Rollback ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("failing_step", ["update_rule", "add_history", "commit"])
def test_failed_change_is_rolled_back(tokens, identity, store, admin, failing_step) -> None:
    client = _client(tokens, identity, store, raise_server_exceptions=False)
    store.fail_on = failing_step
    assert client.put(f"{SETTINGS}/default", headers=admin, json=_default(60)).status_code == 500
    store.fail_on = None
    # Neither the rule nor a history row was kept.
    assert [r.percentage for r in store.committed_rules] == [50]
    assert len(store.committed_history) == 1
    default = client.get(SETTINGS, headers=admin).json()["default"]
    assert (default["percentage"], default["version"]) == (50, 1)
    # The next change works normally.
    assert client.put(f"{SETTINGS}/default", headers=admin, json=_default(60)).json()["version"] == 2


def test_failed_override_creation_is_rolled_back(tokens, identity, store, admin) -> None:
    client = _client(tokens, identity, store, raise_server_exceptions=False)
    store.fail_on = "add_history"
    body = {"percentage": 70, "reason": REASON, "expected_version": None}
    assert client.put(f"{SETTINGS}/venues/F303", headers=admin, json=body).status_code == 500
    store.fail_on = None
    assert client.get(SETTINGS, headers=admin).json()["venue_overrides"] == []


# --- Logging, CORS and documentation -------------------------------------------------------------------------------


def test_changes_are_logged_without_tokens(client, identity, admin, caplog, monkeypatch) -> None:
    # Alembic's fileConfig (run by the migration tests) disables existing loggers.
    monkeypatch.setattr(logging.getLogger("app.seating.service"), "disabled", False)
    with caplog.at_level(logging.INFO, logger="app.seating.service"):
        client.put(f"{SETTINGS}/default", headers=admin, json=_default(60))
        client.put(f"{SETTINGS}/default", headers=admin, params={"dry_run": True}, json=_default(70, 2))
    messages = [r.getMessage() for r in caplog.records if r.name == "app.seating.service"]
    assert len(messages) == 1  # dry runs are not logged as changes
    assert "update" in messages[0] and "50% -> 60%" in messages[0] and _user_id(identity, admin) in messages[0]
    assert admin["Authorization"].removeprefix("Bearer ") not in caplog.text


def test_cors_allows_the_settings_methods(client) -> None:
    response = client.options(f"{SETTINGS}/default", headers={
        "Origin": "http://localhost:3000", "Access-Control-Request-Method": "PUT"})
    assert response.status_code == 200 and "PUT" in response.headers["access-control-allow-methods"]


def test_openapi_marks_settings_routes_as_protected(client) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    for path, method in [(SETTINGS, "get"), (f"{SETTINGS}/default", "put"),
                         (f"{SETTINGS}/venues/{{venue_id}}", "put"), (f"{SETTINGS}/venues/{{venue_id}}", "delete")]:
        assert paths[path][method]["security"] == [{"BearerToken": []}]


# --- Backward compatibility of GET /venues ------------------------------------------------------------------


OLD_VENUE_FIELDS = {"venue_id": str, "venue_name": str, "venue_type": str, "building": str, "floor": int,
                    "normal_capacity": int, "exam_capacity": int}


def test_venues_keep_every_old_field_with_the_same_values(client, admin) -> None:
    venues = client.get(f"{API}/venues", headers=admin).json()
    by_id = {v.venue_id: v for v in SOURCE._venues}
    assert [v["venue_id"] for v in venues] == sorted(by_id)  # same order as before
    for venue in venues:
        old = by_id[venue["venue_id"]]
        for field, kind in OLD_VENUE_FIELDS.items():
            assert type(venue[field]) is kind
        assert {f: venue[f] for f in OLD_VENUE_FIELDS} == {
            "venue_id": old.venue_id, "venue_name": old.venue_name, "venue_type": old.venue_type,
            "building": old.building, "floor": old.floor, "normal_capacity": old.normal_capacity,
            "exam_capacity": old.exam_capacity,  # at the 50% default: identical to the old answer
        }
    filtered = client.get(f"{API}/venues", headers=admin, params={"venue_type": "computer_lab"}).json()
    assert {v["venue_type"] for v in filtered} == {"computer_lab"}


def test_exam_capacity_is_the_effective_capacity(client, admin) -> None:
    client.put(f"{SETTINGS}/default", headers=admin, json=_default(73))
    client.put(f"{SETTINGS}/venues/F303", headers=admin,
               json={"percentage": 99, "reason": REASON, "expected_version": None})
    for venue in client.get(f"{API}/venues", headers=admin).json():
        percentage = 99 if venue["venue_id"] == "F303" else 73
        assert venue["exam_seating_percentage"] == percentage
        assert venue["exam_capacity"] == venue["normal_capacity"] * percentage // 100
        assert venue["available_for_exams"] is (venue["exam_capacity"] > 0)
        assert venue["seed_exam_capacity"] == venue["normal_capacity"] // 2  # legacy value only
