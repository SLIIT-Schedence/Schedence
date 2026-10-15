"""Synthetic native chronological successor optimization and independent validation."""

from dataclasses import replace

import pytest

from schedence_teaching.consecutive import assess_consecutive_sessions
from schedence_teaching.solver import (
    ConsecutiveOptimizationConfig, SolverInputError, SolverStatus, TimetableValidationError,
    solve_timetable, validate_consecutive_result,
)

from test_staff_solver import demand, windows
from test_preferences import calendar
from test_consecutive import policy, scoring, hard


def options(**updates):
    return ConsecutiveOptimizationConfig.model_validate({"priority": "EXISTING_OBJECTIVES_THEN_CONSECUTIVE",
        "priority_policy_reference": "synthetic:consecutive-last", **updates})


def data(*, count=2, minutes=90, days=("DAY",), prepared=None):
    prepared = prepared or demand({"OFFER": [("STAFF", "LIC")]}, [("OFFER", "GROUP", count)])
    return prepared, windows(minutes, days=days)


def context(prepared, config, **updates):
    return {"consecutive_policy": scoring(), "consecutive_calendar": calendar(config),
        "consecutive_config": options(), **updates}


def run(source=None, **updates):
    prepared, config = source or data()
    original = context(prepared, config, **updates)
    before = repr((prepared, config, original))
    result = solve_timetable(prepared, config, **original)
    assert before == repr((prepared, config, original))
    summary = validate_consecutive_result(prepared, config, result, **original)
    if result.status in {SolverStatus.OPTIMAL, SolverStatus.FEASIBLE}:
        assert result.validation == summary and result.consecutive_report is not None
    else:
        assert summary is None and result.schedule == result.administrative_schedule == ()
        assert result.consecutive_report is result.fairness_report is result.preference_report is result.workload_report is None
    return prepared, config, result, original


def test_two_lectures_gain_a_preferred_break_without_more_deliveries():
    prepared, config = data()
    baseline = solve_timetable(prepared, config)
    before = assess_consecutive_sessions(prepared, config, baseline, calendar(config), scoring())
    _, _, result, _ = run((prepared, config))
    report = result.consecutive_report
    assert before.total_soft_penalty == 35 and report.total_weighted_penalty == 0
    assert report.assessment.lecturers[0].back_to_back_pairs == 0
    assert report.assessment.lecturers[0].maximum_continuous_minutes == 30
    assert len(result.schedule) == len(baseline.schedule) == 2
    assert report.priority_order == ("DELIVERIES", "CONTACT_MINUTES", "CONSECUTIVE_PENALTY")
    assert report.all_stages_proven_optimal and result.status is SolverStatus.OPTIMAL


@pytest.mark.parametrize("count,pairs,minutes,penalty", [(2, 1, 60, 35), (3, 2, 90, 160), (4, 3, 120, 285)])
def test_impossible_soft_breaks_remain_feasible_and_excess_is_charged_once(count, pairs, minutes, penalty):
    _, _, result, _ = run(data(count=count, minutes=minutes))
    report = result.consecutive_report
    row = report.assessment.lecturers[0]
    assert row.back_to_back_pairs == pairs and row.maximum_consecutive_sessions == count
    assert row.maximum_continuous_minutes == minutes and report.total_weighted_penalty == penalty
    assert len(report.modeled_entities[0].successors) == pairs
    assert len(report.modeled_entities[0].terminal_delivery_ids) == 1


@pytest.mark.parametrize("minutes,status", [(75, SolverStatus.OPTIMAL), (60, SolverStatus.INFEASIBLE)])
def test_confirmed_hard_break_requires_time_and_reports_infeasibility(minutes, status):
    _, _, result, _ = run(data(minutes=minutes), consecutive_policy=scoring(confirmed_hard_minimum_break=hard()),
        consecutive_config=options(enforce_confirmed_hard_break=True))
    assert result.status is status
    if status is SolverStatus.OPTIMAL:
        assert result.consecutive_report.assessment.confirmed_hard_break_compliant
        assert result.consecutive_report.assessment.confirmed_hard_break_enforced_by_solver
    else:
        assert "minimum-break" in result.failure_reason


def test_confirmed_hard_policy_is_not_enforced_without_explicit_flag():
    _, _, result, _ = run(data(minutes=60), consecutive_policy=scoring(confirmed_hard_minimum_break=hard()))
    assert result.status is SolverStatus.OPTIMAL and result.consecutive_report.total_weighted_penalty == 35
    assert result.consecutive_report.assessment.confirmed_hard_break_compliant is False
    assert result.consecutive_report.assessment.confirmed_hard_break_enforced_by_solver is False


def test_disabled_mode_preserves_existing_output_and_assignments():
    prepared, config = data()
    baseline = solve_timetable(prepared, config)
    result = solve_timetable(prepared, config, consecutive_policy=None, consecutive_calendar=None, consecutive_config=None)
    assert result.schedule == baseline.schedule and result.validation == baseline.validation
    assert result.consecutive_report is None and not result.consecutive_optimization_enabled
    assert "consecutive_optimization" not in result.to_dict()


@pytest.mark.parametrize("missing", ["policy", "calendar", "config"])
def test_mode_requires_explicit_policy_calendar_and_config(missing):
    prepared, config = data()
    original = context(prepared, config)
    original[f"consecutive_{missing}"] = None
    with pytest.raises(ValueError):
        solve_timetable(prepared, config, **original)


def test_optional_idle_cost_chooses_minimum_preferred_gap_instead_of_excessive_idle():
    _, _, result, _ = run(data(minutes=300), consecutive_policy=scoring(
        idle_gap_minute_penalty=1, idle_gap_policy_reference="synthetic:compactness"))
    assert result.consecutive_report.total_weighted_penalty == 15
    assert result.consecutive_report.assessment.lecturers[0].gap_distribution == ((15, 1),)
    profile = next(row for row in result.consecutive_report.teaching_distributions if row.kind == "LECTURER")
    assert profile.total_daily_idle_minutes == 15 and profile.total_daily_span_minutes == 75


def test_student_group_scoring_works_independently_of_distinct_lecturers():
    prepared = demand({"A": [("FIRST", "LIC")], "B": [("SECOND", "LIC")]}, [("A", "G", 1), ("B", "G", 1)])
    _, _, result, _ = run(data(prepared=prepared), consecutive_policy=scoring(score_lecturers=False, score_student_groups=True))
    assert result.consecutive_report.total_weighted_penalty == 0
    assert result.consecutive_report.assessment.student_groups[0].gap_distribution[0][0] >= 15
    assert all(row.kind == "STUDENT_GROUP" for row in result.consecutive_report.modeled_entities)


def test_independent_group_and_lecturer_scope_weights_are_applied_exactly():
    _, _, result, _ = run(data(minutes=60), consecutive_policy=scoring(score_student_groups=True),
        consecutive_config=options(lecturer_penalty_weight=2, student_group_penalty_weight=3))
    assert result.consecutive_report.total_weighted_penalty == 175
    assert {row.kind: row.weighted_penalty for row in result.consecutive_report.modeled_entities} == {"LECTURER": 70, "STUDENT_GROUP": 105}


def test_alternative_staff_allocation_can_remove_lecturer_back_to_back_cost():
    prepared = demand({"OFFER": [("FIRST", "LIC"), ("SECOND", "TEAM_MEMBER")]}, [("OFFER", "G", 2)])
    _, _, result, _ = run(data(prepared=prepared, minutes=60))
    assert {row.assigned_staff_id for row in result.schedule} == {"FIRST", "SECOND"}
    assert result.consecutive_report.total_weighted_penalty == 0
    assert result.consecutive_report.assessment.student_groups[0].back_to_back_pairs == 1


@pytest.mark.parametrize("layout,minutes,deliveries", [("combined", 60, 2), ("mixed", 90, 3), ("separate", 120, 4)])
def test_combined_mixed_separate_and_unselected_delivery_alternatives(layout, minutes, deliveries):
    from test_combination_solver import inputs, venues
    from schedence_teaching.preparation import prepare_group_combinations
    combinations = prepare_group_combinations(inputs())
    rooms = venues() if layout == "combined" else venues(large_minutes=30 if layout == "mixed" else 15)
    _, _, result, _ = run((combinations.prepared, windows(180)), combination_input=combinations, venue_input=rooms,
        consecutive_policy=scoring(score_student_groups=True))
    assert len(result.schedule) == deliveries and result.consecutive_report.assessment.scheduled_teaching_minutes == minutes
    assert all(row.teaching.assigned_delivery_count == 2 for row in result.consecutive_report.assessment.student_groups)
    staff = next(row for row in result.consecutive_report.modeled_entities if row.kind == "LECTURER")
    assert len(staff.selected_delivery_ids) == deliveries and len(staff.successors) == deliveries-1
    assert result.validation.assigned_occurrences == 4 and result.objective_status == "EVALUATED_COMPONENT"


def test_ancestor_teaching_propagates_without_merging_sibling_lab_sessions():
    prepared = demand({"MAIN": [("A", "LIC")], "LEFT": [("B", "LIC")], "RIGHT": [("C", "LIC")]},
        [("MAIN", "PARENT", 1), ("LEFT", "CHILD", 1), ("RIGHT", "SIBLING", 1)],
        parents={"CHILD": "PARENT", "SIBLING": "PARENT"})
    _, _, result, _ = run(data(prepared=prepared), consecutive_policy=scoring(score_lecturers=False, score_student_groups=True))
    rows = {row.entity_id: row for row in result.consecutive_report.modeled_entities}
    assert len(rows["PARENT"].selected_delivery_ids) == 1
    assert len(rows["CHILD"].selected_delivery_ids) == len(rows["SIBLING"].selected_delivery_ids) == 2
    assert result.consecutive_report.total_weighted_penalty == 0
    assert result.validation.valid_for_implemented_constraints


def test_different_durations_and_single_long_required_delivery():
    from schedence_teaching.preparation import prepare_timetable_input
    from schedence_teaching.schemas import TeachingTimetableInput
    prepared, config = data(count=2, minutes=180)
    payload = prepared.input_snapshot.model_dump(round_trip=True)
    payload["session_requirements"][0].update(duration_minutes=75, sessions_per_week=1)
    prepared = prepare_timetable_input(TeachingTimetableInput.model_validate(payload))
    _, _, result, _ = run((prepared, config))
    assert result.consecutive_report.total_weighted_penalty == 45
    assert result.consecutive_report.assessment.lecturers[0].maximum_continuous_minutes == 75
    assert len(result.schedule) == 1 and result.schedule[0].session.duration_minutes == 75


def test_selected_sessions_on_distinct_dates_do_not_form_a_consecutive_pair():
    _, _, result, _ = run(data(minutes=30, days=("MONDAY", "TUESDAY")))
    assert result.consecutive_report.total_weighted_penalty == 0
    row = result.consecutive_report.modeled_entities[0]
    assert len(row.successors) == 1 and not row.successors[0].comparable
    profile = next(row for row in result.consecutive_report.teaching_distributions if row.kind == "LECTURER")
    assert profile.teaching_day_count == 2 and profile.total_daily_idle_minutes == 0


def test_actual_dates_are_used_even_when_solver_day_order_is_not_chronological():
    from datetime import timedelta
    from test_preferences import MONDAY
    prepared, config = data(minutes=30, days=("LATE", "EARLY"))
    dates = calendar(config)
    dates.day_dates = {"LATE": MONDAY+timedelta(days=1), "EARLY": MONDAY}
    _, _, result, _ = run((prepared, config), consecutive_calendar=dates)
    links = result.consecutive_report.modeled_entities[0].successors
    records = {row.session.occurrence_id: row for row in result.schedule}
    assert records[links[0].previous_delivery_id].day == "EARLY" and records[links[0].next_delivery_id].day == "LATE"


def test_stream_transitions_are_neither_penalized_nor_skipped_to_fabricate_a_break():
    prepared = demand({"A": [("STAFF", "LIC")], "B": [("STAFF", "LIC")], "C": [("STAFF", "LIC")]},
        streams={"A": "WEEKDAY", "B": "WEEKEND", "C": "WEEKDAY"})
    _, _, result, _ = run((prepared, windows(90, both_streams=True)))
    row = result.consecutive_report.modeled_entities[0]
    assert result.consecutive_report.total_weighted_penalty == 0 and len(row.successors) == 2
    assert all(not link.comparable and link.gap_minutes is None for link in row.successors)
    assert result.consecutive_report.assessment.lecturers[0].stream_transitions_not_compared == 2


def test_date_applicability_scores_only_explicit_applicable_dates():
    from datetime import timedelta
    from test_preferences import MONDAY
    _, _, result, _ = run(data(count=4, minutes=60, days=("MONDAY", "TUESDAY")),
        consecutive_policy=scoring(effective_from=MONDAY+timedelta(days=1), effective_until=MONDAY+timedelta(days=1)))
    assert result.consecutive_report.total_weighted_penalty == 35
    sequences = result.consecutive_report.assessment.lecturers[0].sequences
    assert [row.policy_applicable for row in sequences] == [False, True]
    assert sum(row.back_to_back_pairs for row in sequences) == 2


def test_confirmed_student_hard_break_scope_is_independent_of_staff_scoring():
    prepared = demand({"A": [("FIRST", "LIC")], "B": [("SECOND", "LIC")]}, [("A", "G", 1), ("B", "G", 1)])
    _, _, result, _ = run(data(prepared=prepared, minutes=60), consecutive_policy=policy(
        confirmed_hard_minimum_break=hard(applies_to=["STUDENT_GROUP"])),
        consecutive_config=options(enforce_confirmed_hard_break=True))
    assert result.status is SolverStatus.INFEASIBLE


def test_fairness_priority_and_preference_priority_are_preserved():
    from test_work_period_solver import scenario
    from test_fairness_solver import context as fairness_context
    from test_preference_solver import inputs
    from test_preferences import preference, policy as preference_policy
    from schedence_teaching.workload import WorkloadBand
    prepared = demand({"OFFER": [("D", "LIC"), ("P", "TEAM_MEMBER")]}, [("OFFER", "G", 3)])
    source = scenario(prepared=prepared, minutes=90)
    source[2].workload_input.profiles[0].workload_band = WorkloadBand.DR
    original = fairness_context(source)
    pref = inputs(prepared, source[1], [preference(staff_id="D", category="TEACHING_WINDOW", direction="AVOID",
        days=[], time_window={"start_minute": 0, "end_minute": 30}, confirmation_status="CONFIRMED",
        penalty_weight=100000, weight_reference="synthetic:confirmed-test")], scoring=preference_policy(weight_mode="CONFIRMED_WEIGHTS"))
    _, _, result, _ = run((prepared, source[1]), **original, **pref)
    report = result.consecutive_report
    assert report.priority_order == ("DELIVERIES", "CONTACT_MINUTES", "PREFERENCE_PENALTY",
        "MAXIMUM_TARGET_DEVIATION", "AGGREGATE_TARGET_DEVIATION", "CONSECUTIVE_PENALTY")
    assert result.preference_report.score.total_weighted_penalty == 0
    assert {row.staff_id: row.teaching_minutes for row in result.fairness_report.modeled_workloads} == {"D": 60, "P": 30}
    assert report.total_weighted_penalty == 35  # Preferred first slot prevents D-P-D alternation.
    assert result.fairness_report.objective_status == result.preference_report.objective_status == "EVALUATED_COMPONENT"
    _, _, without_preferences, _ = run((prepared, source[1]), **original)
    assert without_preferences.consecutive_report.total_weighted_penalty == 0
    assert result.fairness_report.maximum_normalized_target_deviation == without_preferences.fairness_report.maximum_normalized_target_deviation
    assert result.fairness_report.aggregate_normalized_target_deviation == without_preferences.fairness_report.aggregate_normalized_target_deviation


@pytest.mark.parametrize("kind", ["admin", "research", "availability", "daily_cap", "weekly_cap"])
def test_existing_hard_protections_and_staff_limits_remain_active(kind):
    from test_work_period_solver import scenario
    from test_work_periods import admin, research, research_policy, FIRST
    from schedence_teaching.workload import prepare_work_periods
    from schedence_teaching.solver import StaffBlockedInterval
    source = scenario(count=2, minutes=90)
    original = {"staff_input": source[2]}
    if kind in {"daily_cap", "weekly_cap"}:
        setattr(source[2].availability[0], f"{kind.split('_')[0]}_teaching_max_minutes", 30)
    elif kind == "availability":
        source[2].availability[0].blocked_periods = [StaffBlockedInterval(day="DAY", start_minute=30, end_minute=60)]
    else:
        if kind == "admin":
            source[3].administrative_periods = [admin(source[2].workload_input,
                window={"start_minute": 30, "end_minute": 60}, planned_duration_minutes=30)]
        else:
            source[3].research_policies = [research_policy()]
            source[3].research_reservations = [research(on_date=FIRST, full_day=False,
                windows=[{"start_minute": 30, "end_minute": 60}])]
        original.update(work_periods=prepare_work_periods(source[0].input_snapshot, source[2].workload_input, source[3]),
            work_period_config=source[4])
    _, _, result, _ = run((source[0], source[1]), **original)
    if kind in {"daily_cap", "weekly_cap"}:
        assert result.status is SolverStatus.INFEASIBLE
    else:
        assert result.consecutive_report.total_weighted_penalty == 0
        assert result.consecutive_report.assessment.lecturers[0].gap_distribution == ((30, 1),)
        assert result.workload_report.staff[0].scheduled_teaching_minutes == 60
        assert result.workload_report.staff[0].administrative_minutes == 0


@pytest.mark.parametrize("cause", ["missing_weights", "missing_break", "missing_limit", "no_criteria", "week", "dates", "hard_unconfirmed", "approval"])
def test_missing_required_consecutive_configuration_is_rejected(cause):
    prepared, config = data()
    original = context(prepared, config)
    if cause == "missing_weights":
        original["consecutive_policy"] = policy(soft_criteria=["BACK_TO_BACK"])
    elif cause == "missing_break":
        original["consecutive_policy"] = policy(soft_criteria=["SHORT_BREAK"], short_break_minute_penalty=1)
    elif cause == "missing_limit":
        original["consecutive_policy"] = policy(soft_criteria=["EXCESS_CONTINUOUS_MINUTES"], excess_continuous_minute_penalty=1)
    elif cause == "no_criteria":
        original["consecutive_policy"] = policy()
    elif cause == "week":
        original["consecutive_policy"].week_reference = "OTHER"
    elif cause == "dates":
        from datetime import timedelta
        from test_preferences import MONDAY
        original["consecutive_policy"] = scoring(effective_from=MONDAY+timedelta(days=7))
    elif cause == "hard_unconfirmed":
        original["consecutive_config"] = options(enforce_confirmed_hard_break=True)
    else:
        original["consecutive_calendar"].synthetic_data = False
    with pytest.raises(SolverInputError):
        solve_timetable(prepared, config, **original)


@pytest.mark.parametrize("limit", ["max_participants", "max_nodes_per_participant", "max_successor_arcs", "max_model_variables", "max_model_constraints", "max_integer_bound"])
def test_model_size_and_integer_guards_reject_without_silently_dropping_events(limit):
    with pytest.raises(SolverInputError, match="limit|bound"):
        run(consecutive_policy=scoring(score_student_groups=True), consecutive_config=options(**{limit: 1}))


def test_real_mode_requires_explicit_approval_and_preserves_source_policy():
    prepared, config = data()
    dates = calendar(config, synthetic_data=False)
    _, _, result, _ = run((prepared, config), consecutive_calendar=dates,
        consecutive_config=options(policy_approval_reference="synthetic:test-approval-shape-only"))
    assert result.status is SolverStatus.OPTIMAL
    assert result.consecutive_report.to_dict()["total_confirmed_consecutive_penalty"] == 0


@pytest.mark.parametrize("cause", ["successor", "gap", "run", "terminal", "maximum", "penalty", "attendance", "total", "policy", "history", "span", "mode"])
def test_independent_validation_rejects_corrupted_consecutive_results(cause):
    prepared, config, result, original = run(data(minutes=60))
    report = result.consecutive_report
    entity = report.modeled_entities[0]
    if cause in {"successor", "gap"}:
        link = entity.successors[0]
        changes = {"previous_delivery_id": "UNKNOWN"} if cause == "successor" else {"gap_minutes": 99}
        entity = replace(entity, successors=(replace(link, **changes),))
    elif cause in {"run", "terminal", "maximum", "penalty", "attendance"}:
        changes = {"run": {"run_prefix_minutes": ()}, "terminal": {"terminal_delivery_ids": ()},
            "maximum": {"maximum_continuous_minutes": 0}, "penalty": {"raw_penalty": 0},
            "attendance": {"selected_delivery_ids": ("UNKNOWN",)}}[cause]
        entity = replace(entity, **changes)
    else:
        changes = {"total": {"total_weighted_penalty": 0}, "policy": {"optimization_config_json": "{}"},
            "history": {"stages": tuple(reversed(report.stages))}, "span": {"teaching_distributions": ()},
            "mode": {"objective_status": "FEASIBLE"}}[cause]
        report = replace(report, **changes)
    if cause in {"successor", "gap", "run", "terminal", "maximum", "penalty", "attendance"}:
        report = replace(report, modeled_entities=(entity,))
    with pytest.raises(TimetableValidationError):
        validate_consecutive_result(prepared, config, replace(result, consecutive_report=report), **original)


@pytest.mark.parametrize("cause", ["assignment", "coverage", "duration", "summary", "disabled_flag", "bool_flag"])
def test_original_hard_validation_and_mode_metadata_cannot_be_bypassed(cause):
    prepared, config, result, original = run()
    if cause == "assignment":
        result = replace(result, schedule=(replace(result.schedule[0], assigned_staff_id="UNKNOWN"), result.schedule[1]))
    elif cause == "coverage":
        result = replace(result, schedule=result.schedule[:-1])
    elif cause == "duration":
        result = replace(result, schedule=(replace(result.schedule[0], end_minute=result.schedule[0].end_minute+15), result.schedule[1]))
    elif cause == "summary":
        result = replace(result, validation=replace(result.validation, assigned_occurrences=999))
    else:
        result = replace(result, consecutive_optimization_enabled=False if cause == "disabled_flag" else 1)
    with pytest.raises(ValueError):
        validate_consecutive_result(prepared, config, result, **original)


def test_validator_detects_a_broken_native_successor_constraint(monkeypatch):
    from ortools.sat.python import cp_model
    monkeypatch.setattr(cp_model.CpModel, "add_circuit", lambda self, arcs: self.add(0 == 0))
    with pytest.raises(TimetableValidationError, match="modeled_consecutive"):
        run(data(minutes=60))


def test_confirmed_hard_break_is_independently_rechecked():
    prepared, config, result, original = run(data(minutes=60), consecutive_policy=scoring(confirmed_hard_minimum_break=hard()))
    original["consecutive_config"] = options(enforce_confirmed_hard_break=True)
    with pytest.raises(TimetableValidationError, match="confirmed_hard_break_violation"):
        validate_consecutive_result(prepared, config, result, **original)


def test_postsolve_reports_retain_full_validation_context_and_true_mode_flags():
    from schedence_teaching.solver import validate_staff_policy_result
    from schedence_teaching.workload import assess_weekly_workload
    from test_work_period_solver import scenario
    source = scenario(count=2, minutes=90)
    prepared, config, result, original = run((source[0], source[1]), staff_input=source[2])
    assert validate_staff_policy_result(prepared, config, result, **original) == result.validation
    assert assess_weekly_workload(prepared, config, result, source[2].workload_input, **original) == result.workload_report
    assessed = assess_consecutive_sessions(prepared, config, result, original["consecutive_calendar"], original["consecutive_policy"], **original)
    assert assessed.cp_sat_consecutive_objective_enabled
    with pytest.raises(ValueError):
        validate_staff_policy_result(prepared, config, result, staff_input=source[2])


def test_exhaustive_tiny_assignments_match_the_native_minimum_with_scope_weights():
    from itertools import product
    from schedence_teaching.solver import validate_timetable
    prepared = demand({"OFFER": [("FIRST", "LIC"), ("SECOND", "TEAM_MEMBER")]}, [("OFFER", "G", 3)])
    config = windows(120)
    config.granularity_minutes = 30
    settings = scoring(score_student_groups=True)
    baseline = solve_timetable(prepared, config)
    best = None
    for starts in product(range(0, 91, 30), repeat=3):
        if len(set(starts)) != 3:
            continue
        for staff in product(("FIRST", "SECOND"), repeat=3):
            schedule = tuple(replace(row, start_minute=start, end_minute=start+30, assigned_staff_id=identity)
                for row, start, identity in zip(baseline.schedule, starts, staff, strict=True))
            candidate = replace(baseline, schedule=schedule, validation=validate_timetable(prepared, config, schedule))
            evaluated = assess_consecutive_sessions(prepared, config, candidate, calendar(config), settings)
            penalty = sum(row.total_soft_penalty for row in evaluated.lecturers)*2 + sum(row.total_soft_penalty for row in evaluated.student_groups)*3
            best = penalty if best is None else min(best, penalty)
    _, _, native, _ = run((prepared, config), consecutive_policy=settings,
        consecutive_config=options(lecturer_penalty_weight=2, student_group_penalty_weight=3))
    assert native.status is SolverStatus.OPTIMAL and native.consecutive_report.total_weighted_penalty == best == 105


@pytest.mark.parametrize("at_stage", [1, 2, 3])
def test_feasible_stage_stops_lower_priority_work_without_claiming_optimality(monkeypatch, at_stage):
    from ortools.sat.python import cp_model
    native = cp_model.CpSolver.solve
    calls = []
    def feasible(self, model, *args, **kwargs):
        status = native(self, model, *args, **kwargs)
        calls.append(status)
        return cp_model.FEASIBLE if len(calls) == at_stage else status
    monkeypatch.setattr(cp_model.CpSolver, "solve", feasible)
    _, _, result, _ = run()
    assert result.status is SolverStatus.FEASIBLE and len(calls) == at_stage
    assert result.consecutive_report.stages[-1].status == "FEASIBLE"
    assert not result.consecutive_report.all_stages_proven_optimal


@pytest.mark.parametrize("native_status", ["UNKNOWN", "MODEL_INVALID"])
def test_no_incumbent_has_empty_reports_and_no_partial_optimization(monkeypatch, native_status):
    from ortools.sat.python import cp_model
    monkeypatch.setattr(cp_model.CpSolver, "solve", lambda *args, **kwargs: getattr(cp_model, native_status))
    _, _, result, _ = run()
    assert result.status.value == native_status and result.consecutive_report is None


def test_later_timeout_retains_the_previous_complete_incumbent(monkeypatch):
    from ortools.sat.python import cp_model
    native = cp_model.CpSolver.solve
    calls = []
    def timeout(self, model, *args, **kwargs):
        calls.append(1)
        return native(self, model, *args, **kwargs) if len(calls) == 1 else cp_model.UNKNOWN
    monkeypatch.setattr(cp_model.CpSolver, "solve", timeout)
    _, _, result, _ = run()
    assert result.status is SolverStatus.FEASIBLE and len(result.schedule) == 2
    assert [row.status for row in result.consecutive_report.stages] == ["OPTIMAL", "UNKNOWN"]


def test_exhausted_search_budget_does_not_start_another_stage(monkeypatch):
    import schedence_teaching.solver.lexicographic as stages
    times = iter([0.0, 0.0, 10.0])
    monkeypatch.setattr(stages, "perf_counter", lambda: next(times))
    _, _, result, _ = run()
    assert result.status is SolverStatus.FEASIBLE
    assert result.consecutive_report.stages[-1].status == "NOT_RUN_TIME_LIMIT"


@pytest.mark.parametrize("stop_stage", [1, 3, 5])
def test_fairness_prefix_status_is_preserved_when_full_consecutive_solve_is_not_optimal(monkeypatch, stop_stage):
    from ortools.sat.python import cp_model
    from test_work_period_solver import scenario
    from test_fairness_solver import context as fairness_context
    from schedence_teaching.workload import WorkloadBand
    from schedence_teaching.solver import validate_fairness_result
    prepared = demand({"OFFER": [("D", "LIC"), ("P", "TEAM_MEMBER")]}, [("OFFER", "G", 3)])
    source = scenario(prepared=prepared, minutes=120)
    source[2].workload_input.profiles[0].workload_band = WorkloadBand.DR
    original = fairness_context(source)
    native = cp_model.CpSolver.solve
    calls = []
    def feasible(self, model, *args, **kwargs):
        status = native(self, model, *args, **kwargs)
        calls.append(status)
        return cp_model.FEASIBLE if len(calls) == stop_stage else status
    monkeypatch.setattr(cp_model.CpSolver, "solve", feasible)
    prepared, config, result, full = run((prepared, source[1]), **original)
    assert result.status is SolverStatus.FEASIBLE and len(calls) == stop_stage
    assert result.fairness_report.all_stages_proven_optimal is (stop_stage == 5)
    assert result.fairness_report.objective_status == "EVALUATED_COMPONENT"
    assert validate_fairness_result(prepared, config, result, **full) == result.validation


def test_teaching_delivery_and_contact_minute_objectives_keep_priority():
    from test_preference_solver import priority_source, minute_priority_source
    from test_combination_solver import venues
    # One combined delivery beats two sessions assigned to distinct lecturers,
    # even though lecturer consecutive costs are optional in both cases.
    combinations = priority_source()
    _, _, result, _ = run((combinations.prepared, windows(90)), combination_input=combinations)
    assert len(result.schedule) == 1 and result.schedule[0].assigned_staff_id == "COMMON"
    combinations = minute_priority_source()
    _, _, result, _ = run((combinations.prepared, windows(240)), combination_input=combinations,
        venue_input=venues(minutes=240, large_minutes=60))
    assert len(result.schedule) == 3 and result.consecutive_report.assessment.scheduled_teaching_minutes == 120


def test_disabled_and_empty_attendees_have_exact_zero_modeled_cost():
    prepared = demand({"A": [("STAFF", "LIC")]}, requirements=[], extra_staff=["UNASSIGNED"])
    _, _, result, _ = run((prepared, windows(90)))
    assert result.status is SolverStatus.OPTIMAL and result.consecutive_report.total_weighted_penalty == 0
    assert all(not row.selected_delivery_ids and row.maximum_continuous_minutes == 0 and row.raw_penalty == 0
               for row in result.consecutive_report.modeled_entities)


def test_non_aligned_preferred_threshold_is_not_rewritten_to_a_campus_rule():
    settings = scoring()
    settings.preferred_minimum_break_minutes = 10
    _, _, result, _ = run(data(minutes=75), consecutive_policy=settings)
    assert result.consecutive_report.total_weighted_penalty == 0
    assert result.consecutive_report.assessment.lecturers[0].gap_distribution == ((15, 1),)


def test_hard_only_mode_requires_explicit_confirmation_and_enablement():
    _, _, result, _ = run(data(minutes=75), consecutive_policy=policy(confirmed_hard_minimum_break=hard()),
        consecutive_config=options(enforce_confirmed_hard_break=True))
    assert result.consecutive_report.total_weighted_penalty == 0
    assert result.consecutive_report.assessment.confirmed_hard_break_compliant


def test_report_serialization_and_regeneration_are_deterministic():
    import json
    prepared, config, result, original = run()
    output = result.consecutive_report.to_dict(include_details=True)
    assert json.loads(json.dumps(output)) == output
    assert output["assessment"]["cp_sat_consecutive_objective_enabled"]
    assert output["total_confirmed_consecutive_penalty"] is None
    assert validate_consecutive_result(prepared, config, result, **original) == result.validation


def test_independent_validator_never_constructs_a_cp_model(monkeypatch):
    from ortools.sat.python import cp_model
    prepared, config, result, original = run()
    def unexpected(*args, **kwargs):
        raise AssertionError("Independent validation must not build another CP model")
    monkeypatch.setattr(cp_model, "CpModel", unexpected)
    assert validate_consecutive_result(prepared, config, result, **original) == result.validation


def test_standalone_demo_proves_improvement_without_changing_higher_priorities(capsys):
    from pathlib import Path
    import runpy
    example = runpy.run_path(str(Path(__file__).resolve().parents[1] / "examples" / "consecutive-optimization-demo.py"))
    output = example["main"]()
    before, after = output["baseline"], output["optimized"]
    assert output["actual_penalty_improved"] and output["independently_validated"]
    assert output["synthetic"] and output["university_ready"] is False
    assert before["status"] == after["status"] == "OPTIMAL"
    assert (before["consecutive_penalty"], after["consecutive_penalty"]) == (320, 60)
    assert (before["lecturer_back_to_back_pairs"], after["lecturer_back_to_back_pairs"]) == (2, 0)
    assert (before["student_group_back_to_back_pairs"], after["student_group_back_to_back_pairs"]) == (2, 0)
    assert (before["maximum_continuous_minutes"], after["maximum_continuous_minutes"]) == (90, 30)
    assert before["idle_gaps"] == [0, 0] and after["idle_gaps"] == [15, 15]
    for key in ("delivery_count", "contact_minutes", "preference_penalty", "lecturer_teaching_days",
            "maximum_normalized_workload_deviation", "aggregate_normalized_workload_deviation", "lecturer_assignments"):
        assert before[key] == after[key]
    assert all(row["status"] == "OPTIMAL" for row in output["stages"])
    assert '"actual_penalty_improved": true' in capsys.readouterr().out
