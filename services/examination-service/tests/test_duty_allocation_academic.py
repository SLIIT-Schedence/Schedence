"""Academic Data Service client and adapters, against a mocked HTTP transport.

All staff, ranks, workloads and institutions are SYNTHETIC; no real service is called.
Response shapes follow academic-data StaffOut, Page[StaffOut] and AdministrativeWorkloadOut.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid

import httpx
import pytest

from app.duty_allocation import (
    AcademicDataClient, AcademicDataForbidden, AcademicDataInstitutionMismatch, AcademicDataMalformed,
    AcademicDataNotFound, AcademicDataUnauthenticated, AcademicDataUnavailable, DutyFairnessPolicy, DutyRole,
    EligibilityPolicy, FairnessWeights, LoadUnit, MissingAdminHours, RoleRule, load_academic_staff_data, load_staff,
)
from app.duty_allocation.fairness import fairness_input_errors
from app.duty_allocation.fairness_metrics import staff_offsets
from app.duty_allocation.staff_source import STAFF_RECORD
from duty_allocation_support import at, make_input, session

BASE = "http://academic.test"
TOKEN = "synthetic-access-token-123"
INST = uuid.UUID(int=1000)
OTHER = uuid.UUID(int=2000)
POLICY = EligibilityPolicy(("Rank A", "Rank B"), (RoleRule(DutyRole.SUPERVISOR, "Rank A", False),
                                                 RoleRule(DutyRole.INVIGILATOR, None, True)), 0, "synthetic test policy")


def sid(n: int) -> str:
    return str(uuid.UUID(int=n))


def admin_policy(missing: MissingAdminHours) -> DutyFairnessPolicy:
    """Synthetic: 10 load units per weekly admin hour; one duty = 100 units."""
    return DutyFairnessPolicy("synthetic", LoadUnit.DUTIES, 100, 100, 100, 10, missing, 0, FairnessWeights(1, 0, 0, 0))


def staff_row(n, rank="Rank A", status="ACTIVE", institution=INST) -> dict:
    return {"id": sid(n), "institution_id": str(institution), "staff_number": f"EMP{n:03}",
            "full_name": f"Synthetic Staff {n}", "email": None, "academic_rank": rank,
            "employment_status": status, "identity_user_id": None, "created_at": "2026-10-01T00:00:00Z"}


def workload_row(n, hours=None, status="COMPUTED", institution=INST, reason=None) -> dict:
    return {"staff_id": sid(n), "institution_id": str(institution), "as_of": "2026-11-01", "appointments": [],
            "aggregate": {"status": status, "rule": "SUM" if status == "COMPUTED" else None,
                          "total_admin_hours_per_week": hours, "reason": reason}}


class FakeAcademic:
    """Minimal fake of the three endpoints. ``override`` maps a path to (status, body)."""

    def __init__(self, staff, workloads=None, override=None):
        self.staff, self.workloads, self.override = staff, workloads or {}, override or {}
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path.removeprefix("/api/v1/academic")
        if path in self.override:
            status, body = self.override[path](request) if callable(self.override[path]) else self.override[path]
            return httpx.Response(status, json=body) if not isinstance(body, bytes) else httpx.Response(status, content=body)
        if request.headers.get("Authorization") != f"Bearer {TOKEN}":
            return httpx.Response(401, json={"error": {"code": "UNAUTHENTICATED", "message": "x"}})
        if path == "/staff":
            page, size = int(request.url.params["page"]), int(request.url.params["page_size"])
            rows = [r for r in self.staff if r["institution_id"] == request.url.params.get("institution_id", str(INST))]
            return httpx.Response(200, json={"items": rows[(page - 1) * size: page * size], "total": len(rows),
                                             "page": page, "page_size": size})
        if path.endswith("/administrative-workload"):
            staff_id = path.split("/")[2]
            if staff_id not in self.workloads:
                return httpx.Response(404, json={"error": {"code": "NOT_FOUND", "message": "x"}})
            return httpx.Response(200, json=self.workloads[staff_id])
        if path.startswith("/staff/"):
            row = next((r for r in self.staff if r["id"] == path.split("/")[2]), None)
            return httpx.Response(200, json=row) if row else httpx.Response(404, json={"error": {"code": "NOT_FOUND"}})
        return httpx.Response(404)


def client_for(fake, **kwargs) -> AcademicDataClient:
    return AcademicDataClient(BASE, http=httpx.AsyncClient(transport=httpx.MockTransport(fake)), **kwargs)


# --- Pagination ----------------------------------------------------------------------------------

async def test_reads_every_page_with_institution_and_page_params():
    fake = FakeAcademic([staff_row(n) for n in range(1, 6)])
    rows = await client_for(fake, page_size=2).list_staff(TOKEN, institution_id=INST)
    assert [r["id"] for r in rows] == [sid(n) for n in range(1, 6)]
    assert [r.url.params["page"] for r in fake.requests] == ["1", "2", "3"]
    assert all(r.url.params["institution_id"] == str(INST) and r.url.params["page_size"] == "2" for r in fake.requests)
    assert all(r.url.path == "/api/v1/academic/staff" for r in fake.requests)


async def test_single_page_and_empty_list():
    assert len(await client_for(FakeAcademic([staff_row(1)])).list_staff(TOKEN, institution_id=INST)) == 1
    fake = FakeAcademic([])
    assert await client_for(fake).list_staff(TOKEN, institution_id=INST) == ()
    assert len(fake.requests) == 1


async def test_list_that_changes_between_pages_fails():
    calls = {"n": 0}

    def shifting(request):
        calls["n"] += 1
        page = int(request.url.params["page"])
        return 200, {"items": [staff_row(page)], "total": 2 if calls["n"] == 1 else 3, "page": page, "page_size": 1}
    with pytest.raises(AcademicDataUnavailable, match="changed"):
        await client_for(FakeAcademic([], override={"/staff": shifting}), page_size=1).list_staff(TOKEN, institution_id=INST)

    def repeating(request):
        return 200, {"items": [staff_row(1)], "total": 2, "page": int(request.url.params["page"]), "page_size": 1}
    with pytest.raises(AcademicDataUnavailable, match="changed"):
        await client_for(FakeAcademic([], override={"/staff": repeating}), page_size=1).list_staff(TOKEN, institution_id=INST)


async def test_short_list_fails_rather_than_returning_partial_staff():
    def short(request):
        page = int(request.url.params["page"])
        return 200, {"items": [staff_row(1)] if page == 1 else [], "total": 3, "page": page, "page_size": 1}
    with pytest.raises(AcademicDataUnavailable, match="Expected 3"):
        await client_for(FakeAcademic([], override={"/staff": short}), page_size=1).list_staff(TOKEN, institution_id=INST)


@pytest.mark.parametrize("kwargs", [{"page_size": 0}, {"page_size": 101}, {"max_pages": 0}, {"max_concurrency": 0}])
def test_invalid_client_configuration(kwargs):
    with pytest.raises(ValueError):
        client_for(FakeAcademic([]), **kwargs)
    with pytest.raises(ValueError):
        AcademicDataClient("academic.test", http=httpx.AsyncClient())


# --- Mapping to Member 3 models ----------------------------------------------------------------------

async def test_staff_mapping_preserves_ids_and_numbers_and_uses_policy_ranks():
    fake = FakeAcademic([staff_row(1, "Rank A"), staff_row(2, "Rank B"), staff_row(3, None),
                         staff_row(4, "Rank A", status="INACTIVE"), staff_row(5, "Visiting")])
    result = await load_staff(client_for(fake), TOKEN, institution_id=INST, eligibility=POLICY)
    by_id = {m.staff_id: m for m in result.staff}
    assert by_id[sid(1)].staff_number == "EMP001" and by_id[sid(1)].full_name == "Synthetic Staff 1"
    assert by_id[sid(1)].eligible_roles == (DutyRole.SUPERVISOR, DutyRole.INVIGILATOR)
    assert by_id[sid(2)].eligible_roles == (DutyRole.INVIGILATOR,)  # below the Supervisor minimum
    assert by_id[sid(3)].eligible_roles == (DutyRole.INVIGILATOR,)  # no rank: not a Supervisor
    assert by_id[sid(5)].eligible_roles == (DutyRole.INVIGILATOR,)  # rank not in the configured order
    assert [(e.staff_id, e.staff_number) for e in result.excluded] == [(sid(4), "EMP004")]
    assert "INACTIVE" in result.excluded[0].reason


async def test_unknown_employment_status_is_reported_not_guessed():
    fake = FakeAcademic([staff_row(1), staff_row(2, status="ON_LEAVE")])
    result = await load_staff(client_for(fake), TOKEN, institution_id=INST, eligibility=POLICY)
    assert [m.staff_id for m in result.staff] == [sid(1)]
    assert [v.constraint for v in result.violations] == [STAFF_RECORD] and "ON_LEAVE" in result.violations[0].message


async def test_staff_with_no_role_under_policy_are_excluded():
    strict = EligibilityPolicy(("Rank A",), (RoleRule(DutyRole.SUPERVISOR, "Rank A", False),
                                             RoleRule(DutyRole.INVIGILATOR, "Rank A", False)), 0, "synthetic strict")
    result = await load_staff(client_for(FakeAcademic([staff_row(1, None)])), TOKEN, institution_id=INST, eligibility=strict)
    assert result.staff == () and result.excluded[0].staff_id == sid(1)


# --- Administrative workload -------------------------------------------------------------------------

def three_staff_with_workloads():
    staff = [staff_row(1), staff_row(2), staff_row(3)]
    workloads = {sid(1): workload_row(1, 7.5),
                 sid(2): workload_row(2, None, "NOT_COMPUTED", reason="No workload policy for position")}
    return FakeAcademic(staff, workloads)  # staff 3 has no workload record (404)


async def test_computed_not_computed_and_missing_workload_are_kept_apart():
    data = await load_academic_staff_data(client_for(three_staff_with_workloads()), TOKEN, institution_id=INST,
                                          eligibility=POLICY, as_of=dt.date(2026, 11, 1))
    admin = {a.staff_id: a for a in data.admin}
    assert admin[sid(1)].hours_per_week == 7.5
    assert admin[sid(2)].hours_per_week is None and "No workload policy" in admin[sid(2)].note
    assert data.not_computed == (admin[sid(2)],)
    assert [m.staff_id for m in data.missing_workload] == [sid(3)] and sid(3) not in admin
    inputs = data.fairness_inputs()
    assert inputs.admin == data.admin and inputs.history == () and inputs.preferences == ()


async def test_as_of_is_passed_to_the_workload_endpoint():
    fake = three_staff_with_workloads()
    await load_academic_staff_data(client_for(fake), TOKEN, institution_id=INST, eligibility=POLICY,
                                   as_of=dt.date(2026, 11, 1))
    workload_requests = [r for r in fake.requests if r.url.path.endswith("/administrative-workload")]
    assert len(workload_requests) == 3 and all(r.url.params["as_of"] == "2026-11-01" for r in workload_requests)


async def test_missing_workload_is_not_zero_unless_policy_allows_it():
    data = await load_academic_staff_data(client_for(three_staff_with_workloads()), TOKEN, institution_id=INST,
                                          eligibility=POLICY)
    roster_input = make_input([session("X1", at(9), at(11), "R1")], data.staff.staff)
    errors = fairness_input_errors(roster_input, data.fairness_inputs(), admin_policy(MissingAdminHours.REJECT))
    assert {e.message for e in errors} == {f"admin hours missing for staff {sid(2)}.", f"admin hours missing for staff {sid(3)}."}
    offsets, missing = staff_offsets(roster_input, data.fairness_inputs(), admin_policy(MissingAdminHours.ZERO))
    assert missing == (sid(2), sid(3)) and offsets[sid(1)] == 75  # reported as missing, counted 0 only by policy


async def test_workload_can_be_skipped():
    fake = three_staff_with_workloads()
    data = await load_academic_staff_data(client_for(fake), TOKEN, institution_id=INST, eligibility=POLICY,
                                          include_workload=False)
    assert data.admin == () and data.missing_workload == ()
    assert not any(r.url.path.endswith("/administrative-workload") for r in fake.requests)


@pytest.mark.parametrize("bad", [
    {"staff_id": sid(1), "institution_id": str(INST)},  # no aggregate
    workload_row(1, 5.0, status="ESTIMATED"),
])
async def test_malformed_workload_responses(bad):
    fake = FakeAcademic([staff_row(1)], {sid(1): bad})
    with pytest.raises(AcademicDataMalformed):
        await load_academic_staff_data(client_for(fake), TOKEN, institution_id=INST, eligibility=POLICY)


async def test_invalid_workload_values_become_malformed_errors():
    fake = FakeAcademic([staff_row(1)], {sid(1): workload_row(1, -4.0)})
    with pytest.raises(AcademicDataMalformed, match="hours_per_week"):
        await load_academic_staff_data(client_for(fake), TOKEN, institution_id=INST, eligibility=POLICY)


async def test_workload_for_a_different_staff_member_is_rejected():
    fake = FakeAcademic([staff_row(1)], {sid(1): workload_row(9, 2.0)})
    with pytest.raises(AcademicDataMalformed, match="different staff"):
        await client_for(fake).get_administrative_workload(TOKEN, sid(1), institution_id=INST)


# --- HTTP errors, network failures ---------------------------------------------------------------------

@pytest.mark.parametrize("status,error,remote", [
    (401, AcademicDataUnauthenticated, "UNAUTHENTICATED"), (403, AcademicDataForbidden, "FORBIDDEN"),
    (404, AcademicDataNotFound, "NOT_FOUND"), (503, AcademicDataUnavailable, "SERVICE_UNAVAILABLE"),
    (500, AcademicDataUnavailable, "INTERNAL_ERROR"), (429, AcademicDataUnavailable, "RATE_LIMITED"),
])
async def test_http_errors_are_typed(status, error, remote):
    fake = FakeAcademic([], override={"/staff": (status, {"error": {"code": remote, "message": "x"}})})
    with pytest.raises(error) as excinfo:
        await client_for(fake).list_staff(TOKEN, institution_id=INST)
    assert excinfo.value.status == status and excinfo.value.remote_code == remote


async def test_wrong_token_is_unauthenticated_and_empty_token_is_never_sent():
    fake = FakeAcademic([staff_row(1)])
    with pytest.raises(AcademicDataUnauthenticated):
        await client_for(fake).list_staff("someone-else", institution_id=INST)
    with pytest.raises(AcademicDataUnauthenticated):
        await client_for(fake).list_staff("  ", institution_id=INST)
    assert len(fake.requests) == 1  # only the first call reached the service


async def test_permission_error_on_any_workload_fails_the_whole_fetch():
    fake = FakeAcademic([staff_row(1), staff_row(2)], {sid(1): workload_row(1, 1.0)},
                        override={f"/staff/{sid(2)}/administrative-workload": (403, {"error": {"code": "FORBIDDEN"}})})
    with pytest.raises(AcademicDataForbidden):
        await load_academic_staff_data(client_for(fake), TOKEN, institution_id=INST, eligibility=POLICY)


@pytest.mark.parametrize("failure", [httpx.ConnectError("refused"), httpx.ReadTimeout("slow"), httpx.ConnectTimeout("slow")])
async def test_network_failures_and_timeouts(failure, caplog):
    def handler(request):
        raise failure
    client = AcademicDataClient(BASE, http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with caplog.at_level(logging.WARNING), pytest.raises(AcademicDataUnavailable):
        await client.list_staff(TOKEN, institution_id=INST)
    assert TOKEN not in caplog.text and caplog.records


async def test_token_is_sent_as_bearer_header_and_never_logged(caplog):
    fake = FakeAcademic([], override={"/staff": (503, {"error": {"code": "SERVICE_UNAVAILABLE"}})})
    with caplog.at_level(logging.DEBUG), pytest.raises(AcademicDataUnavailable):
        await client_for(fake).list_staff(TOKEN, institution_id=INST)
    assert fake.requests[0].headers["Authorization"] == f"Bearer {TOKEN}"
    assert TOKEN not in caplog.text and TOKEN not in str(fake.requests[0].url)


# --- Malformed responses ------------------------------------------------------------------------------

@pytest.mark.parametrize("body", [
    b"not json",
    {"items": "x", "total": 1, "page": 1, "page_size": 100},
    {"items": [], "page": 1, "page_size": 100},  # no total
    {"items": [], "total": -1, "page": 1, "page_size": 100},
    {"items": [], "total": 0, "page": 2, "page_size": 100},  # wrong page
    {"items": ["x"], "total": 1, "page": 1, "page_size": 100},
    {"items": [staff_row(1) | {"id": "EMP001"}], "total": 1, "page": 1, "page_size": 100},
    [staff_row(1)],
])
async def test_malformed_staff_pages(body):
    with pytest.raises(AcademicDataMalformed):
        await client_for(FakeAcademic([], override={"/staff": (200, body)})).list_staff(TOKEN, institution_id=INST)


async def test_unreadable_staff_records_are_reported_by_import_staff():
    bad = staff_row(2)
    del bad["full_name"]
    result = await load_staff(client_for(FakeAcademic([staff_row(1), bad])), TOKEN, institution_id=INST, eligibility=POLICY)
    assert [m.staff_id for m in result.staff] == [sid(1)] and result.violations[0].constraint == STAFF_RECORD


# --- Institution safety ----------------------------------------------------------------------------

async def test_staff_from_another_institution_fail_closed():
    def leaky(request):
        return 200, {"items": [staff_row(1), staff_row(2, institution=OTHER)], "total": 2, "page": 1, "page_size": 100}
    with pytest.raises(AcademicDataInstitutionMismatch):
        await client_for(FakeAcademic([], override={"/staff": leaky})).list_staff(TOKEN, institution_id=INST)


async def test_only_the_requested_institution_is_asked_for():
    fake = FakeAcademic([staff_row(1), staff_row(2, institution=OTHER)])
    rows = await client_for(fake).list_staff(TOKEN, institution_id=INST)
    assert [r["id"] for r in rows] == [sid(1)]
    with pytest.raises(AcademicDataInstitutionMismatch):  # asking for OTHER but reading staff 1 of INST
        await client_for(fake).get_staff(TOKEN, sid(1), institution_id=OTHER)


async def test_workload_from_another_institution_fails_closed():
    fake = FakeAcademic([staff_row(1)], {sid(1): workload_row(1, 3.0, institution=OTHER)})
    with pytest.raises(AcademicDataInstitutionMismatch):
        await load_academic_staff_data(client_for(fake), TOKEN, institution_id=INST, eligibility=POLICY)


# --- Single staff record -------------------------------------------------------------------------------

async def test_get_staff():
    fake = FakeAcademic([staff_row(1), staff_row(2)])
    row = await client_for(fake).get_staff(TOKEN, uuid.UUID(sid(2)), institution_id=INST)
    assert row["staff_number"] == "EMP002" and fake.requests[0].url.path == f"/api/v1/academic/staff/{sid(2)}"
    with pytest.raises(AcademicDataNotFound):
        await client_for(fake).get_staff(TOKEN, sid(9), institution_id=INST)
    with pytest.raises(AcademicDataMalformed):
        await client_for(fake).get_staff(TOKEN, "not-a-uuid", institution_id=INST)
    wrong = FakeAcademic([], override={f"/staff/{sid(1)}": (200, staff_row(2))})
    with pytest.raises(AcademicDataMalformed, match="different staff"):
        await client_for(wrong).get_staff(TOKEN, sid(1), institution_id=INST)
