"""Synthetic inputs for schema validation; these are not university records."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from schedence_teaching.schemas import (
    ModuleOffering,
    SessionRequirement,
    StaffMember,
    StudentGroup,
    TeachingTeamMember,
)


@pytest.fixture
def schema_payloads() -> dict[type[BaseModel], dict[str, Any]]:
    return {
        StaffMember: {
            "staff_id": "LEC001",
            "staff_name": "Example Lecturer",
            "position": "Lecturer",
            "staff_type": "ACADEMIC",
        },
        ModuleOffering: {
            "offering_id": "OFFER001",
            "module_code": "IT2100",
            "module_name": "Example Module A",
            "academic_year": 2,
            "semester": 1,
            "specialization": "Example Specialization",
            "degree_type": "Example Degree",
            "stream": "WEEKDAY",
        },
        TeachingTeamMember: {
            "offering_id": "OFFER001",
            "staff_id": "LEC001",
            "role": "LIC",
        },
        StudentGroup: {
            "group_id": "MAIN",
            "academic_year": 2,
            "semester": 1,
            "stream": "WEEKDAY",
            "student_count": 120,
        },
        SessionRequirement: {
            "requirement_id": "REQ001",
            "offering_id": "OFFER001",
            "group_id": "MAIN",
            "session_type": "LECTURE",
            "duration_minutes": 90,
            "sessions_per_week": 1,
        },
    }


@pytest.fixture
def input_payload(schema_payloads: dict[type[BaseModel], dict[str, Any]]) -> dict[str, Any]:
    staff = schema_payloads[StaffMember]
    offering = schema_payloads[ModuleOffering]
    group = schema_payloads[StudentGroup]
    requirement = schema_payloads[SessionRequirement]
    return {
        "staff_members": [
            staff,
            {
                **staff,
                "staff_id": "INS007",
                "staff_name": "Example Instructor",
                "staff_type": "INSTRUCTOR",
            },
        ],
        "module_offerings": [
            offering,
            {**offering, "offering_id": "OFFER002", "module_name": "Example Module B"},
        ],
        "teaching_team_members": [
            schema_payloads[TeachingTeamMember],
            {"offering_id": "OFFER001", "staff_id": "INS007", "role": "TEAM_MEMBER"},
        ],
        "student_groups": [
            group,
            {**group, "group_id": "LAB-A", "student_count": 60, "parent_group_id": "MAIN"},
            {**group, "group_id": "LAB-B", "student_count": 60, "parent_group_id": "MAIN"},
        ],
        "session_requirements": [
            requirement,
            {**requirement, "requirement_id": "REQ002", "offering_id": "OFFER002"},
        ],
    }
