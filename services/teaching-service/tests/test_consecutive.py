"""Synthetic consecutive teaching metrics and independently validated source bookings."""

from dataclasses import replace
import json
from pathlib import Path
import runpy

import pytest
from pydantic import ValidationError

from schedence_teaching.consecutive import (
    AttendanceKind, ConfirmedMinimumBreak, ConsecutiveCriterion, ConsecutiveInputError, ConsecutivePolicy,
    assess_consecutive_sessions, detect_consecutive_sequences, validate_consecutive_report,
)
from schedence_teaching.preparation import prepare_timetable_input
from schedence_teaching.schemas import TeachingTimetableInput
from schedence_teaching.solver import SolverStatus, TimetableValidationError, solve_timetable, validate_timetable

from test_staff_solver import demand, windows
from test_preferences import calendar


def policy(**updates):
    return ConsecutivePolicy.model_validate({"policy_reference": "synthetic:consecutive-assessment",
        "week_reference": "SYNTHETIC-WEEK", "provenance": {"source": "synthetic", "reference": "example-only"}, **updates})


def scoring(**updates):
    return policy(soft_criteria=list(ConsecutiveCriterion), preferred_minimum_break_minutes=15,
        maximum_preferred_continuous_minutes=60, back_to_back_penalty=5, short_break_minute_penalty=2,
        excess_continuous_minute_penalty=3, **updates)


def hard(**updates):
    return ConfirmedMinimumBreak.model_validate({"minutes": 15, "confirmation": "CONFIRMED",
        "campus_policy_reference": "synthetic:confirmed-test-policy", "approval_reference": "synthetic:test-only",
        "applies_to": ["LECTURER"], **updates})


def placed(records=None, *, prepared=None, config=None):
    # Records are (day, start, duration); source occurrence order is deterministic.
    records = records if records is not None else [("DAY", 0, 30), ("DAY", 30, 30)]
    prepared = prepared or demand({str(index): [("STAFF", "LIC")] for index in range(len(records))})
    payload = prepared.input_snapshot.model_dump(round_trip=True)
    for requirement, (_, _, duration) in zip(payload["session_requirements"], records, strict=True):
        requirement["duration_minutes"] = duration
    prepared = prepare_timetable_input(TeachingTimetableInput.model_validate(payload))
    config = config or windows(1440, days=tuple(dict.fromkeys(day for day, _, _ in records)) or ("DAY",))
    result = solve_timetable(prepared, config)
    assert result.status in {SolverStatus.OPTIMAL, SolverStatus.FEASIBLE}
    by_requirement = {row.session.requirement_id: row for row in result.schedule}
    schedule = tuple(replace(by_requirement[requirement.requirement_id], day=day, start_minute=start, end_minute=start+duration)
        for requirement, (day, start, duration) in zip(prepared.input_snapshot.session_requirements, records, strict=True))
    result = replace(result, schedule=schedule, validation=validate_timetable(prepared, config, schedule))
    return prepared, config, result


def assess(data=None, *, settings=None, context=None):
    prepared, config, result = data or placed()
    dates, settings = calendar(config), settings or scoring()
    original = context or {}
    before = repr((prepared, config, result, settings, dates, original))
    report = assess_consecutive_sessions(prepared, config, result, dates, settings, **original)
    assert report == validate_consecutive_report(report, prepared, config, result, dates, settings, **original)
    assert before == repr((prepared, config, result, settings, dates, original))
    return report


@pytest.mark.parametrize("count,pairs,minutes,penalty", [(2, 1, 60, 35), (3, 2, 90, 160), (4, 3, 120, 285)])
def test_touching_lectures_are_one_maximal_run_and_excess_is_counted_once(count, pairs, minutes, penalty):
    report = assess(placed([("DAY", index*30, 30) for index in range(count)]))
    row = report.lecturers[0]
    assert row.back_to_back_pairs == row.back_to_back_lecture_pairs == pairs
    assert row.maximum_consecutive_sessions == count and row.maximum_continuous_minutes == minutes
    assert row.teaching.longest_consecutive_teaching_minutes == minutes
    assert row.gap_distribution == ((0, pairs),)
    assert report.total_soft_penalty == penalty and report.scoring_complete
    assert row.sequences[0].uninterrupted_runs[0].session_count == count


@pytest.mark.parametrize("gap,penalty", [(15, 0), (30, 0), (5, 20), (10, 10)])
def test_positive_gaps_end_uninterrupted_runs_and_score_shortfall_separately(gap, penalty):
    config = windows(1440)
    config.granularity_minutes = 5
    report = assess(placed([("DAY", 0, 30), ("DAY", 30+gap, 30)], config=config))
    row = report.lecturers[0]
    assert row.maximum_consecutive_sessions == 1 and row.maximum_continuous_minutes == 30
    assert row.back_to_back_pairs == 0 and row.gap_distribution == ((gap, 1),)
    assert report.total_soft_penalty == penalty


def test_different_dates_never_form_a_pair_and_daily_distribution_is_preserved():
    report = assess(placed([("MONDAY", 0, 30), ("TUESDAY", 30, 30)]))
    row = report.lecturers[0]
    assert row.back_to_back_pairs == 0 and not row.gap_distribution
    assert row.maximum_consecutive_sessions == 1 and row.teaching.teaching_day_count == 2
    assert row.teaching.daily_teaching_minutes == (("MONDAY", 30), ("TUESDAY", 30))


def test_different_lecturers_are_evaluated_independently():
    prepared = demand({"A": [("FIRST", "LIC")], "B": [("SECOND", "LIC")]})
    report = assess(placed(prepared=prepared))
    assert [row.back_to_back_pairs for row in report.lecturers] == [0, 0]
    assert report.assigned_physical_delivery_count == 2 and report.scheduled_teaching_minutes == 60


def test_student_groups_are_independent_of_shared_lecturer_sequence():
    report = assess(settings=scoring(score_student_groups=True))
    assert report.lecturers[0].back_to_back_pairs == 1
    assert all(row.back_to_back_pairs == 0 for row in report.student_groups)
    assert report.total_soft_penalty == report.lecturers[0].total_soft_penalty == 35


def test_same_group_attending_different_modules_has_its_own_consecutive_sequence():
    prepared = demand({"A": [("FIRST", "LIC")], "B": [("SECOND", "LIC")]}, [("A", "G", 1), ("B", "G", 1)])
    report = assess(placed(prepared=prepared), settings=scoring(score_student_groups=True))
    assert all(row.back_to_back_pairs == 0 for row in report.lecturers)
    assert report.student_groups[0].back_to_back_pairs == 1 and report.total_soft_penalty == 35


def test_nested_group_inherits_ancestor_lectures_without_unioning_sibling_labs():
    prepared = demand({"MAIN": [("A", "LIC")], "LEFT": [("B", "LIC")], "RIGHT": [("C", "LIC")]},
        [("MAIN", "PARENT", 1), ("LEFT", "CHILD", 1), ("RIGHT", "SIBLING", 1)],
        parents={"CHILD": "PARENT", "SIBLING": "PARENT"})
    report = assess(placed([("DAY", 0, 30), ("DAY", 30, 30), ("DAY", 30, 30)], prepared=prepared))
    groups = {row.entity_id: row for row in report.student_groups}
    assert groups["PARENT"].teaching.assigned_delivery_count == 1
    assert groups["CHILD"].teaching.assigned_delivery_count == groups["SIBLING"].teaching.assigned_delivery_count == 2
    assert groups["CHILD"].back_to_back_pairs == groups["SIBLING"].back_to_back_pairs == 1
    assert groups["CHILD"].inherited_delivery_ids == groups["SIBLING"].inherited_delivery_ids == ("REQ1:1",)
    assert report.scheduled_teaching_minutes == 90  # Not the sum of overlapping group views.


def test_deep_descendant_inherits_all_its_ancestor_requirements_only():
    prepared = demand({"A": [("STAFF", "LIC")], "B": [("STAFF", "LIC")], "C": [("STAFF", "LIC")]},
        [("A", "ROOT", 1), ("B", "CHILD", 1), ("C", "LEAF", 1)], parents={"CHILD": "ROOT", "LEAF": "CHILD"})
    report = assess(placed([("DAY", 0, 30), ("DAY", 30, 30), ("DAY", 60, 30)], prepared=prepared))
    groups = {row.entity_id: row for row in report.student_groups}
    assert groups["LEAF"].maximum_consecutive_sessions == 3
    assert groups["ROOT"].maximum_consecutive_sessions == 1
    assert groups["LEAF"].inherited_delivery_ids == ("REQ1:1", "REQ2:1")


@pytest.mark.parametrize("layout,minutes,deliveries", [("combined", 60, 2), ("mixed", 90, 3), ("separate", 120, 4)])
def test_combined_and_mixed_delivery_do_not_duplicate_lecturer_or_group_attendance(layout, minutes, deliveries):
    from test_combination_solver import solve, venues
    rooms = venues() if layout == "combined" else venues(large_minutes=30 if layout == "mixed" else 15)
    combinations, config, result = solve(rooms=rooms)
    report = assess((combinations.prepared, config, result), settings=scoring(score_student_groups=True),
        context={"combination_input": combinations, "venue_input": rooms})
    assert report.assigned_physical_delivery_count == deliveries and report.scheduled_teaching_minutes == minutes
    assert report.lecturers[0].teaching.assigned_delivery_count == deliveries
    assert all(row.teaching.assigned_delivery_count == 2 for row in report.student_groups)
    for row in report.student_groups:
        assert len(row.direct_delivery_ids) == len(set(row.direct_delivery_ids)) == 2
    assert sum(row.teaching.scheduled_teaching_minutes for row in report.student_groups) == 120


def test_different_durations_and_nonlecture_teaching_are_preserved():
    data = placed([("DAY", 0, 45), ("DAY", 45, 60)])
    payload = data[0].input_snapshot.model_dump(round_trip=True)
    payload["session_requirements"][1]["session_type"] = "LAB"
    data = placed([("DAY", 0, 45), ("DAY", 45, 60)], prepared=prepare_timetable_input(TeachingTimetableInput.model_validate(payload)))
    report = assess(data)
    row = report.lecturers[0]
    assert row.maximum_continuous_minutes == 105 and row.back_to_back_pairs == 1
    assert row.back_to_back_lecture_pairs == 0 and row.teaching.scheduled_teaching_minutes == 105
    assert dict(row.penalty_components)["EXCESS_CONTINUOUS_MINUTES"] == 135


def test_stream_changes_are_not_scored_or_skipped_to_invent_a_break():
    prepared = demand({"A": [("STAFF", "LIC")], "B": [("STAFF", "LIC")], "C": [("STAFF", "LIC")]},
        streams={"A": "WEEKDAY", "B": "WEEKEND", "C": "WEEKDAY"})
    config = windows(120, both_streams=True)
    report = assess(placed([("DAY", 0, 30), ("DAY", 30, 30), ("DAY", 60, 30)], prepared=prepared, config=config))
    row = report.lecturers[0]
    assert row.back_to_back_pairs == 0 and row.stream_transitions_not_compared == 2
    assert row.maximum_consecutive_sessions == 1 and row.maximum_continuous_minutes == 30
    assert row.teaching.scheduled_teaching_minutes == 90 and not row.gap_distribution
    assert all(not sequence.gaps for sequence in row.sequences)


def test_missing_break_configuration_is_explicit_and_never_a_fabricated_zero_penalty():
    report = assess(settings=policy(soft_criteria=list(ConsecutiveCriterion)))
    assert report.total_soft_penalty is None and report.scoring_complete is False
    assert report.known_soft_penalty == 0 and "missing_preferred_minimum_break_minutes" in report.diagnostics
    assert all(value is None for _, value in report.lecturers[0].penalty_components)


def test_descriptive_only_policy_does_not_invent_thresholds_or_weights():
    report = assess(settings=policy())
    assert report.total_soft_penalty is report.scoring_complete is None
    assert report.lecturers[0].back_to_back_pairs == 1
    assert "descriptive_only_no_soft_criteria_selected" in report.diagnostics


@pytest.mark.parametrize("lecturers,groups,expected", [(True, False, 35), (False, True, 0), (True, True, 35), (False, False, None)])
def test_scoring_scopes_are_optional_and_independent(lecturers, groups, expected):
    report = assess(settings=scoring(score_lecturers=lecturers, score_student_groups=groups))
    assert report.total_soft_penalty == expected
    assert report.lecturers[0].scoring_enabled is lecturers
    assert all(row.scoring_enabled is groups for row in report.student_groups)


def test_soft_preference_shortfall_does_not_change_hard_validity():
    data = placed()
    report = assess(data)
    assert report.lecturers[0].sequences[0].preferred_break_shortfall_minutes == 15
    assert data[2].validation.valid_for_implemented_constraints
    assert report.confirmed_hard_break_compliant is None and not report.confirmed_hard_break_enforced_by_solver


@pytest.mark.parametrize("applies_to,count", [(["LECTURER"], 1), (["STUDENT_GROUP"], 0), (["LECTURER", "STUDENT_GROUP"], 1)])
def test_confirmed_hard_minimum_break_is_assessed_separately_without_solver_changes(applies_to, count):
    report = assess(settings=policy(confirmed_hard_minimum_break=hard(applies_to=applies_to)))
    assert report.confirmed_hard_break_violation_count == count
    assert report.confirmed_hard_break_compliant is (count == 0)
    assert not report.confirmed_hard_break_enforced_by_solver
    assert report.total_soft_penalty is None


def test_soft_and_confirmed_hard_break_durations_remain_distinct():
    config = windows(120)
    config.granularity_minutes = 5
    report = assess(placed([("DAY", 0, 30), ("DAY", 40, 30)], config=config),
        settings=scoring(confirmed_hard_minimum_break=hard(minutes=5)))
    assert report.total_soft_penalty == 10 and report.confirmed_hard_break_compliant


@pytest.mark.parametrize("updates", [{"confirmation": "UNCONFIRMED"}, {"approval_reference": " "},
    {"campus_policy_reference": " "}, {"minutes": 0}, {"applies_to": []}, {"confirmation": True}])
def test_unconfirmed_or_incomplete_hard_break_policy_is_rejected(updates):
    with pytest.raises(ValidationError):
        hard(**updates)


@pytest.mark.parametrize("updates", [{"preferred_minimum_break_minutes": -1}, {"preferred_minimum_break_minutes": True},
    {"maximum_preferred_continuous_minutes": 0}, {"back_to_back_penalty": 5},
    {"soft_criteria": ["BACK_TO_BACK"], "back_to_back_penalty": -1},
    {"soft_criteria": ["BACK_TO_BACK"], "back_to_back_penalty": True}, {"score_lecturers": 1},
    {"soft_criteria": ["UNKNOWN"]}, {"invented_travel_penalty": 1}, {"week_reference": " "}])
def test_invalid_policy_inputs(updates):
    with pytest.raises(ValidationError):
        policy(**updates)


@pytest.mark.parametrize("cause", ["overlap", "duplicate", "duration", "staff", "coverage", "metadata", "summary", "failed"])
def test_corrupt_timetables_are_rejected_before_sequence_scoring(cause):
    prepared, config, result = placed()
    if cause == "overlap":
        result = replace(result, schedule=(result.schedule[0], replace(result.schedule[1], start_minute=15, end_minute=45)))
    elif cause == "duplicate":
        result = replace(result, schedule=(*result.schedule, result.schedule[0]))
    elif cause == "duration":
        result = replace(result, schedule=(replace(result.schedule[0], end_minute=15), result.schedule[1]))
    elif cause == "staff":
        result = replace(result, schedule=(replace(result.schedule[0], assigned_staff_id="UNKNOWN"), result.schedule[1]))
    elif cause == "coverage":
        result = replace(result, schedule=result.schedule[:-1])
    elif cause == "metadata":
        changed = replace(result.schedule[0].session, group_id="UNKNOWN")
        result = replace(result, schedule=(replace(result.schedule[0], session=changed), result.schedule[1]))
    elif cause == "summary":
        result = replace(result, validation=replace(result.validation, assigned_occurrences=0))
    else:
        result = replace(result, status=SolverStatus.UNKNOWN)
    with pytest.raises(ValueError):
        assess_consecutive_sessions(prepared, config, result, calendar(config), scoring())


@pytest.mark.parametrize("cause", ["week", "unmapped_day", "duplicate_date", "missing_calendar", "missing_policy"])
def test_invalid_calendar_and_policy_context_is_rejected(cause):
    prepared, config, result = placed()
    dates, settings = calendar(config), scoring()
    if cause == "week":
        settings.week_reference = "OTHER"
    elif cause == "unmapped_day":
        dates.day_dates = {}
    elif cause == "duplicate_date":
        dates.day_dates["OTHER"] = next(iter(dates.day_dates.values()))
    elif cause == "missing_calendar":
        dates = None
    else:
        settings = None
    with pytest.raises(ValueError):
        assess_consecutive_sessions(prepared, config, result, dates, settings)


@pytest.mark.parametrize("cause", ["total", "pairs", "run", "policy", "calendar", "bool", "objective_flag", "hard_flag"])
def test_report_regeneration_detects_tampering(cause):
    data, settings = placed(), scoring()
    report = assess(data, settings=settings)
    if cause in {"pairs", "run"}:
        row = report.lecturers[0]
        changes = {"back_to_back_pairs": 99} if cause == "pairs" else {"maximum_continuous_minutes": 0}
        changed = replace(report, lecturers=(replace(row, **changes),))
    else:
        changes = {"total": {"total_soft_penalty": 0}, "policy": {"policy_snapshot_json": "{}"},
            "calendar": {"calendar_snapshot_json": "{}"}, "bool": {"assigned_physical_delivery_count": True},
            "objective_flag": {"cp_sat_consecutive_objective_enabled": True},
            "hard_flag": {"confirmed_hard_break_enforced_by_solver": True}}[cause]
        changed = replace(report, **changes)
    with pytest.raises(ConsecutiveInputError, match="independently recomputed"):
        validate_consecutive_report(changed, *data, calendar(data[1]), settings)


def test_report_serialization_is_deterministic_and_aggregate_by_default():
    report = assess()
    output = report.to_dict()
    assert "lecturers" not in output and "student_groups" not in output
    assert output["lecturer_summary"]["back_to_back_pairs"] == 1
    detailed = report.to_dict(include_lecturer_details=True, include_student_group_details=True)
    assert detailed["lecturers"][0]["sequences"][0]["on_date"] == "2026-10-19"
    assert detailed["lecturers"][0]["sequences"][0]["stream"] == "WEEKDAY"
    assert json.loads(json.dumps(detailed)) == detailed
    assert not output["cp_sat_consecutive_objective_enabled"] and not output["university_ready"]


def test_empty_demand_and_unassigned_staff_have_zero_metrics():
    prepared = demand({"A": [("UNASSIGNED", "LIC")]}, requirements=[])
    config = windows()
    report = assess((prepared, config, solve_timetable(prepared, config)))
    assert report.assigned_physical_delivery_count == report.scheduled_teaching_minutes == 0
    assert report.total_soft_penalty == 0 and report.lecturers[0].maximum_consecutive_sessions == 0
    assert not report.student_groups and "no_scheduled_teaching" in report.lecturers[0].diagnostics


def test_synthetic_demo_runs(capsys):
    result = runpy.run_path(str(Path(__file__).resolve().parents[1] / "examples" / "consecutive-teaching-demo.py"))
    summary = result["main"]()
    assert summary["synthetic"] and summary["total_soft_penalty"] == 160
    assert "synthetic" in capsys.readouterr().out


def test_staff_blocked_periods_are_respected_and_not_added_to_teaching_minutes():
    from test_work_period_solver import scenario
    from schedence_teaching.solver import StaffBlockedInterval
    data = scenario(count=2, minutes=90)
    data[2].availability[0].blocked_periods = [StaffBlockedInterval(day="DAY", start_minute=30, end_minute=60)]
    result = solve_timetable(data[0], data[1], staff_input=data[2])
    report = assess((data[0], data[1], result), context={"staff_input": data[2]})
    assert report.lecturers[0].gap_distribution == ((30, 1),)
    assert report.scheduled_teaching_minutes == 60 and report.total_soft_penalty == 0
    assert "staff_blocked_periods" in report.hard_validation_checks


@pytest.mark.parametrize("kind", ["admin", "research"])
def test_approved_duties_remain_protected_and_are_not_teaching_sessions(kind):
    from test_work_period_solver import scenario, run as protected_run
    from test_work_periods import admin, research, research_policy, FIRST
    data = scenario(count=2, minutes=90)
    if kind == "admin":
        data[3].administrative_periods = [admin(data[2].workload_input,
            window={"start_minute": 30, "end_minute": 60}, planned_duration_minutes=30)]
    else:
        data[3].research_policies = [research_policy()]
        data[3].research_reservations = [research(on_date=FIRST, full_day=False,
            windows=[{"start_minute": 30, "end_minute": 60}])]
    result, context = protected_run(data)
    report = assess((data[0], data[1], result), context=context)
    assert report.scheduled_teaching_minutes == 60 and report.lecturers[0].gap_distribution == ((30, 1),)
    assert report.lecturers[0].maximum_continuous_minutes == 30
    assert not report.total_soft_penalty
    with pytest.raises(ValueError):
        assess_consecutive_sessions(data[0], data[1], result, calendar(data[1]), scoring(), staff_input=data[2])


def test_preference_optimization_reports_remain_independently_checked():
    from test_preference_solver import run as preference_run
    prepared = demand({"OFFER": [("STAFF", "LIC")]}, [("OFFER", "G", 2)])
    prepared, config, result, context = preference_run(prepared=prepared)
    report = assess((prepared, config, result), context=context)
    assert report.lecturers[0].back_to_back_pairs == 1
    corrupt = replace(result, preference_report=replace(result.preference_report, objective_value=999))
    with pytest.raises(ValueError):
        assess_consecutive_sessions(prepared, config, corrupt, calendar(config), scoring(), **context)


def test_fairness_optimization_is_preserved_and_full_original_context_is_required():
    from test_fairness_solver import run as fairness_run
    data, context, result = fairness_run()
    report = assess((data[0], data[1], result), context=context)
    assert report.scheduled_teaching_minutes == 600 and not report.cp_sat_consecutive_objective_enabled
    corrupt = replace(result, fairness_report=replace(result.fairness_report, maximum_normalized_target_deviation=0))
    with pytest.raises(ValueError):
        assess_consecutive_sessions(data[0], data[1], corrupt, context["fairness_calendar"], scoring(), **context)
    with pytest.raises(ValueError):
        assess_consecutive_sessions(data[0], data[1], result, context["fairness_calendar"], scoring(), staff_input=data[2])


def test_corrupted_workload_summary_is_never_used_as_sequence_evidence():
    from test_work_period_solver import scenario
    data = scenario(count=2, minutes=60)
    result = solve_timetable(data[0], data[1], staff_input=data[2])
    changed = replace(result.workload_report.staff[0], daily_teaching_minutes=(("DAY", 0),))
    result = replace(result, workload_report=replace(result.workload_report, staff=(changed,)))
    with pytest.raises(TimetableValidationError, match="workload_summary_changed"):
        assess_consecutive_sessions(data[0], data[1], result, calendar(data[1]), scoring(), staff_input=data[2])


def test_combination_result_requires_original_population_and_venue_context():
    from test_combination_solver import solve, venues
    combinations, config, result = solve(rooms=venues())
    with pytest.raises(ValueError):
        assess_consecutive_sessions(combinations.prepared, config, result, calendar(config), scoring())


def test_missing_evidence_is_reported_even_with_no_scheduled_entities():
    empty = TeachingTimetableInput(staff_members=[], module_offerings=[], teaching_team_members=[], student_groups=[], session_requirements=[])
    prepared = prepare_timetable_input(empty)
    config = windows()
    report = assess((prepared, config, solve_timetable(prepared, config)), settings=policy(soft_criteria=["SHORT_BREAK"]))
    assert report.scoring_complete is False and report.total_soft_penalty is None
    assert "missing_preferred_minimum_break_minutes" in report.diagnostics


def test_explicit_zero_weight_is_a_complete_deliberate_zero_cost():
    report = assess(settings=policy(soft_criteria=["BACK_TO_BACK"], back_to_back_penalty=0))
    assert report.total_soft_penalty == 0 and report.scoring_complete is True


def test_assessment_builds_no_cp_model_and_does_not_modify_result(monkeypatch):
    from ortools.sat.python import cp_model
    data = placed()
    before = data[2].to_dict()
    def unexpected_model(*args, **kwargs):
        raise AssertionError("Step 11A must not construct a CP-SAT model")
    monkeypatch.setattr(cp_model, "CpModel", unexpected_model)
    assert assess(data).total_soft_penalty == 35
    assert data[2].to_dict() == before


def test_low_level_detector_rejects_duplicates_and_overlaps_instead_of_scoring_them():
    _, config, result = placed()
    dates = calendar(config)
    with pytest.raises(ValueError, match="duplicate"):
        detect_consecutive_sequences([result.schedule[0], result.schedule[0]], dates, scoring())
    with pytest.raises(ValueError, match="overlapping"):
        detect_consecutive_sequences([result.schedule[0], replace(result.schedule[1], start_minute=15, end_minute=45)], dates, scoring())


def test_scoring_disabled_entities_keep_metrics_without_requiring_missing_weights():
    report = assess(settings=policy(score_lecturers=False, soft_criteria=["BACK_TO_BACK"]))
    assert report.scoring_complete is None and report.lecturers[0].back_to_back_pairs == 1
    assert report.lecturers[0].penalty_components == ()


def test_report_regeneration_rejects_nonreport_values():
    data = placed()
    with pytest.raises(ConsecutiveInputError):
        validate_consecutive_report({}, *data, calendar(data[1]), scoring())


def test_single_long_required_session_has_excess_minutes_without_an_invented_break_or_pair():
    report = assess(placed([("DAY", 0, 120)]))
    row = report.lecturers[0]
    assert row.back_to_back_pairs == 0 and row.maximum_consecutive_sessions == 1
    assert row.maximum_continuous_minutes == 120 and report.total_soft_penalty == 180


def test_one_combined_booking_is_not_two_consecutive_original_requirements():
    from test_combination_solver import inputs, solve
    combinations, config, result = solve(inputs(frequency=1))
    report = assess((combinations.prepared, config, result), settings=scoring(score_student_groups=True),
        context={"combination_input": combinations})
    assert report.assigned_physical_delivery_count == 1 and result.validation.assigned_occurrences == 2
    assert report.lecturers[0].back_to_back_pairs == 0 and report.total_soft_penalty == 0
    assert all(row.teaching.assigned_delivery_count == 1 for row in report.student_groups)


def test_confirmed_student_break_scope_is_independent_of_staff_and_soft_scoring():
    prepared = demand({"A": [("FIRST", "LIC")], "B": [("SECOND", "LIC")]}, [("A", "G", 1), ("B", "G", 1)])
    report = assess(placed(prepared=prepared), settings=policy(
        confirmed_hard_minimum_break=hard(applies_to=[AttendanceKind.STUDENT_GROUP])))
    assert report.confirmed_hard_break_violation_count == 1 and not report.confirmed_hard_break_compliant
    assert all(row.confirmed_hard_break_compliant is None for row in report.lecturers)
    assert report.total_soft_penalty is None


def test_penalties_are_exact_integers_with_no_float_scaling():
    settings = policy(soft_criteria=["BACK_TO_BACK"], back_to_back_penalty=10**20)
    report = assess(settings=settings)
    assert type(report.total_soft_penalty) is int and report.total_soft_penalty == 10**20
