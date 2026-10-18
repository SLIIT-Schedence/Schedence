"""Reporting filters and owner-supplied participation evidence, never write APIs."""
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from schedence_teaching.schemas import Stream
from schedence_teaching.schemas.time import TimeWindow
from schedence_teaching.workload.contracts import WorkloadBand, StaffAppointment


class WorkloadQuery(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    institution_id: UUID | None = None
    version_id: UUID
    academic_year: int | None = Field(default=None, ge=1, le=4)
    semester: int | None = Field(default=None, ge=1, le=2)
    stream: Stream | None = None
    lecturer: str | None = Field(default=None, min_length=1, max_length=200)
    academic_rank: WorkloadBand | None = None
    appointment: StaffAppointment | None = None
    module_code: str | None = Field(default=None, min_length=1, max_length=100)
    offering_id: str | None = Field(default=None, min_length=1, max_length=2000)


class WorkloadExportQuery(WorkloadQuery):
    export_format: Literal['pdf', 'csv'] = Field(default='pdf', alias='format')
    kind: Literal['summary', 'individual', 'detailed', 'assignments', 'targets', 'fairness'] = 'summary'


class VerifiedParticipation(BaseModel):
    """Trusted owner adapter input, not accepted from report requests.

    A row explicitly replaces that staff member's participation in one booking.
    Additional co-teachers require their own verified row. Missing primary
    participation falls back only to the actual saved responsible assignment.
    """
    model_config = ConfigDict(extra='forbid', frozen=True)
    institution_id: UUID
    version_id: UUID
    delivery_reference: str = Field(min_length=1)
    staff_id: str = Field(min_length=1)
    intervals: tuple[TimeWindow, ...] = Field(min_length=1)
    evidence_reference: str = Field(min_length=1)

    @model_validator(mode='after')
    def nonoverlapping_participation(self):
        ordered = sorted(self.intervals, key=lambda i: i.start_minute)
        if any(a.end_minute > b.start_minute for a,b in zip(ordered, ordered[1:])):
            raise ValueError('Duplicate or overlapping participation cannot be credited twice.')
        return self
