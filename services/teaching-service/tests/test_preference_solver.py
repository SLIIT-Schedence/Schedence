"""Opt-in preference CP-SAT, independent scoring, bounded priorities and hard protection."""

from dataclasses import replace
from datetime import timedelta
from pathlib import Path
import runpy

import pytest
from ortools.sat.python import cp_model
from pydantic import ValidationError

from schedence_teaching.preferences import (
    LecturerPreferenceInput, PreferenceCalendar, PreferenceScoringPolicy,
    prepare_lecturer_preferences, score_lecturer_preferences,
)
from schedence_teaching.preparation import prepare_group_combinations
from schedence_teaching.schemas import GroupCombinationInput, Stream
from schedence_teaching.solver import (
    CombinationSolverConfig, DeliveryObjective, PreferenceOptimizationConfig, PreferencePriority,
    SolverInputError, SolverStatus, TimetableValidationError, TimeWindow,
    solve_timetable, validate_delivery_result, validate_preference_result,
    validate_staff_policy_result, validate_work_period_result, validate_timetable,
)
from schedence_teaching.workload import assess_weekly_workload, prepare_work_periods

from test_preferences import MONDAY, calendar, policy, preference
from test_staff_solver import demand, windows
from test_combination_solver import inputs as combination_inputs, venues
from test_group_combinations import make_inputs, shared_content
from test_work_period_solver import scenario, with_flexible
from test_work_periods import FIRST, admin, research, research_policy


def options(config, **updates):
    return PreferenceOptimizationConfig.model_validate({
        "priority_policy_reference": "synthetic:development-teaching-before-preferences",
        "calendar_semantics_reference": "synthetic:explicit-day-and-stream-interpretation",
        "day_streams": {row.day: set(row.windows) for row in config.days},
        "unscoped_preferences_apply_to_all_streams": True, **updates})


def inputs(prepared, config, records=None, *, dates=None, scoring=None, optimization=None):
    return {"preference_input": prepare_lecturer_preferences(prepared.input_snapshot,
                LecturerPreferenceInput(preferences=records if records is not None else [preference()])),
        "preference_calendar": dates or calendar(config),
        "preference_policy": scoring or policy(weight_mode="SYNTHETIC_DEFAULTS", synthetic_default_weight=3),
        "preference_config": optimization or options(config)}


def run(prepared=None, config=None, records=None, *, dates=None, scoring=None, optimization=None, **hard):
    prepared = prepared or demand({"OFFER": [("STAFF", "LIC")]})
    config = config or windows()
    context = inputs(prepared, config, records, dates=dates, scoring=scoring, optimization=optimization)
    before = repr((prepared, config, context, hard))
    result = solve_timetable(prepared, config, **context, **hard)
    assert repr((prepared, config, context, hard)) == before
    assert result.preference_optimization_enabled
    summary = validate_preference_result(prepared, config, result, **context, **hard)
    if result.status in {SolverStatus.OPTIMAL, SolverStatus.FEASIBLE}:
        assert summary == result.validation
        score = score_lecturer_preferences(prepared, config, result, context["preference_input"],
            context["preference_calendar"], context["preference_policy"],
            preference_config=context["preference_config"], **hard)
        assert score == result.preference_report.score and score.cp_sat_preference_objective_enabled
        assert score.weighted_scoring_complete and score.unresolved_count == 0
        assert result.preference_report.objective_status == result.status.value
        assert result.preference_report.objective_value == sum(dict(result.preference_report.objective_components)[key] * value
            for key, value in result.preference_report.objective_coefficients)
    else:
        assert summary is None and result.preference_report is result.work_period_report is result.workload_report is None
        assert result.schedule == result.administrative_schedule == ()
    assert not result.to_dict()["university_ready"]
    return prepared, config, result, context


@pytest.mark.parametrize("label,direction,earliest,latest", [
    ("MORNING", "PREFER", 0, 30), ("AFTERNOON", "PREFER", 60, 90),
    ("MORNING", "AVOID", 60, 90), ("AFTERNOON", "AVOID", 0, 30)])
def test_time_of_day_preferences_change_feasible_start(label, direction, earliest, latest):
    scoring = policy(weight_mode="SYNTHETIC_DEFAULTS", synthetic_default_weight=2,
        time_of_day_windows={"MORNING": {"start_minute": 0, "end_minute": 30},
                            "AFTERNOON": {"start_minute": 60, "end_minute": 90}},
        time_window_policy_reference="synthetic:day-parts")
    _, _, result, _ = run(config=windows(90), records=[preference(category="TIME_OF_DAY", time_of_day=label,
        direction=direction)], scoring=scoring)
    assert result.preference_report.score.violation_count == 0
    row = result.schedule[0]
    if direction == "PREFER":
        assert (row.start_minute, row.end_minute) == (earliest, latest)
    else:
        assert row.end_minute <= 60 if label == "AFTERNOON" else row.start_minute >= 30


@pytest.mark.parametrize("preferred", ["SATURDAY", "SUNDAY"])
def test_weekend_preference_selects_actual_calendar_day(preferred):
    config = windows(30, days=("SATURDAY", "SUNDAY"))
    _, _, result, _ = run(config=config, dates=calendar(config, first=MONDAY + timedelta(days=5)),
        records=[preference(category="WEEKEND_DAY", days=[preferred])])
    assert result.schedule[0].day == preferred and result.preference_report.score.violation_count == 0


def test_preferred_free_day_avoids_teaching_without_attendance_implications():
    config = windows(30, days=("MONDAY", "TUESDAY"))
    _, _, result, _ = run(config=config, records=[preference(direction="AVOID")])
    assert result.schedule[0].day == "TUESDAY" and result.workload_report is None


@pytest.mark.parametrize("direction", ["PREFER", "AVOID"])
def test_explicit_clock_window_scoring(direction):
    _, _, result, _ = run(records=[preference(category="TEACHING_WINDOW", direction=direction,
        time_window={"start_minute": 30, "end_minute": 60})])
    assert result.preference_report.score.total_weighted_penalty == 0
    assert result.schedule[0].start_minute == (30 if direction == "PREFER" else 0)


def test_both_preferred_day_parts_are_one_alternative_not_competing_obligations():
    rows = [preference(identity=label, category="TIME_OF_DAY", time_of_day=label,
        alternative_group_id="DAY-PART-CHOICES") for label in ("MORNING", "AFTERNOON")]
    scoring = policy(weight_mode="SYNTHETIC_DEFAULTS", synthetic_default_weight=3,
        time_of_day_windows={"MORNING": {"start_minute": 0, "end_minute": 30},
                            "AFTERNOON": {"start_minute": 30, "end_minute": 60}}, time_window_policy_reference="synthetic:bands")
    _, _, result, _ = run(records=rows, scoring=scoring)
    report = result.preference_report
    assert report.configured_criteria_count == report.score.evaluated_count == report.score.satisfied_count == 1
    assert report.score.total_weighted_penalty == 0
    assert report.score.evaluations[0].preference_ids == ("MORNING", "AFTERNOON")


@pytest.mark.parametrize("direction,window_end,penalty", [("PREFER", 20, 3), ("AVOID", 20, 3),
    ("PREFER", 31, 0), ("AVOID", 1, 3)])
def test_crossing_non_grid_boundaries_matches_source_minute_scorer(direction, window_end, penalty):
    _, _, result, _ = run(config=windows(30), records=[preference(category="TEACHING_WINDOW", direction=direction,
        time_window={"start_minute": 0, "end_minute": window_end})])
    assert result.preference_report.score.total_weighted_penalty == penalty
    assert result.status is SolverStatus.OPTIMAL


@pytest.mark.parametrize("kind", ["availability", "admin", "research", "caps", "full_day"])
def test_hard_protection_overrides_preferences(kind):
    data = scenario(minutes=60)
    prepared, config, staff, periods, work_config = data
    if kind == "availability":
        staff.availability[0].available_windows["DAY"] = [TimeWindow(start_minute=30, end_minute=60)]
    elif kind == "admin":
        periods.administrative_periods = [admin(staff.workload_input,
            window={"start_minute": 0, "end_minute": 30}, planned_duration_minutes=30)]
    elif kind in {"research", "full_day"}:
        periods.research_policies = [research_policy()]
        periods.research_reservations = [research(on_date=FIRST, full_day=kind == "full_day",
            windows=[] if kind == "full_day" else [{"start_minute": 0, "end_minute": 30}])]
    else:
        staff.availability[0].weekly_teaching_max_minutes = 0
    work = prepare_work_periods(prepared.input_snapshot, staff.workload_input, periods)
    hard = dict(staff_input=staff, work_periods=work, work_period_config=work_config)
    _, _, result, context = run(prepared, config, records=[preference(category="TEACHING_WINDOW",
        time_window={"start_minute": 0, "end_minute": 30})], **hard)
    if kind in {"caps", "full_day"}:
        assert result.status is SolverStatus.INFEASIBLE
    else:
        assert result.schedule[0].start_minute == 30 and result.preference_report.score.violation_count == 1
        assert result.work_period_report.staff[0].verified_completed_administrative_minutes == 0
        assert validate_work_period_result(prepared, config, result, **hard, **context) == result.validation
        assert assess_weekly_workload(prepared, config, result, staff.workload_input, **hard, **context) == result.workload_report


def test_mandatory_flexible_admin_and_preferences_share_staff_without_work_credit():
    prepared, config, staff, source, work_config = scenario(minutes=270)
    with_flexible(staff, source, eligible=[{"on_date": FIRST, "start_minute": 0, "end_minute": 240}])
    periods = prepare_work_periods(prepared.input_snapshot, staff.workload_input, source)
    _, _, result, _ = run(prepared, config, records=[preference(category="TEACHING_WINDOW",
        time_window={"start_minute": 0, "end_minute": 30})], staff_input=staff, work_periods=periods,
        work_period_config=work_config)
    assert result.schedule[0].start_minute == 240 and result.preference_report.score.violation_count == 1
    assert result.work_period_report.staff[0].reserved_administrative_minutes == 240
    assert result.work_period_report.staff[0].verified_completed_administrative_minutes == 0


@pytest.mark.parametrize("delivery_mode,count", [("combined", 2), ("separate", 4), ("mixed", 3)])
def test_combined_separate_mixed_cost_once_per_actual_delivery(delivery_mode, count):
    combinations = prepare_group_combinations(combination_inputs())
    rooms = venues(20) if delivery_mode == "separate" else venues(large_minutes=30) if delivery_mode == "mixed" else venues()
    _, config, result, context = run(combinations.prepared, windows(120), records=[preference(direction="AVOID")],
        combination_input=combinations, venue_input=rooms)
    assert result.preference_report.score.violation_count == count
    assert result.preference_report.score.total_weighted_penalty == 3 * count
    assert len(result.preference_report.modeled_evaluations) == count
    assert result.preference_report.score.staff[0].scheduled_teaching_minutes == count * 30
    assert result.objective_value == count * 121 + count * 30
    assert result.objective_status == "EVALUATED_COMPONENT"
    assert validate_delivery_result(combinations.prepared, config, result, combination_input=combinations,
        venue_input=rooms, **context) == result.validation


def test_multiple_staff_get_only_their_own_preference_penalties():
    prepared = demand({"A": [("A", "LIC")], "B": [("B", "LIC")]})
    rows = [preference(staff_id="A", identity="A", category="TEACHING_WINDOW", time_window={"start_minute": 0, "end_minute": 30}),
            preference(staff_id="B", identity="B", category="TEACHING_WINDOW", time_window={"start_minute": 30, "end_minute": 60})]
    _, _, result, _ = run(prepared, records=rows)
    assert {row.assigned_staff_id: row.start_minute for row in result.schedule} == {"A": 0, "B": 30}
    assert result.preference_report.score.total_weighted_penalty == 0
    assert {(row.staff_id, row.preference_ids) for row in result.preference_report.modeled_evaluations} == {("A", ("A",)), ("B", ("B",))}


def priority_source():
    return prepare_group_combinations(shared_content(combination_inputs(frequency=1, separate_offerings=True,
        staff_pools={"OFFER-1": ["A", "COMMON"], "OFFER-2": ["B", "COMMON"]})))


@pytest.mark.parametrize("priority,count,penalty", [("TEACHING_THEN_PREFERENCES", 1, 100000),
    ("PREFERENCES_THEN_TEACHING", 2, 0)])
def test_delivery_count_priority_is_bounded_not_an_arbitrary_weighted_sum(priority, count, penalty):
    combinations = priority_source()
    config = windows(90)
    _, _, result, _ = run(combinations.prepared, config,
        records=[preference(staff_id="COMMON", direction="AVOID")], combination_input=combinations,
        scoring=policy(weight_mode="SYNTHETIC_DEFAULTS", synthetic_default_weight=100000),
        optimization=options(config, priority=priority))
    assert len(result.schedule) == count and result.preference_report.score.total_weighted_penalty == penalty
    if count == 2:
        assert {row.assigned_staff_id for row in result.schedule} == {"A", "B"}
        assert result.preference_report.modeled_evaluations == ()  # No charge to unselected COMMON alternatives.


def minute_priority_source():
    pools = {"OFFER-1": ["S1", "CS"], "OFFER-2": ["S2", "CS"],
             "OFFER-3": ["L1", "CL"], "OFFER-4": ["L2", "CL"]}
    payload = make_inputs(4, sizes=[20] * 4, separate_offerings=True, staff_pools=pools).model_dump(round_trip=True)
    for index, row in enumerate(payload["teaching_input"]["session_requirements"]):
        row.update(duration_minutes=30 if index < 2 else 60, sessions_per_week=1)
    payload["requirement_permissions"] = [{"requirement_id": f"REQ{index}", "shared_content_reference": "SHORT" if index < 3 else "LONG"}
                                          for index in range(1, 5)]
    return prepare_group_combinations(GroupCombinationInput.model_validate(payload))


@pytest.mark.parametrize("priority,minutes,penalty", [("TEACHING_THEN_PREFERENCES", 120, 10000),
    ("DELIVERIES_THEN_PREFERENCES_THEN_MINUTES", 150, 0)])
def test_contact_minutes_beat_preference_penalty_at_equal_delivery_count(priority, minutes, penalty):
    combinations = minute_priority_source()
    config = windows(240)
    rooms = venues(minutes=240, large_minutes=60)
    _, _, result, _ = run(combinations.prepared, config, records=[preference(staff_id="CL", direction="AVOID")],
        combination_input=combinations, venue_input=rooms, optimization=options(config, priority=priority),
        scoring=policy(weight_mode="SYNTHETIC_DEFAULTS", synthetic_default_weight=10000))
    assert len(result.schedule) == 3
    assert result.preference_report.score.total_weighted_penalty == penalty
    assert dict(result.preference_report.objective_components)["CONTACT_MINUTES"] == minutes


@pytest.mark.parametrize("mode,order", [("DELIVERIES_THEN_MINUTES", ("DELIVERIES", "CONTACT_MINUTES", "PREFERENCE_PENALTY")),
    ("DELIVERIES", ("DELIVERIES", "PREFERENCE_PENALTY")), ("CONTACT_MINUTES", ("CONTACT_MINUTES", "PREFERENCE_PENALTY")),
    ("FEASIBILITY", ("PREFERENCE_PENALTY",))])
def test_existing_combination_objective_modes_are_preserved(mode, order):
    combinations = prepare_group_combinations(combination_inputs())
    _, _, result, _ = run(combinations.prepared, windows(120), combination_input=combinations,
        combination_config=CombinationSolverConfig(objective=mode))
    assert result.preference_report.priority_order == order


@pytest.mark.parametrize("missing", ["preparation", "calendar", "weight_policy", "config", "day_streams", "stream_scope", "scope", "boundaries", "confirmation", "weight", "applicability"])
def test_missing_or_ambiguous_input_is_rejected_without_hard_fabrication(missing):
    prepared, config = demand({"OFFER": [("STAFF", "LIC")]}), windows()
    context = inputs(prepared, config)
    if missing == "preparation":
        context["preference_input"] = None
    elif missing == "calendar":
        context["preference_calendar"] = None
    elif missing == "weight_policy":
        context["preference_policy"] = None
    elif missing == "config":
        context["preference_config"] = None
    elif missing == "day_streams":
        context["preference_config"].day_streams["DAY"] = {Stream.WEEKEND}
    elif missing == "stream_scope":
        context["preference_config"].unscoped_preferences_apply_to_all_streams = False
    elif missing == "scope":
        context["preference_input"].input_snapshot.preferences[0].scope = None
    elif missing == "boundaries":
        context["preference_input"] = inputs(prepared, config,
            [preference(category="TIME_OF_DAY", time_of_day="MORNING")])["preference_input"]
    elif missing == "confirmation":
        context["preference_calendar"].synthetic_data = False
        context["preference_policy"] = policy(weight_mode="CONFIRMED_WEIGHTS")
    elif missing == "weight":
        context["preference_policy"] = policy(weight_mode="CONFIRMED_WEIGHTS")
    else:
        context["preference_input"] = inputs(prepared, config, [preference(scope={"week_reference": "OTHER"})])["preference_input"]
    with pytest.raises(SolverInputError):
        solve_timetable(prepared, config, **context)


def test_real_mode_requires_confirmed_contracts_explicit_weight_and_semantics():
    config = windows()
    _, _, result, _ = run(config=config, records=[preference(direction="AVOID", confirmation_status="CONFIRMED",
        penalty_weight=5, weight_reference="supplied:confirmed-weight")], dates=calendar(config, synthetic_data=False),
        scoring=policy(weight_mode="CONFIRMED_WEIGHTS"))
    assert result.preference_report.confirmed_criteria_count == 1
    assert result.preference_report.to_dict()["total_confirmed_preference_penalty"] == 5


def test_synthetic_weights_are_never_activated_for_real_data():
    config = windows()
    with pytest.raises(SolverInputError, match="Synthetic default"):
        run(config=config, dates=calendar(config, synthetic_data=False))


def test_unweighted_policy_does_not_implicitly_enable_unit_penalties():
    with pytest.raises(SolverInputError, match="weighted policy"):
        run(scoring=policy())


def test_disabled_mode_retains_old_objective_and_has_no_preference_report():
    combinations = prepare_group_combinations(combination_inputs())
    config = windows(120)
    old = solve_timetable(combinations.prepared, config, combination_input=combinations)
    assert old.preference_report is None and not old.preference_optimization_enabled
    assert old.objective_value == 2 * 121 + 60 and old.objective_status == "OPTIMAL"


@pytest.mark.parametrize("corruption", ["total", "staff", "priority", "coefficients", "criteria", "score_count", "unselected", "missing", "wrong_staff", "bool_penalty", "mode", "status"])
def test_independent_validator_detects_corrupted_scores_and_model_charges(corruption):
    prepared, config, result, context = run(records=[preference(direction="AVOID")])
    report = result.preference_report
    if corruption == "total":
        report = replace(report, objective_value=report.objective_value + 1)
    elif corruption == "staff":
        report = replace(report, score=replace(report.score, staff=(replace(report.score.staff[0], violation_count=0),)))
    elif corruption == "priority":
        report = replace(report, priority_order=("PREFERENCE_PENALTY",))
    elif corruption == "coefficients":
        report = replace(report, objective_coefficients=(("PREFERENCE_PENALTY", 1),))
    elif corruption == "criteria":
        report = replace(report, confirmed_criteria_count=99)
    elif corruption == "score_count":
        report = replace(report, score=replace(report.score, violation_count=0))
    elif corruption == "unselected":
        report = replace(report, modeled_evaluations=report.modeled_evaluations +
            (replace(report.modeled_evaluations[0], delivery_id="UNSELECTED"),))
    elif corruption == "missing":
        report = replace(report, modeled_evaluations=())
    elif corruption == "wrong_staff":
        report = replace(report, modeled_evaluations=(replace(report.modeled_evaluations[0], staff_id="OTHER"),))
    elif corruption == "bool_penalty":
        report = replace(report, modeled_evaluations=(replace(report.modeled_evaluations[0], penalty=True),))
    elif corruption == "mode":
        result = replace(result, preference_optimization_enabled=False)
    else:
        report = replace(report, objective_status="FEASIBLE")
    with pytest.raises(TimetableValidationError):
        validate_preference_result(prepared, config, replace(result, preference_report=report), **context)


def test_existing_result_validator_requires_original_preference_context():
    combinations = prepare_group_combinations(combination_inputs())
    _, config, result, _ = run(combinations.prepared, windows(120), combination_input=combinations)
    with pytest.raises(TimetableValidationError, match="preference_mode_changed"):
        validate_delivery_result(combinations.prepared, config, result, combination_input=combinations)


@pytest.mark.parametrize("limit", ["max_preference_records", "max_preference_terms", "max_model_variables", "max_model_constraints"])
def test_limits_fail_instead_of_dropping_criteria(limit):
    config = windows()
    updates = {limit: 1}
    with pytest.raises(SolverInputError, match="limit"):
        run(config=config, records=[preference(), preference(identity="SECOND", direction="AVOID")], optimization=options(config, **updates))


def test_objective_integer_bound_is_checked_before_model_overflow():
    with pytest.raises(SolverInputError, match="integer bound"):
        run(scoring=policy(weight_mode="SYNTHETIC_DEFAULTS", synthetic_default_weight=2**60))


@pytest.mark.parametrize("native", [cp_model.UNKNOWN, cp_model.MODEL_INVALID])
def test_unsuccessful_status_returns_no_partial_score_or_timetable(monkeypatch, native):
    monkeypatch.setattr(cp_model.CpSolver, "solve", lambda *args, **kwargs: native)
    prepared, config, result, context = run()
    assert result.status.value == ("UNKNOWN" if native == cp_model.UNKNOWN else "MODEL_INVALID")
    with pytest.raises(TimetableValidationError, match="partial_preference_failure"):
        validate_preference_result(prepared, config, replace(result, schedule=("PARTIAL",)), **context)


def test_native_tiny_timeout_is_unknown_without_a_score():
    _, _, result, _ = run(config=windows(limit=1e-12))
    assert result.status is SolverStatus.UNKNOWN


def test_nonoptimal_feasible_status_never_claims_preference_optimality(monkeypatch):
    native = cp_model.CpSolver.solve
    def feasible(self, model, *args, **kwargs):
        assert native(self, model, *args, **kwargs) == cp_model.OPTIMAL
        return cp_model.FEASIBLE
    monkeypatch.setattr(cp_model.CpSolver, "solve", feasible)
    _, _, result, _ = run()
    assert result.status is SolverStatus.FEASIBLE and result.preference_report.objective_status == "FEASIBLE"


def test_model_predicate_corruption_is_detected_by_independent_source_minute_scoring(monkeypatch):
    import schedence_teaching.solver.preference_constraints as constraints
    monkeypatch.setattr(constraints, "_domains", lambda *args: ([[0, 3]], [[0, 3]]))
    with pytest.raises(TimetableValidationError, match="modeled_preference_penalties_changed"):
        run(records=[preference(direction="AVOID")])


def test_executable_synthetic_demo(capsys):
    runpy.run_path(str(Path(__file__).resolve().parents[1] / "examples" / "preference-optimization-demo.py"), run_name="__main__")
    output = capsys.readouterr().out
    assert "synthetic" in output and "preference_penalty" in output and "OPTIMAL" in output


def test_independent_validator_rejects_hard_clash_before_preference_report():
    prepared = demand({"A": [("STAFF", "LIC")], "B": [("STAFF", "LIC")]})
    prepared, config, result, context = run(prepared)
    first, second = result.schedule
    damaged = replace(result, schedule=(first, replace(second, start_minute=first.start_minute, end_minute=first.end_minute)))
    with pytest.raises(TimetableValidationError, match="staff_clash"):
        validate_preference_result(prepared, config, damaged, **context)


def test_day_part_boundary_crossing_is_a_soft_violation_not_a_hard_restriction():
    config = windows(45)
    config.days[0].windows["WEEKDAY"] = [TimeWindow(start_minute=15, end_minute=45)]
    scoring = policy(weight_mode="SYNTHETIC_DEFAULTS", synthetic_default_weight=2,
        time_of_day_windows={"MORNING": TimeWindow(start_minute=0, end_minute=30),
                            "AFTERNOON": TimeWindow(start_minute=30, end_minute=90)}, time_window_policy_reference="synthetic:bands")
    _, _, result, _ = run(config=config, records=[preference(category="TIME_OF_DAY", time_of_day="MORNING")], scoring=scoring)
    assert result.status is SolverStatus.OPTIMAL and result.preference_report.score.total_weighted_penalty == 2


def test_scope_and_cross_stream_constraints_do_not_assign_penalties_to_wrong_stream():
    prepared = demand({"WD": [("STAFF", "LIC")], "WE": [("STAFF", "LIC")]},
        streams={"WD": "WEEKDAY", "WE": "WEEKEND"})
    prepared, config, result, _ = run(prepared, windows(60, both_streams=True),
        records=[preference(direction="AVOID", stream="WEEKEND")])
    assert result.preference_report.score.violation_count == 1
    assert len(result.preference_report.modeled_evaluations) == 1
    row = next(row for row in result.schedule if row.session.stream is Stream.WEEKEND)
    assert result.preference_report.modeled_evaluations[0].delivery_id == row.session.occurrence_id


def test_real_weight_reference_without_confirmation_is_not_authoritative():
    config = windows()
    with pytest.raises(SolverInputError, match="fully confirmed"):
        run(config=config, records=[preference(penalty_weight=2, weight_reference="supplied:unconfirmed")],
            dates=calendar(config, synthetic_data=False), scoring=policy(weight_mode="CONFIRMED_WEIGHTS"))


def test_inactive_unresolved_records_are_retained_without_becoming_penalties():
    rows = [preference(), preference(identity="INACTIVE", category="TIME_OF_DAY", time_of_day="AFTERNOON",
        scope={"week_reference": "OTHER"})]
    _, _, result, _ = run(records=rows)
    assert result.preference_report.score.inactive_preference_count == 1
    assert result.preference_report.score.unresolved_count == 0
    assert result.preference_report.configured_criteria_count == 1


def test_source_staging_expressions_cannot_be_used_as_solver_contracts():
    from schedence_teaching.preferences import PreferenceExpression
    prepared, config = demand({"OFFER": [("STAFF", "LIC")]}), windows()
    raw = preference().model_dump(round_trip=True)
    del raw["scope"]
    candidate = PreferenceExpression.model_validate(raw)
    with pytest.raises(SolverInputError, match="prepared preferences"):
        solve_timetable(prepared, config, preference_input=[candidate], preference_calendar=calendar(config),
            preference_policy=policy(weight_mode="SYNTHETIC_DEFAULTS", synthetic_default_weight=1), preference_config=options(config))


@pytest.mark.parametrize("case", ["preferred_window", "avoided_window", "short_preferred", "all_avoided",
    "preferred_day", "avoided_day", "weekend_day", "time_of_day", "alternatives", "conflicting_criteria"])
def test_cp_sat_minimum_matches_exhaustive_independent_minute_scoring(case):
    prepared = demand({"OFFER": [("STAFF", "LIC")]})
    config = windows(90, days=("SATURDAY", "SUNDAY"))
    dates = calendar(config, first=MONDAY + timedelta(days=5))
    scoring = policy(weight_mode="SYNTHETIC_DEFAULTS", synthetic_default_weight=7,
        time_of_day_windows={"MORNING": TimeWindow(start_minute=0, end_minute=37),
                            "AFTERNOON": TimeWindow(start_minute=37, end_minute=90)},
        time_window_policy_reference="synthetic:non-grid-day-parts")
    if case in {"preferred_window", "avoided_window", "short_preferred", "all_avoided"}:
        left, right = {"preferred_window": (17, 70), "avoided_window": (17, 70),
                       "short_preferred": (17, 40), "all_avoided": (0, 90)}[case]
        rows = [preference(category="TEACHING_WINDOW", direction="AVOID" if case in {"avoided_window", "all_avoided"} else "PREFER",
            days=[], time_window=TimeWindow(start_minute=left, end_minute=right))]
    elif case in {"preferred_day", "avoided_day", "weekend_day"}:
        rows = [preference(category="WEEKEND_DAY" if case == "weekend_day" else "WORKING_DAY", days=["SATURDAY"],
                           direction="AVOID" if case == "avoided_day" else "PREFER")]
    elif case in {"time_of_day", "alternatives"}:
        rows = [preference(identity=label, category="TIME_OF_DAY", days=[], time_of_day=label,
            alternative_group_id="PARTS" if case == "alternatives" else None)
            for label in (("MORNING", "AFTERNOON") if case == "alternatives" else ("MORNING",))]
    else:
        rows = [preference(identity="YES", days=["SATURDAY"]), preference(identity="NO", days=["SATURDAY"], direction="AVOID")]
    _, _, result, context = run(prepared, config, records=rows, dates=dates, scoring=scoring)
    baseline = solve_timetable(prepared, config)
    penalties = []
    for day in ("SATURDAY", "SUNDAY"):
        for start in range(0, 61, 15):
            scheduled = (replace(baseline.schedule[0], day=day, start_minute=start, end_minute=start + 30),)
            candidate = replace(baseline, schedule=scheduled, validation=validate_timetable(prepared, config, scheduled))
            score = score_lecturer_preferences(prepared, config, candidate, context["preference_input"], dates, scoring)
            penalties.append(score.total_weighted_penalty)
    assert result.preference_report.score.total_weighted_penalty == min(penalties)


@pytest.mark.parametrize("priority", list(PreferencePriority))
def test_objective_coefficient_proof_dominates_all_lower_tier_variation(priority):
    config = windows()
    _, _, result, _ = run(config=config, optimization=options(config, priority=priority))
    report = result.preference_report
    bounds, coefficients = dict(report.objective_bounds), dict(report.objective_coefficients)
    for index, criterion in enumerate(report.priority_order):
        lower = report.priority_order[index + 1:]
        assert coefficients[criterion] == 1 + sum(bounds[name] * coefficients[name] for name in lower)


def test_explicit_zero_weight_is_preserved_without_false_missing_weight():
    _, _, result, _ = run(records=[preference(direction="AVOID", confirmation_status="CONFIRMED",
        penalty_weight=0, weight_reference="synthetic:explicit-zero")], scoring=policy(weight_mode="CONFIRMED_WEIGHTS"))
    assert result.preference_report.score.violation_count == 1
    assert result.preference_report.score.total_weighted_penalty == 0


def test_fresh_import_orders_support_preferences_and_solver():
    import subprocess
    import sys
    for order in ("import schedence_teaching.preferences; import schedence_teaching.solver",
                  "import schedence_teaching.solver; import schedence_teaching.preferences"):
        result = subprocess.run([sys.executable, "-B", "-c", order], capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("corruption", ["overflow", "unsupported_priority"])
def test_failure_validator_rechecks_original_objective_policy(monkeypatch, corruption):
    monkeypatch.setattr(cp_model.CpSolver, "solve", lambda *args, **kwargs: cp_model.UNKNOWN)
    combinations = prepare_group_combinations(combination_inputs())
    hard = {"combination_input": combinations,
            "combination_config": CombinationSolverConfig(objective="CONTACT_MINUTES")}
    prepared, config, result, context = run(combinations.prepared, windows(120), **hard)
    if corruption == "overflow":
        context["preference_policy"].synthetic_default_weight = 2**60
    else:
        context["preference_config"].priority = PreferencePriority.DELIVERIES_THEN_PREFERENCES_THEN_MINUTES
    with pytest.raises(SolverInputError):
        validate_preference_result(prepared, config, result, **context, **hard)
