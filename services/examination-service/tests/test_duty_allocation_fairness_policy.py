"""Fairness policy, fairness inputs and roster metrics. No OR-Tools needed.

All weights here are synthetic test values, not SLIIT policy.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.duty_allocation import (
    AdminWorkload, DayPreference, DayPreferenceKind, DutyAssignment, DutyFairnessPolicy, DutyHistory, DutyRole,
    FairnessInputs, FairnessWeights, LoadUnit, MissingAdminHours, admin_workload_from_records, measure_fairness,
)
from app.duty_allocation.fairness_metrics import staff_offsets
from app.scheduling.contracts import SchedulingValidationError
from duty_allocation_support import DAY, INV, at, make_input, member, session, sid, team

S, I = DutyRole.SUPERVISOR, DutyRole.INVIGILATOR


def policy(**changes) -> DutyFairnessPolicy:
    values = dict(source="synthetic", load_unit=LoadUnit.DUTIES, supervisor_weight=100, invigilator_weight=100,
                  history_weight=100, admin_load_per_weekly_hour=0, missing_admin_hours=MissingAdminHours.ZERO,
                  back_to_back_minutes=0, weights=FairnessWeights(1, 0, 0, 0))
    return DutyFairnessPolicy(**(values | changes))


CONFIG = {"source": "cfg", "load_unit": "MINUTES", "supervisor_weight": 120, "invigilator_weight": 100,
          "history_weight": 50, "admin_load_per_weekly_hour": 10, "missing_admin_hours": "REJECT",
          "back_to_back_minutes": 60, "weights": {"balance": 1, "peak": 2, "back_to_back": 3, "preference": 4}}


# --- Policy ---------------------------------------------------------------------------------

def test_policy_from_config():
    p = DutyFairnessPolicy.from_mapping(CONFIG)
    assert p.load_unit is LoadUnit.MINUTES and p.missing_admin_hours is MissingAdminHours.REJECT
    assert p.weights == FairnessWeights(1, 2, 3, 4) and p.hard_avoid_days is False
    assert p.duty_load(S, 90) == 120 * 90 and p.duty_load(I, 90) == 100 * 90
    assert policy().duty_load(S, 90) == 100  # DUTIES ignores minutes


@pytest.mark.parametrize("change", [
    lambda c: c.pop("weights"), lambda c: c.update(extra=1), lambda c: c.update(load_unit="HOURS"),
    lambda c: c.update(missing_admin_hours="GUESS"), lambda c: c.update(supervisor_weight=0),
    lambda c: c.update(history_weight=-1), lambda c: c.update(weights={"balance": 1}),
    lambda c: c.update(weights={"balance": 0, "peak": 0, "back_to_back": 0, "preference": 0}),
    lambda c: c.update(hard_avoid_days="yes"),
])
def test_invalid_config_is_rejected(change):
    config = {k: (dict(v) if isinstance(v, dict) else v) for k, v in CONFIG.items()}
    change(config)
    with pytest.raises(SchedulingValidationError):
        DutyFairnessPolicy.from_mapping(config)


# --- Inputs ---------------------------------------------------------------------------------

def test_admin_workload_reads_academic_data_contract():
    rows = admin_workload_from_records([
        {"staff_id": sid(1), "aggregate": {"status": "COMPUTED", "rule": "SUM", "total_admin_hours_per_week": 7.5, "reason": None}},
        {"staff_id": sid(2).upper(), "aggregate": {"status": "NOT_COMPUTED", "rule": None,
                                                  "total_admin_hours_per_week": None, "reason": "no policy"}},
    ])
    assert rows == (AdminWorkload(sid(1), 7.5, "SUM"), AdminWorkload(sid(2), None, "no policy"))
    with pytest.raises(SchedulingValidationError):
        admin_workload_from_records([{"staff_id": sid(1), "aggregate": {"status": "MAYBE"}}])


@pytest.mark.parametrize("build", [
    lambda: DutyHistory("EMP1", 1, 1), lambda: DutyHistory(sid(1), -1, 0),
    lambda: AdminWorkload(sid(1), -2.0), lambda: AdminWorkload(sid(1), float("inf")),
    lambda: DayPreference(sid(1), "2026-11-02", DayPreferenceKind.AVOID),
    lambda: FairnessInputs(history=(DutyHistory(sid(1), 1, 1), DutyHistory(sid(1), 2, 2))),
])
def test_invalid_inputs_are_rejected(build):
    with pytest.raises(SchedulingValidationError):
        build()


def test_offsets_combine_history_and_admin_hours():
    data = make_input([session("X1", at(9), at(11), "R1")], team(3))
    inputs = FairnessInputs(history=(DutyHistory(sid(1), 2, 240),),
                            admin=(AdminWorkload(sid(1), 5.0), AdminWorkload(sid(2), 12.5), AdminWorkload(sid(3), None)))
    offsets, missing = staff_offsets(data, inputs, policy(admin_load_per_weekly_hour=10))
    assert offsets == {sid(1): 200 + 50, sid(2): 125, sid(3): 0} and missing == (sid(3),)
    offsets, _ = staff_offsets(data, inputs, policy(load_unit=LoadUnit.MINUTES, history_weight=50))
    assert offsets[sid(1)] == 50 * 240  # admin ignored when admin_load_per_weekly_hour is 0


# --- Metrics --------------------------------------------------------------------------------

def test_metrics_for_an_unbalanced_roster():
    sessions = [session(f"X{n}", at(8 + 2 * n), at(9 + 2 * n), f"R{n}") for n in range(3)]
    data = make_input(sessions, team(3, INV), need=(0, 1))
    roster = tuple(DutyAssignment(f"X{n}", f"R{n}", sid(1), I) for n in range(3))
    m = measure_fairness(data, FairnessInputs(), policy(back_to_back_minutes=90), roster)
    assert dict(m.duty_counts) == {sid(1): 3, sid(2): 0, sid(3): 0}
    assert (m.max_load, m.min_load, m.load_range, m.staff_without_duties) == (300, 0, 300, 2)
    assert m.gini == pytest.approx(2 / 3) and m.back_to_back_pairs == 2


def test_metrics_for_a_balanced_roster():
    sessions = [session(f"X{n}", at(8 + 2 * n), at(9 + 2 * n), f"R{n}") for n in range(3)]
    data = make_input(sessions, team(3, INV), need=(0, 1))
    roster = tuple(DutyAssignment(f"X{n}", f"R{n}", sid(n + 1), I) for n in range(3))
    m = measure_fairness(data, FairnessInputs(), policy(), roster)
    assert m.load_range == 0 and m.gini == 0 and m.std_load == 0 and m.staff_without_duties == 0


def test_metrics_count_preference_breaches():
    sessions = [session("X0", at(9), at(11), "R0"), session("X1", at(9, day=1), at(11, day=1), "R1")]
    data = make_input(sessions, team(2, INV), need=(0, 1))
    roster = (DutyAssignment("X0", "R0", sid(1), I), DutyAssignment("X1", "R1", sid(2), I))
    prefs = (DayPreference(sid(1), DAY, DayPreferenceKind.AVOID),
             DayPreference(sid(2), DAY, DayPreferenceKind.PREFER))  # works day 1, prefers day 0
    m = measure_fairness(data, FairnessInputs(preferences=prefs), policy(), roster)
    assert m.preference_breaches == 2
