from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from schedence_teaching.schemas import StaffType, TeachingRole, TeachingTimetableInput


def test_duplicate_module_codes_use_distinct_offering_ids(input_payload) -> None:
    inputs = TeachingTimetableInput.model_validate(input_payload)
    first, second = inputs.module_offerings
    assert first.module_code == second.module_code == "IT2100"
    assert first.module_name != second.module_name
    assert first.offering_id != second.offering_id
    assert [item.offering_id for item in inputs.session_requirements] == [
        first.offering_id, second.offering_id,
    ]
    # The same offering-independent group can participate in both offerings.
    assert {item.group_id for item in inputs.session_requirements} == {"MAIN"}
    assert TeachingTimetableInput.model_validate_json(inputs.model_dump_json()) == inputs


@pytest.mark.parametrize(("collection", "id_field"), [
    ("staff_members", "staff_id"),
    ("module_offerings", "offering_id"),
    ("student_groups", "group_id"),
    ("session_requirements", "requirement_id"),
])
def test_duplicate_record_ids_are_rejected(collection, id_field, input_payload) -> None:
    input_payload[collection].append(deepcopy(input_payload[collection][0]))
    with pytest.raises(ValidationError, match=f"duplicate {id_field}"):
        TeachingTimetableInput.model_validate(input_payload)


@pytest.mark.parametrize("role", ["LIC", "CO_LIC"])
def test_lic_and_co_lic_accept_academics(role, input_payload) -> None:
    input_payload["teaching_team_members"][0]["role"] = role
    inputs = TeachingTimetableInput.model_validate(input_payload)
    assert inputs.teaching_team_members[0].role == TeachingRole(role)


@pytest.mark.parametrize("role", ["LIC", "CO_LIC"])
def test_instructors_cannot_be_lic_or_co_lic(role, input_payload) -> None:
    input_payload["teaching_team_members"][1]["role"] = role
    with pytest.raises(ValidationError, match="require ACADEMIC staff"):
        TeachingTimetableInput.model_validate(input_payload)


def test_instructor_candidates_remain_instructors_without_assignments(input_payload) -> None:
    inputs = TeachingTimetableInput.model_validate(input_payload)
    instructor = inputs.staff_members[1]
    candidate = inputs.teaching_team_members[1]
    assert instructor.staff_id == candidate.staff_id == "INS007"
    assert instructor.staff_type is StaffType.INSTRUCTOR
    assert candidate.role is TeachingRole.TEAM_MEMBER
    assert set(candidate.model_dump()) == {"offering_id", "staff_id", "role"}
    assert all("staff_id" not in item.model_dump() for item in inputs.session_requirements)


@pytest.mark.parametrize(("collection", "field_name"), [
    ("teaching_team_members", "staff_id"),
    ("teaching_team_members", "offering_id"),
    ("session_requirements", "offering_id"),
    ("session_requirements", "group_id"),
])
def test_unknown_references_are_rejected(collection, field_name, input_payload) -> None:
    input_payload[collection][0][field_name] = "UNKNOWN"
    with pytest.raises(ValidationError, match=f"unknown {field_name}"):
        TeachingTimetableInput.model_validate(input_payload)


@pytest.mark.parametrize(("main_count", "subgroup_count"), [(120, 60), (170, 85)])
def test_main_groups_and_lab_subgroups_have_supplied_sizes(main_count, subgroup_count, input_payload) -> None:
    input_payload["student_groups"][0]["student_count"] = main_count
    for group in input_payload["student_groups"][1:]:
        group["student_count"] = subgroup_count
    inputs = TeachingTimetableInput.model_validate(input_payload)
    main, first_lab, second_lab = inputs.student_groups
    assert main.student_count == main_count
    assert first_lab.student_count == second_lab.student_count == subgroup_count
    assert first_lab.parent_group_id == second_lab.parent_group_id == main.group_id


def test_unknown_parent_groups_are_rejected(input_payload) -> None:
    input_payload["student_groups"][1]["parent_group_id"] = "UNKNOWN"
    with pytest.raises(ValidationError, match="unknown parent_group_id"):
        TeachingTimetableInput.model_validate(input_payload)


def test_parent_group_cycles_are_rejected(input_payload) -> None:
    input_payload["student_groups"][0]["parent_group_id"] = "LAB-A"
    with pytest.raises(ValidationError, match="contain a cycle"):
        TeachingTimetableInput.model_validate(input_payload)


def test_empty_snapshot_does_not_invent_records() -> None:
    inputs = TeachingTimetableInput(
        staff_members=[], module_offerings=[], teaching_team_members=[],
        student_groups=[], session_requirements=[],
    )
    assert all(value == [] for value in inputs.model_dump().values())
