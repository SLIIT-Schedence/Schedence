"""Explicit administrative/research permissions and periods, without solver bookings."""

from datetime import date
from enum import StrEnum
from typing import Annotated, Self

from pydantic import ConfigDict, Field, StrictBool, model_validator

from schedence_teaching.schemas.models import InputContract, PositiveInteger, RequiredText
from schedence_teaching.schemas.time import TimeWindow


class Weekday(StrEnum):
    MONDAY = "MONDAY"
    TUESDAY = "TUESDAY"
    WEDNESDAY = "WEDNESDAY"
    THURSDAY = "THURSDAY"
    FRIDAY = "FRIDAY"
    SATURDAY = "SATURDAY"
    SUNDAY = "SUNDAY"


class PeriodApprovalStatus(StrEnum):
    PROPOSED = "PROPOSED"
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class PeriodProtection(StrEnum):
    HARD = "HARD"
    SOFT = "SOFT"


class PeriodProvenance(InputContract):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source: RequiredText
    reference: RequiredText
    source_record_id: RequiredText | None = None


class EffectivePeriod(InputContract):
    effective_from: date
    effective_until: date | None = None

    @model_validator(mode="after")
    def date_order(self) -> Self:
        if self.effective_until is not None and self.effective_until < self.effective_from:
            raise ValueError("effective_until must not precede effective_from")
        return self


class DateSelector(EffectivePeriod):
    on_date: date | None = None
    weekday: Weekday | None = None

    @model_validator(mode="after")
    def explicit_date_selector(self) -> Self:
        if (self.on_date is None) == (self.weekday is None):
            raise ValueError("supply exactly one specific date or recurring weekday")
        if self.on_date is not None and (self.on_date < self.effective_from or
                self.effective_until is not None and self.on_date > self.effective_until):
            raise ValueError("specific date is outside the reservation's effective period")
        return self


class PeriodAuthorization(InputContract):
    staff_id: RequiredText
    institution_id: RequiredText | None = None
    policy_reference: RequiredText
    status: PeriodApprovalStatus
    approval_reference: RequiredText | None = None
    provenance: PeriodProvenance

    @model_validator(mode="after")
    def explicit_approval(self) -> Self:
        if self.status is PeriodApprovalStatus.APPROVED and self.approval_reference is None:
            raise ValueError("approved records require explicit approval evidence")
        if self.status in {PeriodApprovalStatus.PROPOSED, PeriodApprovalStatus.PENDING} and self.approval_reference is not None:
            raise ValueError("pending/proposed records cannot claim approval")
        return self  # Cancelled/expired records may retain historical approval provenance.


class AppointmentApplicability(EffectivePeriod):
    """Caller-supplied ADS appointment and policy snapshot; IDs are never mapped implicitly."""
    appointment_id: RequiredText
    staff_id: RequiredText
    institution_id: RequiredText | None = None
    position_code: RequiredText
    policy_reference: RequiredText
    policy_effective_from: date
    policy_effective_until: date | None = None
    provenance: PeriodProvenance

    @model_validator(mode="after")
    def policy_dates(self) -> Self:
        if self.policy_effective_until is not None and self.policy_effective_until < self.policy_effective_from:
            raise ValueError("appointment policy effective dates are reversed")
        return self


class AdministrativeAggregateStatus(StrEnum):
    COMPUTED = "COMPUTED"
    NOT_COMPUTED = "NOT_COMPUTED"


class AdministrativePolicyResolution(EffectivePeriod):
    """Upstream-computed allocation; this service never repeats ADS aggregation rules."""
    staff_id: RequiredText
    institution_id: RequiredText | None = None
    appointment_ids: tuple[RequiredText, ...]
    status: AdministrativeAggregateStatus
    allocation_minutes: Annotated[int, Field(strict=True, ge=0)] | None = None
    rule: RequiredText | None = None
    resolution_reference: RequiredText | None = None
    provenance: PeriodProvenance

    @model_validator(mode="after")
    def explicit_resolution(self) -> Self:
        if len(set(self.appointment_ids)) != len(self.appointment_ids):
            raise ValueError("duplicate appointment IDs in resolution")
        if self.status is AdministrativeAggregateStatus.COMPUTED:
            if self.allocation_minutes is None or self.rule is None or self.resolution_reference is None:
                raise ValueError("computed administrative resolution needs allocation, rule and source reference")
        elif self.allocation_minutes is not None:
            raise ValueError("unresolved policy must not invent an aggregate allocation")
        return self


class AdministrativeTimeReservation(DateSelector, PeriodAuthorization):
    reservation_id: RequiredText
    week_reference: RequiredText | None = None
    appointment_id: RequiredText | None = None
    window: TimeWindow
    planned_duration_minutes: PositiveInteger
    protection: PeriodProtection

    @model_validator(mode="after")
    def supplied_duration(self) -> Self:
        if self.planned_duration_minutes != self.window.end_minute - self.window.start_minute:
            raise ValueError("planned administrative duration must equal its supplied time window")
        return self


class ResearchDayPolicy(EffectivePeriod, PeriodAuthorization):
    allow_full_day: StrictBool
    allow_partial_day: StrictBool
    allow_hard_protection: StrictBool
    allowed_weekdays: set[Weekday] | None = None


class ResearchDayReservation(DateSelector, PeriodAuthorization):
    reservation_id: RequiredText
    week_reference: RequiredText | None = None
    full_day: StrictBool
    windows: tuple[TimeWindow, ...] = ()
    protection: PeriodProtection

    @model_validator(mode="after")
    def explicit_research_windows(self) -> Self:
        if self.full_day and self.windows or not self.full_day and not self.windows:
            raise ValueError("full-day protection has no partial windows; partial days need explicit windows")
        ordered = sorted(self.windows, key=lambda row: row.start_minute)
        if any(first.end_minute > second.start_minute for first, second in zip(ordered, ordered[1:])):
            raise ValueError("research windows must not overlap")
        return self


class FlexibleAdministrativePolicy(EffectivePeriod, PeriodAuthorization):
    administrative_policy_reference: RequiredText
    flexible_scheduling_permitted: StrictBool
    allow_hard_protection: StrictBool
    # Unknown is preserved for Step 9A callers; booking requires an explicit choice.
    reservation_required: StrictBool | None = None


class EligibleAdministrativeWindow(TimeWindow):
    on_date: date


class FlexibleAdministrativeRequest(EffectivePeriod, PeriodAuthorization):
    request_id: RequiredText
    week_reference: RequiredText | None = None
    appointment_id: RequiredText | None = None
    planned_duration_minutes: PositiveInteger
    protection: PeriodProtection
    allow_split: StrictBool
    minimum_period_minutes: PositiveInteger | None = None
    eligible_windows: Annotated[tuple[EligibleAdministrativeWindow, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def explicit_splitting_and_windows(self) -> Self:
        if self.allow_split and (self.minimum_period_minutes is None or self.minimum_period_minutes > self.planned_duration_minutes):
            raise ValueError("split scheduling needs an explicit minimum no larger than the planned duration")
        if not self.allow_split and self.minimum_period_minutes is not None:
            raise ValueError("minimum period applies only to explicitly allowed split scheduling")
        if any(row.on_date < self.effective_from or self.effective_until is not None and row.on_date > self.effective_until
               for row in self.eligible_windows):
            raise ValueError("eligible windows must be within request effective dates")
        return self


class WorkPeriodInput(InputContract):
    week_reference: RequiredText
    week_start: date
    week_end: date
    staff_institutions: dict[RequiredText, RequiredText] = Field(default_factory=dict)
    appointments: list[AppointmentApplicability] = Field(default_factory=list)
    administrative_resolutions: list[AdministrativePolicyResolution] = Field(default_factory=list)
    administrative_periods: list[AdministrativeTimeReservation] = Field(default_factory=list)
    research_policies: list[ResearchDayPolicy] = Field(default_factory=list)
    research_reservations: list[ResearchDayReservation] = Field(default_factory=list)
    flexible_policies: list[FlexibleAdministrativePolicy] = Field(default_factory=list)
    flexible_requests: list[FlexibleAdministrativeRequest] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_records_and_week(self) -> Self:
        if not 0 <= (self.week_end - self.week_start).days <= 6:
            raise ValueError("academic week must fit within seven consecutive calendar days")
        for rows, field in ((self.appointments, "appointment_id"),):
            ids = [getattr(row, field) for row in rows]
            if len(set(ids)) != len(ids):
                raise ValueError(f"duplicate {field}")
        for policies in (self.research_policies, self.flexible_policies):
            identities = [(row.staff_id, row.policy_reference) for row in policies]
            if len(set(identities)) != len(identities):
                raise ValueError("duplicate staff/policy reference")
        ids = ([row.reservation_id for row in self.administrative_periods + self.research_reservations]
               + [row.request_id for row in self.flexible_requests])
        if len(set(ids)) != len(ids):
            raise ValueError("reservation/request identifiers must be unique")
        return self
