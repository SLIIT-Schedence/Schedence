"""Workload arithmetic shared by the fairness objective, and fairness metrics for any roster.

Metrics are computed from assignments alone, so the Step 4 baseline and the
fairness-aware roster are measured the same way.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass

from app.duty_allocation.fairness_policy import DayPreferenceKind, DutyFairnessPolicy, FairnessInputs
from app.duty_allocation.types import DutyAllocationInput, DutyAssignment


def session_minutes(session) -> int:
    return int((session.end - session.start).total_seconds() // 60)


def staff_offsets(data: DutyAllocationInput, inputs: FairnessInputs,
                  policy: DutyFairnessPolicy) -> tuple[dict[str, int], tuple[str, ...]]:
    """Load each person carries before this period's duties, and staff missing admin hours."""
    history = {h.staff_id: h for h in inputs.history}
    admin = {a.staff_id: a for a in inputs.admin}
    offsets, missing = {}, []
    for member in data.staff:
        load = policy.history_weight * history[member.staff_id].amount(policy.load_unit) if member.staff_id in history else 0
        if policy.admin_load_per_weekly_hour:
            hours = admin[member.staff_id].hours_per_week if member.staff_id in admin else None
            if hours is None:
                missing.append(member.staff_id)
            else:
                load += round(policy.admin_load_per_weekly_hour * hours)
        offsets[member.staff_id] = load
    return offsets, tuple(sorted(missing))


def is_preference_breach(staff_id: str, day, inputs: FairnessInputs) -> bool:
    own = [p for p in inputs.preferences if p.staff_id == staff_id]
    if any(p.kind is DayPreferenceKind.AVOID and p.day == day for p in own):
        return True
    preferred = {p.day for p in own if p.kind is DayPreferenceKind.PREFER}
    return bool(preferred) and day not in preferred


def is_avoided_day(staff_id: str, day, inputs: FairnessInputs) -> bool:
    return any(p.staff_id == staff_id and p.kind is DayPreferenceKind.AVOID and p.day == day for p in inputs.preferences)


def back_to_back_pairs(sessions_of_staff: dict[str, list], threshold_minutes: int) -> int:
    """Pairs of one person's sessions where the next starts less than threshold after the first ends."""
    if not threshold_minutes:
        return 0
    pairs = 0
    for sessions in sessions_of_staff.values():
        ordered = sorted(sessions, key=lambda s: (s.start, s.session_id))
        for i, first in enumerate(ordered):
            for second in ordered[i + 1:]:
                gap = (second.start - first.end).total_seconds() / 60
                if 0 <= gap < threshold_minutes:
                    pairs += 1
    return pairs


@dataclass(frozen=True)
class FairnessMetrics:
    total_loads: tuple[tuple[str, int], ...]  # (staff_id, history + admin + this period), sorted by id
    duty_counts: tuple[tuple[str, int], ...]  # (staff_id, duties this period)
    min_load: int
    max_load: int
    mean_load: float
    std_load: float
    gini: float  # 0 = perfectly equal total loads
    max_duties: int
    min_duties: int
    staff_without_duties: int
    supervisor_duties_range: int  # max - min Supervisor duties among Supervisor-eligible staff
    back_to_back_pairs: int
    preference_breaches: int
    missing_admin_hours: tuple[str, ...]

    @property
    def load_range(self) -> int:
        return self.max_load - self.min_load


def _gini(values: list[int]) -> float:
    total = sum(values)
    if not values or total == 0:
        return 0.0
    ordered = sorted(values)
    weighted = sum((i + 1) * v for i, v in enumerate(ordered))
    return (2 * weighted) / (len(values) * total) - (len(values) + 1) / len(values)


def measure_fairness(data: DutyAllocationInput, inputs: FairnessInputs, policy: DutyFairnessPolicy,
                     assignments: tuple[DutyAssignment, ...]) -> FairnessMetrics:
    sessions = {s.session_id: s for s in data.timetable.sessions}
    offsets, missing = staff_offsets(data, inputs, policy)
    loads = dict(offsets)
    counts: Counter[str] = Counter()
    supervising: Counter[str] = Counter()
    worked: dict[str, list] = defaultdict(list)
    breaches = 0
    for a in assignments:
        s = sessions[a.session_id]
        loads[a.staff_id] += policy.duty_load(a.role, session_minutes(s))
        counts[a.staff_id] += 1
        supervising[a.staff_id] += a.role.value == "SUPERVISOR"
        worked[a.staff_id].append(s)
        breaches += is_preference_breach(a.staff_id, s.start.date(), inputs)
    ids = sorted(loads)
    values = [loads[i] for i in ids]
    duty_values = [counts[i] for i in ids]
    mean = sum(values) / len(values) if values else 0.0
    supervisors = [m.staff_id for m in data.staff if any(r.value == "SUPERVISOR" for r in m.eligible_roles)]
    sup_counts = [supervising[i] for i in supervisors] or [0]
    return FairnessMetrics(
        total_loads=tuple((i, loads[i]) for i in ids),
        duty_counts=tuple((i, counts[i]) for i in ids),
        min_load=min(values, default=0), max_load=max(values, default=0), mean_load=mean,
        std_load=math.sqrt(sum((v - mean) ** 2 for v in values) / len(values)) if values else 0.0,
        gini=_gini(values),
        max_duties=max(duty_values, default=0), min_duties=min(duty_values, default=0),
        staff_without_duties=sum(1 for v in duty_values if v == 0),
        supervisor_duties_range=max(sup_counts) - min(sup_counts),
        back_to_back_pairs=back_to_back_pairs(worked, policy.back_to_back_minutes),
        preference_breaches=breaches,
        missing_admin_hours=missing,
    )
