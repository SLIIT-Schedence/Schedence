"""Read-only exports consumed by the Teaching service (teaching-service/docs/owner-api-integration.md)."""

from __future__ import annotations

import uuid

import httpx

from app.models.teaching_inputs import TEACHING_INPUT_TABLES
from tests.conftest import INSTITUTION, OTHER_INSTITUTION

API = "/api/v1/academic"
EXPECTED_TABLES = {"staff", *(t.name for t in TEACHING_INPUT_TABLES)}
COMPUTING = uuid.UUID("33333333-3333-4333-8333-333333333333")


async def create_staff(client: httpx.AsyncClient, headers: dict, number: str, user: str | None = None) -> dict:
    response = await client.post(f"{API}/staff", headers=headers, json={
        "institution_id": str(INSTITUTION), "staff_number": number, "full_name": f"Synthetic {number}", "identity_user_id": user})
    assert response.status_code == 201, response.text
    return response.json()


def profile(number: str, staff_type: str = "ACADEMIC", department: uuid.UUID | None = COMPUTING) -> dict:
    return {"id": uuid.uuid4(), "institution_id": INSTITUTION, "staff_number": number, "staff_type": staff_type,
            "department_id": department}


async def test_generation_input_export_is_complete_and_tenant_scoped(client: httpx.AsyncClient, caller, store) -> None:
    admin = caller("INSTITUTION_ADMIN", ("INSTITUTION_ADMIN", OTHER_INSTITUTION))
    staff = await create_staff(client, admin, "SYN-001")
    other = await client.post(f"{API}/staff", headers=admin, json={
        "institution_id": str(OTHER_INSTITUTION), "staff_number": "SYN-900", "full_name": "Synthetic Other"})
    assert other.status_code == 201

    response = await client.get(f"{API}/teaching-inputs", headers=caller("ACADEMIC_ADMIN"),
                                params={"institution_id": str(INSTITUTION), "semester": 1})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["institution_id"] == str(INSTITUTION) and body["complete"] is True
    assert set(body["tables"]) == EXPECTED_TABLES
    assert [row["id"] for row in body["tables"]["staff"]] == [staff["id"]]
    assert all(row["institution_id"] == str(INSTITUTION) for rows in body["tables"].values() for row in rows)


async def test_exports_require_the_academic_owner_permission(client: httpx.AsyncClient, caller) -> None:
    params = {"institution_id": str(INSTITUTION)}
    assert (await client.get(f"{API}/teaching-inputs", params=params)).status_code == 401
    for path in ("/teaching-inputs", "/form-recipients"):
        assert (await client.get(API + path, headers=caller("LECTURER"), params=params)).status_code == 403
        outsider = caller(("ACADEMIC_ADMIN", OTHER_INSTITUTION))
        assert (await client.get(API + path, headers=outsider, params=params)).status_code == 404


async def test_form_recipients_report_link_status(client: httpx.AsyncClient, caller, store) -> None:
    admin = caller("INSTITUTION_ADMIN")
    shared, own = str(uuid.uuid4()), str(uuid.uuid4())
    ready = await create_staff(client, admin, "SYN-READY", own)
    await create_staff(client, admin, "SYN-NOLINK")
    await create_staff(client, admin, "SYN-AMB-1", shared)
    await create_staff(client, admin, "SYN-AMB-2", shared)
    await create_staff(client, admin, "SYN-NOPROFILE")
    store.teaching_rows = {
        "staff_teaching_profiles": [profile("SYN-READY"), profile("SYN-NOLINK", "INSTRUCTOR", None),
                                    profile("SYN-AMB-1"), profile("SYN-AMB-2")],
        "departments": [{"id": COMPUTING, "institution_id": INSTITUTION, "name": "Computing"}],
    }

    response = await client.get(f"{API}/form-recipients", headers=caller("ACADEMIC_ADMIN"),
                                params={"institution_id": str(INSTITUTION)})
    assert response.status_code == 200, response.text
    items = {row["staff_number"]: row for row in response.json()["items"]}
    assert {k: v["link_status"] for k, v in items.items()} == {
        "SYN-READY": "READY", "SYN-NOLINK": "NO_LINK", "SYN-AMB-1": "AMBIGUOUS_LINK", "SYN-AMB-2": "AMBIGUOUS_LINK"}
    assert items["SYN-READY"]["staff_id"] == ready["id"]
    assert items["SYN-READY"]["departments"] == [{"id": str(COMPUTING), "name": "Computing"}]
    assert items["SYN-NOLINK"]["department_ids"] == []


async def test_self_recipient_returns_only_the_callers_own_link(client: httpx.AsyncClient, caller, store) -> None:
    admin = caller("INSTITUTION_ADMIN")
    lecturer = caller("LECTURER")
    await create_staff(client, admin, "SYN-ME", lecturer["X-Test-User-Id"])
    await create_staff(client, admin, "SYN-SOMEONE", str(uuid.uuid4()))
    store.teaching_rows = {"staff_teaching_profiles": [profile("SYN-ME"), profile("SYN-SOMEONE")]}
    params = {"institution_id": str(INSTITUTION)}

    mine = (await client.get(f"{API}/form-recipients/me", headers=lecturer, params=params)).json()["items"]
    assert [row["staff_number"] for row in mine] == ["SYN-ME"]
    unlinked = (await client.get(f"{API}/form-recipients/me", headers=caller("LECTURER"), params=params)).json()
    assert unlinked["items"] == [] and unlinked["complete"] is True
    outsider = caller(("LECTURER", OTHER_INSTITUTION))
    assert (await client.get(f"{API}/form-recipients/me", headers=outsider, params=params)).status_code == 404
