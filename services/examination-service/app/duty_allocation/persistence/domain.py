"""Records and rules of the saved duty rosters, change requests and seating drafts.

Fairness-Aware Staff Allocation for Examination Duties: persistence layer.

Roster lifecycle::

    DRAFT -> VALIDATED -> APPROVED -> PUBLISHED
    DRAFT | VALIDATED | APPROVED -> REJECTED          (a rejected roster is revised, never edited)
    PUBLISHED -> SUPERSEDED                            (only when its revision is published)

Plain frozen dataclasses: no SQLAlchemy and no web types, so the rules can be tested on their own.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class RosterStatus(str, Enum):
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    APPROVED = "APPROVED"
    PUBLISHED = "PUBLISHED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"


WORKING = (RosterStatus.DRAFT, RosterStatus.VALIDATED, RosterStatus.APPROVED)  # at most one per period
ALLOWED_TRANSITIONS: dict[RosterStatus, frozenset[RosterStatus]] = {
    RosterStatus.DRAFT: frozenset({RosterStatus.VALIDATED, RosterStatus.REJECTED}),
    RosterStatus.VALIDATED: frozenset({RosterStatus.VALIDATED, RosterStatus.APPROVED, RosterStatus.REJECTED}),
    RosterStatus.APPROVED: frozenset({RosterStatus.PUBLISHED, RosterStatus.VALIDATED, RosterStatus.REJECTED}),
    RosterStatus.PUBLISHED: frozenset({RosterStatus.SUPERSEDED}),
    RosterStatus.REJECTED: frozenset(),
    RosterStatus.SUPERSEDED: frozenset(),
}


def can_transition(old: RosterStatus, new: RosterStatus) -> bool:
    return new in ALLOWED_TRANSITIONS[old]


class ChangeKind(str, Enum):
    SWAP = "SWAP"
    REPLACEMENT = "REPLACEMENT"


class ChangeStatus(str, Enum):
    REQUESTED = "REQUESTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    APPLIED = "APPLIED"
    CANCELLED = "CANCELLED"
    STALE = "STALE"  # the roster changed after the request was made


OPEN_CHANGE = (ChangeStatus.REQUESTED, ChangeStatus.APPROVED)


class SeatingDraftStatus(str, Enum):
    DRAFT = "DRAFT"
    SUPERSEDED = "SUPERSEDED"


SEAT_STATES = ("SYNTHETIC", "PROVISIONAL", "VERIFIED")


@dataclass(frozen=True)
class AssignmentRecord:
    session_id: str
    venue_id: str
    staff_id: str  # academic-data Staff.id (UUID text)
    role: str  # SUPERVISOR | INVIGILATOR
    staff_number: str | None
    module_code: str | None
    starts_at: dt.datetime
    ends_at: dt.datetime

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.session_id, self.venue_id, self.staff_id, self.role)


@dataclass(frozen=True)
class RosterRecord:
    roster_id: uuid.UUID
    institution_id: uuid.UUID  # the examination institution in which the caller's permissions were checked
    period_code: str
    version: int
    status: RosterStatus
    row_version: int  # optimistic-lock counter; every change adds 1
    algorithm_mode: str  # BASELINE | FAIR | COMPARE (the mode of the generating run)
    roster_label: str  # BASELINE | FAIR (which roster of the run was saved)
    timetable_reference: str  # a verified published reference only when timetable_verified
    timetable_fingerprint: str
    timetable_verified: bool
    input_digest: str  # hash of the timetable, staff, requirements and constraints used
    data_source: str
    synthetic: bool
    fairness_metrics: dict[str, Any]
    validation: dict[str, Any]
    created_by: uuid.UUID
    created_at: dt.datetime
    updated_at: dt.datetime
    parent_roster_id: uuid.UUID | None = None
    published_timetable_reference: str | None = None
    validated_by: uuid.UUID | None = None
    validated_at: dt.datetime | None = None
    approved_by: uuid.UUID | None = None
    approved_at: dt.datetime | None = None
    published_by: uuid.UUID | None = None
    published_at: dt.datetime | None = None
    rejected_by: uuid.UUID | None = None
    rejected_at: dt.datetime | None = None
    rejection_reason: str | None = None


@dataclass(frozen=True)
class EventRecord:
    event_id: int
    roster_id: uuid.UUID
    action: str
    from_status: str | None
    to_status: str | None
    actor_id: uuid.UUID
    at: dt.datetime
    reason: str | None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ChangeRequestRecord:
    request_id: uuid.UUID
    roster_id: uuid.UUID
    institution_id: uuid.UUID
    kind: ChangeKind
    status: ChangeStatus
    base_row_version: int  # the roster version the proposal was validated against
    removed: tuple[AssignmentRecord, ...]
    added: tuple[AssignmentRecord, ...]
    reason: str
    parameters: dict[str, Any]  # what is needed to re-run the proposal (window, preferred staff)
    summary: dict[str, Any]  # proposal status, objective and fairness metrics before / after
    requested_by: uuid.UUID
    requested_at: dt.datetime
    decided_by: uuid.UUID | None = None
    decided_at: dt.datetime | None = None
    decision_reason: str | None = None
    applied_by: uuid.UUID | None = None
    applied_at: dt.datetime | None = None


@dataclass(frozen=True)
class SeatRecord:
    session_id: str
    venue_id: str
    student_id: str
    module_code: str
    logical_seat_number: int  # from the timetable (Member 2's logical seat)
    physical_seat_number: int  # provisional position in the hall layout
    physical_seat_label: str
    verification_status: str  # SYNTHETIC | PROVISIONAL | VERIFIED (never VERIFIED without a verified layout)


@dataclass(frozen=True)
class SeatingDraftRecord:
    draft_id: uuid.UUID
    institution_id: uuid.UUID
    period_code: str
    version: int
    status: SeatingDraftStatus
    timetable_reference: str
    data_source: str
    synthetic: bool
    seat_state: str
    capacity_rules_source: str
    summary: dict[str, Any]
    created_by: uuid.UUID
    created_at: dt.datetime


class PersistenceConflict(Exception):
    """A uniqueness or optimistic-lock rule was hit; nothing was changed."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        self.code, self.message, self.details = code, message, details or {}
        super().__init__(message)


def assignment_to_json(a: AssignmentRecord) -> dict[str, Any]:
    return {"session_id": a.session_id, "venue_id": a.venue_id, "staff_id": a.staff_id, "role": a.role,
            "staff_number": a.staff_number, "module_code": a.module_code,
            "starts_at": a.starts_at.isoformat(), "ends_at": a.ends_at.isoformat()}


def assignment_from_json(data: dict[str, Any]) -> AssignmentRecord:
    return AssignmentRecord(data["session_id"], data["venue_id"], data["staff_id"], data["role"], data.get("staff_number"),
                            data.get("module_code"), dt.datetime.fromisoformat(data["starts_at"]),
                            dt.datetime.fromisoformat(data["ends_at"]))
