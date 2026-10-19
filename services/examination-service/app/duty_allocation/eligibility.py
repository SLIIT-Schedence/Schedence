"""Who may work each room, which sessions clash for one person, and early shortage checks.

For every (session, room) and duty role, a staff member is eligible when:
  - the role is in their eligible_roles and their rank qualifies under this policy,
  - no configured StaffRestriction excludes them for that role/session/module,
  - if they declared availability windows, the session fits inside one of them,
  - no unavailability overlaps the session.

One person cannot work two rooms whose sessions overlap, or that are closer together than
the policy's minimum gap; those session pairs are reported as conflicts for the later
allocator. Shortage checks are necessary conditions only: passing them does not prove a
feasible allocation exists, but failing them proves one does not.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from itertools import combinations

from app.duty_allocation.eligibility_policy import EligibilityPolicy
from app.duty_allocation.types import DUTY_INPUT, DutyAllocationInput, DutyRole, StaffMember
from app.scheduling.contracts import SchedulingValidationError, Violation

ROOM_SHORTAGE = "D_SHORTAGE_ROOM"
TIME_SHORTAGE = "D_SHORTAGE_TIME"


@dataclass(frozen=True)
class Exclusion:
    staff_id: str
    role: DutyRole
    reason: str


@dataclass(frozen=True)
class RoomEligibility:
    session_id: str
    venue_id: str
    module_code: str
    start: dt.datetime
    end: dt.datetime
    eligible: tuple[tuple[DutyRole, tuple[str, ...]], ...]  # (role, sorted staff ids)
    exclusions: tuple[Exclusion, ...]

    @property
    def room_key(self) -> tuple[str, str]:
        return self.session_id, self.venue_id

    def staff_for(self, role: DutyRole) -> tuple[str, ...]:
        return dict(self.eligible)[role]


@dataclass(frozen=True)
class EligibilityReport:
    rooms: tuple[RoomEligibility, ...]
    session_conflicts: tuple[tuple[str, str], ...]  # sessions one person cannot both work
    violations: tuple[Violation, ...]
    policy_source: str

    @property
    def is_feasible_so_far(self) -> bool:
        return not self.violations

    def room(self, session_id: str, venue_id: str) -> RoomEligibility:
        return next(r for r in self.rooms if r.room_key == (session_id, venue_id))


def _exclusion_reason(staff: StaffMember, role: DutyRole, session, data: DutyAllocationInput,
                      policy: EligibilityPolicy) -> str | None:
    if role not in staff.eligible_roles:
        return f"not eligible for {role.value} by rank rules"
    # Re-check rank against this policy: StaffMember roles may come from another policy or no import.
    rank_reason = policy.rank_decision(staff.academic_rank, role)
    if rank_reason is not None:
        return rank_reason
    restriction = policy.restriction_for(staff.staff_id, role, session.session_id, session.module_code)
    if restriction is not None:
        return f"restricted: {restriction.reason}"
    windows = [a for a in data.availability if a.staff_id == staff.staff_id]
    if windows and not any(a.contains(session.start, session.end) for a in windows):
        return "session is outside declared availability"
    blocked = next((u for u in data.unavailability
                    if u.staff_id == staff.staff_id and u.overlaps(session.start, session.end)), None)
    if blocked is not None:
        return f"unavailable{': ' + blocked.reason if blocked.reason else ''}"
    return None


def _clashes(a, b, gap: dt.timedelta) -> bool:
    return a.start < b.end + gap and b.start < a.end + gap


def build_eligibility(data: DutyAllocationInput, policy: EligibilityPolicy) -> EligibilityReport:
    if type(data) is not DutyAllocationInput or type(policy) is not EligibilityPolicy:
        raise SchedulingValidationError((Violation(DUTY_INPUT, "build_eligibility needs a DutyAllocationInput and EligibilityPolicy."),))
    gap = dt.timedelta(minutes=policy.minimum_gap_minutes)
    sessions = sorted(data.timetable.sessions, key=lambda s: (s.start, s.session_id))
    requirements = {r.room_key: r for r in data.requirements}
    staff = sorted(data.staff, key=lambda s: s.staff_id)

    rooms, violations = [], []
    for session in sessions:
        for room in sorted(session.rooms, key=lambda r: r.venue_id):
            eligible, exclusions = [], []
            for role in DutyRole:
                ids = []
                for member in staff:
                    reason = _exclusion_reason(member, role, session, data, policy)
                    if reason is None:
                        ids.append(member.staff_id)
                    else:
                        exclusions.append(Exclusion(member.staff_id, role, reason))
                eligible.append((role, tuple(ids)))
                needed = requirements[session.session_id, room.venue_id].required(role)
                if len(ids) < needed:
                    violations.append(Violation(ROOM_SHORTAGE, f"session {session.session_id} room {room.venue_id} needs "
                                                               f"{needed} {role.value}, only {len(ids)} eligible."))
            rooms.append(RoomEligibility(session.session_id, room.venue_id, session.module_code,
                                         session.start, session.end, tuple(eligible), tuple(exclusions)))

    conflicts = tuple((a.session_id, b.session_id) for a, b in combinations(sessions, 2) if _clashes(a, b, gap))
    violations += _time_shortages(sessions, rooms, requirements, gap)
    return EligibilityReport(tuple(rooms), conflicts, tuple(violations), policy.source)


def _time_shortages(sessions, rooms, requirements, gap) -> list[Violation]:
    """At each session start, all clashing sessions need distinct people at once."""
    violations, reported = [], set()
    for anchor in sessions:
        group = {s.session_id for s in sessions if s.start <= anchor.start < s.end + gap}
        active = [r for r in rooms if r.session_id in group]
        if len(group) < 2 and len(active) < 2:
            continue  # a single room is already covered by the room check
        checks = [(f"{role.value}", (role,)) for role in DutyRole] + [("duty staff in total", tuple(DutyRole))]
        for label, roles in checks:
            needed = sum(requirements[r.room_key].required(role) for r in active for role in roles)
            pool = {sid for r in active for role in roles for sid in r.staff_for(role)}
            key = (frozenset(group), label)
            if needed > len(pool) and key not in reported:
                reported.add(key)
                violations.append(Violation(TIME_SHORTAGE, f"at {anchor.start:%Y-%m-%d %H:%M}, sessions {', '.join(sorted(group))} "
                                                           f"need {needed} {label} at once, only {len(pool)} eligible."))
    return violations
