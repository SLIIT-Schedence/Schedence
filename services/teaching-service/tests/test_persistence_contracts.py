"""Synthetic persistence envelopes and actual independent solver validation."""

from dataclasses import replace
from datetime import date
import json
from uuid import UUID

import pytest
from pydantic import ValidationError

from schedence_teaching.persistence.contracts import SchedulingContexts, SnapshotDocument
from schedence_teaching.persistence.snapshots import (canonical_json, content_digest, prepare_attempt,
    replay_snapshot, restore_snapshot, snapshot_payload)
from schedence_teaching.solver import SolverResult, SolverStatus, solve_timetable

from test_online_attendance_solver import problem

TENANT = UUID("00000000-0000-4000-8000-000000000001")
OTHER_TENANT = UUID("00000000-0000-4000-8000-000000000002")


def snapshot_for(prepared, config, *, contexts=None, specializations=None, calendar=None):
    groups = prepared.input_snapshot.student_groups
    specializations = specializations or {group.group_id: "IT" for group in groups}
    scope_groups = {}
    for group in groups:
        key = (group.academic_year, group.semester, group.stream, specializations[group.group_id])
        scope_groups.setdefault(key, []).append(group.group_id)
    return SnapshotDocument(institution_id=TENANT, academic_term_reference="SYNTHETIC-TERM",
        timezone_name="Asia/Colombo", calendar_label="PP1_EXPERIMENTAL_CALENDAR",
        calendar=calendar or dict(week_reference="SYNTHETIC-WEEK", week_start=date(2026,10,19), week_end=date(2026,10,25),
            day_dates={day.day: date(2026,10,19 + index) for index, day in enumerate(config.days)}, synthetic_data=True),
        teaching_input=prepared.input_snapshot, solver_config=config, contexts=contexts or {},
        group_specializations=specializations,
        scopes=[dict(scope_key=f"SYNTHETIC-{index}", academic_year=key[0], semester=key[1], stream=key[2],
                     specialization=key[3], scope_status="SUPPORTED", group_references=references)
                for index, (key, references) in enumerate(scope_groups.items())],
        sources=[dict(label="Synthetic fixture", sha256="a"*64, owner="TEACHING_SERVICE",
                      provenance_kind="SYNTHETIC_PP1", provenance={"seed": 7})],
        issues=[dict(issue_code="RESEARCH_PENDING_SESSION_MAPPING", severity="BLOCKER",
                     details={"compulsory": True, "session_hours": None})])


def example(mode="PHYSICAL"):
    prepared, config, venues = problem(mode=mode)
    snapshot = snapshot_for(prepared, config, contexts={"venue_input": venues})
    result = solve_timetable(prepared, config, venue_input=venues)
    return snapshot, result


@pytest.mark.parametrize("mode", ["PHYSICAL", "ONLINE"])
def test_round_trip_replays_original_contracts_and_validates_actual_schedule(mode):
    snapshot, result = example(mode)
    restored = SnapshotDocument.model_validate_json(canonical_json(snapshot))
    assert restored == snapshot
    assert content_digest(restored) == content_digest(snapshot)
    plan = prepare_attempt(restored, result)
    assert plan.success and plan.expected_occurrences == plan.scheduled_occurrences == 2
    bookings = json.loads(plan.deliveries_json)
    assert len(bookings) == 2
    assert {row["delivery_mode"] for row in bookings} == {mode}
    assert all(row["venue_reference"] is None for row in bookings) is (mode == "ONLINE")
    assert {row["coverage"][0]["occurrence_reference"] for row in bookings} == {"R-A:1", "R-B:1"}
    assert json.loads(plan.validation_json)["assessment"] == "PASSED"


def test_source_ids_duplicate_module_codes_and_exact_text_are_not_replaced():
    snapshot, _ = example()
    snapshot.teaching_input.staff_members[0].staff_id = "LEC001"
    snapshot.teaching_input.teaching_team_members[0].staff_id = "LEC001"
    payload = json.loads(canonical_json(snapshot))
    payload["external_references"] = [dict(owner="ACADEMIC_DATA_SERVICE", record_kind="staff",
        local_reference="LEC001", authoritative_id=None, source_reference="staff_number")]
    # Validate the mutated payload; source codes are intentionally not unique.
    document = SnapshotDocument.model_validate_json(canonical_json(payload))
    prepared = replay_snapshot(document)[1]
    assert [row.module_code for row in prepared.input_snapshot.module_offerings] == ["SYN", "SYN"]
    assert prepared.input_snapshot.staff_members[0].staff_id == "LEC001"
    assert document.external_references[0].authoritative_id is None
    assert canonical_json({"id": " LEC001 "}) == '{"id":" LEC001 "}'


def test_mixed_provenance_and_blocked_scope_survive_serialization():
    snapshot, _ = example()
    payload = json.loads(canonical_json(snapshot))
    kinds = ["UNIVERSITY_SOURCED", "APPROVED_PP1_ESTIMATE", "SYNTHETIC_PP1",
             "EXPERIMENTAL_CONFIGURATION", "USER_CONFIRMED_CORRECTION", "SERVICE_EXPORT"]
    payload["sources"] = [dict(label=f"Synthetic example {kind}", sha256=str(index)*64,
        owner="ACADEMIC_DATA_SERVICE", provenance_kind=kind,
        provenance={"original_status": "VERIFY", "updated_confirmation": "USER_CONFIRMED", "source_row": 12})
        for index, kind in enumerate(kinds)]
    payload["scopes"].append(dict(scope_key="DS-BLOCKED", academic_year=4,semester=1,stream="WEEKEND",
        specialization="DS",student_count=7,scope_status="BLOCKED_MISSING_ACADEMIC_MAPPING",group_references=[]))
    payload["issues"].append(dict(issue_code="BLOCKED_MISSING_ACADEMIC_MAPPING",scope_key="DS-BLOCKED",
        severity="BLOCKER",details={"allocated_students": 7}))
    restored = SnapshotDocument.model_validate_json(canonical_json(payload))
    assert len(restored.sources) == 6 and restored.scopes[-1].student_count == 7
    assert restored.sources[0].provenance["original_status"] == "VERIFY"
    assert restored.issues[0].details["session_hours"] is None


@pytest.mark.parametrize("change", ["tenant", "timezone", "dates", "source_hash", "source_duplicate", "scope_duplicate",
    "unknown_group", "duplicate_group", "year", "semester", "stream", "specialization", "uncovered", "missing_specialization",
    "unknown_issue_scope", "blocked_groups", "empty_supported"])
def test_invalid_snapshot_metadata_is_rejected(change):
    payload = json.loads(canonical_json(example()[0]))
    scope = payload["scopes"][0]
    if change == "tenant": payload["institution_id"] = str(UUID(int=0))
    elif change == "timezone": payload["timezone_name"] = "No/Such_Zone"
    elif change == "dates": payload["calendar"]["day_dates"] = {}
    elif change == "source_hash": payload["sources"][0]["sha256"] = "invalid"
    elif change == "source_duplicate": payload["sources"] *= 2
    elif change == "scope_duplicate": payload["scopes"] *= 2
    elif change == "unknown_group": scope["group_references"].append("NO-SUCH-GROUP")
    elif change == "duplicate_group": scope["group_references"] *= 2
    elif change == "year": scope["academic_year"] = 3
    elif change == "semester": scope["semester"] = 2
    elif change == "stream": scope["stream"] = "WEEKDAY"
    elif change == "specialization": scope["specialization"] = "OTHER"
    elif change == "uncovered": scope["group_references"].pop()
    elif change == "missing_specialization": payload["group_specializations"].pop("A")
    elif change == "unknown_issue_scope": payload["issues"][0]["scope_key"] = "UNKNOWN"
    elif change == "blocked_groups": scope["scope_status"] = "BLOCKED_MISSING_ACADEMIC_MAPPING"
    elif change == "empty_supported": scope["group_references"] = []
    with pytest.raises(ValidationError):
        SnapshotDocument.model_validate_json(canonical_json(payload))


@pytest.mark.parametrize("name", ["combination_config", "work_period_config", "preference_config",
                                  "fairness_config", "consecutive_config", "student_lunch_config"])
def test_optional_features_cannot_lose_original_context(name):
    from schedence_teaching import solver
    types = dict(combination_config=solver.CombinationSolverConfig, work_period_config=solver.WorkPeriodSolverConfig,
        preference_config=solver.PreferenceOptimizationConfig, fairness_config=solver.FairnessOptimizationConfig,
        consecutive_config=solver.ConsecutiveOptimizationConfig, student_lunch_config=solver.StudentLunchEnforcementConfig)
    with pytest.raises(ValidationError):
        SchedulingContexts.model_validate({name: types[name]()})


def test_canonical_digest_is_stable_for_unordered_values_but_preserves_array_order():
    assert canonical_json({"b": {"z", "a"}, "a": 1}) == canonical_json({"a": 1, "b": frozenset(["a", "z"])})
    assert content_digest(["a", "b"]) != content_digest(["b", "a"])
    assert content_digest({"id": "LEC001"}) != content_digest({"id": " LEC001 "})
    with pytest.raises(ValueError): canonical_json({"runtime": float("nan")})


def test_nested_mutation_cannot_bypass_existing_input_validation():
    snapshot, _ = example()
    snapshot.teaching_input.student_groups[0].student_count = 0
    with pytest.raises(ValidationError): replay_snapshot(snapshot)


@pytest.mark.parametrize("change", ["missing", "duplicate", "staff", "time", "venue", "validation", "count", "runtime"])
def test_successful_status_is_insufficient_for_persistence(change):
    snapshot, result = example()
    if change == "missing": result = replace(result, schedule=result.schedule[:-1])
    elif change == "duplicate": result = replace(result, schedule=result.schedule[:1]*2)
    elif change == "staff": result = replace(result, schedule=(replace(result.schedule[0], assigned_staff_id="UNKNOWN"), result.schedule[1]))
    elif change == "time": result = replace(result, schedule=(replace(result.schedule[0], end_minute=571), result.schedule[1]))
    elif change == "venue": result = replace(result, schedule=(replace(result.schedule[0], assigned_venue_id=None), result.schedule[1]))
    elif change == "validation": result = replace(result, validation=None)
    elif change == "count": result = replace(result, original_occurrence_count=1)
    elif change == "runtime": result = replace(result, runtime_seconds=float("inf"))
    with pytest.raises(ValueError): prepare_attempt(snapshot, result)


@pytest.mark.parametrize("status", [SolverStatus.UNKNOWN, SolverStatus.INFEASIBLE, SolverStatus.MODEL_INVALID])
def test_unsuccessful_attempts_are_preserved_without_timetable(status):
    snapshot, _ = example()
    result = SolverResult(status=status, schedule=(), runtime_seconds=0.1, validation=None, original_occurrence_count=2)
    # Simulate JSONB key reordering in storage; the replay string remains exact.
    stored_payload = json.loads(canonical_json(snapshot_payload(snapshot)))
    restored = restore_snapshot(stored_payload)
    assert snapshot.calendar.model_dump_json() == restored.calendar.model_dump_json()
    plan = prepare_attempt(restored, result)
    assert not plan.success and plan.scheduled_occurrences == 0
    assert json.loads(plan.deliveries_json) == []
    assert json.loads(plan.validation_json)["assessment"] == "NOT_ASSESSED"


def test_unsuccessful_attempt_cannot_claim_schedule():
    snapshot, result = example()
    with pytest.raises(ValueError): prepare_attempt(snapshot, replace(result, status=SolverStatus.UNKNOWN))


@pytest.mark.parametrize("mixed", [False, True])
def test_combined_and_mixed_delivery_preserve_coverage_without_duplicate_bookings(mixed):
    from test_combination_solver import inputs, venues
    from schedence_teaching.preparation import prepare_group_combinations
    from schedence_teaching.solver import CombinationSolverConfig
    from test_staff_solver import windows
    source = inputs()
    combined = prepare_group_combinations(source)
    config, rooms, options = windows(120), venues(large_minutes=30) if mixed else venues(), CombinationSolverConfig()
    snapshot = snapshot_for(combined.prepared, config, specializations=source.group_specializations,
        contexts=dict(venue_input=rooms, combination_input=source, combination_config=options))
    result = solve_timetable(combined.prepared, config, venue_input=rooms,
        combination_input=combined, combination_config=options)
    plan = prepare_attempt(snapshot, result)
    rows = json.loads(plan.deliveries_json)
    assert len(rows) == (3 if mixed else 2)
    covered = [item["occurrence_reference"] for row in rows for item in row["coverage"]]
    assert len(covered) == len(set(covered)) == 4
    assert sum(row["end_minute"]-row["start_minute"] for row in rows) == (90 if mixed else 60)
    assert sorted(row["student_count"] for row in rows) == ([20,20,40] if mixed else [40,40])


def test_all_existing_joint_policies_replay_without_losing_protection_or_objectives():
    from test_scheduling_integration import build_case, run_case
    case = build_case(administrative_mode="flexible", time_limit=10.0)
    original = case["inputs"]
    raw = {key: value for key, value in original.items() if key in SchedulingContexts.model_fields}
    raw.update(combination_input=original["combination_input"].input_snapshot,
        work_period_input=original["work_periods"].input_snapshot,
        preference_input=original["preference_input"].input_snapshot)
    from schedence_teaching.solver import CombinationSolverConfig
    raw.setdefault("combination_config", CombinationSolverConfig())
    snapshot = snapshot_for(case["prepared"], case["config"], contexts=raw,
                            calendar=original["consecutive_calendar"])
    result, _ = run_case(case)
    # Retain exact model JSON despite JSONB's non-preservation of object order.
    restored = restore_snapshot(json.loads(canonical_json(snapshot_payload(snapshot))))
    assert snapshot.calendar.model_dump_json() == restored.calendar.model_dump_json()
    plan = prepare_attempt(restored, result)
    assert plan.scheduled_occurrences == plan.expected_occurrences == 7
    evidence = json.loads(plan.result_json)
    assert evidence["staff_policy_enabled"] and evidence["work_period_enabled"]
    assert evidence["fairness_optimization_enabled"] and evidence["consecutive_optimization_enabled"]
    assert len(evidence["administrative_schedule"]) == 1
    assert evidence["work_period_report"] is not None and evidence["workload_assessment"] is not None
    assert len(json.loads(plan.deliveries_json)) == 6
    # Calendar dates are persistence metadata, so they must agree with the hard
    # staff and protection checks rather than merely relabeling abstract days.
    snapshot.calendar.day_dates = {key: date(2026, 10, 19 + i) for i, key in enumerate(snapshot.calendar.day_dates)}
    with pytest.raises(ValidationError): replay_snapshot(snapshot)


def test_confirmed_lunch_context_is_revalidated_and_cannot_be_dropped():
    from test_student_lunch_solver import context
    snapshot, _ = example(mode="ONLINE")
    lunch = context(snapshot.calendar)
    snapshot.contexts = SchedulingContexts(venue_input=snapshot.contexts.venue_input,
        student_lunch_policy=lunch["student_lunch_policy"], student_lunch_config=lunch["student_lunch_config"])
    document, prepared, config, inputs = replay_snapshot(snapshot)
    result = solve_timetable(prepared, config, **inputs)
    plan = prepare_attempt(document, result)
    assert json.loads(plan.result_json)["student_lunch_enabled"]
    snapshot.contexts = SchedulingContexts(venue_input=snapshot.contexts.venue_input)
    with pytest.raises(ValueError): prepare_attempt(snapshot, result)
