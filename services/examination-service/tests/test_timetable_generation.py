"""Timetable generation API: permissions, inputs, saved settings, solver outcomes and validation.

Real RS256 tokens and a fake Identity Service; inputs come from the synthetic
dataset and in-memory stores through the same loader the database path uses
(load_snapshot). The solver is the real CP-SAT scheduler unless a test replaces it.
"""

from __future__ import annotations

import asyncio
import dataclasses
import datetime as dt
import threading
from collections import Counter

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("ortools")

import app.generation.service as generation  # noqa: E402
from app.api.deps import get_timetable_generator  # noqa: E402
from app.generation.service import (  # noqa: E402
    MAX_API_TIME_LIMIT_SECONDS,
    GenerationBusyError,
    GenerationRequest,
    TimetableGenerator,
)
from app.main import create_app  # noqa: E402
from app.scheduling.configuration import SchedulingConfiguration  # noqa: E402
from app.scheduling.inputs import load_snapshot  # noqa: E402
from app.scheduling.types import SolveResult, SolveStatus  # noqa: E402
from app.scheduling_settings.errors import SchedulingSettingsNotConfiguredError  # noqa: E402
from app.scheduling_settings.memory import MemorySchedulingSettingsStore  # noqa: E402
from app.scheduling_settings.service import SchedulingSettingsService  # noqa: E402
from app.seating.memory import MemorySeatingStore  # noqa: E402
from app.seed.generator import generate_dataset  # noqa: E402
from app.sources import DatasetSource  # noqa: E402
from auth_support import (  # noqa: E402
    EXAMINATION_PERMISSION_SETS,
    INSTITUTION,
    OTHER_INSTITUTION,
    ROLE_PERMISSIONS,
    FakeIdentityService,
    make_auth_context,
    own_dataset,
)
from schedence_auth.testing import TokenFactory  # noqa: E402

URL = "/api/v1/timetables/generate"
PERIOD = "PP1-2026-S2-FINAL"
DATASET = generate_dataset()
SOURCE = DatasetSource(DATASET)
SHARED = ["IT1010", "IT1030"]  # 105 students sit both
ADMIN_USER = __import__("uuid").uuid4()


class Harness:
    """In-memory replacements for the two database loaders, with call records."""

    def __init__(self, source=SOURCE, seating_percentage=None):
        self.source = source
        self.seating_percentage = seating_percentage
        self.settings = MemorySchedulingSettingsStore()
        self.loads = 0
        self.loader_threads: list[int] = []

    async def save(self, **changes) -> None:
        document = SchedulingConfiguration().to_document()
        for path, value in changes.items():
            section, key = path.split("__")
            document[section][key] = value
        view = await SchedulingSettingsService(self.settings, INSTITUTION).view(history_limit=0)
        await SchedulingSettingsService(self.settings, INSTITUTION).update(
            document, reason="Generation test settings", expected_version=view.version, user_id=ADMIN_USER,
            dry_run=False)

    def generator(self, lock) -> TimetableGenerator:
        async def load_settings():
            return await SchedulingSettingsService(self.settings, INSTITUTION).active()

        async def load(period_code, policy, module_codes, calendar=None, seating_preferences=None):
            self.loads += 1
            self.loader_threads.append(threading.get_ident())
            store = MemorySeatingStore() if self.seating_percentage is None else MemorySeatingStore(
                default_percentage=self.seating_percentage)
            return await load_snapshot(self.source, store, period_code=period_code, policy=policy,
                                       module_codes=module_codes, from_cycle=True, calendar=calendar, seating_preferences=seating_preferences)

        return TimetableGenerator(load, load_settings, lock)


@pytest.fixture
def tokens() -> TokenFactory:
    return TokenFactory()


@pytest.fixture
def identity() -> FakeIdentityService:
    return FakeIdentityService()


@pytest.fixture
def harness() -> Harness:
    return Harness()


def make_client(tokens, identity, harness) -> TestClient:
    app = own_dataset(create_app(make_auth_context(tokens, identity)))
    app.dependency_overrides[get_timetable_generator] = lambda: harness.generator(app.state.generation_lock)
    return TestClient(app)


@pytest.fixture
def client(tokens, identity, harness) -> TestClient:
    return make_client(tokens, identity, harness)


def headers(tokens, identity, *roles, institution=INSTITUTION, permissions=None) -> dict[str, str]:
    perms = permissions if permissions is not None else sorted({p for r in roles for p in ROLE_PERMISSIONS[r]})
    sub = identity.register({institution: (list(roles), perms)})
    return {"Authorization": f"Bearer {tokens.access_token(sub)}"}


@pytest.fixture
def admin(tokens, identity) -> dict[str, str]:
    return headers(tokens, identity, "EXAMINATION_ADMIN")


def body(modules=SHARED, mode="feasibility", **extra) -> dict:
    return {"period_code": PERIOD, "module_codes": list(modules), "mode": mode, "time_limit_seconds": 10, **extra}


# --- Permissions ------------------------------------------------------------------------------------------------


def test_no_or_invalid_token_is_401_and_reads_nothing(client, harness) -> None:
    for auth in ({}, {"Authorization": "Bearer not-a-jwt"}):
        assert client.post(URL, headers=auth, json=body()).status_code == 401
    assert harness.loads == 0


@pytest.mark.parametrize("role", sorted(ROLE_PERMISSIONS))
def test_only_examination_admins_may_generate(client, tokens, identity, harness, role) -> None:
    response = client.post(URL, headers=headers(tokens, identity, role), json=body())
    assert response.status_code == (200 if role == "EXAMINATION_ADMIN" else 403), response.text
    assert harness.loads == (1 if role == "EXAMINATION_ADMIN" else 0)


@pytest.mark.parametrize("permissions", EXAMINATION_PERMISSION_SETS)
def test_generation_needs_examinations_generate(client, tokens, identity, harness, permissions) -> None:
    auth = headers(tokens, identity, "CUSTOM", permissions=permissions)
    response = client.post(URL, headers=auth, json=body(time_limit_seconds=5))
    assert response.status_code == (200 if "examinations:generate" in permissions else 403)


def test_permission_in_another_institution_does_not_count(client, tokens, identity, harness) -> None:
    auth = headers(tokens, identity, "EXAMINATION_ADMIN", institution=OTHER_INSTITUTION)
    assert client.post(URL, headers=auth, json=body()).status_code == 403
    assert harness.loads == 0


# --- Request validation and input limits ------------------------------------------------------------------------


@pytest.mark.parametrize("payload", [
    {"module_codes": SHARED},  # no period
    {"period_code": "bad period", "module_codes": SHARED},
    {"period_code": PERIOD, "module_codes": []},
    {"period_code": PERIOD, "module_codes": ["IT1010", "IT1010"]},
    {"period_code": PERIOD, "module_codes": ["not-a-code"]},
    {"period_code": PERIOD, "module_codes": [f"AB{i:04d}" for i in range(201)]},
    {"period_code": PERIOD, "module_codes": SHARED, "mode": "fastest"},
    {"period_code": PERIOD, "module_codes": SHARED, "time_limit_seconds": 0},
    {"period_code": PERIOD, "module_codes": SHARED, "time_limit_seconds": MAX_API_TIME_LIMIT_SECONDS + 1},
    {"period_code": PERIOD, "module_codes": SHARED, "time_limit_seconds": "10"},
    {"period_code": PERIOD, "module_codes": SHARED, "extra": True},
])
def test_invalid_requests_are_422_before_anything_is_read(client, admin, harness, payload) -> None:
    assert client.post(URL, headers=admin, json=payload).status_code == 422
    assert harness.loads == 0


@pytest.mark.parametrize("payload,expected", [
    (body(modules=["IT1010", "ZZ9999"]), "unknown or duplicate codes"),
    ({**body(), "period_code": "PP9-2099-S1-FINAL"}, "Unknown exam period"),
])
def test_unknown_period_or_modules_are_422_with_reasons(client, admin, payload, expected) -> None:
    response = client.post(URL, headers=admin, json=payload)
    assert response.status_code == 422 and expected in response.text
    assert all(set(item) == {"rule", "message"} for item in response.json()["detail"])


def test_modules_from_another_semester_are_refused(tokens, identity, admin) -> None:
    modules = tuple(dataclasses.replace(m, semester=1) if m.module_code == "IT1030" else m for m in DATASET.modules)
    client = make_client(tokens, identity, Harness(DatasetSource(dataclasses.replace(DATASET, modules=modules))))
    response = client.post(URL, headers=admin, json=body())
    assert response.status_code == 422
    assert "IT1030 is a semester 1 module" in response.text


def test_time_limit_may_only_lower_the_saved_limit(client, admin, harness) -> None:
    asyncio.run(harness.save(solver__time_limit_seconds=20))
    response = client.post(URL, headers=admin, json=body(time_limit_seconds=30))
    assert response.status_code == 422 and "only lower the saved limit" in response.text


def test_saved_limits_above_the_api_limit_need_a_lower_request(client, admin, harness) -> None:
    asyncio.run(harness.save(solver__time_limit_seconds=MAX_API_TIME_LIMIT_SECONDS + 100))
    payload = body()
    del payload["time_limit_seconds"]
    response = client.post(URL, headers=admin, json=payload)
    assert response.status_code == 422 and f"limited to {MAX_API_TIME_LIMIT_SECONDS} seconds" in response.text
    assert client.post(URL, headers=admin, json=body(time_limit_seconds=10)).status_code == 200


# --- Generation with the saved settings --------------------------------------------------------------------------


def test_feasibility_generation_returns_a_validated_timetable(client, admin, harness) -> None:
    asyncio.run(harness.save(scheduling__permitted_start_times=["09:00", "14:00"],
                             scheduling__minimum_break_minutes=120))
    response = client.post(URL, headers=admin, json=body())
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] in ("OPTIMAL", "FEASIBLE") and data["generated"] and data["persisted"] is False
    assert data["validation"] == {"passed": True, "rules_checked": [f"H{i}" for i in range(1, 12)],
                                  "violations": []}
    assert data["inputs"]["settings_version"] == 1 and data["inputs"]["modules"] == 2
    assert data["inputs"]["exam_kind"] == "FINAL"
    assert {s["module_code"] for s in data["sessions"]} == set(SHARED)
    registrations = sum(1 for r in DATASET.registrations if r.module_code in SHARED)
    assert data["summary"]["seat_allocations"] == data["inputs"]["registrations"] == registrations
    for session in data["sessions"]:
        assert session["start_time"][:5] in ("09:00", "14:00")  # the saved start times were used
        assert session["candidates"] == sum(room["seats"] for room in session["rooms"])
        assert all(room["seats"] <= room["exam_capacity"] for room in session["rooms"])
        assert dt.date.fromisoformat(session["date"]).isoformat() in data["inputs"]["exam_days"]
    first, second = sorted(data["sessions"], key=lambda s: s["start"])
    gap = dt.datetime.fromisoformat(second["start"]) - dt.datetime.fromisoformat(first["end"])
    assert gap >= dt.timedelta(minutes=120)  # the saved minimum break (shared students)
    assert data["workload"]["penalties"][0]["constraint"] == "S1" and data["optimization"] is None
    assert data["solver"]["time_limit_seconds"] == 10 and data["solver"]["mode"] == "feasibility"
    assert "student_id" not in response.text  # summaries only, no per-student data


def test_optimized_generation_uses_saved_weights_and_fairness(client, admin, harness) -> None:
    asyncio.run(harness.save(optimization__s3_weight=50, fairness__enabled=True))
    response = client.post(URL, headers=admin, json=body(mode="optimized"))
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["generated"] and data["solver"]["mode"] == "optimized"
    optimization = data["optimization"]
    assert optimization["fairness"]["protection_applied"] is True
    assert optimization["fairness"]["limits_satisfied"] is True
    assert optimization["selected"]["objective_units"] <= optimization["baseline"]["objective_units"]
    assert {p["constraint"]: p["weight"] for p in data["workload"]["penalties"]}["S3"] == 50
    assert data["inputs"]["settings_fingerprint"] != SchedulingConfiguration().fingerprint


def test_defaults_are_used_until_settings_are_saved(client, admin) -> None:
    data = client.post(URL, headers=admin, json=body()).json()
    assert data["inputs"]["settings_version"] == 0
    assert data["inputs"]["settings_fingerprint"] == SchedulingConfiguration().fingerprint


def test_far_too_few_seats_answer_clearly_before_solving(tokens, identity, admin) -> None:
    harness = Harness(seating_percentage=1)  # every room seats almost nobody: more sessions than allowed
    asyncio.run(harness.save(scheduling__max_rooms_per_exam=1))
    response = make_client(tokens, identity, harness).post(URL, headers=admin, json=body())
    assert response.status_code == 422 and "configured maximum is 3 sessions" in response.text
    assert harness.loads == 1


def test_infeasible_inputs_answer_clearly_without_a_timetable(client, admin, harness) -> None:
    # The only permitted start leaves too little time before the end of the exam day.
    asyncio.run(harness.save(scheduling__permitted_start_times=["17:00"]))
    data = client.post(URL, headers=admin, json=body()).json()
    assert (data["status"], data["generated"], data["sessions"]) == ("INFEASIBLE", False, [])
    assert data["validation"]["passed"] is None and data["diagnostics"]


def test_unknown_result_is_not_reported_as_infeasible(client, admin, monkeypatch) -> None:
    monkeypatch.setattr(generation, "generate_timetable", lambda snapshot, settings: SolveResult(
        SolveStatus.UNKNOWN, diagnostics=("Search stopped without a solution or proof of infeasibility.",)))
    data = client.post(URL, headers=admin, json=body()).json()
    assert (data["status"], data["generated"], data["sessions"]) == ("UNKNOWN", False, [])
    assert "proof of infeasibility" in data["diagnostics"][0]


def test_a_timetable_that_fails_independent_validation_is_never_returned(client, admin, monkeypatch) -> None:
    real = generation.generate_timetable

    def tampered(snapshot, settings):
        result = real(snapshot, settings)
        first = result.timetable.sessions[0]
        moved = dataclasses.replace(first, start=first.start.replace(hour=3), end=first.end.replace(hour=5))
        return dataclasses.replace(result, timetable=dataclasses.replace(
            result.timetable, sessions=(moved, *result.timetable.sessions[1:])))

    monkeypatch.setattr(generation, "generate_timetable", tampered)
    data = client.post(URL, headers=admin, json=body()).json()
    assert (data["status"], data["generated"], data["sessions"]) == ("MODEL_INVALID", False, [])
    assert data["validation"]["passed"] is False and data["validation"]["violations"]


def test_solver_runs_off_the_event_loop_thread(client, admin, harness, monkeypatch) -> None:
    real, solver_threads = generation.generate_timetable, []

    def recording(snapshot, settings):
        solver_threads.append(threading.get_ident())
        return real(snapshot, settings)

    monkeypatch.setattr(generation, "generate_timetable", recording)
    assert client.post(URL, headers=admin, json=body()).status_code == 200
    assert solver_threads and solver_threads[0] != harness.loader_threads[0]


# --- Errors and resources ----------------------------------------------------------------------------------------


def test_missing_solver_is_a_safe_503(client, admin, monkeypatch) -> None:
    def missing(snapshot, settings):
        raise RuntimeError("Offline solver is not installed. Install this service's [solver] extra.")

    monkeypatch.setattr(generation, "generate_timetable", missing)
    response = client.post(URL, headers=admin, json=body())
    assert response.status_code == 503 and response.json()["detail"] == "The timetable solver is not installed on this server."


def test_invalid_saved_settings_block_generation(client, admin, harness) -> None:
    asyncio.run(harness.save())
    saved = harness.settings._committed.settings[INSTITUTION]
    broken = dataclasses.replace(saved, format="scheduling-settings-v0")
    harness.settings._committed.settings[INSTITUTION] = harness.settings._work.settings[INSTITUTION] = broken
    response = client.post(URL, headers=admin, json=body())
    assert response.status_code == 503 and "must repair them" in response.json()["detail"]
    assert harness.loads == 0  # nothing scheduled with broken or default settings


def test_missing_settings_tables_are_a_safe_503(tokens, identity, admin) -> None:
    harness = Harness()

    async def missing():
        raise SchedulingSettingsNotConfiguredError()

    app = own_dataset(create_app(make_auth_context(tokens, identity)))
    app.dependency_overrides[get_timetable_generator] = lambda: TimetableGenerator(
        harness.generator(app.state.generation_lock)._load_snapshot, missing, app.state.generation_lock)
    response = TestClient(app).post(URL, headers=admin, json=body())
    assert response.status_code == 503 and harness.loads == 0


def test_a_second_request_while_generating_is_429(tokens, identity, admin, harness) -> None:
    class Busy:
        def locked(self) -> bool:
            return True

    app = own_dataset(create_app(make_auth_context(tokens, identity)))
    app.dependency_overrides[get_timetable_generator] = lambda: harness.generator(Busy())
    response = TestClient(app).post(URL, headers=admin, json=body())
    assert response.status_code == 429 and response.headers["Retry-After"] == "30"
    assert harness.loads == 0


async def test_only_one_generation_runs_at_a_time(harness) -> None:
    lock, started, release = asyncio.Lock(), asyncio.Event(), asyncio.Event()
    generator = harness.generator(lock)
    real_load = generator._load_snapshot

    async def slow_load(*args):
        started.set()
        await release.wait()
        return await real_load(*args)

    generator._load_snapshot = slow_load
    request = GenerationRequest(PERIOD, tuple(SHARED), "feasibility", 5)
    first = asyncio.create_task(generator.generate(request))
    await started.wait()
    with pytest.raises(GenerationBusyError):
        await generator.generate(request)
    release.set()
    assert (await first).timetable is not None
    assert (await generator.generate(request)).timetable is not None  # free again afterwards


def test_existing_routes_and_cors_allow_the_frontend_to_post(client) -> None:
    response = client.options(URL, headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST"})
    assert response.status_code == 200 and "POST" in response.headers["access-control-allow-methods"]


# --- Examination cycle (SYNTHETIC mapping) -------------------------------------------------------------------------


def _cycle_client(tokens, identity, members) -> TestClient:
    from app.seed.records import ExamPeriodModule

    data = dataclasses.replace(DATASET, exam_period_modules=tuple(ExamPeriodModule(PERIOD, code, scope)
                                                                  for code, scope in members))
    return make_client(tokens, identity, Harness(DatasetSource(data)))


def _cycle_body(**extra) -> dict:
    payload = body(**extra)
    del payload["module_codes"]
    return payload


def test_full_cycle_generation_needs_no_module_codes(tokens, identity, admin) -> None:
    client = _cycle_client(tokens, identity, [(code, "all") for code in SHARED])
    response = client.post(URL, headers=admin, json=_cycle_body())
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["generated"] and data["inputs"]["selection"] == "full_cycle"
    assert {s["module_code"] for s in data["sessions"]} == set(SHARED)
    expected = [r for r in DATASET.registrations if r.module_code in SHARED]
    assert data["inputs"]["registrations"] == len(expected)
    assert data["inputs"]["repeat_registrations"] == sum(r.registration_type == "repeat" for r in expected)
    assert data["inputs"]["repeat_only_modules"] == 0
    assert any("configured examination cycle mapping" in n for n in data["notices"])


def test_partial_cycle_generation_uses_listed_modules(tokens, identity, admin) -> None:
    client = _cycle_client(tokens, identity, [(code, "all") for code in SHARED])
    data = client.post(URL, headers=admin, json=body(modules=["IT1010"])).json()
    assert data["inputs"]["selection"] == "partial_cycle"
    assert [s["module_code"] for s in data["sessions"]] == ["IT1010"]


def test_listed_module_outside_the_cycle_is_422(tokens, identity, admin) -> None:
    client = _cycle_client(tokens, identity, [("IT1010", "all")])
    response = client.post(URL, headers=admin, json=body())
    assert response.status_code == 422 and "Modules not in cycle" in response.text and "IT1030" in response.text


def test_full_cycle_without_a_mapping_is_422(client, admin, harness) -> None:
    response = client.post(URL, headers=admin, json=_cycle_body())
    assert response.status_code == 422 and "has no cycle module mapping" in response.text
    assert harness.loads == 1


def test_listed_modules_without_a_mapping_still_work(client, admin) -> None:
    data = client.post(URL, headers=admin, json=body()).json()
    assert data["generated"] and data["inputs"]["selection"] == "listed_modules"
    assert any("no examination cycle mapping" in n for n in data["notices"])


def test_null_module_codes_mean_the_full_cycle(tokens, identity, admin) -> None:
    client = _cycle_client(tokens, identity, [(code, "all") for code in SHARED])
    data = client.post(URL, headers=admin, json=_cycle_body() | {"module_codes": None}).json()
    assert data["inputs"]["selection"] == "full_cycle"


# --- Capacity-based multiple sessions -------------------------------------------------------------------------------


def test_modules_too_large_for_one_sitting_get_numbered_sessions(client, admin, harness) -> None:
    # One room per sitting: the candidates cannot sit in one room, so each module is split.
    # 111 candidates / 30 seats needs 4 sessions: above the default maximum (3), so it is configured.
    asyncio.run(harness.save(scheduling__max_rooms_per_exam=1, scheduling__max_sessions_per_exam=4))
    response = client.post(URL, headers=admin, json=body())
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["generated"] and data["validation"]["passed"]
    by_module: dict[str, list] = {}
    for session in data["sessions"]:
        by_module.setdefault(session["module_code"], []).append(session)
    split = [code for code, sessions in by_module.items() if len(sessions) > 1]
    assert set(by_module) == set(SHARED) and split and data["summary"]["multi_session_modules"] == len(split)
    for code, sessions in by_module.items():
        sessions.sort(key=lambda s: s["start"])
        count = len(sessions)
        assert all(s["session_count"] == count and len(s["rooms"]) == 1 for s in sessions)
        assert [s["session_label"] for s in sessions] == [f"Session {i:02d}" for i in range(1, count + 1)]
        registered = sum(1 for r in DATASET.registrations if r.module_code == code)
        assert sum(s["candidates"] for s in sessions) == registered
        assert all(s["rooms"][0]["seats"] <= s["rooms"][0]["exam_capacity"] for s in sessions)


def test_one_sitting_is_kept_when_capacity_is_sufficient(client, admin) -> None:
    data = client.post(URL, headers=admin, json=body()).json()
    assert data["summary"]["multi_session_modules"] == 0
    assert all((s["session_number"], s["session_count"], s["session_label"]) == (1, 1, "Session 01")
               for s in data["sessions"])


def test_sessions_above_the_configured_maximum_are_422(client, admin, harness) -> None:
    asyncio.run(harness.save(scheduling__max_rooms_per_exam=1))
    response = client.post(URL, headers=admin, json=body())
    assert response.status_code == 422 and "needs 4 sessions" in response.text and "maximum is 3" in response.text


# --- Session details: scope, year, semester, delivery, normal/repeat candidates ------------------------------------


def _registration_types(data, code) -> Counter:
    return Counter(r.registration_type for r in data.registrations if r.module_code == code)


def _check_session_details(data, sessions) -> None:
    modules = {m.module_code: m for m in data.modules}
    for s in sessions:
        m = modules[s["module_code"]]
        assert (s["academic_year"], s["semester"], s["delivery_mode"]) == (m.academic_year, m.semester, m.delivery_mode)
        assert s["normal_candidates"] + s["repeat_candidates"] == s["candidates"]


def test_regular_sessions_show_module_details_and_candidate_types(tokens, identity, admin) -> None:
    client = _cycle_client(tokens, identity, [(code, "all") for code in SHARED])
    data = client.post(URL, headers=admin, json=_cycle_body()).json()
    _check_session_details(DATASET, data["sessions"])
    for s in data["sessions"]:
        types = _registration_types(DATASET, s["module_code"])
        assert s["candidate_scope"] == "all" and s["delivery_mode"] == "regular"
        assert (s["normal_candidates"], s["repeat_candidates"]) == (types["normal"], types["repeat"])


def test_listed_modules_without_a_mapping_show_scope_all(client, admin) -> None:
    data = client.post(URL, headers=admin, json=body()).json()
    assert {s["candidate_scope"] for s in data["sessions"]} == {"all"}
    _check_session_details(DATASET, data["sessions"])


def test_repeat_only_and_block_mode_sessions_are_marked(tokens, identity, admin) -> None:
    from app.seed.cycles import build_synthetic_cycle
    from app.seed.records import ExamPeriodModule

    synthetic = build_synthetic_cycle(DATASET)
    [repeat_only] = [c.module_code for c in synthetic.exam_period_modules if c.candidate_scope == "repeat_only"][:1]
    # SYNTHETIC Block Mode label on IT1030, for this test only.
    modules = tuple(dataclasses.replace(m, delivery_mode="block", block_category="full_module")
                    if m.module_code == "IT1030" else m for m in synthetic.modules)
    members = (ExamPeriodModule(PERIOD, "IT1010"), ExamPeriodModule(PERIOD, "IT1030"),
               ExamPeriodModule(PERIOD, repeat_only, "repeat_only"))
    data = dataclasses.replace(synthetic, modules=modules, exam_period_modules=members)
    client = make_client(tokens, identity, Harness(DatasetSource(data)))
    response = client.post(URL, headers=admin, json=_cycle_body())
    assert response.status_code == 200, response.text
    sessions = {s["module_code"]: s for s in response.json()["sessions"]}
    _check_session_details(data, sessions.values())
    ro = sessions[repeat_only]
    assert ro["candidate_scope"] == "repeat_only" and ro["semester"] == 1
    assert ro["normal_candidates"] == 0 and ro["repeat_candidates"] == ro["candidates"] > 0
    assert sessions["IT1030"]["delivery_mode"] == "block" and sessions["IT1010"]["delivery_mode"] == "regular"


def test_split_sessions_count_candidate_types_per_session(client, admin, harness) -> None:
    asyncio.run(harness.save(scheduling__max_rooms_per_exam=1, scheduling__max_sessions_per_exam=4))
    data = client.post(URL, headers=admin, json=body(time_limit_seconds=60)).json()  # per-student session choices
    assert data["generated"], (data["status"], data["diagnostics"])
    _check_session_details(DATASET, data["sessions"])
    split = {}
    for s in data["sessions"]:
        if s["session_count"] > 1:
            split.setdefault(s["module_code"], []).append(s)
    assert split
    for code, sessions in split.items():
        assert sorted(s["session_label"] for s in sessions) == [f"Session {i:02d}" for i in range(1, len(sessions) + 1)]
        types = _registration_types(DATASET, code)
        assert sum(s["normal_candidates"] for s in sessions) == types["normal"]
        assert sum(s["repeat_candidates"] for s in sessions) == types["repeat"]


# --- Potentially excluded registrations -------------------------------------------------------------------------


def test_registrations_outside_the_cycle_are_reported_without_student_ids(tokens, identity, admin) -> None:
    client = _cycle_client(tokens, identity, [(code, "all") for code in SHARED])
    response = client.post(URL, headers=admin, json=_cycle_body())
    data = response.json()
    assert data["generated"] and data["validation"]["passed"]  # a valid timetable is still generated
    assert {s["module_code"] for s in data["sessions"]} == set(SHARED)  # nothing is added automatically
    excluded = data["excluded_registrations"]
    outside = [r for r in DATASET.registrations if r.module_code not in SHARED]
    assert excluded["total"] == len(outside) and excluded["note"].startswith("Potentially excluded")
    assert excluded["repeat"] == sum(r.registration_type == "repeat" for r in outside)
    rows = {m["module_code"]: m for m in excluded["modules"]}
    assert set(rows) == {r.module_code for r in outside} and not set(rows) & set(SHARED)
    for code, row in rows.items():
        types = Counter(r.registration_type for r in outside if r.module_code == code)
        assert (row["normal_registrations"], row["repeat_registrations"], row["other_cycles"]) == (
            types["normal"], types["repeat"], [])
    assert any(n.startswith(f"Warning: {len(outside)} registration(s)") for n in data["notices"])
    assert not any(s.student_id in response.text for s in DATASET.students)  # no student identifiers anywhere


def test_no_excluded_registrations_when_every_module_is_mapped(tokens, identity, admin) -> None:
    client = _cycle_client(tokens, identity, [(m.module_code, "all") for m in DATASET.modules])
    data = client.post(URL, headers=admin, json=body(modules=SHARED)).json()  # partial run of a complete cycle
    assert data["excluded_registrations"]["total"] == 0 and data["excluded_registrations"]["modules"] == []
    assert not any(n.startswith("Warning:") for n in data["notices"])


def test_periods_without_a_mapping_report_no_excluded_registrations(client, admin) -> None:
    data = client.post(URL, headers=admin, json=body()).json()
    assert data["excluded_registrations"]["total"] == 0 and data["inputs"]["selection"] == "listed_modules"
