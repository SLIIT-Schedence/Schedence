"""Synthetic physical scheduling; no inferred campus hours or equipment."""

from dataclasses import replace
import json
from pathlib import Path

import pytest
from ortools.sat.python import cp_model
from pydantic import ValidationError

from schedence_teaching.preparation import prepare_timetable_input
from schedence_teaching.solver import (
    SolverInputError, SolverStatus, TimetableValidationError, VenueSchedulingInput,
    solve_timetable, validate_timetable, venues_from_import,
)

from test_staff_solver import demand, windows
from test_venue_import import dataset, read  # Reuse the sanitized Step 7A fixture.


def physical(rooms=None, *, rules=(), combined=(), shared=(), unresolved=(), features=None):
    rooms = rooms if rooms is not None else [("R1", 30, "Lecture Hall")]
    return VenueSchedulingInput(
        venues=[{"venue_id": identity, "venue_name": f"Synthetic {identity}", "capacity": capacity,
                 "venue_type": kind, "building": "Synthetic", "floor": 1, "lab_specialization": "General"}
                for identity, capacity, kind in rooms],
        unresolved_venue_ids=unresolved,
        calendars=[{"venue_id": identity, "available_windows": {"DAY": [{"start_minute": 0, "end_minute": 60}]},
                    "blocked_periods": []} for identity, _, _ in rooms],
        requirements=rules, combined_venues=combined, shared_resources=shared, venue_features=features or {},
    )


def one_session():
    return demand({"A": [("LEC001", "LIC")]})


def separate_students_staff():
    return demand({"A": [("LEC001", "LIC")], "B": [("LEC002", "CO_LIC")]})


def complete(prepared, config, venues, result):
    assert result.status in {SolverStatus.OPTIMAL, SolverStatus.FEASIBLE}
    assert result.venue_enabled and result.to_dict()["venue_enabled"]
    assert len(result.schedule) == len(prepared.sessions)
    assert all(item.assigned_venue_id and item.assigned_staff_id for item in result.schedule)
    summary = validate_timetable(prepared, config, result.schedule, venue_input=venues)
    assert summary.venue_enabled and summary == result.validation
    assert {"venue_capacity", "venue_type_and_features", "venue_availability", "venue_blocked_periods",
            "venue_clashes", "shared_space_clashes", "staff_clashes"} <= set(summary.checks)
    assert result.to_dict()["university_ready"] is False


def failed(result):
    assert result.status is SolverStatus.INFEASIBLE
    assert result.schedule == () and result.validation is None and result.assigned_session_count == 0
    assert result.venue_enabled and result.to_dict()["venue_enabled"] and result.failure_reason


def test_successful_physical_allocation_preserves_output_metadata():
    prepared, config, venues = one_session(), windows(), physical()
    original = venues.model_dump(round_trip=True)
    result = solve_timetable(prepared, config, venue_input=venues)
    complete(prepared, config, venues, result)
    assert result.schedule[0].assigned_venue_id == "R1"
    output = result.to_dict()["schedule"][0]
    assert {key: output[key] for key in prepared.sessions[0].to_dict()} == prepared.sessions[0].to_dict()
    assert output["assigned_staff_id"] == "LEC001" and output["day"] == "DAY"
    assert output["end_minute"] - output["start_minute"] == 30
    assert venues.model_dump(round_trip=True) == original


def test_multiple_eligible_rooms_can_host_concurrent_unrelated_groups():
    prepared, config = separate_students_staff(), windows(30)
    venues = physical([("R1", 30, "Lecture Hall"), ("R2", 30, "Smart Classroom")])
    result = solve_timetable(prepared, config, venue_input=venues)
    complete(prepared, config, venues, result)
    assert {item.assigned_venue_id for item in result.schedule} == {"R1", "R2"}
    assert {item.start_minute for item in result.schedule} == {0}


@pytest.mark.parametrize("rules,rooms,features", [
    ([], [("R1", 24, "Lecture Hall")], {}),
    ([{"requirement_id": "REQ1", "allowed_venue_types": ["Computer Lab"]}], [("R1", 30, "Lecture Hall")], {}),
    ([{"requirement_id": "REQ1", "required_features": ["Projector"]}], [("R1", 30, "Lecture Hall")], {}),
    ([{"requirement_id": "REQ1", "required_features": ["Projector"]}], [("R1", 30, "Lecture Hall")], {"R1": {"Computer"}}),
    ([{"requirement_id": "REQ1", "required_lab_specialization": "Linux"}], [("R1", 30, "Computer Lab")], {}),
    ([{"requirement_id": "REQ1", "eligible_venue_ids": []}], [("R1", 30, "Lecture Hall")], {}),
    ([{"requirement_id": "REQ1", "excluded_venue_ids": ["R1"]}], [("R1", 30, "Lecture Hall")], {}),
    ([{"requirement_id": "REQ1", "allowed_venue_types": []}], [("R1", 30, "Lecture Hall")], {}),
])
def test_no_eligible_room_is_infeasible_without_downgrading_mode(rules, rooms, features):
    failed(solve_timetable(one_session(), windows(), venue_input=physical(rooms, rules=rules, features=features)))


def test_explicit_equipment_specialization_type_and_restrictions_are_respected():
    venues = physical([("R1", 30, "Lecture Hall"), ("R2", 30, "Computer Lab"), ("R3", 30, "Computer Lab")],
                      rules=[{"requirement_id": "REQ1", "allowed_venue_types": ["Computer Lab"],
                              "required_features": ["Projector"], "required_lab_specialization": "Linux",
                              "eligible_venue_ids": ["R2", "R3"], "excluded_venue_ids": ["R3"]}],
                      features={"R2": {"Projector"}, "R3": {"Projector"}})
    for venue in venues.venues[1:]:
        venue.lab_specialization = "Linux"
    prepared, config = one_session(), windows()
    result = solve_timetable(prepared, config, venue_input=venues)
    complete(prepared, config, venues, result)
    assert result.schedule[0].assigned_venue_id == "R2"


def test_capacity_uses_child_group_size_and_never_infers_partitioning():
    prepared = demand({"A": [("LEC001", "LIC")]}, [("A", "MAIN", 1), ("A", "CHILD", 1)],
                      parents={"CHILD": "MAIN"})
    inputs = prepared.input_snapshot.model_copy(deep=True)
    inputs.student_groups[0].student_count = 75
    inputs.student_groups[1].student_count = 20
    prepared = prepare_timetable_input(inputs)
    venues = physical([("LARGE", 75, "Lecture Hall"), ("SMALL", 20, "Lecture Hall")], rules=[
        {"requirement_id": "REQ1", "eligible_venue_ids": ["LARGE"]},
        {"requirement_id": "REQ2", "eligible_venue_ids": ["SMALL"]},
    ])
    result = solve_timetable(prepared, windows(), venue_input=venues)
    complete(prepared, windows(), venues, result)
    assert {item.session.group_id: item.assigned_venue_id for item in result.schedule} == {"MAIN": "LARGE", "CHILD": "SMALL"}


def test_lab_session_does_not_automatically_require_computer_lab():
    prepared = one_session()
    inputs = prepared.input_snapshot.model_dump(round_trip=True)
    inputs["session_requirements"][0]["session_type"] = "LAB"
    from schedence_teaching.schemas import TeachingTimetableInput
    prepared = prepare_timetable_input(TeachingTimetableInput.model_validate(inputs))
    result = solve_timetable(prepared, windows(), venue_input=physical())
    assert result.status is SolverStatus.OPTIMAL and result.schedule[0].assigned_venue_id == "R1"


@pytest.mark.parametrize("mutation", ["missing_calendar", "unknown_windows", "empty_calendar"])
def test_missing_availability_is_explicit_input_error(mutation):
    venues = physical()
    if mutation == "missing_calendar":
        venues.calendars.clear()
    elif mutation == "unknown_windows":
        venues.calendars[0].available_windows = None
    else:
        venues.calendars[0].available_windows = {}
    with pytest.raises(SolverInputError, match="missing_venue_availability"):
        solve_timetable(one_session(), windows(), venue_input=venues)


def test_unknown_blocked_periods_are_distinct_from_confirmed_empty_list():
    venues = physical()
    venues.calendars[0].blocked_periods = None
    with pytest.raises(SolverInputError, match="unknown_venue_blocked_periods"):
        solve_timetable(one_session(), windows(), venue_input=venues)
    venues.calendars[0].blocked_periods = []
    result = solve_timetable(one_session(), windows(), venue_input=venues)
    complete(one_session(), windows(), venues, result)


@pytest.mark.parametrize("available", [{"DAY": []}, {"DAY": [{"start_minute": 0, "end_minute": 15}]}])
def test_explicit_closed_or_too_short_availability_is_infeasible(available):
    payload = physical().model_dump(round_trip=True)
    payload["calendars"][0]["available_windows"] = available
    failed(solve_timetable(one_session(), windows(), venue_input=VenueSchedulingInput.model_validate(payload)))


def test_omitted_calendar_day_is_unknown_rather_than_inferred_closed():
    with pytest.raises(SolverInputError, match="missing_venue_availability.*D2"):
        solve_timetable(one_session(), windows(days=("DAY", "D2")), venue_input=physical())


def test_stream_and_venue_windows_intersect_without_crossing_gaps():
    payload = physical().model_dump(round_trip=True)
    payload["calendars"][0]["available_windows"] = {"DAY": [
        {"start_minute": 15, "end_minute": 45}, {"start_minute": 60, "end_minute": 90},
    ]}
    venues = VenueSchedulingInput.model_validate(payload)
    prepared = one_session()
    result = solve_timetable(prepared, windows(), venue_input=venues)
    assert result.schedule[0].start_minute == 15 and result.schedule[0].end_minute == 45
    config = windows(90)
    inputs = prepared.input_snapshot.model_copy(deep=True)
    inputs.session_requirements[0].duration_minutes = 45
    failed(solve_timetable(prepare_timetable_input(inputs), config, venue_input=venues))


def test_blocked_periods_are_removed_and_touching_block_boundaries_are_allowed():
    payload = physical().model_dump(round_trip=True)
    payload["calendars"][0]["blocked_periods"] = [{"day": "DAY", "start_minute": 0, "end_minute": 30}]
    venues = VenueSchedulingInput.model_validate(payload)
    result = solve_timetable(one_session(), windows(), venue_input=venues)
    assert result.schedule[0].start_minute == 30
    failed(solve_timetable(one_session(), windows(30), venue_input=venues))


def test_overlapping_blocks_do_not_conflict_with_each_other_or_create_false_infeasibility():
    payload = physical().model_dump(round_trip=True)
    payload["calendars"][0]["blocked_periods"] = [
        {"day": "DAY", "start_minute": 0, "end_minute": 30},
        {"day": "DAY", "start_minute": 15, "end_minute": 30},
    ]
    result = solve_timetable(one_session(), windows(), venue_input=VenueSchedulingInput.model_validate(payload))
    assert result.status is SolverStatus.OPTIMAL and result.schedule[0].start_minute == 30


def test_same_room_clashes_are_prevented_but_adjacent_sessions_are_feasible():
    prepared, venues = separate_students_staff(), physical()
    failed(solve_timetable(prepared, windows(30), venue_input=venues))
    result = solve_timetable(prepared, windows(), venue_input=venues)
    complete(prepared, windows(), venues, result)
    assert {item.start_minute for item in result.schedule} == {0, 30}
    assert {item.assigned_venue_id for item in result.schedule} == {"R1"}


def test_room_is_shared_across_streams_on_same_day():
    prepared = demand({"A": [("LEC001", "LIC")], "B": [("LEC002", "LIC")]}, streams={"B": "WEEKEND"})
    venues = physical()
    failed(solve_timetable(prepared, windows(30, both_streams=True), venue_input=venues))
    config = windows(both_streams=True)
    result = solve_timetable(prepared, config, venue_input=venues)
    complete(prepared, config, venues, result)
    assert {item.start_minute for item in result.schedule} == {0, 30}


def test_same_room_same_local_times_on_different_days_do_not_clash():
    prepared, venues = separate_students_staff(), physical()
    payload = venues.model_dump(round_trip=True)
    payload["calendars"][0]["available_windows"] = {
        day: [{"start_minute": 0, "end_minute": 30}] for day in ("D1", "D2")
    }
    venues = VenueSchedulingInput.model_validate(payload)
    config = windows(30, days=("D1", "D2"))
    result = solve_timetable(prepared, config, venue_input=venues)
    complete(prepared, config, venues, result)
    assert {item.day for item in result.schedule} == {"D1", "D2"}


def test_joint_staff_time_room_choices_use_non_greedy_alternatives():
    prepared = demand({"A": [("LEC001", "LIC"), ("LEC002", "CO_LIC")], "B": [("LEC001", "LIC")]})
    venues = physical([("R1", 30, "Lecture Hall"), ("R2", 30, "Lecture Hall")],
                      rules=[{"requirement_id": "REQ2", "eligible_venue_ids": ["R1"]}])
    config = windows(30)
    result = solve_timetable(prepared, config, venue_input=venues)
    complete(prepared, config, venues, result)
    assert {item.session.offering_id: (item.assigned_staff_id, item.assigned_venue_id) for item in result.schedule} == {
        "A": ("LEC002", "R2"), "B": ("LEC001", "R1"),
    }


@pytest.mark.parametrize("constraint", ["staff", "parent", "nested"])
def test_sufficient_venues_do_not_remove_existing_staff_or_student_constraints(constraint):
    venues = physical([("R1", 30, "Lecture Hall"), ("R2", 30, "Lecture Hall")])
    if constraint == "staff":
        prepared = demand({"A": [("LEC001", "LIC")], "B": [("LEC001", "LIC")]})
    else:
        parents = {"CHILD": "MAIN"} if constraint == "parent" else {"MIDDLE": "MAIN", "CHILD": "MIDDLE"}
        requirements = [("A", group, 1) for group in parents | {"MAIN": None}]
        prepared = demand({"A": [("LEC001", "LIC"), ("LEC002", "CO_LIC"), ("LEC003", "TEAM_MEMBER")]},
                          requirements, parents=parents)
    failed(solve_timetable(prepared, windows(30), venue_input=venues))


def test_siblings_remain_concurrent_when_staff_and_room_capacity_exist():
    prepared = demand({"A": [("LEC001", "LIC"), ("LEC002", "CO_LIC")]},
                      [("A", "MAIN", 1), ("A", "X", 1), ("A", "Y", 1)], parents={"X": "MAIN", "Y": "MAIN"})
    venues = physical([("R1", 30, "Lecture Hall"), ("R2", 30, "Lecture Hall")])
    result = solve_timetable(prepared, windows(), venue_input=venues)
    complete(prepared, windows(), venues, result)
    by_group = {item.session.group_id: item for item in result.schedule}
    assert by_group["X"].start_minute == by_group["Y"].start_minute != by_group["MAIN"].start_minute


def combined_input(first_room="COMBINED", second_room="B"):
    return physical([("COMBINED", 60, "Lecture Hall"), ("B", 30, "Lecture Hall"), ("C", 30, "Lecture Hall")],
                    combined=[{"combined_venue_id": "COMBINED", "component_venue_ids": ["B", "C"]}],
                    rules=[{"requirement_id": "REQ1", "eligible_venue_ids": [first_room]},
                           {"requirement_id": "REQ2", "eligible_venue_ids": [second_room]}])


@pytest.mark.parametrize("component", ["B", "C"])
def test_combined_room_blocks_each_component_but_allows_adjacent_bookings(component):
    prepared, venues = separate_students_staff(), combined_input(second_room=component)
    failed(solve_timetable(prepared, windows(30), venue_input=venues))
    result = solve_timetable(prepared, windows(), venue_input=venues)
    complete(prepared, windows(), venues, result)
    assert {item.start_minute for item in result.schedule} == {0, 30}


def test_component_rooms_do_not_automatically_conflict_with_each_other():
    venues = combined_input("B", "C")
    prepared, config = separate_students_staff(), windows(30)
    result = solve_timetable(prepared, config, venue_input=venues)
    complete(prepared, config, venues, result)
    assert {item.start_minute for item in result.schedule} == {0}


def test_nested_components_and_explicit_shared_resources_protect_real_footprints():
    prepared = separate_students_staff()
    rooms = [(name, 60, "Lecture Hall") for name in ("A", "B", "C", "D")]
    venues = physical(rooms, combined=[
        {"combined_venue_id": "A", "component_venue_ids": ["B", "C"]},
        {"combined_venue_id": "B", "component_venue_ids": ["D"]},
    ], rules=[{"requirement_id": "REQ1", "eligible_venue_ids": ["A"]},
              {"requirement_id": "REQ2", "eligible_venue_ids": ["D"]}])
    failed(solve_timetable(prepared, windows(30), venue_input=venues))
    shared = physical([("R1", 30, "Lecture Hall"), ("R2", 30, "Lecture Hall")],
                      shared=[{"resource_id": "EXPLICIT-SHARED-SPACE", "venue_ids": ["R1", "R2"]}])
    failed(solve_timetable(prepared, windows(30), venue_input=shared))


def test_component_availability_and_blocked_periods_apply_to_combined_booking():
    venues = combined_input()
    venues.requirements = venues.requirements[:1]
    payload = venues.model_dump(round_trip=True)
    payload["calendars"][1]["blocked_periods"] = [{"day": "DAY", "start_minute": 0, "end_minute": 30}]
    result = solve_timetable(one_session(), windows(), venue_input=VenueSchedulingInput.model_validate(payload))
    assert result.schedule[0].assigned_venue_id == "COMBINED" and result.schedule[0].start_minute == 30
    payload["calendars"][2]["available_windows"] = {"DAY": []}
    failed(solve_timetable(one_session(), windows(), venue_input=VenueSchedulingInput.model_validate(payload)))


def test_block_on_combined_space_also_blocks_component_booking():
    venues = combined_input("B", "C")
    venues.requirements = venues.requirements[:1]
    payload = venues.model_dump(round_trip=True)
    payload["calendars"][0]["blocked_periods"] = [{"day": "DAY", "start_minute": 0, "end_minute": 30}]
    result = solve_timetable(one_session(), windows(), venue_input=VenueSchedulingInput.model_validate(payload))
    assert result.schedule[0].assigned_venue_id == "B" and result.schedule[0].start_minute == 30


def test_unresolved_rooms_are_excluded_even_with_availability_and_without_name_cues():
    venues = physical([("NORMAL-LOOKING-ID", 30, "Lecture Hall"), ("R2", 30, "Lecture Hall")], unresolved=["NORMAL-LOOKING-ID"])
    venues.calendars = venues.calendars[1:]  # No invented availability for the excluded room.
    result = solve_timetable(one_session(), windows(), venue_input=venues)
    assert result.schedule[0].assigned_venue_id == "R2"
    only = physical(unresolved=["R1"])
    only.calendars = []
    failed(solve_timetable(one_session(), windows(), venue_input=only))


def test_names_never_create_components_or_shared_resources():
    venues = physical([("X+Y", 30, "Lecture Hall"), ("X", 30, "Lecture Hall")])
    result = solve_timetable(separate_students_staff(), windows(30), venue_input=venues)
    assert result.status is SolverStatus.OPTIMAL  # Explicitly declared no unresolved spaces; no parsing names.


@pytest.mark.parametrize("problem", ["venue", "calendar", "unresolved", "features", "restriction", "component", "resource"])
def test_unknown_or_duplicate_evidence_references_are_rejected(problem):
    payload = physical().model_dump(round_trip=True)
    if problem == "venue":
        payload["venues"].append(payload["venues"][0])
    elif problem == "calendar":
        payload["calendars"][0]["venue_id"] = "UNKNOWN"
    elif problem == "unresolved":
        payload["unresolved_venue_ids"] = ["UNKNOWN"]
    elif problem == "features":
        payload["venue_features"] = {"UNKNOWN": ["Projector"]}
    elif problem == "restriction":
        payload["requirements"] = [{"requirement_id": "REQ1", "eligible_venue_ids": ["UNKNOWN"]}]
    elif problem == "component":
        payload["combined_venues"] = [{"combined_venue_id": "R1", "component_venue_ids": ["UNKNOWN"]}]
    else:
        payload["shared_resources"] = [{"resource_id": "SHARED", "venue_ids": ["R1", "UNKNOWN"]}]
    with pytest.raises(ValidationError):
        VenueSchedulingInput.model_validate(payload)


@pytest.mark.parametrize("problem", ["cycle", "self", "unresolved_confirmed", "duplicate_components", "duplicate_resource_members"])
def test_invalid_shared_space_relationships_are_rejected(problem):
    payload = physical([("A", 30, "Lecture Hall"), ("B", 30, "Lecture Hall")]).model_dump(round_trip=True)
    payload["combined_venues"] = [{"combined_venue_id": "A", "component_venue_ids": ["B"]}]
    if problem == "cycle":
        payload["combined_venues"].append({"combined_venue_id": "B", "component_venue_ids": ["A"]})
    elif problem == "self":
        payload["combined_venues"][0]["component_venue_ids"] = ["A"]
    elif problem == "unresolved_confirmed":
        payload["unresolved_venue_ids"] = ["A"]
    elif problem == "duplicate_components":
        payload["combined_venues"][0]["component_venue_ids"] = ["B", "B"]
    else:
        payload["shared_resources"] = [{"resource_id": "SHARED", "venue_ids": ["A", "A"]}]
    with pytest.raises(ValidationError):
        VenueSchedulingInput.model_validate(payload)


@pytest.mark.parametrize("problem,code", [("day", "Unknown scheduling day"), ("alignment", "align"), ("requirement", "Unknown requirement_id")])
def test_bad_days_alignment_and_requirement_references_are_explicit(problem, code):
    payload = physical().model_dump(round_trip=True)
    if problem == "day":
        payload["calendars"][0]["available_windows"] = {"UNKNOWN": []}
    elif problem == "alignment":
        payload["calendars"][0]["blocked_periods"] = [{"day": "DAY", "start_minute": 1, "end_minute": 30}]
    else:
        payload["requirements"] = [{"requirement_id": "UNKNOWN"}]
    with pytest.raises(SolverInputError, match=code):
        solve_timetable(one_session(), windows(), venue_input=VenueSchedulingInput.model_validate(payload))


def test_unknown_types_and_mutated_models_cannot_bypass_validation():
    payload = physical().model_dump(round_trip=True)
    payload["venues"][0]["venue_type"] = "Unknown venue type"
    with pytest.raises(ValidationError):
        VenueSchedulingInput.model_validate(payload)
    venues = physical()
    venues.venues[0].capacity = 0
    with pytest.raises(ValidationError):
        solve_timetable(one_session(), windows(), venue_input=venues)
    with pytest.raises(SolverInputError, match="VenueSchedulingInput"):
        solve_timetable(one_session(), windows(), venue_input={})


def test_venue_free_mode_remains_explicit_and_cannot_validate_venue_claims():
    prepared, config = one_session(), windows()
    result = solve_timetable(prepared, config)
    assert not result.venue_enabled and result.schedule[0].assigned_venue_id is None
    assert not result.validation.venue_enabled and "venue_capacity" not in result.validation.checks
    with pytest.raises(TimetableValidationError, match="unexpected_venue_assignment"):
        validate_timetable(prepared, config, [replace(result.schedule[0], assigned_venue_id="R1")])


@pytest.mark.parametrize("value,code", [(None, "invalid_venue_id"), ([], "invalid_venue_id"),
                                       (False, "invalid_venue_id"), ("", "invalid_venue_id"), ("UNKNOWN", "unknown_venue")])
def test_validator_rejects_missing_malformed_or_unknown_room(value, code):
    prepared, config, venues = one_session(), windows(), physical()
    result = solve_timetable(prepared, config, venue_input=venues)
    with pytest.raises(TimetableValidationError, match=code):
        validate_timetable(prepared, config, [replace(result.schedule[0], assigned_venue_id=value)], venue_input=venues)


@pytest.mark.parametrize("problem,code", [("capacity", "venue_capacity"), ("type", "venue_type"),
    ("features", "venue_features"), ("specialization", "venue_specialization"),
    ("eligible", "venue_restriction"), ("excluded", "venue_restriction"),
    ("unresolved", "unresolved_venue"), ("availability", "outside_venue_availability"),
    ("block", "venue_blocked_period")])
def test_independent_validator_rejects_room_policy_and_calendar_violations(problem, code):
    prepared, config, venues = one_session(), windows(), physical()
    result = solve_timetable(prepared, config, venue_input=venues)
    payload = venues.model_dump(round_trip=True)
    rule = {"requirement_id": "REQ1"}
    if problem == "capacity":
        payload["venues"][0]["capacity"] = 24
    elif problem == "type":
        rule["allowed_venue_types"] = ["Computer Lab"]
    elif problem == "features":
        rule["required_features"] = ["Projector"]
    elif problem == "specialization":
        rule["required_lab_specialization"] = "Linux"
    elif problem == "eligible":
        rule["eligible_venue_ids"] = []
    elif problem == "excluded":
        rule["excluded_venue_ids"] = ["R1"]
    elif problem == "unresolved":
        payload["unresolved_venue_ids"] = ["R1"]
    elif problem == "availability":
        payload["calendars"][0]["available_windows"] = {"DAY": []}
    else:
        payload["calendars"][0]["blocked_periods"] = [{"day": "DAY", "start_minute": 0, "end_minute": 60}]
    payload["requirements"] = [rule]
    with pytest.raises(TimetableValidationError, match=code):
        validate_timetable(prepared, config, result.schedule, venue_input=VenueSchedulingInput.model_validate(payload))


@pytest.mark.parametrize("shared", [False, True])
def test_validator_detects_corrupt_same_room_and_combined_component_overlaps(shared):
    prepared, config = separate_students_staff(), windows()
    venues = combined_input() if shared else physical()
    result = solve_timetable(prepared, config, venue_input=venues)
    corrupt = [replace(item, start_minute=0, end_minute=30) for item in result.schedule]
    with pytest.raises(TimetableValidationError, match="shared_space_clash" if shared else "venue_clash"):
        validate_timetable(prepared, config, corrupt, venue_input=venues)


def test_validator_detects_shared_venue_overlap_across_streams():
    prepared = demand({"A": [("LEC001", "LIC")], "B": [("LEC002", "LIC")]}, streams={"B": "WEEKEND"})
    config, venues = windows(both_streams=True), physical()
    result = solve_timetable(prepared, config, venue_input=venues)
    with pytest.raises(TimetableValidationError, match="venue_clash"):
        validate_timetable(prepared, config, [replace(item, start_minute=0, end_minute=30) for item in result.schedule], venue_input=venues)


def test_validator_uses_original_requirement_for_venue_policy_after_metadata_corruption():
    prepared, config = separate_students_staff(), windows()
    venues = physical([("R1", 30, "Lecture Hall"), ("R2", 30, "Computer Lab")], rules=[
        {"requirement_id": "REQ1", "eligible_venue_ids": ["R1"]},
        {"requirement_id": "REQ2", "eligible_venue_ids": ["R2"]},
    ])
    result = solve_timetable(prepared, config, venue_input=venues)
    corrupt = replace(result.schedule[0], session=replace(result.schedule[0].session, requirement_id="REQ2"), assigned_venue_id="R2")
    with pytest.raises(TimetableValidationError) as error:
        validate_timetable(prepared, config, [corrupt, result.schedule[1]], venue_input=venues)
    assert {"metadata_changed", "venue_restriction"} <= {issue.code for issue in error.value.issues}


def test_validator_is_independent_of_model_capacity_filter(monkeypatch):
    from schedence_teaching.solver import engine
    monkeypatch.setattr(engine, "eligible_venue", lambda *args: True)
    with pytest.raises(TimetableValidationError, match="venue_capacity"):
        solve_timetable(one_session(), windows(), venue_input=physical([("R1", 24, "Lecture Hall")]))


def test_validator_is_independent_of_model_shared_resource_builder(monkeypatch):
    from schedence_teaching.solver import engine
    monkeypatch.setattr(engine, "venue_footprints", lambda inputs: {row.venue_id: frozenset({("venue", row.venue_id)}) for row in inputs.venues})
    with pytest.raises(TimetableValidationError, match="shared_space_clash"):
        solve_timetable(separate_students_staff(), windows(30), venue_input=combined_input())


def test_actual_timeout_emits_no_partial_physical_schedule():
    prepared = demand({"A": [("LEC001", "LIC"), ("LEC002", "CO_LIC")]}, [("A", "G", 3)])
    result = solve_timetable(prepared, windows(120, limit=1e-12), venue_input=physical())
    assert result.status is SolverStatus.UNKNOWN and result.venue_enabled
    assert result.schedule == () and result.validation is None


@pytest.mark.parametrize("native", [cp_model.UNKNOWN, cp_model.INFEASIBLE, cp_model.MODEL_INVALID])
def test_unsuccessful_statuses_never_decode_room_choices(monkeypatch, native):
    monkeypatch.setattr(cp_model.CpSolver, "solve", lambda *args: native)
    def forbidden(*args):
        pytest.fail("Failed solves cannot decode physical assignments.")
    monkeypatch.setattr(cp_model.CpSolver, "value", forbidden)
    result = solve_timetable(one_session(), windows(), venue_input=physical())
    assert result.schedule == () and result.validation is None and result.venue_enabled


def test_feasible_native_status_still_requires_complete_independent_validation(monkeypatch):
    original = cp_model.CpSolver.solve
    def feasible(self, model):
        result = original(self, model)
        return cp_model.FEASIBLE if result == cp_model.OPTIMAL else result
    monkeypatch.setattr(cp_model.CpSolver, "solve", feasible)
    prepared, config, venues = one_session(), windows(), physical()
    result = solve_timetable(prepared, config, venue_input=venues)
    assert result.status is SolverStatus.FEASIBLE
    complete(prepared, config, venues, result)


def test_fixed_seed_repeatability_includes_room_choices_and_reordered_inputs():
    prepared, config = separate_students_staff(), windows()
    venues = physical([("R1", 30, "Lecture Hall"), ("R2", 30, "Lecture Hall")])
    first = solve_timetable(prepared, config, venue_input=venues)
    reordered = venues.model_copy(deep=True)
    reordered.venues.reverse()
    reordered.calendars.reverse()
    assert first.schedule == solve_timetable(prepared, config, venue_input=reordered).schedule


def test_empty_physical_demand_is_complete_but_still_requires_explicit_calendars():
    prepared = demand({"A": [("LEC001", "LIC")]}, [])
    venues, config = physical(), windows()
    result = solve_timetable(prepared, config, venue_input=venues)
    complete(prepared, config, venues, result)
    assert result.schedule == () and result.validation.assigned_occurrences == 0
    venues.calendars = []
    with pytest.raises(SolverInputError, match="missing_venue_availability"):
        solve_timetable(prepared, config, venue_input=venues)


def test_import_bridge_retains_confirmation_and_known_unresolved_rooms(dataset):
    imported = read(dataset)
    source = [venue.model_dump(round_trip=True) for venue in imported.venues]
    inputs = venues_from_import(imported, unresolved_venue_ids=[])
    assert inputs.unresolved_venue_ids == {"SYNTH-C+SYNTH-D"}
    assert inputs.verification == imported.verification and inputs.source_workbook == dataset["path"].name
    assert [venue.model_dump(round_trip=True) for venue in inputs.venues] == source
    assert inputs.calendars == []
    with pytest.raises(SolverInputError, match="missing_venue_availability"):
        solve_timetable(one_session(), windows(), venue_input=inputs)


def test_import_bridge_accepts_only_explicit_resolution_of_combined_space(dataset):
    inputs = venues_from_import(read(dataset), combined_venues=[{
        "combined_venue_id": "SYNTH-C+SYNTH-D", "component_venue_ids": ["SYNTH-A", "SYNTH-B"],
    }])
    assert not inputs.unresolved_venue_ids and len(inputs.venues) == 4


def test_import_bridge_does_not_allow_provenance_overrides_or_bad_imports(dataset):
    imported = read(dataset)
    with pytest.raises(SolverInputError, match="cannot be overridden"):
        venues_from_import(imported, venues=[])
    imported.source_sha256 = "0" * 64
    with pytest.raises(SolverInputError, match="confirmation"):
        venues_from_import(imported)


def test_checked_in_joint_venue_example_is_complete_and_valid():
    from schedence_teaching.schemas import TeachingTimetableInput
    from schedence_teaching.solver import SolverConfig
    path = Path(__file__).resolve().parents[1] / "examples" / "venue-scheduling.example.json"
    example = json.loads(path.read_text(encoding="utf-8"))
    prepared = prepare_timetable_input(TeachingTimetableInput.model_validate(example["teaching_input"]))
    config = SolverConfig.model_validate(example["solver_config"])
    venues = VenueSchedulingInput.model_validate(example["venue_input"])
    result = solve_timetable(prepared, config, venue_input=venues)
    complete(prepared, config, venues, result)
    assert result.status is SolverStatus.OPTIMAL and result.validation.clash_pairs_checked == 1
    assert {item.session.requirement_id: (item.assigned_staff_id, item.assigned_venue_id) for item in result.schedule} == {
        "REQ-A": ("SYNTHETIC-STAFF-B", "SYNTHETIC-ROOM-B"),
        "REQ-B": ("SYNTHETIC-STAFF-A", "SYNTHETIC-ROOM-A"),
    }
    assert {item.to_dict()["start_time"] for item in result.schedule} == {"09:00"}


def test_physical_days_remain_distinct_when_granularity_does_not_divide_midnight():
    from schedence_teaching.solver import SolverConfig
    prepared = demand({"A": [("LEC001", "LIC")]}, [("A", "G", 2)])
    payload = prepared.input_snapshot.model_copy(deep=True)
    payload.session_requirements[0].duration_minutes = 14
    prepared = prepare_timetable_input(payload)
    venue_payload = physical().model_dump(round_trip=True)
    venue_payload["calendars"][0]["available_windows"] = {
        day: [{"start_minute": 0, "end_minute": 14}] for day in ("D1", "D2")
    }
    venues = VenueSchedulingInput.model_validate(venue_payload)
    config = SolverConfig(days=[{"day": day, "windows": {"WEEKDAY": [{"start_minute": 0, "end_minute": 14}]}}
                                for day in ("D1", "D2")], granularity_minutes=7, max_time_seconds=5)
    result = solve_timetable(prepared, config, venue_input=venues)
    complete(prepared, config, venues, result)
    assert {item.day for item in result.schedule} == {"D1", "D2"}
