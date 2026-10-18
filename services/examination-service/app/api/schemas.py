"""Response models of the examination API (built from ``app.sources`` records)."""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.scheduling.configuration import SchedulingConfiguration
from app.seating.rules import MAX_PERCENTAGE, MIN_PERCENTAGE

T = TypeVar("T")


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ModuleOut(_Out):
    module_code: str
    module_name: str
    academic_year: int
    semester: int
    specializations: list[str]
    credits: int
    difficulty: int
    exam_type: str
    duration_minutes: int
    has_extra_reading_time: bool
    reading_time_minutes: int
    candidate_count: int


class StudentOut(_Out):
    student_id: str
    academic_year: int
    specialization: str


class RegistrationOut(_Out):
    student_id: str
    module_code: str
    registration_type: str


class StudentRegistrationOut(_Out):
    module_code: str
    registration_type: str


class StudentDetailOut(StudentOut):
    registrations: list[StudentRegistrationOut]


class VenueOut(_Out):
    venue_id: str
    venue_name: str
    venue_type: str
    building: str
    floor: int
    normal_capacity: int = Field(description="Full seating capacity")
    exam_seating_percentage: int = Field(description="Share of the full capacity usable in an exam (1-100)")
    exam_seating_rule: Literal["default", "venue"] = Field(description="Where the percentage comes from")
    exam_capacity: int = Field(description="Effective exam seats: floor(full capacity x percentage / 100)")
    available_for_exams: bool = Field(description="False when the effective exam capacity is 0")
    seed_exam_capacity: int = Field(description="Legacy stored value (always 50%); not used for scheduling")


class VenueBlockOut(_Out):
    venue_id: str
    block_date: dt.date
    start_time: dt.time
    end_time: dt.time
    source: str
    reason: str
    source_ref: str | None


class ExamPeriodExclusionOut(_Out):
    excluded_date: dt.date
    reason: str


class ExamPeriodOut(_Out):
    period_code: str
    period_name: str
    academic_year_label: str
    semester: int
    start_date: dt.date
    end_date: dt.date
    day_start_time: dt.time
    day_end_time: dt.time
    include_saturdays: bool
    include_sundays: bool
    exclusions: list[ExamPeriodExclusionOut]
    exam_days: list[dt.date]


# --- Examination seating settings --------------------------------------------------

Percentage = Annotated[int, Field(ge=MIN_PERCENTAGE, le=MAX_PERCENTAGE, description="Whole percentage 1-100")]
Version = Annotated[int, Field(ge=1, le=2_147_483_647)]
# 5-500 printable characters; no control characters, no leading/trailing spaces.
REASON_PATTERN = r"^[^\s\x00-\x1f\x7f](?:[^\x00-\x1f\x7f]*[^\s\x00-\x1f\x7f])?$"
REASON_MIN, REASON_MAX = 5, 500
Reason = Annotated[str, Field(min_length=REASON_MIN, max_length=REASON_MAX, pattern=REASON_PATTERN,
                              description="Why the rule changes (kept in the history)")]


class _In(BaseModel):
    # strict: 50.0, "50" or true are not accepted as a percentage.
    model_config = ConfigDict(extra="forbid", strict=True)


class SetDefaultSeatingIn(_In):
    percentage: Percentage
    reason: Reason
    expected_version: Version = Field(description="The default rule's version you last read (409 if it changed)")


class SetVenueSeatingIn(_In):
    percentage: Percentage
    reason: Reason
    expected_version: Version | None = Field(
        description="The override's version you last read; null to create a new override"
    )


class SeatingRuleOut(_Out):
    scope: Literal["default", "venue"]
    venue_id: str | None
    percentage: int
    version: int
    reason: str
    updated_by: UUID | None = Field(description="Identity Service user ID; null = set by a migration")
    updated_at: dt.datetime


class SeatingHistoryOut(_Out):
    history_id: int
    action: Literal["create", "update", "delete"]
    scope: Literal["default", "venue"]
    venue_id: str | None
    old_percentage: int | None
    new_percentage: int | None
    version: int
    changed_by: UUID | None
    changed_at: dt.datetime
    reason: str


class ExamSeatingSettingsOut(BaseModel):
    min_percentage: int = MIN_PERCENTAGE
    max_percentage: int = MAX_PERCENTAGE
    ruleset_version: int = Field(description="Changes whenever any rule changes")
    default: SeatingRuleOut
    venue_overrides: list[SeatingRuleOut]
    venue_count: int
    total_normal_capacity: int
    total_exam_capacity: int
    unavailable_venues: list[str] = Field(description="Venues whose effective exam capacity is 0")
    history: list[SeatingHistoryOut] = Field(description="Newest first")


class VenueSeatingChangeOut(_Out):
    venue_id: str
    normal_capacity: int
    old_percentage: int
    new_percentage: int
    old_exam_capacity: int
    new_exam_capacity: int
    available_for_exams: bool


class SeatingChangeOut(_Out):
    dry_run: bool
    changed: bool = Field(description="False when the rule already had this value (nothing saved)")
    action: Literal["create", "update", "delete"] | None
    scope: Literal["default", "venue"]
    venue_id: str | None
    old_percentage: int | None
    new_percentage: int | None
    version: int | None = Field(description="Rule version after the change (dry run: the version it would get)")
    history_id: int | None
    changed_by: UUID | None
    affected_venues: list[VenueSeatingChangeOut]
    newly_unavailable_venues: list[str]
    total_exam_capacity_before: int
    total_exam_capacity_after: int


# --- Examination scheduling settings -----------------------------------------------

SettingsVersion = Annotated[int, Field(ge=0, le=2_147_483_647)]


class SetSchedulingSettingsIn(_In):
    settings: dict[str, Any] = Field(
        description="The complete settings document (as returned by GET). Every section and key is required; "
                    "unknown keys are rejected. Hard rules H1-H11 cannot be configured.",
        examples=[SchedulingConfiguration().to_document()],
    )
    reason: Reason
    expected_version: SettingsVersion = Field(
        description="The settings version you last read (0 = nothing saved yet; 409 if it changed)"
    )


class SettingsProblemOut(_Out):
    rule: str
    message: str


class SchedulingSettingsHistoryOut(_Out):
    history_id: int
    action: Literal["create", "update", "repair"]
    version: int
    changed_fields: list[str]
    changed_by: UUID
    changed_at: dt.datetime
    reason: str


class SchedulingSettingsOut(_Out):
    institution_id: UUID
    source: Literal["defaults", "saved"]
    status: Literal["valid", "invalid"] = Field(
        description="invalid: the saved document fails validation; the scheduler will not use it until an "
                    "admin repairs it (PUT with replace_invalid=true)")
    problems: list[SettingsProblemOut] = Field(description="Why the saved document is invalid (empty when valid)")
    version: int
    format: str = Field(description="Format of the saved document (or of the defaults)")
    fingerprint: str | None = Field(description="Fingerprint of the valid configuration; null when invalid")
    reason: str | None
    updated_by: UUID | None
    updated_at: dt.datetime | None
    settings: dict[str, Any] = Field(description="The configuration; when invalid, the saved document as stored")
    mandatory_rules: list[str]
    history: list[SchedulingSettingsHistoryOut]


class SchedulingSettingsChangeOut(_Out):
    dry_run: bool
    changed: bool
    action: Literal["create", "update", "repair"] | None
    version: int
    history_id: int | None
    changed_fields: list[str]
    changed_by: UUID | None
    fingerprint: str
    settings: dict[str, Any]


class PageOut(_Out, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


class HealthOut(BaseModel):
    status: str
    service: str


class DatabaseHealthOut(BaseModel):
    status: str
    latency_ms: float | None
    error: str | None
