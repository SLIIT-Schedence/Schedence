"""Venue input contracts and confirmation evidence, separate from source labels."""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, StrictInt, model_validator

from .models import InputContract, PositiveInteger, RequiredText


class VenueType(StrEnum):
    LECTURE_HALL = "Lecture Hall"
    COMPUTER_LAB = "Computer Lab"
    SMART_CLASSROOM = "Smart Classroom"
    BYOD_LAB = "BYOD Lab"


class Venue(InputContract):
    """Supplied venue attributes; no availability or room-sharing policy is inferred."""

    venue_id: RequiredText
    venue_name: RequiredText
    venue_type: VenueType
    capacity: PositiveInteger
    building: RequiredText | None = None
    floor: StrictInt | RequiredText | None = None
    lab_specialization: RequiredText | None = None


ConfirmedVenueField = Literal["venue_id", "venue_name", "venue_type", "capacity", "provided_location"]


class VenueVerification(InputContract):
    """Owner-reported campus confirmation tied to exact source bytes, not blanket readiness."""

    status: Literal["campus_confirmed_development"]
    evidence_source: Literal["project_owner_reports_campus_confirmation"]
    statement: RequiredText
    workbook_sha256: Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{64}$")]
    record_count: PositiveInteger
    confirmed_fields: Annotated[tuple[ConfirmedVenueField, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def unique_fields(self):
        if len(set(self.confirmed_fields)) != len(self.confirmed_fields):
            raise ValueError("confirmed_fields must not contain duplicates")
        return self
