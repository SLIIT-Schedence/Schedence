"""Models of the saved-roster API: Fairness-Aware Staff Allocation for Examination Duties.

Requests never carry a role or permission: authority comes only from the caller's verified
permissions (``extra="forbid"`` rejects any such field). Every request that changes state
may carry ``expected_version`` (the roster's ``row_version`` the caller saw); a mismatch is a
409, so a stale screen can never overwrite newer work.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.api.duty_schemas import COMPONENT_NAME, AssignmentRef, Label
from app.duty_allocation.demo_settings import PersistenceMode

T = TypeVar("T")
Version = Annotated[int | None, Field(ge=1, description="The row_version you loaded; a mismatch gives 409 STALE_ROSTER.")]
Reason = Annotated[str, Field(min_length=1, max_length=500)]
RunId = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]


class _Out(BaseModel):
    model_config = ConfigDict(use_enum_values=True)


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PersistenceInfo(_Out):
    mode: PersistenceMode
    durable: bool = Field(description="False for MEMORY: drafts are lost when the service restarts.")
    component: str = COMPONENT_NAME
    notice: str


class Page(_Out, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int
    persistence: PersistenceInfo


# --- Rosters ------------------------------------------------------------------------------------------

class SaveRosterRequest(_In):
    run_id: RunId
    roster: Label | None = Field(None, description="Which roster of the run to save (default: FAIR if present).")


class TransitionRequest(_In):
    expected_version: Version = None
    note: Annotated[str | None, Field(max_length=500)] = None


class ReasonRequest(_In):
    expected_version: Version = None
    reason: Reason


class RosterAssignmentOut(_Out):
    session_id: str
    venue_id: str
    staff_id: str
    staff_number: str | None
    role: str
    module_code: str | None
    starts_at: dt.datetime
    ends_at: dt.datetime


class RosterOut(_Out):
    roster_id: UUID
    period_code: str
    version: int
    status: str
    row_version: int
    algorithm_mode: str
    roster_label: str
    timetable_reference: str
    timetable_verified: bool
    published_timetable_reference: str | None
    data_source: str
    synthetic: bool
    parent_roster_id: UUID | None
    fairness_metrics: dict[str, Any]
    validation: dict[str, Any]
    created_by: UUID
    created_at: dt.datetime
    updated_at: dt.datetime
    validated_by: UUID | None
    validated_at: dt.datetime | None
    approved_by: UUID | None
    approved_at: dt.datetime | None
    published_by: UUID | None
    published_at: dt.datetime | None
    rejected_by: UUID | None
    rejected_at: dt.datetime | None
    rejection_reason: str | None
    assignment_count: int
    next_actions: list[str] = Field(description="Lifecycle actions that are possible now (permissions still apply).")
    can_be_published: bool = Field(description="False for synthetic demo data, which can never be published.")
    notice: str
    persistence: PersistenceInfo


class RosterDetailOut(RosterOut):
    assignments: list[RosterAssignmentOut]


class EventOut(_Out):
    event_id: int
    action: str
    from_status: str | None
    to_status: str | None
    actor_id: UUID
    at: dt.datetime
    reason: str | None
    details: dict[str, Any]


class HistoryOut(_Out):
    roster_id: UUID
    events: list[EventOut]
    persistence: PersistenceInfo


# --- Changes (swap / emergency replacement) -----------------------------------------------------------

class SwapChangeRequest(_In):
    expected_version: Version = None
    first: AssignmentRef
    second: AssignmentRef
    reason: Reason


class ReplacementChangeRequest(_In):
    expected_version: Version = None
    duty: AssignmentRef
    reason: Reason
    unavailable_from: dt.datetime | None = None
    unavailable_until: dt.datetime | None = None
    preferred_staff_id: UUID | None = None

    @model_validator(mode="after")
    def _window(self) -> ReplacementChangeRequest:
        if (self.unavailable_from is None) != (self.unavailable_until is None):
            raise ValueError("unavailable_from and unavailable_until must be given together")
        if self.unavailable_from is not None and self.unavailable_until <= self.unavailable_from:
            raise ValueError("unavailable_until must be after unavailable_from")
        return self


class DecisionRequest(_In):
    reason: Annotated[str | None, Field(max_length=500)] = None


class RejectDecisionRequest(_In):
    reason: Reason


class CandidateOut(_Out):
    staff_id: str
    staff_number: str
    staff_name: str | None
    duties_after: int
    objective: int | None
    metrics: dict[str, Any]


class RejectedCandidateOut(_Out):
    staff_id: str
    staff_number: str
    reasons: list[str]


class ChangePreviewOut(_Out):
    roster_id: UUID
    roster_version: int
    kind: Literal["SWAP", "REPLACEMENT"]
    status: str
    applied: bool = False
    removed: list[RosterAssignmentOut]
    added: list[RosterAssignmentOut]
    candidates: list[CandidateOut]
    rejected: list[RejectedCandidateOut]
    violations: list[dict[str, str]]
    independent_check_violations: list[dict[str, str]] | None
    reasons: list[str]
    original_metrics: dict[str, Any]
    proposed_metrics: dict[str, Any]
    notice: str = "PREVIEW ONLY: nothing was requested or changed."
    persistence: PersistenceInfo


class ChangeRequestOut(_Out):
    request_id: UUID
    roster_id: UUID
    kind: Literal["SWAP", "REPLACEMENT"]
    status: str
    base_row_version: int
    removed: list[RosterAssignmentOut]
    added: list[RosterAssignmentOut]
    reason: str
    summary: dict[str, Any]
    requested_by: UUID
    requested_at: dt.datetime
    decided_by: UUID | None
    decided_at: dt.datetime | None
    decision_reason: str | None
    applied_by: UUID | None
    applied_at: dt.datetime | None
    next_actions: list[str]
    notice: str
    persistence: PersistenceInfo


class ChangeAppliedOut(_Out):
    request: ChangeRequestOut
    roster: RosterOut


# --- Seating drafts -----------------------------------------------------------------------------------

class SeatOut(_Out):
    session_id: str
    venue_id: str
    student_id: str
    module_code: str
    logical_seat_number: int
    physical_seat_number: int
    physical_seat_label: str
    verification_status: str


class SeatingDraftOut(_Out):
    draft_id: UUID
    period_code: str
    version: int
    status: str
    timetable_reference: str
    data_source: str
    synthetic: bool
    seat_state: Literal["SYNTHETIC", "PROVISIONAL", "VERIFIED"]
    capacity_rules_source: str
    summary: dict[str, Any]
    created_by: UUID
    created_at: dt.datetime
    notice: str
    persistence: PersistenceInfo


class SeatingRoomOut(_Out):
    draft_id: UUID
    session_id: str
    venue_id: str
    seat_state: str
    students: int
    seats: list[SeatOut]
    persistence: PersistenceInfo
