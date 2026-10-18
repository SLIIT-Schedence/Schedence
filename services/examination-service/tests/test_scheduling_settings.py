"""Admin scheduling settings: validation, versioning, RBAC, rollback and scheduler use.

Real RS256 tokens and a fake Identity Service (as in test_seating_api.py); the
settings live in an in-memory store with real transaction behaviour. The same
store flows run against PostgreSQL in test_seating_postgres.py.
"""

from __future__ import annotations

import copy
import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_scheduling_settings_store, get_scheduling_settings_writer, get_seating_store, get_source
from app.main import create_app
from app.scheduling.configuration import MANDATORY_RULES, SETTINGS_FORMAT, SchedulingConfiguration, changed_fields
from app.scheduling.contracts import SchedulingValidationError
from app.scheduling_settings.errors import (
    SchedulingSettingsConflictError,
    SchedulingSettingsInvalidError,
    SchedulingSettingsInvalidStoredError,
    SchedulingSettingsNotConfiguredError,
)
from app.scheduling_settings.memory import MemorySchedulingSettingsStore
from app.scheduling_settings.service import SchedulingSettingsService
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
)
from schedence_auth.testing import TokenFactory

API = "/api/v1"
SETTINGS = f"{API}/settings/scheduling"
SOURCE = DatasetSource(generate_dataset())
REASON = "Exam board decision 2026-10"
USER = uuid.uuid4()


def defaults() -> dict:
    return SchedulingConfiguration().to_document()


def edited(**changes) -> dict:
    """Defaults with ``section__key=value`` changes."""
    document = defaults()
    for path, value in changes.items():
        section, key = path.split("__")
        document[section][key] = value
    return document


def violations(document) -> list[str]:
    with pytest.raises(SchedulingValidationError) as exc:
        SchedulingConfiguration.from_document(document)
    return [v.message for v in exc.value.violations]


# --- The settings document --------------------------------------------------------------------------------


def test_defaults_round_trip_through_json_unchanged() -> None:
    document = json.loads(json.dumps(defaults()))
    configuration = SchedulingConfiguration.from_document(document)
    assert configuration == SchedulingConfiguration()
    assert configuration.fingerprint == SchedulingConfiguration().fingerprint
    assert configuration.to_document() == defaults()


def test_saved_values_reach_the_existing_policy_classes() -> None:
    configuration = SchedulingConfiguration.from_document(edited(
        scheduling__permitted_start_times=["09:00", "14:00"], scheduling__minimum_break_minutes=90,
        scheduling__max_rooms_per_exam=3, solver__time_limit_seconds=30, optimization__s3_weight=40,
        fairness__enabled=True, fairness__max_individual_increase_points=2.5,
    ))
    assert [t.strftime("%H:%M") for t in configuration.scheduling.permitted_start_times] == ["09:00", "14:00"]
    assert configuration.scheduling.minimum_break_minutes == 90
    assert configuration.scheduling.max_rooms_per_exam == 3
    assert configuration.solver.time_limit_seconds == 30.0
    assert configuration.optimization.s3_weight == 40
    assert configuration.fairness.enabled and configuration.fairness.max_individual_increase_points == 2.5
    assert configuration.fingerprint != SchedulingConfiguration().fingerprint


@pytest.mark.parametrize("section,key", [
    (None, "disable_student_clash_check"), (None, "hard_constraints"),
    ("scheduling", "enforce_room_capacity"), ("scheduling", "allow_room_conflicts"),
    ("scheduling", "student_clash_prevention"), ("solver", "skip_validation"),
    ("optimization", "enabled"), ("fairness", "allow_relaxation"),
])
def test_hard_rules_cannot_be_switched_off_by_any_key(section, key) -> None:
    document = defaults()
    (document if section is None else document[section])[key] = False
    messages = violations(document)
    assert any(key in m and "H1-H11 are always enforced" in m for m in messages), messages


@pytest.mark.parametrize("section,key", [(None, "solver"), ("scheduling", "minimum_break_minutes"),
                                         ("optimization", "s1_weight"), ("fairness", "enabled")])
def test_incomplete_documents_are_rejected_so_nothing_resets_silently(section, key) -> None:
    document = defaults()
    del (document if section is None else document[section])[key]
    assert any("Missing" in m and key in m for m in violations(document))


@pytest.mark.parametrize("changes,expected", [
    ({"scheduling__permitted_start_times": []}, "Permitted starts must be non-empty"),
    ({"scheduling__permitted_start_times": ["8:30"]}, '"HH:MM"'),
    ({"scheduling__permitted_start_times": ["08:30", "08:30"]}, "unique"),
    ({"scheduling__permitted_start_times": ["24:00"]}, '"HH:MM"'),
    ({"scheduling__permitted_start_times": "08:30"}, '"HH:MM"'),
    ({"scheduling__minimum_break_minutes": -1}, "Minimum break"),
    ({"scheduling__minimum_break_minutes": True}, "Minimum break"),
    ({"scheduling__minimum_break_minutes": 60.0}, "Minimum break"),
    ({"scheduling__max_rooms_per_exam": 0}, "Maximum rooms"),
    ({"scheduling__venue_compatibility": {"written": ["moon_base"], "computer_based": ["computer_lab"],
                                          "hybrid": ["byod_lab"]}}, "unknown venue types: moon_base"),
    ({"scheduling__venue_compatibility": {"written": ["lecture_hall"]}}, "needs every exam type"),
    ({"scheduling__venue_compatibility": {"written": [], "computer_based": ["computer_lab"],
                                          "hybrid": ["byod_lab"]}}, "non-empty list"),
    ({"scheduling__module_venue_overrides": {"not a module": ["lecture_hall"]}}, "unknown module code"),
    ({"solver__time_limit_seconds": 0}, "time limit"),
    ({"solver__time_limit_seconds": "60"}, "finite number"),
    ({"solver__time_limit_seconds": 10**400}, "finite number"),
    ({"solver__num_search_workers": 0}, "workers"),
    ({"solver__random_seed": True}, "seed"),
    ({"optimization__short_gap_minutes": 5000}, "0 < short < preparation < difficult"),
    ({"optimization__s1_weight": -1}, "s1_weight"),
    ({"fairness__automatic_relaxation": True}, "relaxation is not supported"),
    ({"fairness__max_individual_increase_points": 101}, "between 0 and 100"),
])
def test_invalid_values_are_rejected_by_the_existing_rules(changes, expected) -> None:
    assert any(expected in m for m in violations(edited(**changes))), violations(edited(**changes))


def test_every_problem_is_reported_at_once() -> None:
    messages = violations(edited(scheduling__minimum_break_minutes=-1, solver__num_search_workers=0,
                                 fairness__automatic_relaxation=True))
    assert len(messages) == 3


@pytest.mark.parametrize("document", [None, [], "settings", {"scheduling": []}])
def test_non_object_documents_are_rejected(document) -> None:
    assert violations(document)


def test_changed_fields_lists_dotted_paths() -> None:
    assert changed_fields(defaults(), edited(scheduling__minimum_break_minutes=90, fairness__enabled=True)) == [
        "fairness.enabled", "scheduling.minimum_break_minutes"]
    assert changed_fields(None, defaults()) == []  # nothing saved yet: compared with the defaults
    assert changed_fields(None, edited(solver__random_seed=1)) == ["solver.random_seed"]
    broken = {**defaults(), "solver": "oops", "extra": {"x": 1}}
    broken["fairness"] = {k: v for k, v in defaults()["fairness"].items() if k != "enabled"}
    assert changed_fields(broken, defaults()) == ["extra.x", "fairness.enabled", "solver", *[
        f"solver.{k}" for k in sorted(defaults()["solver"])]]


def test_mandatory_rules_list_all_eleven_hard_rules() -> None:
    assert [rule.split(":")[0] for rule in MANDATORY_RULES] == [f"H{i}" for i in range(1, 12)]


def test_prototype_settings_file_matches_the_built_in_defaults() -> None:
    from conftest import SERVICE_DIR
    path = SERVICE_DIR / "data" / "scheduling_settings.prototype.json"
    assert json.loads(path.read_text(encoding="utf-8")) == defaults()


# --- Service: versioning, dry run, rollback ----------------------------------------------------------------


@pytest.fixture
def store() -> MemorySchedulingSettingsStore:
    return MemorySchedulingSettingsStore()


def service(store, institution=INSTITUTION) -> SchedulingSettingsService:
    return SchedulingSettingsService(store, institution)


async def test_defaults_apply_until_settings_are_saved(store) -> None:
    view = await service(store).view(history_limit=10)
    assert (view.source, view.version, view.saved, view.history) == ("defaults", 0, None, ())
    assert await service(store).configuration() == SchedulingConfiguration()


async def test_first_save_creates_version_1_with_history(store) -> None:
    change = await service(store).update(edited(scheduling__minimum_break_minutes=90), reason=REASON,
                                         expected_version=0, user_id=USER, dry_run=False)
    assert (change.changed, change.action, change.version) == (True, "create", 1)
    assert change.changed_fields == ("scheduling.minimum_break_minutes",)
    view = await service(store).view(history_limit=10)
    assert (view.source, view.version, view.saved.updated_by, view.saved.format) == ("saved", 1, USER, SETTINGS_FORMAT)
    assert view.configuration.scheduling.minimum_break_minutes == 90
    [entry] = view.history
    assert (entry.action, entry.version, entry.old_settings, entry.changed_by, entry.reason) == (
        "create", 1, None, USER, REASON)
    assert (await service(store).configuration()).scheduling.minimum_break_minutes == 90


async def test_updates_need_the_current_version(store) -> None:
    await service(store).update(defaults(), reason=REASON, expected_version=0, user_id=USER, dry_run=False)
    with pytest.raises(SchedulingSettingsConflictError) as exc:
        await service(store).update(edited(scheduling__minimum_break_minutes=30), reason=REASON,
                                    expected_version=0, user_id=USER, dry_run=False)
    assert exc.value.status_code == 409 and exc.value.current_version == 1
    change = await service(store).update(edited(scheduling__minimum_break_minutes=30), reason=REASON,
                                         expected_version=1, user_id=USER, dry_run=False)
    assert (change.action, change.version) == ("update", 2)
    history = (await service(store).view(history_limit=10)).history
    assert [h.version for h in history] == [2, 1]
    assert history[0].old_settings == defaults()


async def test_saving_the_same_settings_is_a_no_op(store) -> None:
    await service(store).update(defaults(), reason=REASON, expected_version=0, user_id=USER, dry_run=False)
    change = await service(store).update(defaults(), reason=REASON, expected_version=1, user_id=USER, dry_run=False)
    assert (change.changed, change.version, change.history_id) == (False, 1, None)
    assert store.commits == 1


async def test_dry_run_validates_and_previews_but_saves_nothing(store) -> None:
    change = await service(store).update(edited(fairness__enabled=True), reason=REASON, expected_version=0,
                                         user_id=USER, dry_run=True)
    assert (change.dry_run, change.changed, change.action, change.version, change.history_id) == (
        True, True, "create", 1, None)
    assert change.changed_fields == ("fairness.enabled",)
    assert (await service(store).view(history_limit=10)).version == 0 and store.commits == 0


async def test_invalid_document_saves_nothing(store) -> None:
    with pytest.raises(SchedulingSettingsInvalidError) as exc:
        await service(store).update(edited(scheduling__minimum_break_minutes=-5), reason=REASON,
                                    expected_version=0, user_id=USER, dry_run=False)
    assert exc.value.status_code == 422 and exc.value.detail[0]["rule"] == "SCHEDULING"
    assert (await service(store).view(history_limit=10)).version == 0


@pytest.mark.parametrize("step", ["insert", "add_history", "commit"])
async def test_a_failed_write_rolls_back_settings_and_history(store, step) -> None:
    store.fail_on = step
    with pytest.raises(RuntimeError):
        await service(store).update(defaults(), reason=REASON, expected_version=0, user_id=USER, dry_run=False)
    store.fail_on = None
    view = await service(store).view(history_limit=10)
    assert (view.version, view.history) == (0, ())


async def test_settings_belong_to_one_institution(store) -> None:
    await service(store).update(edited(scheduling__minimum_break_minutes=90), reason=REASON, expected_version=0,
                                user_id=USER, dry_run=False)
    other = await service(store, OTHER_INSTITUTION).view(history_limit=10)
    assert (other.source, other.version, other.history) == ("defaults", 0, ())


async def test_an_unusable_saved_document_fails_safely(store) -> None:
    await service(store).update(defaults(), reason=REASON, expected_version=0, user_id=USER, dry_run=False)
    saved = store._committed.settings[INSTITUTION]
    broken = copy.deepcopy(saved.settings)
    broken["scheduling"]["minimum_break_minutes"] = -1  # e.g. edited by hand in the database
    from dataclasses import replace
    for bad in (replace(saved, settings=broken), replace(saved, format="scheduling-settings-v0")):
        store._committed.settings[INSTITUTION] = store._work.settings[INSTITUTION] = bad
        with pytest.raises(SchedulingSettingsInvalidStoredError) as exc:
            await service(store).configuration()
        assert exc.value.status_code == 503


# --- API ----------------------------------------------------------------------------------------------------


@pytest.fixture
def tokens() -> TokenFactory:
    return TokenFactory()


@pytest.fixture
def identity() -> FakeIdentityService:
    return FakeIdentityService()


@pytest.fixture
def client(tokens, identity, store) -> TestClient:
    app = create_app(make_auth_context(tokens, identity))
    app.dependency_overrides[get_source] = lambda: SOURCE
    app.dependency_overrides[get_seating_store] = lambda: MemorySeatingStore()
    app.dependency_overrides[get_scheduling_settings_store] = lambda: store
    app.dependency_overrides[get_scheduling_settings_writer] = lambda: SchedulingSettingsService(store, INSTITUTION)
    return TestClient(app)


def headers(tokens, identity, *roles, institution=INSTITUTION, permissions=None) -> dict[str, str]:
    perms = permissions if permissions is not None else sorted({p for r in roles for p in ROLE_PERMISSIONS[r]})
    sub = identity.register({institution: (list(roles), perms)})
    return {"Authorization": f"Bearer {tokens.access_token(sub)}"}


@pytest.fixture
def admin(tokens, identity) -> dict[str, str]:
    return headers(tokens, identity, "EXAMINATION_ADMIN")


def body(document=None, version=0, reason=REASON) -> dict:
    return {"settings": defaults() if document is None else document, "reason": reason, "expected_version": version}


def test_get_shows_defaults_and_mandatory_rules(client, admin) -> None:
    response = client.get(SETTINGS, headers=admin)
    assert response.status_code == 200
    data = response.json()
    assert (data["source"], data["status"], data["problems"], data["version"], data["format"]) == (
        "defaults", "valid", [], 0, SETTINGS_FORMAT)
    assert data["institution_id"] == str(INSTITUTION)
    assert data["settings"] == defaults() and data["fingerprint"] == SchedulingConfiguration().fingerprint
    assert data["mandatory_rules"] == list(MANDATORY_RULES)
    assert data["history"] == [] and data["updated_by"] is None


def test_put_saves_versioned_settings_with_history(client, admin, identity) -> None:
    document = edited(scheduling__permitted_start_times=["09:00", "14:00"], optimization__s1_weight=30)
    response = client.put(SETTINGS, headers=admin, json=body(document))
    assert response.status_code == 200, response.text
    change = response.json()
    assert (change["changed"], change["action"], change["version"], change["dry_run"]) == (True, "create", 1, False)
    assert change["changed_fields"] == ["optimization.s1_weight", "scheduling.permitted_start_times"]
    data = client.get(SETTINGS, headers=admin).json()
    assert (data["source"], data["version"], data["settings"]) == ("saved", 1, document)
    assert data["updated_by"] == change["changed_by"] and data["reason"] == REASON
    assert data["history"][0]["changed_fields"] == change["changed_fields"]  # response and history agree
    response = client.put(SETTINGS, headers=admin, json=body(edited(scheduling__minimum_break_minutes=45), version=1))
    assert response.json()["version"] == 2
    history = client.get(SETTINGS, headers=admin).json()["history"]
    assert [h["version"] for h in history] == [2, 1]
    assert "scheduling.minimum_break_minutes" in history[0]["changed_fields"]


def test_put_with_a_stale_version_is_409_and_saves_nothing(client, admin) -> None:
    client.put(SETTINGS, headers=admin, json=body())
    response = client.put(SETTINGS, headers=admin, json=body(edited(scheduling__minimum_break_minutes=1), version=0))
    assert response.status_code == 409 and "Current version: 1" in response.json()["detail"]
    assert client.get(SETTINGS, headers=admin).json()["settings"] == defaults()


@pytest.mark.parametrize("document", [
    edited(scheduling__minimum_break_minutes=-1),
    {**defaults(), "disable_room_capacity_check": True},
    edited(fairness__automatic_relaxation=True),
])
def test_put_rejects_invalid_or_rule_disabling_settings(client, admin, document) -> None:
    response = client.put(SETTINGS, headers=admin, json=body(document))
    assert response.status_code == 422
    assert all(set(item) == {"rule", "message"} for item in response.json()["detail"])
    assert client.get(SETTINGS, headers=admin).json()["version"] == 0


@pytest.mark.parametrize("payload", [
    {"settings": defaults(), "reason": REASON, "expected_version": "0"},
    {"settings": defaults(), "reason": REASON, "expected_version": -1},
    {"settings": defaults(), "reason": "x", "expected_version": 0},
    {"settings": defaults(), "expected_version": 0},
    {"settings": defaults(), "reason": REASON, "expected_version": 0, "extra": 1},
    {"settings": [], "reason": REASON, "expected_version": 0},
])
def test_put_body_is_strict(client, admin, payload) -> None:
    assert client.put(SETTINGS, headers=admin, json=payload).status_code == 422


def test_dry_run_saves_nothing(client, admin) -> None:
    response = client.put(SETTINGS, headers=admin, params={"dry_run": "true"},
                          json=body(edited(fairness__enabled=True)))
    assert response.status_code == 200 and response.json()["dry_run"] and response.json()["version"] == 1
    assert client.get(SETTINGS, headers=admin).json()["version"] == 0


def test_missing_storage_is_a_safe_503(tokens, identity, admin) -> None:
    class MissingTables(MemorySchedulingSettingsStore):
        async def get(self, *args, **kwargs):
            raise SchedulingSettingsNotConfiguredError()

    missing = MissingTables()
    app = create_app(make_auth_context(tokens, identity))
    app.dependency_overrides[get_scheduling_settings_store] = lambda: missing
    app.dependency_overrides[get_scheduling_settings_writer] = lambda: SchedulingSettingsService(missing, INSTITUTION)
    client = TestClient(app)
    for response in (client.get(SETTINGS, headers=admin), client.put(SETTINGS, headers=admin, json=body())):
        assert response.status_code == 503
        assert response.json()["detail"] == "Examination scheduling settings are not available. Please try again later."


def test_no_or_invalid_token_is_401(client) -> None:
    for auth in ({}, {"Authorization": "Bearer not-a-jwt"}):
        assert client.get(SETTINGS, headers=auth).status_code == 401
        assert client.put(SETTINGS, headers=auth, json=body()).status_code == 401


@pytest.mark.parametrize("role", sorted(ROLE_PERMISSIONS))
def test_only_roles_with_examination_permissions_get_access(client, tokens, identity, role) -> None:
    auth = headers(tokens, identity, role)
    allowed = role == "EXAMINATION_ADMIN"
    assert client.get(SETTINGS, headers=auth).status_code == (200 if allowed else 403)
    assert client.put(SETTINGS, headers=auth, json=body()).status_code == (200 if allowed else 403)


@pytest.mark.parametrize("permissions", EXAMINATION_PERMISSION_SETS)
def test_read_needs_read_and_write_needs_manage(client, tokens, identity, permissions) -> None:
    auth = headers(tokens, identity, "CUSTOM", permissions=permissions)
    assert client.get(SETTINGS, headers=auth).status_code == (200 if "examinations:read" in permissions else 403)
    response = client.put(SETTINGS, headers=auth, params={"dry_run": "true"}, json=body())
    assert response.status_code == (200 if "examinations:manage" in permissions else 403)


def test_permissions_in_another_institution_do_not_count(client, tokens, identity) -> None:
    auth = headers(tokens, identity, "EXAMINATION_ADMIN", institution=OTHER_INSTITUTION)
    assert client.get(SETTINGS, headers=auth).status_code == 403
    assert client.put(SETTINGS, headers=auth, json=body()).status_code == 403


# --- The scheduler uses the saved settings ------------------------------------------------------------------


def test_scheduler_runs_with_settings_saved_through_the_api(client, admin, tmp_path, capsys) -> None:
    pytest.importorskip("ortools")
    from app.scheduling.__main__ import main
    document = edited(scheduling__permitted_start_times=["09:00", "14:00"], scheduling__minimum_break_minutes=120,
                      solver__time_limit_seconds=20)
    assert client.put(SETTINGS, headers=admin, json=body(document)).status_code == 200
    saved = tmp_path / "saved.json"
    saved.write_text(client.get(SETTINGS, headers=admin).text, encoding="utf-8")  # the whole GET response
    capsys.readouterr()
    assert main(["--settings-file", str(saved), "--module", "IT1010", "--module", "IE2012", "--include-draft"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["settings_source"] == "file"
    assert payload["settings_fingerprint"] == SchedulingConfiguration.from_document(document).fingerprint
    assert payload["policy"]["minimum_break_minutes"] == 120 and payload["settings"]["time_limit_seconds"] == 20
    assert payload["summary"]["independent_validation_passed"]
    assert {s["start"][11:16] for s in payload["timetable"]["sessions"]} <= {"09:00", "14:00"}


def test_scheduler_optimizes_with_saved_weights_and_fairness(tmp_path, capsys) -> None:
    pytest.importorskip("ortools")
    from app.scheduling.__main__ import main
    document = edited(optimization__s5_weight=0, fairness__enabled=True, solver__time_limit_seconds=5)
    saved = tmp_path / "settings.json"
    saved.write_text(json.dumps(document), encoding="utf-8")  # just the document
    assert main(["--settings-file", str(saved), "--optimize", "--module", "IT1010"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["optimization_policy"]["s5_weight"] == 0
    assert payload["fairness_policy"]["enabled"] is True


@pytest.mark.parametrize("option", [["--minimum-break", "5"], ["--starts", "09:00"], ["--time-limit", "3"],
                                    ["--seed", "1"], ["--workers", "2"], ["--max-rooms", "2"],
                                    ["--compatibility", "written=lecture_hall"], ["--optimize", "--fairness"],
                                    ["--optimize", "--optimization-config", "x.json"]])
def test_settings_file_cannot_be_mixed_with_individual_options(tmp_path, capsys, option) -> None:
    from app.scheduling.__main__ import main
    saved = tmp_path / "settings.json"
    saved.write_text(json.dumps(defaults()), encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        main(["--settings-file", str(saved), *option])
    assert exc.value.code == 2
    assert "--settings-file cannot be combined" in json.loads(capsys.readouterr().out)["error"]["message"]


@pytest.mark.parametrize("content", [json.dumps({**defaults(), "disable_h5": True}),
                                     json.dumps({"format": "scheduling-settings-v0", "settings": defaults()}),
                                     '{"scheduling": {}, "scheduling": {}}', "not json"])
def test_invalid_settings_file_is_a_json_model_invalid(tmp_path, capsys, content) -> None:
    from app.scheduling.__main__ import main
    saved = tmp_path / "settings.json"
    saved.write_text(content, encoding="utf-8")
    assert main(["--settings-file", str(saved)]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "MODEL_INVALID" and payload["violations"]


# --- Server-side resource limits (API, saved settings, command line, library) --------------------------------

from app.scheduling.policy import (  # noqa: E402
    MAX_MINIMUM_BREAK_MINUTES,
    MAX_SEARCH_WORKERS,
    MAX_TIME_LIMIT_SECONDS,
    SchedulingPolicy,
    SolverSettings,
)


def test_limits_are_documented_values() -> None:
    assert (MAX_TIME_LIMIT_SECONDS, MAX_SEARCH_WORKERS, MAX_MINIMUM_BREAK_MINUTES) == (3600, 16, 1440)


@pytest.mark.parametrize("changes,expected", [
    ({"solver__time_limit_seconds": MAX_TIME_LIMIT_SECONDS + 0.5}, "at most 3600 seconds"),
    ({"solver__num_search_workers": MAX_SEARCH_WORKERS + 1}, "at most 16"),
    ({"scheduling__minimum_break_minutes": MAX_MINIMUM_BREAK_MINUTES + 1}, "at most 1440 minutes"),
])
def test_documents_above_the_limits_are_rejected(changes, expected) -> None:
    assert any(expected in m for m in violations(edited(**changes)))


def test_documents_at_the_limits_are_accepted() -> None:
    configuration = SchedulingConfiguration.from_document(edited(
        solver__time_limit_seconds=MAX_TIME_LIMIT_SECONDS, solver__num_search_workers=MAX_SEARCH_WORKERS,
        scheduling__minimum_break_minutes=MAX_MINIMUM_BREAK_MINUTES))
    assert configuration.solver.num_search_workers == MAX_SEARCH_WORKERS


def test_the_same_limits_apply_to_the_policy_classes() -> None:
    assert SolverSettings(time_limit_seconds=MAX_TIME_LIMIT_SECONDS + 1).errors()
    assert SolverSettings(num_search_workers=MAX_SEARCH_WORKERS + 1).errors()
    assert SchedulingPolicy(minimum_break_minutes=MAX_MINIMUM_BREAK_MINUTES + 1).errors()
    assert not SolverSettings(MAX_TIME_LIMIT_SECONDS, 1, MAX_SEARCH_WORKERS).errors()


async def test_the_scheduler_refuses_settings_above_the_limits() -> None:
    from app.scheduling import generate_timetable, load_snapshot, optimize_timetable
    from app.scheduling.types import SolveStatus
    snapshot = await load_snapshot(SOURCE, MemorySeatingStore(), period_code="PP1-2026-S2-FINAL",
                                   module_codes=("IT1010",))
    for settings in (SolverSettings(time_limit_seconds=MAX_TIME_LIMIT_SECONDS + 1),
                     SolverSettings(num_search_workers=MAX_SEARCH_WORKERS + 1)):
        assert generate_timetable(snapshot, settings).status == SolveStatus.MODEL_INVALID
        assert optimize_timetable(snapshot, settings).status == SolveStatus.MODEL_INVALID


@pytest.mark.parametrize("option", [["--time-limit", "3601"], ["--workers", "17"], ["--minimum-break", "1441"]])
def test_the_command_line_applies_the_same_limits(capsys, option) -> None:
    from app.scheduling.__main__ import main
    assert main(["--module", "IT1010", *option]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "MODEL_INVALID" and "at most" in json.dumps(payload["violations"])


def test_the_api_applies_the_same_limits(client, admin) -> None:
    response = client.put(SETTINGS, headers=admin, json=body(edited(solver__num_search_workers=64)))
    assert response.status_code == 422 and "at most 16" in response.text


# --- Repairing an invalid saved configuration ------------------------------------------------------------------

from dataclasses import replace as _replace  # noqa: E402


async def save_then_break(store, *, settings=None, format=None) -> dict:
    """Save valid settings (version 1), then make the stored row invalid, as a code update or manual edit might."""
    await service(store).update(defaults(), reason=REASON, expected_version=0, user_id=USER, dry_run=False)
    saved = store._committed.settings[INSTITUTION]
    if settings is None:
        settings = copy.deepcopy(saved.settings)
        settings["solver"]["num_search_workers"] = 64  # valid before the worker limit existed
    broken = _replace(saved, settings=settings, format=format or saved.format)
    store._committed.settings[INSTITUTION] = broken
    store._work.settings[INSTITUTION] = copy.deepcopy(broken)
    return copy.deepcopy(broken.settings)


def repair(document=None, version=1) -> dict:
    return body(document, version=version, reason="Repair invalid scheduling settings")


REPLACE = {"replace_invalid": "true"}


async def test_get_reports_an_invalid_configuration_without_using_defaults(client, admin, store) -> None:
    broken = await save_then_break(store)
    data = client.get(SETTINGS, headers=admin).json()
    assert (data["source"], data["status"], data["version"], data["fingerprint"]) == ("saved", "invalid", 1, None)
    assert data["settings"] == broken  # the stored document, not the defaults
    assert data["problems"] == [{"rule": "SOLVER", "message": "Solver workers must be at most 16."}]
    assert data["mandatory_rules"] == list(MANDATORY_RULES)


async def test_scheduler_stays_blocked_until_repaired(client, admin, store) -> None:
    await save_then_break(store)
    with pytest.raises(SchedulingSettingsInvalidStoredError):
        await service(store).configuration()
    assert client.put(SETTINGS, headers=admin, params=REPLACE, json=repair()).status_code == 200
    assert await service(store).configuration() == SchedulingConfiguration()


def test_command_line_refuses_an_exported_invalid_configuration(client, admin, store, tmp_path, capsys) -> None:
    import asyncio
    from app.scheduling.__main__ import main
    asyncio.run(save_then_break(store))  # synchronous test: the CLI runs its own event loop
    exported = tmp_path / "exported.json"
    exported.write_text(client.get(SETTINGS, headers=admin).text, encoding="utf-8")
    assert main(["--settings-file", str(exported)]) == 1
    assert "must repair them" in capsys.readouterr().out


async def test_repair_needs_replace_invalid(client, admin, store) -> None:
    broken = await save_then_break(store)
    response = client.put(SETTINGS, headers=admin, json=repair())
    assert response.status_code == 409 and "replace_invalid=true" in response.json()["detail"]
    assert client.get(SETTINGS, headers=admin).json()["settings"] == broken


@pytest.mark.parametrize("format", [None, "scheduling-settings-v0"])
async def test_repair_replaces_the_document_and_keeps_the_broken_one_in_history(client, admin, store, format) -> None:
    broken = await save_then_break(store, format=format)
    old_format = format or SETTINGS_FORMAT
    document = edited(scheduling__minimum_break_minutes=90)
    response = client.put(SETTINGS, headers=admin, params=REPLACE, json=repair(document))
    assert response.status_code == 200, response.text
    change = response.json()
    assert (change["action"], change["version"], change["changed"]) == ("repair", 2, True)
    assert "scheduling.minimum_break_minutes" in change["changed_fields"]
    data = client.get(SETTINGS, headers=admin).json()
    assert (data["status"], data["version"], data["settings"], data["problems"]) == ("valid", 2, document, [])
    assert data["history"][0]["action"] == "repair"
    assert data["history"][0]["changed_fields"] == change["changed_fields"]  # response and history agree
    entry = (await store.list_history(INSTITUTION, limit=5))[0]
    assert (entry.old_settings, entry.old_format, entry.format) == (broken, old_format, SETTINGS_FORMAT)
    assert entry.new_settings == document and entry.reason == "Repair invalid scheduling settings"


async def test_repair_of_a_document_that_is_not_even_complete(client, admin, store) -> None:
    broken = await save_then_break(store, settings={"solver": "not an object", "legacy_key": 1})
    data = client.get(SETTINGS, headers=admin).json()
    assert data["status"] == "invalid" and data["settings"] == broken and data["problems"]
    response = client.put(SETTINGS, headers=admin, params=REPLACE, json=repair())
    assert response.status_code == 200
    assert {"solver", "legacy_key"} <= set(response.json()["changed_fields"])


async def test_repair_still_needs_the_current_version(client, admin, store) -> None:
    broken = await save_then_break(store)
    response = client.put(SETTINGS, headers=admin, params=REPLACE, json=repair(version=0))
    assert response.status_code == 409 and "Current version: 1" in response.json()["detail"]
    assert client.get(SETTINGS, headers=admin).json()["settings"] == broken


async def test_repair_still_validates_the_new_document(client, admin, store) -> None:
    broken = await save_then_break(store)
    for document in (edited(solver__num_search_workers=64), {**defaults(), "disable_h1": True}):
        response = client.put(SETTINGS, headers=admin, params=REPLACE, json=repair(document))
        assert response.status_code == 422
    assert client.get(SETTINGS, headers=admin).json()["settings"] == broken


async def test_repair_dry_run_saves_nothing(client, admin, store) -> None:
    broken = await save_then_break(store)
    response = client.put(SETTINGS, headers=admin, params={**REPLACE, "dry_run": "true"}, json=repair())
    assert response.status_code == 200
    change = response.json()
    assert (change["action"], change["version"], change["history_id"]) == ("repair", 2, None)
    data = client.get(SETTINGS, headers=admin).json()
    assert (data["status"], data["version"], data["settings"], len(data["history"])) == ("invalid", 1, broken, 1)


async def test_replace_invalid_is_refused_when_nothing_is_invalid(client, admin, store) -> None:
    response = client.put(SETTINGS, headers=admin, params=REPLACE, json=body())
    assert response.status_code == 409 and "only allowed" in response.json()["detail"]  # nothing saved yet
    client.put(SETTINGS, headers=admin, json=body())
    response = client.put(SETTINGS, headers=admin, params=REPLACE, json=body(edited(solver__random_seed=1), version=1))
    assert response.status_code == 409 and client.get(SETTINGS, headers=admin).json()["version"] == 1


@pytest.mark.parametrize("permissions", [[], ["examinations:read"], ["examinations:generate", "examinations:approve"]])
async def test_repair_needs_manage_permission(client, tokens, identity, store, permissions) -> None:
    broken = await save_then_break(store)
    auth = headers(tokens, identity, "CUSTOM", permissions=permissions)
    assert client.put(SETTINGS, headers=auth, params=REPLACE, json=repair()).status_code == 403
    other = headers(tokens, identity, "EXAMINATION_ADMIN", institution=OTHER_INSTITUTION)
    assert client.put(SETTINGS, headers=other, params=REPLACE, json=repair()).status_code == 403
    assert store._committed.settings[INSTITUTION].settings == broken


@pytest.mark.parametrize("step", ["update", "add_history", "commit"])
async def test_a_failed_repair_keeps_the_invalid_document(store, step) -> None:
    broken = await save_then_break(store)
    store.fail_on = step
    with pytest.raises(RuntimeError):
        await service(store).update(defaults(), reason=REASON, expected_version=1, user_id=USER, dry_run=False,
                                    replace_invalid=True)
    store.fail_on = None
    view = await service(store).view(history_limit=10)
    assert (view.status, view.version, view.saved.settings, len(view.history)) == ("invalid", 1, broken, 1)
