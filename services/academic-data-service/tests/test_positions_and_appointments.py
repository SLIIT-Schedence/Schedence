"""Administrative positions, appointments and workload (mocked repositories + mocked Identity Service)."""

from __future__ import annotations

import uuid
from decimal import Decimal

import httpx
import pytest

from app.services.appointments import WorkloadItem, aggregate
from tests.conftest import INSTITUTION, OTHER_INSTITUTION

API = "/api/v1/academic"


def hod_body(**overrides) -> dict:
    return {"institution_id": str(INSTITUTION), "code": "HEAD_OF_DEPARTMENT", "name": "Head of Department",
            "description": "Leads an academic department", "unit_type": "DEPARTMENT", "is_exclusive_per_unit": True,
            "admin_hours_per_week": "10", "policy_reference": "Example policy ref", "effective_from": "2026-01-01", **overrides}


def coordinator_body(**overrides) -> dict:
    return {"institution_id": str(INSTITUTION), "code": "PROGRAM_COORDINATOR", "name": "Program Coordinator",
            "description": "Responsible for coordinating an academic program", "unit_type": "PROGRAM",
            "admin_hours_per_week": 6, "effective_from": "2026-01-01", **overrides}


async def create_position(client: httpx.AsyncClient, headers: dict, body: dict) -> dict:
    response = await client.post(f"{API}/positions", headers=headers, json=body)
    assert response.status_code == 201, response.text
    return response.json()


async def create_staff(client: httpx.AsyncClient, headers: dict, number: str = "EMP001", name: str = "Example Lecturer", **extra) -> dict:
    response = await client.post(f"{API}/staff", headers=headers, json={
        "institution_id": str(extra.pop("institution", INSTITUTION)), "staff_number": number, "full_name": name,
        "academic_rank": "Senior Lecturer", **extra})
    assert response.status_code == 201, response.text
    return response.json()


async def assign(client: httpx.AsyncClient, headers: dict, staff_id: str, position_id: str, **body) -> httpx.Response:
    return await client.post(f"{API}/staff/{staff_id}/appointments", headers=headers,
                             json={"position_type_id": position_id, "effective_from": "2026-01-01", **body})


# ---------------------------------------------------------------- position types


async def test_super_admin_creates_hod_position(client: httpx.AsyncClient, caller) -> None:
    admin = caller("SUPER_ADMIN")
    hod = await create_position(client, admin, hod_body())
    assert hod["code"] == "HEAD_OF_DEPARTMENT" and hod["unit_type"] == "DEPARTMENT" and hod["is_exclusive_per_unit"]
    assert hod["default_admin_hours_per_week"] == 10.0
    assert hod["effective_policy"]["version"] == 1 and hod["effective_policy"]["combination_rule"] == "UNSPECIFIED"


async def test_super_admin_creates_program_coordinator_position(client: httpx.AsyncClient, caller) -> None:
    pc = await create_position(client, caller("SUPER_ADMIN"), coordinator_body())
    assert (pc["code"], pc["default_admin_hours_per_week"], pc["unit_type"]) == ("PROGRAM_COORDINATOR", 6.0, "PROGRAM")


async def test_position_hours_are_required_input_not_hardcoded(client: httpx.AsyncClient, caller) -> None:
    body = coordinator_body()
    body.pop("admin_hours_per_week")
    response = await client.post(f"{API}/positions", headers=caller("SUPER_ADMIN"), json=body)
    assert response.status_code == 422


async def test_administrative_hours_are_configurable_with_history(client: httpx.AsyncClient, caller) -> None:
    admin = caller("SUPER_ADMIN")
    pc = await create_position(client, admin, coordinator_body())

    updated = await client.post(f"{API}/positions/{pc['id']}/workload-policies", headers=admin, json={
        "admin_hours_per_week": 8, "combination_rule": "ADDITIVE", "policy_reference": "Revised 2026", "effective_from": "2026-10-01"})
    assert updated.status_code == 201, updated.text
    body = updated.json()
    assert body["default_admin_hours_per_week"] == 8.0
    v1, v2 = body["policy_history"]
    assert (v1["version"], v1["admin_hours_per_week"], v1["effective_until"]) == (1, 6.0, "2026-09-30")
    assert (v2["version"], v2["admin_hours_per_week"], v2["effective_until"]) == (2, 8.0, None)

    backdated = await client.post(f"{API}/positions/{pc['id']}/workload-policies", headers=admin,
                                  json={"admin_hours_per_week": 9, "effective_from": "2026-05-01"})
    assert backdated.status_code == 422 and backdated.json()["error"]["code"] == "INVALID_EFFECTIVE_DATE"

    capped_without_cap = await client.post(f"{API}/positions/{pc['id']}/workload-policies", headers=admin,
                                           json={"admin_hours_per_week": 9, "combination_rule": "CAPPED", "effective_from": "2026-12-01"})
    assert capped_without_cap.status_code == 422


async def test_duplicate_position_code_is_rejected(client: httpx.AsyncClient, caller) -> None:
    admin = caller("SUPER_ADMIN")
    await create_position(client, admin, hod_body())
    duplicate = await client.post(f"{API}/positions", headers=admin, json=hod_body())
    assert duplicate.status_code == 409 and duplicate.json()["error"]["code"] == "POSITION_CODE_EXISTS"


async def test_update_and_disable_positions(client: httpx.AsyncClient, caller) -> None:
    admin = caller("SUPER_ADMIN")
    hod = await create_position(client, admin, hod_body())
    staff = await create_staff(client, admin)

    renamed = await client.patch(f"{API}/positions/{hod['id']}", headers=admin, json={"name": "Head of Academic Department"})
    assert renamed.json()["name"] == "Head of Academic Department"

    await assign(client, admin, staff["id"], hod["id"], unit_code="computing")
    in_use = await client.post(f"{API}/positions/{hod['id']}/disable", headers=admin)
    assert in_use.status_code == 409 and in_use.json()["error"]["code"] == "POSITION_IN_USE"

    dean = await create_position(client, admin, hod_body(code="DEAN", name="Dean", unit_type="FACULTY"))
    disabled = await client.post(f"{API}/positions/{dean['id']}/disable", headers=admin)
    assert disabled.status_code == 200 and disabled.json()["is_active"] is False
    listed = (await client.get(f"{API}/positions", headers=admin)).json()
    assert {p["code"] for p in listed["items"]} == {"HEAD_OF_DEPARTMENT"}
    blocked = await assign(client, admin, staff["id"], dean["id"], unit_code="FOC")
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "POSITION_DISABLED"


async def test_academic_admin_can_read_but_not_manage_positions(client: httpx.AsyncClient, caller) -> None:
    await create_position(client, caller("SUPER_ADMIN"), hod_body())
    academic_admin = caller("ACADEMIC_ADMIN")
    assert (await client.get(f"{API}/positions", headers=academic_admin)).status_code == 200
    assert (await client.post(f"{API}/positions", headers=academic_admin, json=coordinator_body())).status_code == 403


# ---------------------------------------------------------------- appointments


async def test_staff_appointment_creation(client: httpx.AsyncClient, caller, store) -> None:
    admin = caller("SUPER_ADMIN")
    hod = await create_position(client, admin, hod_body())
    staff = await create_staff(client, admin)

    response = await assign(client, admin, staff["id"], hod["id"], unit_code=" computing ", unit_name="Department of Computing",
                            effective_from="2026-11-01")
    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["unit_type"], body["unit_code"], body["status"]) == ("DEPARTMENT", "COMPUTING", "SCHEDULED")
    assert ("staff", uuid.UUID(staff["id"])) in store.row_locks  # serialised per staff member
    assert [e.action for e in store.audit][-1] == "appointment.assigned"


async def test_invalid_staff_reference(client: httpx.AsyncClient, caller) -> None:
    admin = caller("SUPER_ADMIN")
    hod = await create_position(client, admin, hod_body())
    response = await assign(client, admin, str(uuid.uuid4()), hod["id"], unit_code="COMPUTING")
    assert response.status_code == 404 and response.json()["error"]["code"] == "STAFF_NOT_FOUND"


async def test_unit_is_required_for_department_positions(client: httpx.AsyncClient, caller) -> None:
    admin = caller("SUPER_ADMIN")
    hod = await create_position(client, admin, hod_body())
    staff = await create_staff(client, admin)
    response = await assign(client, admin, staff["id"], hod["id"])
    assert response.status_code == 422 and response.json()["error"]["code"] == "INVALID_UNIT"


async def test_duplicate_and_exclusive_appointments_are_prevented(client: httpx.AsyncClient, caller) -> None:
    admin = caller("SUPER_ADMIN")
    hod = await create_position(client, admin, hod_body())
    alice = await create_staff(client, admin, "EMP001", "Alice")
    bob = await create_staff(client, admin, "EMP002", "Bob")
    assert (await assign(client, admin, alice["id"], hod["id"], unit_code="COMPUTING", effective_until="2026-12-31")).status_code == 201

    duplicate = await assign(client, admin, alice["id"], hod["id"], unit_code="COMPUTING", effective_from="2026-06-01")
    assert duplicate.status_code == 409 and duplicate.json()["error"]["code"] == "DUPLICATE_APPOINTMENT"

    second_hod = await assign(client, admin, bob["id"], hod["id"], unit_code="COMPUTING", effective_from="2026-06-01")
    assert second_hod.status_code == 409 and second_hod.json()["error"]["code"] == "POSITION_ALREADY_HELD"

    other_department = await assign(client, admin, bob["id"], hod["id"], unit_code="ENGINEERING")
    assert other_department.status_code == 201
    successor = await assign(client, admin, bob["id"], hod["id"], unit_code="COMPUTING", effective_from="2027-01-01")
    assert successor.status_code == 201

    pc = await create_position(client, admin, coordinator_body())  # not exclusive: two coordinators may share a program
    assert (await assign(client, admin, alice["id"], pc["id"], unit_code="BSC_IT")).status_code == 201
    assert (await assign(client, admin, bob["id"], pc["id"], unit_code="BSC_IT")).status_code == 201


async def test_staff_may_hold_multiple_different_appointments(client: httpx.AsyncClient, caller) -> None:
    admin = caller("SUPER_ADMIN")
    hod = await create_position(client, admin, hod_body())
    pc = await create_position(client, admin, coordinator_body())
    staff = await create_staff(client, admin)
    assert (await assign(client, admin, staff["id"], hod["id"], unit_code="COMPUTING")).status_code == 201
    assert (await assign(client, admin, staff["id"], pc["id"], unit_code="BSC_IT")).status_code == 201
    current = (await client.get(f"{API}/staff/{staff['id']}/appointments", headers=admin)).json()
    assert {a["position_code"] for a in current} == {"HEAD_OF_DEPARTMENT", "PROGRAM_COORDINATOR"}


async def test_effective_date_validation(client: httpx.AsyncClient, caller) -> None:
    admin = caller("SUPER_ADMIN")
    hod = await create_position(client, admin, hod_body())
    staff = await create_staff(client, admin)

    reversed_dates = await assign(client, admin, staff["id"], hod["id"], unit_code="X", effective_from="2026-11-01", effective_until="2026-10-01")
    assert reversed_dates.status_code == 422
    before_policy = await assign(client, admin, staff["id"], hod["id"], unit_code="X", effective_from="2025-06-01")
    assert before_policy.status_code == 422 and before_policy.json()["error"]["code"] == "NO_WORKLOAD_POLICY"
    with_override = await assign(client, admin, staff["id"], hod["id"], unit_code="X", effective_from="2025-06-01",
                                 effective_until="2025-12-31", admin_hours_override=12, override_reason="Acting HOD arrangement")
    assert with_override.status_code == 201
    override_without_reason = await assign(client, admin, staff["id"], hod["id"], unit_code="Y", admin_hours_override=5)
    assert override_without_reason.status_code == 422


async def test_appointment_termination_and_history(client: httpx.AsyncClient, caller) -> None:
    admin = caller("SUPER_ADMIN")
    hod = await create_position(client, admin, hod_body())
    staff = await create_staff(client, admin)
    appointment = (await assign(client, admin, staff["id"], hod["id"], unit_code="COMPUTING")).json()
    url = f"{API}/appointments/{appointment['id']}"

    too_early = await client.post(f"{url}/end", headers=admin, json={"end_date": "2025-12-31"})
    assert too_early.status_code == 422

    ended = await client.post(f"{url}/end", headers=admin, json={"end_date": "2026-09-30", "reason": "Term completed"})
    assert ended.status_code == 200
    assert (ended.json()["status"], ended.json()["effective_until"], ended.json()["end_reason"]) == ("ENDED", "2026-09-30", "Term completed")

    assert (await client.post(f"{url}/end", headers=admin)).json()["error"]["code"] == "APPOINTMENT_ENDED"
    assert (await client.patch(url, headers=admin, json={"notes": "x"})).status_code == 409

    current = (await client.get(f"{API}/staff/{staff['id']}/appointments", headers=admin)).json()
    history = (await client.get(f"{API}/staff/{staff['id']}/appointments", headers=admin, params={"include_ended": "true"})).json()
    assert current == [] and [a["id"] for a in history] == [appointment["id"]]

    detail = (await client.get(url, headers=admin)).json()
    assert [e["action"] for e in detail["audit_trail"]] == ["appointment.assigned", "appointment.ended"]


async def test_update_permitted_fields_only(client: httpx.AsyncClient, caller) -> None:
    admin = caller("SUPER_ADMIN")
    hod = await create_position(client, admin, hod_body())
    staff = await create_staff(client, admin)
    appointment = (await assign(client, admin, staff["id"], hod["id"], unit_code="COMPUTING")).json()
    url = f"{API}/appointments/{appointment['id']}"

    ok = await client.patch(url, headers=admin, json={"effective_until": "2027-12-31", "unit_name": "School of Computing"})
    assert ok.status_code == 200 and ok.json()["effective_until"] == "2027-12-31"
    assert (await client.patch(url, headers=admin, json={"staff_id": str(uuid.uuid4())})).status_code == 422
    assert (await client.patch(url, headers=admin, json={"effective_until": "2025-01-01"})).status_code == 422


# ---------------------------------------------------------------- institution scope


async def test_institution_scope_is_enforced(client: httpx.AsyncClient, caller) -> None:
    sliit_admin = caller("SUPER_ADMIN")
    other_admin = caller(("SUPER_ADMIN", OTHER_INSTITUTION))
    hod = await create_position(client, sliit_admin, hod_body())
    staff = await create_staff(client, sliit_admin)

    assert (await client.get(f"{API}/positions/{hod['id']}", headers=other_admin)).status_code == 404
    assert (await client.get(f"{API}/staff/{staff['id']}", headers=other_admin)).status_code == 404
    assert (await client.post(f"{API}/positions", headers=other_admin, json=hod_body())).status_code == 404
    assert (await client.get(f"{API}/positions", headers=other_admin)).json()["total"] == 0

    foreign_position = await create_position(client, other_admin, hod_body(institution_id=str(OTHER_INSTITUTION)))
    cross = await assign(client, sliit_admin, staff["id"], foreign_position["id"], unit_code="COMPUTING")
    assert cross.status_code == 404 and cross.json()["error"]["code"] == "POSITION_NOT_FOUND"


# ---------------------------------------------------------------- appointments never grant login roles


async def test_hod_appointment_does_not_grant_super_admin(client: httpx.AsyncClient, caller, identity) -> None:
    admin = caller("SUPER_ADMIN")
    lecturer = caller("LECTURER")
    hod = await create_position(client, admin, hod_body())
    staff = await create_staff(client, admin, identity_user_id=lecturer["X-Test-User-Id"])
    assert (await assign(client, admin, staff["id"], hod["id"], unit_code="COMPUTING")).status_code == 201

    # The newly appointed HOD still has only LECTURER access...
    assert (await client.post(f"{API}/positions", headers=lecturer, json=coordinator_body())).status_code == 403
    assert (await client.get(f"{API}/positions", headers=lecturer)).status_code == 403
    # ...and the Academic Data Service never asked the Identity Service to change anything.
    assert {request for request in identity.requests} == {("GET", "/api/v1/identity/me/permissions")}


async def test_program_coordinator_appointment_does_not_grant_academic_admin(client: httpx.AsyncClient, caller, identity) -> None:
    admin = caller("SUPER_ADMIN")
    lecturer = caller("LECTURER")
    pc = await create_position(client, admin, coordinator_body())
    staff = await create_staff(client, admin, identity_user_id=lecturer["X-Test-User-Id"])
    assert (await assign(client, admin, staff["id"], pc["id"], unit_code="BSC_IT")).status_code == 201

    assert (await client.get(f"{API}/staff/{staff['id']}/administrative-workload", headers=lecturer)).status_code == 403
    assert all(method == "GET" for method, _ in identity.requests)


async def test_staff_without_cognito_account_can_hold_appointments(client: httpx.AsyncClient, caller) -> None:
    admin = caller("SUPER_ADMIN")
    pc = await create_position(client, admin, coordinator_body())
    staff = await create_staff(client, admin, "EMP900", "Visiting Lecturer")
    assert staff["identity_user_id"] is None
    assert (await assign(client, admin, staff["id"], pc["id"], unit_code="BSC_IT")).status_code == 201
    workload = (await client.get(f"{API}/staff/{staff['id']}/administrative-workload", headers=admin)).json()
    assert workload["appointments"][0]["required_admin_hours_per_week"] == 6.0


# ---------------------------------------------------------------- workload contract


async def test_administrative_workload_retrieval(client: httpx.AsyncClient, caller) -> None:
    admin = caller("SUPER_ADMIN")
    academic_admin = caller("ACADEMIC_ADMIN")  # a future scheduler acts with this kind of permission
    hod = await create_position(client, admin, hod_body())
    pc = await create_position(client, admin, coordinator_body())
    staff = await create_staff(client, admin)
    await assign(client, admin, staff["id"], hod["id"], unit_code="COMPUTING", effective_from="2026-02-01")
    url = f"{API}/staff/{staff['id']}/administrative-workload"

    single = (await client.get(url, headers=academic_admin, params={"as_of": "2026-03-01"})).json()
    assert single["aggregate"] == {"status": "COMPUTED", "rule": "SINGLE_APPOINTMENT", "total_admin_hours_per_week": 10.0, "reason": None}
    item = single["appointments"][0]
    assert (item["position_code"], item["required_admin_hours_per_week"], item["hours_source"]) == ("HEAD_OF_DEPARTMENT", 10.0, "POLICY")
    assert item["policy"]["version"] == 1 and item["policy"]["policy_reference"] == "Example policy ref"
    assert item["effective_from"] == "2026-02-01"

    await assign(client, admin, staff["id"], pc["id"], unit_code="BSC_IT", effective_from="2026-04-01",
                 admin_hours_override=4, override_reason="Shared coordination")
    multiple = (await client.get(url, headers=academic_admin, params={"as_of": "2026-05-01"})).json()
    assert multiple["aggregate"]["status"] == "NOT_COMPUTED" and multiple["aggregate"]["total_admin_hours_per_week"] is None
    assert sorted((a["position_code"], a["required_admin_hours_per_week"], a["hours_source"]) for a in multiple["appointments"]) == [
        ("HEAD_OF_DEPARTMENT", 10.0, "POLICY"), ("PROGRAM_COORDINATOR", 4.0, "OVERRIDE")]

    before = (await client.get(url, headers=academic_admin, params={"as_of": "2026-01-15"})).json()
    assert before["appointments"] == [] and before["aggregate"]["total_admin_hours_per_week"] == 0.0

    lecturer = caller("LECTURER")
    assert (await client.get(url, headers=lecturer)).status_code == 403


def _item(hours: str, rule: str, cap: str | None = None) -> WorkloadItem:
    class P:  # minimal stand-in for a policy row
        combination_rule = rule
        cap_hours_per_week = Decimal(cap) if cap else None

    return WorkloadItem(appointment=None, position=None, policy=P(), required_hours=Decimal(hours), hours_source="POLICY")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("items", "status", "total"),
    [
        ([_item("6", "ADDITIVE"), _item("10", "ADDITIVE")], "COMPUTED", Decimal("16")),
        ([_item("6", "CAPPED", "12"), _item("10", "CAPPED", "12")], "COMPUTED", Decimal("12")),
        ([_item("6", "REPLACEMENT"), _item("10", "REPLACEMENT")], "COMPUTED", Decimal("10")),
        ([_item("6", "UNSPECIFIED"), _item("10", "UNSPECIFIED")], "NOT_COMPUTED", None),
        ([_item("6", "ADDITIVE"), _item("10", "CAPPED", "12")], "NOT_COMPUTED", None),
        ([_item("6", "CAPPED", "8"), _item("10", "CAPPED", "12")], "NOT_COMPUTED", None),
    ],
)
def test_workload_aggregation_only_when_policy_is_resolved(items, status, total) -> None:
    result = aggregate(items)
    assert (result.status, result.total_hours) == (status, total)


# ---------------------------------------------------------------- authentication / integration


async def test_requests_without_valid_tokens_are_rejected(client: httpx.AsyncClient, tokens) -> None:
    assert (await client.get(f"{API}/positions")).status_code == 401
    assert (await client.get(f"{API}/positions", headers={"Authorization": f"Bearer {tokens.id_token()}"})).status_code == 401


async def test_disabled_identity_is_rejected(client: httpx.AsyncClient, caller) -> None:
    response = await client.get(f"{API}/positions", headers=caller("SUPER_ADMIN", status="DISABLED"))
    assert response.status_code == 403 and response.json()["error"]["code"] == "ACCOUNT_DISABLED"


async def test_identity_service_outage_fails_closed(client: httpx.AsyncClient, caller, identity) -> None:
    admin = caller("SUPER_ADMIN")
    identity.down = True
    response = await client.get(f"{API}/positions", headers=admin)
    assert response.status_code == 503 and response.json()["error"]["code"] == "IDENTITY_SERVICE_UNAVAILABLE"


async def test_reads_reuse_cached_permissions_but_writes_always_recheck(client: httpx.AsyncClient, caller, identity) -> None:
    admin = caller("SUPER_ADMIN")
    await client.get(f"{API}/positions", headers=admin)
    await client.get(f"{API}/positions", headers=admin)
    assert len(identity.requests) == 1
    await client.post(f"{API}/positions", headers=admin, json=hod_body())
    assert len(identity.requests) == 2


async def test_health_endpoints(client: httpx.AsyncClient) -> None:
    assert (await client.get("/health")).json() == {"status": "ok", "service": "academic-data-service"}
    assert (await client.get("/health/db")).json()["status"] == "healthy"
