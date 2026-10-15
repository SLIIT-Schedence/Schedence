"""Response models of the examination API (built from ``app.sources`` records)."""

from __future__ import annotations

import datetime as dt
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

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
    exam_capacity: int = Field(description="Seats usable in an exam: floor(full capacity x 0.5)")


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
