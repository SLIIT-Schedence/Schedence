"""P2.3 exact time-indexed consecutive scoring versus the original successor circuit.

All break values here are explicitly synthetic test settings, never campus policy.
"""

import pytest
from pydantic import ValidationError

from schedence_teaching.solver import (ConsecutiveOptimizationConfig, SolverInputError, SolverStatus,
                                       TimetableValidationError, solve_timetable)

from test_consecutive import hard, policy as consecutive_policy, scoring
from test_consecutive_solver import data, options, run
from test_preferences import calendar
from test_staff_solver import demand, windows

CIRCUIT, GRID = "SUCCESSOR_CIRCUIT", "TIME_INDEXED"


def solve_both(source_factory, *, config=None, **updates):
    output = {}
    for formulation in (CIRCUIT, GRID):
        output[formulation] = run(source_factory(), consecutive_config=options(formulation=formulation, **(config or {})),
                                  **updates)[2]
    circuit, grid = output[CIRCUIT], output[GRID]
    assert circuit.status is grid.status
    if circuit.status is SolverStatus.OPTIMAL:
        assert circuit.consecutive_report.total_weighted_penalty == grid.consecutive_report.total_weighted_penalty
    return circuit, grid


def components(result, kind="LECTURER"):
    rows = [row for row in result.consecutive_report.modeled_entities if row.kind == kind]
    return {row.entity_id: dict(row.penalty_components) for row in rows}


def test_back_to_back_and_touching_short_break_are_scored_identically():
    # 2 x 30 minutes in a 60-minute window must touch: 1 pair (5) and a full 15-minute shortfall (15 x 2).
    circuit, grid = solve_both(lambda: data(count=2, minutes=60))
    expected = {"BACK_TO_BACK": 5, "SHORT_BREAK": 30, "EXCESS_CONTINUOUS_MINUTES": 0}
    assert components(circuit) == components(grid) == {"STAFF": expected}


def test_partial_short_break_uses_the_exact_shortfall():
    # Preferred 30-minute break on a 15-minute grid: a 15-minute gap leaves a 15-minute shortfall (x 2).
    policy = consecutive_policy(soft_criteria=["SHORT_BREAK", "BACK_TO_BACK"], preferred_minimum_break_minutes=30,
                                back_to_back_penalty=5, short_break_minute_penalty=2)
    circuit, grid = solve_both(lambda: data(count=2, minutes=75), consecutive_policy=policy)
    assert components(circuit) == components(grid) == {"STAFF": {"BACK_TO_BACK": 0, "SHORT_BREAK": 30}}


def test_continuous_teaching_is_charged_once_per_maximal_run():
    circuit, grid = solve_both(lambda: data(count=3, minutes=90))
    assert components(grid)["STAFF"]["EXCESS_CONTINUOUS_MINUTES"] == 90  # (90 - 60) x 3
    assert components(circuit) == components(grid)
    for result in (circuit, grid):
        entity = result.consecutive_report.modeled_entities[0]
        assert entity.maximum_continuous_minutes == 90 and len(entity.terminal_delivery_ids) == 1


def test_optional_idle_gap_scoring_matches():
    policy = scoring(idle_gap_minute_penalty=1, idle_gap_policy_reference="synthetic:test-only-idle")
    circuit, grid = solve_both(lambda: data(count=2, minutes=90), consecutive_policy=policy)
    assert components(circuit) == components(grid)
    assert components(grid)["STAFF"]["IDLE_GAP_MINUTES"] == 15  # A 15-minute gap beats touching (5 + 30).


def test_cross_day_neighbours_create_no_consecutive_link():
    circuit, grid = solve_both(lambda: data(count=2, minutes=30, days=("D1", "D2")))
    for result in (circuit, grid):
        entity = result.consecutive_report.modeled_entities[0]
        assert result.consecutive_report.total_weighted_penalty == 0
        assert [(row.comparable, row.gap_minutes) for row in entity.successors] == [(False, None)]


def test_student_group_hierarchy_is_scored_identically():
    def source():
        prepared = demand({"A": [("P", "LIC")], "B": [("Q", "LIC")]}, [("A", "G", 1), ("B", "C", 1)], parents={"C": "G"})
        return prepared, windows(60)
    policy = scoring(score_lecturers=False, score_student_groups=True)
    circuit, grid = solve_both(source, consecutive_policy=policy)
    # Child C inherits the parent's lecture and its own, which must touch; parent G only sees one delivery.
    assert components(circuit, "STUDENT_GROUP") == components(grid, "STUDENT_GROUP")
    assert components(grid, "STUDENT_GROUP")["C"]["BACK_TO_BACK"] == 5 and components(grid, "STUDENT_GROUP")["G"]["BACK_TO_BACK"] == 0


@pytest.mark.parametrize("minutes,status", [(75, SolverStatus.OPTIMAL), (60, SolverStatus.INFEASIBLE)])
def test_confirmed_hard_break_is_enforced_identically(minutes, status):
    circuit, grid = solve_both(lambda: data(minutes=minutes), consecutive_policy=scoring(confirmed_hard_minimum_break=hard()),
                               config=dict(enforce_confirmed_hard_break=True))
    assert circuit.status is grid.status is status


def test_online_lectures_are_scored_like_physical_ones():
    from test_online_attendance_solver import problem

    def source():
        prepared, config, _ = problem(mode="ONLINE", shared_staff=True, start=510, end=630)
        return prepared, config
    circuit, grid = solve_both(source)
    assert all(row.delivery_mode.value == "ONLINE" for row in grid.schedule)
    assert components(circuit) == components(grid)  # Online lectures remain in the lecturer's sequence.


def test_eligibility_clashes_and_venues_hold_under_the_time_indexed_model():
    from test_teaching_assignment_policy import policy
    from test_venue_solver import physical
    from test_work_period_solver import scenario
    from schedence_teaching.solver import TimeWindow
    prepared, config, staff, *_ = scenario(prepared=demand({"A": [("P", "LIC")], "B": [("P", "LIC")]},
        [("A", "G1", 2), ("B", "G2", 2)]), minutes=240, days=("D1", "D2"), duration=60, band="LECTURER")
    staff.assignment_policy = policy(staff)
    rooms = physical([("R1", 30, "Lecture Hall")])
    rooms.calendars[0].available_windows = {"D1": [TimeWindow(start_minute=0, end_minute=240)], "D2": []}
    _, _, result, _ = run((prepared, config), staff_input=staff, venue_input=rooms,
                          consecutive_config=options(formulation=GRID))
    assert result.status is SolverStatus.OPTIMAL and {row.day for row in result.schedule} == {"D1"}
    assert {"staff_clashes", "same_group_and_ancestor_clashes", "rank_appointment_session_eligibility"} <= set(result.validation.checks)


def test_ninety_six_candidates_are_modelled_without_dropping_and_without_successor_arcs():
    groups = 8
    prepared = demand({f"O{g}": [("P", "LIC")] for g in range(groups)}, [(f"O{g}", f"G{g}", 12) for g in range(groups)])
    config = windows(720, days=("D1", "D2", "D3", "D4", "D5", "D6", "D7"), limit=5.0)
    context = dict(consecutive_policy=scoring(), consecutive_calendar=calendar(config))
    with pytest.raises(SolverInputError, match="nodes-per-participant"):
        solve_timetable(prepared, config, **context, consecutive_config=options())  # Original circuit preflight.
    diagnostics = {}
    result = solve_timetable(prepared, config, **context, consecutive_config=options(formulation=GRID),
                             diagnostics=diagnostics)
    assert len(prepared.sessions) == 96 and diagnostics["model_variables"] > 0
    # A built model is not a proof: within 5 s the status is whatever CP-SAT actually returned.
    assert result.status in {SolverStatus.OPTIMAL, SolverStatus.FEASIBLE, SolverStatus.UNKNOWN}
    if result.schedule:
        assert len(result.schedule) == 96 and result.consecutive_report.estimated_successor_arcs == 0
        assert len(result.consecutive_report.modeled_entities[0].selected_delivery_ids) == 96


def test_validator_detects_a_broken_time_indexed_model(monkeypatch):
    from schedence_teaching.solver import consecutive_time_indexed as grid
    original = grid._and
    monkeypatch.setattr(grid, "_and", lambda model, literals, name: model.new_bool_var(name) if ":touch:" in name
                        else original(model, literals, name))
    with pytest.raises(TimetableValidationError, match="modeled_consecutive"):
        run(data(minutes=60), consecutive_config=options(formulation=GRID))


def test_formulation_is_explicit_and_legacy_payloads_are_unchanged():
    legacy = options()
    assert "formulation" not in legacy.model_dump(round_trip=True) and "formulation" not in legacy.model_dump_json()
    assert options(formulation=GRID).model_dump(round_trip=True)["formulation"] == GRID
    with pytest.raises(ValidationError, match="formulation"):
        ConsecutiveOptimizationConfig.model_validate({**legacy.model_dump(round_trip=True), "formulation": "APPROXIMATE"})


def test_quality_stages_with_consecutive_optimization_remain_explicitly_rejected():
    from test_daily_load_distribution import daily
    from test_teaching_day_compactness import compact
    from test_work_period_solver import scenario
    prepared, config, staff, *_ = scenario(prepared=demand({"A": [("P", "LIC")]}, [("A", "G", 2)]), minutes=240,
                                          days=("D1", "D2"), duration=60)
    for quality in (compact(config), daily(config)):
        with pytest.raises(SolverInputError, match="not yet combined with consecutive"):
            solve_timetable(prepared, quality, staff_input=staff, consecutive_policy=scoring(),
                            consecutive_calendar=calendar(quality), consecutive_config=options(formulation=GRID))
