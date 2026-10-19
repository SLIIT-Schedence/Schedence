"""Soft fairness objective added on top of the unchanged Step 4 hard-constraint model.

    minimize  balance      x sum_{s<t} |L_s - L_t|      (pairwise gaps; total load is fixed,
                                                         so this minimises the Gini coefficient)
            + peak         x max_s L_s                   (heaviest total load)
            + back_to_back x #(duty pairs closer than back_to_back_minutes)
            + preference   x #(duties on avoided / non-preferred days)

L_s is staff s's total load (history + admin + duties this period, see fairness_policy).
The balance term adds n(n-1)/2 variables for n staff. The only extra hard rule is
opt-in: hard_avoid_days.
"""

from __future__ import annotations

from collections import defaultdict

from ortools.sat.python import cp_model

from app.duty_allocation.fairness_metrics import is_avoided_day, is_preference_breach, session_minutes, staff_offsets
from app.duty_allocation.fairness_policy import DutyFairnessPolicy, FairnessInputs
from app.duty_allocation.model import DutyModel
from app.duty_allocation.types import DutyAllocationInput, DutyRole


def add_fairness_objective(duty_model: DutyModel, data: DutyAllocationInput, inputs: FairnessInputs,
                           policy: DutyFairnessPolicy) -> None:
    model, assign = duty_model.model, duty_model.assign
    sessions = {s.session_id: s for s in data.timetable.sessions}
    offsets, _ = staff_offsets(data, inputs, policy)
    staff_ids = sorted(offsets)
    n = len(staff_ids)

    terms: dict[str, list] = defaultdict(list)  # staff_id -> [(load, var)]
    worked: dict[tuple[str, str], list] = defaultdict(list)  # (staff, session) -> vars
    preference_terms = []
    for (session_id, _venue, role, staff_id), var in assign.items():
        session = sessions[session_id]
        terms[staff_id].append((policy.duty_load(role, session_minutes(session)), var))
        worked[staff_id, session_id].append(var)
        day = session.start.date()
        if policy.hard_avoid_days and is_avoided_day(staff_id, day, inputs):
            model.add(var == 0)
        elif is_preference_breach(staff_id, day, inputs):
            preference_terms.append(var)

    # Nobody can carry more than every duty of this period on top of the largest prior load.
    period_load = sum(req.required(role) * policy.duty_load(role, session_minutes(sessions[req.session_id]))
                      for req in data.requirements for role in DutyRole)
    upper = max(offsets.values(), default=0) + period_load

    loads = {}
    for staff_id in staff_ids:
        load = model.new_int_var(0, upper, f"load:{staff_id}")
        model.add(load == offsets[staff_id] + sum(weight * var for weight, var in terms[staff_id]))
        loads[staff_id] = load

    objective = []
    w = policy.weights
    if w.balance and n > 1:
        # Pairwise gaps: the Gini numerator. Unlike distance-from-mean, it also evens out staff who
        # are all on one side of the mean (e.g. invigilator-only staff when supervising weighs more).
        for i, first in enumerate(staff_ids):
            for second in staff_ids[i + 1:]:
                gap = model.new_int_var(0, upper, f"gap:{first}:{second}")
                model.add_abs_equality(gap, loads[first] - loads[second])
                objective.append(w.balance * gap)
    if w.peak and n:
        peak = model.new_int_var(0, upper, "peak_load")
        model.add_max_equality(peak, list(loads.values()))
        objective.append(w.peak * peak)
    if w.back_to_back and policy.back_to_back_minutes:
        for staff_id in staff_ids:
            mine = sorted({sid for (member, sid) in worked if member == staff_id},
                          key=lambda sid: (sessions[sid].start, sid))
            for i, first in enumerate(mine):
                for second in mine[i + 1:]:
                    gap = (sessions[second].start - sessions[first].end).total_seconds() / 60
                    if 0 <= gap < policy.back_to_back_minutes:
                        pair = model.new_bool_var(f"b2b:{staff_id}:{first}:{second}")
                        model.add(pair >= sum(worked[staff_id, first]) + sum(worked[staff_id, second]) - 1)
                        objective.append(w.back_to_back * pair)
    if w.preference:
        objective.extend(w.preference * var for var in preference_terms)
    if objective:
        model.minimize(sum(objective))
