"""Authentication and permission checks, end to end through the API.

Real RS256 tokens (auth-core TokenFactory) and a fake Identity Service: the full
chain token -> verification -> permissions -> route is exercised without AWS.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_scheduling_settings_store, get_seating_store, get_source
from app.auth.settings import ExaminationAuthSettings, load_auth_settings
from app.main import create_app
from app.scheduling_settings.memory import MemorySchedulingSettingsStore
from app.seating.memory import MemorySeatingStore
from app.seed.generator import generate_dataset
from app.sources import DatasetSource
from auth_support import (
    EXAMINATION_PERMISSION_SETS,
    INSTITUTION,
    OTHER_INSTITUTION,
    ROLE_PERMISSIONS,
    FakeIdentityService,
    make_auth_context,
    own_dataset,
)
from schedence_auth import ConfigurationError
from schedence_auth.testing import SigningKey, TokenFactory

API = "/api/v1"
SOURCE = DatasetSource(generate_dataset())
# The imported dataset: only the institution that owns it may read it.
DATASET_ENDPOINTS = [
    f"{API}/modules", f"{API}/modules/IT3041", f"{API}/students", f"{API}/students/IT30129034",
    f"{API}/registrations", f"{API}/venues", f"{API}/venue-blocks", f"{API}/exam-periods",
    f"{API}/exam-periods/PP1-2026-S2-FINAL", f"{API}/exam-periods/PP1-2026-S2-FINAL/modules",
    f"{API}/settings/exam-seating",
]
# Stored per institution: every institution reads its own.
SCHEDULING_SETTINGS = f"{API}/settings/scheduling"
DATA_ENDPOINTS = DATASET_ENDPOINTS + [SCHEDULING_SETTINGS]
STUDENT_DATA = [f"{API}/students", f"{API}/students/IT30129034", f"{API}/registrations"]


@pytest.fixture
def tokens() -> TokenFactory:
    return TokenFactory()


@pytest.fixture
def identity() -> FakeIdentityService:
    return FakeIdentityService()


class _Untouchable:
    """A data source that fails loudly: proves a rejected request never reaches the data."""

    def __getattr__(self, name):
        raise AssertionError(f"data source used by an unauthorised request ({name})")


def _client(tokens, identity, source=SOURCE, **settings) -> TestClient:
    app = own_dataset(create_app(make_auth_context(tokens, identity, **settings)))
    app.dependency_overrides[get_source] = lambda: source
    app.dependency_overrides[get_seating_store] = MemorySeatingStore
    app.dependency_overrides[get_scheduling_settings_store] = MemorySchedulingSettingsStore
    return TestClient(app)


@pytest.fixture
def client(tokens, identity) -> TestClient:
    return _client(tokens, identity)


def _headers(tokens: TokenFactory, identity: FakeIdentityService, *roles, institution=INSTITUTION, status="ACTIVE",
             permissions: list[str] | None = None) -> dict[str, str]:
    perms = permissions if permissions is not None else sorted({p for r in roles for p in ROLE_PERMISSIONS[r]})
    sub = identity.register({institution: (list(roles), perms)}, status=status)
    return {"Authorization": f"Bearer {tokens.access_token(sub)}"}


# --- Public endpoints ------------------------------------------------------------------


def test_health_is_public(client) -> None:
    assert client.get("/health").status_code == 200


def test_database_health_is_public(client) -> None:
    class _Ok:
        async def execute(self, statement):
            class R:
                def scalar_one(self):
                    return 1
            return R()

    client.app.state.engine = _Ok()
    assert client.get("/health/db").json()["status"] == "healthy"


# --- No / bad token -> 401 -----------------------------------------------------------------


@pytest.mark.parametrize("url", DATA_ENDPOINTS)
def test_data_endpoints_need_a_token(tokens, identity, url) -> None:
    response = _client(tokens, identity, source=_Untouchable()).get(url)
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"].startswith("Bearer")
    assert identity.requests == []  # nothing forwarded without a token


def test_unauthenticated_request_is_rejected_before_input_validation(tokens, identity) -> None:
    # 401, not 422: an anonymous caller learns nothing about the API's inputs.
    assert _client(tokens, identity).get(f"{API}/students?limit=99999").status_code == 401


@pytest.mark.parametrize("make_token", [
    lambda t: "not-a-jwt",
    lambda t: t.id_token(),  # ID token instead of access token
    lambda t: t.access_token(expires_in=-120),  # expired
    lambda t: t.access_token(client_id="someotherclient00000000000"),  # another app client
    lambda t: t.access_token(key=SigningKey.generate()),  # signed by an unknown key
    lambda t: t.access_token(iss="https://evil.example.com/pool"),  # wrong issuer
])
def test_invalid_tokens_are_rejected(tokens, identity, make_token) -> None:
    token = make_token(tokens)
    response = _client(tokens, identity, source=_Untouchable()).get(f"{API}/students",
                                                                    headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert token not in response.text
    assert identity.requests == []  # an unverified token is never forwarded


# --- Role matrix --------------------------------------------------------------------------------


@pytest.mark.parametrize("url", DATA_ENDPOINTS)
def test_examination_admin_can_read_every_endpoint(client, tokens, identity, url) -> None:
    assert client.get(url, headers=_headers(tokens, identity, "EXAMINATION_ADMIN")).status_code == 200


@pytest.mark.parametrize("role", ["SUPER_ADMIN", "ACADEMIC_ADMIN", "VENUE_MANAGER", "LECTURER", "STUDENT"])
@pytest.mark.parametrize("url", DATA_ENDPOINTS)
def test_other_roles_are_denied(tokens, identity, role, url) -> None:
    client = _client(tokens, identity, source=_Untouchable())
    response = client.get(url, headers=_headers(tokens, identity, role))
    assert response.status_code == 403
    assert "student_id" not in response.text and "IT3" not in response.text


@pytest.mark.parametrize("role", sorted(ROLE_PERMISSIONS))
@pytest.mark.parametrize("permissions", EXAMINATION_PERMISSION_SETS)
def test_get_endpoints_require_read_for_every_role_and_permission_set(tokens, identity, role, permissions) -> None:
    # Role labels never bypass (or replace) permissions returned by Identity.
    allowed = "examinations:read" in permissions
    client = _client(tokens, identity, source=SOURCE if allowed else _Untouchable())
    headers = _headers(tokens, identity, role, permissions=permissions)
    for url in DATA_ENDPOINTS:
        assert client.get(url, headers=headers).status_code == (200 if allowed else 403), url


@pytest.mark.parametrize("url", DATASET_ENDPOINTS)
def test_permission_in_another_institution_does_not_count(tokens, identity, url) -> None:
    headers = _headers(tokens, identity, "EXAMINATION_ADMIN", institution=OTHER_INSTITUTION)
    response = _client(tokens, identity, source=_Untouchable()).get(url, headers=headers)
    assert response.status_code == 403
    assert response.json()["detail"] == "This institution has no examination dataset in this service."


def test_another_institution_reads_only_its_own_scheduling_settings(client, tokens, identity) -> None:
    headers = _headers(tokens, identity, "EXAMINATION_ADMIN", institution=OTHER_INSTITUTION)
    response = client.get(SCHEDULING_SETTINGS, headers=headers)
    assert response.status_code == 200 and response.json()["institution_id"] == str(OTHER_INSTITUTION)


def test_read_permission_cannot_be_borrowed_from_another_institution(tokens, identity) -> None:
    sub = identity.register({
        INSTITUTION: (["EXAMINATION_ADMIN"], ["examinations:manage"]),
        OTHER_INSTITUTION: (["EXAMINATION_ADMIN"], ["examinations:read"]),
    })
    headers = {"Authorization": f"Bearer {tokens.access_token(sub)}"}
    client = _client(tokens, identity, source=_Untouchable())
    for url in DATASET_ENDPOINTS:  # read is held only in OTHER_INSTITUTION, which does not own the dataset
        assert client.get(url, headers=headers).status_code == 403
    assert client.get(SCHEDULING_SETTINGS, headers=headers).json()["institution_id"] == str(OTHER_INSTITUTION)
    explicit = {**headers, "X-Institution-Id": str(INSTITUTION)}  # manage there, but no read
    for url in DATA_ENDPOINTS:
        assert client.get(url, headers=explicit).status_code == 403


# --- Institution of the request -------------------------------------------------------------------------------


def _two_institutions(tokens, identity, permissions=("examinations:read",)) -> dict[str, str]:
    sub = identity.register({INSTITUTION: (["EXAMINATION_ADMIN"], list(permissions)),
                             OTHER_INSTITUTION: (["EXAMINATION_ADMIN"], list(permissions))})
    return {"Authorization": f"Bearer {tokens.access_token(sub)}"}


@pytest.mark.parametrize("url", DATA_ENDPOINTS)
def test_permission_in_two_institutions_without_a_choice_is_400(tokens, identity, url) -> None:
    response = _client(tokens, identity, source=_Untouchable()).get(url, headers=_two_institutions(tokens, identity))
    assert response.status_code == 400
    assert "X-Institution-Id" in response.json()["detail"]


def test_the_institution_header_chooses_one_of_the_callers_institutions(client, tokens, identity) -> None:
    headers = _two_institutions(tokens, identity)
    mine = {**headers, "X-Institution-Id": str(INSTITUTION)}
    other = {**headers, "X-Institution-Id": str(OTHER_INSTITUTION)}
    for url in DATASET_ENDPOINTS:
        assert client.get(url, headers=mine).status_code == 200, url
        assert client.get(url, headers=other).status_code == 403, url  # OTHER_INSTITUTION does not own the dataset
    assert client.get(SCHEDULING_SETTINGS, headers=mine).json()["institution_id"] == str(INSTITUTION)
    assert client.get(SCHEDULING_SETTINGS, headers=other).json()["institution_id"] == str(OTHER_INSTITUTION)


def test_the_institution_header_is_never_trusted_without_a_permission_there(tokens, identity) -> None:
    client = _client(tokens, identity, source=_Untouchable())
    headers = _headers(tokens, identity, "EXAMINATION_ADMIN", institution=OTHER_INSTITUTION)
    for url in DATA_ENDPOINTS:
        assert client.get(url, headers={**headers, "X-Institution-Id": str(INSTITUTION)}).status_code == 403


@pytest.mark.parametrize("value", ["SLIIT", "", "1111", "11111111-1111-4111-8111-11111111111x"])
def test_a_malformed_institution_header_is_400(client, tokens, identity, value) -> None:
    headers = {**_headers(tokens, identity, "EXAMINATION_ADMIN"), "X-Institution-Id": value}
    response = client.get(f"{API}/modules", headers=headers)
    assert response.status_code == 400 and "UUID" in response.json()["detail"]


def test_without_a_dataset_owner_no_institution_reads_the_dataset(tokens, identity) -> None:
    client = _client(tokens, identity, source=_Untouchable())
    own_dataset(client.app, None)
    headers = _headers(tokens, identity, "EXAMINATION_ADMIN")
    for url in DATASET_ENDPOINTS:
        assert client.get(url, headers=headers).status_code == 403
    assert client.get(SCHEDULING_SETTINGS, headers=headers).status_code == 200  # per institution, not the dataset


def test_cors_allows_the_institution_header(client) -> None:
    response = client.options(f"{API}/modules", headers={
        "Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "authorization,x-institution-id"})
    assert response.status_code == 200
    assert "x-institution-id" in response.headers["access-control-allow-headers"].lower()


@pytest.mark.parametrize("role", sorted(ROLE_PERMISSIONS))
def test_supporting_permissions_alone_cannot_read_examination_data(tokens, identity, role) -> None:
    headers = _headers(tokens, identity, role, permissions=[
        "academic:staff:read", "academic:workload:read", "venues:read",
    ])
    client = _client(tokens, identity, source=_Untouchable())
    for url in DATA_ENDPOINTS:
        assert client.get(url, headers=headers).status_code == 403


def test_role_and_permission_headers_are_ignored(tokens, identity) -> None:
    headers = _headers(tokens, identity, "STUDENT")
    headers.update({"X-Roles": "EXAMINATION_ADMIN", "X-Permissions": "examinations:read",
                    "X-User-Role": "EXAMINATION_ADMIN"})
    assert _client(tokens, identity, source=_Untouchable()).get(f"{API}/students", headers=headers).status_code == 403


def test_student_role_cannot_see_own_or_any_student_data(tokens, identity) -> None:
    # Student self-service stays disabled until a secure user -> student ID mapping exists.
    client = _client(tokens, identity, source=_Untouchable())
    headers = _headers(tokens, identity, "STUDENT")
    for url in STUDENT_DATA:
        assert client.get(url, headers=headers).status_code == 403


# --- Account and Identity Service problems ------------------------------------------------------


def test_disabled_account_is_denied(tokens, identity) -> None:
    headers = _headers(tokens, identity, "EXAMINATION_ADMIN", status="DISABLED")
    response = _client(tokens, identity, source=_Untouchable()).get(f"{API}/venues", headers=headers)
    assert response.status_code == 403 and "disabled" in response.json()["detail"]


def test_unknown_account_is_denied(tokens, identity) -> None:
    headers = {"Authorization": f"Bearer {tokens.access_token('never-provisioned')}"}
    assert _client(tokens, identity, source=_Untouchable()).get(f"{API}/venues", headers=headers).status_code == 403


def test_identity_service_down_fails_closed(tokens, identity) -> None:
    headers = _headers(tokens, identity, "EXAMINATION_ADMIN")
    identity.down = True
    response = _client(tokens, identity, source=_Untouchable()).get(f"{API}/students", headers=headers)
    assert response.status_code == 503


@pytest.mark.parametrize("status_code", [500, 502, 404])
def test_unexpected_identity_answer_fails_closed(tokens, identity, status_code) -> None:
    headers = _headers(tokens, identity, "EXAMINATION_ADMIN")
    identity.status_override = status_code
    assert _client(tokens, identity, source=_Untouchable()).get(f"{API}/venues", headers=headers).status_code == 503


def test_identity_answer_for_another_user_is_rejected(tokens, identity) -> None:
    headers = _headers(tokens, identity, "EXAMINATION_ADMIN")
    identity.answer_for_sub = "someone-else"
    assert _client(tokens, identity, source=_Untouchable()).get(f"{API}/venues", headers=headers).status_code == 503


def test_identity_rejecting_the_token_gives_401(tokens, identity) -> None:
    headers = _headers(tokens, identity, "EXAMINATION_ADMIN")
    identity.status_override = 401
    assert _client(tokens, identity, source=_Untouchable()).get(f"{API}/venues", headers=headers).status_code == 401


def test_permissions_are_cached_briefly_for_reads(client, tokens, identity) -> None:
    headers = _headers(tokens, identity, "EXAMINATION_ADMIN")
    client.get(f"{API}/venues", headers=headers)
    client.get(f"{API}/modules", headers=headers)
    assert identity.requests == [("GET", "/api/v1/identity/me/permissions")]


# --- Documentation and configuration -------------------------------------------------------------


def test_openapi_marks_data_routes_as_protected(client) -> None:
    spec = client.get("/openapi.json").json()
    assert "BearerToken" in spec["components"]["securitySchemes"]
    for path, operations in spec["paths"].items():
        for operation in operations.values():
            if path.startswith(API + "/"):
                assert operation["security"] == [{"BearerToken": []}]
            else:
                assert "security" not in operation


def test_permission_matrix_covers_every_protected_endpoint(client) -> None:
    """Fail if an API route is added without adding it to the access tests."""
    paths = client.get("/openapi.json").json()["paths"]
    sample_values = {"module_code": "IT3041", "student_id": "IT30129034",
                     "period_code": "PP1-2026-S2-FINAL", "venue_id": "F303", "timetable_id": "1"}
    gets = {path.format(**sample_values) for path, operations in paths.items()
            if path.startswith(API + "/") and "get" in operations}
    writes = {(method.upper(), path.format(**sample_values)) for path, operations in paths.items()
              if path.startswith(API + "/") for method in operations if method != "get"}
    assert gets == set(DATA_ENDPOINTS) | {  # saved timetables; access tests: test_saved_timetables.py
        f"{API}/timetables", f"{API}/timetables/1", f"{API}/timetables/1/seats",
        f"{API}/timetables/1/history"}  # access tests: test_timetable_workflow.py
    assert writes == {
        ("PUT", f"{API}/settings/exam-seating/default"),
        ("PUT", f"{API}/settings/exam-seating/venues/F303"),
        ("DELETE", f"{API}/settings/exam-seating/venues/F303"),
        ("PUT", f"{API}/settings/scheduling"),  # access tests: test_scheduling_settings.py
        ("POST", f"{API}/exam-periods"),  # access tests: test_exam_period_management.py
        ("PUT", f"{API}/exam-periods/PP1-2026-S2-FINAL"),
        ("PUT", f"{API}/modules/IT3041/exam-details"),  # access tests: test_module_details_management.py
        ("POST", f"{API}/timetables/generate"),  # access tests: test_timetable_generation.py
        ("POST", f"{API}/timetables/drafts"),  # access tests: test_saved_timetables.py
        ("POST", f"{API}/timetables/generate/both-semesters"),  # examinations:generate; access tests: test_both_semesters.py
        ("POST", f"{API}/timetables/drafts/both-semesters"),
        ("POST", f"{API}/timetables/1/approve"),  # access tests: test_timetable_workflow.py
        ("POST", f"{API}/timetables/1/publish"),
        ("POST", f"{API}/timetables/1/regenerate"),
    }


def test_production_hides_the_docs(tokens, identity) -> None:
    client = _client(tokens, identity, app_env="production")
    for url in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(url).status_code == 404
    assert client.get("/health").status_code == 200


COGNITO_ENV = ["AWS_REGION=ap-south-1", "COGNITO_USER_POOL_ID=ap-south-1_TestPool1",
               "COGNITO_APP_CLIENT_ID=testclientid0000000000000a"]


def test_the_service_starts_without_an_institution_variable(tmp_path, monkeypatch) -> None:
    env = tmp_path / ".env"
    env.write_text("\n".join(COGNITO_ENV), encoding="utf-8")
    settings = load_auth_settings(env)
    assert "institution_id" not in ExaminationAuthSettings.model_fields and not hasattr(settings, "institution_id")
    monkeypatch.setenv("SCHEDENCE_ENV_FILE", str(env))
    app = create_app()  # settings from the environment, as run_services.py starts it
    assert TestClient(app).get("/health").status_code == 200


def test_an_old_institution_variable_is_ignored(tmp_path) -> None:
    env = tmp_path / ".env"
    env.write_text("\n".join([*COGNITO_ENV, f"SLIIT_INSTITUTION_ID={INSTITUTION}",
                              f"EXAMINATION_INSTITUTION_ID={OTHER_INSTITUTION}"]), encoding="utf-8")
    assert not hasattr(load_auth_settings(env), "institution_id")


def test_missing_settings_are_named_without_values(tmp_path) -> None:
    env = tmp_path / ".env"
    env.write_text("AWS_REGION=ap-south-1\nCOGNITO_APP_CLIENT_ID=secretish-value\n", encoding="utf-8")
    with pytest.raises(ConfigurationError) as error:
        load_auth_settings(env)
    message = str(error.value)
    assert "COGNITO_USER_POOL_ID" in message and "secretish-value" not in message


def test_error_bodies_contain_no_token(tokens, identity) -> None:
    token = tokens.access_token("never-provisioned")
    response = _client(tokens, identity, source=_Untouchable()).get(f"{API}/venues",
                                                                    headers={"Authorization": f"Bearer {token}"})
    assert token not in response.text and set(json.loads(response.text)) == {"detail"}
