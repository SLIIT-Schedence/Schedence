"""Derive per-room duty requirements from a scheduler draft and a staffing policy.

A room is one (session_id, venue_id) in DraftTimetable; its student count is the number of
seats the scheduler allocated there. The draft is only read, never changed.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.duty_allocation.staffing_policy import StaffingPolicy
from app.duty_allocation.types import DUTY_INPUT, RoomDutyRequirement
from app.scheduling.contracts import SchedulingValidationError, Violation
from app.scheduling.types import DraftTimetable

# A room's student count matches no configured staffing rule.
UNCOVERED_ROOM = "D_STAFFING_UNCOVERED"


@dataclass(frozen=True)
class RequirementCalculation:
    requirements: tuple[RoomDutyRequirement, ...]
    violations: tuple[Violation, ...]
    policy_source: str

    @property
    def is_complete(self) -> bool:
        return not self.violations

    @property
    def total_supervisors(self) -> int:
        return sum(r.supervisors for r in self.requirements)

    @property
    def total_invigilators(self) -> int:
        return sum(r.invigilators for r in self.requirements)


def calculate_requirements(timetable: DraftTimetable, policy: StaffingPolicy) -> RequirementCalculation:
    if type(timetable) is not DraftTimetable or type(policy) is not StaffingPolicy:
        raise SchedulingValidationError((Violation(DUTY_INPUT,"calculate_requirements needs a DraftTimetable and StaffingPolicy."),))
    requirements, violations = [], []
    for session in sorted(timetable.sessions, key=lambda s: s.session_id):
        for room in sorted(session.rooms, key=lambda r: r.venue_id):
            students = len(room.seats)
            rule = policy.rule_for(students)
            if rule is None:
                violations.append(Violation(UNCOVERED_ROOM, f"session {session.session_id} room {room.venue_id} has "
                                                            f"{students} students; no staffing rule covers that count."))
                continue
            requirements.append(RoomDutyRequirement(session.session_id, room.venue_id, rule.supervisors, rule.invigilators))
    return RequirementCalculation(tuple(requirements), tuple(violations), policy.source)
