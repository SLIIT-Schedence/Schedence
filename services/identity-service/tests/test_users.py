"""User onboarding, provisioning failures, status changes and role assignment (mocked Cognito + repositories)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import httpx
import pytest

from app.core.errors import ConflictError
from app.integrations.cognito import CognitoPermissionError, CognitoUnavailableError
from app.models import ACCOUNT_ACTIVE, ACCOUNT_DISABLED, PROVISIONING_COMPLETED, PROVISIONING_FAILED, Role
from app.rbac.catalog import PERMISSIONS
from app.repositories.base import PersistenceError
from app.services.users import UserManagementService
from tests.conftest import INSTITUTION, OTHER_INSTITUTION
from tests.fakes import FakeUnitOfWork

USERS = "/api/v1/identity/users"


def new_user(email: str = "lecturer1@sliit.example", role: str = "LECTURER", institution: uuid.UUID = INSTITUTION) -> dict:
    return {"email": email, "full_name": "Example Lecturer", "role_code": role, "institution_id": str(institution)}


# ---------------------------------------------------------------- creation


@pytest.mark.parametrize("role", ["ACADEMIC_ADMIN", "EXAMINATION_ADMIN", "VENUE_MANAGER", "LECTURER", "STUDENT"])
async def test_institution_admin_creates_users_for_every_role(client: httpx.AsyncClient, make_account, store, cognito, role: str) -> None:
    admin = make_account("admin@sliit.example", "INSTITUTION_ADMIN")
    response = await client.post(USERS, headers=admin.headers, json=new_user(f"{role.lower()}@sliit.example", role))

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["user"]["account_status"] == "ACTIVE"
    assert [r["role_code"] for r in body["user"]["roles"]] == [role]
    assert body["provisioning"]["status"] == PROVISIONING_COMPLETED
    assert body["linked_existing_identity"] is False
    assert cognito.count("create_user") == 1
    created = store.users[uuid.UUID(body["user"]["id"])]
    assert created.cognito_sub == cognito.users[f"{role.lower()}@sliit.example"].sub
    assert {"user.provisioning_started", "user.created", "user.role_assigned"} <= set(store.actions())


async def test_email_is_normalised_and_duplicates_are_rejected(client: httpx.AsyncClient, make_account) -> None:
    admin = make_account("admin@sliit.example", "INSTITUTION_ADMIN")
    first = await client.post(USERS, headers=admin.headers, json=new_user("  Lecturer1@SLIIT.example "))
    assert first.status_code == 201
    assert first.json()["user"]["email"] == "lecturer1@sliit.example"

    duplicate = await client.post(USERS, headers=admin.headers, json=new_user("lecturer1@sliit.example"))
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "EMAIL_ALREADY_REGISTERED"


@pytest.mark.parametrize("role", ["LECTURER", "ACADEMIC_ADMIN", "EXAMINATION_ADMIN", "VENUE_MANAGER", "STUDENT"])
async def test_users_without_permission_cannot_create_accounts(client: httpx.AsyncClient, make_account, cognito, role: str) -> None:
    caller = make_account("caller@sliit.example", role)
    response = await client.post(USERS, headers=caller.headers, json=new_user())
    assert response.status_code == 403
    assert cognito.count("create_user") == 0


async def test_institution_admin_cannot_create_users_in_another_institution(client: httpx.AsyncClient, make_account, cognito) -> None:
    admin = make_account("admin@sliit.example", "INSTITUTION_ADMIN")
    response = await client.post(USERS, headers=admin.headers, json=new_user(institution=OTHER_INSTITUTION))
    assert response.status_code == 403
    assert cognito.count("create_user") == 0


async def test_unknown_role_is_rejected(client: httpx.AsyncClient, make_account) -> None:
    admin = make_account("admin@sliit.example", "INSTITUTION_ADMIN")
    response = await client.post(USERS, headers=admin.headers, json=new_user(role="HEAD_OF_DEPARTMENT"))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "UNKNOWN_ROLE"


async def test_request_cannot_set_status_or_extra_fields(client: httpx.AsyncClient, make_account) -> None:
    admin = make_account("admin@sliit.example", "INSTITUTION_ADMIN")
    response = await client.post(USERS, headers=admin.headers, json={**new_user(), "account_status": "ACTIVE", "roles": ["INSTITUTION_ADMIN"]})
    assert response.status_code == 422


# ---------------------------------------------------------------- partial failures and retries


async def test_cognito_outage_marks_provisioning_failed_and_retry_succeeds(client: httpx.AsyncClient, make_account, store, cognito) -> None:
    admin = make_account("admin@sliit.example", "INSTITUTION_ADMIN")
    cognito.fail("create_user", CognitoUnavailableError("EndpointConnectionError"))

    failed = await client.post(USERS, headers=admin.headers, json=new_user())
    assert failed.status_code == 503
    error = failed.json()["error"]
    assert error["code"] == "IDENTITY_PROVIDER_UNAVAILABLE"
    record = store.provisioning[uuid.UUID(error["details"]["provisioning_id"])]
    assert (record.status, record.error_category, record.cognito_sub) == (PROVISIONING_FAILED, "COGNITO_UNAVAILABLE", None)
    assert store.users.keys() == {admin.user.id}  # no half-created local account

    retried = await client.post(f"/api/v1/identity/provisioning/{record.id}/retry", headers=admin.headers)
    assert retried.status_code == 200, retried.text
    assert retried.json()["provisioning"]["status"] == PROVISIONING_COMPLETED
    assert retried.json()["provisioning"]["attempt_count"] == 2


async def test_database_failure_after_cognito_create_is_recoverable_without_duplicate(
    client: httpx.AsyncClient, make_account, store, cognito
) -> None:
    admin = make_account("admin@sliit.example", "INSTITUTION_ADMIN")
    # commit 1: provisioning row; commit 2: COGNITO_CREATED; commit 3 (local account) fails.
    original_commit = FakeUnitOfWork.commit
    calls = {"n": 0}

    async def flaky_commit(self):
        calls["n"] += 1
        if calls["n"] == 3:
            self.store.restore()
            raise PersistenceError("connection timed out")
        await original_commit(self)

    FakeUnitOfWork.commit = flaky_commit
    try:
        failed = await client.post(USERS, headers=admin.headers, json=new_user())
    finally:
        FakeUnitOfWork.commit = original_commit

    assert failed.status_code == 503
    assert failed.json()["error"]["code"] == "PROVISIONING_INCOMPLETE"
    record = next(iter(store.provisioning.values()))
    assert record.status == PROVISIONING_FAILED and record.error_category == "DATABASE_ERROR"
    assert record.cognito_sub == cognito.users["lecturer1@sliit.example"].sub  # the orphan is tracked, not silent
    assert not any(u.email == "lecturer1@sliit.example" for u in store.users.values())

    # Re-submitting the same request resumes the same record; Cognito is not called again.
    resumed = await client.post(USERS, headers=admin.headers, json=new_user())
    assert resumed.status_code == 201, resumed.text
    assert resumed.json()["provisioning"]["id"] == str(record.id)
    assert cognito.count("create_user") == 1
    assert len([u for u in store.users.values() if u.email == "lecturer1@sliit.example"]) == 1


async def test_existing_cognito_identity_is_linked_not_duplicated(client: httpx.AsyncClient, make_account, store, cognito) -> None:
    admin = make_account("admin@sliit.example", "INSTITUTION_ADMIN")
    orphan = cognito.add_user("lecturer1@sliit.example", status="FORCE_CHANGE_PASSWORD")

    response = await client.post(USERS, headers=admin.headers, json=new_user())
    assert response.status_code == 201
    assert response.json()["linked_existing_identity"] is True
    assert store.users[uuid.UUID(response.json()["user"]["id"])].cognito_sub == orphan.sub
    assert len(cognito.users) == 2  # admin + the one linked identity


async def test_different_request_for_in_progress_email_conflicts(client: httpx.AsyncClient, make_account, cognito) -> None:
    admin = make_account("admin@sliit.example", "INSTITUTION_ADMIN")
    cognito.fail("create_user", CognitoUnavailableError("timeout"))
    await client.post(USERS, headers=admin.headers, json=new_user(role="LECTURER"))

    other = await client.post(USERS, headers=admin.headers, json=new_user(role="STUDENT"))
    assert other.status_code == 409
    assert other.json()["error"]["code"] == "PROVISIONING_IN_PROGRESS"


async def test_misconfigured_aws_credentials_are_reported_without_details(client: httpx.AsyncClient, make_account, cognito) -> None:
    admin = make_account("admin@sliit.example", "INSTITUTION_ADMIN")
    cognito.fail("create_user", CognitoPermissionError("ExpiredTokenException"))
    response = await client.post(USERS, headers=admin.headers, json=new_user())
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "IDENTITY_PROVIDER_MISCONFIGURED"
    assert "ExpiredToken" not in response.text


async def test_cancel_is_refused_once_a_cognito_identity_exists(client: httpx.AsyncClient, make_account, store, cognito) -> None:
    admin = make_account("admin@sliit.example", "INSTITUTION_ADMIN")
    cognito.fail("create_user", CognitoUnavailableError("timeout"))
    failed = await client.post(USERS, headers=admin.headers, json=new_user())
    provisioning_id = failed.json()["error"]["details"]["provisioning_id"]

    cancelled = await client.post(f"/api/v1/identity/provisioning/{provisioning_id}/cancel", headers=admin.headers)
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "CANCELLED"

    record = store.provisioning[uuid.UUID(provisioning_id)]
    record.status, record.cognito_sub = PROVISIONING_FAILED, "some-sub"
    store.snapshot()
    refused = await client.post(f"/api/v1/identity/provisioning/{provisioning_id}/cancel", headers=admin.headers)
    assert refused.status_code == 409


# ---------------------------------------------------------------- read, search, update


async def test_list_and_search_are_scoped_to_institution(client: httpx.AsyncClient, make_account) -> None:
    admin = make_account("admin@sliit.example", "INSTITUTION_ADMIN")
    make_account("alice@sliit.example", "LECTURER", full_name="Alice Perera")
    make_account("bob@sliit.example", "STUDENT", full_name="Bob Silva")
    outsider = make_account("carol@other.example", ("LECTURER", OTHER_INSTITUTION))

    listed = (await client.get(USERS, headers=admin.headers)).json()
    assert {u["email"] for u in listed["items"]} == {"admin@sliit.example", "alice@sliit.example", "bob@sliit.example"}

    searched = (await client.get(USERS, headers=admin.headers, params={"search": "perera"})).json()
    assert [u["email"] for u in searched["items"]] == ["alice@sliit.example"]

    by_role = (await client.get(USERS, headers=admin.headers, params={"role_code": "student"})).json()
    assert [u["email"] for u in by_role["items"]] == ["bob@sliit.example"]

    assert (await client.get(f"{USERS}/{outsider.id}", headers=admin.headers)).status_code == 404


async def test_update_profile_syncs_name_to_cognito(client: httpx.AsyncClient, make_account, cognito) -> None:
    admin = make_account("admin@sliit.example", "INSTITUTION_ADMIN")
    lecturer = make_account("lecturer@sliit.example", "LECTURER")
    response = await client.patch(f"{USERS}/{lecturer.id}", headers=admin.headers, json={"full_name": "  Dr.  Example   Lecturer "})
    assert response.status_code == 200
    assert response.json()["user"]["full_name"] == "Dr. Example Lecturer"
    assert response.json()["cognito_sync"] == "ok"
    assert cognito.users["lecturer@sliit.example"].name == "Dr. Example Lecturer"

    forbidden = await client.patch(f"{USERS}/{lecturer.id}", headers=admin.headers, json={"email": "x@y.example"})
    assert forbidden.status_code == 422


# ---------------------------------------------------------------- status


async def test_disable_blocks_api_access_immediately_and_enable_restores_it(client: httpx.AsyncClient, make_account, cognito) -> None:
    admin = make_account("admin@sliit.example", "INSTITUTION_ADMIN")
    lecturer = make_account("lecturer@sliit.example", "LECTURER")
    assert (await client.get("/api/v1/identity/me", headers=lecturer.headers)).status_code == 200

    disabled = await client.post(f"{USERS}/{lecturer.id}/disable", headers=admin.headers, json={"reason": "left the university"})
    assert disabled.status_code == 200
    assert disabled.json()["user"]["account_status"] == ACCOUNT_DISABLED
    assert cognito.count("disable_user") == 1 and cognito.count("global_sign_out") == 1

    # The lecturer's token is still unexpired, but the API rejects it.
    assert (await client.get("/api/v1/identity/me", headers=lecturer.headers)).json()["error"]["code"] == "ACCOUNT_DISABLED"

    status = (await client.get(f"{USERS}/{lecturer.id}/status", headers=admin.headers)).json()
    assert status["account_status"] == ACCOUNT_DISABLED and status["cognito_enabled"] is False

    enabled = await client.post(f"{USERS}/{lecturer.id}/enable", headers=admin.headers)
    assert enabled.json()["user"]["account_status"] == ACCOUNT_ACTIVE
    assert (await client.get("/api/v1/identity/me", headers=lecturer.headers)).status_code == 200


async def test_disable_succeeds_locally_even_if_cognito_is_down(client: httpx.AsyncClient, make_account, cognito, store) -> None:
    admin = make_account("admin@sliit.example", "INSTITUTION_ADMIN")
    lecturer = make_account("lecturer@sliit.example", "LECTURER")
    cognito.fail("disable_user", CognitoUnavailableError("timeout"))
    response = await client.post(f"{USERS}/{lecturer.id}/disable", headers=admin.headers)
    assert response.status_code == 200
    assert response.json()["cognito_sync"] == "failed"
    assert store.users[lecturer.user.id].account_status == ACCOUNT_DISABLED
    assert "user.cognito_sync_failed" in store.actions()
    # Calling disable again is the retry and completes the Cognito side.
    again = await client.post(f"{USERS}/{lecturer.id}/disable", headers=admin.headers)
    assert again.json()["cognito_sync"] == "ok"


async def test_cannot_disable_own_account(client: httpx.AsyncClient, make_account) -> None:
    admin = make_account("admin@sliit.example", "INSTITUTION_ADMIN")
    response = await client.post(f"{USERS}/{admin.id}/disable", headers=admin.headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CANNOT_DISABLE_SELF"


async def test_last_institution_admin_cannot_be_removed_even_with_stale_permissions(make_account, store, cognito, principal_of) -> None:
    """Two Institution Admins try to disable each other. The second request was authorised before the first committed."""
    first = make_account("first@sliit.example", "INSTITUTION_ADMIN")
    second = make_account("second@sliit.example", "INSTITUTION_ADMIN")
    first_principal, second_principal = await principal_of(first), await principal_of(second)

    await UserManagementService(FakeUnitOfWork(store), cognito).disable_user(first_principal, second.user.id)

    with pytest.raises(ConflictError) as info:
        await UserManagementService(FakeUnitOfWork(store), cognito).disable_user(second_principal, first.user.id)
    assert info.value.code == "LAST_INSTITUTION_ADMIN"
    assert store.users[first.user.id].account_status == ACCOUNT_ACTIVE
    assert store.locks >= 2

    with pytest.raises(ConflictError):
        await UserManagementService(FakeUnitOfWork(store), cognito).set_roles(
            second_principal, first.user.id, institution_id=INSTITUTION, role_codes=[])


# ---------------------------------------------------------------- roles


async def test_assign_and_revoke_roles_keeps_history(client: httpx.AsyncClient, make_account, store) -> None:
    admin = make_account("admin@sliit.example", "INSTITUTION_ADMIN")
    lecturer = make_account("lecturer@sliit.example", "LECTURER")

    granted = await client.put(f"{USERS}/{lecturer.id}/roles", headers=admin.headers,
                               json={"institution_id": str(INSTITUTION), "role_codes": ["LECTURER", "ACADEMIC_ADMIN"]})
    assert granted.status_code == 200, granted.text
    assert {r["role_code"] for r in granted.json()["roles"]} == {"LECTURER", "ACADEMIC_ADMIN"}

    revoked = await client.put(f"{USERS}/{lecturer.id}/roles", headers=admin.headers,
                               json={"institution_id": str(INSTITUTION), "role_codes": ["LECTURER"]})
    assert {r["role_code"] for r in revoked.json()["roles"]} == {"LECTURER"}
    history = [a for a in store.user_roles.values() if a.user_id == lecturer.user.id]
    assert len(history) == 2  # active LECTURER + revoked ACADEMIC_ADMIN: revocation keeps the row
    revoked_row = next(a for a in history if store.roles[a.role_id].code == "ACADEMIC_ADMIN")
    assert revoked_row.revoked_at is not None and revoked_row.revoked_by_user_id == admin.user.id
    assert store.actions().count("user.role_revoked") == 1


async def test_role_assignment_validation(client: httpx.AsyncClient, make_account) -> None:
    admin = make_account("admin@sliit.example", "INSTITUTION_ADMIN")
    lecturer = make_account("lecturer@sliit.example", "LECTURER")
    url = f"{USERS}/{lecturer.id}/roles"

    unknown = await client.put(url, headers=admin.headers, json={"institution_id": str(INSTITUTION), "role_codes": ["DEAN"]})
    assert unknown.status_code == 422 and unknown.json()["error"]["code"] == "UNKNOWN_ROLE"

    duplicate = await client.put(url, headers=admin.headers, json={"institution_id": str(INSTITUTION), "role_codes": ["LECTURER", "lecturer"]})
    assert duplicate.status_code == 422

    other_institution = await client.put(url, headers=admin.headers,
                                         json={"institution_id": str(OTHER_INSTITUTION), "role_codes": ["LECTURER"]})
    assert other_institution.status_code == 403


async def test_users_cannot_elevate_their_own_permissions(client: httpx.AsyncClient, make_account) -> None:
    lecturer = make_account("lecturer@sliit.example", "LECTURER")
    response = await client.put(f"{USERS}/{lecturer.id}/roles", headers=lecturer.headers,
                                json={"institution_id": str(INSTITUTION), "role_codes": ["LECTURER", "INSTITUTION_ADMIN"]})
    assert response.status_code == 403

    admin = make_account("admin@sliit.example", "INSTITUTION_ADMIN")
    self_change = await client.put(f"{USERS}/{admin.id}/roles", headers=admin.headers,
                                   json={"institution_id": str(INSTITUTION), "role_codes": ["INSTITUTION_ADMIN", "VENUE_MANAGER"]})
    assert self_change.status_code == 403
    assert self_change.json()["error"]["code"] == "SELF_ROLE_CHANGE_FORBIDDEN"


async def test_only_institution_admins_grant_institution_admin(client: httpx.AsyncClient, make_account, store) -> None:
    """A (hypothetical) delegated user-admin role with identity permissions still cannot grant INSTITUTION_ADMIN."""
    delegated = Role(id=uuid.uuid4(), code="USER_ADMIN", name="User admin", description="", is_system_role=False,
                     created_at=datetime.now(UTC), updated_at=datetime.now(UTC))
    store.roles[delegated.id] = delegated
    for permission in store.permissions.values():
        if permission.code.startswith("identity:"):
            store.role_permissions.add((delegated.id, permission.id))
    store.snapshot()

    user_admin = make_account("useradmin@sliit.example", "USER_ADMIN")
    lecturer = make_account("lecturer@sliit.example", "LECTURER")

    escalate = await client.put(f"{USERS}/{lecturer.id}/roles", headers=user_admin.headers,
                                json={"institution_id": str(INSTITUTION), "role_codes": ["LECTURER", "INSTITUTION_ADMIN"]})
    assert escalate.status_code == 403 and escalate.json()["error"]["code"] == "PRIVILEGED_ROLE_REQUIRED"

    create_admin = await client.post(USERS, headers=user_admin.headers, json=new_user("x@sliit.example", "INSTITUTION_ADMIN"))
    assert create_admin.status_code == 403

    ordinary = await client.put(f"{USERS}/{lecturer.id}/roles", headers=user_admin.headers,
                                json={"institution_id": str(INSTITUTION), "role_codes": ["LECTURER", "VENUE_MANAGER"]})
    assert ordinary.status_code == 200

    institution_admin = make_account("admin@sliit.example", "INSTITUTION_ADMIN")
    touch_admin = await client.patch(f"{USERS}/{institution_admin.id}", headers=user_admin.headers, json={"full_name": "Hacked"})
    assert touch_admin.status_code == 403 and touch_admin.json()["error"]["code"] == "PRIVILEGED_TARGET"


async def test_audit_log_is_readable_by_institution_admin_only(client: httpx.AsyncClient, make_account) -> None:
    admin = make_account("admin@sliit.example", "INSTITUTION_ADMIN")
    lecturer = make_account("lecturer@sliit.example", "LECTURER")
    await client.post(USERS, headers=admin.headers, json=new_user("new@sliit.example"))

    logs = await client.get("/api/v1/identity/audit-logs", headers=admin.headers, params={"action": "user.created"})
    assert logs.status_code == 200
    assert logs.json()["total"] == 1
    assert logs.json()["items"][0]["actor_user_id"] == admin.id
    assert (await client.get("/api/v1/identity/audit-logs", headers=lecturer.headers)).status_code == 403


async def test_every_user_event_is_visible_in_the_institution_audit_log(client: httpx.AsyncClient, make_account, store) -> None:
    admin = make_account("admin@sliit.example", "INSTITUTION_ADMIN")
    lecturer = make_account("lecturer@sliit.example", "LECTURER")
    await client.patch(f"{USERS}/{lecturer.id}", headers=admin.headers, json={"full_name": "Renamed"})
    await client.post(f"{USERS}/{lecturer.id}/disable", headers=admin.headers, json={"reason": "test"})
    await client.post(f"{USERS}/{lecturer.id}/enable", headers=admin.headers)

    logs = (await client.get("/api/v1/identity/audit-logs", headers=admin.headers, params={"target_user_id": lecturer.id})).json()
    assert {e["action"] for e in logs["items"]} == {"user.updated", "user.disabled", "user.enabled"}
    assert all(e["institution_id"] == str(INSTITUTION) for e in logs["items"])
    assert all(entry.institution_id is not None for entry in store.audit)


async def test_roles_and_permissions_catalogue(client: httpx.AsyncClient, make_account) -> None:
    """Only roles and permissions that can be granted within an institution are listed (no SYSTEM_ADMIN, no platform:*)."""
    admin = make_account("admin@sliit.example", "INSTITUTION_ADMIN")
    roles = (await client.get("/api/v1/identity/roles", headers=admin.headers)).json()
    assert {r["code"] for r in roles} == {"INSTITUTION_ADMIN", "ACADEMIC_ADMIN", "EXAMINATION_ADMIN", "VENUE_MANAGER", "LECTURER", "STUDENT"}
    permissions = (await client.get("/api/v1/identity/permissions", headers=admin.headers)).json()
    assert {p["code"] for p in permissions} == {p for p in PERMISSIONS if not p.startswith("platform:")}


async def test_health_endpoints(client: httpx.AsyncClient) -> None:
    assert (await client.get("/health")).json()["status"] == "ok"
    assert (await client.get("/health/db")).json()["status"] == "healthy"
