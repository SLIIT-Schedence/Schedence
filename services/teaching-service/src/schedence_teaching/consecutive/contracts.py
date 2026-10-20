"""Consecutive-teaching assessment choices; no campus durations are defaulted."""

from enum import StrEnum
from datetime import date
from typing import Annotated, Literal, Self

from pydantic import Field, StrictBool, model_validator

from schedence_teaching.schemas.models import InputContract, RequiredText
from schedence_teaching.workload.period_contracts import PeriodProvenance


Minutes = Annotated[int, Field(strict=True, ge=0, le=1440)]
PositiveMinutes = Annotated[int, Field(strict=True, gt=0, le=1440)]
PenaltyWeight = Annotated[int, Field(strict=True, ge=0)]


class ConsecutiveCriterion(StrEnum):
    BACK_TO_BACK = "BACK_TO_BACK"
    SHORT_BREAK = "SHORT_BREAK"
    EXCESS_CONTINUOUS_MINUTES = "EXCESS_CONTINUOUS_MINUTES"


class AttendanceKind(StrEnum):
    LECTURER = "LECTURER"
    STUDENT_GROUP = "STUDENT_GROUP"


class ConfirmedMinimumBreak(InputContract):
    """Explicit supplied confirmation metadata, not independent university verification."""

    minutes: PositiveMinutes
    confirmation: Literal["CONFIRMED"]
    campus_policy_reference: RequiredText
    approval_reference: RequiredText
    applies_to: set[AttendanceKind] = Field(min_length=1)


class ConsecutivePolicy(InputContract):
    policy_reference: RequiredText
    week_reference: RequiredText
    provenance: PeriodProvenance
    effective_from: date | None = None
    effective_until: date | None = None
    score_lecturers: StrictBool = True
    score_student_groups: StrictBool = False
    soft_criteria: set[ConsecutiveCriterion] = Field(default_factory=set)
    preferred_minimum_break_minutes: Minutes | None = None
    maximum_preferred_continuous_minutes: PositiveMinutes | None = None
    back_to_back_penalty: PenaltyWeight | None = None
    short_break_minute_penalty: PenaltyWeight | None = None
    excess_continuous_minute_penalty: PenaltyWeight | None = None
    # Explicit optional compactness cost; no default idle-time objective.
    idle_gap_minute_penalty: PenaltyWeight | None = None
    idle_gap_policy_reference: RequiredText | None = None
    confirmed_hard_minimum_break: ConfirmedMinimumBreak | None = None

    @model_validator(mode="after")
    def explicit_penalty_selection(self) -> Self:
        if self.effective_until is not None and (self.effective_from is None or self.effective_until < self.effective_from):
            raise ValueError("effective_until requires an ordered effective_from date")
        if (self.idle_gap_minute_penalty is not None) != (self.idle_gap_policy_reference is not None):
            raise ValueError("optional idle-gap scoring requires an explicit weight and policy reference together")
        for criterion, weight in (
            (ConsecutiveCriterion.BACK_TO_BACK, self.back_to_back_penalty),
            (ConsecutiveCriterion.SHORT_BREAK, self.short_break_minute_penalty),
            (ConsecutiveCriterion.EXCESS_CONTINUOUS_MINUTES, self.excess_continuous_minute_penalty),
        ):
            if weight is not None and criterion not in self.soft_criteria:
                raise ValueError("a supplied penalty weight requires its criterion to be explicitly enabled")
        return self
