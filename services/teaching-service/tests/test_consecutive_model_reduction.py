"""P2.3 time-indexed model reductions are exact: same optimum as the successor circuit, fewer variables.

Synthetic settings only. Each reduction omits structures that are provably unread or provably zero.
"""

import re

import pytest
from ortools.sat.python import cp_model

from schedence_teaching.solver import SolverStatus, StaffBlockedInterval

from test_consecutive import policy as consecutive_policy
from test_consecutive_solver import data, options, run
from test_work_period_solver import scenario

CIRCUIT, GRID = "SUCCESSOR_CIRCUIT", "TIME_INDEXED"
EXCESS_ONLY = dict(soft_criteria=["EXCESS_CONTINUOUS_MINUTES"], maximum_preferred_continuous_minutes=60,
                   excess_continuous_minute_penalty=3)
NEIGHBOUR_PARTS = {"st", "en", "busy", "carry_last", "last", "carry_next", "next", "gap_from", "within", "touch", "short", "idle"}


def built(monkeypatch, source, formulation, **updates):
    """Solve and capture every grid variable part name of the first native model."""
    native, names = cp_model.CpSolver.solve, []

    def capture(solver, model, *args, **kwargs):
        if not names:
            names.extend(variable.name for variable in model.proto.variables)
        return native(solver, model, *args, **kwargs)
    monkeypatch.setattr(cp_model.CpSolver, "solve", capture)
    result = run(source, consecutive_config=options(formulation=formulation), **updates)[2]
    monkeypatch.setattr(cp_model.CpSolver, "solve", native)
    parts = [match.groups() for name in names
             if (match := re.match(r"consecutive_grid:LECTURER:[^:]+:([a-z_]+):(.*)", name))]
    return result, parts


def both(monkeypatch, source_factory, **updates):
    circuit, _ = built(monkeypatch, source_factory(), CIRCUIT, **updates)
    grid, parts = built(monkeypatch, source_factory(), GRID, **updates)
    assert circuit.status is grid.status is SolverStatus.OPTIMAL
    assert circuit.consecutive_report.total_weighted_penalty == grid.consecutive_report.total_weighted_penalty
    return circuit, grid, parts


def test_excess_only_scoring_builds_no_neighbour_structures(monkeypatch):
    circuit, grid, parts = both(monkeypatch, lambda: data(count=3, minutes=90),
                                consecutive_policy=consecutive_policy(**EXCESS_ONLY))
    assert grid.consecutive_report.total_weighted_penalty == 90  # A forced 90-minute run: (90 - 60) x 3.
    assert not {part for part, _ in parts} & NEIGHBOUR_PARTS
    assert {"start", "occ", "run", "terminal", "over", "excess"} <= {part for part, _ in parts}


def test_excess_is_modelled_only_where_a_run_can_exceed_the_preferred_maximum(monkeypatch):
    # 15-minute grid, 60-minute preferred maximum: a run ending at slot t is at most (t - low + 1) x 15 minutes.
    _, grid, parts = both(monkeypatch, lambda: data(count=3, minutes=90), consecutive_policy=consecutive_policy(**EXCESS_ONLY))
    slots = sorted(int(rest.split(":")[-1]) for part, rest in parts if part == "excess")
    assert slots and min(slots) == 4  # Slots 0-3 can hold at most 60 continuous minutes.
    assert len(slots) < sum(part == "run" for part, _ in parts)


def test_candidate_minutes_within_the_preferred_maximum_need_no_excess_terms(monkeypatch):
    # Two 30-minute deliveries can never exceed 60 continuous minutes on any day.
    _, grid, parts = both(monkeypatch, lambda: data(count=2, minutes=90), consecutive_policy=consecutive_policy(**EXCESS_ONLY))
    assert grid.consecutive_report.total_weighted_penalty == 0
    assert not any(part in {"terminal", "over", "excess"} for part, _ in parts)


def blocked_scenario(block):
    prepared, config, staff, _, _ = scenario(count=2, minutes=120)
    if block:
        staff.availability[0].blocked_periods = [StaffBlockedInterval(day="DAY", start_minute=30, end_minute=60)]
    return prepared, config, staff


def test_starts_outside_a_lecturers_enforced_domain_create_no_literal(monkeypatch):
    def source(block):
        prepared, config, staff = blocked_scenario(block)
        return (prepared, config), staff
    counts = {}
    for block in (False, True):
        (prepared, config), staff = source(block)
        circuit, _ = built(monkeypatch, (prepared, config), CIRCUIT, staff_input=staff,
                           consecutive_policy=consecutive_policy(**EXCESS_ONLY))
        (prepared, config), staff = source(block)
        grid, parts = built(monkeypatch, (prepared, config), GRID, staff_input=staff,
                            consecutive_policy=consecutive_policy(**EXCESS_ONLY))
        assert circuit.status is grid.status is SolverStatus.OPTIMAL
        assert circuit.consecutive_report.total_weighted_penalty == grid.consecutive_report.total_weighted_penalty
        ticks = {int(rest.split(":")[-1]) for part, rest in parts if part == "start"}
        counts[block] = sum(part == "start" for part, _ in parts)
        if block:
            # A 30-minute delivery may not start at 15, 30 or 45 (it would overlap 30-60); nothing is booked there.
            assert not ticks & {1, 2, 3}
            assert not any(row.start_minute < 60 and row.end_minute > 30 for row in grid.schedule)
    assert counts[True] < counts[False]


@pytest.mark.parametrize("criteria", [
    dict(soft_criteria=["EXCESS_CONTINUOUS_MINUTES"], maximum_preferred_continuous_minutes=45, excess_continuous_minute_penalty=2),
    dict(soft_criteria=["BACK_TO_BACK", "EXCESS_CONTINUOUS_MINUTES"], maximum_preferred_continuous_minutes=60,
         back_to_back_penalty=4, excess_continuous_minute_penalty=1),
    dict(soft_criteria=["SHORT_BREAK", "EXCESS_CONTINUOUS_MINUTES"], preferred_minimum_break_minutes=30,
         short_break_minute_penalty=1, maximum_preferred_continuous_minutes=60, excess_continuous_minute_penalty=2),
])
@pytest.mark.parametrize("block", [False, True])
def test_reduced_grid_matches_the_circuit_for_mixed_criteria_and_blocked_periods(monkeypatch, criteria, block):
    def source():
        prepared, config, staff = blocked_scenario(block)
        return prepared, config, staff
    prepared, config, staff = source()
    circuit, _ = built(monkeypatch, (prepared, config), CIRCUIT, staff_input=staff, consecutive_policy=consecutive_policy(**criteria))
    prepared, config, staff = source()
    grid, _ = built(monkeypatch, (prepared, config), GRID, staff_input=staff, consecutive_policy=consecutive_policy(**criteria))
    assert circuit.status is grid.status is SolverStatus.OPTIMAL
    assert circuit.consecutive_report.total_weighted_penalty == grid.consecutive_report.total_weighted_penalty
    assert dict(circuit.consecutive_report.modeled_entities[0].penalty_components) == dict(
        grid.consecutive_report.modeled_entities[0].penalty_components)


@pytest.mark.parametrize("minutes,active,thin,feasible", [
    (120, 1, 1, False),  # Not thin (meets the minimum): a spurious thin flag is now impossible.
    (120, 1, 0, True),
    (60, 1, 1, True),    # Thin day: the flag is forced.
    (60, 1, 0, False),
    (0, 0, 1, False),    # A day without teaching is never thin.
    (0, 0, 0, True),
])
def test_thin_day_flags_are_exact_at_every_feasible_point(minutes, active, thin, feasible):
    # A run stopped before THIN_TEACHING_DAYS must report the true count, not an unminimized flag.
    from types import SimpleNamespace
    from schedence_teaching.solver.compactness_inputs import add_thin_day_flags
    model = cp_model.CpModel()
    load, on = model.new_int_var(0, 1440, "minutes"), model.new_bool_var("active")
    config = SimpleNamespace(teaching_day_compactness=SimpleNamespace(preferred_minimum_daily_teaching_minutes=120),
                             days=[SimpleNamespace(day="MONDAY")])
    total = add_thin_day_flags(model, config, {("STAFF", 0): (load, on)})
    model.add(load == minutes)
    model.add(on == active)
    model.add(total == thin)
    status = cp_model.CpSolver().solve(model)
    assert (status in (cp_model.OPTIMAL, cp_model.FEASIBLE)) is feasible
