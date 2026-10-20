"""Saved rosters, approval workflow, swap / replacement requests and seating drafts (API level).

Persistence mode MEMORY on the mocked compact SYNTHETIC data set: nothing touches a database.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import importlib.util
import uuid

import pytest

from app.duty_allocation.persistence import service as roster_service
from app.duty_allocation.persistence.domain import AssignmentRecord, RosterRecord, RosterStatus
from app.duty_allocation.persistence.published_timetable import PublishedTimetable
from app.duty_allocation.persistence.repository import MemoryDutyRosterRepository
from app.duty_allocation.demo_settings import PersistenceMode
from duty_allocation_demo_support import PERIOD, sid
from test_duty_allocation_api import API, AUTH_INSTITUTION, Env, fake_source, local_settings, ref, run, staff_id_of

pytestmark = pytest.mark.skipif(importlib.util.find_spec("ortools") is None, reason="install the [solver] extra")

COMPONENT = "Fairness-Aware Staff Allocation for Examination Duties"
READ, GENERATE, MANAGE, APPROVE = "examinations:read", "examinations:generate", "examinations:manage", "examinations:approve"


def memory_env(change=None, **settings) -> Env:
    return Env(local_settings(persistence=PersistenceMode.MEMORY, **settings), factory=fake_source(change))


@pytest.fixture
def env() -> Env:
    return memory_env()


def save(env, label=None, kind="fair") -> dict:
    body = {"run_id": run(env, kind)["run_id"], **({"roster": label} if label else {})}
    reply = env.post("/rosters", json=body)
    assert reply.status_code == 201, reply.text
    return reply.json()


def act(env, roster, action, headers=None, **body):
    return env.post(f"/rosters/{roster['roster_id']}/{action}", json={"expected_version": roster["row_version"], **body},
                    headers=headers)


def approved(env) -> dict:
    roster = save(env)
    roster = act(env, roster, "validate").json()
    return act(env, roster, "approve").json()


def memory_state(env):
    return env.app.state.m3_memory_state


def make_publishable(env, monkeypatch):
    """Pretend the data was NOT synthetic and a published timetable exists (only possible in a test)."""
    original = roster_service.assemble_context
    monkeypatch.setattr(roster_service, "assemble_context", lambda ctx: dataclasses.replace(original(ctx), synthetic=False))

    class Provider:
        async def get_published(self, institution_id, period_code):
            context = await env.service.context(period_code)
            draft = context.inputs.timetable.draft
            return PublishedTimetable("PUBLISHED-REF-2026-1", draft.input_fingerprint, draft)

    env.app.state.m3_published_timetable_provider = Provider()


# --- Gate, naming ---------------------------------------------------------------------------------------

def test_saving_is_disabled_by_default_and_status_says_so():
    env = Env(local_settings())
    status = env.get("/status").json()
    assert status["persistence_mode"] == "DISABLED" and "preview only" in status["persistence"]
    for reply in (env.get("/rosters"), env.post("/rosters", json={"run_id": "a" * 32}), env.get("/seating-drafts")):
        assert reply.status_code == 503 and reply.json()["code"] == "M3_PERSISTENCE_DISABLED"


def test_database_mode_needs_the_acknowledgement():
    env = Env(local_settings(persistence=PersistenceMode.DATABASE))
    reply = env.get("/rosters")
    assert reply.status_code == 503 and reply.json()["code"] == "M3_PERSISTENCE_NOT_CONFIGURED"
    assert env.service._snapshot is None  # nothing was read or connected


def test_memory_mode_is_labelled_not_durable(env):
    roster = save(env)
    assert roster["persistence"]["mode"] == "MEMORY" and roster["persistence"]["durable"] is False
    assert roster["persistence"]["component"] == COMPONENT and "lost on restart" in roster["persistence"]["notice"]
    assert "working draft" in roster["notice"] and "SYNTHETIC" in roster["notice"] and roster["can_be_published"] is False


def test_status_reports_memory_mode(env):
    assert env.get("/status").json()["persistence_mode"] == "MEMORY"


# --- Permissions ----------------------------------------------------------------------------------------

def test_every_new_endpoint_requires_a_token(env):
    roster = save(env)
    rid = roster["roster_id"]
    calls = [("get", "/rosters"), ("get", f"/rosters/{rid}"), ("get", f"/rosters/{rid}/history"),
             ("post", "/rosters"), ("post", f"/rosters/{rid}/validate"), ("post", f"/rosters/{rid}/approve"),
             ("post", f"/rosters/{rid}/publish"), ("post", f"/rosters/{rid}/reject"), ("post", f"/rosters/{rid}/revisions"),
             ("post", f"/rosters/{rid}/change-previews/swap"), ("post", f"/rosters/{rid}/change-requests/replacement"),
             ("get", f"/rosters/{rid}/change-requests"), ("get", f"/change-requests/{uuid.uuid4()}"),
             ("post", f"/change-requests/{uuid.uuid4()}/approve"), ("post", f"/change-requests/{uuid.uuid4()}/apply"),
             ("post", f"/seating/{'a' * 32}/save"), ("get", "/seating-drafts"), ("get", f"/seating-drafts/{uuid.uuid4()}")]
    for method, path in calls:
        reply = getattr(env.client, method)(API + path) if method == "get" else env.client.post(API + path, json={})
        assert reply.status_code == 401, (method, path)


def test_permission_matrix(env):
    roster = save(env)
    rid, version = roster["roster_id"], roster["row_version"]
    users = {name: env.headers(*perms) for name, perms in {
        "nobody": (), "reader": (READ,), "generator": (READ, GENERATE), "approver": (READ, APPROVE),
        "manager": (READ, MANAGE), "admin": (READ, GENERATE, MANAGE, APPROVE)}.items()}
    body = {"expected_version": version}
    # (method, path, json, allowed users that pass the permission check)
    cases = [
        ("get", "/rosters", None, {"reader", "generator", "approver", "manager", "admin"}),
        ("get", f"/rosters/{rid}/history", None, {"reader", "generator", "approver", "manager", "admin"}),
        ("post", "/rosters", {"run_id": "0" * 32}, {"generator", "admin"}),
        ("post", f"/rosters/{rid}/validate", body, {"generator", "admin"}),
        ("post", f"/rosters/{rid}/approve", body, {"approver", "admin"}),
        ("post", f"/rosters/{rid}/publish", body, {"approver", "admin"}),
        ("post", f"/rosters/{rid}/reject", {**body, "reason": "x"}, {"approver", "manager", "admin"}),
        ("post", f"/rosters/{rid}/revisions", {**body, "reason": "x"}, {"manager", "admin"}),
        ("post", f"/rosters/{rid}/change-previews/swap", {"first": ref(roster_assignment(roster)), "second": ref(roster_assignment(roster)),
                                                         "reason": "x"}, {"generator", "admin"}),
        ("post", f"/change-requests/{uuid.uuid4()}/approve", {}, {"approver", "admin"}),
        ("post", f"/change-requests/{uuid.uuid4()}/apply", {}, {"approver", "admin"}),
        ("post", f"/change-requests/{uuid.uuid4()}/cancel", {}, {"generator", "admin"}),
        ("post", f"/seating/{'0' * 32}/save", {}, {"generator", "admin"}),
    ]
    for method, path, payload, allowed in cases:
        for name, headers in users.items():
            if name in allowed:
                continue  # the allowed side is covered by the workflow tests
            reply = env.client.get(API + path, headers=headers) if method == "get" else \
                env.client.post(API + path, json=payload, headers=headers)
            assert reply.status_code == 403, (method, path, name, reply.status_code)


def roster_assignment(roster):
    a = roster["assignments"][0]
    return {"session_id": a["session_id"], "venue_id": a["venue_id"], "staff_id": a["staff_id"], "role": a["role"]}


def test_a_role_name_never_grants_permissions(env):
    roster = save(env)
    body = {"expected_version": roster["row_version"], "role": "EXAMINATION_ADMIN"}
    assert env.post(f"/rosters/{roster['roster_id']}/validate", json=body).status_code == 422  # unknown field refused
    assert env.post("/rosters", json={"run_id": "a" * 32, "roles": ["EXAMINATION_ADMIN"], "permissions": [APPROVE]}).status_code == 422
    sneaky = {**env.headers(READ), "X-Role": "EXAMINATION_ADMIN", "X-Permissions": APPROVE, "X-Institution-Role": "ADMIN"}
    assert env.post(f"/rosters/{roster['roster_id']}/approve", json={}, headers=sneaky).status_code == 403
    assert env.post(f"/rosters/{roster['roster_id']}/validate", json={}, headers=sneaky).status_code == 403


# --- Save, versions -------------------------------------------------------------------------------------

def test_save_draft_stores_everything_that_was_asked(env):
    roster = save(env)
    assert roster["status"] == "DRAFT" and roster["version"] == 1 and roster["row_version"] == 1
    assert roster["period_code"] == PERIOD and roster["algorithm_mode"] == "FAIR" and roster["roster_label"] == "FAIR"
    assert roster["timetable_reference"].startswith("DEMO-UNPUBLISHED:") and roster["timetable_verified"] is False
    assert roster["fairness_metrics"]["gini"] >= 0 and roster["fairness_metrics"]["per_staff"]
    assert roster["validation"]["valid"] is True and roster["assignment_count"] == 8 and len(roster["assignments"]) == 8
    assert roster["created_by"] and roster["created_at"] and roster["synthetic"] is True
    assert roster["data_source"] == "LOCAL_DEMO" and roster["next_actions"] == ["VALIDATE", "REJECT"]
    first = roster["assignments"][0]
    assert {"session_id", "venue_id", "staff_id", "staff_number", "role", "module_code", "starts_at", "ends_at"} <= set(first)
    again = env.get(f"/rosters/{roster['roster_id']}").json()
    assert again["assignments"] == roster["assignments"] and again["row_version"] == 1


def test_baseline_and_compare_runs_can_be_saved_with_their_mode(env):
    compare = run(env, "compare")["run_id"]
    saved = env.post("/rosters", json={"run_id": compare, "roster": "BASELINE"}).json()
    assert saved["algorithm_mode"] == "COMPARE" and saved["roster_label"] == "BASELINE"


def test_only_one_working_roster_per_period(env):
    first = save(env)
    clash = env.post("/rosters", json={"run_id": run(env, "baseline")["run_id"]})
    assert clash.status_code == 409 and clash.json()["code"] == "ACTIVE_ROSTER_EXISTS"
    assert clash.json()["details"]["roster_id"] == first["roster_id"]
    act(env, first, "reject", reason="Wrong algorithm")
    second = save(env, kind="baseline")
    assert second["version"] == 2 and second["status"] == "DRAFT"
    items = env.get("/rosters", params={"period_code": PERIOD}).json()
    assert [(i["version"], i["status"]) for i in items["items"]] == [(2, "DRAFT"), (1, "REJECTED")]
    assert env.get("/rosters", params={"status": "REJECTED"}).json()["total"] == 1
    assert env.get("/rosters", params={"status": "ALMOST"}).status_code == 422


def test_a_failed_or_unknown_run_cannot_be_saved(env):
    assert env.post("/rosters", json={"run_id": "f" * 32}).json()["code"] == "M3_PREVIEW_NOT_FOUND"
    assert env.post("/rosters", json={"run_id": "nope"}).status_code == 422
    infeasible = memory_env(lambda t: [row.update(employment_status="INACTIVE") for row in t["staff"][2:4]])
    reply = infeasible.post("/rosters", json={"run_id": run(infeasible, "baseline")["run_id"]})
    assert reply.status_code == 422 and reply.json()["code"] == "RUN_NOT_COMPLETED"
    assert infeasible.get("/rosters").json()["total"] == 0
    only_fair = run(env, "baseline")["run_id"]
    assert env.post("/rosters", json={"run_id": only_fair, "roster": "FAIR"}).json()["code"] == "ROSTER_NOT_IN_RUN"


def test_a_roster_with_a_staff_conflict_in_another_period_is_not_saved(env):
    state = memory_state(env)
    repo = MemoryDutyRosterRepository(state)
    start = dt.datetime(2026, 11, 16, 6, 0)
    other = RosterRecord(uuid.uuid4(), AUTH_INSTITUTION, "OTHER-PERIOD", 1, RosterStatus.DRAFT, 1, "FAIR", "FAIR", "ref", "fp",
                         False, "0" * 64, "LOCAL_DEMO", True, {}, {}, uuid.uuid4(), start, start)
    everyone = [AssignmentRecord(f"S{i}", "H1", sid(n), "INVIGILATOR", None, "X", start, start + dt.timedelta(hours=14))
                for i, n in enumerate(range(1, 8))]
    await_sync(repo.insert_roster(other, everyone))
    await_sync(repo.commit())
    reply = env.post("/rosters", json={"run_id": run(env, "fair")["run_id"]})
    assert reply.status_code == 409 and reply.json()["code"] == "STAFF_CONFLICT" and reply.json()["details"]["conflicts"]
    assert env.get("/rosters", params={"period_code": PERIOD}).json()["total"] == 0


def await_sync(coro):
    import asyncio
    return asyncio.new_event_loop().run_until_complete(coro)


# --- Lifecycle ------------------------------------------------------------------------------------------

def test_validate_then_approve_then_publish_is_refused_for_synthetic_data(env):
    roster = save(env)
    validated = act(env, roster, "validate")
    assert validated.status_code == 200 and validated.json()["status"] == "VALIDATED"
    assert validated.json()["row_version"] == 2 and validated.json()["next_actions"] == ["VALIDATE", "APPROVE", "REJECT"]
    approved_roster = act(env, validated.json(), "approve", note="Looks fair")
    assert approved_roster.json()["status"] == "APPROVED" and approved_roster.json()["next_actions"] == ["REJECT"]
    refused = act(env, approved_roster.json(), "publish")
    assert refused.status_code == 409 and refused.json()["code"] == "SYNTHETIC_ROSTER_CANNOT_BE_PUBLISHED"
    assert env.get(f"/rosters/{roster['roster_id']}").json()["status"] == "APPROVED"
    actions = [e["action"] for e in env.get(f"/rosters/{roster['roster_id']}/history").json()["events"]]
    assert actions == ["CREATED", "VALIDATED", "APPROVED", "PUBLISH_REFUSED"]


def test_transitions_must_follow_the_lifecycle(env):
    roster = save(env)
    for action, code in (("approve", "INVALID_TRANSITION"), ("publish", "INVALID_TRANSITION")):
        reply = act(env, roster, action)
        assert reply.status_code == 409 and reply.json()["code"] == code
    assert act(env, roster, "revisions", reason="x").status_code == 409  # a DRAFT is not revised
    rejected = act(env, roster, "reject", reason="Not needed").json()
    assert rejected["status"] == "REJECTED" and rejected["rejection_reason"] == "Not needed" and rejected["next_actions"] == ["REVISE"]
    for action in ("validate", "approve", "publish", "reject"):
        assert act(env, rejected, action, **({"reason": "again"} if action == "reject" else {})).status_code == 409


def test_stale_versions_are_refused(env):
    roster = save(env)
    assert act(env, roster, "validate").status_code == 200
    stale = act(env, roster, "validate")  # still sends version 1
    assert stale.status_code == 409 and stale.json()["code"] == "STALE_ROSTER"
    assert stale.json()["details"] == {"current_version": 2, "expected_version": 1}
    assert env.post(f"/rosters/{roster['roster_id']}/validate", json={"expected_version": 0}).status_code == 422


def test_reject_needs_a_reason(env):
    roster = save(env)
    for reason in ("", "x" * 501):
        assert act(env, roster, "reject", reason=reason).status_code == 422
    assert env.post(f"/rosters/{roster['roster_id']}/reject", json={"expected_version": 1}).status_code == 422


def test_hard_constraint_violations_block_validation_and_approval(env):
    roster = save(env)
    rid = uuid.UUID(roster["roster_id"])
    work = memory_state(env).data.assignments[rid]
    inactive = staff_id_of(env, 8)
    broken = [dataclasses.replace(a, staff_id=inactive) if i == 0 else a for i, a in enumerate(work)]
    memory_state(env).data.assignments[rid] = broken  # tampering behind the API's back
    blocked = act(env, roster, "validate")
    assert blocked.status_code == 422 and blocked.json()["code"] == "ROSTER_HAS_VIOLATIONS" and blocked.json()["details"]["violations"]
    current = env.get(f"/rosters/{rid}").json()
    assert current["status"] == "DRAFT" and current["validation"]["valid"] is False and current["validation"]["violations"]
    # a valid roster that is damaged after validation cannot be approved either
    memory_state(env).data.assignments[rid] = work
    validated = act(env, env.get(f"/rosters/{rid}").json(), "validate").json()
    memory_state(env).data.assignments[rid] = broken
    refused = act(env, validated, "approve")
    assert refused.status_code == 422 and refused.json()["code"] == "ROSTER_HAS_VIOLATIONS"
    assert env.get(f"/rosters/{rid}").json()["status"] == "VALIDATED"
    events = [e["action"] for e in env.get(f"/rosters/{rid}/history").json()["events"]]
    assert "VALIDATION_FAILED" in events and "APPROVAL_REFUSED" in events


def test_changed_inputs_block_validation_until_a_revision_is_made(env):
    roster = save(env)
    env.service._row_source = fake_source(lambda t: t["staff_unavailability"].append(dict(
        unavailability_id="U9", staff_id=sid(4), start_at=dt.datetime(2026, 11, 16, 7), end_at=dt.datetime(2026, 11, 16, 18),
        reason="new leave", source="SYNTHETIC")))
    assert env.post("/snapshot/refresh").status_code == 200
    reply = act(env, roster, "validate")
    assert reply.status_code == 409 and reply.json()["code"] == "ROSTER_INPUT_CHANGED"
    assert env.get(f"/rosters/{roster['roster_id']}").json()["status"] == "DRAFT"


# --- Publishing and controlled revisions ----------------------------------------------------------------

def test_publishing_fails_closed_without_the_timetable_contract(env, monkeypatch):
    monkeypatch.setattr(roster_service, "assemble_context",
                        (lambda original: lambda ctx: dataclasses.replace(original(ctx), synthetic=False))(roster_service.assemble_context))
    roster = approved(env)
    reply = act(env, roster, "publish")  # no provider installed: the default one is unavailable
    assert reply.status_code == 503 and reply.json()["code"] == "PUBLISHED_TIMETABLE_UNAVAILABLE"
    assert env.get(f"/rosters/{roster['roster_id']}").json()["status"] == "APPROVED"


def test_publishing_checks_the_published_timetable(env, monkeypatch):
    make_publishable(env, monkeypatch)
    roster = approved(env)
    assert roster["can_be_published"] is True and "PUBLISH" in roster["next_actions"]

    class Empty:
        async def get_published(self, *_):
            return None
    env.app.state.m3_published_timetable_provider = Empty()
    assert act(env, roster, "publish").json()["code"] == "PUBLISHED_TIMETABLE_NOT_FOUND"

    class Different:
        async def get_published(self, institution_id, period_code):
            context = await env.service.context(period_code)
            draft = context.inputs.timetable.draft
            return PublishedTimetable("REF-9", "another-fingerprint", draft)
    env.app.state.m3_published_timetable_provider = Different()
    mismatch = act(env, roster, "publish")
    assert mismatch.status_code == 409 and mismatch.json()["code"] == "TIMETABLE_INCOMPATIBLE"
    assert env.get(f"/rosters/{roster['roster_id']}").json()["status"] == "APPROVED"

    make_publishable(env, monkeypatch)
    published = act(env, roster, "publish")
    assert published.status_code == 200, published.text
    body = published.json()
    assert body["status"] == "PUBLISHED" and body["timetable_verified"] is True
    assert body["published_timetable_reference"] == "PUBLISHED-REF-2026-1" == body["timetable_reference"]
    assert body["published_by"] and body["published_at"] and body["next_actions"] == ["REVISE"]


def test_published_roster_changes_only_through_a_revision(env, monkeypatch):
    make_publishable(env, monkeypatch)
    published = act(env, approved(env), "publish").json()
    rid = published["roster_id"]
    detail = env.get(f"/rosters/{rid}").json()
    swap = {"first": ref(roster_assignment(detail)), "second": ref(roster_assignment(detail)), "reason": "x"}
    blocked = env.post(f"/rosters/{rid}/change-previews/swap", json=swap)
    assert blocked.status_code == 409 and blocked.json()["code"] == "REVISION_REQUIRED"
    assert env.post(f"/rosters/{rid}/change-requests/swap", json=swap).json()["code"] == "REVISION_REQUIRED"
    assert act(env, published, "reject", reason="no").json()["code"] == "INVALID_TRANSITION"
    revision = act(env, published, "revisions", reason="Emergency change").json()
    assert revision["version"] == 2 and revision["status"] == "DRAFT" and revision["parent_roster_id"] == rid
    assert revision["timetable_verified"] is False and revision["assignments"] == detail["assignments"]
    assert env.get(f"/rosters/{rid}").json()["status"] == "PUBLISHED"  # still in force
    assert act(env, published, "revisions", reason="again").json()["code"] in ("STALE_ROSTER", "ACTIVE_ROSTER_EXISTS")
    again = act(env, env.get(f"/rosters/{rid}").json(), "revisions", reason="again")
    assert again.status_code == 409 and again.json()["code"] == "ACTIVE_ROSTER_EXISTS"
    # the revision goes through the full lifecycle, then replaces the published roster
    validated = act(env, revision, "validate").json()
    ready = act(env, validated, "approve").json()
    assert act(env, ready, "publish").status_code == 200
    assert env.get(f"/rosters/{rid}").json()["status"] == "SUPERSEDED"
    actions = [e["action"] for e in env.get(f"/rosters/{rid}/history").json()["events"]]
    assert "REVISION_CREATED" in actions and "SUPERSEDED" in actions


def test_a_rejected_roster_can_be_revised(env):
    roster = save(env)
    rejected = act(env, roster, "reject", reason="Needs different staff").json()
    revision = act(env, rejected, "revisions", reason="Try again")
    assert revision.status_code == 201 and revision.json()["version"] == 2 and revision.json()["status"] == "DRAFT"


# --- Swaps and emergency replacements -------------------------------------------------------------------

def invigilator_swap(env, roster):
    invigilators = [a for a in roster["assignments"] if a["role"] == "INVIGILATOR"]
    for x in invigilators:
        for y in invigilators:
            if x["staff_id"] < y["staff_id"]:
                preview = env.post(f"/rosters/{roster['roster_id']}/change-previews/swap",
                                   json={"first": ref(x), "second": ref(y), "reason": "Swap"}).json()
                if preview["status"] == "PROPOSED":
                    return {"first": ref(x), "second": ref(y), "reason": "Swap"}
    raise AssertionError("no valid invigilator swap in the compact fixture")


def replacement_body(roster, reason="Sick leave", **extra):
    duty = next(a for a in roster["assignments"] if a["role"] == "INVIGILATOR")
    return {"duty": ref(duty), "reason": reason, **extra}


def test_change_preview_validates_and_changes_nothing(env):
    roster = save(env)
    rid = roster["roster_id"]
    swap = invigilator_swap(env, roster)
    preview = env.post(f"/rosters/{rid}/change-previews/swap", json=swap).json()
    assert preview["status"] == "PROPOSED" and preview["applied"] is False and preview["independent_check_violations"] == []
    assert len(preview["removed"]) == len(preview["added"]) == 2 and preview["proposed_metrics"]["per_staff"]
    assert preview["notice"].startswith("PREVIEW ONLY") and preview["roster_version"] == 1
    replacement = env.post(f"/rosters/{rid}/change-previews/replacement", json=replacement_body(roster)).json()
    assert replacement["status"] == "PROPOSED" and replacement["candidates"] and replacement["rejected"]
    assert env.get(f"/rosters/{rid}/change-requests").json() == []
    assert env.get(f"/rosters/{rid}").json()["row_version"] == 1


def test_swap_request_approve_apply(env):
    roster = save(env)
    rid, swap = roster["roster_id"], invigilator_swap(env, roster)
    request = env.post(f"/rosters/{rid}/change-requests/swap", json={**swap, "expected_version": 1})
    assert request.status_code == 201, request.text
    req = request.json()
    assert req["status"] == "REQUESTED" and req["kind"] == "SWAP" and req["base_row_version"] == 1
    assert req["next_actions"] == ["APPROVE", "REJECT", "CANCEL"] and "not applied" in req["notice"]
    unchanged = env.get(f"/rosters/{rid}").json()
    assert unchanged["assignments"] == roster["assignments"]  # a request changes nothing
    early = env.post(f"/change-requests/{req['request_id']}/apply")
    assert early.status_code == 409 and early.json()["code"] == "INVALID_TRANSITION"
    approved_req = env.post(f"/change-requests/{req['request_id']}/approve", json={"reason": "OK"}).json()
    assert approved_req["status"] == "APPROVED" and approved_req["decision_reason"] == "OK" and approved_req["next_actions"] == ["APPLY", "CANCEL"]
    applied = env.post(f"/change-requests/{req['request_id']}/apply")
    assert applied.status_code == 200, applied.text
    out = applied.json()
    assert out["request"]["status"] == "APPLIED" and out["request"]["applied_by"] and out["roster"]["row_version"] == 2
    changed = env.get(f"/rosters/{rid}").json()
    before = {(a["session_id"], a["venue_id"], a["role"]): a["staff_id"] for a in roster["assignments"]}
    after = {(a["session_id"], a["venue_id"], a["role"]): a["staff_id"] for a in changed["assignments"]}
    first, second = swap["first"], swap["second"]
    assert after[(first["session_id"], first["venue_id"], first["role"])] == second["staff_id"]
    assert after[(second["session_id"], second["venue_id"], second["role"])] == first["staff_id"]
    assert {k for k in before if before[k] != after[k]} == {(first["session_id"], first["venue_id"], first["role"]),
                                                            (second["session_id"], second["venue_id"], second["role"])}
    assert changed["validation"]["valid"] is True and len(changed["assignments"]) == 8
    assert env.post(f"/change-requests/{req['request_id']}/apply").json()["code"] == "INVALID_TRANSITION"  # only once
    audit = [e["action"] for e in env.get(f"/rosters/{rid}/history").json()["events"]]
    assert audit == ["CREATED", "CHANGE_REQUESTED", "CHANGE_APPROVED", "CHANGE_APPLIED"]


def test_applying_a_change_to_an_approved_roster_withdraws_the_approval(env):
    ready = approved(env)
    rid = ready["roster_id"]
    detail = env.get(f"/rosters/{rid}").json()
    request = env.post(f"/rosters/{rid}/change-requests/replacement", json=replacement_body(detail)).json()
    env.post(f"/change-requests/{request['request_id']}/approve")
    out = env.post(f"/change-requests/{request['request_id']}/apply").json()
    assert out["roster"]["status"] == "VALIDATED" and out["roster"]["approved_by"] is None
    assert "approval, if any, was withdrawn" in out["request"]["notice"]
    new_duty = out["request"]["added"][0]
    assert new_duty["staff_id"] != request["removed"][0]["staff_id"] and new_duty["session_id"] == request["removed"][0]["session_id"]
    again = act(env, out["roster"], "approve")  # must be approved again
    assert again.status_code == 200 and again.json()["status"] == "APPROVED"


def test_stale_and_conflicting_change_requests(env):
    roster = save(env)
    rid = roster["roster_id"]
    first = env.post(f"/rosters/{rid}/change-requests/replacement", json=replacement_body(roster)).json()
    duplicate = env.post(f"/rosters/{rid}/change-requests/replacement", json=replacement_body(roster))
    assert duplicate.status_code == 409 and duplicate.json()["code"] == "CONFLICTING_REQUEST"
    other = next(a for a in roster["assignments"] if a["role"] == "INVIGILATOR" and a["session_id"] != first["removed"][0]["session_id"])
    second = env.post(f"/rosters/{rid}/change-requests/replacement",
                      json={"duty": ref(other), "reason": "Second"}).json()
    env.post(f"/change-requests/{first['request_id']}/approve")
    assert env.post(f"/change-requests/{first['request_id']}/apply").status_code == 200
    # the second request was made against version 1; the roster is now at version 2
    stale = env.post(f"/change-requests/{second['request_id']}/approve")
    assert stale.status_code == 409 and stale.json()["code"] == "STALE_CHANGE_REQUEST"
    assert env.get(f"/change-requests/{second['request_id']}").json()["status"] == "STALE"
    stale_version = env.post(f"/rosters/{rid}/change-requests/replacement",
                             json={**replacement_body(env.get(f"/rosters/{rid}").json()), "expected_version": 1})
    assert stale_version.status_code == 409 and stale_version.json()["code"] == "STALE_ROSTER"


def test_approved_request_goes_stale_when_the_roster_changes_first(env):
    roster = save(env)
    rid = roster["roster_id"]
    duty_a = replacement_body(roster)
    other = next(a for a in roster["assignments"] if a["role"] == "INVIGILATOR" and a["session_id"] != duty_a["duty"]["session_id"])
    one = env.post(f"/rosters/{rid}/change-requests/replacement", json=duty_a).json()
    two = env.post(f"/rosters/{rid}/change-requests/replacement", json={"duty": ref(other), "reason": "Other"}).json()
    env.post(f"/change-requests/{one['request_id']}/approve")
    env.post(f"/change-requests/{two['request_id']}/approve")
    assert env.post(f"/change-requests/{one['request_id']}/apply").status_code == 200
    late = env.post(f"/change-requests/{two['request_id']}/apply")
    assert late.status_code == 409 and late.json()["code"] == "STALE_CHANGE_REQUEST"
    assert env.get(f"/change-requests/{two['request_id']}").json()["status"] == "STALE"
    assert len(env.get(f"/rosters/{rid}").json()["assignments"]) == 8


def test_reject_and_cancel_change_requests(env):
    roster = save(env)
    rid = roster["roster_id"]
    first = env.post(f"/rosters/{rid}/change-requests/replacement", json=replacement_body(roster)).json()
    assert env.post(f"/change-requests/{first['request_id']}/reject", json={}).status_code == 422  # reason required
    rejected = env.post(f"/change-requests/{first['request_id']}/reject", json={"reason": "Not needed"}).json()
    assert rejected["status"] == "REJECTED" and rejected["decision_reason"] == "Not needed" and rejected["next_actions"] == []
    assert env.post(f"/change-requests/{first['request_id']}/approve").json()["code"] == "INVALID_TRANSITION"
    second = env.post(f"/rosters/{rid}/change-requests/replacement", json=replacement_body(roster, "Again")).json()
    other_generator = env.headers(READ, GENERATE)  # a different user
    denied = env.post(f"/change-requests/{second['request_id']}/cancel", json={}, headers=other_generator)
    assert denied.status_code == 403 and denied.json()["code"] == "NOT_YOUR_REQUEST"
    manager = env.headers(READ, GENERATE, MANAGE)
    cancelled = env.post(f"/change-requests/{second['request_id']}/cancel", json={"reason": "Withdrawn"}, headers=manager)
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "CANCELLED"
    assert env.get(f"/change-requests/{uuid.uuid4()}").status_code == 404


def test_invalid_changes_are_not_stored(env):
    roster = save(env)
    rid, detail = roster["roster_id"], roster
    supervisor = next(a for a in detail["assignments"] if a["role"] == "SUPERVISOR")
    lecturer = staff_id_of(env, 5)  # a Lecturer cannot supervise
    bad = env.post(f"/rosters/{rid}/change-requests/replacement", json={
        "duty": ref(supervisor), "reason": "Cover", "preferred_staff_id": lecturer})
    assert bad.status_code == 409 and bad.json()["code"] == "CHANGE_NOT_VALID"
    assert any("not eligible for SUPERVISOR by rank" in r for r in bad.json()["details"]["reasons"])
    assert env.get(f"/rosters/{rid}/change-requests").json() == []  # nothing was stored
    swap_same = env.post(f"/rosters/{rid}/change-requests/swap", json={"first": ref(supervisor), "second": ref(supervisor), "reason": "x"})
    assert swap_same.status_code == 409 and swap_same.json()["code"] == "CHANGE_NOT_VALID"
    ghost = {**ref(supervisor), "staff_id": str(uuid.uuid4())}
    assert env.post(f"/rosters/{rid}/change-requests/swap", json={"first": ref(supervisor), "second": ghost, "reason": "x"}).status_code == 409
    for body in ({"first": ref(supervisor)}, {"duty": ref(supervisor), "reason": "x", "unavailable_from": "2026-11-16T07:00:00"}):
        assert env.post(f"/rosters/{rid}/change-requests/swap", json=body).status_code == 422


def test_rejecting_a_roster_makes_its_open_requests_stale(env):
    roster = save(env)
    request = env.post(f"/rosters/{roster['roster_id']}/change-requests/replacement", json=replacement_body(roster)).json()
    act(env, roster, "reject", reason="Dropped")
    assert env.get(f"/change-requests/{request['request_id']}").json()["status"] == "STALE"


# --- Seating drafts -------------------------------------------------------------------------------------

def test_seating_draft_is_saved_with_logical_and_provisional_seats(env):
    seating_id = env.post("/seating").json()["seating_id"]
    saved = env.post(f"/seating/{seating_id}/save")
    assert saved.status_code == 201, saved.text
    draft = saved.json()
    assert draft["status"] == "DRAFT" and draft["version"] == 1 and draft["seat_state"] == "SYNTHETIC" and draft["synthetic"] is True
    assert draft["summary"]["assignments"] == 11 and draft["summary"]["all_seats_verified"] is False
    assert "not verified" in draft["notice"] and draft["persistence"]["mode"] == "MEMORY"
    room = env.get(f"/seating/{seating_id}").json()["rooms"][0]
    seats = env.get(f"/seating-drafts/{draft['draft_id']}/rooms/{room['session_id']}/{room['venue_id']}").json()
    assert seats["students"] == room["students"] and seats["seat_state"] == "SYNTHETIC"
    first = seats["seats"][0]
    assert {"session_id", "venue_id", "student_id", "module_code", "logical_seat_number", "physical_seat_number",
            "physical_seat_label", "verification_status"} <= set(first)
    assert all(s["verification_status"] == "SYNTHETIC" for s in seats["seats"])  # never VERIFIED without a verified layout
    numbers = [s["physical_seat_number"] for s in seats["seats"]]
    assert numbers == sorted(set(numbers))


def test_a_new_seating_draft_supersedes_the_previous_one(env):
    first = env.post(f"/seating/{env.post('/seating').json()['seating_id']}/save").json()
    second = env.post(f"/seating/{env.post('/seating').json()['seating_id']}/save").json()
    assert second["version"] == 2
    assert env.get(f"/seating-drafts/{first['draft_id']}").json()["status"] == "SUPERSEDED"
    listing = env.get("/seating-drafts", params={"period_code": PERIOD}).json()
    assert [(d["version"], d["status"]) for d in listing["items"]] == [(2, "DRAFT"), (1, "SUPERSEDED")]


def test_seating_draft_errors(env):
    assert env.post(f"/seating/{'0' * 32}/save").json()["code"] == "M3_PREVIEW_NOT_FOUND"
    assert env.post("/seating/not-an-id/save").status_code == 422
    assert env.get(f"/seating-drafts/{uuid.uuid4()}").json()["code"] == "SEATING_DRAFT_NOT_FOUND"
    seating_id = env.post("/seating").json()["seating_id"]
    draft = env.post(f"/seating/{seating_id}/save").json()
    assert env.get(f"/seating-drafts/{draft['draft_id']}/rooms/NOPE/H1").status_code == 404
    run_id = run(env, "fair")["run_id"]
    assert env.post(f"/seating/{run_id}/save").json()["code"] == "M3_PREVIEW_NOT_FOUND"  # a duty run is not a seating plan


# --- Isolation, documentation ---------------------------------------------------------------------------

def test_saved_rosters_belong_to_the_callers_institution_only(env):
    roster = save(env)
    other = MemoryDutyRosterRepository(memory_state(env))
    assert await_sync(other.get_roster(uuid.UUID(roster["roster_id"]), uuid.uuid4())) is None
    assert await_sync(other.get_roster(uuid.UUID(roster["roster_id"]), AUTH_INSTITUTION)) is not None
    assert env.get(f"/rosters/{uuid.uuid4()}").json()["code"] == "ROSTER_NOT_FOUND"
    assert roster["data_source"] == "LOCAL_DEMO" and roster["synthetic"] is True
    assert roster["created_by"] != str(AUTH_INSTITUTION)


def test_openapi_documents_the_workflow_with_the_official_name(env):
    spec = env.client.get("/openapi.json").json()
    new = {(m.upper(), p) for p, v in spec["paths"].items() if p.startswith(API) for m in v
           if any("saved drafts and approval" in t for t in v[m]["tags"])}
    assert len(new) == 23
    tags = {t for p, v in spec["paths"].items() if p.startswith(API) for op in v.values() for t in op["tags"]}
    assert f"{COMPONENT} (saved drafts and approval)" in tags and f"{COMPONENT} (PREVIEW ONLY)" in tags
    assert all(spec["paths"][p][m.lower()].get("summary") for m, p in new)
