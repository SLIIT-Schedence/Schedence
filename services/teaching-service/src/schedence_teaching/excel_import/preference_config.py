"""Explicit soft semantics for the inspected Department_Aligned preference columns."""

from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, model_validator

from schedence_teaching.preferences.contracts import (
    PreferenceConfirmation, PreferenceScope, TimeOfDay,
)
from schedence_teaching.schemas import Stream
from schedence_teaching.schemas.models import InputContract, RequiredText
from schedence_teaching.workload.period_contracts import Weekday


class SlotMeaning(StrEnum):
    PREFER = "PREFER"
    AVOID = "AVOID"
    NEUTRAL = "NEUTRAL"
    AVAILABILITY_ASSERTION = "AVAILABILITY_ASSERTION"


class PreferenceSlotColumn(InputContract):
    day: Weekday
    time_of_day: TimeOfDay


def _slot_columns():
    return {f"{day.value.title()}_{part.value.title()}": PreferenceSlotColumn(day=day, time_of_day=part)
            for day in Weekday for part in TimeOfDay}


class LecturerPreferenceMapping(InputContract):
    layout: Literal["department-aligned-preferences-v1"]
    source_reference: RequiredText
    # Deliberately absent in the real workbook: never fill these from execution date.
    scope: PreferenceScope | None = None
    stream: Stream | None = None
    confirmation_status: PreferenceConfirmation | None = None
    free_day_column: RequiredText = "Preferred_Free_Day"
    free_day_values: dict[RequiredText, Weekday | None] = Field(default_factory=lambda: {
        **{day.value.title(): day for day in Weekday}, "No Preference": None})
    slot_columns: dict[RequiredText, PreferenceSlotColumn] = Field(default_factory=_slot_columns)
    slot_values: dict[RequiredText, SlotMeaning] = Field(default_factory=lambda: {
        "Preferred": SlotMeaning.PREFER, "Not Preferred": SlotMeaning.AVOID,
        "Available": SlotMeaning.NEUTRAL, "Not Available": SlotMeaning.AVAILABILITY_ASSERTION})
    weekend_pattern_column: RequiredText = "Weekend_Work_Pattern"
    weekend_pattern_values: dict[RequiredText, set[Weekday]] = Field(default_factory=dict)
    weekend_mapping_confirmation_reference: RequiredText | None = None

    @model_validator(mode="after")
    def explicit_weekend_interpretation(self) -> Self:
        if bool(self.weekend_pattern_values) != (self.weekend_mapping_confirmation_reference is not None):
            raise ValueError("weekend work-pattern translations need a separate confirmation reference")
        if any(not days or days - {Weekday.SATURDAY, Weekday.SUNDAY} for days in self.weekend_pattern_values.values()):
            raise ValueError("weekend mappings must name Saturday and/or Sunday")
        headers = [self.free_day_column, self.weekend_pattern_column, *self.slot_columns]
        if len(set(headers)) != len(headers):
            raise ValueError("preference source fields must use distinct column headers")
        selectors = [(row.day, row.time_of_day) for row in self.slot_columns.values()]
        if len(set(selectors)) != len(selectors):
            raise ValueError("duplicate weekday/time-of-day source selectors")
        return self
