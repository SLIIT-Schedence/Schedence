"""Per-timetable preferences through real CP-SAT and in-memory workflow stores. No database connections."""
from __future__ import annotations

import asyncio
import copy
import dataclasses
import datetime as dt

import pytest

from app.generation.preferences import effective_configuration, saved_preferences
from app.scheduling.configuration import SchedulingConfiguration
from app.seating.memory import MemorySeatingStore
from app.seating.snapshot import load_capacity_snapshot
from test_generation_calendar import SELECTED, CalendarHarness, admin, identity, tokens  # noqa: F401
from test_saved_timetables import client, store, DRAFTS, GENERATE, LIST  # noqa: F401
from test_timetable_generation import body, headers
from auth_support import INSTITUTION, OTHER_INSTITUTION


@pytest.fixture
def harness():
    return CalendarHarness()


PREFERENCES = {
    "scheduling": {"permitted_start_times": ["09:00", "13:30"], "minimum_break_minutes": 90,
                   "max_rooms_per_exam": 4, "max_sessions_per_exam": 4},
    "solver": {"time_limit_seconds": 12, "random_seed": 17, "num_search_workers": 1},
    "optimization": {"s1_weight": 40, "short_gap_minutes": 600},
    "fairness": {"enabled": True, "max_individual_increase_points": 0.0},
    "seating": {"default_percentage": 60},
}


def save(client, admin, preferences=PREFERENCES, **extra):
    response = client.post(DRAFTS, headers=admin, json=body(scheduling_preferences=preferences, **extra))
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["validation"]["passed"] is True
    assert len(result["validation"]["rules_checked"]) == 11
    return result


def change(client, admin, timetable, action):
    return client.post(f"{LIST}/{timetable['timetable_id']}/{action}", headers=admin,
                       json={"expected_revision": timetable["revision"], "reason": "Reviewed timetable preferences"})


def test_different_timetables_never_change_defaults_or_stale_an_approved_one(client, admin, harness, store):
    before = copy.deepcopy(harness.settings._committed)
    legacy = client.post(DRAFTS, headers=admin, json=body()).json()
    approved = change(client, admin, legacy, "approve")
    assert approved.status_code == 200, approved.text
    first = save(client, admin)
    second = save(client, admin, {"scheduling": {"minimum_break_minutes": 120}, "seating": {"default_percentage": 40}})
    assert first["settings_fingerprint"] != second["settings_fingerprint"]
    assert first["capacity_fingerprint"] != second["capacity_fingerprint"]
    assert harness.settings._committed == before
    assert harness.seating_percentage is None
    assert change(client, admin, approved.json()["timetable"], "publish").status_code == 200
    assert client.get(f"{LIST}/{first['timetable_id']}", headers=admin).json()["inputs"] == first["inputs"]


def test_generate_save_approve_publish_regenerate_preserve_effective_preferences_and_calendar(client, admin, harness):
    preview = client.post(GENERATE, headers=admin, json=body(mode="optimized", calendar=SELECTED, scheduling_preferences=PREFERENCES))
    assert preview.status_code == 200, preview.text
    generated = preview.json()
    assert generated["validation"]["passed"] is True
    assert generated["solver"]["time_limit_seconds"] == 10  # effective override includes the request's lower limit
    draft = save(client, admin, mode="optimized", calendar=SELECTED)
    prefs = draft["inputs"]["scheduling_preferences"]
    assert prefs["settings"]["solver"]["time_limit_seconds"] == 10
    assert prefs["settings"]["optimization"]["s1_weight"] == 40
    assert prefs["seating"]["default_percentage"] == 60
    assert prefs == generated["inputs"]["scheduling_preferences"]
    # A later scheduling-default edit is irrelevant to this timetable's stored configuration.
    asyncio.run(harness.save(scheduling__minimum_break_minutes=180, optimization__s1_weight=7))
    approved = change(client, admin, draft, "approve")
    assert approved.status_code == 200, approved.text
    published = change(client, admin, approved.json()["timetable"], "publish")
    assert published.status_code == 200, published.text
    regenerated = client.post(f"{LIST}/{draft['timetable_id']}/regenerate", headers=admin, json={})
    assert regenerated.status_code == 201, regenerated.text
    assert regenerated.json()["inputs"]["scheduling_preferences"] == prefs
    assert regenerated.json()["inputs"]["start_date"] == SELECTED["start_date"]
    assert regenerated.json()["source_timetable_id"] == draft["timetable_id"]
    assert client.get(f"{LIST}/{draft['timetable_id']}", headers=admin).json()["status"] == "PUBLISHED"


def test_solver_and_independent_validator_receive_identical_effective_snapshot(client, admin, monkeypatch):
    import app.generation.service as generation
    import app.api.timetables as api
    solve_snapshots, validated_snapshots = [], []
    real_solve, real_validate = generation.generate_timetable, generation.validate_timetable

    def solve(snapshot, settings):
        solve_snapshots.append(snapshot)
        assert snapshot.policy.minimum_break_minutes == 90
        assert snapshot.capacities.default_percentage == 60
        assert settings.random_seed == 17
        return real_solve(snapshot, settings)

    def validate(snapshot, timetable):
        validated_snapshots.append(snapshot)
        return real_validate(snapshot, timetable)

    monkeypatch.setattr(generation, "generate_timetable", solve)
    monkeypatch.setattr(generation, "validate_timetable", validate)
    monkeypatch.setattr(api, "validate_timetable", validate)
    save(client, admin)
    assert solve_snapshots and len(validated_snapshots) >= 2
    assert all(snapshot is solve_snapshots[0] for snapshot in validated_snapshots)


@pytest.mark.parametrize("preferences", [
    {"H1": False}, {"scheduling": {"venue_compatibility": {}}},
    {"optimization": {"score_scale": 1}}, {"fairness": {"automatic_relaxation": True}},
    {"seating": {"venue_overrides": {}}}, {"seating": {"default_percentage": 0}},
    {"seating": {"default_percentage": True}}, {"solver": {"time_limit_seconds": 301}},
    {"solver": {"num_search_workers": 17}}, {"solver": {"random_seed": -1}},
    {"scheduling": {"permitted_start_times": []}}, {"scheduling": {"minimum_break_minutes": -1}},
    {"scheduling": {"max_sessions_per_exam": 11}}, {"fairness": {"enabled": "yes"}},
    {"optimization": {"difficulty_weight": 0, "credits_weight": 0, "duration_weight": 0}},
])
def test_invalid_preferences_are_rejected_before_loading_or_saving(client, admin, harness, preferences, store):
    for path in (GENERATE, DRAFTS):
        response = client.post(path, headers=admin, json=body(scheduling_preferences=preferences))
        assert response.status_code == 422, response.text
    assert harness.loads == 0
    assert not store._work.timetables


@pytest.mark.parametrize("role,institution,permissions", [
    ("ACADEMIC_ADMIN", INSTITUTION, None), ("EXAMINATION_ADMIN", OTHER_INSTITUTION, None),
    ("EXAMINATION_ADMIN", INSTITUTION, ["examinations:read"]),
])
def test_preferences_do_not_expand_authorization(client, tokens, identity, harness, role, institution, permissions):
    auth = headers(tokens, identity, role, institution=institution, permissions=permissions)
    for path in (GENERATE, DRAFTS):
        assert client.post(path, headers=auth, json=body(scheduling_preferences=PREFERENCES)).status_code == 403
    assert harness.loads == 0


def test_missing_auth_still_blocks_preferences(client, harness):
    assert client.post(GENERATE, json=body(scheduling_preferences=PREFERENCES)).status_code == 401
    assert harness.loads == 0


@pytest.mark.parametrize("mutation", ["registrations", "venues", "blocks", "modules", "seating"])
def test_custom_preferences_keep_source_staleness_checks(client, admin, harness, mutation):
    draft = save(client, admin)
    from app.sources import DatasetSource
    from test_timetable_generation import DATASET
    data = DATASET
    if mutation == "registrations":
        dropped = next(r for r in data.registrations if r.module_code in body()["module_codes"])
        data = dataclasses.replace(data, registrations=tuple(r for r in data.registrations if r != dropped))
    elif mutation == "venues":
        data = dataclasses.replace(data, venues=tuple(dataclasses.replace(v, normal_capacity=v.normal_capacity + 1) for v in data.venues))
    elif mutation == "modules":
        data = dataclasses.replace(data, modules=tuple(dataclasses.replace(m, difficulty=5 if m.difficulty != 5 else 4) for m in data.modules))
    elif mutation == "blocks":
        data = dataclasses.replace(data, venue_blocks=tuple(dataclasses.replace(b, reason="Changed source block") for b in data.venue_blocks))
        if not data.venue_blocks:
            pytest.skip("fixture has no source blocks")
    else:
        # Reader below changes the real source seating rules, not the timetable override.
        original = harness.generator
        def generator(lock):
            result = original(lock)
            original_loader = result._load_snapshot
            async def loader(*args, **kwargs):
                snapshot = await original_loader(*args, **kwargs)
                capacities = dataclasses.replace(snapshot.capacities, ruleset_version=snapshot.capacities.ruleset_version + 1)
                return dataclasses.replace(snapshot, capacities=capacities)
            result._load_snapshot = loader
            return result
        harness.generator = generator
    harness.source = DatasetSource(data)
    response = change(client, admin, draft, "approve")
    assert response.status_code == 409, response.text
    assert "STALE" in response.text


def test_corrupt_preference_snapshot_cannot_approve_or_regenerate(client, admin, store):
    draft = save(client, admin)
    # In-memory tampering models corruption; the real immutable-content trigger also prevents this update.
    store._work.timetables[0][2]["inputs"]["scheduling_preferences"]["settings"]["scheduling"]["minimum_break_minutes"] = 0
    store._committed = copy.deepcopy(store._work)
    assert change(client, admin, draft, "approve").status_code == 409
    assert client.post(f"{LIST}/{draft['timetable_id']}/regenerate", headers=admin, json={}).status_code == 409


def test_empty_preferences_freeze_defaults_and_omitted_preferences_keep_legacy_behavior(client, admin, harness):
    frozen = save(client, admin, {})
    legacy = client.post(DRAFTS, headers=admin, json=body()).json()
    assert legacy["inputs"]["scheduling_preferences"] is None
    asyncio.run(harness.save(scheduling__minimum_break_minutes=120))
    assert change(client, admin, frozen, "approve").status_code == 200
    assert change(client, admin, legacy, "approve").status_code == 409


@pytest.mark.asyncio
async def test_seating_override_keeps_venue_specific_rules_and_original_source_fingerprint(harness):
    store = MemorySeatingStore()
    source = harness.source
    from app.seating.rules import SeatingRule
    from uuid import uuid4
    first = (await source.list_venues())[0]
    # The memory store's update API is exercised in the existing seating suites; a pure snapshot is sufficient here.
    original_list = store.list_rules
    async def rules():
        return [*(await original_list()), SeatingRule(2, "venue", first.venue_id, 80, 1, "Venue override", uuid4(), dt.datetime.now(dt.UTC))]
    store.list_rules = rules
    original = await load_capacity_snapshot(source, store)
    effective = await load_capacity_snapshot(source, store, preferences={"default_percentage": 35})
    assert effective.source_fingerprint == original.fingerprint
    assert next(v for v in effective.venues if v.venue_id == first.venue_id).percentage == 80
    assert all(v.percentage == 35 for v in effective.venues if v.rule_scope == "default")


def test_snapshots_fail_closed_and_legacy_absence_remains_supported():
    assert saved_preferences({}) is None
    with pytest.raises(ValueError):
        saved_preferences({"scheduling_preferences": {"format": "future"}})
    original = SchedulingConfiguration()
    effective = effective_configuration(original, PREFERENCES)
    assert original.scheduling.minimum_break_minutes == 60
    assert effective.scheduling.minimum_break_minutes == 90


def test_zero_soft_weights_remain_supported_without_changing_formulas(client, admin):
    result = save(client, admin, {"optimization": {f"s{i}_weight": 0 for i in range(1, 6)}})
    assert result["workload"]["total_weighted_penalty"] == 0


def test_a_timetable_can_raise_the_default_solver_limit_within_api_caps(client, admin):
    result = save(client, admin, {"solver": {"time_limit_seconds": 120}}, time_limit_seconds=None)
    assert result["solver"]["time_limit_seconds"] == 120


def test_partial_thresholds_are_validated_against_actual_institution_defaults(client, admin, harness):
    asyncio.run(harness.save(optimization__preparation_target_minutes=4000, optimization__difficult_target_minutes=6000))
    result = save(client, admin, {"optimization": {"short_gap_minutes": 3000}})
    effective = result["inputs"]["scheduling_preferences"]["settings"]["optimization"]
    assert (effective["short_gap_minutes"], effective["preparation_target_minutes"], effective["difficult_target_minutes"]) == (3000, 4000, 6000)
    refused = client.post(GENERATE, headers=admin, json=body(scheduling_preferences={"optimization": {"short_gap_minutes": 5000}}))
    assert refused.status_code == 422


def test_custom_preferences_keep_revision_permissions_and_post_approval_source_checks(client, admin, tokens, identity, harness):
    draft = save(client, admin)
    path = f"{LIST}/{draft['timetable_id']}/approve"
    payload = {"expected_revision": draft["revision"], "reason": "Review with timetable preferences"}
    read_only = headers(tokens, identity, "EXAMINATION_ADMIN", permissions=["examinations:read"])
    assert client.post(path, headers=read_only, json=payload).status_code == 403
    assert client.post(path, headers=admin, json={**payload, "expected_revision": 99}).status_code == 409
    approved = change(client, admin, draft, "approve").json()["timetable"]
    from test_timetable_generation import DATASET
    from app.sources import DatasetSource
    altered = dataclasses.replace(DATASET, venues=tuple(dataclasses.replace(v, normal_capacity=v.normal_capacity + 1) for v in DATASET.venues))
    harness.source = DatasetSource(altered)
    assert change(client, admin, approved, "publish").status_code == 409


@pytest.mark.asyncio
async def test_legacy_input_fingerprint_is_byte_compatible(harness):
    import hashlib
    import json
    from app.scheduling.inputs import load_snapshot
    snapshot = await load_snapshot(harness.source, MemorySeatingStore(), period_code=body()["period_code"],
                                   module_codes=body()["module_codes"])
    content = {name: dataclasses.asdict(getattr(snapshot, name)) if name in ("period", "capacities", "policy")
               else [dataclasses.asdict(row) for row in getattr(snapshot, name)]
               for name in ("period", "modules", "students", "registrations", "venues", "blocks", "capacities", "policy")}
    content["capacities"].pop("taken_at")
    content["capacities"].pop("source_fingerprint")
    expected = hashlib.sha256(json.dumps(content, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()
    assert snapshot.fingerprint == expected
