"""Bounded query/response contracts; no persistence payloads or registration rosters."""

from datetime import date
from typing import Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from schedence_teaching.schemas import Stream
from schedence_teaching.persistence.lifecycle import TimetableStatus
from schedence_teaching.generation.contracts import OptimizationSummaryOut


class TimetableQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    institution_id: UUID | None = None
    academic_year: int | None = Field(default=None, ge=1, le=4)
    semester: int | None = Field(default=None, ge=1, le=2)
    stream: Stream | None = None
    specialization: str | None = Field(default=None, min_length=1, max_length=100)
    student_group: str | None = Field(default=None, min_length=1, max_length=200)
    lecturer: str | None = Field(default=None, min_length=1, max_length=200)
    venue: str | None = Field(default=None, min_length=1, max_length=200)
    lifecycle_status: TimetableStatus | None = None
    page: int = Field(default=1, ge=1, le=100_000)
    page_size: int = Field(default=20, ge=1, le=100)


class ScopeOut(BaseModel):
    academic_year: int
    semester: int
    stream: str
    specialization: str
    student_groups: list[str]
    scope_status: str


class IssueOut(BaseModel):
    code: str
    severity: str
    scope_key: str | None


class CalendarOut(BaseModel):
    label: str
    week_reference: str
    week_start: date
    week_end: date
    timezone: str
    synthetic_data: bool
    sha256: str
    teaching_windows: list[dict]


class TimetableOut(BaseModel):
    institution_id: UUID
    version_id: UUID
    series_id: UUID
    version_number: int
    lifecycle_status: str
    storage_status: str
    solver_status: str
    validation_status: str
    academic_complete: bool
    matched_occurrences: int
    scopes: list[ScopeOut]
    calendar: CalendarOut
    input_snapshot_id: UUID | None = None
    policy_version: str | None = None
    policy_version_kind: str | None = None
    policy_reference: str | None = None
    resolved_policy_digest: str | None = None
    institution_policy_revision: str | None = None
    policy_sources: list[dict[str, str]] | None = None
    unresolved_issues: list[IssueOut] | None = None
    full_scope_expected_occurrences: int | None = None


class LecturerOut(BaseModel):
    staff_id: str
    staff_name: str


class VenueOut(BaseModel):
    venue_id: str
    venue_name: str
    venue_type: str
    capacity: int
    building: str | None
    floor: str | None
    lab_specialization: str | None


class CoverageOut(BaseModel):
    occurrence_id: str
    requirement_id: str
    offering_id: str
    offering_identifier_status: str
    module_code: str
    module_name: str
    group_id: str
    session_type: str
    duration_minutes: int
    academic_year: int
    semester: int
    stream: str
    specialization: str


class SessionOut(BaseModel):
    delivery_reference: str
    teaching_date: date
    day: str
    start_minute: int
    end_minute: int
    start_time: str
    end_time: str
    delivery_mode: str
    lecturer: LecturerOut
    venue: VenueOut | None
    coverage: list[CoverageOut]


class ValidationOut(BaseModel):
    version_id: UUID
    status: str
    expected_occurrences: int
    covered_occurrences: int
    actual_deliveries: int
    unscheduled_occurrences: int
    hard_violations: int
    academic_complete: bool
    unresolved_issues: list[IssueOut]
    blocked_scopes: list[dict]
    research_pending: bool
    experimental_calendar: bool
    # Stored P3.1 stage history of the validated run, when it used a combined objective plan.
    optimization: OptimizationSummaryOut | None = None


T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
