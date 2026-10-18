"""Independent, hand-calculated workload scoring and configuration tests."""
import datetime as dt
import json
from dataclasses import FrozenInstanceError, asdict, replace

import pytest

from app.scheduling import OptimizationPolicy, SchedulingPolicy, SchedulingValidationError, evaluate_timetable
from app.scheduling.__main__ import main, read_optimization_policy
from app.scheduling.optimization_policy import workload_units
from optimization_support import optimization_case, starts_on, timed_draft
from scheduling_support import MONDAY


@pytest.mark.parametrize("field", tuple(asdict(OptimizationPolicy())))
@pytest.mark.parametrize("value", [None, True, "1", float("nan"), float("inf"), -1])
def test_policy_rejects_malformed_values(field, value):
    with pytest.raises(SchedulingValidationError):
        OptimizationPolicy.from_mapping({field: value})


@pytest.mark.parametrize("settings", [
    {"short_gap_minutes": 0}, {"short_gap_minutes": 2880}, {"difficult_target_minutes": 2880},
    {"high_difficulty_minimum": 6}, {"reference_difficulty": 0}, {"reference_credits": 0},
    {"reference_duration_minutes": 0}, {"window_days": 0}, {"score_scale": 0}, {"load_scale": 0},
    {"baseline_budget_percent": 0}, {"baseline_budget_percent": 100},
    {"difficulty_weight": 0, "credits_weight": 0, "duration_weight": 0}, {"typo": 10},
])
def test_policy_checks_relationships_and_unknown_fields(settings):
    with pytest.raises(SchedulingValidationError):
        OptimizationPolicy.from_mapping(settings)


def test_policy_is_frozen_serializable_and_configuration_identity_changes():
    policy = OptimizationPolicy()
    assert OptimizationPolicy.from_mapping(json.loads(json.dumps(asdict(policy)))) == policy
    assert policy.fingerprint == OptimizationPolicy().fingerprint
    assert replace(policy, s1_weight=0).fingerprint != policy.fingerprint
    with pytest.raises(FrozenInstanceError):
        policy.s1_weight = 1


async def test_load_formula_and_reading_time_are_exact():
    x = await optimization_case(1, difficulties=(3,))
    module = replace(x.modules[0], duration_minutes=110, reading_time_minutes=10, has_extra_reading_time=True)
    assert workload_units(module, OptimizationPolicy()) == 100000
    harder = replace(module, difficulty=5)
    assert workload_units(harder, OptimizationPolicy()) == 133333
    assert workload_units(replace(module, credits=8), OptimizationPolicy()) == 125000
    assert workload_units(replace(module, duration_minutes=230), OptimizationPolicy()) == 125000
    custom = OptimizationPolicy(difficulty_weight=0, credits_weight=100, duration_weight=0)
    assert workload_units(harder, custom) == 100000


async def test_evaluator_recomputes_load_without_the_objective_helper(monkeypatch):
    x=await optimization_case(1,difficulties=(3,),durations=(110,),readings=(10,))
    def fail(*args): raise AssertionError("Independent scoring called objective workload helper")
    monkeypatch.setattr("app.scheduling.optimization_policy.workload_units",fail)
    measured=evaluate_timetable(x,timed_draft(x,starts_on(0)))
    assert measured.maximum_window_load_points==100


@pytest.mark.parametrize("gap,p1,p2", [(0,100,100),(719,100,100),(720,100,100),
    (721,100,99.9),(2879,100,0),(2880,100,0),(2881,99.9,0),(4319,0,0),(4320,0,0)])
async def test_gap_bands_have_no_duplicate_missing_minutes(gap, p1, p2):
    first = dt.datetime.combine(MONDAY, dt.time(8,30))
    second = first + dt.timedelta(minutes=60 + gap)
    hard = SchedulingPolicy(permitted_start_times=tuple(sorted({first.time(), second.time()})), minimum_break_minutes=0)
    x = await optimization_case(2, difficulties=(4,5), policy=hard,
        day_start=dt.time(0), day_end=dt.time(23,59))
    e = evaluate_timetable(x, timed_draft(x, (first,second)))
    assert e.penalties[0].penalty_points == p1
    assert e.penalties[1].penalty_points == p2
    assert e.minimum_gap_minutes == gap


async def test_same_day_is_charged_once_and_difficulty_threshold_is_configurable():
    x = await optimization_case(2, difficulties=(3,4))
    times = (starts_on(0)[0], dt.datetime.combine(MONDAY,dt.time(13,30)))
    draft = timed_draft(x,times)
    e = evaluate_timetable(x,draft)
    assert e.penalties[0].penalty_points == 0
    assert e.penalties[2].penalty_points == 100
    assert e.same_day_student_pairs == e.short_gap_student_pairs == 1
    assert evaluate_timetable(x,draft,OptimizationPolicy(high_difficulty_minimum=3)).penalties[0].penalty_points == 100


async def test_s3_also_detects_a_short_overnight_gap_without_same_day_penalty():
    hard=SchedulingPolicy(permitted_start_times=(dt.time(8,30),dt.time(21,30)))
    x=await optimization_case(2,difficulties=(3,3),policy=hard,day_start=dt.time(0),day_end=dt.time(23,59))
    times=(dt.datetime.combine(MONDAY,dt.time(21,30)),
           dt.datetime.combine(MONDAY+dt.timedelta(days=1),dt.time(8,30)))
    e=evaluate_timetable(x,timed_draft(x,times))
    assert e.same_day_student_pairs==0 and e.short_gap_student_pairs==1
    assert e.minimum_gap_minutes==600 and e.penalties[2].penalty_points==16.6


async def test_s4_detects_heavy_clusters_without_recounting_density_or_single_exam():
    x = await optimization_case()
    clustered = evaluate_timetable(x,timed_draft(x,starts_on(0,1,7,8)))
    spread = evaluate_timetable(x,timed_draft(x,starts_on(0,4,1,7)))
    assert clustered.penalties[3].penalty_points == 100
    assert spread.penalties[3].penalty_points == 0
    assert clustered.penalties[4].penalty_points == spread.penalties[4].penalty_points == 0
    one = await optimization_case(1, difficulties=(5,))
    assert evaluate_timetable(one,timed_draft(one,starts_on(0))).penalties[3].penalty_points == 0


@pytest.mark.parametrize("field,values", [("difficulty",(5,5,1,1)),("credits",(8,8,1,1)),
                                        ("duration_minutes",(180,180,60,60))])
async def test_s4_uses_each_workload_feature(field,values):
    x = await optimization_case(difficulties=(3,)*4)
    x = replace(x,modules=tuple(replace(m,**{field:value}) for m,value in zip(x.modules,values)))
    assert evaluate_timetable(x,timed_draft(x,starts_on(0,1,7,8))).penalties[3].penalty_points == 100


async def test_s5_peak_counts_cluster_once_and_uses_calendar_days():
    x = await optimization_case(difficulties=(3,)*4)
    e = evaluate_timetable(x,timed_draft(x,starts_on(0,1,2,7)))
    assert e.maximum_window_exams == 3
    assert e.penalties[4].penalty_points == 50
    assert e.penalties[3].penalty_points == 0
    e = evaluate_timetable(x,timed_draft(x,starts_on(0,1,3,7)))
    assert e.penalties[4].penalty_points == 0
    friday_monday = evaluate_timetable(x,timed_draft(x,starts_on(0,3,4,7)))
    assert friday_monday.maximum_window_exams == 2


async def test_large_window_and_zero_weights_are_supported():
    x = await optimization_case()
    policy=OptimizationPolicy(window_days=2**31-1,s1_weight=0,s2_weight=0,s3_weight=0,s4_weight=0,s5_weight=0)
    e=evaluate_timetable(x,timed_draft(x,starts_on(0,1,7,8)),policy)
    assert e.maximum_window_exams == 4 and e.total_weighted_penalty == 0


async def test_repeat_status_has_no_multiplier_and_unused_students_do_not_change_scores():
    x = await optimization_case()
    draft=timed_draft(x,starts_on(0,1,7,8))
    original=evaluate_timetable(x,draft)
    normal=replace(x,registrations=tuple(replace(r,registration_type="normal") for r in x.registrations))
    other=evaluate_timetable(normal,replace(draft,input_fingerprint=normal.fingerprint))
    assert original.penalties == other.penalties
    assert other.repeat_student_mean_weighted_penalty is None
    extra=replace(x,students=(*x.students,replace(x.students[0],student_id="UNREGISTERED")))
    assert evaluate_timetable(extra,replace(draft,input_fingerprint=extra.fingerprint)) == original


async def test_equal_registration_patterns_preserve_per_student_normalization():
    a = await optimization_case(2,difficulties=(5,5))
    b = await optimization_case(2,difficulties=(5,5),candidates=(("A","B"),("A","B")))
    ea=evaluate_timetable(a,timed_draft(a,starts_on(0,1)))
    eb=evaluate_timetable(b,timed_draft(b,starts_on(0,1)))
    assert ea.penalties == eb.penalties
    assert eb.objective_units == 2 * ea.objective_units


async def test_invalid_or_stale_drafts_cannot_be_scored():
    x=await optimization_case(1,difficulties=(3,))
    d=timed_draft(x,starts_on(0))
    for bad in (replace(d,sessions=()),replace(d,input_fingerprint="stale")):
        with pytest.raises(SchedulingValidationError): evaluate_timetable(x,bad)


@pytest.mark.parametrize("text", ['NaN','[]','{"s1_weight":NaN}','{"s1_weight":Infinity}',
                                 '{"s1_weight":1,"s1_weight":2}','{"unknown":1}','{"window_days":true}'])
def test_cli_configuration_rejects_invalid_standard_json(text,monkeypatch,capsys):
    monkeypatch.setattr("pathlib.Path.read_text",lambda *a,**k:text)
    assert main(["--optimize","--optimization-config","settings.json"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "MODEL_INVALID"


def test_cli_reads_updated_configuration_on_each_run(monkeypatch):
    monkeypatch.setattr("pathlib.Path.read_text",lambda *a,**k:'{"s1_weight":0,"window_days":5}')
    a=read_optimization_policy("settings.json")
    monkeypatch.setattr("pathlib.Path.read_text",lambda *a,**k:'{"s1_weight":99,"window_days":2}')
    b=read_optimization_policy("settings.json")
    assert (a.s1_weight,a.window_days)==(0,5)
    assert (b.s1_weight,b.window_days)==(99,2)
