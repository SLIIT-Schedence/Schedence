"""Independent hard-constraint check of a staff duty roster.

Works from the raw input, policies and assignments only; it does not use the CP-SAT model
or build_eligibility(), so a modelling mistake there cannot hide a broken roster here.

    D1  every required position is filled exactly (no gaps, no extras)
    D2  no overlapping duties, and the configured minimum break between duties
    D3  at most one duty (room and role) per staff member per session
    D4  sessions fit inside declared availability windows
    D5  no duty overlaps staff unavailability
    D6  role allowed by staff eligible_roles and the policy's rank rules
    D7  no configured staff restriction applies
    D8  at most max_duties_per_day duties per staff member per day
    D9  at most max_duties_per_period duties per staff member in the period
    D10 assignments refer to known rooms and staff, with no duplicates
"""

from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict
from itertools import combinations

from app.duty_allocation.allocation_policy import DutyLimitPolicy
from app.duty_allocation.eligibility_policy import EligibilityPolicy
from app.duty_allocation.types import DutyAllocationInput, DutyAssignment, DutyRole
from app.scheduling.contracts import Violation


def validate_allocation(data: DutyAllocationInput, eligibility: EligibilityPolicy, limits: DutyLimitPolicy,
                        assignments: tuple[DutyAssignment, ...]) -> tuple[Violation, ...]:
    errors: list[Violation] = []

    def error(code: str, message: str) -> None:
        errors.append(Violation(code, message))

    sessions = {s.session_id: s for s in data.timetable.sessions}
    rooms = {(s.session_id, r.venue_id) for s in data.timetable.sessions for r in s.rooms}
    staff = {m.staff_id: m for m in data.staff}

    valid: list[DutyAssignment] = []
    for a, count in Counter(assignments).items():
        if type(a) is not DutyAssignment:
            error("D10", "Roster contains a value that is not a DutyAssignment.")
        elif (a.session_id, a.venue_id) not in rooms:
            error("D10", f"Assignment refers to unknown room {a.session_id}/{a.venue_id}.")
        elif a.staff_id not in staff:
            error("D10", f"Assignment refers to unknown staff {a.staff_id}.")
        else:
            if count > 1:
                error("D10", f"Staff {a.staff_id} is assigned twice as {a.role.value} in {a.session_id}/{a.venue_id}.")
            valid.append(a)

    filled = Counter((a.session_id, a.venue_id, a.role) for a in valid)
    for req in data.requirements:
        for role in DutyRole:
            got, need = filled[req.session_id, req.venue_id, role], req.required(role)
            if got != need:
                error("D1", f"{req.session_id}/{req.venue_id} has {got} {role.value}, requires {need}.")

    by_staff: dict[str, list[DutyAssignment]] = defaultdict(list)
    for a in valid:
        by_staff[a.staff_id].append(a)
    gap = dt.timedelta(minutes=eligibility.minimum_gap_minutes)

    for staff_id, duties in sorted(by_staff.items()):
        member = staff[staff_id]
        per_session = Counter(a.session_id for a in duties)
        for session_id, count in sorted(per_session.items()):
            if count > 1:
                error("D3", f"Staff {staff_id} has {count} duties in session {session_id}.")
        for first, second in combinations(sorted(per_session, key=lambda sid: (sessions[sid].start, sid)), 2):
            a, b = sessions[first], sessions[second]  # a starts no later than b
            if b.start < a.end + gap:
                error("D2", f"Staff {staff_id} has sessions {first} and {second} overlapping or "
                            f"less than {eligibility.minimum_gap_minutes} minutes apart.")
        windows = [w for w in data.availability if w.staff_id == staff_id]
        blocks = [u for u in data.unavailability if u.staff_id == staff_id]
        for a in duties:
            s = sessions[a.session_id]
            if windows and not any(w.start <= s.start and s.end <= w.end for w in windows):
                error("D4", f"Staff {staff_id} works {a.session_id} outside declared availability.")
            if any(u.start < s.end and s.start < u.end for u in blocks):
                error("D5", f"Staff {staff_id} works {a.session_id} while unavailable.")
            if a.role not in member.eligible_roles or eligibility.rank_decision(member.academic_rank, a.role) is not None:
                error("D6", f"Staff {staff_id} is not eligible for {a.role.value}.")
            if eligibility.restriction_for(staff_id, a.role, a.session_id, s.module_code) is not None:
                error("D7", f"Staff {staff_id} is restricted from {a.role.value} in {a.session_id}.")
        if limits.max_duties_per_day is not None:
            for day, count in sorted(Counter(sessions[a.session_id].start.date() for a in duties).items()):
                if count > limits.max_duties_per_day:
                    error("D8", f"Staff {staff_id} has {count} duties on {day}, limit {limits.max_duties_per_day}.")
        if limits.max_duties_per_period is not None and len(duties) > limits.max_duties_per_period:
            error("D9", f"Staff {staff_id} has {len(duties)} duties, period limit {limits.max_duties_per_period}.")
    return tuple(errors)
