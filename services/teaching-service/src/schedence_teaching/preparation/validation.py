"""Reuse Pydantic contract validation, then check preparation-specific periods."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import ValidationError

from schedence_teaching.schemas import ModuleOffering, StudentGroup, TeachingTimetableInput

from .errors import PreparationError, PreparationIssue

PERIOD_FIELDS = ("academic_year", "semester", "stream")


def _validate_payload(payload: dict) -> TeachingTimetableInput:
    try:
        return TeachingTimetableInput.model_validate(payload)
    except ValidationError as error:
        raise PreparationError([
            PreparationIssue(detail["type"], tuple(detail["loc"]), detail["msg"])
            for detail in error.errors(include_input=False, include_url=False, include_context=False)
        ]) from None


def validated_snapshot(inputs: TeachingTimetableInput) -> TeachingTimetableInput:
    """Take an independent snapshot, including checks for mutated model instances."""
    if not isinstance(inputs, TeachingTimetableInput):
        raise PreparationError([PreparationIssue(
            "invalid_input", (), "Preparation requires TeachingTimetableInput contracts, not Excel staging records.",
        )])
    return _validate_payload(inputs.model_dump(round_trip=True))


def validated_group_snapshot(groups: Sequence[StudentGroup]) -> TeachingTimetableInput:
    if any(not isinstance(group, StudentGroup) for group in groups):
        raise PreparationError([PreparationIssue(
            "invalid_input", ("student_groups",), "Hierarchy construction requires StudentGroup contracts.",
        )])
    return _validate_payload({
        "staff_members": [], "module_offerings": [], "teaching_team_members": [],
        "student_groups": [group.model_dump(round_trip=True) for group in groups], "session_requirements": [],
    })


def period_issues(
    group: StudentGroup, other: StudentGroup | ModuleOffering, *, code: str,
    location: tuple[str | int, ...], description: str,
) -> list[PreparationIssue]:
    return [PreparationIssue(code, location + (field,), f"{description}: {field} must match.")
            for field in PERIOD_FIELDS if getattr(group, field) != getattr(other, field)]
