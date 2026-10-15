"""All scheduling modes jointly enabled, using only explicit synthetic evidence."""

from collections import Counter
from dataclasses import replace
from fractions import Fraction
from itertools import combinations
from pathlib import Path
import runpy

from ortools.sat.python import cp_model
import pytest

from schedence_teaching.consecutive import ConfirmedMinimumBreak, assess_consecutive_sessions
from schedence_teaching.fairness import assess_fairness
from schedence_teaching.preferences import score_lecturer_preferences
from schedence_teaching.preparation import PreparationError, prepare_group_combinations
from schedence_teaching.schemas import GroupCombinationInput
from schedence_teaching.solver import (
    SolverInputError, SolverStatus, StaffBlockedInterval, TimetableValidationError,
    solve_timetable, validate_consecutive_result,
)
from schedence_teaching.solver.evaluation import validate_evaluation_result
from schedence_teaching.workload import WorkPeriodInput, prepare_work_periods


EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
FIXTURE = runpy.run_path(str(EXAMPLES / "integration_fixture.py"))
build_case, run_case = FIXTURE["build_case"], FIXTURE["run_case"]


def validate(case, result):
    return validate_consecutive_result(case["prepared"], case["config"], result, **case["inputs"])


def reprepare(case, source):
    combined = prepare_group_combinations(GroupCombinationInput.model_validate(source))
    case["prepared"], case["inputs"]["combination_input"] = combined.prepared, combined
    refresh_periods(case)


def refresh_periods(case, source=None):
    inputs = case["inputs"]
    inputs["work_periods"] = prepare_work_periods(case["prepared"].input_snapshot,
        inputs["staff_input"].workload_input, source or inputs["work_periods"].input_snapshot)


def assert_failure(case, result, status=SolverStatus.INFEASIBLE):
    assert result.status is status and result.schedule == result.administrative_schedule == ()
    assert result.validation is result.workload_report is result.work_period_report is None
    assert result.preference_report is result.fairness_report is result.consecutive_report is None
    assert result.objective_value is result.objective_status is None
    assert validate(case, result) is None
    assert result.venue_enabled and result.staff_policy_enabled and result.combination_enabled
    assert result.preference_optimization_enabled and result.fairness_optimization_enabled
    assert result.consecutive_optimization_enabled and result.work_period_enabled
    assert result.failure_reason and result.to_dict()["university_ready"] is False
    with pytest.raises(SolverInputError, match="complete feasible"):
        validate_evaluation_result(case["prepared"], case["config"], result, **case["inputs"])


@pytest.fixture(scope="module")
def integrated():
    case = build_case(time_limit=10.0)
    before = repr((case["prepared"], case["config"], case["inputs"]))
    result, stats = run_case(case)
    assert repr((case["prepared"], case["config"], case["inputs"])) == before
    assert result.status is SolverStatus.OPTIMAL
    return case, result, stats


def test_all_modes_and_exact_mixed_coverage(integrated):
    case, result, _ = integrated
    assert validate(case, result) == result.validation
    assert result.validation.expected_occurrences == result.validation.assigned_occurrences == 7
    coverage = Counter(row.occurrence_id for delivery in result.schedule for row in delivery.covered_sessions)
    assert coverage == Counter(row.occurrence_id for row in case["prepared"].sessions)
    assert result.validation.combined_deliveries == 1 and result.validation.separate_deliveries == 5
    assert len(result.schedule) == 6 and result.validation.lecturer_contact_minutes == 315
    assert sum(row.duration_minutes for row in case["prepared"].sessions) == 360
    assert len(case["inputs"]["combination_input"].candidates) == 1


def test_venues_staff_groups_and_protected_intervals_are_jointly_respected(integrated):
    case, result, _ = integrated
    inputs = case["inputs"]
    venues = {row.venue_id: row for row in inputs["venue_input"].venues}
    for delivery in result.schedule:
        assert delivery.combined_student_count <= venues[delivery.assigned_venue_id].capacity
        assert all(row.stream in next(day.windows for day in case["config"].days if day.day == delivery.day)
            for row in delivery.covered_sessions)
        if any(row.session_type.value == "LAB" for row in delivery.covered_sessions):
            assert venues[delivery.assigned_venue_id].venue_type.value == "Computer Lab"
        for interval in inputs["work_periods"].protected_intervals:
            if interval.staff_id == delivery.assigned_staff_id and interval.on_date == inputs["staff_input"].day_dates[delivery.day]:
                assert delivery.end_minute <= interval.start_minute or interval.end_minute <= delivery.start_minute
    for first, second in combinations(result.schedule, 2):
        if first.day != second.day or first.end_minute <= second.start_minute or second.end_minute <= first.start_minute:
            continue
        assert first.assigned_staff_id != second.assigned_staff_id
        assert first.assigned_venue_id != second.assigned_venue_id
        assert not any(case["prepared"].hierarchy.conflicts(a, b) for a in first.group_ids for b in second.group_ids)


def test_workload_admin_completion_and_campus_attendance_remain_distinct(integrated):
    _, result, _ = integrated
    rows = {row.staff_id: row for row in result.work_period_report.staff}
    professor = rows["SYN-1-P"]
    assert professor.required_teaching_minutes == 360 and professor.administrative_allocation_minutes == 240
    assert professor.reserved_administrative_minutes == professor.fixed_reserved_minutes == 240
    assert professor.verified_completed_administrative_minutes == 0
    assert professor.administrative_completion_status == "NOT_VERIFIED" and professor.administrative_completion_fulfilled is None
    assert professor.campus_attendance_status == "EXEMPT" and not professor.evidenced_campus_days
    assert rows["SYN-1-L"].campus_attendance_status == "NOT_VERIFIED"
    assert rows["SYN-1-I"].required_teaching_minutes == 1200
    assert sum(row.scheduled_teaching_minutes for row in rows.values()) == 315
    assert all(row.teaching_requirement_status == "BELOW_TARGET" for row in rows.values())


def test_every_assessment_entry_point_retains_original_joint_context(integrated):
    case, result, _ = integrated
    prepared, config, original = case["prepared"], case["config"], case["inputs"]
    assert validate_evaluation_result(prepared, config, result, **original) == result.validation
    preferences = score_lecturer_preferences(prepared, config, result, original["preference_input"],
        original["preference_calendar"], original["preference_policy"], **{key: value for key, value in original.items()
            if key not in {"preference_input", "preference_calendar", "preference_policy"}})
    assert preferences.total_weighted_penalty == result.preference_report.score.total_weighted_penalty == 0
    assert not any(row.staff_id.endswith("-I") for row in original["preference_input"].input_snapshot.preferences)
    fairness = assess_fairness(prepared, config, result, original["fairness_calendar"], original["fairness_policy"], **original)
    assert sum(row.teaching.scheduled_teaching_minutes for row in fairness.staff) == 315
    consecutive = assess_consecutive_sessions(prepared, config, result, original["consecutive_calendar"], original["consecutive_policy"], **original)
    assert consecutive == result.consecutive_report.assessment
    assert consecutive.total_soft_penalty == result.consecutive_report.total_weighted_penalty == 30
    groups = {row.entity_id: row for row in consecutive.student_groups}
    assert groups["SYN-1-G1"].teaching.assigned_delivery_count == 2
    assert groups["SYN-1-G1-LAB"].teaching.assigned_delivery_count == 3
    assert len(groups["SYN-1-G1-LAB"].inherited_delivery_ids) == 2


def test_joint_objectives_are_exact_and_proven_in_the_declared_order(integrated):
    _, result, _ = integrated
    report = result.consecutive_report
    assert report.priority_order == ("DELIVERIES", "CONTACT_MINUTES", "PREFERENCE_PENALTY",
        "MAXIMUM_TARGET_DEVIATION", "AGGREGATE_TARGET_DEVIATION", "CONSECUTIVE_PENALTY")
    assert report.objective_vector == (6, 315, 0, 13500, 50310, 30)
    assert all(row.status == "OPTIMAL" for row in report.stages) and report.all_stages_proven_optimal
    assert result.fairness_report.maximum_normalized_target_deviation == Fraction(15, 16)
    assert result.fairness_report.aggregate_normalized_target_deviation == Fraction(559, 160)
    assert result.preference_report.objective_status == result.fairness_report.objective_status == "EVALUATED_COMPONENT"


@pytest.mark.parametrize("layout,deliveries,minutes,combined", [("combined", 5, 270, 2), ("separate", 7, 360, 0)])
def test_all_features_support_combined_and_separate_layouts(layout, deliveries, minutes, combined):
    case = build_case(layout=layout, time_limit=10.0)
    result, _ = run_case(case)
    assert result.status is SolverStatus.OPTIMAL
    assert len(result.schedule) == deliveries and result.validation.combined_deliveries == combined
    assert result.validation.lecturer_contact_minutes == minutes and result.validation.assigned_occurrences == 7


def test_required_flexible_admin_reservation_integrates_with_every_objective():
    case = build_case(administrative_mode="flexible", time_limit=10.0)
    result, _ = run_case(case)
    assert result.status is SolverStatus.OPTIMAL and len(result.administrative_schedule) == 1
    professor = next(row for row in result.work_period_report.staff if row.staff_id == "SYN-1-P")
    assert professor.fixed_reserved_minutes == 0 and professor.flexible_reserved_minutes == 240
    assert professor.verified_completed_administrative_minutes == 0


@pytest.mark.parametrize("cause", ["capacity", "venue_calendar", "eligible_staff", "weekly_caps", "availability"])
def test_joint_resource_shortages_cannot_be_overridden_by_soft_objectives(cause):
    case = build_case()
    inputs = case["inputs"]
    if cause == "capacity":
        for venue in inputs["venue_input"].venues:
            venue.capacity = 1
    elif cause == "venue_calendar":
        for row in inputs["venue_input"].calendars:
            row.available_windows = {day: [] for day in row.available_windows}
    elif cause in {"eligible_staff", "availability"}:
        source = inputs["combination_input"].input_snapshot.model_dump(round_trip=True)
        source["teaching_input"]["teaching_team_members"] = [row for row in source["teaching_input"]["teaching_team_members"]
            if row["offering_id"] != "SYN-1-PROGRAMMING" or cause == "availability" and row["staff_id"] == "SYN-1-P"]
        reprepare(case, source)
        if cause == "availability":
            staff = next(row for row in inputs["staff_input"].availability if row.staff_id == "SYN-1-P")
            staff.blocked_periods.extend([StaffBlockedInterval(day="TUESDAY", start_minute=540, end_minute=780),
                StaffBlockedInterval(day="MONDAY", start_minute=840, end_minute=900)])
    else:
        for row in inputs["staff_input"].availability:
            if row.staff_id in {"SYN-1-P", "SYN-1-D"}:
                # Each can fit a 45-minute lecture; together their explicit caps
                # still cannot cover the minimum 135 physical lecture minutes.
                row.weekly_teaching_max_minutes = 60
    result = solve_timetable(case["prepared"], case["config"], **inputs)
    assert_failure(case, result)


def test_contradictory_population_evidence_is_rejected_before_scheduling():
    case = build_case()
    source = case["inputs"]["combination_input"].input_snapshot.model_dump(round_trip=True)
    source["population_evidence"].append(dict(group_ids=["SYN-1-G1", "SYN-1-G2"],
        relationship="OVERLAPPING", evidence_reference="synthetic:conflicting-evidence"))
    with pytest.raises(PreparationError) as caught:
        reprepare(case, source)
    assert "conflicting_population_evidence" in {row.code for row in caught.value.issues}


def test_unknown_population_prevents_combining_without_losing_original_requirements():
    case = build_case(time_limit=10.0)
    source = case["inputs"]["combination_input"].input_snapshot.model_dump(round_trip=True)
    source["population_evidence"] = []
    reprepare(case, source)
    result, _ = run_case(case)
    assert not case["inputs"]["combination_input"].candidates
    assert len(result.schedule) == 7 and result.validation.combined_deliveries == 0


def test_overlapping_approved_admin_and_research_reservations_are_rejected():
    case = build_case()
    source = case["inputs"]["work_periods"].input_snapshot.model_dump(round_trip=True)
    research_policy = {**source["research_policies"][0], "staff_id": "SYN-1-P"}
    source["research_policies"].append(research_policy)
    source["research_reservations"].append({**source["research_reservations"][0], "staff_id": "SYN-1-P",
        "reservation_id": "SYNTHETIC-CONFLICT", "on_date": FIXTURE["FIRST"], "windows": [FIXTURE["window"](600, 720)]})
    with pytest.raises(PreparationError) as caught:
        refresh_periods(case, WorkPeriodInput.model_validate(source))
    assert "conflicting_protected_periods" in {row.code for row in caught.value.issues}


@pytest.mark.parametrize("missing", ["consecutive_config", "consecutive_weight", "fairness_approval", "staff_profile", "venue_blocks"])
def test_missing_authoritative_inputs_fail_clearly(missing):
    case = build_case()
    original = case["inputs"]
    if missing == "consecutive_config":
        original["consecutive_config"] = None
    elif missing == "consecutive_weight":
        original["consecutive_policy"].short_break_minute_penalty = None
    elif missing == "fairness_approval":
        for key in ("consecutive_calendar", "fairness_calendar", "preference_calendar"):
            original[key] = original[key].model_copy(update={"synthetic_data": False})
    elif missing == "staff_profile":
        original["staff_input"].workload_input.profiles = original["staff_input"].workload_input.profiles[1:]
    else:
        original["venue_input"].calendars[0].blocked_periods = None
    with pytest.raises(SolverInputError):
        solve_timetable(case["prepared"], case["config"], **original)


def test_impossible_confirmed_group_hard_break_returns_infeasible():
    case = build_case()
    case["inputs"]["consecutive_policy"].confirmed_hard_minimum_break = ConfirmedMinimumBreak(minutes=600,
        confirmation="CONFIRMED", campus_policy_reference="synthetic:impossible-rule", approval_reference="synthetic:approval",
        applies_to={"STUDENT_GROUP"})
    case["inputs"]["consecutive_config"].enforce_confirmed_hard_break = True
    result = solve_timetable(case["prepared"], case["config"], **case["inputs"])
    assert_failure(case, result)
    assert "minimum-break" in result.failure_reason


@pytest.mark.parametrize("status", [SolverStatus.UNKNOWN, SolverStatus.MODEL_INVALID])
def test_initial_native_failure_returns_no_complete_timetable(monkeypatch, status):
    case = build_case()
    native_status = cp_model.UNKNOWN if status is SolverStatus.UNKNOWN else cp_model.MODEL_INVALID
    monkeypatch.setattr(cp_model.CpSolver, "solve", lambda *args, **kwargs: native_status)
    result = solve_timetable(case["prepared"], case["config"], **case["inputs"])
    assert_failure(case, result, status)


@pytest.mark.parametrize("component", ["preference", "fairness", "consecutive"])
def test_joint_calendar_applicability_cannot_be_changed_for_one_component(component):
    case = build_case()
    case["inputs"][f"{component}_calendar"] = case["inputs"][f"{component}_calendar"].model_copy(update={"week_reference": "OTHER-WEEK"})
    with pytest.raises(SolverInputError):
        solve_timetable(case["prepared"], case["config"], **case["inputs"])


@pytest.mark.parametrize("stop", ["FEASIBLE", "LATER_UNKNOWN"])
def test_joint_nonoptimal_incumbent_remains_independently_validated(monkeypatch, stop):
    case = build_case(time_limit=10.0)
    native, calls = cp_model.CpSolver.solve, []
    def controlled(solver, model, *args, **kwargs):
        calls.append(1)
        if len(calls) > 1:
            return cp_model.UNKNOWN
        actual = native(solver, model, *args, **kwargs)
        assert actual == cp_model.OPTIMAL
        return cp_model.FEASIBLE if stop == "FEASIBLE" else actual
    monkeypatch.setattr(cp_model.CpSolver, "solve", controlled)
    result = solve_timetable(case["prepared"], case["config"], **case["inputs"])
    assert result.status is SolverStatus.FEASIBLE and validate(case, result) == result.validation
    assert not result.consecutive_report.all_stages_proven_optimal
    assert result.consecutive_report.stages[-1].status == ("FEASIBLE" if stop == "FEASIBLE" else "UNKNOWN")


@pytest.mark.parametrize("cause", ["coverage", "capacity", "staff", "stream", "admin", "preference", "fairness", "consecutive", "history", "mode", "validation"])
def test_complete_validator_rejects_corruption_across_components(integrated, cause):
    case, result, _ = integrated
    first = result.schedule[0]
    if cause == "coverage":
        invalid = replace(result, schedule=(*result.schedule, first))
    elif cause == "capacity":
        invalid = replace(result, schedule=(replace(first, combined_student_count=1), *result.schedule[1:]))
    elif cause == "staff":
        invalid = replace(result, schedule=(replace(first, assigned_staff_id="UNKNOWN"), *result.schedule[1:]))
    elif cause == "stream":
        weekend = next(row for row in result.schedule if row.day == "SATURDAY")
        invalid = replace(result, schedule=tuple(replace(row, day="TUESDAY") if row is weekend else row for row in result.schedule))
    elif cause == "admin":
        invalid = replace(result, work_period_report=replace(result.work_period_report, fixed_administrative_periods=()))
    elif cause == "preference":
        invalid = replace(result, preference_report=replace(result.preference_report,
            score=replace(result.preference_report.score, total_weighted_penalty=999)))
    elif cause == "fairness":
        invalid = replace(result, fairness_report=replace(result.fairness_report, maximum_normalized_target_deviation=Fraction(0)))
    elif cause == "consecutive":
        invalid = replace(result, consecutive_report=replace(result.consecutive_report, total_weighted_penalty=999))
    elif cause == "history":
        invalid = replace(result, consecutive_report=replace(result.consecutive_report, stages=tuple(reversed(result.consecutive_report.stages))))
    elif cause == "mode":
        invalid = replace(result, staff_policy_enabled=False)
    else:
        invalid = replace(result, validation=replace(result.validation, assigned_occurrences=999))
    with pytest.raises((SolverInputError, TimetableValidationError)):
        validate(case, invalid)


def test_joint_validation_does_not_construct_a_cp_model(integrated, monkeypatch):
    case, result, _ = integrated
    def unexpected(*args, **kwargs):
        raise AssertionError("Independent validation attempted to build a model")
    monkeypatch.setattr(cp_model, "CpModel", unexpected)
    assert validate(case, result) == result.validation


def test_benchmark_counts_and_timing_boundaries_are_explicit(integrated):
    _, result, stats = integrated
    assert (stats["staff_count"], stats["academic_lecturer_count"], stats["instructor_count"]) == (4, 3, 1)
    assert (stats["student_group_count"], stats["original_occurrences"], stats["combined_occurrence_alternatives"], stats["venue_count"]) == (5, 7, 2, 3)
    assert (stats["combination_candidate_count"], stats["total_delivery_alternatives"]) == (1, 9)
    assert stats["maximum_model_variables"] > 0 and stats["maximum_model_constraints"] > 0
    assert stats["solve_and_internal_reporting_seconds"] >= stats["native_solve_seconds"] > 0
    assert stats["model_preparation_seconds"] > 0 and stats["independent_validation_seconds"] > 0
    assert stats["memory_bytes"] is None and stats["independently_validated"]
    assert len(stats["native_stages"]) == len(result.consecutive_report.stages)


def test_output_limitations_describe_current_scope_without_claiming_readiness(integrated):
    _, result, _ = integrated
    output = result.to_dict()
    text = " ".join(output["limitations"])
    assert "fairness are not implemented" not in text
    assert "Student lunch constraints are not implemented" in text
    assert "Travel optimization is outside the research scope" in text
    assert output["university_ready"] is False


@pytest.mark.parametrize("updates", [{"copies": 0}, {"copies": 4}, {"copies": True}, {"layout": "unknown"}, {"administrative_mode": "unknown"}])
def test_fixture_rejects_unbounded_or_unknown_benchmark_requests(updates):
    with pytest.raises(ValueError):
        build_case(**updates)
