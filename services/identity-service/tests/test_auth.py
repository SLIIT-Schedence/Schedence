"""Authentication and authorisation dependencies (locally signed JWTs, fake repositories)."""

from __future__ import annotations

import time
import uuid

import httpx
import pytest
from fastapi import Depends, FastAPI

from app.api.dependencies import get_current_user, require_institution_scope, require_permissions, require_roles
from app.core.errors import install_exception_handlers
from app.core.principal import Grant, Principal
from app.models import ACCOUNT_DISABLED
from app.rbac.catalog import ROLE_CODES
from tests.conftest import INSTITUTION, OTHER_INSTITUTION


async def test_valid_access_token_resolves_current_user(client: httpx.AsyncClient, make_account) -> None:
    admin = make_account("admin@sliit.example", "SUPER_ADMIN")
    response = await client.get("/api/v1/identity/me", headers=admin.headers)
    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "admin@sliit.example"
    assert [r["role_code"] for r in body["roles"]] == ["SUPER_ADMIN"]


async def test_missing_token_is_401(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/identity/me")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"
    assert response.headers["www-authenticate"].startswith("Bearer")


async def test_invalid_token_is_rejected(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/identity/me", headers={"Authorization": "Bearer not.a.jwt"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_TOKEN"


async def test_expired_token_is_rejected(client: httpx.AsyncClient, make_account, tokens) -> None:
    admin = make_account("admin@sliit.example", "SUPER_ADMIN")
    expired = tokens.access_token(admin.user.cognito_sub, iat=int(time.time()) - 7200, exp=int(time.time()) - 3600)
    response = await client.get("/api/v1/identity/me", headers={"Authorization": f"Bearer {expired}"})
    assert response.status_code == 401
    assert "expired" in response.json()["error"]["message"]


async def test_wrong_issuer_is_rejected(client: httpx.AsyncClient, make_account, tokens) -> None:
    admin = make_account("admin@sliit.example", "SUPER_ADMIN")
    forged = tokens.access_token(admin.user.cognito_sub, iss="https://cognito-idp.ap-south-1.amazonaws.com/ap-south-1_Other")
    response = await client.get("/api/v1/identity/me", headers={"Authorization": f"Bearer {forged}"})
    assert response.status_code == 401


async def test_wrong_app_client_is_rejected(client: httpx.AsyncClient, make_account, tokens) -> None:
    admin = make_account("admin@sliit.example", "SUPER_ADMIN")
    other_client = tokens.access_token(admin.user.cognito_sub, client_id="anotherappclient0000000000")
    response = await client.get("/api/v1/identity/me", headers={"Authorization": f"Bearer {other_client}"})
    assert response.status_code == 401


async def test_id_token_is_rejected_for_api_calls(client: httpx.AsyncClient, make_account, tokens) -> None:
    admin = make_account("admin@sliit.example", "SUPER_ADMIN")
    response = await client.get("/api/v1/identity/me", headers={"Authorization": f"Bearer {tokens.id_token(admin.user.cognito_sub)}"})
    assert response.status_code == 401
    assert "access token is required" in response.json()["error"]["message"]


async def test_disabled_account_is_rejected(client: httpx.AsyncClient, make_account) -> None:
    lecturer = make_account("lecturer@sliit.example", "LECTURER", status=ACCOUNT_DISABLED)
    response = await client.get("/api/v1/identity/me", headers=lecturer.headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ACCOUNT_DISABLED"


async def test_valid_token_without_local_account_is_rejected(client: httpx.AsyncClient, tokens) -> None:
    response = await client.get("/api/v1/identity/me", headers={"Authorization": f"Bearer {tokens.access_token(str(uuid.uuid4()))}"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ACCOUNT_NOT_PROVISIONED"


async def test_role_headers_from_the_client_are_ignored(client: httpx.AsyncClient, make_account) -> None:
    lecturer = make_account("lecturer@sliit.example", "LECTURER")
    spoofed = {**lecturer.headers, "X-Role": "SUPER_ADMIN", "X-Roles": "SUPER_ADMIN", "X-Permissions": "identity:users:create"}
    response = await client.post("/api/v1/identity/users", headers=spoofed, json={
        "email": "new@sliit.example", "full_name": "New User", "role_code": "STUDENT", "institution_id": str(INSTITUTION)})
    assert response.status_code == 403


async def test_me_permissions_lists_effective_permissions_per_institution(client: httpx.AsyncClient, make_account) -> None:
    user = make_account("multi@sliit.example", ("ACADEMIC_ADMIN", INSTITUTION), ("LECTURER", OTHER_INSTITUTION))
    body = (await client.get("/api/v1/identity/me/permissions", headers=user.headers)).json()
    by_institution = {i["institution_id"]: i for i in body["institutions"]}
    assert by_institution[str(INSTITUTION)]["roles"] == ["ACADEMIC_ADMIN"]
    assert "teaching:generate" in by_institution[str(INSTITUTION)]["permissions"]
    assert by_institution[str(OTHER_INSTITUTION)]["permissions"] == []
    assert body["cognito_sub"] == user.user.cognito_sub


@pytest.mark.parametrize("role", sorted(ROLE_CODES))
async def test_examination_permission_contract_for_each_role(client: httpx.AsyncClient, make_account, role) -> None:
    user = make_account("role@sliit.example", role)
    response = await client.get("/api/v1/identity/me/permissions", headers=user.headers)
    assert response.status_code == 200
    institution = next(i for i in response.json()["institutions"] if i["institution_id"] == str(INSTITUTION))
    expected = {"examinations:read", "examinations:manage", "examinations:generate", "examinations:approve"}
    assert {p for p in institution["permissions"] if p.startswith("examinations:")} == (
        expected if role == "EXAMINATION_ADMIN" else set()
    )


async def test_examination_permissions_do_not_cross_institutions(client: httpx.AsyncClient, make_account) -> None:
    user = make_account("exam@sliit.example", ("EXAMINATION_ADMIN", INSTITUTION), ("LECTURER", OTHER_INSTITUTION))
    response = await client.get("/api/v1/identity/me/permissions", headers=user.headers)
    assert response.status_code == 200
    permissions = {i["institution_id"]: set(i["permissions"]) for i in response.json()["institutions"]}
    assert permissions[str(INSTITUTION)] == {
        "examinations:read", "examinations:manage", "examinations:generate", "examinations:approve",
        "academic:staff:read", "academic:workload:read", "venues:read",
    }
    assert permissions[str(OTHER_INSTITUTION)] == set()


def _app_with(principal: Principal) -> FastAPI:
    app = FastAPI()
    install_exception_handlers(app)

    @app.get("/scoped/{institution_id}", dependencies=[Depends(require_institution_scope(permission="venues:manage"))])
    async def scoped(institution_id: uuid.UUID) -> dict:
        return {"ok": True}

    @app.get("/roles-only", dependencies=[Depends(require_roles("VENUE_MANAGER", "SUPER_ADMIN"))])
    async def roles_only() -> dict:
        return {"ok": True}

    @app.get("/perms-only", dependencies=[Depends(require_permissions("venues:manage", "venues:read"))])
    async def perms_only() -> dict:
        return {"ok": True}

    app.dependency_overrides[get_current_user] = lambda: principal
    return app


async def test_require_dependencies_are_deny_by_default() -> None:
    venue_manager = Principal.from_grants(user_id=uuid.uuid4(), cognito_sub="s", email="v@x", full_name="V",
                                          grants=[Grant("VENUE_MANAGER", INSTITUTION, frozenset({"venues:read", "venues:manage"}))])
    nobody = Principal.from_grants(user_id=uuid.uuid4(), cognito_sub="t", email="n@x", full_name="N", grants=[])

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app_with(venue_manager)), base_url="http://t") as http:
        assert (await http.get(f"/scoped/{INSTITUTION}")).status_code == 200
        assert (await http.get(f"/scoped/{OTHER_INSTITUTION}")).status_code == 403
        assert (await http.get("/roles-only")).status_code == 200
        assert (await http.get("/perms-only")).status_code == 200

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app_with(nobody)), base_url="http://t") as http:
        for path in (f"/scoped/{INSTITUTION}", "/roles-only", "/perms-only"):
            assert (await http.get(path)).status_code == 403


def test_principal_permission_evaluation() -> None:
    principal = Principal.from_grants(user_id=uuid.uuid4(), cognito_sub="s", email="e", full_name="n", grants=[
        Grant("SUPER_ADMIN", INSTITUTION, frozenset({"identity:users:create"})),
        Grant("LECTURER", OTHER_INSTITUTION, frozenset()),
    ])
    assert principal.has_permission("identity:users:create")
    assert principal.has_permission("identity:users:create", INSTITUTION)
    assert not principal.has_permission("identity:users:create", OTHER_INSTITUTION)
    assert not principal.has_permission("anything:else")
    assert principal.institutions_with("identity:users:create") == {INSTITUTION}
    assert principal.has_role("LECTURER", OTHER_INSTITUTION) and not principal.has_role("LECTURER", INSTITUTION)
