from __future__ import annotations

import pytest
from pydantic import ValidationError

from schedence_teaching.schemas import (
    ModuleOffering,
    SessionRequirement,
    SessionType,
    StaffMember,
    StaffType,
    Stream,
    StudentGroup,
    TeachingRole,
    TeachingTeamMember,
)

MODELS = (StaffMember, ModuleOffering, TeachingTeamMember, StudentGroup, SessionRequirement)
ID_FIELDS = [
    (StaffMember, "staff_id"),
    (ModuleOffering, "offering_id"),
    (TeachingTeamMember, "offering_id"),
    (TeachingTeamMember, "staff_id"),
    (StudentGroup, "group_id"),
    (SessionRequirement, "requirement_id"),
    (SessionRequirement, "offering_id"),
    (SessionRequirement, "group_id"),
]


@pytest.mark.parametrize("model_type", MODELS)
def test_valid_input_and_json_round_trip(model_type, schema_payloads) -> None:
    model = model_type.model_validate(schema_payloads[model_type])
    assert model_type.model_validate_json(model.model_dump_json()) == model


@pytest.mark.parametrize("model_type", MODELS)
def test_required_fields_have_no_invented_defaults(model_type, schema_payloads) -> None:
    for field_name, field in model_type.model_fields.items():
        if field.is_required():
            payload = {
                key: value
                for key, value in schema_payloads[model_type].items()
                if key != field_name
            }
            with pytest.raises(ValidationError) as error:
                model_type.model_validate(payload)
            assert any(
                item["loc"] == (field_name,) and item["type"] == "missing"
                for item in error.value.errors()
            )


@pytest.mark.parametrize(("model_type", "field_name"), ID_FIELDS)
@pytest.mark.parametrize("value", ["", "   ", "\t\n", None, 123])
def test_required_ids_reject_blank_or_non_string_values(model_type, field_name, value, schema_payloads) -> None:
    with pytest.raises(ValidationError):
        model_type.model_validate({**schema_payloads[model_type], field_name: value})


@pytest.mark.parametrize("staff_id", ["LEC001", "LEC090", "INS007", "External-ID-42"])
def test_staff_ids_are_preserved_without_a_generated_format(staff_id, schema_payloads) -> None:
    staff = StaffMember.model_validate({**schema_payloads[StaffMember], "staff_id": staff_id})
    assert staff.staff_id == staff_id


def test_department_is_optional_but_supplied_values_must_be_nonblank(schema_payloads) -> None:
    assert StaffMember.model_validate(schema_payloads[StaffMember]).department is None
    assert StaffMember.model_validate({**schema_payloads[StaffMember], "department": None}).department is None
    staff = StaffMember.model_validate({**schema_payloads[StaffMember], "department": "Computing"})
    assert staff.department == "Computing"
    with pytest.raises(ValidationError):
        StaffMember.model_validate({**schema_payloads[StaffMember], "department": " "})


@pytest.mark.parametrize(("model_type", "field_name"), [
    (StaffMember, "staff_name"),
    (StaffMember, "position"),
    (ModuleOffering, "module_code"),
    (ModuleOffering, "module_name"),
    (ModuleOffering, "specialization"),
    (ModuleOffering, "degree_type"),
])
def test_required_descriptive_text_rejects_whitespace(model_type, field_name, schema_payloads) -> None:
    with pytest.raises(ValidationError):
        model_type.model_validate({**schema_payloads[model_type], field_name: "\t "})


ENUM_FIELDS = [
    (StaffMember, "staff_type", StaffType, ("ACADEMIC", "INSTRUCTOR")),
    (ModuleOffering, "stream", Stream, ("WEEKDAY", "WEEKEND")),
    (StudentGroup, "stream", Stream, ("WEEKDAY", "WEEKEND")),
    (TeachingTeamMember, "role", TeachingRole, ("LIC", "CO_LIC", "TEAM_MEMBER")),
    (SessionRequirement, "session_type", SessionType, ("LECTURE", "TUTORIAL", "WORKSHOP", "LAB")),
]


@pytest.mark.parametrize(("model_type", "field_name", "enum_type", "values"), ENUM_FIELDS)
def test_all_documented_enum_values_are_accepted(
    model_type, field_name, enum_type, values, schema_payloads,
) -> None:
    assert {item.value for item in enum_type} == set(values)
    for value in values:
        model = model_type.model_validate({**schema_payloads[model_type], field_name: value})
        assert getattr(model, field_name) is enum_type(value)
        assert model.model_dump(mode="json")[field_name] == value


@pytest.mark.parametrize(("model_type", "field_name"), [item[:2] for item in ENUM_FIELDS])
@pytest.mark.parametrize("value", ["INVALID", "weekday", "Co-LIC", "", None])
def test_invalid_enum_values_are_rejected(model_type, field_name, value, schema_payloads) -> None:
    with pytest.raises(ValidationError):
        model_type.model_validate({**schema_payloads[model_type], field_name: value})


@pytest.mark.parametrize("model_type", [ModuleOffering, StudentGroup])
@pytest.mark.parametrize(("field_name", "value"), [
    ("academic_year", 0), ("academic_year", -1), ("academic_year", 5),
    ("academic_year", 2026), ("academic_year", True), ("academic_year", "2"),
    ("academic_year", 2.5), ("semester", 0), ("semester", 3),
    ("semester", -1), ("semester", True), ("semester", "1"), ("semester", 1.5),
])
def test_invalid_years_and_semesters(model_type, field_name, value, schema_payloads) -> None:
    with pytest.raises(ValidationError):
        model_type.model_validate({**schema_payloads[model_type], field_name: value})


@pytest.mark.parametrize("model_type", [ModuleOffering, StudentGroup])
@pytest.mark.parametrize(("year", "semester"), [(1, 1), (4, 2)])
def test_year_and_semester_boundaries(model_type, year, semester, schema_payloads) -> None:
    model = model_type.model_validate({
        **schema_payloads[model_type], "academic_year": year, "semester": semester,
    })
    assert model.academic_year == year
    assert model.semester == semester


@pytest.mark.parametrize(("model_type", "field_name"), [
    (StudentGroup, "student_count"),
    (SessionRequirement, "duration_minutes"),
    (SessionRequirement, "sessions_per_week"),
])
@pytest.mark.parametrize("value", [0, -1, True, 1.5, "60", None])
def test_counts_and_durations_require_positive_integers(model_type, field_name, value, schema_payloads) -> None:
    with pytest.raises(ValidationError):
        model_type.model_validate({**schema_payloads[model_type], field_name: value})


def test_explicit_requirement_values_are_preserved(schema_payloads) -> None:
    requirement = SessionRequirement.model_validate({
        **schema_payloads[SessionRequirement], "duration_minutes": 45, "sessions_per_week": 3,
    })
    assert requirement.duration_minutes == 45
    assert requirement.sessions_per_week == 3


def test_group_is_offering_independent_and_may_have_an_optional_parent(schema_payloads) -> None:
    main = StudentGroup.model_validate(schema_payloads[StudentGroup])
    assert main.parent_group_id is None
    assert "offering_id" not in main.model_dump()
    subgroup = StudentGroup.model_validate({
        **schema_payloads[StudentGroup], "group_id": "LAB-A", "parent_group_id": "MAIN",
    })
    assert subgroup.parent_group_id == main.group_id
    with pytest.raises(ValidationError):
        StudentGroup.model_validate({**schema_payloads[StudentGroup], "parent_group_id": "MAIN"})
    with pytest.raises(ValidationError):
        StudentGroup.model_validate({**schema_payloads[StudentGroup], "parent_group_id": " "})


@pytest.mark.parametrize("model_type", MODELS)
def test_unknown_fields_are_rejected(model_type, schema_payloads) -> None:
    with pytest.raises(ValidationError) as error:
        model_type.model_validate({**schema_payloads[model_type], "unexpected_field": "value"})
    assert error.value.errors()[0]["type"] == "extra_forbidden"
