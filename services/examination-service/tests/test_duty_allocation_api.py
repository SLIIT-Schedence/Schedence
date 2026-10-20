"""Member 3 duty-allocation preview API: auth, source gating, data, allocation, swaps, seating.

Real RS256 tokens and a fake Identity Service (as in test_seating_api.py). The data comes from
mocked row sources (the compact SYNTHETIC fixture, or the seed-pack CSVs when present);
nothing connects to a database and nothing is written anywhere.
"""

from __future__ import annotations

import importlib.util
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.duty_allocation.demo_database import (
    DemoDatabaseUnavailable, DemoSchemaError, DemoTargetRefused,
)
from app.duty_allocation.demo_production import DemoRoleRefused
from app.duty_allocation.demo_service import default_row_source
from app.duty_allocation.demo_settings import DEMO_INSTITUTION_ID, DemoSource, Member3DemoSettings
from app.main import create_app
from auth_support import OTHER_INSTITUTION, ROLE_PERMISSIONS, FakeIdentityService, make_auth_context
from duty_allocation_demo_support import PERIOD, SEED_PACK, compact_source, sid
from schedence_auth.testing import TokenFactory

API = "/api/v1/duty-allocation"
SOLVER = pytest.mark.skipif(importlib.util.find_spec("ortools") is None, reason="install the [solver] extra")
AUTH_INSTITUTION = OTHER_INSTITUTION  # the real examination institution; must differ from the synthetic one


def fake_source(change=None, record=None):
    @asynccontextmanager
    async def factory(_settings):
        source = compact_source(change)
        if record is not None:
            record.append(source)
        yield source
    return factory


def local_settings(**kw) -> Member3DemoSettings:
    return Member3DemoSettings(source=DemoSource.LOCAL_DEMO, seed_dir=Path("unused"), **kw)


class Env:
    """App + token helpers for one test."""

    def __init__(self, settings=None, *, factory=None, app_env="testing") -> None:
        self.tokens, self.identity = TokenFactory(), FakeIdentityService()
        auth = make_auth_context(self.tokens, self.identity, institution_id=AUTH_INSTITUTION, app_env=app_env)
        self.app = create_app(auth, m3_demo=settings or local_settings())
        self.service = getattr(self.app.state, "m3_demo_service", None)  # None while the feature is DISABLED
        if self.service is not None:
            self.service._row_source = factory or fake_source()
        self.client = TestClient(self.app)

    def headers(self, *perms: str) -> dict[str, str]:
        sub = self.identity.register({AUTH_INSTITUTION: (["EXAMINATION_ADMIN"], list(perms))})
        return {"Authorization": f"Bearer {self.tokens.access_token(sub)}"}

    @property
    def admin(self) -> dict[str, str]:
        return self.headers(*ROLE_PERMISSIONS["EXAMINATION_ADMIN"])

    def get(self, path, headers=None, **kw):
        return self.client.get(API + path, headers=headers or self.admin, **kw)

    def post(self, path, json=None, headers=None):
        return self.client.post(API + path, json=json if json is not None else {}, headers=headers or self.admin)


@pytest.fixture
def env():
    return Env()


def staff_id_of(env, number):
    return next(s["staff_id"] for s in env.get("/staff", params={"include_excluded": True}).json()["items"]
                if s["staff_number"] == f"EMP{number:04}")


# --- Authentication and authorisation ---------------------------------------------------------------

GETS = ["/status", "/exam-periods", f"/exam-periods/{PERIOD}/sessions", f"/exam-periods/{PERIOD}/requirements", "/staff",
        "/staff/workload", f"/staff/{sid(1)}", f"/staff/{sid(1)}/constraints", f"/allocations/{'a' * 32}",
        f"/allocations/{'a' * 32}/roster", f"/allocations/{'a' * 32}/fairness", f"/seating/{'a' * 32}",
        f"/seating/{'a' * 32}/rooms/S/H1"]
POSTS = ["/snapshot/refresh", "/allocations/baseline", "/allocations/fair", "/allocations/compare",
         f"/allocations/{'a' * 32}/validation", f"/allocations/{'a' * 32}/swap-preview",
         f"/allocations/{'a' * 32}/replacement-preview", "/seating"]


@pytest.mark.parametrize("path", GETS)
def test_get_requires_a_token(env, path):
    assert env.client.get(API + path).status_code == 401


@pytest.mark.parametrize("path", POSTS)
def test_post_requires_a_token(env, path):
    assert env.client.post(API + path, json={}).status_code == 401


@pytest.mark.parametrize("path", GETS)
def test_get_requires_read_permission(env, path):
    assert env.get(path, headers=env.headers()).status_code == 403
    assert env.get(path, headers=env.headers("examinations:generate")).status_code == 403


@pytest.mark.parametrize("path", POSTS)
def test_post_requires_generate_permission(env, path):
    assert env.post(path, headers=env.headers()).status_code == 403
    assert env.post(path, headers=env.headers("examinations:read")).status_code == 403


def test_permission_in_another_institution_is_not_enough(env):
    sub = env.identity.register({uuid.uuid4(): (["EXAMINATION_ADMIN"], list(ROLE_PERMISSIONS["EXAMINATION_ADMIN"]))})
    assert env.get("/status", headers={"Authorization": f"Bearer {env.tokens.access_token(sub)}"}).status_code == 403


def test_only_get_and_post_exist_and_cors_allows_post(env):
    paths = {p: v for p, v in env.client.get("/openapi.json").json()["paths"].items() if p.startswith(API)}
    methods = {m.upper() for v in paths.values() for m in v}
    assert paths and methods == {"GET", "POST"}  # no PUT, PATCH or DELETE: nothing can be saved or removed
    pre = env.client.options(API + "/seating", headers={"Origin": "http://localhost:3000",
                                                        "Access-Control-Request-Method": "POST"})
    assert pre.status_code == 200 and "POST" in pre.headers["access-control-allow-methods"]


# --- Data source gating -------------------------------------------------------------------------------

def test_feature_is_not_mounted_by_default_and_dotenv_is_ignored(tmp_path):
    (tmp_path / ".env").write_text("M3_DEMO_SOURCE=LOCAL_DEMO\n", encoding="utf-8")  # conftest chdir()s here
    assert Member3DemoSettings().source is DemoSource.DISABLED
    env = Env(Member3DemoSettings())  # what create_app() builds from an empty environment
    assert env.service is None
    assert env.get("/status").status_code == 404 and env.get("/exam-periods").status_code == 404
    assert not [p for p in env.client.get("/openapi.json").json()["paths"] if p.startswith(API)]
    assert Env(local_settings()).get("/status").json()["source"] == "LOCAL_DEMO"


def test_neon_without_setup_names_missing_variables_and_never_connects():
    calls = []
    env = Env(Member3DemoSettings(source=DemoSource.NEON_READ_ONLY), factory=fake_source(record=calls))
    status = env.get("/status").json()
    assert status["enabled"] is False and "M3_DEMO_NEON_ENV_FILE" in status["missing_settings"]
    assert "M3_DEMO_NEON_CONFIRMED_IN_CONSOLE=true" in status["missing_settings"]
    assert "M3_DEMO_NEON_CREDENTIAL_ROTATED=true" in status["missing_settings"]
    reply = env.get("/staff")
    assert reply.status_code == 503 and reply.json()["code"] == "M3_DEMO_NOT_CONFIGURED" and calls == []


def neon_settings(**kw) -> Member3DemoSettings:
    from app.duty_allocation.demo_production import PRODUCTION_READ_ONLY_ACKNOWLEDGEMENT
    values = dict(source=DemoSource.NEON_READ_ONLY, neon_env_file=Path("/nonexistent/m3.env"),
                  neon_url_variable="M3_DEMO_DATABASE_URL", neon_host="ep-fake.example.neon.tech", neon_database="neondb",
                  neon_role="m3_demo_reader", neon_confirmed_in_console=True, neon_credential_rotated=True,
                  neon_acknowledgement=PRODUCTION_READ_ONLY_ACKNOWLEDGEMENT)
    return Member3DemoSettings(**{**values, **kw})


def test_neon_configuration_that_passes_the_gate_still_has_to_pass_the_production_guards():
    env = Env(neon_settings(neon_acknowledgement="yes please"))
    env.service._row_source = default_row_source  # the real, guarded reader
    reply = env.get("/exam-periods")
    assert reply.status_code == 403 and reply.json()["code"] == "M3_DEMO_TARGET_REFUSED"
    assert "acknowledgement" in reply.json()["detail"]


@pytest.mark.parametrize("error,status,code", [
    (DemoTargetRefused("The URL host does not match the confirmed endpoint. Not connecting."), 403, "M3_DEMO_TARGET_REFUSED"),
    (DemoRoleRefused("The role can create objects in: public."), 403, "M3_DEMO_ROLE_REFUSED"),
    (DemoDatabaseUnavailable("m3_demo read failed: connection timed out"), 503, "M3_DEMO_DATABASE_UNAVAILABLE"),
    (DemoSchemaError("Incorrect m3_demo schema: m3_demo.staff is missing."), 502, "M3_DEMO_SCHEMA_ERROR"),
])
def test_neon_failures_are_reported_and_never_fall_back_to_local_data(error, status, code):
    @asynccontextmanager
    async def failing(_settings):
        raise error
        yield  # pragma: no cover

    env = Env(neon_settings(), factory=failing)
    for path in ("/exam-periods", "/staff", f"/exam-periods/{PERIOD}/sessions"):
        reply = env.get(path)
        assert reply.status_code == status and reply.json()["code"] == code
        assert "items" not in reply.json() and "meta" not in reply.json()  # no data of any kind
    assert env.service._snapshot is None


def test_local_demo_is_refused_in_production():
    env = Env(app_env="production")
    reply = env.get("/exam-periods")
    assert reply.status_code == 403 and reply.json()["code"] == "M3_DEMO_LOCAL_FORBIDDEN"


def test_synthetic_institution_must_differ_from_the_real_one():
    env = Env(local_settings(institution_id=AUTH_INSTITUTION))
    reply = env.get("/exam-periods")
    assert reply.status_code == 409 and reply.json()["code"] == "M3_DEMO_INSTITUTION_CONFLICT"


def test_responses_identify_the_source_and_keep_the_synthetic_institution(env):
    for path in ("/exam-periods", f"/exam-periods/{PERIOD}/sessions", "/staff", "/staff/workload",
                 f"/exam-periods/{PERIOD}/requirements"):
        meta = env.get(path).json()["meta"]
        assert meta["data_source"] == "LOCAL_DEMO" and meta["synthetic"] is True and meta["preview_only"] is True
        assert meta["institution_id"] == str(DEMO_INSTITUTION_ID) != str(AUTH_INSTITUTION)
        assert "Local synthetic seed pack" in meta["source_description"]


def test_snapshot_is_cached_and_refreshable_and_only_reads():
    loaded = []
    env = Env(factory=fake_source(record=loaded))
    env.get("/exam-periods")
    env.get("/staff")
    env.get(f"/exam-periods/{PERIOD}/sessions")
    assert len(loaded) == 1  # one read-only snapshot serves all three requests
    assert env.post("/snapshot/refresh").status_code == 200 and len(loaded) == 2
    for source in loaded:
        assert source.calls[0] == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
        assert all(c == "schema_columns" or c.startswith("fetch ") for c in source.calls[1:])


# --- Periods, sessions, requirements ---------------------------------------------------------------

def test_periods_and_sessions(env):
    periods = env.get("/exam-periods").json()["items"]
    assert [p["period_code"] for p in periods] == [PERIOD] and periods[0]["session_count"] == 3
    sessions = env.get(f"/exam-periods/{PERIOD}/sessions").json()
    assert sessions["total"] == 3
    first = sessions["items"][0]
    assert {"session_id", "module_code", "module_name", "date", "start", "end", "duration_minutes", "students", "rooms",
            "publication_status"} <= set(first)
    assert first["duration_minutes"] > 0 and first["students"] == sum(r["students"] for r in first["rooms"])
    assert {"venue_id", "venue_name", "venue_type", "building", "students"} <= set(first["rooms"][0])


def test_unknown_or_malformed_period(env):
    assert env.get("/exam-periods/UNKNOWN-1/sessions").status_code == 404
    assert env.get("/exam-periods/UNKNOWN-1/sessions").json()["code"] == "M3_PERIOD_NOT_FOUND"
    assert env.get("/exam-periods/bad period!/sessions").status_code == 422
    assert env.get("/staff", params={"period_code": "lower"}).status_code == 422


def test_requirements_are_calculated_from_the_policy(env):
    body = env.get(f"/exam-periods/{PERIOD}/requirements").json()
    assert body["complete"] is True and len(body["items"]) == 4
    assert body["total_supervisors"] == 4 and body["total_invigilators"] == 4 and body["stored_example_mismatches"] == []
    assert {"session_id", "venue_id", "module_code", "students", "supervisors", "invigilators"} <= set(body["items"][0])


def test_stored_requirement_that_disagrees_is_reported_not_used():
    env = Env(factory=fake_source(lambda t: t["duty_requirements"][0].update(supervisors_required=3)))
    body = env.get(f"/exam-periods/{PERIOD}/requirements").json()
    assert len(body["stored_example_mismatches"]) == 1 and body["total_supervisors"] == 4


def test_invalid_demo_data_is_a_422_with_problems():
    env = Env(factory=fake_source(lambda t: t["exam_room_allocations"][0].update(venue_id="Z9")))
    reply = env.get("/exam-periods")
    assert reply.status_code == 200  # periods need no mapping
    reply = env.get("/staff")
    assert reply.status_code == 422 and reply.json()["code"] == "M3_DEMO_DATA_INVALID" and reply.json()["problems"]


# --- Staff ---------------------------------------------------------------------------------------------

def test_staff_list_excludes_inactive_staff_and_can_explain_why(env):
    page = env.get("/staff").json()
    assert page["total"] == 7 and page["limit"] == 50 and page["offset"] == 0
    assert "EMP0008" not in {s["staff_number"] for s in page["items"]}
    assert all(s["eligible"] and s["eligible_roles"] for s in page["items"])
    full = env.get("/staff", params={"include_excluded": True}).json()
    excluded = [s for s in full["items"] if not s["eligible"]]
    assert [s["staff_number"] for s in excluded] == ["EMP0008"] and excluded[0]["exclusion_reason"]
    assert excluded[0]["full_name"] is None


def test_staff_paging_role_filter_and_validation(env):
    assert [s["staff_number"] for s in env.get("/staff", params={"limit": 2, "offset": 1}).json()["items"]] == ["EMP0002", "EMP0003"]
    supervisors = env.get("/staff", params={"eligible_role": "SUPERVISOR"}).json()
    assert supervisors["total"] == 4 and all("SUPERVISOR" in s["eligible_roles"] for s in supervisors["items"])
    for params in ({"limit": 0}, {"limit": 501}, {"offset": -1}, {"eligible_role": "BOSS"}):
        assert env.get("/staff", params=params).status_code == 422


def test_workload_statuses_stay_distinct(env):
    body = env.get("/staff/workload").json()
    by_number = {w["staff_number"]: w for w in body["items"]}
    assert by_number["EMP0001"]["status"] == "COMPUTED" and by_number["EMP0001"]["hours_per_week"] == 2.0
    assert by_number["EMP0005"]["status"] == "NOT_COMPUTED" and by_number["EMP0005"]["hours_per_week"] is None
    assert by_number["EMP0006"]["status"] == "MISSING_RESPONSE" and by_number["EMP0006"]["hours_per_week"] is None
    assert body["counts"]["COMPUTED"] == 5 and body["counts"]["NOT_COMPUTED"] == 1 and body["counts"]["MISSING_RESPONSE"] == 1
    only = env.get("/staff/workload", params={"status": "MISSING_RESPONSE"}).json()
    assert [w["staff_number"] for w in only["items"]] == ["EMP0006"]
    assert env.get("/staff/workload", params={"status": "GUESSED"}).status_code == 422


def test_staff_detail_and_constraints(env):
    lecturer = staff_id_of(env, 1)
    detail = env.get(f"/staff/{lecturer}").json()
    assert detail["full_name"] == "Synthetic Staff 1" and detail["workload"]["status"] == "COMPUTED"
    assert detail["constraints"]["restrictions"][0]["module_code"] == "IT1010"
    assert detail["constraints"]["history"] == {"prior_duties": 1, "prior_minutes": 120}
    assert detail["constraints"]["limits"] == {"max_duties_per_day": 2, "max_duties_per_period": 7,
                                               "minimum_gap_minutes": 60, "source": detail["constraints"]["limits"]["source"]}
    assert len(detail["constraints"]["availability"]) == 1
    leave = env.get(f"/staff/{staff_id_of(env, 2)}/constraints").json()
    assert len(leave["unavailability"]) == 1 and leave["unavailability"][0]["reason"] == "SYNTHETIC leave"
    prefs = {p["kind"] for n in (3, 4) for p in env.get(f"/staff/{staff_id_of(env, n)}/constraints").json()["preferences"]}
    assert prefs == {"AVOID", "PREFER"}
    assert env.get(f"/staff/{lecturer}/constraints").json()["meta"]["data_source"] == "LOCAL_DEMO"


def test_excluded_unknown_and_malformed_staff(env):
    inactive = staff_id_of(env, 8)
    detail = env.get(f"/staff/{inactive}").json()
    assert detail["eligible"] is False and detail["constraints"] is None and detail["exclusion_reason"]
    assert env.get(f"/staff/{inactive}/constraints").status_code == 404
    assert env.get(f"/staff/{uuid.uuid4()}").status_code == 404
    assert env.get("/staff/not-a-uuid").status_code == 422


# --- Allocation ----------------------------------------------------------------------------------------

def run(env, kind, **body):
    reply = env.post(f"/allocations/{kind}", json={"time_limit_seconds": 10, **body})
    assert reply.status_code == 201, reply.text
    return reply.json()


@SOLVER
def test_baseline_allocation_is_a_preview_with_readable_assignments(env):
    body = run(env, "baseline")
    assert body["status"] == "COMPLETED" and body["mode"] == "BASELINE" and body["comparison"] is None
    assert (body["saved"], body["approved"], body["published"]) == (False, False, False)
    assert body["meta"]["data_source"] == "LOCAL_DEMO" and body["meta"]["preview_only"] is True
    [roster] = body["rosters"]
    assert roster["label"] == "BASELINE" and roster["independently_valid"] is True and len(roster["assignments"]) == 8
    first = roster["assignments"][0]
    assert {"session_id", "venue_id", "venue_name", "module_code", "date", "start", "end", "staff_id", "staff_number",
            "staff_name", "role"} <= set(first) and first["role"] in ("SUPERVISOR", "INVIGILATOR")
    assert roster["revalidation_violations"] == []


@SOLVER
def test_fair_and_compare_allocations_report_fairness(env):
    fair = run(env, "fair")
    assert fair["status"] == "COMPLETED" and [r["label"] for r in fair["rosters"]] == ["FAIR"]
    assert fair["rosters"][0]["metrics"]["gini"] >= 0 and fair["fairness_complete"] is False
    assert "duty balance" in fair["fairness_scope"] and any("PARTIAL" in w or "partial" in w for w in fair["warnings"])
    both = run(env, "compare")
    assert [r["label"] for r in both["rosters"]] == ["BASELINE", "FAIR"] and both["status"] == "COMPLETED"
    cmp = both["comparison"]
    assert cmp["fairer_by_gini"] in ("FAIR", "TIE") and cmp["std_load_fair"] <= cmp["std_load_baseline"]
    assert all(r["independently_valid"] for r in both["rosters"])


@SOLVER
def test_admin_workload_kinds_are_visible_in_the_fair_run(env):
    body = run(env, "fair")
    assert any("counted as 0 because missing_admin_hours=ZERO" in d for d in body["diagnostics"])
    assert any(staff_id_of(env, 6) in d and "No workload record" in d for d in body["diagnostics"])
    items = {d["name"]: d for d in body["data_availability"]}
    assert items["staff"]["origin"] == "PRELOADED" and items["duty history"]["status"] == "PROVIDED"
    assert items["admin workload"]["records"] == 6


@SOLVER
def test_stored_constraints_are_enforced_through_the_api(env):
    roster = run(env, "baseline")["rosters"][0]["assignments"]
    on = lambda session: {a["staff_id"] for a in roster if a["session_id"] == session}
    assert staff_id_of(env, 2) not in on("X1-IT1010") | on("X2-IT1020")  # leave on day 0
    assert staff_id_of(env, 1) not in on("X1-IT1010")  # lecturer of the module
    assert staff_id_of(env, 8) not in {a["staff_id"] for a in roster}  # inactive
    ranks = {s["staff_id"]: s["academic_rank"] for s in env.get("/staff").json()["items"]}
    assert all(ranks[a["staff_id"]] in ("Professor", "Associate Professor", "Senior Lecturer II")
               for a in roster if a["role"] == "SUPERVISOR")
    per_day: dict = {}
    for a in roster:
        per_day.setdefault((a["staff_id"], a["date"]), []).append(a)
    assert max(len(v) for v in per_day.values()) <= 2  # maximum duties per day


@SOLVER
def test_infeasible_allocation_is_reported_with_diagnostics():
    def short_staffed(t):
        for row in t["staff"][2:4]:
            row["employment_status"] = "INACTIVE"
    env = Env(factory=fake_source(short_staffed))
    body = run(env, "baseline")
    assert body["status"] == "INFEASIBLE" and body["violations"] and body["rosters"][0]["assignments"] == []
    assert body["rosters"][0]["solver_status"] == "INFEASIBLE" and body["rosters"][0]["independently_valid"] is None
    roster = env.get(f"/allocations/{body['run_id']}/roster").json()
    assert roster["status"] == "INFEASIBLE" and roster["roster"]["assignments"] == []


@SOLVER
def test_run_can_be_retrieved_roster_and_fairness(env):
    created = run(env, "compare")
    run_id = created["run_id"]
    assert env.get(f"/allocations/{run_id}").json()["run_id"] == run_id
    fair = env.get(f"/allocations/{run_id}/roster").json()  # default: FAIR
    assert fair["roster"]["label"] == "FAIR" and len(fair["roster"]["assignments"]) == 8
    base = env.get(f"/allocations/{run_id}/roster", params={"roster": "BASELINE"}).json()
    assert base["roster"]["label"] == "BASELINE"
    metrics = env.get(f"/allocations/{run_id}/fairness").json()
    assert metrics["baseline"]["gini"] == created["comparison"]["gini_baseline"] and metrics["comparison"] is not None
    assert {"staff_id", "staff_number", "duties", "total_load"} <= set(metrics["fair"]["per_staff"][0])
    assert env.get(f"/allocations/{run_id}/roster", params={"roster": "BOTH"}).status_code == 422


@SOLVER
def test_missing_or_malformed_run_ids(env):
    assert env.get(f"/allocations/{'0' * 32}").status_code == 404
    assert env.get(f"/allocations/{'0' * 32}/fairness").json()["code"] == "M3_PREVIEW_NOT_FOUND"
    assert env.get("/allocations/not-a-run").status_code == 422
    baseline = run(env, "baseline")["run_id"]
    assert env.get(f"/allocations/{baseline}/roster", params={"roster": "FAIR"}).status_code == 409
    assert env.get(f"/seating/{baseline}").status_code == 404  # a duty run is not a seating plan


@pytest.mark.parametrize("body", [{"time_limit_seconds": 0}, {"time_limit_seconds": 601}, {"time_limit_seconds": "x"},
                                  {"period_code": "bad code"}, {"approve": True}, {"save": True}])
def test_allocation_request_validation(env, body):
    assert env.post("/allocations/baseline", json=body).status_code == 422
    assert env.service._snapshot is None  # rejected before any data was read


def test_unknown_period_for_allocation_and_missing_solver(env, monkeypatch):
    assert env.post("/allocations/fair", json={"period_code": "NOPE-1"}).status_code == 404

    def no_solver(*_a, **_k):
        raise RuntimeError("Offline solver is not installed. Install this service's [solver] extra.")
    monkeypatch.setattr("app.duty_allocation.demo_service.run_demo_duty_allocation", no_solver)
    reply = env.post("/allocations/baseline")
    assert reply.status_code == 503 and reply.json()["code"] == "M3_SOLVER_UNAVAILABLE"


@SOLVER
def test_allocation_never_writes(env):
    loaded = []
    env = Env(factory=fake_source(record=loaded))
    run(env, "compare")
    env.post("/seating")
    assert len(loaded) == 1 and all(c == "schema_columns" or c.startswith("fetch ") or c.startswith("SET TRANSACTION")
                                    for c in loaded[0].calls)


# --- Validation ---------------------------------------------------------------------------------------

@SOLVER
def test_validation_of_the_run_roster_and_of_submitted_rosters(env):
    created = run(env, "fair")
    run_id, assignments = created["run_id"], created["rosters"][0]["assignments"]
    ok = env.post(f"/allocations/{run_id}/validation").json()
    assert ok["valid"] is True and ok["source"] == "RUN_ROSTER" and ok["assignments_checked"] == 8 and ok["violations"] == []
    ref = lambda a: {k: a[k] for k in ("session_id", "venue_id", "staff_id", "role")}
    short = env.post(f"/allocations/{run_id}/validation", json={"assignments": [ref(a) for a in assignments[1:]]}).json()
    assert short["valid"] is False and short["source"] == "SUBMITTED_ROSTER" and short["violations"]
    clash = [ref(a) for a in assignments] + [ref(assignments[0]) | {"session_id": assignments[1]["session_id"],
                                                                    "venue_id": assignments[1]["venue_id"]}]
    assert env.post(f"/allocations/{run_id}/validation", json={"assignments": clash}).json()["valid"] is False
    leave = [ref(a) for a in assignments if a["role"] == "INVIGILATOR"][:1]
    leave[0]["staff_id"] = staff_id_of(env, 8)  # an inactive person is not a valid duty holder
    assert env.post(f"/allocations/{run_id}/validation", json={"assignments": leave}).json()["valid"] is False


@SOLVER
def test_validation_request_errors(env):
    run_id = run(env, "fair")["run_id"]
    for body in ({"roster": "MIDDLE"}, {"assignments": [{"session_id": "X1-IT1010"}]},
                 {"assignments": [{"session_id": "S", "venue_id": "H1", "staff_id": "nope", "role": "SUPERVISOR"}]},
                 {"assignments": [{"session_id": "S", "venue_id": "H1", "staff_id": str(uuid.uuid4()), "role": "BOSS"}]},
                 {"unknown": 1}):
        assert env.post(f"/allocations/{run_id}/validation", json=body).status_code == 422
    assert env.post(f"/allocations/{run_id}/validation", json={"roster": "BASELINE"}).status_code == 409


# --- Swap and replacement previews ------------------------------------------------------------------

def ref(a):
    return {k: a[k] for k in ("session_id", "venue_id", "staff_id", "role")}


@SOLVER
def test_swap_preview_is_validated_and_never_applied(env):
    created = run(env, "fair")
    run_id, assignments = created["run_id"], created["rosters"][0]["assignments"]
    before = env.get(f"/allocations/{run_id}/roster").json()
    invigilators = [a for a in assignments if a["role"] == "INVIGILATOR"]
    previews = [env.post(f"/allocations/{run_id}/swap-preview", json={"first": ref(x), "second": ref(y)}).json()
                for x in invigilators for y in invigilators if x["staff_id"] < y["staff_id"]]
    assert previews and all(p["kind"] == "SWAP" and p["applied"] is False for p in previews)
    proposed = [p for p in previews if p["status"] == "PROPOSED"]
    assert {p["status"] for p in previews} <= {"PROPOSED", "INFEASIBLE"}
    for p in previews:
        assert p["meta"]["preview_only"] is True and p["reasons"]
        if p["status"] == "INFEASIBLE":
            assert p["violations"] and p["proposed_roster"] is None
    assert proposed, "at least one invigilator swap should be possible in the compact fixture"
    p = proposed[0]
    assert len(p["removed"]) == len(p["added"]) == 2 and p["independent_check_violations"] == []
    assert p["original_metrics"] and p["proposed_metrics"] and len(p["proposed_roster"]) == 8
    assert {a["staff_id"] for a in p["removed"]} == {a["staff_id"] for a in p["added"]}
    assert env.get(f"/allocations/{run_id}/roster").json() == before  # the stored draft did not change


@SOLVER
def test_swap_that_breaks_rules_is_infeasible_with_reasons(env):
    created = run(env, "fair")
    run_id, assignments = created["run_id"], created["rosters"][0]["assignments"]
    supervisor = next(a for a in assignments if a["role"] == "SUPERVISOR")
    invigilator = next(a for a in assignments if a["role"] == "INVIGILATOR"
                       and a["staff_id"] != supervisor["staff_id"])
    lecturer = next(s["staff_id"] for s in env.get("/staff").json()["items"] if s["academic_rank"] == "Lecturer")
    low = next(a for a in assignments if a["role"] == "INVIGILATOR" and a["staff_id"] == lecturer) \
        if any(a["staff_id"] == lecturer for a in assignments) else invigilator
    reply = env.post(f"/allocations/{run_id}/swap-preview", json={"first": ref(supervisor), "second": ref(low)}).json()
    assert reply["applied"] is False and reply["status"] in ("PROPOSED", "INFEASIBLE")
    if low["staff_id"] == lecturer:  # a Lecturer cannot supervise
        assert reply["status"] == "INFEASIBLE" and any(v["constraint"] == "D6" for v in reply["violations"])


@SOLVER
def test_swap_request_errors(env):
    created = run(env, "fair")
    run_id, a = created["run_id"], created["rosters"][0]["assignments"][0]
    same = env.post(f"/allocations/{run_id}/swap-preview", json={"first": ref(a), "second": ref(a)}).json()
    assert same["status"] == "INVALID_REQUEST" and same["reasons"]
    stranger = ref(a) | {"staff_id": str(uuid.uuid4())}
    assert env.post(f"/allocations/{run_id}/swap-preview", json={"first": ref(a), "second": stranger}).json()["status"] == "INVALID_REQUEST"
    assert env.post(f"/allocations/{run_id}/swap-preview", json={"first": ref(a)}).status_code == 422
    assert env.post(f"/allocations/{run_id}/swap-preview", json={"first": ref(a), "second": ref(a), "apply": True}).status_code == 422
    assert env.post(f"/allocations/{run_id}/swap-preview", json={"roster": "BASELINE", "first": ref(a), "second": ref(a)}).status_code == 409


@SOLVER
def test_replacement_preview_ranks_valid_candidates_and_explains_rejections(env):
    created = run(env, "fair")
    run_id, assignments = created["run_id"], created["rosters"][0]["assignments"]
    duty = next(a for a in assignments if a["role"] == "INVIGILATOR")
    reply = env.post(f"/allocations/{run_id}/replacement-preview",
                     json={"duty": ref(duty), "reason": "Sick leave"}).json()
    assert reply["kind"] == "REPLACEMENT" and reply["applied"] is False and reply["status"] == "PROPOSED"
    assert reply["candidates"] and reply["rejected"] and all(r["reasons"] for r in reply["rejected"])
    best = reply["candidates"][0]
    assert best["staff_id"] != duty["staff_id"] and best["metrics"] and best["duties_after"] >= 1
    assert reply["independent_check_violations"] == [] and len(reply["removed"]) == len(reply["added"]) == 1
    assert reply["added"][0]["staff_id"] != duty["staff_id"] and reply["added"][0]["session_id"] == duty["session_id"]
    assert duty["staff_id"] not in {a["staff_id"] for a in reply["proposed_roster"]
                                    if a["session_id"] == duty["session_id"] and a["venue_id"] == duty["venue_id"]
                                    and a["role"] == duty["role"]}
    chosen = best["staff_id"]
    preferred = env.post(f"/allocations/{run_id}/replacement-preview",
                         json={"duty": ref(duty), "reason": "Sick leave", "preferred_staff_id": chosen}).json()
    assert preferred["added"][0]["staff_id"] == chosen


@SOLVER
def test_replacement_request_errors(env):
    created = run(env, "fair")
    run_id, duty = created["run_id"], ref(created["rosters"][0]["assignments"][0])
    base = {"duty": duty, "reason": "Illness"}
    for body in ({**base, "reason": ""}, {**base, "reason": "x" * 501}, {"duty": duty},
                 {**base, "unavailable_from": "2026-11-16T08:00:00"},
                 {**base, "unavailable_from": "2026-11-16T10:00:00", "unavailable_until": "2026-11-16T09:00:00"},
                 {**base, "preferred_staff_id": "nope"}, {**base, "commit": True}):
        assert env.post(f"/allocations/{run_id}/replacement-preview", json=body).status_code == 422
    missing = {"duty": duty | {"staff_id": str(uuid.uuid4())}, "reason": "Illness"}
    assert env.post(f"/allocations/{run_id}/replacement-preview", json=missing).json()["status"] == "INVALID_REQUEST"
    window = {**base, "unavailable_from": "2026-11-16T07:00:00", "unavailable_until": "2026-11-16T19:00:00"}
    assert env.post(f"/allocations/{run_id}/replacement-preview", json=window).status_code == 200


# --- Student seating --------------------------------------------------------------------------------

def test_seating_preview_is_provisional_with_room_capacities(env):
    created = env.post("/seating")
    assert created.status_code == 201
    body = created.json()
    assert body["status"] == "COMPLETE" and body["assignment_count"] == 11 and body["all_seats_verified"] is False
    assert body["seat_state"] == "SYNTHETIC" and set(body["synthetic_halls"]) == {"H1", "L1"}
    assert (body["saved"], body["approved"], body["published"]) == (False, False, False)
    percentages = {r["venue_type"]: r["capacity_percentage"] for r in body["rooms"]}
    assert percentages == {"lecture_hall": 50, "computer_lab": 90}
    assert all(r["seat_limit"] is not None and r["students"] <= r["seat_limit"] for r in body["rooms"])
    assert body["meta"]["data_source"] == "LOCAL_DEMO" and body["example_problems"] == []
    again = env.get(f"/seating/{body['seating_id']}").json()
    assert again["assignment_count"] == 11 and again["seat_state"] == "SYNTHETIC"


def test_room_wise_seat_allocation(env):
    body = env.post("/seating").json()
    seen = []
    for room in body["rooms"]:
        seats = env.get(f"/seating/{body['seating_id']}/rooms/{room['session_id']}/{room['venue_id']}").json()
        assert seats["students"] == room["students"] == len(seats["seats"]) and seats["seat_state"] == "SYNTHETIC"
        numbers = [s["seat_number"] for s in seats["seats"]]
        assert numbers == sorted(set(numbers)) and all(not s["seat_verified"] for s in seats["seats"])
        assert [s["student_id"] for s in seats["seats"]] == sorted(s["student_id"] for s in seats["seats"])  # IT-number order
        seen += [(room["session_id"], room["venue_id"], s["seat_label"]) for s in seats["seats"]]
    assert len(seen) == len(set(seen)) == 11
    assert env.get(f"/seating/{body['seating_id']}/rooms/X1-IT1010/NOPE").status_code == 404
    assert env.get(f"/seating/{'1' * 32}/rooms/X1-IT1010/H1").status_code == 404


def test_seating_validation_and_capacity_contradictions():
    env = Env(factory=fake_source(lambda t: t["capacity_policies"][0].update(exam_percentage=75, effective_exam_capacity=6)))
    reply = env.post("/seating")
    assert reply.status_code == 422 and reply.json()["code"] == "M3_DEMO_DATA_INVALID"
    assert any("percentage" in p or "capacity" in p for p in reply.json()["problems"])
    assert env.post("/seating", json={"period_code": "bad code"}).status_code == 422
    assert env.post("/seating", json={"verified": True}).status_code == 422
    assert env.post("/seating", json={"period_code": "NOPE-1"}).status_code == 404


def test_timetable_overlaps_are_rejected_before_any_seating():
    def clash(t):  # the second exam starts in the same room while the first is running
        t["exam_sessions"][1]["start_at"] = t["exam_sessions"][0]["start_at"]
        t["exam_sessions"][1]["end_at"] = t["exam_sessions"][0]["end_at"]
        t["exam_room_allocations"][1]["venue_id"] = t["exam_room_allocations"][0]["venue_id"]
    reply = Env(factory=fake_source(clash)).post("/seating")
    assert reply.status_code == 422 and reply.json()["problems"]


# --- Documentation ------------------------------------------------------------------------------------

def test_openapi_documents_every_endpoint(env):
    spec = env.client.get("/openapi.json").json()
    paths = {p for p in spec["paths"] if p.startswith(API)}
    assert len(paths) >= 18
    operations = [(m, p) for p in paths for m in spec["paths"][p]]
    assert len(operations) == len(GETS) + len(POSTS)  # every route listed above is documented, once
    assert all("summary" in spec["paths"][p][m] and spec["paths"][p][m]["tags"] for m, p in operations)
    assert all(spec["paths"][API + "/allocations/" + k]["post"]["responses"].get("201") for k in ("baseline", "fair", "compare"))
    assert "BearerToken" in spec["components"]["securitySchemes"]


# --- The real seed pack (offline; skipped when absent) ----------------------------------------------

@SOLVER
@pytest.mark.skipif(not (SEED_PACK / "manifest.json").is_file(), reason="Member 3 seed pack not present")
def test_local_demo_source_serves_the_full_seed_pack():
    env = Env(Member3DemoSettings(source=DemoSource.LOCAL_DEMO, seed_dir=SEED_PACK, max_solver_seconds=20))
    env.service._row_source = default_row_source  # the real CSV reader
    assert env.get("/status").json()["enabled"] is True
    assert env.get(f"/exam-periods/{PERIOD}/sessions").json()["total"] == 24
    staff = env.get("/staff", params={"include_excluded": True}).json()
    assert staff["total"] == 48 and sum(not s["eligible"] for s in staff["items"]) == 2
    counts = env.get("/staff/workload").json()["counts"]
    assert {"COMPUTED", "NOT_COMPUTED", "MISSING_RESPONSE"} <= set(counts)
    both = run(env, "compare")
    assert both["status"] == "COMPLETED" and len(both["rosters"][1]["assignments"]) == 64
    assert both["comparison"]["gini_fair"] <= both["comparison"]["gini_baseline"]
    seating = env.post("/seating").json()
    assert seating["status"] == "COMPLETE" and seating["assignment_count"] == 1200 and seating["all_seats_verified"] is False


def test_local_demo_source_reports_a_missing_seed_pack(tmp_path):
    env = Env(Member3DemoSettings(source=DemoSource.LOCAL_DEMO, seed_dir=tmp_path / "nope"))
    env.service._row_source = default_row_source
    reply = env.get("/exam-periods")
    assert reply.status_code == 503 and reply.json()["code"] == "M3_DEMO_DATABASE_UNAVAILABLE"
    assert "nope" not in reply.text  # no local paths in errors


# --- Official component name, PREVIEW ONLY marking and the six frontend screens ---------------------

COMPONENT = "Fairness-Aware Staff Allocation for Examination Duties"


def test_openapi_uses_the_official_component_name_and_paths_are_unchanged(env):
    spec = env.client.get("/openapi.json").json()
    tags = {t for p, v in spec["paths"].items() if p.startswith(API) for op in v.values() for t in op["tags"]}
    assert tags == {f"{COMPONENT} (PREVIEW ONLY)"}
    assert API + "/allocations/compare" in spec["paths"] and API + "/seating" in spec["paths"]


@SOLVER
def test_every_response_is_marked_preview_only_and_names_the_component(env):
    run_id = run(env, "compare")["run_id"]
    seating_id = env.post("/seating").json()["seating_id"]
    duty = env.get(f"/allocations/{run_id}/roster").json()["roster"]["assignments"][0]
    responses = [
        env.get("/status"), env.get("/exam-periods"), env.get(f"/exam-periods/{PERIOD}/sessions"),
        env.get(f"/exam-periods/{PERIOD}/requirements"), env.get("/staff"), env.get("/staff/workload"),
        env.get(f"/staff/{staff_id_of(env, 1)}"), env.get(f"/staff/{staff_id_of(env, 1)}/constraints"),
        env.get(f"/allocations/{run_id}"), env.get(f"/allocations/{run_id}/roster"), env.get(f"/allocations/{run_id}/fairness"),
        env.post(f"/allocations/{run_id}/validation"),
        env.post(f"/allocations/{run_id}/replacement-preview", json={"duty": ref(duty), "reason": "Illness"}),
        env.post(f"/allocations/{run_id}/swap-preview", json={"first": ref(duty), "second": ref(duty)}),
        env.get(f"/seating/{seating_id}"),
    ]
    for reply in responses:
        assert reply.status_code == 200, reply.text
        body = reply.json()
        meta = body.get("meta", body)  # /status carries the notice at top level
        assert meta["component"] == COMPONENT and meta["notice"].startswith("PREVIEW ONLY")
        assert meta.get("preview_only", True) is True
        for flag in ("saved", "approved", "published", "applied"):
            assert body.get(flag, False) is False
    created = [run(env, "baseline"), env.post("/seating").json()]
    assert all(c["saved"] is False and c["approved"] is False and c["published"] is False for c in created)
    assert all(c["meta"]["notice"].startswith("PREVIEW ONLY") for c in created)


@SOLVER
def test_the_six_frontend_screens_have_the_data_they_need(env):
    # 1. Dashboard
    status = env.get("/status").json()
    periods = env.get("/exam-periods").json()["items"]
    sessions = env.get(f"/exam-periods/{PERIOD}/sessions").json()
    needs = env.get(f"/exam-periods/{PERIOD}/requirements").json()
    assert status["enabled"] and periods[0]["session_count"] == sessions["total"] == 3 and needs["complete"]
    # 2. Staff information and availability
    staff = env.get("/staff").json()["items"][0]
    detail = env.get(f"/staff/{staff['staff_id']}").json()
    assert {"staff_number", "full_name", "academic_rank", "eligible_roles"} <= set(detail)
    assert {"unavailability", "availability", "preferences", "restrictions", "history", "limits"} <= set(detail["constraints"])
    assert detail["workload"]["status"] in ("COMPUTED", "NOT_COMPUTED", "MISSING_RESPONSE", "NOT_LOADED")
    # 3. Generate duty roster
    generated = run(env, "compare")
    roster = generated["rosters"][1]
    assert roster["label"] == "FAIR" and roster["independently_valid"] is True
    assert {"date", "start", "end", "venue_name", "staff_name", "role"} <= set(roster["assignments"][0])
    # 4. Fairness analytics
    fairness = env.get(f"/allocations/{generated['run_id']}/fairness").json()
    assert fairness["comparison"]["fairer_by_gini"] in ("FAIR", "TIE") and fairness["fair"]["per_staff"]
    assert fairness["fairness_scope"] and "fairness_complete" in fairness
    # 5. Swap and replacement management
    duty = next(a for a in roster["assignments"] if a["role"] == "INVIGILATOR")
    change = env.post(f"/allocations/{generated['run_id']}/replacement-preview",
                      json={"duty": ref(duty), "reason": "Illness"}).json()
    assert change["status"] == "PROPOSED" and change["candidates"] and change["applied"] is False
    check = env.post(f"/allocations/{generated['run_id']}/validation").json()
    assert check["valid"] is True
    # 6. Student seating allocation
    seating = env.post("/seating").json()
    room = seating["rooms"][0]
    seats = env.get(f"/seating/{seating['seating_id']}/rooms/{room['session_id']}/{room['venue_id']}").json()
    assert seating["seat_state"] == "SYNTHETIC" and seats["seats"] and "seat_label" in seats["seats"][0]
