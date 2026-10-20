"""Fixed student lunch periods, separate from still-unconfirmed applicability."""

from datetime import date
from typing import Literal, Self

from pydantic import Field, model_validator, model_serializer

from schedence_teaching.schemas import Stream
from schedence_teaching.schemas.models import InputContract, RequiredText, PositiveInteger
from schedence_teaching.schemas.time import TimeWindow
from schedence_teaching.workload.period_contracts import PeriodProvenance


class StudentLunchPeriod(InputContract):
    academic_year: PositiveInteger
    window: TimeWindow
    # Absent means the original initial-campus contract, preserving old payloads.
    duration_minutes: PositiveInteger | None = None

    @model_serializer(mode='wrap')
    def historical_payload(self, handler):
        result = handler(self)
        if self.duration_minutes is None:
            result.pop('duration_minutes', None)
        return result

    @model_validator(mode="after")
    def continuous_hour(self) -> Self:
        if self.duration_minutes is None and self.academic_year not in (1, 2, 3, 4):
            raise ValueError('Legacy lunch requires study year 1–4; institution periods require explicit duration.')
        if self.window.end_minute - self.window.start_minute != (self.duration_minutes or 60):
            raise ValueError(f'student lunch must be one fixed continuous {self.duration_minutes or 60}-minute period')
        return self


class StudentLunchPolicy(InputContract):
    """Confirmation covers supplied periods, not unknown dates/streams or work credit.

    None applicability is unknown; an explicit empty set excludes every date or
    stream. Choosing dates/streams needs a referenced configuration, not a guess.
    """

    policy_reference: RequiredText
    week_reference: RequiredText
    provenance: PeriodProvenance
    period_confirmation: Literal["CONFIRMED"]
    year_periods: tuple[StudentLunchPeriod, ...] = Field(min_length=1)
    configured_academic_years: frozenset[PositiveInteger] | None = None
    applicable_dates: set[date] | None = None
    applicable_streams: set[Stream] | None = None
    applicability_reference: RequiredText | None = None

    @model_serializer(mode='wrap')
    def historical_payload(self, handler):
        result = handler(self)
        if self.configured_academic_years is None:
            result.pop('configured_academic_years', None)
        return result

    @model_validator(mode="after")
    def explicit_years_and_scope(self) -> Self:
        years = {row.academic_year for row in self.year_periods}
        expected = self.configured_academic_years if self.configured_academic_years is not None else {1, 2, 3, 4}
        if years != expected or len(years) != len(self.year_periods):
            raise ValueError('supply exactly one fixed lunch period for each configured study year' if
                self.configured_academic_years is not None else 'supply exactly one fixed lunch period for each study year 1–4')
        if self.configured_academic_years is not None and any(p.duration_minutes is None for p in self.year_periods):
            raise ValueError('Institution lunch periods require explicit durations.')
        if self.configured_academic_years is None and any(p.window.end_minute-p.window.start_minute != 60 for p in self.year_periods):
            raise ValueError('Legacy lunch periods must retain one continuous hour.')
        if (self.applicable_dates is not None or self.applicable_streams is not None) and self.applicability_reference is None:
            raise ValueError("explicit date/stream applicability requires an applicability_reference")
        return self


def confirmed_student_lunch_policy(*, policy_reference, week_reference, provenance,
        applicable_dates=None, applicable_streams=None, applicability_reference=None):
    """Current owner-confirmed campus periods; no applicability is defaulted.

    All identifiers and provenance are caller supplied. Generic policy contracts
    permit later explicitly versioned periods, without editing this confirmation.
    """
    return StudentLunchPolicy(policy_reference=policy_reference, week_reference=week_reference, provenance=provenance,
        period_confirmation="CONFIRMED", year_periods=[dict(academic_year=year,
            window=dict(start_minute=750 if year <= 2 else 810, end_minute=810 if year <= 2 else 870))
            for year in (1, 2, 3, 4)], applicable_dates=applicable_dates, applicable_streams=applicable_streams,
        applicability_reference=applicability_reference)
