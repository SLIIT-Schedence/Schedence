"""Synthetic contract inputs only; no real workbook-to-solver conversion."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from schedence_teaching.preparation import (
    PreparationError, build_group_hierarchy, expand_session_requirements,
    groups_conflict, prepare_timetable_input,
)
from schedence_teaching.schemas import SessionRequirement, SessionType, Stream, StudentGroup, TeachingTimetableInput


@pytest.fixture
def preparation_input() -> TeachingTimetableInput:
    path = Path(__file__).resolve().parents[1] / "examples" / "preparation-input.example.json"
    return TeachingTimetableInput.model_validate_json(path.read_text(encoding="utf-8"))


def test_example_expansion_and_summary_are_explicit(preparation_input):
    prepared = prepare_timetable_input(preparation_input)
    assert [session.occurrence_id for session in prepared.sessions] == ["REQ001:1", "REQ001:2", "REQ002:1", "REQ003:1"]
    assert prepared.sessions[0].to_dict() == {
        "occurrence_id": "REQ001:1", "requirement_id": "REQ001", "offering_id": "OFFER-WD",
        "group_id": "MAIN-WD", "session_type": "LECTURE", "duration_minutes": 120,
        "occurrence_number": 1, "stream": "WEEKDAY",
    }
    assert prepared.sessions[1].duration_minutes == 120
    assert prepared.summary.to_dict() == {
        "total_student_groups": 4, "main_groups": 2, "subgroups": 2,
        "session_requirement_count": 3, "expanded_weekly_session_count": 4,
        "weekly_contact_minutes": 390,
        "session_type_counts": {"LECTURE": 2, "TUTORIAL": 1, "WORKSHOP": 0, "LAB": 1},
        "stream_counts": {"WEEKDAY": 3, "WEEKEND": 1},
    }
    assert expand_session_requirements(preparation_input) == prepared.sessions


@pytest.mark.parametrize(("first", "second", "expected"), [
    ("MAIN-WD", "MAIN-WD", True), ("LAB-A", "LAB-A", True),
    ("MAIN-WD", "LAB-A", True), ("LAB-B", "MAIN-WD", True),
    ("LAB-A", "LAB-B", False), ("MAIN-WD", "MAIN-WE", False),
])
def test_conflict_rules_are_symmetric(first, second, expected, preparation_input):
    hierarchy = build_group_hierarchy(preparation_input.student_groups)
    assert groups_conflict(hierarchy, first, second) is expected
    assert hierarchy.conflicts(second, first) is expected


def test_nested_descendants_and_unrelated_branches(preparation_input):
    groups = preparation_input.student_groups + [StudentGroup(
        group_id="LAB-A-NESTED", academic_year=2, semester=1, stream="WEEKDAY",
        student_count=20, parent_group_id="LAB-A",
    )]
    hierarchy = build_group_hierarchy(groups)
    assert hierarchy.children["MAIN-WD"] == ("LAB-A", "LAB-B")
    assert hierarchy.parents["LAB-A-NESTED"] == "LAB-A"
    assert hierarchy.descendants("MAIN-WD") == ("LAB-A", "LAB-A-NESTED", "LAB-B")
    assert hierarchy.descendants("LAB-A-NESTED") == ()
    assert hierarchy.conflicts("MAIN-WD", "LAB-A-NESTED")
    assert hierarchy.conflicts("LAB-A", "LAB-A-NESTED")
    assert not hierarchy.conflicts("LAB-B", "LAB-A-NESTED")


def test_deep_hierarchy_uses_no_recursive_traversal():
    groups = [StudentGroup(
        group_id=f"G{index}", academic_year=1, semester=1, stream="WEEKDAY", student_count=1,
        parent_group_id=f"G{index-1}" if index else None,
    ) for index in range(1100)]
    hierarchy = build_group_hierarchy(groups)
    assert hierarchy.conflicts("G0", "G1099")
    assert len(hierarchy.descendants("G0")) == 1099


def test_sizes_are_supplied_and_no_partition_is_assumed(preparation_input):
    preparation_input.student_groups[0].student_count = 170
    preparation_input.student_groups[1].student_count = 80
    preparation_input.student_groups[2].student_count = 50
    before = preparation_input.model_dump()
    prepared = prepare_timetable_input(preparation_input)
    assert [group.student_count for group in prepared.input_snapshot.student_groups] == [170, 80, 50, 75]
    assert preparation_input.model_dump() == before
    assert prepared.summary.weekly_contact_minutes == 390  # Not weighted by student counts.


@pytest.mark.parametrize(("field", "value"), [("academic_year", 1), ("semester", 2), ("stream", Stream.WEEKEND)])
def test_parent_child_period_mismatches_have_locations(field, value, preparation_input):
    setattr(preparation_input.student_groups[1], field, value)
    with pytest.raises(PreparationError) as error:
        build_group_hierarchy(preparation_input.student_groups)
    assert error.value.issues[0].code == "parent_period_mismatch"
    assert error.value.issues[0].location == ("student_groups", 1, field)
    assert field in str(error.value)


@pytest.mark.parametrize(("field", "value"), [("academic_year", 1), ("semester", 2), ("stream", Stream.WEEKEND)])
def test_offering_group_compatibility_is_checked_per_requirement(field, value, preparation_input):
    setattr(preparation_input.module_offerings[0], field, value)
    with pytest.raises(PreparationError) as error:
        prepare_timetable_input(preparation_input)
    assert len(error.value.issues) == 2
    assert all(issue.code == "offering_group_period_mismatch" for issue in error.value.issues)
    assert all(issue.location[-1] == field for issue in error.value.issues)
    assert error.value.errors()[0]["location"] == ["session_requirements", 0, field]


def test_all_period_mismatches_are_reported_together(preparation_input):
    offering = preparation_input.module_offerings[0]
    offering.academic_year, offering.semester, offering.stream = 1, 2, Stream.WEEKEND
    with pytest.raises(PreparationError) as error:
        prepare_timetable_input(preparation_input)
    assert len(error.value.issues) == 6


@pytest.mark.parametrize(("case", "message"), [
    ("missing_parent", "unknown parent_group_id"), ("cycle", "contain a cycle"),
    ("self_parent", "own parent"), ("duplicate_group", "duplicate group_id"),
    ("missing_offering", "unknown offering_id"), ("missing_group", "unknown group_id"),
    ("duplicate_requirement", "duplicate requirement_id"), ("duplicate_offering", "duplicate offering_id"),
])
def test_shared_contract_relationship_checks_are_reused(case, message, preparation_input):
    if case == "missing_parent":
        preparation_input.student_groups[1].parent_group_id = "UNKNOWN"
    elif case == "cycle":
        preparation_input.student_groups[0].parent_group_id = "LAB-A"
    elif case == "self_parent":
        preparation_input.student_groups[0].parent_group_id = "MAIN-WD"
    elif case == "duplicate_group":
        preparation_input.student_groups.append(deepcopy(preparation_input.student_groups[0]))
    elif case == "missing_offering":
        preparation_input.session_requirements[0].offering_id = "UNKNOWN"
    elif case == "missing_group":
        preparation_input.session_requirements[0].group_id = "UNKNOWN"
    elif case == "duplicate_requirement":
        preparation_input.session_requirements.append(deepcopy(preparation_input.session_requirements[0]))
    else:
        preparation_input.module_offerings.append(deepcopy(preparation_input.module_offerings[0]))
    with pytest.raises(PreparationError, match=message):
        prepare_timetable_input(preparation_input)


@pytest.mark.parametrize(("collection", "field", "value"), [
    ("student_groups", "student_count", 0), ("student_groups", "student_count", -1),
    ("student_groups", "academic_year", 0), ("student_groups", "academic_year", 5),
    ("student_groups", "semester", 3), ("module_offerings", "academic_year", 5),
    ("module_offerings", "semester", 0), ("session_requirements", "duration_minutes", 0),
    ("session_requirements", "sessions_per_week", 0),
])
def test_mutated_contracts_are_revalidated(collection, field, value, preparation_input):
    setattr(getattr(preparation_input, collection)[0], field, value)
    with pytest.raises(PreparationError) as error:
        prepare_timetable_input(preparation_input)
    assert error.value.issues[0].location == (collection, 0, field)


def test_ids_and_occurrence_order_are_stable_when_inputs_are_reordered(preparation_input):
    first = prepare_timetable_input(preparation_input)
    reordered = preparation_input.model_copy(deep=True)
    for field in ("student_groups", "module_offerings", "session_requirements", "teaching_team_members"):
        getattr(reordered, field).reverse()
    second = prepare_timetable_input(reordered)
    assert first.sessions == second.sessions
    assert first.hierarchy.descendants("MAIN-WD") == second.hierarchy.descendants("MAIN-WD")
    assert first.summary.to_dict() == second.summary.to_dict()


def test_streams_remain_in_one_demand_set_with_shared_candidate_staff(preparation_input):
    prepared = prepare_timetable_input(preparation_input)
    assert len(prepared.sessions_by_stream[Stream.WEEKDAY]) == 3
    assert len(prepared.sessions_by_stream[Stream.WEEKEND]) == 1
    assert {member.staff_id for member in prepared.input_snapshot.teaching_team_members} == {"SYNTHETIC-STAFF"}
    assert len({session.offering_id for session in prepared.sessions}) == 2
    assert all(set(session.to_dict()).isdisjoint({"staff_id", "venue_id", "day", "start_time"}) for session in prepared.sessions)


def test_no_session_is_inferred_for_an_offering_without_requirements(preparation_input):
    preparation_input.session_requirements = []
    prepared = prepare_timetable_input(preparation_input)
    assert prepared.sessions == ()
    assert prepared.summary.session_requirement_count == prepared.summary.weekly_contact_minutes == 0


def test_empty_input_is_an_empty_preparation():
    inputs = TeachingTimetableInput(staff_members=[], module_offerings=[], teaching_team_members=[],
                                   student_groups=[], session_requirements=[])
    prepared = prepare_timetable_input(inputs)
    assert prepared.sessions == () and prepared.hierarchy.main_group_ids == ()
    assert prepared.summary.to_dict() == {
        "total_student_groups": 0, "main_groups": 0, "subgroups": 0, "session_requirement_count": 0,
        "expanded_weekly_session_count": 0, "weekly_contact_minutes": 0,
        "session_type_counts": {"LECTURE": 0, "TUTORIAL": 0, "WORKSHOP": 0, "LAB": 0},
        "stream_counts": {"WEEKDAY": 0, "WEEKEND": 0},
    }


@pytest.mark.parametrize("session_type", [SessionType.LECTURE, SessionType.TUTORIAL, SessionType.WORKSHOP, SessionType.LAB])
def test_each_session_type_preserves_explicit_duration_and_frequency(session_type, preparation_input):
    preparation_input.session_requirements = [SessionRequirement(
        requirement_id="EXPLICIT", offering_id="OFFER-WD", group_id="MAIN-WD",
        session_type=session_type, duration_minutes=45, sessions_per_week=3,
    )]
    prepared = prepare_timetable_input(preparation_input)
    assert [session.occurrence_id for session in prepared.sessions] == ["EXPLICIT:1", "EXPLICIT:2", "EXPLICIT:3"]
    assert all(session.duration_minutes == 45 and session.session_type == session_type for session in prepared.sessions)
    assert prepared.summary.weekly_contact_minutes == 135
    assert prepared.summary.session_type_counts[session_type] == 3


def test_colons_in_requirement_ids_do_not_collide_with_occurrence_suffixes(preparation_input):
    preparation_input.session_requirements[1].requirement_id = "REQ001:1"
    sessions = expand_session_requirements(preparation_input)
    assert len({session.occurrence_id for session in sessions}) == 4
    assert {"REQ001:1", "REQ001:1:1"}.issubset({session.occurrence_id for session in sessions})


def test_unknown_conflict_query_fails_instead_of_implying_no_conflict(preparation_input):
    hierarchy = build_group_hierarchy(preparation_input.student_groups)
    with pytest.raises(PreparationError, match="Unknown group_id"):
        groups_conflict(hierarchy, "UNKNOWN", "MAIN-WD")
    with pytest.raises(PreparationError):
        hierarchy.descendants("UNKNOWN")


def test_preparation_is_independent_of_later_input_mutation(preparation_input):
    prepared = prepare_timetable_input(preparation_input)
    preparation_input.session_requirements[0].duration_minutes = 10
    preparation_input.student_groups[0].student_count = 999
    assert prepared.sessions[0].duration_minutes == 120
    assert prepared.input_snapshot.student_groups[0].student_count == 120


def test_incomplete_staging_or_plain_dicts_are_not_prepared(preparation_input):
    with pytest.raises(PreparationError, match="not Excel staging"):
        prepare_timetable_input(preparation_input.model_dump())
    with pytest.raises(PreparationError, match="StudentGroup contracts"):
        build_group_hierarchy([{"group_id": "INCOMPLETE"}])
