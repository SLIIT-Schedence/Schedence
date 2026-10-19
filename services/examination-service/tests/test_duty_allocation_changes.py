"""Duty replacement and swap proposals. Synthetic staff, sessions and policies only.

Base timetable (day 0 unless noted), each room needs 1 Supervisor + 1 Invigilator:
    X1 08:30-10:30 R1   S: staff 1   I: staff 2
    X2 10:00-12:00 R2   S: staff 3   I: staff 4      (overlaps X1)
    X3 12:15-14:00 R3   S: staff 1   I: staff 7      (15 min after X2)
    X4 day 1 09:00-11:00 R4   S: staff 6   I: staff 2
Staff 7 may only invigilate; 5, 8 and 9 have no duties.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.duty_allocation import (
    AdminWorkload, ChangeStatus, DayPreference, DayPreferenceKind, DutyAssignment, DutyFairnessPolicy, DutyHistory,
    DutyRole, FairnessInputs, FairnessWeights, LoadUnit, MissingAdminHours, StaffRestriction, StaffUnavailability,
    check_proposal, measure_fairness, propose_replacement, propose_swap, validate_allocation,
)
from app.duty_allocation.duty_changes import unrelated_changes
from app.duty_allocation.fairness_metrics import fairness_objective
from duty_allocation_support import DAY, INV, at, limits, make_input, member, open_policy, session, sid

S, I = DutyRole.SUPERVISOR, DutyRole.INVIGILATOR
SESSIONS = [session("X1", at(8, 30), at(10, 30), "R1"), session("X2", at(10), at(12), "R2"),
            session("X3", at(12, 15), at(14), "R3"), session("X4", at(9, day=1), at(11, day=1), "R4")]
STAFF = [member(n) for n in (1, 2, 3, 4, 5, 6, 8, 9)] + [member(7, INV)]


def d(session_id, venue, n, role) -> DutyAssignment:
    return DutyAssignment(session_id, venue, sid(n), role)


ROSTER = (d("X1", "R1", 1, S), d("X1", "R1", 2, I), d("X2", "R2", 3, S), d("X2", "R2", 4, I),
          d("X3", "R3", 1, S), d("X3", "R3", 7, I), d("X4", "R4", 6, S), d("X4", "R4", 2, I))


def data(unavailability=()):
    return make_input(SESSIONS, STAFF, unavailability=unavailability)


def reasons_for(proposal, n) -> str:
    return " | ".join(next(r.reasons for r in proposal.rejected if r.staff_id == sid(n)))


def test_base_roster_is_valid():
    assert validate_allocation(data(), open_policy(), limits(), ROSTER) == ()


# --- Replacement -----------------------------------------------------------------------------------

def test_successful_replacement_changes_only_the_requested_duty():
    original = tuple(ROSTER)
    p = propose_replacement(data(), open_policy(), limits(), ROSTER, ROSTER[0], "medical leave")
    assert p.status is ChangeStatus.PROPOSED and p.applied is False
    assert p.removed == (ROSTER[0],) and p.added == (d("X1", "R1", 5, S),)  # fewest duties, lowest id
    assert {c.staff_id for c in p.candidates} == {sid(5), sid(6), sid(8), sid(9)}
    assert unrelated_changes(ROSTER, p.proposed_roster, p.removed, p.added) == ()
    assert check_proposal(ROSTER, p, open_policy(), limits()) == ()
    assert ROSTER == original  # nothing applied
    assert any(u.staff_id == sid(1) and u.reason == "medical leave" for u in p.proposed_data.unavailability)


def test_role_ineligible_staff_are_rejected_with_reason():
    p = propose_replacement(data(), open_policy(), limits(), ROSTER, ROSTER[0], "leave")
    assert "not eligible for SUPERVISOR" in reasons_for(p, 7)


def test_rank_rules_are_respected():
    ranked = [member(n, rank="Rank A" if n in (1, 3, 6, 9) else "Rank B") for n in (1, 2, 3, 4, 5, 6, 8, 9)] + [member(7, INV)]
    policy = open_policy(rank_order=("Rank A", "Rank B"), supervisor_min="Rank A")
    p = propose_replacement(make_input(SESSIONS, ranked), policy, limits(), ROSTER, ROSTER[0], "leave")
    assert p.added == (d("X1", "R1", 9, S),)
    assert "below the SUPERVISOR minimum" in reasons_for(p, 5)


def test_unavailable_staff_are_rejected():
    off = [StaffUnavailability(sid(5), at(8), at(9), "training")]
    p = propose_replacement(data(off), open_policy(), limits(), ROSTER, ROSTER[0], "leave")
    assert "unavailable: training" in reasons_for(p, 5)
    assert p.added == (d("X1", "R1", 8, S),)  # next fewest duties: 8 and 9 have none, 6 has one


def test_restrictions_are_respected():
    policy = open_policy(restrictions=[StaffRestriction(sid(5), "module lecturer", module_code="IT1010")])
    p = propose_replacement(data(), policy, limits(), ROSTER, ROSTER[0], "leave")
    assert "restricted: module lecturer" in reasons_for(p, 5)


def test_overlapping_duties_are_rejected():
    p = propose_replacement(data(), open_policy(), limits(), ROSTER, ROSTER[0], "leave")
    assert "D2" in reasons_for(p, 3) and "D2" in reasons_for(p, 4)  # busy in overlapping X2
    assert "D3" in reasons_for(p, 2)  # already invigilating X1


def test_insufficient_break_is_rejected():
    policy = open_policy(gap=30)
    assert validate_allocation(data(), policy, limits(), ROSTER) == ()
    p = propose_replacement(data(), policy, limits(), ROSTER, ROSTER[5], "leave")  # X3 invigilator
    assert "D2" in reasons_for(p, 3) and "less than 30 minutes apart" in reasons_for(p, 3)
    relaxed = propose_replacement(data(), open_policy(gap=0), limits(), ROSTER, ROSTER[5], "leave")
    assert sid(3) in {c.staff_id for c in relaxed.candidates}


def test_duty_limits_are_respected():
    p = propose_replacement(data(), open_policy(), limits(per_period=2), ROSTER, ROSTER[6], "leave")  # X4 S
    assert "D9" in reasons_for(p, 1)  # already has 2 duties in the period


def test_per_day_limit_rejects_busy_candidates():
    sessions = [session("A", at(8), at(9), "R1"), session("B", at(10), at(11), "R2"), session("C", at(12), at(13), "R3")]
    staff = [member(n) for n in range(1, 6)]
    roster = (d("A", "R1", 1, S), d("A", "R1", 2, I), d("B", "R2", 1, S), d("B", "R2", 3, I),
              d("C", "R3", 4, S), d("C", "R3", 5, I))
    p = propose_replacement(make_input(sessions, staff), open_policy(), limits(per_day=2), roster, roster[4], "leave")
    assert "D8" in reasons_for(p, 1) and p.added == (d("C", "R3", 2, S),)


def test_no_replacement_available():
    sessions = [session("A", at(8), at(10), "R1")]
    roster = (d("A", "R1", 1, S), d("A", "R1", 2, I))
    p = propose_replacement(make_input(sessions, [member(1), member(2), member(3, INV)]), open_policy(), limits(),
                            roster, roster[0], "leave")
    assert p.status is ChangeStatus.INFEASIBLE and p.proposed_roster is None and p.candidates == ()
    assert "No staff member can take SUPERVISOR" in p.reasons[0]
    assert len(p.rejected) == 2  # staff 2 (same session) and 3 (role)


def test_preferred_replacement():
    ok = propose_replacement(data(), open_policy(), limits(), ROSTER, ROSTER[0], "leave", preferred_staff_id=sid(9))
    assert ok.status is ChangeStatus.PROPOSED and ok.added == (d("X1", "R1", 9, S),)
    bad = propose_replacement(data(), open_policy(), limits(), ROSTER, ROSTER[0], "leave", preferred_staff_id=sid(3))
    assert bad.status is ChangeStatus.INFEASIBLE and "D2" in " ".join(bad.reasons)
    assert bad.candidates  # other valid options are still listed


def test_absence_window_covering_other_duties_is_explained():
    p = propose_replacement(data(), open_policy(), limits(), ROSTER, ROSTER[0], "sick all day",
                            unavailable=(at(0), at(23)))
    assert p.status is ChangeStatus.INFEASIBLE and "X3/R3 SUPERVISOR" in p.reasons


def test_absence_window_must_cover_the_duty():
    p = propose_replacement(data(), open_policy(), limits(), ROSTER, ROSTER[0], "late", unavailable=(at(9), at(10)))
    assert p.status is ChangeStatus.INVALID_REQUEST


# --- Swap ----------------------------------------------------------------------------------------

def test_successful_swap():
    p = propose_swap(data(), open_policy(), limits(), ROSTER, ROSTER[5], ROSTER[7])  # 7 (X3 I) <-> 2 (X4 I)
    assert p.status is ChangeStatus.PROPOSED and p.applied is False
    assert set(p.added) == {d("X4", "R4", 7, I), d("X3", "R3", 2, I)}
    assert check_proposal(ROSTER, p, open_policy(), limits()) == ()


def test_swap_into_ineligible_role_is_infeasible():
    p = propose_swap(data(), open_policy(), limits(), ROSTER, ROSTER[5], ROSTER[6])  # 7 would supervise X4
    assert p.status is ChangeStatus.INFEASIBLE and "D6" in {v.constraint for v in p.violations}


def test_swap_creating_overlap_is_infeasible():
    p = propose_swap(data(), open_policy(), limits(), ROSTER, ROSTER[4], ROSTER[2])  # 1 to X2 while in X1
    assert p.status is ChangeStatus.INFEASIBLE and "D2" in {v.constraint for v in p.violations}


def test_swap_breaking_minimum_break_is_infeasible():
    # Staff 1 would take X2 (ends 12:00) while keeping X3 (starts 12:15): 15 < 30 minutes.
    p = propose_swap(data(), open_policy(gap=30), limits(), ROSTER, ROSTER[0], ROSTER[2])
    assert p.status is ChangeStatus.INFEASIBLE and "D2" in {v.constraint for v in p.violations}
    assert propose_swap(data(), open_policy(gap=0), limits(), ROSTER, ROSTER[0], ROSTER[2]).status is ChangeStatus.PROPOSED


@pytest.mark.parametrize("pair", [(0, 4), (0, 99)])
def test_invalid_swap_requests(pair):
    second = ROSTER[pair[1]] if pair[1] < len(ROSTER) else d("X9", "R9", 5, S)
    p = propose_swap(data(), open_policy(), limits(), ROSTER, ROSTER[pair[0]], second)
    assert p.status is ChangeStatus.INVALID_REQUEST  # same staff member, or duty not in roster


def test_invalid_original_roster_is_refused():
    broken = ROSTER[1:]  # X1 has no Supervisor
    p = propose_replacement(data(), open_policy(), limits(), broken, ROSTER[1], "leave")
    assert p.status is ChangeStatus.INVALID_REQUEST and "not valid" in p.reasons[0]


# --- Fairness comparison -------------------------------------------------------------------------------

def fairness_policy() -> DutyFairnessPolicy:
    return DutyFairnessPolicy("synthetic", LoadUnit.DUTIES, 100, 100, 100, 0, MissingAdminHours.ZERO, 0,
                              FairnessWeights(1, 0, 0, 0))


def test_fairness_ranks_candidates_and_reports_before_and_after():
    inputs = FairnessInputs(history=(DutyHistory(sid(5), 6, 0), DutyHistory(sid(8), 6, 0)))
    p = propose_replacement(data(), open_policy(), limits(), ROSTER, ROSTER[0], "leave",
                            fairness=(inputs, fairness_policy()))
    assert p.added == (d("X1", "R1", 9, S),)  # 5 and 8 carry heavy history
    assert p.original_metrics is not None and p.proposed_metrics is not None
    assert [c.metrics.gini for c in p.candidates] == sorted(c.metrics.gini for c in p.candidates)
    assert p.proposed_metrics.gini <= p.original_metrics.gini  # busy person's load moves to an idle one


def test_swap_reports_fairness_change():
    inputs = FairnessInputs(admin=tuple(AdminWorkload(m.staff_id, 0.0) for m in STAFF))
    p = propose_swap(data(), open_policy(), limits(), ROSTER, ROSTER[5], ROSTER[7], fairness=(inputs, fairness_policy()))
    assert p.original_metrics.load_range == p.proposed_metrics.load_range  # duty counts unchanged by a swap


def test_invalid_fairness_argument():
    p = propose_replacement(data(), open_policy(), limits(), ROSTER, ROSTER[0], "leave", fairness=("x",))
    assert p.status is ChangeStatus.INVALID_REQUEST


# --- Starting from a CP-SAT roster --------------------------------------------------------------------

def test_replacement_on_a_solver_generated_roster():
    pytest.importorskip("ortools", reason="install the examination service [solver] extra")
    from app.duty_allocation import allocate_duties
    from app.scheduling.policy import SolverSettings
    sessions = [session(f"D{k}", at(8 + 3 * k), at(10 + 3 * k), f"R{k}a", f"R{k}b") for k in range(3)]
    inp = make_input(sessions, [member(n) for n in range(1, 9)])
    roster = allocate_duties(inp, open_policy(gap=30), limits(per_day=2), SolverSettings(time_limit_seconds=10)).assignments
    p = propose_replacement(inp, open_policy(gap=30), limits(per_day=2), roster, roster[0], "emergency")
    assert p.status is ChangeStatus.PROPOSED
    assert check_proposal(roster, p, open_policy(gap=30), limits(per_day=2)) == ()


# --- Hardening: Step 5 objective, hard AVOID days, fairness inputs, malformed requests ----------------

def weighted(weights=FairnessWeights(1, 0, 0, 0), **changes) -> DutyFairnessPolicy:
    values = dict(source="synthetic", load_unit=LoadUnit.DUTIES, supervisor_weight=100, invigilator_weight=100,
                  history_weight=100, admin_load_per_weekly_hour=0, missing_admin_hours=MissingAdminHours.ZERO,
                  back_to_back_minutes=0, weights=weights)
    return DutyFairnessPolicy(**(values | changes))


AVOID5 = FairnessInputs(preferences=(DayPreference(sid(5), DAY, DayPreferenceKind.AVOID),))


def test_pairwise_balance_term_matches_brute_force():
    inputs = FairnessInputs(history=(DutyHistory(sid(5), 2, 0),))
    loads = [load for _, load in measure_fairness(data(), inputs, weighted(), ROSTER).total_loads]
    brute = sum(abs(a - b) for i, a in enumerate(loads) for b in loads[i + 1:])
    assert fairness_objective(data(), inputs, weighted(), ROSTER) == brute


def test_candidates_ranked_by_configured_weighted_objective():
    # Only back-to-back is weighted: staff 3 and 4 finish X2 at 12:00 and X3 starts 12:15.
    policy = weighted(FairnessWeights(0, 0, 1, 0), back_to_back_minutes=60)
    p = propose_replacement(data(), open_policy(), limits(), ROSTER, ROSTER[5], "leave",
                            fairness=(FairnessInputs(), policy))
    objectives = {c.staff_id: c.objective for c in p.candidates}
    assert objectives[sid(3)] == objectives[sid(4)] == 1 and objectives[sid(5)] == 0
    assert [c.objective for c in p.candidates] == sorted(c.objective for c in p.candidates)
    assert p.added[0].staff_id not in (sid(3), sid(4))
    for c in p.candidates:  # each candidate's score is the Step 5 objective of its full roster
        trial = tuple(a for a in ROSTER if a != ROSTER[5]) + (replace(ROSTER[5], staff_id=c.staff_id),)
        assert c.objective == fairness_objective(p.proposed_data, FairnessInputs(), policy, trial)
    assert p.proposed_objective == p.candidates[0].objective and p.original_objective is not None


def test_soft_preference_weight_lowers_rank_but_keeps_candidate():
    p = propose_replacement(data(), open_policy(), limits(), ROSTER, ROSTER[0], "leave",
                            fairness=(AVOID5, weighted(FairnessWeights(0, 0, 0, 1))))
    assert p.added[0].staff_id != sid(5)  # without the preference, 5 would be chosen (fewest duties, lowest id)
    assert sid(5) in {c.staff_id for c in p.candidates}


def test_hard_avoid_day_blocks_replacement_candidates():
    p = propose_replacement(data(), open_policy(), limits(), ROSTER, ROSTER[0], "leave",
                            fairness=(AVOID5, weighted(hard_avoid_days=True)))
    assert sid(5) not in {c.staff_id for c in p.candidates} and "D11" in reasons_for(p, 5)
    only5 = propose_replacement(data(), open_policy(), limits(), ROSTER, ROSTER[0], "leave",
                                preferred_staff_id=sid(5), fairness=(AVOID5, weighted(hard_avoid_days=True)))
    assert only5.status is ChangeStatus.INFEASIBLE and "D11" in " ".join(only5.reasons)


def test_hard_avoid_day_blocks_swaps():
    day1 = FairnessInputs(preferences=(DayPreference(sid(7), at(9, day=1).date(), DayPreferenceKind.AVOID),))
    hard = propose_swap(data(), open_policy(), limits(), ROSTER, ROSTER[5], ROSTER[7],  # 7 would work X4 on day 1
                        fairness=(day1, weighted(hard_avoid_days=True)))
    assert hard.status is ChangeStatus.INFEASIBLE and "D11" in {v.constraint for v in hard.violations}
    soft = propose_swap(data(), open_policy(), limits(), ROSTER, ROSTER[5], ROSTER[7], fairness=(day1, weighted()))
    assert soft.status is ChangeStatus.PROPOSED


def test_check_proposal_flags_added_duty_on_hard_avoid_day():
    p = propose_replacement(data(), open_policy(), limits(), ROSTER, ROSTER[0], "leave", preferred_staff_id=sid(5))
    assert p.status is ChangeStatus.PROPOSED  # no fairness given, so the AVOID day was not known
    flagged = check_proposal(ROSTER, p, open_policy(), limits(), fairness=(AVOID5, weighted(hard_avoid_days=True)))
    assert [v.constraint for v in flagged] == ["D11"]


def test_missing_admin_hours_reject_is_respected():
    policy = weighted(admin_load_per_weekly_hour=10, missing_admin_hours=MissingAdminHours.REJECT)
    partial = FairnessInputs(admin=(AdminWorkload(sid(1), 4.0),))
    for p in (propose_replacement(data(), open_policy(), limits(), ROSTER, ROSTER[0], "x", fairness=(partial, policy)),
              propose_swap(data(), open_policy(), limits(), ROSTER, ROSTER[5], ROSTER[7], fairness=(partial, policy))):
        assert p.status is ChangeStatus.INVALID_REQUEST and any("admin hours missing" in r for r in p.reasons)
    complete = FairnessInputs(admin=tuple(AdminWorkload(m.staff_id, 1.0) for m in STAFF))
    assert propose_replacement(data(), open_policy(), limits(), ROSTER, ROSTER[0], "x",
                               fairness=(complete, policy)).status is ChangeStatus.PROPOSED


def test_unknown_staff_in_fairness_inputs_is_rejected():
    stranger = FairnessInputs(history=(DutyHistory(sid(99), 1, 1),))
    p = propose_replacement(data(), open_policy(), limits(), ROSTER, ROSTER[0], "x", fairness=(stranger, weighted()))
    assert p.status is ChangeStatus.INVALID_REQUEST and "unknown staff" in p.reasons[0]


@pytest.mark.parametrize("fairness", [5, "ab", (FairnessInputs(),), (FairnessInputs(), "policy"),
                                      ({"history": []}, weighted()), [FairnessInputs(), weighted()]])
def test_malformed_fairness_arguments_are_invalid_requests(fairness):
    for p in (propose_replacement(data(), open_policy(), limits(), ROSTER, ROSTER[0], "x", fairness=fairness),
              propose_swap(data(), open_policy(), limits(), ROSTER, ROSTER[5], ROSTER[7], fairness=fairness)):
        assert p.status is ChangeStatus.INVALID_REQUEST


@pytest.mark.parametrize("call", [
    lambda: propose_replacement(data(), open_policy(), limits(), None, ROSTER[0], "x"),
    lambda: propose_replacement(data(), open_policy(), limits(), 5, ROSTER[0], "x"),
    lambda: propose_replacement(data(), open_policy(), limits(), "roster", ROSTER[0], "x"),
    lambda: propose_replacement(data(), open_policy(), limits(), ROSTER + ({"staff_id": "x"},), ROSTER[0], "x"),
    lambda: propose_replacement(data(), open_policy(), limits(), ROSTER, None, "x"),
    lambda: propose_replacement(data(), open_policy(), limits(), ROSTER, ROSTER[0], 5),
    lambda: propose_replacement(data(), open_policy(), limits(), ROSTER, ROSTER[0], "x", unavailable=(at(8),)),
    lambda: propose_replacement(data(), open_policy(), limits(), ROSTER, ROSTER[0], "x", unavailable="all day"),
    lambda: propose_replacement(data(), open_policy(), limits(), ROSTER, ROSTER[0], "x", unavailable=("a", "b")),
    lambda: propose_replacement(data(), open_policy(), limits(), ROSTER, ROSTER[0], "x", preferred_staff_id=5),
    lambda: propose_replacement("data", open_policy(), limits(), ROSTER, ROSTER[0], "x"),
    lambda: propose_swap(data(), open_policy(), limits(), None, ROSTER[0], ROSTER[1]),
    lambda: propose_swap(data(), open_policy(), limits(), ROSTER, None, ROSTER[1]),
    lambda: propose_swap(data(), {"gap": 0}, limits(), ROSTER, ROSTER[0], ROSTER[1]),
])
def test_malformed_requests_are_invalid_not_exceptions(call):
    assert call().status is ChangeStatus.INVALID_REQUEST


def test_check_proposal_with_malformed_arguments():
    p = propose_replacement(data(), open_policy(), limits(), ROSTER, ROSTER[0], "x")
    assert check_proposal(None, p, open_policy(), limits())[0].constraint == "D_INPUT"
    assert check_proposal(ROSTER, "proposal", open_policy(), limits())[0].constraint == "D_INPUT"
