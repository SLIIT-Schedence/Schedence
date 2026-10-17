"""Immutable scheduler inputs and outputs, independent of CP-SAT and storage."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import asdict, dataclass, field, replace
from enum import Enum

from app.seating.snapshot import CapacitySnapshot
from app.scheduling.contracts import SchedulingValidationError, Violation, data_shape_errors, frozen_sequence, text_errors
from app.scheduling.policy import SchedulingPolicy, SolverSettings
from app.sources.types import ExamPeriod, Module, Registration, Student, Venue, VenueBlock


@dataclass(frozen=True)
class InputSnapshot:
    period: ExamPeriod
    modules: tuple[Module, ...]
    students: tuple[Student, ...]
    registrations: tuple[Registration, ...]
    venues: tuple[Venue, ...]
    blocks: tuple[VenueBlock, ...]
    capacities: CapacitySnapshot
    policy: SchedulingPolicy
    fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        from app.sources.types import ExamPeriodExclusion
        from app.seating.snapshot import SnapshotVenue

        for name, record in (("modules", Module), ("students", Student), ("registrations", Registration),
                             ("venues", Venue), ("blocks", VenueBlock)):
            object.__setattr__(self, name, frozen_sequence(getattr(self, name), name, record))
        if type(self.period) is not ExamPeriod or type(self.capacities) is not CapacitySnapshot or type(self.policy) is not SchedulingPolicy:
            raise SchedulingValidationError((Violation("H11", "Snapshot requires an ExamPeriod, CapacitySnapshot and SchedulingPolicy."),))
        object.__setattr__(self, "modules", tuple(replace(m, specializations=frozen_sequence(
            m.specializations, "module specializations", str)) for m in self.modules))
        object.__setattr__(self, "period", replace(self.period,
            exclusions=frozen_sequence(self.period.exclusions, "period exclusions", ExamPeriodExclusion),
            exam_days=frozen_sequence(self.period.exam_days, "exam_days", dt.date)))
        object.__setattr__(self, "capacities", replace(self.capacities,
            venues=frozen_sequence(self.capacities.venues, "capacity venues", SnapshotVenue)))
        errors = data_shape_errors(self.period, self.modules, self.students, self.registrations,
                                   self.venues, self.blocks, self.capacities)
        if errors:
            raise SchedulingValidationError(errors)
        # Content identity excludes the snapshot clock; includes all scheduling inputs.
        content = {name: asdict(getattr(self, name)) if name in ("period", "capacities", "policy")
                   else [asdict(row) for row in getattr(self, name)]
                   for name in ("period", "modules", "students", "registrations", "venues", "blocks", "capacities", "policy")}
        content["capacities"].pop("taken_at")
        digest = hashlib.sha256(json.dumps(content, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()
        object.__setattr__(self, "fingerprint", digest)


@dataclass(frozen=True)
class SeatAllocation:
    student_id: str
    seat_number: int  # logical seat, not a physical room layout

    def __post_init__(self) -> None:
        errors = text_errors(self.student_id, "seat.student_id")
        if errors:
            raise SchedulingValidationError(errors)
        # Keep immutable invalid numbers available to the independent H2 check.
        if type(self.seat_number) not in (int, float, bool):
            raise SchedulingValidationError((Violation("H2", "seat_number must be numeric."),))


@dataclass(frozen=True)
class RoomAllocation:
    venue_id: str
    seats: tuple[SeatAllocation, ...]

    def __post_init__(self) -> None:
        errors = text_errors(self.venue_id, "room.venue_id")
        if errors:
            raise SchedulingValidationError(errors)
        object.__setattr__(self, "seats", frozen_sequence(self.seats, "room.seats", SeatAllocation))


@dataclass(frozen=True)
class ExamSession:
    session_id: str
    module_code: str
    period_code: str
    start: dt.datetime  # local calendar time, matching existing source records
    end: dt.datetime
    rooms: tuple[RoomAllocation, ...]  # every room inherits this session's times

    def __post_init__(self) -> None:
        errors = tuple(v for name in ("session_id", "module_code", "period_code")
                       for v in text_errors(getattr(self, name), "session." + name))
        if errors:
            raise SchedulingValidationError(errors)
        if type(self.start) is not dt.datetime or type(self.end) is not dt.datetime:
            raise SchedulingValidationError((Violation("H11", "Session start and end must be datetimes."),))
        object.__setattr__(self, "rooms", frozen_sequence(self.rooms, "session.rooms", RoomAllocation))


@dataclass(frozen=True)
class DraftTimetable:
    period_code: str
    input_fingerprint: str
    sessions: tuple[ExamSession, ...]

    def __post_init__(self) -> None:
        errors = text_errors(self.period_code, "draft.period_code") + text_errors(self.input_fingerprint, "draft.input_fingerprint")
        if errors:
            raise SchedulingValidationError(errors)
        object.__setattr__(self, "sessions", frozen_sequence(self.sessions, "draft.sessions", ExamSession))


class SolveStatus(str, Enum):
    FEASIBLE = "FEASIBLE"
    OPTIMAL = "OPTIMAL"
    INFEASIBLE = "INFEASIBLE"
    UNKNOWN = "UNKNOWN"
    MODEL_INVALID = "MODEL_INVALID"


@dataclass(frozen=True)
class SolveResult:
    status: SolveStatus
    timetable: DraftTimetable | None = None
    violations: tuple[Violation, ...] = ()
    diagnostics: tuple[str, ...] = ()
    wall_time_seconds: float = 0.0
    solver_version: str | None = None
    settings: SolverSettings = SolverSettings()
