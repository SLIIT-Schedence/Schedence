"""Immutable duty-allocation inputs and outputs, independent of CP-SAT and storage.

The scheduler's DraftTimetable is consumed as-is: each (session_id, venue_id) room in it
is one place that needs Supervisors and Invigilators. Staffing numbers are supplied per
room by the caller; this module does not derive them from candidate counts.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from enum import Enum

from app.scheduling.contracts import SchedulingValidationError, Violation, frozen_sequence, text_errors
from app.scheduling.types import DraftTimetable

# Shape and reference errors raised by duty-allocation constructors.
DUTY_INPUT = "D_INPUT"


def _raise(errors) -> None:
    if errors:
        raise SchedulingValidationError(tuple(errors))


def _texts(record, label: str, names, optional=()) -> list[Violation]:
    errors = [v for name in names for v in text_errors(getattr(record, name), f"{label}.{name}")]
    errors += [v for name in optional for v in text_errors(getattr(record, name), f"{label}.{name}", optional=True)]
    return [Violation(DUTY_INPUT, v.message) for v in errors]


def is_staff_uuid(value: object) -> bool:
    """Staff ids are academic-data Staff.id values in canonical lowercase UUID text."""
    if type(value) is not str:
        return False
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def _staff_id_errors(value: object, label: str) -> list[Violation]:
    return [] if is_staff_uuid(value) else [Violation(DUTY_INPUT, f"{label}.staff_id must be a lowercase UUID string.")]


def _window_errors(start: object, end: object, label: str) -> list[Violation]:
    if type(start) is not dt.datetime or type(end) is not dt.datetime:
        return [Violation(DUTY_INPUT, f"{label} start and end must be datetimes.")]
    if start.tzinfo is not None or end.tzinfo is not None:
        return [Violation(DUTY_INPUT, f"{label} times must be naive local times, like ExamSession.")]
    if start >= end:
        return [Violation(DUTY_INPUT, f"{label} must end after it starts.")]
    return []


class DutyRole(str, Enum):
    SUPERVISOR = "SUPERVISOR"
    INVIGILATOR = "INVIGILATOR"


@dataclass(frozen=True)
class StaffMember:
    staff_id: str  # academic-data Staff.id (UUID text): internal identity
    staff_number: str  # academic-data Staff.staff_number: display only
    full_name: str
    academic_rank: str | None = None  # Supervisor suitability only, via EligibilityPolicy
    eligible_roles: tuple[DutyRole, ...] = (DutyRole.SUPERVISOR, DutyRole.INVIGILATOR)

    def __post_init__(self) -> None:
        errors = _staff_id_errors(self.staff_id, "staff")
        errors += _texts(self, "staff", ("staff_number", "full_name"), optional=("academic_rank",))
        _raise(errors)
        roles = frozen_sequence(self.eligible_roles, "staff.eligible_roles", DutyRole)
        if not roles or len(set(roles)) != len(roles):
            _raise([Violation(DUTY_INPUT, f"staff {self.staff_id} eligible_roles must be non-empty and unique.")])
        object.__setattr__(self, "eligible_roles", roles)


@dataclass(frozen=True)
class StaffUnavailability:
    staff_id: str
    start: dt.datetime  # local calendar time, same convention as ExamSession
    end: dt.datetime
    reason: str = ""

    def __post_init__(self) -> None:
        errors = _staff_id_errors(self.staff_id, "unavailability")
        errors += _window_errors(self.start, self.end, f"unavailability for {self.staff_id}")
        if type(self.reason) is not str:
            errors.append(Violation(DUTY_INPUT, "unavailability.reason must be a string."))
        _raise(errors)

    def overlaps(self, start: dt.datetime, end: dt.datetime) -> bool:
        return self.start < end and start < self.end


@dataclass(frozen=True)
class StaffAvailability:
    """A window a staff member declared they can work. A staff member with any declared
    windows is only available inside them; one with none is available unless unavailable."""
    staff_id: str
    start: dt.datetime
    end: dt.datetime

    def __post_init__(self) -> None:
        _raise(_staff_id_errors(self.staff_id, "availability")
               + _window_errors(self.start, self.end, f"availability for {self.staff_id}"))

    def contains(self, start: dt.datetime, end: dt.datetime) -> bool:
        return self.start <= start and end <= self.end


@dataclass(frozen=True)
class RoomDutyRequirement:
    session_id: str
    venue_id: str
    supervisors: int
    invigilators: int

    def __post_init__(self) -> None:
        errors = _texts(self, "requirement", ("session_id", "venue_id"))
        for name in ("supervisors", "invigilators"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                errors.append(Violation(DUTY_INPUT, f"requirement.{name} must be a non-negative int."))
        _raise(errors)

    @property
    def room_key(self) -> tuple[str, str]:
        return self.session_id, self.venue_id

    def required(self, role: DutyRole) -> int:
        return self.supervisors if role is DutyRole.SUPERVISOR else self.invigilators


@dataclass(frozen=True)
class DutyAllocationInput:
    timetable: DraftTimetable
    staff: tuple[StaffMember, ...]
    requirements: tuple[RoomDutyRequirement, ...]
    unavailability: tuple[StaffUnavailability, ...] = ()
    availability: tuple[StaffAvailability, ...] = ()

    def __post_init__(self) -> None:
        if type(self.timetable) is not DraftTimetable:
            _raise([Violation(DUTY_INPUT, "timetable must be a DraftTimetable.")])
        for name, record in (("staff", StaffMember), ("requirements", RoomDutyRequirement),
                             ("unavailability", StaffUnavailability), ("availability", StaffAvailability)):
            object.__setattr__(self, name, frozen_sequence(getattr(self, name), name, record))

        errors = []
        staff_ids = [s.staff_id for s in self.staff]
        if len(set(staff_ids)) != len(staff_ids):
            errors.append(Violation(DUTY_INPUT, "staff_id values must be unique."))
        known_staff = set(staff_ids)
        for label, rows in (("unavailability", self.unavailability), ("availability", self.availability)):
            errors += [Violation(DUTY_INPUT, f"{label} references unknown staff {row.staff_id}.")
                       for row in rows if row.staff_id not in known_staff]

        rooms = self.room_keys()
        keys = [r.room_key for r in self.requirements]
        if len(set(keys)) != len(keys):
            errors.append(Violation(DUTY_INPUT, "requirements must have one row per (session_id, venue_id)."))
        errors += [Violation(DUTY_INPUT, f"requirement {k} is not a room in the timetable.")
                   for k in keys if k not in rooms]
        errors += [Violation(DUTY_INPUT, f"timetable room {k} has no duty requirement.")
                   for k in sorted(rooms - set(keys))]
        _raise(errors)

    def room_keys(self) -> set[tuple[str, str]]:
        return {(s.session_id, r.venue_id) for s in self.timetable.sessions for r in s.rooms}


@dataclass(frozen=True)
class DutyAssignment:
    session_id: str
    venue_id: str
    staff_id: str
    role: DutyRole

    def __post_init__(self) -> None:
        errors = _texts(self, "assignment", ("session_id", "venue_id")) + _staff_id_errors(self.staff_id, "assignment")
        if type(self.role) is not DutyRole:
            errors.append(Violation(DUTY_INPUT, "assignment.role must be a DutyRole."))
        _raise(errors)


class DutyAllocationStatus(str, Enum):
    FEASIBLE = "FEASIBLE"
    OPTIMAL = "OPTIMAL"
    INFEASIBLE = "INFEASIBLE"
    UNKNOWN = "UNKNOWN"
    MODEL_INVALID = "MODEL_INVALID"


@dataclass(frozen=True)
class DutyAllocationResult:
    status: DutyAllocationStatus
    input_fingerprint: str  # DraftTimetable.input_fingerprint the duties were built on
    assignments: tuple[DutyAssignment, ...] = ()
    violations: tuple[Violation, ...] = ()
    diagnostics: tuple[str, ...] = ()
    wall_time_seconds: float = 0.0
    solver_version: str | None = None

    def __post_init__(self) -> None:
        if type(self.status) is not DutyAllocationStatus:
            _raise([Violation(DUTY_INPUT, "result.status must be a DutyAllocationStatus.")])
        _raise(_texts(self, "result", ("input_fingerprint",)))
        object.__setattr__(self, "assignments", frozen_sequence(self.assignments, "result.assignments", DutyAssignment))
        object.__setattr__(self, "violations", frozen_sequence(self.violations, "result.violations", Violation))
        object.__setattr__(self, "diagnostics", frozen_sequence(self.diagnostics, "result.diagnostics", str))
