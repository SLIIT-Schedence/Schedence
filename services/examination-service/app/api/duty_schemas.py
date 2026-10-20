"""Request and response models of the Member 3 duty-allocation demo API.

Every response carries ``meta``: the active data source, the synthetic institution, the
snapshot time and the preview-only guarantee, so the frontend can always show where data
came from. Nothing in these models is a saved, approved or published record.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.duty_allocation.demo_settings import DemoSource
from app.duty_allocation.orchestration import AllocationMode
from app.duty_allocation.types import DutyRole

T = TypeVar("T")
Label = Literal["BASELINE", "FAIR"]
IdText = Annotated[str, Field(min_length=1, max_length=64)]


class _Out(BaseModel):
    model_config = ConfigDict(use_enum_values=True)


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- Common -------------------------------------------------------------------------------------------

class MetaOut(_Out):
    data_source: DemoSource
    source_description: str
    synthetic: bool = True
    institution_id: UUID = Field(description="The synthetic m3_demo institution; never the real institution.")
    period_code: str | None = None
    snapshot_loaded_at: dt.datetime
    preview_only: bool = Field(True, description="Nothing is saved, approved, published or applied.")


class ViolationOut(_Out):
    constraint: str
    message: str


class DemoPage(_Out, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int
    meta: MetaOut


class DemoStatusOut(_Out):
    source: DemoSource
    enabled: bool
    problem_code: str | None = None
    problem: str | None = None
    missing_settings: list[str]
    institution_id: UUID
    default_period_code: str
    app_env: str
    snapshot_loaded: bool
    safeguards: list[str]
    persistence: str = "NOT_IMPLEMENTED: roster approval, publication and saved swaps are not available."


# --- Periods and sessions -----------------------------------------------------------------------------

class PeriodOut(_Out):
    period_code: str
    period_name: str | None
    academic_year_label: str | None
    semester: int | None
    start_date: dt.date
    end_date: dt.date
    day_start_time: dt.time
    day_end_time: dt.time
    include_saturdays: bool
    include_sundays: bool
    session_count: int


class PeriodsOut(_Out):
    items: list[PeriodOut]
    meta: MetaOut


class RoomOut(_Out):
    venue_id: str
    venue_name: str | None
    venue_type: str | None
    building: str | None
    students: int


class SessionOut(_Out):
    session_id: str
    module_code: str
    module_name: str | None
    period_code: str
    date: dt.date
    start: dt.datetime
    end: dt.datetime
    duration_minutes: int
    students: int
    rooms: list[RoomOut]
    publication_status: str | None = Field(None, description="Status stored in m3_demo; nothing here is published.")


class SessionsOut(_Out):
    items: list[SessionOut]
    total: int
    meta: MetaOut


# --- Staff --------------------------------------------------------------------------------------------

class WorkloadOut(_Out):
    staff_id: str
    staff_number: str | None
    status: Literal["COMPUTED", "NOT_COMPUTED", "MISSING_RESPONSE", "NOT_LOADED"]
    hours_per_week: float | None = Field(None, description="Only for COMPUTED; never estimated otherwise.")
    note: str = ""


class StaffOut(_Out):
    staff_id: str
    staff_number: str
    full_name: str | None
    academic_rank: str | None
    eligible_roles: list[str]
    eligible: bool
    exclusion_reason: str | None = None


class WindowOut(_Out):
    start: dt.datetime
    end: dt.datetime
    reason: str = ""


class PreferenceOut(_Out):
    day: dt.date
    kind: str


class RestrictionOut(_Out):
    module_code: str | None
    session_id: str | None
    role: str | None
    reason: str


class HistoryOut(_Out):
    prior_duties: int
    prior_minutes: int


class DutyLimitsOut(_Out):
    max_duties_per_day: int | None
    max_duties_per_period: int | None
    minimum_gap_minutes: int
    source: str


class ConstraintsOut(_Out):
    staff_id: str
    unavailability: list[WindowOut]
    availability: list[WindowOut]
    preferences: list[PreferenceOut]
    restrictions: list[RestrictionOut]
    history: HistoryOut | None
    limits: DutyLimitsOut
    notes: list[str]


class StaffDetailOut(StaffOut):
    workload: WorkloadOut | None
    constraints: ConstraintsOut | None
    meta: MetaOut


class WorkloadPageOut(DemoPage[WorkloadOut]):
    counts: dict[str, int]


class ConstraintsResponse(ConstraintsOut):
    meta: MetaOut


# --- Requirements -------------------------------------------------------------------------------------

class RequirementOut(_Out):
    session_id: str
    venue_id: str
    module_code: str
    students: int
    supervisors: int
    invigilators: int


class RequirementsOut(_Out):
    period_code: str
    policy_source: str
    complete: bool
    items: list[RequirementOut]
    total_supervisors: int
    total_invigilators: int
    violations: list[ViolationOut]
    stored_example_mismatches: list[str]
    meta: MetaOut


# --- Allocation ---------------------------------------------------------------------------------------

class AllocationRequest(_In):
    period_code: Annotated[str | None, Field(pattern=r"^[A-Z0-9-]{1,32}$")] = None
    time_limit_seconds: Annotated[float | None, Field(gt=0, le=600, description="Solver limit; capped by the server.")] = None


class AssignmentOut(_Out):
    session_id: str
    venue_id: str
    venue_name: str | None
    module_code: str | None
    date: dt.date | None
    start: dt.datetime | None
    end: dt.datetime | None
    staff_id: str
    staff_number: str | None
    staff_name: str | None
    role: str


class StaffLoadOut(_Out):
    staff_id: str
    staff_number: str | None
    staff_name: str | None
    duties: int
    total_load: int


class FairnessMetricsOut(_Out):
    min_load: int
    max_load: int
    mean_load: float
    std_load: float
    gini: float = Field(description="0 means perfectly equal total loads.")
    max_duties: int
    min_duties: int
    staff_without_duties: int
    supervisor_duties_range: int
    back_to_back_pairs: int
    preference_breaches: int
    staff_missing_admin_hours: list[str]
    per_staff: list[StaffLoadOut]


class RosterOut(_Out):
    label: Label
    solver_status: str
    assignments: list[AssignmentOut]
    metrics: FairnessMetricsOut | None
    revalidation_violations: list[ViolationOut]
    independently_valid: bool | None
    diagnostics: list[str]
    wall_time_seconds: float


class ComparisonOut(_Out):
    fairer_by_gini: Literal["FAIR", "BASELINE", "TIE"]
    gini_baseline: float
    gini_fair: float
    std_load_baseline: float
    std_load_fair: float
    load_range_baseline: int
    load_range_fair: int
    back_to_back_baseline: int
    back_to_back_fair: int
    preference_breaches_baseline: int
    preference_breaches_fair: int
    assignments_different: int


class DataAvailabilityOut(_Out):
    name: str
    status: str
    source: str
    records: int
    origin: str


class AllocationRunOut(_Out):
    run_id: str
    mode: AllocationMode
    status: str
    created_at: dt.datetime
    saved: bool = False
    approved: bool = False
    published: bool = False
    rosters: list[RosterOut]
    comparison: ComparisonOut | None
    fairness_complete: bool
    fairness_scope: list[str]
    data_availability: list[DataAvailabilityOut]
    violations: list[ViolationOut]
    diagnostics: list[str]
    warnings: list[str]
    requirement_mismatches: list[str]
    example_roster_violations: list[ViolationOut]
    meta: MetaOut


class FairnessOut(_Out):
    run_id: str
    status: str
    baseline: FairnessMetricsOut | None
    fair: FairnessMetricsOut | None
    comparison: ComparisonOut | None
    fairness_complete: bool
    fairness_scope: list[str]
    notes: list[str]
    meta: MetaOut


class RosterResponse(_Out):
    run_id: str
    status: str
    roster: RosterOut | None
    meta: MetaOut


# --- Validation, swaps, replacements -----------------------------------------------------------------

class AssignmentRef(_In):
    session_id: IdText
    venue_id: IdText
    staff_id: UUID
    role: DutyRole


class ValidationRequest(_In):
    roster: Label | None = Field(None, description="Which roster of the run (default: FAIR if present).")
    assignments: Annotated[list[AssignmentRef] | None, Field(max_length=2000,
                           description="Optional manual roster to validate against the run's data instead.")] = None


class ValidationOut(_Out):
    run_id: str
    roster: Label
    source: Literal["RUN_ROSTER", "SUBMITTED_ROSTER"]
    valid: bool
    assignments_checked: int
    violations: list[ViolationOut]
    meta: MetaOut


class SwapPreviewRequest(_In):
    roster: Label | None = None
    first: AssignmentRef
    second: AssignmentRef


class ReplacementPreviewRequest(_In):
    roster: Label | None = None
    duty: AssignmentRef
    reason: Annotated[str, Field(min_length=1, max_length=500)]
    unavailable_from: dt.datetime | None = None
    unavailable_until: dt.datetime | None = None
    preferred_staff_id: UUID | None = None

    @model_validator(mode="after")
    def _window(self) -> ReplacementPreviewRequest:
        if (self.unavailable_from is None) != (self.unavailable_until is None):
            raise ValueError("unavailable_from and unavailable_until must be given together")
        if self.unavailable_from is not None and self.unavailable_until <= self.unavailable_from:
            raise ValueError("unavailable_until must be after unavailable_from")
        return self


class CandidateOut(_Out):
    staff_id: str
    staff_number: str
    staff_name: str | None
    duties_after: int
    objective: int | None
    metrics: FairnessMetricsOut | None


class RejectedCandidateOut(_Out):
    staff_id: str
    staff_number: str
    staff_name: str | None
    reasons: list[str]


class ChangePreviewOut(_Out):
    run_id: str
    roster: Label
    kind: Literal["SWAP", "REPLACEMENT"]
    status: str
    applied: bool = False
    removed: list[AssignmentOut]
    added: list[AssignmentOut]
    proposed_roster: list[AssignmentOut] | None
    candidates: list[CandidateOut]
    rejected: list[RejectedCandidateOut]
    violations: list[ViolationOut]
    independent_check_violations: list[ViolationOut] | None
    reasons: list[str]
    original_metrics: FairnessMetricsOut | None
    proposed_metrics: FairnessMetricsOut | None
    original_objective: int | None
    proposed_objective: int | None
    meta: MetaOut

    @field_validator("applied")
    @classmethod
    def _never_applied(cls, value: bool) -> bool:
        if value:
            raise ValueError("previews are never applied")
        return value


# --- Seating ------------------------------------------------------------------------------------------

class SeatingRequest(_In):
    period_code: Annotated[str | None, Field(pattern=r"^[A-Z0-9-]{1,32}$")] = None


class RoomSeatingOut(_Out):
    session_id: str
    venue_id: str
    venue_type: str
    full_capacity: int
    capacity_percentage: int | None
    exam_capacity: int | None
    usable_seats: int
    students: int
    seat_limit: int | None


class SeatOut(_Out):
    student_id: str
    module_code: str
    seat_number: int
    seat_label: str
    seat_verified: bool


class SeatingRunOut(_Out):
    seating_id: str
    status: str
    created_at: dt.datetime
    seat_state: Literal["SYNTHETIC", "PROVISIONAL", "VERIFIED", "NONE"]
    all_seats_verified: bool
    synthetic_halls: list[str]
    provisional_halls: list[str]
    capacity_rules_source: str | None
    assignment_count: int
    rooms: list[RoomSeatingOut]
    violations: list[ViolationOut]
    diagnostics: list[str]
    example_problems: list[str]
    saved: bool = False
    approved: bool = False
    published: bool = False
    meta: MetaOut


class RoomSeatsOut(_Out):
    seating_id: str
    session_id: str
    venue_id: str
    seat_state: Literal["SYNTHETIC", "PROVISIONAL", "VERIFIED", "NONE"]
    students: int
    seats: list[SeatOut]
    meta: MetaOut
