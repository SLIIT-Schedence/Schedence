"""Explicit scheduling availability; no campus-hour or weekend policy defaults."""

from __future__ import annotations

from typing import Annotated, Self

from pydantic import Field, model_validator, model_serializer
from schedence_teaching.policy.contracts import ResolvedInstitutionPolicy

from schedence_teaching.schemas import Stream
from schedence_teaching.schemas.models import InputContract, RequiredText
from schedence_teaching.schemas.time import MINUTES_PER_DAY, TimeWindow
from schedence_teaching.schemas import GroupMembershipEvidence
from .attendance_inputs import DeliveryCalendar
from .online_day_inputs import BatchOnlineDayPolicy, check_calendar
from .compactness_inputs import TeachingDayCompactnessConfig
from .daily_load_inputs import DailyLoadDistributionConfig
from .objective_stage_plan import ObjectiveStagePlan


class SchedulingDay(InputContract):
    day: RequiredText
    windows: dict[Stream, list[TimeWindow]]


class SolverConfig(InputContract):
    days: Annotated[tuple[SchedulingDay, ...], Field(min_length=1)]
    granularity_minutes: Annotated[int, Field(strict=True, gt=0, le=MINUTES_PER_DAY)]
    max_time_seconds: Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)]
    random_seed: Annotated[int, Field(strict=True, ge=0, le=2**31 - 1)] | None = None
    num_search_workers: Annotated[int, Field(strict=True, ge=1, le=32)] = 1
    lab_variables_first: Annotated[bool, Field(strict=True)] = False
    delivery_calendar: DeliveryCalendar | None = None
    attendance_memberships: tuple[GroupMembershipEvidence, ...] = ()
    attendance_required_group_ids: frozenset[RequiredText] = frozenset()
    # Opt-in P2.4 batch online days; omitted when unset so historical seals are unchanged.
    batch_online_days: BatchOnlineDayPolicy | None = None
    # Opt-in P2.1 soft objective; omitted when unset. No default minimum exists.
    teaching_day_compactness: TeachingDayCompactnessConfig | None = None
    # Opt-in P2.2 minimax daily-load objectives; omitted when unset. No daily cap or threshold.
    daily_load_distribution: DailyLoadDistributionConfig | None = None
    # Opt-in P3.1 shared lexicographic plan for combined objectives; omitted when unset.
    objective_stage_plan: ObjectiveStagePlan | None = None
    resolved_policy: ResolvedInstitutionPolicy | None = None

    @model_serializer(mode='wrap')
    def historical_payload(self, handler):
        result = handler(self)
        if self.resolved_policy is None:
            result.pop('resolved_policy', None)
        if self.batch_online_days is None:
            result.pop('batch_online_days', None)
        if self.teaching_day_compactness is None:
            result.pop('teaching_day_compactness', None)
        if self.daily_load_distribution is None:
            result.pop('daily_load_distribution', None)
        if self.objective_stage_plan is None:
            result.pop('objective_stage_plan', None)
        return result

    @model_validator(mode="after")
    def validate_days_and_alignment(self) -> Self:
        if len({day.day for day in self.days}) != len(self.days):
            raise ValueError("configured day identifiers must be unique")
        for day in self.days:
            for windows in day.windows.values():
                for window in windows:
                    if window.start_minute % self.granularity_minutes or window.end_minute % self.granularity_minutes:
                        raise ValueError("window boundaries must align with scheduling granularity")
        if self.delivery_calendar is not None and {(row.day, row.stream) for row in self.delivery_calendar.days} != {
                (day.day, stream) for day in self.days for stream in day.windows}:
            raise ValueError("Delivery calendar must cover every configured stream/day exactly.")
        if self.batch_online_days is not None:
            check_calendar(self)
        return self
