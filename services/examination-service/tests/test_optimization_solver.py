"""Optimization correctness, hard invariants, fallback, and solver/scorer agreement."""
import datetime as dt
import itertools
import json
from dataclasses import replace

import pytest

pytest.importorskip("ortools")
from ortools.sat.python import cp_model

from app.scheduling import (OptimizationPolicy, SchedulingPolicy, SchedulingValidationError,
                            SolverSettings, evaluate_timetable, generate_timetable,
                            optimize_timetable, validate_timetable)
from app.scheduling.__main__ import main
from app.scheduling.availability import minute_offset
from app.scheduling.model import build_hard_model, decode_draft
from app.scheduling.objective import add_objective
from app.scheduling.types import SolveResult, SolveStatus
from optimization_support import optimization_case, starts_on, timed_draft
from scheduling_support import MONDAY, make_case


def only_weight(k):
    return OptimizationPolicy(**{f"s{i}_weight": int(k == i) for i in range(1, 6)})


@pytest.mark.parametrize("component", [0,1,2,3,4,5,6])
async def test_optimizer_matches_exhaustive_independent_scoring(component):
    x = await optimization_case(3, difficulties=(5,5,1), end_date=MONDAY + dt.timedelta(days=2))
    policy = OptimizationPolicy() if component == 6 else only_weight(component)
    choices = tuple(dt.datetime.combine(MONDAY + dt.timedelta(days=d), clock)
                    for d in range(3) for clock in (dt.time(8,30),dt.time(13,30)))
    scores = []
    for assignment in itertools.product(choices, repeat=3):
        draft = timed_draft(x,assignment)
        if not validate_timetable(x,draft):
            scores.append(evaluate_timetable(x,draft,policy).objective_units)
    baseline = timed_draft(x,choices[:3])
    result = optimize_timetable(x,SolverSettings(time_limit_seconds=5),policy,baseline=baseline)
    assert result.status == SolveStatus.OPTIMAL, result
    assert result.selected_evaluation.objective_units == min(scores)
    assert result.selected_evaluation.objective_units <= result.baseline_evaluation.objective_units
    assert validate_timetable(x,result.timetable) == ()
    if not result.improved:
        assert result.timetable is baseline


@pytest.mark.parametrize("days", [(0,1,7,8),(0,4,1,7),(0,1,2,7),(0,1,2,3)])
@pytest.mark.parametrize("component", [1,2,3,4,5,6])
async def test_model_objective_equals_independent_evaluator_for_fixed_timetables(days,component):
    x = await optimization_case()
    policy = only_weight(component) if component <= 5 else OptimizationPolicy(score_scale=23,load_scale=7)
    model, starts, allocated = build_hard_model(x)
    objective = add_objective(model,starts,x,policy)
    for code,start in zip(sorted(starts),starts_on(*days)):
        model.add(starts[code] == minute_offset(x,start))
    solver=cp_model.CpSolver()
    solver.parameters.max_time_in_seconds=3
    solver.parameters.num_search_workers=1
    assert solver.solve(model) == cp_model.OPTIMAL
    measured=evaluate_timetable(x,decode_draft(x,solver,starts,allocated),policy)
    assert measured.objective_units == solver.value(objective)


@pytest.mark.parametrize("minutes", [0,30,60,90])
async def test_optimization_preserves_hard_breaks_and_reading_time(minutes):
    hard=SchedulingPolicy(permitted_start_times=(dt.time(8,30),dt.time(10)),minimum_break_minutes=minutes)
    x=await optimization_case(2,difficulties=(5,5),policy=hard,durations=(60,60),readings=(30,30),
                             end_date=MONDAY)
    r=optimize_timetable(x,SolverSettings(time_limit_seconds=2))
    if minutes:
        assert r.status == SolveStatus.INFEASIBLE and r.timetable is None
    else:
        assert r.timetable is not None and validate_timetable(x,r.timetable) == ()
        assert r.selected_evaluation.minimum_gap_minutes == 0


async def test_multiroom_seats_effective_capacities_blocks_types_calendar_and_reading():
    from app.sources.types import VenueBlock
    block=VenueBlock("R1",MONDAY,dt.time(8,30),dt.time(13,30),"simulated_maintenance","SIMULATED",None)
    x=await optimization_case(2,difficulties=(5,5),candidates=(("A","B","C"),("A","B","C")),
        full_capacities=(4,4),blocks=(block,),durations=(120,120),readings=(10,10),
        excluded=(MONDAY+dt.timedelta(days=1),),venue_types=("computer_lab","computer_lab"),
        exam_types=("computer_based","computer_based"))
    r=optimize_timetable(x,SolverSettings(time_limit_seconds=3))
    assert r.timetable and not validate_timetable(x,r.timetable)
    for s in r.timetable.sessions:
        assert len(s.rooms)==2
        assert (s.end-s.start).total_seconds()==130*60
        assert s.start.weekday()<5 and s.start.date()!=MONDAY+dt.timedelta(days=1)
        assert sum(len(room.seats) for room in s.rooms)==3
        assert all(len(room.seats)<=2 for room in s.rooms)


@pytest.mark.parametrize("count", [1,2,5,9])
async def test_dynamic_module_student_registration_and_venue_counts(count):
    x=await optimization_case(count,difficulties=tuple(1+i%5 for i in range(count)),
        candidates=tuple((f"STUDENT-{i}",) for i in range(count)),
        venue_types=("lecture_hall",)*count,full_capacities=(4,)*count)
    r=optimize_timetable(x,SolverSettings(time_limit_seconds=2))
    assert r.timetable and len(r.timetable.sessions)==count and not validate_timetable(x,r.timetable)
    assert r.selected_evaluation.students==count
    assert r.selected_evaluation.total_weighted_penalty==0


async def test_single_exam_zero_score_keeps_same_baseline_and_is_immutable():
    from dataclasses import FrozenInstanceError
    x=await optimization_case(1,difficulties=(5,))
    baseline=timed_draft(x,starts_on(0))
    r=optimize_timetable(x,SolverSettings(time_limit_seconds=2),baseline=baseline)
    assert r.timetable is baseline and not r.improved and r.status==SolveStatus.OPTIMAL
    assert r.selected_evaluation.total_weighted_penalty==0
    with pytest.raises(FrozenInstanceError): r.improved=True
    assert isinstance(r.timetable.sessions,tuple)
    assert isinstance(r.selected_evaluation.penalties,tuple)


async def test_timeout_keeps_baseline_and_does_not_claim_infeasibility_or_optimality():
    x=await optimization_case()
    baseline=timed_draft(x,starts_on(0,1,7,8))
    r=optimize_timetable(x,SolverSettings(time_limit_seconds=1e-9),baseline=baseline)
    assert r.status==SolveStatus.FEASIBLE and r.optimization_status==SolveStatus.UNKNOWN
    assert r.timetable is baseline and not r.improved
    assert r.objective_bound_units is r.relative_gap is None


async def test_timeout_without_a_baseline_returns_unknown_without_a_draft():
    x=await optimization_case()
    r=optimize_timetable(x,SolverSettings(time_limit_seconds=1e-9))
    assert r.status==SolveStatus.UNKNOWN and r.timetable is None
    assert r.baseline_evaluation is r.selected_evaluation is None


async def test_baseline_unknown_can_continue_search(monkeypatch):
    import app.scheduling.optimization as service
    x=await optimization_case(2,difficulties=(5,5))
    monkeypatch.setattr(service,"generate_timetable",lambda *a:SolveResult(SolveStatus.UNKNOWN,wall_time_seconds=.01))
    r=optimize_timetable(x,SolverSettings(time_limit_seconds=2))
    assert r.timetable and not r.improved and r.baseline is None
    assert not validate_timetable(x,r.timetable)


async def test_shared_budget_subtracts_actual_baseline_search_time(monkeypatch):
    import app.scheduling.optimization as service
    x=await optimization_case(1,difficulties=(5,))
    baseline=timed_draft(x,starts_on(0))
    observed=[]
    def baseline_solver(snapshot,settings):
        observed.append(settings.time_limit_seconds)
        return SolveResult(SolveStatus.OPTIMAL,timetable=baseline,wall_time_seconds=9)
    real_solver=cp_model.CpSolver
    instance=real_solver()
    monkeypatch.setattr(service,"generate_timetable",baseline_solver)
    monkeypatch.setattr(cp_model,"CpSolver",lambda:instance)
    r=optimize_timetable(x,SolverSettings(time_limit_seconds=10))
    assert observed==[1]
    assert instance.parameters.max_time_in_seconds==1
    assert r.solver_time_seconds>=9 and r.timetable is baseline


async def test_insufficient_capacity_and_empty_inputs_are_reported():
    x,_,_=await make_case(candidates=(("A","B","C","D","E"),))
    r=optimize_timetable(x,SolverSettings(time_limit_seconds=2))
    assert r.status==SolveStatus.INFEASIBLE and r.timetable is None
    empty=replace(x,modules=(),registrations=())
    r=optimize_timetable(empty)
    assert r.status==SolveStatus.MODEL_INVALID and r.violations


@pytest.mark.parametrize("field,value", [("difficulty",0),("difficulty",6),("credits",0),("credits",-1),("credits",10**500)])
async def test_optimization_rejects_bad_metadata_without_changing_feasibility_mode(field,value):
    x=await optimization_case(1,difficulties=(3,))
    x=replace(x,modules=(replace(x.modules[0],**{field:value}),))
    assert generate_timetable(x).timetable is not None
    r=optimize_timetable(x)
    assert r.status==SolveStatus.MODEL_INVALID and r.violations[0].constraint=="OPTIMIZATION"


async def test_stale_and_corrupt_baselines_are_rejected():
    x=await optimization_case(1,difficulties=(3,))
    draft=timed_draft(x,starts_on(0))
    for bad in (replace(draft,input_fingerprint="changed"),replace(draft,sessions=())):
        r=optimize_timetable(x,baseline=bad)
        assert r.status==SolveStatus.MODEL_INVALID and r.timetable is None


async def test_invalid_candidate_is_rejected_by_hard_validator_with_safe_fallback(monkeypatch):
    import app.scheduling.model as model
    x=await optimization_case(1,difficulties=(3,))
    baseline=timed_draft(x,starts_on(0))
    monkeypatch.setattr(model,"decode_draft",lambda *a:replace(baseline,sessions=()))
    r=optimize_timetable(x,SolverSettings(time_limit_seconds=2),baseline=baseline)
    assert r.timetable is baseline and r.optimization_status==SolveStatus.MODEL_INVALID
    assert r.status==SolveStatus.FEASIBLE and r.violations


async def test_independent_score_mismatch_never_replaces_baseline(monkeypatch):
    import app.scheduling.objective as objective
    x=await optimization_case(2,difficulties=(5,5))
    baseline=timed_draft(x,starts_on(0,1))
    original=objective.add_objective
    def corrupt(model,*args):
        expr=original(model,*args)+1
        model.minimize(expr)
        return expr
    monkeypatch.setattr(objective,"add_objective",corrupt)
    r=optimize_timetable(x,SolverSettings(time_limit_seconds=3),baseline=baseline)
    assert r.timetable is baseline and r.optimization_status==SolveStatus.MODEL_INVALID
    assert "score" in r.violations[0].message.lower()


async def test_reproducible_single_worker_settings_and_config_fingerprint():
    x=await optimization_case(2,difficulties=(5,5))
    baseline=timed_draft(x,starts_on(0,1))
    a=optimize_timetable(x,SolverSettings(time_limit_seconds=3),baseline=baseline)
    b=optimize_timetable(x,SolverSettings(time_limit_seconds=3),baseline=baseline)
    assert a.selected_evaluation==b.selected_evaluation
    assert a.timetable==b.timetable and a.configuration_fingerprint==b.configuration_fingerprint


async def test_full_synthetic_dataset_compares_the_same_snapshot_and_validates_fallback():
    from app.sources import DatasetSource
    from app.seed.generator import generate_dataset
    from app.seating.memory import MemorySeatingStore
    from app.scheduling import load_snapshot
    x=await load_snapshot(DatasetSource(generate_dataset()),MemorySeatingStore(),period_code="PP1-2026-S2-FINAL")
    baseline=generate_timetable(x).timetable
    original_fingerprint=x.fingerprint
    result=optimize_timetable(x,SolverSettings(time_limit_seconds=1e-9),baseline=baseline)
    assert result.timetable is baseline and not validate_timetable(x,result.timetable)
    assert x.fingerprint==original_fingerprint==result.timetable.input_fingerprint
    assert result.selected_evaluation==result.baseline_evaluation
    assert result.selected_evaluation.students==len({r.student_id for r in x.registrations})
    assert sum(len(room.seats) for s in result.timetable.sessions for room in s.rooms)==len(x.registrations)


@pytest.mark.parametrize("policy", [OptimizationPolicy(score_scale=2**31-1),
                                   OptimizationPolicy(s1_weight=2**31-1,score_scale=2**30)])
async def test_large_integer_objective_returns_structured_error_and_valid_fallback(policy):
    x=await optimization_case()
    baseline=timed_draft(x,starts_on(0,1,7,8))
    result=optimize_timetable(x,SolverSettings(time_limit_seconds=2),policy,baseline=baseline)
    assert result.timetable is baseline and result.status==SolveStatus.FEASIBLE
    assert result.optimization_status==SolveStatus.MODEL_INVALID and result.violations


@pytest.mark.parametrize("settings", [SolverSettings(time_limit_seconds=float("nan")),
                                      SolverSettings(time_limit_seconds=float("inf")),None])
async def test_bad_settings_are_structured_without_solver_execution(settings):
    x=await optimization_case(1,difficulties=(3,))
    r=optimize_timetable(x,settings)
    assert r.status==SolveStatus.MODEL_INVALID and r.timetable is None


def test_cli_requires_opt_in_and_reports_missing_config_without_traceback(capsys):
    with pytest.raises(SystemExit):
        main(["--optimization-config","settings.json"])
    assert json.loads(capsys.readouterr().out)["error"]["code"]=="USAGE_ERROR"
    assert main(["--optimize","--optimization-config","nonexistent-optimization-settings.json"])==1
    assert json.loads(capsys.readouterr().out)["status"]=="MODEL_INVALID"


def test_cli_optional_optimization_reports_comparison_as_strict_json(capsys):
    assert main(["--optimize","--module","IT1010","--time-limit","3"])==0
    def reject(value): raise AssertionError(value)
    payload=json.loads(capsys.readouterr().out,parse_constant=reject)
    assert payload["summary"]["independent_validation_passed"]
    assert payload["baseline_evaluation"] and payload["selected_evaluation"]
    assert len(payload["selected_evaluation"]["penalties"])==5
    assert payload["optimization_policy"]["s1_weight"]==25
    assert "scientifically validated" in payload["objective"]
    assert "baseline" not in payload and "timetable" not in payload
