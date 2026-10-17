"""System Admin: institution registry and Institution Admins (mocked Cognito + repositories).

The chain under test: a System Admin registers an institution and creates its
Institution Admin; that admin creates the institution's other users. A System
Admin never gains access inside an institution, and institution users never
gain platform access.
"""

from __future__ import annotations

import dataclasses
import uuid

import httpx
import pytest

from app.core.principal import Grant, PlatformGrant, Principal
from app.integrations.cognito import STATUS_FORCE_CHANGE_PASSWORD, CognitoUnavailableError
from app.models import ACCOUNT_ACTIVE, ACCOUNT_DISABLED, INSTITUTION_DISABLED, PROVISIONING_COMPLETED, PROVISIONING_FAILED
from tests.conftest import INSTITUTION, OTHER_INSTITUTION

PLATFORM = "/api/v1/identity/platform"
INSTITUTIONS = f"{PLATFORM}/institutions"
USERS = "/api/v1/identity/users"


@pytest.fixture
def owner(make_account):
    return make_account("owner@schedence.example", "SYSTEM_ADMIN")


def admins_url(institution: uuid.UUID | str) -> str:
    return f"{INSTITUTIONS}/{institution}/admins"


# ---------------------------------------------------------------- end-to-end chain


async def test_system_admin_onboards_an_institution_whose_admin_onboards_users(
    client: httpx.AsyncClient, owner, store, cognito, tokens
) -> None:
    created = await client.post(INSTITUTIONS, headers=owner.headers, json={"code": " nsbm ", "name": "  NSBM   Green University "})
    assert created.status_code == 201, created.text
    institution = created.json()
    assert (institution["code"], institution["name"], institution["status"]) == ("NSBM", "NSBM Green University", "ACTIVE")

    admin = await client.post(admins_url(institution["id"]), headers=owner.headers,
                              json={"email": "Admin@NSBM.example", "full_name": "NSBM Admin"})
    assert admin.status_code == 201, admin.text
    body = admin.json()
    assert body["provisioning"]["status"] == PROVISIONING_COMPLETED
    assert [(r["role_code"], r["institution_id"]) for r in body["user"]["roles"]] == [("INSTITUTION_ADMIN", institution["id"])]
    assert body["provisioning"]["requested_by_user_id"] == owner.id
    assert "platform.institution_admin_created" in store.actions()

    # The new admin signs in (Cognito would force a password change first) and creates a lecturer.
    admin_token = tokens.access_token(cognito.users["admin@nsbm.example"].sub)
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    me = (await client.get("/api/v1/identity/me", headers=admin_headers)).json()
    assert me["platform_roles"] == []
    assert me["institutions"] == [{"id": institution["id"], "code": "NSBM", "name": "NSBM Green University", "status": "ACTIVE"}]

    lecturer = await client.post(USERS, headers=admin_headers, json={
        "email": "lecturer@nsbm.example", "full_name": "NSBM Lecturer", "role_code": "LECTURER", "institution_id": institution["id"]})
    assert lecturer.status_code == 201, lecturer.text

    # ...but only in their own institution.
    elsewhere = await client.post(USERS, headers=admin_headers, json={
        "email": "x@sliit.example", "full_name": "X", "role_code": "LECTURER", "institution_id": str(INSTITUTION)})
    assert elsewhere.status_code == 403


# ---------------------------------------------------------------- separation of platform and institution access


async def test_system_admin_has_no_access_inside_institutions(client: httpx.AsyncClient, owner, make_account) -> None:
    lecturer = make_account("lecturer@sliit.example", "LECTURER")

    assert (await client.get(USERS, headers=owner.headers)).status_code == 403
    assert (await client.get(f"{USERS}/{lecturer.id}", headers=owner.headers)).status_code == 403
    assert (await client.post(USERS, headers=owner.headers, json={
        "email": "x@sliit.example", "full_name": "X", "role_code": "LECTURER", "institution_id": str(INSTITUTION)})).status_code == 403
    assert (await client.post(f"{USERS}/{lecturer.id}/disable", headers=owner.headers)).status_code == 403
    assert (await client.get("/api/v1/identity/audit-logs", headers=owner.headers)).status_code == 403
    assert (await client.get("/api/v1/identity/roles", headers=owner.headers)).status_code == 403

    permissions = (await client.get("/api/v1/identity/me/permissions", headers=owner.headers)).json()
    assert permissions["institutions"] == []  # what other services (e.g. academic data) authorise from
    assert permissions["platform"]["roles"] == ["SYSTEM_ADMIN"]
    assert "platform:institutions:create" in permissions["platform"]["permissions"]


@pytest.mark.parametrize("role", ["INSTITUTION_ADMIN", "ACADEMIC_ADMIN", "LECTURER", "STUDENT"])
async def test_institution_users_cannot_use_platform_endpoints(client: httpx.AsyncClient, make_account, role: str) -> None:
    caller = make_account("caller@sliit.example", role)
    assert (await client.get(INSTITUTIONS, headers=caller.headers)).status_code == 403
    assert (await client.post(INSTITUTIONS, headers=caller.headers, json={"code": "EVIL", "name": "Evil"})).status_code == 403
    assert (await client.post(admins_url(INSTITUTION), headers=caller.headers,
                              json={"email": "x@sliit.example", "full_name": "X"})).status_code == 403
    assert (await client.get(f"{PLATFORM}/audit-logs", headers=caller.headers)).status_code == 403


async def test_platform_role_cannot_be_granted_within_an_institution(client: httpx.AsyncClient, make_account) -> None:
    admin = make_account("admin@sliit.example", "INSTITUTION_ADMIN")
    lecturer = make_account("lecturer@sliit.example", "LECTURER")

    created = await client.post(USERS, headers=admin.headers, json={
        "email": "x@sliit.example", "full_name": "X", "role_code": "system_admin", "institution_id": str(INSTITUTION)})
    assert created.status_code == 422 and created.json()["error"]["code"] == "PLATFORM_ROLE_NOT_ASSIGNABLE"

    granted = await client.put(f"{USERS}/{lecturer.id}/roles", headers=admin.headers,
                               json={"institution_id": str(INSTITUTION), "role_codes": ["LECTURER", "SYSTEM_ADMIN"]})
    assert granted.status_code == 422 and granted.json()["error"]["code"] == "PLATFORM_ROLE_NOT_ASSIGNABLE"


async def test_institution_admin_cannot_attach_roles_to_a_system_admin_account(client: httpx.AsyncClient, owner, make_account,
                                                                             store) -> None:
    admin = make_account("admin@sliit.example", "INSTITUTION_ADMIN")
    response = await client.put(f"{USERS}/{owner.id}/roles", headers=admin.headers,
                                json={"institution_id": str(INSTITUTION), "role_codes": ["LECTURER"]})
    assert response.status_code == 403 and response.json()["error"]["code"] == "PLATFORM_ACCOUNT"
    assert store.active_role_codes(owner.user.id) == set()


def test_platform_permissions_never_satisfy_institution_checks() -> None:
    both = Principal.from_grants(
        user_id=uuid.uuid4(), cognito_sub="s", email="e", full_name="n",
        grants=[Grant("LECTURER", INSTITUTION, frozenset())],
        platform_grants=[PlatformGrant("SYSTEM_ADMIN", frozenset({"platform:institutions:create"}))],
    )
    assert both.has_platform_permission("platform:institutions:create")
    assert not both.has_permission("platform:institutions:create")
    assert not both.has_permission("platform:institutions:create", INSTITUTION)
    assert both.institution_ids == {INSTITUTION}
    assert not both.has_platform_permission("identity:users:create")


# ---------------------------------------------------------------- institution registry


async def test_institution_codes_are_unique_and_validated(client: httpx.AsyncClient, owner) -> None:
    taken = await client.post(INSTITUTIONS, headers=owner.headers, json={"code": "sliit", "name": "Duplicate"})
    assert taken.status_code == 409 and taken.json()["error"]["code"] == "INSTITUTION_CODE_TAKEN"

    for code in ("1ABC", "has space", "x"):
        assert (await client.post(INSTITUTIONS, headers=owner.headers, json={"code": code, "name": "N"})).status_code == 422
    assert (await client.post(INSTITUTIONS, headers=owner.headers,
                              json={"code": "NEW", "name": "N", "status": "DISABLED"})).status_code == 422  # no extra fields


async def test_list_search_view_and_update_institutions(client: httpx.AsyncClient, owner, store) -> None:
    listed = (await client.get(INSTITUTIONS, headers=owner.headers)).json()
    assert {i["code"] for i in listed["items"]} == {"SLIIT", "OTHER"} and listed["total"] == 2
    searched = (await client.get(INSTITUTIONS, headers=owner.headers, params={"search": "sli"})).json()
    assert [i["code"] for i in searched["items"]] == ["SLIIT"]

    url = f"{INSTITUTIONS}/{INSTITUTION}"
    assert (await client.get(url, headers=owner.headers)).json()["code"] == "SLIIT"
    assert (await client.get(f"{INSTITUTIONS}/{uuid.uuid4()}", headers=owner.headers)).status_code == 404

    renamed = await client.patch(url, headers=owner.headers, json={"name": "Sri Lanka Institute of Information Technology"})
    assert renamed.status_code == 200 and renamed.json()["name"] == "Sri Lanka Institute of Information Technology"
    assert (await client.patch(url, headers=owner.headers, json={"code": "other"})).status_code == 409
    assert (await client.patch(url, headers=owner.headers, json={})).status_code == 422

    entry = next(e for e in store.audit if e.action == "platform.institution_updated")
    assert entry.institution_id == INSTITUTION and entry.safe_metadata["fields"] == ["name"]


async def test_disabling_an_institution_suspends_its_users_until_re_enabled(client: httpx.AsyncClient, owner, make_account, store) -> None:
    admin = make_account("admin@sliit.example", "INSTITUTION_ADMIN")
    multi = make_account("multi@sliit.example", ("LECTURER", INSTITUTION), ("ACADEMIC_ADMIN", OTHER_INSTITUTION))

    disabled = await client.post(f"{INSTITUTIONS}/{INSTITUTION}/disable", headers=owner.headers, json={"reason": "contract ended"})
    assert disabled.status_code == 200 and disabled.json()["status"] == INSTITUTION_DISABLED

    assert (await client.get(USERS, headers=admin.headers)).status_code == 403
    permissions = (await client.get("/api/v1/identity/me/permissions", headers=multi.headers)).json()
    assert [i["institution_id"] for i in permissions["institutions"]] == [str(OTHER_INSTITUTION)]  # other memberships unaffected
    me = (await client.get("/api/v1/identity/me", headers=admin.headers)).json()
    assert me["institutions"][0]["status"] == INSTITUTION_DISABLED  # the account itself still signs in and sees why

    refused = await client.post(admins_url(INSTITUTION), headers=owner.headers, json={"email": "new@sliit.example", "full_name": "N"})
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "INSTITUTION_DISABLED"

    assert (await client.post(f"{INSTITUTIONS}/{INSTITUTION}/enable", headers=owner.headers)).json()["status"] == "ACTIVE"
    assert (await client.get(USERS, headers=admin.headers)).status_code == 200
    assert {"platform.institution_disabled", "platform.institution_enabled"} <= set(store.actions())


# ---------------------------------------------------------------- institution admins


async def test_admin_onboarding_failure_is_resumed_by_resending_the_request(client: httpx.AsyncClient, owner, store, cognito) -> None:
    cognito.fail("create_user", CognitoUnavailableError("EndpointConnectionError"))
    body = {"email": "admin2@sliit.example", "full_name": "Second Admin"}

    failed = await client.post(admins_url(INSTITUTION), headers=owner.headers, json=body)
    assert failed.status_code == 503 and failed.json()["error"]["code"] == "IDENTITY_PROVIDER_UNAVAILABLE"
    record = store.provisioning[uuid.UUID(failed.json()["error"]["details"]["provisioning_id"])]
    assert record.status == PROVISIONING_FAILED

    resumed = await client.post(admins_url(INSTITUTION), headers=owner.headers, json=body)
    assert resumed.status_code == 201, resumed.text
    assert resumed.json()["provisioning"]["id"] == str(record.id)
    assert resumed.json()["provisioning"]["attempt_count"] == 2


async def test_admin_creation_validates_institution_and_email(client: httpx.AsyncClient, owner, make_account) -> None:
    make_account("taken@sliit.example", "LECTURER")
    unknown = await client.post(admins_url(uuid.uuid4()), headers=owner.headers, json={"email": "a@x.example", "full_name": "A"})
    assert unknown.status_code == 404
    taken = await client.post(admins_url(INSTITUTION), headers=owner.headers, json={"email": "taken@sliit.example", "full_name": "T"})
    assert taken.status_code == 409 and taken.json()["error"]["code"] == "EMAIL_ALREADY_REGISTERED"
    own = await client.post(admins_url(INSTITUTION), headers=owner.headers, json={"email": "owner@schedence.example", "full_name": "O"})
    assert own.status_code == 409  # a System Admin account can never become an institution account


async def test_admin_list_discloses_only_the_admin_assignment(client: httpx.AsyncClient, owner, make_account) -> None:
    admin = make_account("admin@sliit.example", "INSTITUTION_ADMIN", "LECTURER", ("STUDENT", OTHER_INSTITUTION))
    make_account("lecturer@sliit.example", "LECTURER")

    listed = (await client.get(admins_url(INSTITUTION), headers=owner.headers)).json()
    assert [u["email"] for u in listed["items"]] == ["admin@sliit.example"]
    assert [(r["role_code"], r["institution_id"]) for r in listed["items"][0]["roles"]] == [("INSTITUTION_ADMIN", str(INSTITUTION))]
    assert listed["items"][0]["id"] == admin.id


async def test_system_admin_disables_and_re_enables_an_institution_admin(client: httpx.AsyncClient, owner, make_account, cognito,
                                                                       store) -> None:
    admin = make_account("admin@sliit.example", "INSTITUTION_ADMIN")  # the institution's only admin
    url = f"{admins_url(INSTITUTION)}/{admin.id}"

    disabled = await client.post(f"{url}/disable", headers=owner.headers, json={"reason": "compromised"})
    assert disabled.status_code == 200, disabled.text  # allowed even for the last admin: the System Admin can add another
    assert disabled.json()["user"]["account_status"] == ACCOUNT_DISABLED and disabled.json()["cognito_sync"] == "ok"
    assert cognito.count("disable_user") == 1 and cognito.count("global_sign_out") == 1
    assert (await client.get("/api/v1/identity/me", headers=admin.headers)).json()["error"]["code"] == "ACCOUNT_DISABLED"

    enabled = await client.post(f"{url}/enable", headers=owner.headers)
    assert enabled.json()["user"]["account_status"] == ACCOUNT_ACTIVE
    assert {"platform.institution_admin_disabled", "platform.institution_admin_enabled"} <= set(store.actions())


async def test_platform_endpoints_cannot_touch_users_who_are_not_admins_there(client: httpx.AsyncClient, owner, make_account,
                                                                            cognito) -> None:
    lecturer = make_account("lecturer@sliit.example", "LECTURER")
    other_admin = make_account("admin@other.example", ("INSTITUTION_ADMIN", OTHER_INSTITUTION))

    for user in (lecturer, other_admin):
        url = f"{admins_url(INSTITUTION)}/{user.id}"
        assert (await client.post(f"{url}/disable", headers=owner.headers)).status_code == 404
        assert (await client.delete(url, headers=owner.headers)).status_code == 404
        assert (await client.post(f"{url}/resend-invitation", headers=owner.headers)).status_code == 404
    assert cognito.count("disable_user") == 0


async def test_revoking_admin_keeps_the_account_and_history(client: httpx.AsyncClient, owner, make_account, store) -> None:
    admin = make_account("admin@sliit.example", "INSTITUTION_ADMIN", "LECTURER")

    revoked = await client.delete(f"{admins_url(INSTITUTION)}/{admin.id}", headers=owner.headers)
    assert revoked.status_code == 204
    assert store.active_role_codes(admin.user.id, INSTITUTION) == {"LECTURER"}
    row = next(a for a in store.user_roles.values() if a.user_id == admin.user.id and store.roles[a.role_id].code == "INSTITUTION_ADMIN")
    assert row.revoked_at is not None and row.revoked_by_user_id == owner.user.id
    assert store.users[admin.user.id].account_status == ACCOUNT_ACTIVE
    assert (await client.get(USERS, headers=admin.headers)).status_code == 403  # lost admin permissions at once


async def test_resend_admin_invitation(client: httpx.AsyncClient, owner, make_account, cognito) -> None:
    admin = make_account("admin@sliit.example", "INSTITUTION_ADMIN")
    url = f"{admins_url(INSTITUTION)}/{admin.id}/resend-invitation"

    done = await client.post(url, headers=owner.headers)
    assert done.status_code == 409 and done.json()["error"]["code"] == "INVITATION_NOT_APPLICABLE"  # already signed in

    cognito.users["admin@sliit.example"] = dataclasses.replace(cognito.users["admin@sliit.example"], status=STATUS_FORCE_CHANGE_PASSWORD)
    assert (await client.post(url, headers=owner.headers)).status_code == 204
    assert cognito.count("resend_invitation") == 1


# ---------------------------------------------------------------- audit


async def test_platform_audit_shows_platform_events_only_and_institutions_see_theirs(client: httpx.AsyncClient, owner,
                                                                                   make_account) -> None:
    institution_admin = make_account("admin@sliit.example", "INSTITUTION_ADMIN")
    await client.patch(f"{INSTITUTIONS}/{INSTITUTION}", headers=owner.headers, json={"name": "SLIIT Malabe"})
    await client.post(admins_url(INSTITUTION), headers=owner.headers, json={"email": "admin2@sliit.example", "full_name": "A2"})
    await client.post(USERS, headers=institution_admin.headers, json={
        "email": "l@sliit.example", "full_name": "L", "role_code": "LECTURER", "institution_id": str(INSTITUTION)})

    platform = (await client.get(f"{PLATFORM}/audit-logs", headers=owner.headers)).json()
    actions = [e["action"] for e in platform["items"]]
    assert set(actions) == {"platform.institution_updated", "platform.institution_admin_created"}

    institution = (await client.get("/api/v1/identity/audit-logs", headers=institution_admin.headers)).json()
    assert {"platform.institution_updated", "platform.institution_admin_created", "user.created"} <= {
        e["action"] for e in institution["items"]}
