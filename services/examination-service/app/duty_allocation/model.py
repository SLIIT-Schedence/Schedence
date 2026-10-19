"""CP-SAT model for basic staff duty allocation (hard constraints only, no fairness objective).

One Boolean x[session, venue, role, staff] exists only where build_eligibility() found the
staff member eligible, so rank/role rules, restrictions, availability and unavailability
hold by construction. Remaining hard constraints are explicit:

    fill      sum_staff x[room, role, .] == required(room, role)
    session   sum_{room, role} x[session, ., ., staff] <= 1   (one duty per session: no two
              roles and no two rooms in one session)
    clash     for each clashing session pair (overlap or closer than the minimum break):
              duties in either session <= 1 per staff member
    per day   sum over the day's sessions <= max_duties_per_day
    period    sum over all sessions <= max_duties_per_period
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from ortools.sat.python import cp_model

from app.duty_allocation.allocation_policy import DutyLimitPolicy
from app.duty_allocation.eligibility import EligibilityReport
from app.duty_allocation.types import DutyAllocationInput, DutyAssignment, DutyRole

VarKey = tuple[str, str, DutyRole, str]  # session_id, venue_id, role, staff_id


@dataclass(frozen=True)
class DutyModel:
    model: cp_model.CpModel
    assign: dict[VarKey, cp_model.IntVar]


def build_duty_model(data: DutyAllocationInput, report: EligibilityReport, limits: DutyLimitPolicy) -> DutyModel:
    model = cp_model.CpModel()
    requirements = {r.room_key: r for r in data.requirements}
    assign: dict[VarKey, cp_model.IntVar] = {}
    for room in report.rooms:  # already sorted by session start, session id, venue id
        for role in DutyRole:
            pool = []
            for staff_id in room.staff_for(role):
                var = model.new_bool_var(f"duty:{room.session_id}:{room.venue_id}:{role.value}:{staff_id}")
                assign[room.session_id, room.venue_id, role, staff_id] = var
                pool.append(var)
            # An empty pool gives the constant 0 == required, which is False when staff are needed.
            model.add(sum(pool) == requirements[room.room_key].required(role))

    by_staff_session: dict[tuple[str, str], list[cp_model.IntVar]] = defaultdict(list)
    for (session_id, _, _, staff_id), var in assign.items():
        by_staff_session[staff_id, session_id].append(var)
    staff_ids = sorted({staff_id for staff_id, _ in by_staff_session})

    for duties in by_staff_session.values():
        if len(duties) > 1:
            model.add(sum(duties) <= 1)

    for first, second in report.session_conflicts:
        for staff_id in staff_ids:
            duties = by_staff_session.get((staff_id, first), []) + by_staff_session.get((staff_id, second), [])
            if len(duties) > 1:
                model.add(sum(duties) <= 1)

    session_day = {s.session_id: s.start.date() for s in data.timetable.sessions}
    for staff_id in staff_ids:
        sessions = [sid for (member, sid) in by_staff_session if member == staff_id]
        if limits.max_duties_per_day is not None:
            per_day: dict = defaultdict(list)
            for sid in sessions:
                per_day[session_day[sid]].extend(by_staff_session[staff_id, sid])
            for duties in per_day.values():
                if len(duties) > limits.max_duties_per_day:
                    model.add(sum(duties) <= limits.max_duties_per_day)
        if limits.max_duties_per_period is not None:
            duties = [v for sid in sessions for v in by_staff_session[staff_id, sid]]
            if len(duties) > limits.max_duties_per_period:
                model.add(sum(duties) <= limits.max_duties_per_period)
    return DutyModel(model, assign)


def decode_assignments(duty_model: DutyModel, solver: cp_model.CpSolver) -> tuple[DutyAssignment, ...]:
    return tuple(DutyAssignment(session_id, venue_id, staff_id, role)
                 for (session_id, venue_id, role, staff_id), var in duty_model.assign.items()
                 if solver.boolean_value(var))
