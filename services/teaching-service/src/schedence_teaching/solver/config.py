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
    resolved_policy: ResolvedInstitutionPolicy | None = None

    @model_serializer(mode='wrap')
    def historical_payload(self, handler):
        result = handler(self)
        if self.resolved_policy is None:
            result.pop('resolved_policy', None)
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
        return self
