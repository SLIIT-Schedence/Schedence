"""Explicit source mappings; no real workbook headers are assumed."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from schedence_teaching.schemas import ModuleOffering, StaffMember, TeachingTeamMember
from schedence_teaching.schemas.models import InputContract, RequiredText

TableKind = Literal["staff", "preferences", "offerings", "teaching_teams"]
PreferenceType = Literal["text", "integer", "number", "boolean"]
RECORD_MODELS = {
    "staff": StaffMember,
    "offerings": ModuleOffering,
    "teaching_teams": TeachingTeamMember,
}


class PreferenceField(InputContract):
    """A caller-named preference field with an explicit cell type."""

    column: RequiredText
    value_type: PreferenceType
    required: bool = True


class TableMapping(InputContract):
    kind: TableKind
    workbook: Path
    worksheet: RequiredText
    header_row: Annotated[int, Field(strict=True, ge=1)] = 1
    columns: dict[RequiredText, RequiredText]
    value_maps: dict[RequiredText, dict[RequiredText, RequiredText]] = Field(default_factory=dict)
    preference_fields: dict[RequiredText, PreferenceField] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_mapping(self) -> Self:
        if self.kind == "preferences":
            if set(self.columns) != {"staff_id"}:
                raise ValueError("preference columns must map exactly staff_id")
            if not self.preference_fields:
                raise ValueError("map at least one explicit preference field")
            if "staff_id" in self.preference_fields:
                raise ValueError("staff_id cannot also be a preference field")
            if self.value_maps:
                raise ValueError("value_maps apply only to staff, offerings, and teaching teams")
        else:
            model = RECORD_MODELS[self.kind]
            required = {name for name, field in model.model_fields.items() if field.is_required()}
            if not required.issubset(self.columns):
                missing = ", ".join(sorted(required - self.columns.keys()))
                raise ValueError(f"missing required model field mappings: {missing}")
            if self.columns.keys() - model.model_fields.keys():
                raise ValueError("columns contains unknown model fields")
            if self.preference_fields:
                raise ValueError("preference_fields applies only to preferences")
            allowed_maps = {"staff_type", "stream", "role"} & model.model_fields.keys()
            if self.value_maps.keys() - allowed_maps:
                raise ValueError("value_maps can only translate staff_type, stream, or role")
            if self.value_maps.keys() - self.columns.keys():
                raise ValueError("value_maps requires a mapped source column")
            for field_name, translations in self.value_maps.items():
                # Validate configured destinations against the existing enum contract.
                enum_type = model.model_fields[field_name].annotation
                if any(value not in {item.value for item in enum_type} for value in translations.values()):
                    raise ValueError(f"invalid value_maps destination for {field_name}")

        headers = list(self.columns.values()) + [field.column for field in self.preference_fields.values()]
        if len(headers) != len(set(headers)):
            raise ValueError("each field must map to a distinct source column")
        return self


class ImportConfig(InputContract):
    tables: Annotated[list[TableMapping], Field(min_length=1)]
