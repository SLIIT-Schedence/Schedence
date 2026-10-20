"""Explicit synthetic staff candidates; no real preferences or staff data."""

from dataclasses import replace

import pytest
from ortools.sat.python import cp_model
from pydantic import ValidationError

from schedence_teaching.preparation import PreparationError, prepare_timetable_input
from schedence_teaching.schemas import SessionType, TeachingTimetableInput
from schedence_teaching.solver import (
    ScheduledSession, SolverConfig, SolverStatus, TimetableValidationError,
    solve_timetable, validate_timetable,
)


def demand(pools, requirements=None, *, staff_types=None, streams=None, extra_staff=(), parents=None):
    """Pools supply (staff ID, role); requirements supply (offering, group, count)."""
    staff_types, streams, parents = staff_types or {}, streams or {}, parents or {}
    requirements = requirements if requirements is not None else [
        (offering, f"GROUP-{offering}", 1) for offering in pools
    ]
    staff_ids = sorted({staff for members in pools.values() for staff, _ in members} | set(extra_staff))
    group_streams = {group: streams.get(offering, "WEEKDAY") for offering, group, _ in requirements}
    payload = {
        "staff_members": [{
            "staff_id": staff, "staff_name": f"Synthetic {index}", "position": "Synthetic",
            "staff_type": staff_types.get(staff, "ACADEMIC"),
        } for index, staff in enumerate(staff_ids)],
        "module_offerings": [{
            "offering_id": offering, "module_code": "SYNTHETIC-SHARED-CODE",
            "module_name": f"Synthetic {offering}", "academic_year": 2, "semester": 1,
            "specialization": "Synthetic", "degree_type": "Synthetic",
            "stream": streams.get(offering, "WEEKDAY"),
        } for offering in pools],
        "teaching_team_members": [{"offering_id": offering, "staff_id": staff, "role": role}
                                  for offering, members in pools.items() for staff, role in members],
        "student_groups": [{
            "group_id": group, "academic_year": 2, "semester": 1, "stream": stream,
            "student_count": 25, "parent_group_id": parents.get(group),
        } for group, stream in group_streams.items()],
        "session_requirements": [{
            "requirement_id": f"REQ{index}", "offering_id": offering, "group_id": group,
            "session_type": "LECTURE", "duration_minutes": 30, "sessions_per_week": count,
        } for index, (offering, group, count) in enumerate(requirements, 1)],
    }
    return prepare_timetable_input(TeachingTimetableInput.model_validate(payload))


def windows(minutes=60, *, both_streams=False, days=("DAY",), limit=5):
    streams = ("WEEKDAY", "WEEKEND") if both_streams else ("WEEKDAY",)
    return SolverConfig(
        days=[{"day": day, "windows": {
            stream: [{"start_minute": 0, "end_minute": minutes}] for stream in streams
        }} for day in days], granularity_minutes=15, max_time_seconds=limit, random_seed=7,
    )


def assert_complete(prepared, config, result):
    assert result.status in {SolverStatus.OPTIMAL, SolverStatus.FEASIBLE}
    summary = validate_timetable(prepared, config, result.schedule)
    assert summary.expected_occurrences == summary.assigned_occurrences == len(prepared.sessions)
    assert {"assigned_staff_exists", "offering_staff_eligibility", "staff_clashes"} <= set(summary.checks)
    assert result.validation == summary
    assert result.to_dict()["university_ready"] is False


def test_multiple_staff_can_teach_unrelated_groups_simultaneously():
    prepared = demand({"A": [("LEC001", "LIC")], "B": [("LEC002", "CO_LIC")]})
    config = windows(30)
    result = solve_timetable(prepared, config)
    assert_complete(prepared, config, result)
    assert {item.assigned_staff_id for item in result.schedule} == {"LEC001", "LEC002"}
    assert {item.start_minute for item in result.schedule} == {0}


def test_one_staff_teaches_adjacent_sessions_across_distinct_offerings_with_same_code():
    prepared = demand({"A": [("LEC001", "LIC")], "B": [("LEC001", "CO_LIC")]})
    config = windows()
    result = solve_timetable(prepared, config)
    assert_complete(prepared, config, result)
    assert {item.assigned_staff_id for item in result.schedule} == {"LEC001"}
    assert sorted((item.start_minute, item.end_minute) for item in result.schedule) == [(0, 30), (30, 60)]
    assert {item.session.offering_id for item in result.schedule} == {"A", "B"}


def test_one_staff_can_teach_multiple_occurrences_on_different_days():
    prepared = demand({"A": [("LEC001", "LIC")]}, [("A", "G", 2)])
    config = windows(30, days=("D1", "D2"))
    result = solve_timetable(prepared, config)
    assert_complete(prepared, config, result)
    assert {item.day for item in result.schedule} == {"D1", "D2"}


@pytest.mark.parametrize("staff_type", ["ACADEMIC", "INSTRUCTOR"])
def test_forced_shared_staff_clash_is_infeasible_even_for_unrelated_groups(staff_type):
    prepared = demand({"A": [("STAFF", "TEAM_MEMBER")], "B": [("STAFF", "TEAM_MEMBER")]},
                      staff_types={"STAFF": staff_type})
    result = solve_timetable(prepared, windows(30))
    assert result.status is SolverStatus.INFEASIBLE
    assert result.schedule == () and result.validation is None and result.assigned_session_count == 0


def test_joint_candidate_selection_uses_alternative_to_make_timetable_feasible():
    prepared = demand({"A": [("LEC001", "LIC"), ("LEC002", "CO_LIC")],
                       "B": [("LEC001", "LIC")]})
    original = prepared.input_snapshot.model_dump(round_trip=True)
    config = windows(30)
    result = solve_timetable(prepared, config)
    assert_complete(prepared, config, result)
    assert {item.session.offering_id: item.assigned_staff_id for item in result.schedule} == {
        "A": "LEC002", "B": "LEC001",
    }
    assert prepared.input_snapshot.model_dump(round_trip=True) == original


def test_three_sessions_exceed_two_staff_capacity():
    members = [("LEC001", "LIC"), ("LEC002", "CO_LIC")]
    prepared = demand({offering: members for offering in ("A", "B", "C")})
    result = solve_timetable(prepared, windows(30))
    assert result.status is SolverStatus.INFEASIBLE and not result.schedule and result.validation is None


@pytest.mark.parametrize("role,staff_type", [
    ("LIC", "ACADEMIC"), ("CO_LIC", "ACADEMIC"),
    ("TEAM_MEMBER", "ACADEMIC"), ("TEAM_MEMBER", "INSTRUCTOR"),
])
@pytest.mark.parametrize("session_type", ["LECTURE", "TUTORIAL", "WORKSHOP", "LAB"])
def test_every_explicit_candidate_role_can_teach_any_type_without_invented_policy(role, staff_type, session_type):
    prepared = demand({"A": [("SOURCE-ID", role)]}, staff_types={"SOURCE-ID": staff_type})
    inputs = prepared.input_snapshot.model_copy(deep=True)
    inputs.session_requirements[0].session_type = SessionType(session_type)
    prepared = prepare_timetable_input(inputs)
    result = solve_timetable(prepared, windows(30))
    assert_complete(prepared, windows(30), result)
    assert result.schedule[0].assigned_staff_id == "SOURCE-ID"


@pytest.mark.parametrize("role", ["LIC", "CO_LIC"])
def test_existing_contract_rejects_instructor_in_academic_only_role(role):
    with pytest.raises(ValidationError, match="require ACADEMIC"):
        demand({"A": [("INSTRUCTOR-ID", role)]}, staff_types={"INSTRUCTOR-ID": "INSTRUCTOR"})


def test_shared_staff_is_protected_across_weekday_weekend_streams():
    prepared = demand({"A": [("LEC001", "LIC")], "B": [("LEC001", "LIC")]},
                      streams={"B": "WEEKEND"})
    tight = solve_timetable(prepared, windows(30, both_streams=True))
    assert tight.status is SolverStatus.INFEASIBLE and tight.schedule == ()
    config = windows(60, both_streams=True)
    feasible = solve_timetable(prepared, config)
    assert_complete(prepared, config, feasible)
    assert {item.start_minute for item in feasible.schedule} == {0, 30}


def test_empty_candidate_pool_reports_occurrence_and_emits_no_partial_schedule():
    prepared = demand({"A": [("LEC001", "LIC")], "B": []})
    result = solve_timetable(prepared, windows())
    assert result.status is SolverStatus.INFEASIBLE and result.schedule == () and result.validation is None
    assert "REQ2:1" in result.failure_reason and "offering B" in result.failure_reason
    assert "no eligible teaching staff" in result.failure_reason


def test_unused_offering_without_candidates_does_not_block_other_demand():
    prepared = demand({"A": [("LEC001", "LIC")], "UNUSED": []}, [("A", "G", 1)])
    assert_complete(prepared, windows(), solve_timetable(prepared, windows()))


def test_missing_staff_reference_is_rejected_before_solving():
    prepared = demand({"A": [("LEC001", "LIC")]})
    prepared.input_snapshot.staff_members.clear()
    with pytest.raises(PreparationError, match="unknown staff_id"):
        solve_timetable(prepared, windows())


def test_missing_offering_reference_is_rejected_before_solving():
    prepared = demand({"A": [("LEC001", "LIC")]})
    prepared.input_snapshot.teaching_team_members[0].offering_id = "ABSENT"
    with pytest.raises(PreparationError, match="unknown offering_id"):
        solve_timetable(prepared, windows())


def test_multiple_roles_same_staff_produce_one_choice_without_modifying_memberships():
    prepared = demand({"A": [("LEC001", "LIC"), ("LEC001", "TEAM_MEMBER"), ("LEC002", "CO_LIC")]})
    original = prepared.input_snapshot.model_dump(round_trip=True)
    result = solve_timetable(prepared, windows())
    assert_complete(prepared, windows(), result)
    assert len(result.schedule) == 1 and result.schedule[0].assigned_staff_id in {"LEC001", "LEC002"}
    assert prepared.input_snapshot.model_dump(round_trip=True) == original


def test_staff_source_ids_are_preserved_exactly():
    prepared = demand({"A": [("  Instructor-Source-09  ", "TEAM_MEMBER")]},
                      staff_types={"  Instructor-Source-09  ": "INSTRUCTOR"})
    result = solve_timetable(prepared, windows())
    assert result.schedule[0].assigned_staff_id == "  Instructor-Source-09  "
    assert result.to_dict()["schedule"][0]["assigned_staff_id"] == "  Instructor-Source-09  "


def test_candidate_source_order_does_not_change_fixed_seed_result():
    prepared = demand({"A": [("LEC001", "LIC"), ("LEC002", "CO_LIC")]}, [("A", "G", 2)])
    reordered = prepared.input_snapshot.model_copy(deep=True)
    reordered.teaching_team_members.reverse()
    reordered.staff_members.reverse()
    assert solve_timetable(prepared, windows()).schedule == solve_timetable(prepare_timetable_input(reordered), windows()).schedule


@pytest.mark.parametrize("staff_id,code", [
    ("ABSENT", "unknown_staff"), ("OUTSIDE", "ineligible_staff"),
    (None, "invalid_staff_id"), (False, "invalid_staff_id"),
    (123, "invalid_staff_id"), ([], "invalid_staff_id"), ("", "invalid_staff_id"), ("  ", "invalid_staff_id"),
])
def test_independent_validator_rejects_unknown_ineligible_or_malformed_staff(staff_id, code):
    prepared = demand({"A": [("LEC001", "LIC")]}, extra_staff=("OUTSIDE",))
    schedule = [ScheduledSession(prepared.sessions[0], "DAY", 0, 30, staff_id)]
    with pytest.raises(TimetableValidationError) as error:
        validate_timetable(prepared, windows(), schedule)
    assert code in {issue.code for issue in error.value.issues}


@pytest.mark.parametrize("cross_stream", [False, True])
def test_validator_detects_corrupt_shared_staff_overlap_across_offerings(cross_stream):
    prepared = demand({"A": [("LEC001", "LIC")], "B": [("LEC001", "LIC")]},
                      streams={"B": "WEEKEND"} if cross_stream else {})
    schedule = [ScheduledSession(session, "DAY", 0, 30, "LEC001") for session in prepared.sessions]
    with pytest.raises(TimetableValidationError) as error:
        validate_timetable(prepared, windows(both_streams=True), schedule)
    assert {issue.code for issue in error.value.issues} == {"staff_clash"}


def test_validator_accepts_touching_staff_intervals_and_same_time_on_different_days():
    prepared = demand({"A": [("LEC001", "LIC")]}, [("A", "G", 3)])
    schedule = [ScheduledSession(session, day, start, start + 30, "LEC001")
                for session, day, start in zip(prepared.sessions, ("D1", "D1", "D2"), (0, 30, 0))]
    assert validate_timetable(prepared, windows(days=("D1", "D2")), schedule).assigned_occurrences == 3


def test_validator_uses_original_offering_for_eligibility_when_metadata_is_forged():
    prepared = demand({"A": [("LEC001", "LIC")], "B": [("LEC002", "LIC")]}, [("A", "G", 1)])
    forged = replace(prepared.sessions[0], offering_id="B")
    with pytest.raises(TimetableValidationError) as error:
        validate_timetable(prepared, windows(), [ScheduledSession(forged, "DAY", 0, 30, "LEC002")])
    assert {issue.code for issue in error.value.issues} == {"metadata_changed", "ineligible_staff"}


def test_independent_validator_rejects_bad_model_candidate_pool(monkeypatch):
    from schedence_teaching.solver import engine
    prepared = demand({"A": [("LEC001", "LIC")]}, extra_staff=("OUTSIDE",))
    monkeypatch.setattr(engine, "candidate_pools", lambda inputs: {"A": ("OUTSIDE",)})
    with pytest.raises(TimetableValidationError, match="ineligible_staff"):
        solve_timetable(prepared, windows())


def test_multiple_candidates_do_not_remove_parent_child_conflict():
    prepared = demand({"A": [("LEC001", "LIC"), ("LEC002", "CO_LIC")]},
                      [("A", "MAIN", 1), ("A", "CHILD", 1)], parents={"CHILD": "MAIN"})
    assert solve_timetable(prepared, windows(30)).status is SolverStatus.INFEASIBLE


def test_staff_timeout_emits_no_partial_assignments():
    prepared = demand({"A": [("LEC001", "LIC"), ("LEC002", "CO_LIC")]}, [("A", "G", 3)])
    result = solve_timetable(prepared, windows(120, limit=1e-12))
    assert result.status is SolverStatus.UNKNOWN and result.schedule == () and result.validation is None


@pytest.mark.parametrize("native_status", [cp_model.INFEASIBLE, cp_model.UNKNOWN, cp_model.MODEL_INVALID])
def test_non_success_status_never_reads_or_returns_staff_choices(monkeypatch, native_status):
    monkeypatch.setattr(cp_model.CpSolver, "solve", lambda self, model: native_status)
    def forbidden_value(*args):
        pytest.fail("Failed solve must not decode assignments.")
    monkeypatch.setattr(cp_model.CpSolver, "value", forbidden_value)
    result = solve_timetable(demand({"A": [("LEC001", "LIC")]}), windows())
    assert result.schedule == () and result.validation is None and result.failure_reason


def test_serialized_output_contains_one_responsible_staff_and_all_original_metadata():
    prepared = demand({"A": [("LEC001", "LIC")]})
    result = solve_timetable(prepared, windows())
    output = result.to_dict()["schedule"][0]
    assert {key: output[key] for key in prepared.sessions[0].to_dict()} == prepared.sessions[0].to_dict()
    assert output["assigned_staff_id"] == "LEC001"
    assert output["day"] == "DAY" and output["start_time"] == "00:00" and output["end_time"] == "00:30"
    assert any("multiple simultaneous staff" in text for text in result.to_dict()["limitations"])
