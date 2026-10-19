"""Fairness-aware CP-SAT allocation vs the Step 4 baseline. Synthetic data and weights only."""

from __future__ import annotations

import pytest

pytest.importorskip("ortools", reason="install the examination service [solver] extra")

from app.duty_allocation import (
    AdminWorkload, DayPreference, DayPreferenceKind, DutyAllocationStatus, DutyFairnessPolicy, DutyHistory,
    DutyRole, FairnessInputs, FairnessWeights, LoadUnit, MissingAdminHours, StaffUnavailability,
    allocate_fair_duties, compare_duty_fairness, validate_allocation,
)
from app.scheduling.policy import SolverSettings
from duty_allocation_support import DAY, INV, at, limits, make_input, member, open_policy, session, sid, team

OK = (DutyAllocationStatus.OPTIMAL, DutyAllocationStatus.FEASIBLE)
FAST = SolverSettings(time_limit_seconds=20)


def fairness(**changes) -> DutyFairnessPolicy:
    values = dict(source="synthetic test fairness", load_unit=LoadUnit.DUTIES, supervisor_weight=100,
                  invigilator_weight=100, history_weight=100, admin_load_per_weekly_hour=0,
                  missing_admin_hours=MissingAdminHours.ZERO, back_to_back_minutes=0,
                  weights=FairnessWeights(1, 0, 0, 0))
    return DutyFairnessPolicy(**(values | changes))


def fair(data, policy=None, inputs=FairnessInputs(), eligibility=None, limit=None):
    result = allocate_fair_duties(data, eligibility or open_policy(), limit or limits(), inputs, policy or fairness(), FAST)
    if result.status in OK:  # hard constraints from Step 4 still hold
        assert validate_allocation(data, eligibility or open_policy(), limit or limits(), result.assignments) == ()
    return result


def counts(result) -> dict[str, int]:
    out: dict[str, int] = {}
    for a in result.assignments:
        out[a.staff_id] = out.get(a.staff_id, 0) + 1
    return out


def separate_sessions(k: int, need=(0, 1)):
    """k non-overlapping one-room sessions over several days."""
    return [session(f"X{n}", at(8 + 3 * (n % 3), day=n // 3), at(10 + 3 * (n % 3), day=n // 3), f"R{n}")
            for n in range(k)]


# --- Factor 1: equal distribution ------------------------------------------------------------

def test_duties_are_spread_equally():
    data = make_input(separate_sessions(6), team(3, INV), need=(0, 1))
    result = fair(data)
    assert result.status is DutyAllocationStatus.OPTIMAL
    assert sorted(counts(result).values()) == [2, 2, 2]


def test_peak_weight_alone_also_levels_the_heaviest_load():
    data = make_input(separate_sessions(7), team(3, INV), need=(0, 1))
    result = fair(data, fairness(weights=FairnessWeights(0, 1, 0, 0)))
    assert max(counts(result).values()) == 3  # ceil(7 / 3)


# --- Factor 2: historical workload -------------------------------------------------------------

def test_staff_with_heavy_history_get_fewer_duties():
    data = make_input(separate_sessions(4), team(3, INV), need=(0, 1))
    inputs = FairnessInputs(history=(DutyHistory(sid(1), 4, 480),))
    result = fair(data, inputs=inputs)
    assert counts(result).get(sid(1), 0) == 0 and sorted(counts(result).values()) == [2, 2]


def test_history_weight_scales_its_influence():
    data = make_input(separate_sessions(6), team(3, INV), need=(0, 1))
    inputs = FairnessInputs(history=(DutyHistory(sid(1), 3, 0),))
    full = counts(fair(data, inputs=inputs))
    ignored = counts(fair(data, fairness(history_weight=0), inputs))
    # 6 new duties + 3 prior = 9 over 3 people: totals 3,3,3. With history ignored: 2,2,2.
    assert full.get(sid(1), 0) == 0 and full[sid(2)] == full[sid(3)] == 3
    assert sorted(ignored.values()) == [2, 2, 2]


# --- Factor 3: administrative workload -----------------------------------------------------------

def test_admin_hours_reduce_duties():
    data = make_input(separate_sessions(6), team(3, INV), need=(0, 1))
    # Synthetic conversion: 10 load units per weekly admin hour; one duty = 100 units.
    inputs = FairnessInputs(admin=(AdminWorkload(sid(1), 30.0), AdminWorkload(sid(2), 0.0), AdminWorkload(sid(3), 0.0)))
    result = fair(data, fairness(admin_load_per_weekly_hour=10), inputs)
    assert counts(result).get(sid(1), 0) == 0 and sorted(counts(result).values()) == [3, 3]


def test_missing_admin_hours_reject_or_count_as_zero():
    data = make_input(separate_sessions(2), team(2, INV), need=(0, 1))
    inputs = FairnessInputs(admin=(AdminWorkload(sid(1), 4.0), AdminWorkload(sid(2), None, "no policy")))
    rejected = fair(data, fairness(admin_load_per_weekly_hour=10, missing_admin_hours=MissingAdminHours.REJECT), inputs)
    assert rejected.status is DutyAllocationStatus.MODEL_INVALID and sid(2) in rejected.violations[0].message
    zero = fair(data, fairness(admin_load_per_weekly_hour=10), inputs)
    assert zero.status in OK and "counted as 0" in zero.diagnostics[-1]


# --- Factor 4: preferences ---------------------------------------------------------------------

def test_avoided_and_preferred_days_are_soft_by_default():
    data = make_input(separate_sessions(6), team(2, INV), need=(0, 1))  # X0-X2 on DAY, X3-X5 the next day
    prefs = (DayPreference(sid(1), DAY, DayPreferenceKind.AVOID), DayPreference(sid(2), DAY, DayPreferenceKind.PREFER))
    result = fair(data, fairness(weights=FairnessWeights(0, 0, 0, 1)), FairnessInputs(preferences=prefs))
    by_day = {(a.staff_id, a.session_id): a for a in result.assignments}
    assert all(staff == sid(2) for staff, sess in by_day if sess in ("X0", "X1", "X2"))
    assert all(staff == sid(1) for staff, sess in by_day if sess in ("X3", "X4", "X5"))
    # Soft: when nobody else can work an avoided day, the roster still exists.
    lone = fair(make_input(separate_sessions(1), team(1, INV), need=(0, 1)),
                fairness(weights=FairnessWeights(0, 0, 0, 1)), FairnessInputs(preferences=prefs[:1]))
    assert lone.status in OK and lone.assignments[0].staff_id == sid(1)


def test_hard_avoid_days_only_when_configured():
    data = make_input(separate_sessions(1), team(1, INV), need=(0, 1))
    inputs = FairnessInputs(preferences=(DayPreference(sid(1), DAY, DayPreferenceKind.AVOID),))
    assert fair(data, fairness(hard_avoid_days=True), inputs).status is DutyAllocationStatus.INFEASIBLE


# --- Factor 5: Supervisor vs Invigilator weights ----------------------------------------------------

def test_role_weights_balance_heavier_supervising():
    # 4 sessions, each 1 Supervisor + 1 Invigilator; A, B supervise only, C, D, E invigilate.
    data = make_input(separate_sessions(4), [member(1, (DutyRole.SUPERVISOR,)), member(2, (DutyRole.SUPERVISOR,))]
                      + [member(n, INV) for n in (3, 4, 5)], need=(1, 1))
    result = fair(data, fairness(supervisor_weight=150, weights=FairnessWeights(0, 1, 0, 0)))
    assert result.status is DutyAllocationStatus.OPTIMAL
    assert counts(result)[sid(1)] == counts(result)[sid(2)] == 2


# --- Factor 6: exam length and back-to-back duties ------------------------------------------------

def test_minutes_mode_gives_long_exams_more_weight():
    sessions = [session("LONG", at(8), at(11), "R1"), session("S1", at(13), at(14), "R2"),
                session("S2", at(15), at(16), "R3"), session("S3", at(17), at(18), "R4")]
    data = make_input(sessions, team(2, INV), need=(0, 1))
    result = fair(data, fairness(load_unit=LoadUnit.MINUTES))
    long_staff = next(a.staff_id for a in result.assignments if a.session_id == "LONG")
    assert counts(result)[long_staff] == 1  # 180 min vs 3 x 60 min


def test_back_to_back_duties_are_avoided_when_possible():
    sessions = [session("A", at(8), at(10), "R1"), session("B", at(10, 15), at(12), "R2"),
                session("C", at(14), at(16), "R3"), session("D", at(16, 15), at(18), "R4")]
    data = make_input(sessions, team(2, INV), need=(0, 1))
    result = fair(data, fairness(back_to_back_minutes=60, weights=FairnessWeights(0, 0, 1, 0)))
    who = {a.session_id: a.staff_id for a in result.assignments}
    assert who["A"] != who["B"] and who["C"] != who["D"]


# --- Baseline comparison --------------------------------------------------------------------------

def test_fairness_never_worse_than_baseline_and_keeps_hard_constraints():
    sessions = [session(f"D{d}{p}", at(h, day=d), at(h + 2, day=d), f"R{d}{p}a", f"R{d}{p}b")
                for d in range(3) for p, h in (("AM", 8), ("PM", 13))]
    staff = [member(n) for n in range(1, 11)]
    inputs = FairnessInputs(history=(DutyHistory(sid(1), 3, 360), DutyHistory(sid(2), 2, 240)))
    off = [StaffUnavailability(sid(3), at(0, day=1), at(23, day=1), "leave")]
    data = make_input(sessions, staff, unavailability=off)
    # Pairwise balance finds this roster quickly but proving optimality takes long: cap at 5 s.
    cmp = compare_duty_fairness(data, open_policy(gap=60), limits(per_day=2), inputs, fairness(),
                                SolverSettings(time_limit_seconds=5))
    assert cmp.baseline.status in OK and cmp.fair.status in OK
    for result in (cmp.baseline, cmp.fair):
        assert validate_allocation(data, open_policy(gap=60), limits(per_day=2), result.assignments) == ()
    assert cmp.fair_metrics.load_range <= cmp.baseline_metrics.load_range
    assert cmp.fair_metrics.std_load <= cmp.baseline_metrics.std_load
    # 24 duties + 5 history = 29 units of 100 over 10 people: best possible range is 1 duty.
    assert cmp.fair_metrics.load_range == 100


def test_invalid_fairness_inputs_are_model_invalid():
    data = make_input(separate_sessions(1), team(2, INV), need=(0, 1))
    stranger = FairnessInputs(history=(DutyHistory(sid(99), 1, 1),))
    assert fair(data, inputs=stranger).status is DutyAllocationStatus.MODEL_INVALID
    bad = allocate_fair_duties(data, open_policy(), limits(), {"history": []}, fairness(), FAST)
    assert bad.status is DutyAllocationStatus.MODEL_INVALID
