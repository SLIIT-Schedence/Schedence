"""Versioned form definitions. Labels are presentation; scheduler keys are contracts."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StringConstraints, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


Label = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=200)]
Description = Annotated[str, StringConstraints(strict=True, max_length=4000)]
QuestionId = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=100, pattern=r"^[a-zA-Z][a-zA-Z0-9_.-]*$")]
Revision = Annotated[int, Field(strict=True, ge=1)]


class QuestionKind(StrEnum):
    LOCKED = "LOCKED"
    SUGGESTED = "SUGGESTED"
    CUSTOM = "CUSTOM"


class FieldType(StrEnum):
    AVAILABILITY = "availability_matrix"
    MODULE_PREFERENCES = "module_preferences"
    TEXT = "text"
    LONG_TEXT = "long_text"
    NUMBER = "number"
    BOOLEAN = "boolean"
    DATE = "date"
    SINGLE_CHOICE = "single_choice"
    MULTI_CHOICE = "multi_choice"


class AvailabilityMatrix(Contract):
    days: tuple[str, ...] = ("MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY")
    day_parts: tuple[str, ...] = ("MORNING", "AFTERNOON")
    values: tuple[str, ...] = ("Preferred", "Available", "Not Preferred", "Not Available")


class Question(Contract):
    id: QuestionId = Field(default_factory=lambda: "custom." + uuid4().hex)
    kind: QuestionKind = QuestionKind.CUSTOM
    label: Label
    help_text: Description = ""
    field_key: QuestionId | None = None
    field_type: FieldType
    required: StrictBool = False
    choices: tuple[Label, ...] = Field(default=(), max_length=100)
    minimum: Annotated[float, Field(strict=True)] | None = None
    maximum: Annotated[float, Field(strict=True)] | None = None
    matrix: AvailabilityMatrix | None = None
    module_reference: Literal["offering_id"] | None = None

    @model_validator(mode="after")
    def coherent_field(self) -> Self:
        if self.kind is QuestionKind.LOCKED:
            if self.field_key is None:
                raise ValueError("Locked questions require their scheduler field key.")
        elif self.field_key is not None or self.field_type in {FieldType.AVAILABILITY, FieldType.MODULE_PREFERENCES}:
            raise ValueError("Only registered locked questions may declare scheduler fields.")
        choice = self.field_type in {FieldType.SINGLE_CHOICE, FieldType.MULTI_CHOICE}
        if choice != bool(self.choices) or len(set(self.choices)) != len(self.choices):
            raise ValueError("Choice questions require distinct nonempty choices; other fields cannot declare choices.")
        if (self.minimum is not None or self.maximum is not None) and self.field_type is not FieldType.NUMBER:
            raise ValueError("Only numeric questions accept numeric bounds.")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("Numeric bounds must be ordered.")
        if (self.field_type is FieldType.AVAILABILITY) != (self.matrix is not None):
            raise ValueError("Only availability fields require a day-part matrix.")
        if (self.field_type is FieldType.MODULE_PREFERENCES) != (self.module_reference is not None):
            raise ValueError("Module preferences require an offering_id reference.")
        return self


Questions = Annotated[tuple[Question, ...], Field(max_length=100)]


class CreateTemplate(Contract):
    institution_id: UUID
    title: Label
    description: Description = ""
    preset_id: Literal["lecturer-teaching-preferences.v1"] | None = None
    questions: Questions = ()


class SaveTemplate(Contract):
    institution_id: UUID
    expected_revision: Revision
    title: Label
    description: Description = ""
    questions: Questions


class DuplicateTemplate(Contract):
    institution_id: UUID
    title: Label | None = None


class FormTemplate(Contract):
    id: UUID
    institution_id: UUID
    schema_version: Literal[1] = 1
    revision: Revision = 1
    status: Literal["DRAFT"] = "DRAFT"
    preset_id: Literal["lecturer-teaching-preferences.v1"] | None = None
    title: Label
    description: Description
    questions: Questions
    created_by: UUID
    updated_by: UUID
    created_at: datetime
    updated_at: datetime


class Preset(Contract):
    id: Literal["lecturer-teaching-preferences.v1"]
    schema_version: Literal[1] = 1
    title: Label
    description: Description
    questions: Questions


class TemplatePage(Contract):
    items: tuple[FormTemplate, ...]
    total: int
    page: int
    page_size: int
