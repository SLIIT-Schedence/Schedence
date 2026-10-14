"""Soft lecturer input contracts, independent of mandatory teaching availability."""

from datetime import date
from enum import StrEnum
from typing import Annotated, Self

from pydantic import Field, StrictBool, model_validator

from schedence_teaching.schemas import Stream
from schedence_teaching.schemas.models import InputContract, RequiredText
from schedence_teaching.schemas.time import TimeWindow
from schedence_teaching.workload.period_contracts import PeriodProvenance, Weekday

NonnegativeInteger = Annotated[int, Field(strict=True, ge=0)]


class PreferenceCategory(StrEnum):
    WORKING_DAY = "WORKING_DAY"
    WEEKEND_DAY = "WEEKEND_DAY"
    TIME_OF_DAY = "TIME_OF_DAY"
    TEACHING_WINDOW = "TEACHING_WINDOW"


class PreferenceDirection(StrEnum):
    PREFER = "PREFER"
    AVOID = "AVOID"


class TimeOfDay(StrEnum):
    MORNING = "MORNING"
    AFTERNOON = "AFTERNOON"


class PreferenceConfirmation(StrEnum):
    UNCONFIRMED = "UNCONFIRMED"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"


class PreferenceScope(InputContract):
    week_reference: RequiredText | None = None
    effective_from: date | None = None
    effective_until: date | None = None

    @model_validator(mode="after")
    def explicit_applicability(self) -> Self:
        if self.week_reference is None and self.effective_from is None:
            raise ValueError("supply an academic week reference or effective_from; applicability is not invented")
        if self.effective_until is not None and (self.effective_from is None or self.effective_until < self.effective_from):
            raise ValueError("effective_until requires effective_from and must not precede it")
        return self


class PreferenceProvenance(PeriodProvenance):
    interpretation_reference: RequiredText | None = None
    workbook: RequiredText | None = None
    worksheet: RequiredText | None = None
    row: Annotated[int, Field(strict=True, ge=1)] | None = None
    column: RequiredText | None = None

    @model_validator(mode="after")
    def complete_cell_location(self) -> Self:
        values = (self.workbook, self.worksheet, self.row, self.column)
        if any(value is not None for value in values) and not all(value is not None for value in values):
            raise ValueError("Excel provenance requires workbook, worksheet, row and column together")
        return self


class PreferenceExpression(InputContract):
    """A staged, source-preserving expression; not schedulable until scoped."""

    preference_id: RequiredText
    alternative_group_id: RequiredText | None = None
    staff_id: RequiredText
    category: PreferenceCategory
    direction: PreferenceDirection
    days: set[Weekday] = Field(default_factory=set)
    time_window: TimeWindow | None = None
    time_of_day: TimeOfDay | None = None
    stream: Stream | None = None
    strength: RequiredText | None = None
    penalty_weight: NonnegativeInteger | None = None
    weight_reference: RequiredText | None = None
    confirmation_status: PreferenceConfirmation | None = None
    provenance: PreferenceProvenance

    @model_validator(mode="after")
    def compatible_shape(self) -> Self:
        if self.alternative_group_id is not None and self.direction is not PreferenceDirection.PREFER:
            raise ValueError("alternative groups represent preferred choices only; avoided selections remain separate")
        if (self.penalty_weight is None) != (self.weight_reference is None):
            raise ValueError("an explicit penalty weight needs its source reference, and vice versa")
        if self.category in {PreferenceCategory.WORKING_DAY, PreferenceCategory.WEEKEND_DAY}:
            if not self.days or self.time_window is not None or self.time_of_day is not None:
                raise ValueError("day preferences require days and no time selector")
            if self.category is PreferenceCategory.WEEKEND_DAY and self.days - {Weekday.SATURDAY, Weekday.SUNDAY}:
                raise ValueError("weekend preferences may select Saturday and/or Sunday")
        elif self.category is PreferenceCategory.TIME_OF_DAY:
            if self.time_of_day is None or self.time_window is not None:
                raise ValueError("time-of-day preferences require an explicit label, without an invented window")
        elif self.time_window is None or self.time_of_day is not None:
            raise ValueError("teaching-window preferences require a supplied window and no time-of-day label")
        return self

    def with_scope(self, scope: PreferenceScope):
        return LecturerPreference.model_validate({**self.model_dump(round_trip=True), "scope": scope})


class LecturerPreference(PreferenceExpression):
    scope: PreferenceScope


class LecturerPreferenceInput(InputContract):
    preferences: list[LecturerPreference] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_ids(self) -> Self:
        identities = [row.preference_id for row in self.preferences]
        if len(set(identities)) != len(identities):
            raise ValueError("preference identifiers must be unique; source records are never silently merged")
        alternatives = {}
        for row in self.preferences:
            if row.alternative_group_id is None:
                continue
            key = row.staff_id, row.alternative_group_id
            first = alternatives.setdefault(key, row)
            if any(getattr(row, name) != getattr(first, name) for name in (
                "category", "days", "scope", "stream", "strength", "penalty_weight", "weight_reference", "confirmation_status"
            )):
                raise ValueError("preferred alternatives must share category, applicability and weight policy")
        return self


class PreferenceCalendar(InputContract):
    """Explicit dates for scoring abstract solver days; no opening-hour assumptions."""

    week_reference: RequiredText
    week_start: date
    week_end: date
    day_dates: dict[RequiredText, date]
    synthetic_data: StrictBool = False

    @model_validator(mode="after")
    def valid_week(self) -> Self:
        if not 0 <= (self.week_end - self.week_start).days <= 6:
            raise ValueError("academic week must fit within seven consecutive dates")
        if len(set(self.day_dates.values())) != len(self.day_dates):
            raise ValueError("scheduling days must map to distinct dates")
        if any(not self.week_start <= value <= self.week_end for value in self.day_dates.values()):
            raise ValueError("all scheduling dates must lie within the supplied academic week")
        return self


class PreferenceWeightMode(StrEnum):
    UNWEIGHTED = "UNWEIGHTED"
    CONFIRMED_WEIGHTS = "CONFIRMED_WEIGHTS"
    SYNTHETIC_DEFAULTS = "SYNTHETIC_DEFAULTS"


class PreferenceScoringPolicy(InputContract):
    policy_reference: RequiredText
    weight_mode: PreferenceWeightMode = PreferenceWeightMode.UNWEIGHTED
    synthetic_default_weight: NonnegativeInteger | None = None
    time_of_day_windows: dict[TimeOfDay, TimeWindow] = Field(default_factory=dict)
    time_window_policy_reference: RequiredText | None = None

    @model_validator(mode="after")
    def explicit_scoring_assumptions(self) -> Self:
        if (self.weight_mode is PreferenceWeightMode.SYNTHETIC_DEFAULTS) != (self.synthetic_default_weight is not None):
            raise ValueError("synthetic defaults require an explicitly supplied weight and SYNTHETIC_DEFAULTS mode")
        if bool(self.time_of_day_windows) != (self.time_window_policy_reference is not None):
            raise ValueError("configured morning/afternoon windows require an explicit policy reference")
        return self
