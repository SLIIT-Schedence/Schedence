"""Synthetic dated availability, explicit caps and evidence-aware policy results."""

from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
import runpy

import pytest
from pydantic import ValidationError
from ortools.sat.python import cp_model

from schedence_teaching.preparation import prepare_timetable_input, prepare_group_combinations
from schedence_teaching.schemas import TeachingTimetableInput
from schedence_teaching.solver import (
    StaffAvailability, StaffBlockedInterval, StaffSchedulingInput, SolverInputError, SolverStatus,
    TimetableValidationError, TimeWindow, solve_timetable, validate_delivery_result,
    validate_staff_policy_result, validate_timetable,
)
from schedence_teaching.workload import (
    AttendanceStatus, CampusAttendanceRecord, FulfillmentStatus, RecognizedWorkRecord,
    StaffWorkProfile, WeeklyWorkloadInput, WorkloadBand, assess_weekly_workload, confirmed_campus_policy,
    WorkloadTargetBasis,
)

from test_staff_solver import demand, windows
from test_combination_solver import inputs as combination_inputs, venues


def policy_input(prepared, config, *, band="LECTURER", appointment=None, complete=True):
    first, last = date(2026, 10, 12), date(2026, 10, 18)
    workload = WeeklyWorkloadInput(
        # Preserve Step 8's combined-target fixtures as explicit legacy scenarios.
        week_reference="synthetic:2026-10-12-week",
        policy=confirmed_campus_policy(target_basis=WorkloadTargetBasis.TOTAL_RECOGNIZED_WORK),
        profiles=[StaffWorkProfile(staff_id=row.staff_id,
                    workload_band="INSTRUCTOR" if row.staff_type.value == "INSTRUCTOR" else band,
                    appointment=appointment, evidence_reference="synthetic:staff-policy")
                  for row in prepared.input_snapshot.staff_members],
        recognized_work_complete_for={row.staff_id for row in prepared.input_snapshot.staff_members} if complete else set())
    return StaffSchedulingInput(
        workload_input=workload, day_dates={day.day: first + timedelta(days=index) for index, day in enumerate(config.days)},
        policy_effective_from=first, policy_effective_until=last,
        availability=[StaffAvailability(
            staff_id=row.staff_id, allowed_teaching_days={day.day for day in config.days},
            available_windows={day.day: [TimeWindow(start_minute=0, end_minute=1440)] for day in config.days},
            blocked_periods=[], effective_from=first, effective_until=last)
            for row in prepared.input_snapshot.staff_members])


def one(*, count=1, minutes=60, days=("DAY",), duration=30, band="LECTURER", appointment=None, complete=True):
    prepared = demand({"OFFER": [("STAFF", "LIC")]}, requirements=[("OFFER", "GROUP", count)])
    if duration != 30:
        payload = prepared.input_snapshot.model_dump(round_trip=True)
        payload["session_requirements"][0]["duration_minutes"] = duration
        prepared = prepare_timetable_input(TeachingTimetableInput.model_validate(payload))
    config = windows(minutes, days=days)
    return prepared, config, policy_input(prepared, config, band=band, appointment=appointment, complete=complete)


def checked_solve(prepared, config, inputs, **kwargs):
    result = solve_timetable(prepared, config, staff_input=inputs, **kwargs)
    assert result.staff_policy_enabled
    if result.status in {SolverStatus.OPTIMAL, SolverStatus.FEASIBLE}:
        assert validate_staff_policy_result(prepared, config, result, staff_input=inputs, **kwargs) == result.validation
        assert assess_weekly_workload(prepared, config, result, inputs.workload_input, staff_input=inputs, **kwargs) == result.workload_report
    else:
        assert result.schedule == () and result.workload_report is None
    return result


def test_policy_enabled_available_delivery_and_attached_summary():
    prepared, config, inputs = one()
    inputs.availability[0].available_windows["DAY"] = [TimeWindow(start_minute=30, end_minute=60)]
    result = checked_solve(prepared, config, inputs)
    assert result.status is SolverStatus.OPTIMAL
    assert (result.schedule[0].start_minute, result.schedule[0].end_minute) == (30, 60)
    row = result.workload_report.staff[0]
    assert row.daily_teaching_minutes == (("DAY", 30),) and row.scheduled_teaching_minutes == 30
    assert row.target_status is FulfillmentStatus.BELOW_TARGET and row.target_shortfall_minutes == 930
    assert row.attendance_status is AttendanceStatus.NOT_VERIFIED
    assert result.to_dict()["workload_assessment"]["staff_availability_and_caps_enforced"]
    assert not result.to_dict()["university_ready"]


@pytest.mark.parametrize("cause", ["no_allowed_day", "empty_windows", "short_windows", "blocked", "expired", "not_yet_effective"])
def test_valid_but_unusable_staff_calendar_is_infeasible(cause):
    prepared, config, inputs = one()
    calendar = inputs.availability[0]
    if cause == "no_allowed_day":
        calendar.allowed_teaching_days, calendar.available_windows = set(), {}
    elif cause == "empty_windows":
        calendar.available_windows["DAY"] = []
    elif cause == "short_windows":
        calendar.available_windows["DAY"] = [TimeWindow(start_minute=0, end_minute=15)]
    elif cause == "blocked":
        calendar.blocked_periods = [StaffBlockedInterval(day="DAY", start_minute=0, end_minute=60)]
    elif cause == "expired":
        calendar.effective_from, calendar.effective_until = date(2026, 10, 1), date(2026, 10, 11)
    else:
        calendar.effective_from, calendar.effective_until = date(2026, 10, 13), date(2026, 10, 18)
    assert checked_solve(prepared, config, inputs).status is SolverStatus.INFEASIBLE


def test_block_boundary_touching_is_legal():
    prepared, config, inputs = one()
    inputs.availability[0].blocked_periods = [StaffBlockedInterval(day="DAY", start_minute=0, end_minute=30)]
    result = checked_solve(prepared, config, inputs)
    assert result.schedule[0].start_minute == 30


def test_unaligned_availability_and_block_edges_round_inward():
    prepared, config, inputs = one(minutes=120)
    calendar = inputs.availability[0]
    calendar.available_windows["DAY"] = [TimeWindow(start_minute=1, end_minute=119)]
    calendar.blocked_periods = [StaffBlockedInterval(day="DAY", start_minute=31, end_minute=61)]
    result = checked_solve(prepared, config, inputs)
    assert result.schedule[0].start_minute == 75 and result.schedule[0].end_minute == 105


def test_staff_window_gaps_are_not_bridged():
    prepared, config, inputs = one()
    inputs.availability[0].available_windows["DAY"] = [TimeWindow(start_minute=0, end_minute=15), TimeWindow(start_minute=15, end_minute=30)]
    assert checked_solve(prepared, config, inputs).status is SolverStatus.INFEASIBLE


def test_joint_solver_can_select_another_staff_member_with_availability():
    prepared = demand({"OFFER": [("FIRST", "LIC"), ("SECOND", "TEAM_MEMBER")]})
    config = windows()
    inputs = policy_input(prepared, config)
    inputs.availability[0].available_windows["DAY"] = []
    result = checked_solve(prepared, config, inputs)
    assert result.schedule[0].assigned_staff_id == "SECOND"


@pytest.mark.parametrize("field,value", [("daily_teaching_max_minutes", 0), ("daily_teaching_max_minutes", 29),
                                         ("weekly_teaching_max_minutes", 0), ("weekly_teaching_max_minutes", 59)])
def test_explicit_daily_weekly_caps_can_make_demand_infeasible(field, value):
    prepared, config, inputs = one(count=2)
    setattr(inputs.availability[0], field, value)
    assert checked_solve(prepared, config, inputs).status is SolverStatus.INFEASIBLE


def test_daily_cap_spreads_actual_teaching_over_available_days():
    prepared, config, inputs = one(count=3, days=("D1", "D2", "D3"))
    inputs.availability[0].daily_teaching_max_minutes = 30
    result = checked_solve(prepared, config, inputs)
    assert result.workload_report.staff[0].daily_teaching_minutes == (("D1", 30), ("D2", 30), ("D3", 30))


def test_policy_effective_dates_restrict_days_even_without_a_teaching_cap():
    prepared, config, inputs = one(days=("D1", "D2"))
    inputs.availability[0].effective_from = date(2026, 10, 13)
    result = checked_solve(prepared, config, inputs)
    assert result.schedule[0].day == "D2"


@pytest.mark.parametrize("cap", ["daily_teaching_max_minutes", "weekly_teaching_max_minutes"])
def test_caps_sum_weekday_and_weekend_teaching_on_same_day(cap):
    prepared = demand({"A": [("STAFF", "LIC")], "B": [("STAFF", "LIC")]}, streams={"B": "WEEKEND"})
    config = windows(60, both_streams=True)
    inputs = policy_input(prepared, config)
    setattr(inputs.availability[0], cap, 30)
    assert checked_solve(prepared, config, inputs).status is SolverStatus.INFEASIBLE
    setattr(inputs.availability[0], cap, 60)
    result = checked_solve(prepared, config, inputs)
    assert result.workload_report.staff[0].scheduled_teaching_minutes == 60


def test_weekly_caps_span_multiple_days_not_separate_per_day():
    prepared, config, inputs = one(count=2, days=("D1", "D2"))
    inputs.availability[0].daily_teaching_max_minutes = 30
    inputs.availability[0].weekly_teaching_max_minutes = 59
    assert checked_solve(prepared, config, inputs).status is SolverStatus.INFEASIBLE
    inputs.availability[0].weekly_teaching_max_minutes = 60
    assert checked_solve(prepared, config, inputs).workload_report.staff[0].scheduled_teaching_minutes == 60


@pytest.mark.parametrize("capacity,large_minutes,expected_minutes,deliveries", [(40, None, 60, 2), (20, None, 120, 4), (40, 30, 90, 3)])
def test_combined_separate_mixed_workload_and_caps(capacity, large_minutes, expected_minutes, deliveries):
    combinations = prepare_group_combinations(combination_inputs())
    prepared, config = combinations.prepared, windows(120)
    inputs = policy_input(prepared, config, band="PROFESSOR")
    inputs.availability[0].weekly_teaching_max_minutes = expected_minutes
    rooms = venues(capacity, large_minutes=large_minutes)
    result = checked_solve(prepared, config, inputs, venue_input=rooms, combination_input=combinations)
    assert result.status is SolverStatus.OPTIMAL and len(result.schedule) == deliveries
    row = result.workload_report.staff[0]
    assert row.scheduled_teaching_minutes == expected_minutes
    assert row.daily_teaching_minutes == (("DAY", expected_minutes),)
    assert row.target_shortfall_minutes == 360 - expected_minutes
    assert validate_delivery_result(prepared, config, result, combination_input=combinations,
                                     venue_input=rooms, staff_input=inputs) == result.validation


def test_cap_counts_actual_combined_workload_once_and_unselected_alternatives_zero():
    combinations = prepare_group_combinations(combination_inputs())
    prepared, config = combinations.prepared, windows(120)
    inputs = policy_input(prepared, config)
    inputs.availability[0].weekly_teaching_max_minutes = 60
    result = checked_solve(prepared, config, inputs, combination_input=combinations)
    assert result.status is SolverStatus.OPTIMAL and result.validation.combined_deliveries == 2
    inputs.availability[0].weekly_teaching_max_minutes = 59
    assert checked_solve(prepared, config, inputs, combination_input=combinations).status is SolverStatus.INFEASIBLE


def test_professor_target_is_not_an_automatic_teaching_maximum():
    prepared, config, inputs = one(count=4, duration=120, minutes=480, band="PROFESSOR")
    result = checked_solve(prepared, config, inputs)
    row = result.workload_report.staff[0]
    assert result.status is SolverStatus.OPTIMAL and row.scheduled_teaching_minutes == 480
    assert row.policy.weekly_target_minutes == 360 and row.target_status is FulfillmentStatus.EXCEEDED
    assert row.attendance_status is AttendanceStatus.EXEMPT


def test_missing_work_evidence_does_not_require_extra_lectures_or_make_solve_infeasible():
    prepared, config, inputs = one(complete=False)
    result = checked_solve(prepared, config, inputs)
    row = result.workload_report.staff[0]
    assert result.status is SolverStatus.OPTIMAL and len(result.schedule) == 1
    assert row.target_status is FulfillmentStatus.NOT_VERIFIED and row.target_shortfall_minutes is None
    assert row.to_dict()["remaining_uncredited_target_minutes"] == 930
    assert "recognized_work_ledger" in row.missing_evidence
    assert result.workload_report.completion_status == "NOT_VERIFIED"


@pytest.mark.parametrize("complete,expected", [(False, FulfillmentStatus.NOT_VERIFIED), (True, FulfillmentStatus.BELOW_TARGET)])
def test_partial_admin_other_evidence_has_separate_credits_and_unknown_or_known_shortfall(complete, expected):
    prepared, config, inputs = one(band="PROFESSOR", complete=complete)
    inputs.workload_input.recognized_work = [
        RecognizedWorkRecord(work_id="ADMIN", staff_id="STAFF", work_type="ADMINISTRATIVE", duration_minutes=120, evidence_reference="synthetic:admin"),
        RecognizedWorkRecord(work_id="OTHER", staff_id="STAFF", work_type="OTHER_RECOGNIZED", duration_minutes=60, evidence_reference="synthetic:other")]
    result = checked_solve(prepared, config, inputs)
    row = result.workload_report.staff[0]
    assert (row.scheduled_teaching_minutes, row.administrative_minutes, row.other_recognized_minutes) == (30, 120, 60)
    assert row.known_total_work_minutes == 210 and row.target_status is expected
    assert row.target_shortfall_minutes == (150 if complete else None)


def test_partial_ledger_with_enough_credit_is_never_reported_complete():
    prepared, config, inputs = one(band="PROFESSOR", complete=False)
    inputs.workload_input.recognized_work = [RecognizedWorkRecord(
        work_id="ADMIN", staff_id="STAFF", work_type="ADMINISTRATIVE", duration_minutes=330, evidence_reference="synthetic:admin")]
    result = checked_solve(prepared, config, inputs)
    assert result.workload_report.staff[0].target_fulfilled
    assert result.workload_report.completion_status == "NOT_VERIFIED"


@pytest.mark.parametrize("band,appointment,target,attendance", [
    ("LECTURER", None, 960, AttendanceStatus.NOT_VERIFIED),
    ("ASSISTANT_PROFESSOR", None, 360, AttendanceStatus.NOT_VERIFIED),
    ("PROFESSOR", None, 360, AttendanceStatus.EXEMPT),
    ("LECTURER", "HOD", 240, AttendanceStatus.EXEMPT),
    ("LECTURER", "DEAN", 240, AttendanceStatus.EXEMPT),
])
def test_teaching_one_day_does_not_impose_six_teaching_days_or_infer_absence(band, appointment, target, attendance):
    prepared, config, inputs = one(band=band, appointment=appointment)
    result = checked_solve(prepared, config, inputs)
    row = result.workload_report.staff[0]
    assert row.teaching_days == ("DAY",) and row.evidenced_campus_days == ()
    assert row.attendance_status is attendance and row.policy.weekly_target_minutes == target
    if appointment:
        assert row.policy.workload_band is WorkloadBand.LECTURER


def test_independent_six_day_attendance_can_include_days_without_teaching():
    prepared, config, inputs = one()
    inputs.workload_input.attendance = [CampusAttendanceRecord(
        staff_id="STAFF", working_days=[f"D{i}" for i in range(6)], complete=True, evidence_reference="synthetic:attendance")]
    result = checked_solve(prepared, config, inputs)
    assert result.workload_report.staff[0].attendance_status is AttendanceStatus.MET


def test_instructor_still_has_no_invented_attendance_requirement():
    prepared = demand({"OFFER": [("INS", "TEAM_MEMBER")]}, staff_types={"INS": "INSTRUCTOR"})
    config = windows()
    inputs = policy_input(prepared, config)
    result = checked_solve(prepared, config, inputs)
    row = result.workload_report.staff[0]
    assert row.policy.weekly_target_minutes == 1200 and row.attendance_status is AttendanceStatus.UNSPECIFIED
    assert "campus_attendance_policy" in row.missing_evidence


@pytest.mark.parametrize("missing", ["availability", "profile", "dates"])
def test_policy_mode_rejects_missing_required_inputs(missing):
    prepared, config, inputs = one()
    if missing == "availability":
        inputs.availability = []
    elif missing == "profile":
        inputs.workload_input.profiles = []
    else:
        inputs.day_dates = {}
    with pytest.raises(SolverInputError, match="[Mm]issing|explicit date"):
        solve_timetable(prepared, config, staff_input=inputs)


@pytest.mark.parametrize("corruption", ["duplicate", "unknown_staff", "unknown_day", "unknown_block", "inactive_global_policy"])
def test_invalid_calendar_configuration_fails_before_solving(corruption):
    prepared, config, inputs = one()
    if corruption == "duplicate":
        inputs.availability.append(inputs.availability[0])
    elif corruption == "unknown_staff":
        inputs.availability[0].staff_id = "UNKNOWN"
    elif corruption == "unknown_day":
        inputs.availability[0].allowed_teaching_days.add("UNKNOWN")
        inputs.availability[0].available_windows["UNKNOWN"] = []
    elif corruption == "unknown_block":
        inputs.availability[0].blocked_periods = [StaffBlockedInterval(day="UNKNOWN", start_minute=0, end_minute=30)]
    else:
        inputs.policy_effective_until = date(2026, 10, 11)
    with pytest.raises(SolverInputError):
        solve_timetable(prepared, config, staff_input=inputs)


@pytest.mark.parametrize("field,value", [("daily_teaching_max_minutes", -1), ("weekly_teaching_max_minutes", True),
                                         ("daily_teaching_max_minutes", "30"), ("weekly_teaching_max_minutes", 30.5)])
def test_invalid_explicit_caps_rejected(field, value):
    _, _, inputs = one()
    payload = inputs.availability[0].model_dump(round_trip=True)
    payload[field] = value
    with pytest.raises(ValidationError):
        StaffAvailability.model_validate(payload)


@pytest.mark.parametrize("field", ["allowed_teaching_days", "available_windows", "blocked_periods", "effective_from", "effective_until"])
def test_no_implicit_staff_calendar_defaults(field):
    _, _, inputs = one()
    payload = inputs.availability[0].model_dump(round_trip=True)
    payload.pop(field)
    with pytest.raises(ValidationError):
        StaffAvailability.model_validate(payload)


@pytest.mark.parametrize("cause,code", [("window", "outside_staff_availability"), ("day", "staff_disallowed_day"),
                                      ("block", "staff_blocked_period"), ("weekly", "weekly_teaching_cap"),
                                      ("daily", "daily_teaching_cap"), ("effective_date", "staff_inactive_policy")])
def test_independent_validator_catches_staff_constraints(cause, code):
    prepared, config, inputs = one()
    result = checked_solve(prepared, config, inputs)
    item = result.schedule[0]
    calendar = inputs.availability[0]
    if cause == "window":
        calendar.available_windows["DAY"] = []
    elif cause == "day":
        calendar.allowed_teaching_days, calendar.available_windows = set(), {}
    elif cause == "block":
        calendar.blocked_periods = [StaffBlockedInterval(day="DAY", start_minute=item.start_minute, end_minute=item.end_minute)]
    elif cause == "effective_date":
        calendar.effective_from = date(2026, 10, 13)
    else:
        setattr(calendar, f"{cause}_teaching_max_minutes", 0)
    with pytest.raises(TimetableValidationError, match=code):
        validate_staff_policy_result(prepared, config, result, staff_input=inputs)


@pytest.mark.parametrize("field,value", [("scheduled_teaching_minutes", 60), ("daily_teaching_minutes", (("DAY", 60),)),
                                         ("administrative_minutes", 960), ("other_recognized_minutes", 960),
                                         ("known_total_work_minutes", 960), ("target_fulfilled", True),
                                         ("target_shortfall_minutes", 0), ("recognized_work_complete", False),
                                         ("attendance_status", AttendanceStatus.MET)])
def test_workload_summary_corruption_detected(field, value):
    prepared, config, inputs = one()
    result = checked_solve(prepared, config, inputs)
    altered = replace(result.workload_report.staff[0], **{field: value})
    corrupt = replace(result, workload_report=replace(result.workload_report, staff=(altered,)))
    with pytest.raises(TimetableValidationError, match="workload_summary_changed"):
        validate_staff_policy_result(prepared, config, corrupt, staff_input=inputs)


def test_missing_report_and_mode_mismatch_cannot_bypass_validation():
    prepared, config, inputs = one()
    result = checked_solve(prepared, config, inputs)
    for corrupt in (replace(result, workload_report=None), replace(result, staff_policy_enabled=False)):
        with pytest.raises(TimetableValidationError):
            validate_staff_policy_result(prepared, config, corrupt, staff_input=inputs)


def test_independent_validator_is_not_using_model_start_domain_builder(monkeypatch):
    from schedence_teaching.solver import engine
    prepared, config, inputs = one()
    inputs.availability[0].available_windows["DAY"] = []
    monkeypatch.setattr(engine, "staff_start_domains", lambda *args: [[0, 2]])
    with pytest.raises(TimetableValidationError, match="outside_staff_availability"):
        solve_timetable(prepared, config, staff_input=inputs)


def test_independent_validator_is_not_using_model_cap_builder(monkeypatch):
    from schedence_teaching.solver import engine
    prepared, config, inputs = one()
    inputs.availability[0].weekly_teaching_max_minutes = 0
    monkeypatch.setattr(engine, "add_teaching_caps", lambda *args: None)
    with pytest.raises(TimetableValidationError, match="weekly_teaching_cap"):
        solve_timetable(prepared, config, staff_input=inputs)


def test_policy_free_mode_keeps_existing_results_and_requires_no_calendar():
    prepared, config, _ = one()
    result = solve_timetable(prepared, config)
    assert result.status is SolverStatus.OPTIMAL and not result.staff_policy_enabled
    assert result.workload_report is None
    assert validate_timetable(prepared, config, result.schedule) == result.validation


def test_user_policy_and_all_original_inputs_remain_unchanged():
    prepared, config, inputs = one()
    before = inputs.model_dump(round_trip=True), prepared.input_snapshot.model_dump(round_trip=True)
    checked_solve(prepared, config, inputs)
    assert (inputs.model_dump(round_trip=True), prepared.input_snapshot.model_dump(round_trip=True)) == before


@pytest.mark.parametrize("band,appointment,count,expected", [("PROFESSOR", None, 3, 360),
                                                           ("LECTURER", "HOD", 2, 240),
                                                           ("LECTURER", "DEAN", 2, 240)])
def test_complete_exempt_workload_across_fewer_than_six_teaching_days(band, appointment, count, expected):
    prepared, config, inputs = one(count=count, duration=120, minutes=120,
                                   days=tuple(f"D{i}" for i in range(count)), band=band, appointment=appointment)
    result = checked_solve(prepared, config, inputs)
    row = result.workload_report.staff[0]
    assert row.scheduled_teaching_minutes == expected and row.target_status is FulfillmentStatus.MET
    assert row.attendance_status is AttendanceStatus.EXEMPT
    assert len(row.teaching_days) == count and result.workload_report.completion_status == "COMPLETE"


def test_regular_lecturer_full_target_without_attendance_evidence_is_not_complete():
    prepared, config, inputs = one()
    inputs.workload_input.recognized_work = [RecognizedWorkRecord(
        work_id="ADMIN", staff_id="STAFF", work_type="ADMINISTRATIVE", duration_minutes=930, evidence_reference="synthetic:admin")]
    result = checked_solve(prepared, config, inputs)
    assert result.workload_report.staff[0].target_status is FulfillmentStatus.MET
    assert result.workload_report.completion_status == "NOT_VERIFIED"


def test_complete_attendance_shortfall_is_reported_but_not_a_teaching_constraint():
    prepared, config, inputs = one()
    inputs.workload_input.attendance = [CampusAttendanceRecord(
        staff_id="STAFF", working_days=["DAY"], complete=True, evidence_reference="synthetic:one-attendance-day")]
    result = checked_solve(prepared, config, inputs)
    assert result.status is SolverStatus.OPTIMAL
    assert result.workload_report.staff[0].attendance_status is AttendanceStatus.BELOW_REQUIREMENT


def test_policy_mode_timeout_carries_no_unverified_complete_workload_report():
    prepared, config, inputs = one()
    config.max_time_seconds = 1e-12
    assert checked_solve(prepared, config, inputs).status is SolverStatus.UNKNOWN


def test_nonoptimal_feasible_policy_result_preserves_status_and_assessment(monkeypatch):
    native = cp_model.CpSolver.solve

    def feasible(self, model, *args, **kwargs):
        assert native(self, model, *args, **kwargs) == cp_model.OPTIMAL
        return cp_model.FEASIBLE

    monkeypatch.setattr(cp_model.CpSolver, "solve", feasible)
    prepared, config, inputs = one()
    result = checked_solve(prepared, config, inputs)
    assert result.status is SolverStatus.FEASIBLE and result.workload_report is not None


@pytest.mark.parametrize("cause", ["duplicate_dates", "more_than_week", "reversed_calendar", "windows_mismatch"])
def test_malformed_dated_availability_contracts(cause):
    _, _, inputs = one(days=("D1", "D2"))
    payload = inputs.model_dump(round_trip=True)
    if cause == "duplicate_dates":
        payload["day_dates"]["D2"] = payload["day_dates"]["D1"]
    elif cause == "more_than_week":
        payload["policy_effective_until"] = date(2026, 10, 31)
        payload["day_dates"]["D2"] = date(2026, 10, 20)
    elif cause == "reversed_calendar":
        payload["availability"][0]["effective_until"] = date(2026, 10, 1)
    else:
        payload["availability"][0]["available_windows"].pop("D2")
    with pytest.raises(ValidationError):
        StaffSchedulingInput.model_validate(payload)


def test_assessment_refuses_missing_or_different_original_policy_context():
    prepared, config, inputs = one()
    result = checked_solve(prepared, config, inputs)
    from schedence_teaching.workload import WorkloadInputError
    with pytest.raises(WorkloadInputError, match="original staff"):
        assess_weekly_workload(prepared, config, result, inputs.workload_input)
    different = inputs.workload_input.model_copy(deep=True)
    different.week_reference = "different:week"
    with pytest.raises(WorkloadInputError, match="original staff"):
        assess_weekly_workload(prepared, config, result, different, staff_input=inputs)


def test_documented_staff_policy_demo_is_valid_and_evidence_aware():
    outputs = runpy.run_path(str(Path(__file__).parents[1] / "examples" / "staff-policy-demo.py"))["run_scenarios"]()
    for name, minutes, completion in [("combined_professor", 360, "COMPLETE"),
                                       ("separate_hod", 480, "COMPLETE"),
                                       ("mixed_assistant_professor", 360, "NOT_VERIFIED"),
                                       ("partial_lecturer_evidence", 240, "INCOMPLETE")]:
        assert outputs[name]["status"] == "OPTIMAL"
        assert outputs[name]["workload_assessment"]["completion_status"] == completion
        assert outputs[name]["workload_assessment"]["staff"][0]["scheduled_teaching_minutes"] == minutes
    assert outputs["infeasible_explicit_cap"]["status"] == "INFEASIBLE"
    assert outputs["infeasible_explicit_cap"]["schedule"] == []
    assert outputs["infeasible_explicit_cap"]["workload_assessment"] is None
