"""User-confirmed policy, synthetic credited work, and separate attendance evidence."""

from dataclasses import replace

import pytest
from pydantic import ValidationError

from schedence_teaching.preparation import prepare_timetable_input
from schedence_teaching.schemas import StaffMember, StaffType, TeachingTimetableInput
from schedence_teaching.solver import SolverStatus, TimetableValidationError, solve_timetable
from schedence_teaching.workload import (
    AttendanceStatus, CampusAttendanceRecord, FulfillmentStatus, RecognizedWorkRecord,
    StaffAppointment, StaffWorkProfile, WeeklyWorkloadInput, WorkloadBand, WorkloadInputError,
    WorkloadRule, WorkloadTargetBasis, assess_weekly_workload, confirmed_campus_policy, resolve_work_policy,
)

from test_combination_solver import inputs as combination_inputs, solve as solve_combination
from test_staff_solver import demand, windows


def profile(band=WorkloadBand.LECTURER, *, staff_id="SYNTHETIC-STAFF", appointment=None):
    return StaffWorkProfile(staff_id=staff_id, workload_band=band, appointment=appointment,
                            evidence_reference="synthetic:explicit-staff-classification")


def member(*, band=WorkloadBand.LECTURER):
    return StaffMember(staff_id="SYNTHETIC-STAFF", staff_name="Synthetic Staff", position="Original academic rank",
                       staff_type="INSTRUCTOR" if band is WorkloadBand.INSTRUCTOR else "ACADEMIC")


def weekly(*, band=WorkloadBand.LECTURER, appointment=None, complete=True, work=(), attendance=None, basis=None):
    # Keep the prior combined-target scenarios as explicit backward-compatibility tests.
    policy = confirmed_campus_policy(target_basis=WorkloadTargetBasis.TOTAL_RECOGNIZED_WORK if basis is None else basis)
    return WeeklyWorkloadInput(
        week_reference="synthetic:one-scheduling-week", policy=policy,
        profiles=[profile(band, appointment=appointment)], recognized_work=work,
        recognized_work_complete_for=["SYNTHETIC-STAFF"] if complete else [],
        attendance=[] if attendance is None else [CampusAttendanceRecord(
            staff_id="SYNTHETIC-STAFF", working_days=attendance, complete=True,
            evidence_reference="synthetic:campus-attendance")])


def credited(minutes, *, work_type="ADMINISTRATIVE", identity="SYNTHETIC-WORK"):
    return RecognizedWorkRecord(work_id=identity, staff_id="SYNTHETIC-STAFF", work_type=work_type,
                                duration_minutes=minutes, evidence_reference="synthetic:recognized-credit")


def scheduled(*, frequency=1, duration=30, days=("DAY",), extra_staff=()):
    prepared = demand({"OFFER": [("SYNTHETIC-STAFF", "LIC")]}, extra_staff=extra_staff)
    payload = prepared.input_snapshot.model_dump(round_trip=True)
    payload["session_requirements"][0].update(duration_minutes=duration, sessions_per_week=frequency)
    prepared = prepare_timetable_input(TeachingTimetableInput.model_validate(payload))
    config = windows(duration if len(days) > 1 else duration * frequency, days=days)
    return prepared, config, solve_timetable(prepared, config)


def assess(workload, **kwargs):
    prepared, config, result = scheduled(**kwargs)
    return assess_weekly_workload(prepared, config, result, workload)


@pytest.mark.parametrize("band,hours,days,exempt", [
    (WorkloadBand.INSTRUCTOR, 20, None, False),
    (WorkloadBand.ASSISTANT_LECTURER, 18, 6, False),
    (WorkloadBand.LECTURER, 16, 6, False),
    (WorkloadBand.SENIOR_LECTURER, 12, 6, False),
    (WorkloadBand.DR, 8, 6, False),
    (WorkloadBand.ASSISTANT_PROFESSOR, 6, 6, False),
    (WorkloadBand.PROFESSOR, 6, None, True),
])
def test_exact_confirmed_weekly_minutes_and_day_rules(band, hours, days, exempt):
    resolved = resolve_work_policy(member(band=band), profile(band), confirmed_campus_policy())
    assert resolved.weekly_target_minutes == hours * 60
    assert resolved.compulsory_campus_days == days and resolved.campus_day_exempt is exempt
    assert resolved.target_basis is WorkloadTargetBasis.TEACHING_CONTACT
    assert resolved.required_teaching_minutes == hours * 60
    assert resolved.administrative_allocation_minutes == (240 if band is WorkloadBand.PROFESSOR else None)


@pytest.mark.parametrize("appointment", list(StaffAppointment))
@pytest.mark.parametrize("band", [WorkloadBand.LECTURER, WorkloadBand.SENIOR_LECTURER, WorkloadBand.PROFESSOR,
                                  WorkloadBand.ASSISTANT_PROFESSOR])
def test_appointments_override_workload_and_days_without_changing_rank(appointment, band):
    staff, supplied = member(), profile(band, appointment=appointment)
    before = staff.model_dump(round_trip=True), supplied.model_dump(round_trip=True)
    resolved = resolve_work_policy(staff, supplied, confirmed_campus_policy())
    assert resolved.weekly_target_minutes == 240 and resolved.campus_day_exempt
    assert resolved.compulsory_campus_days is None and resolved.workload_band is band
    assert resolved.rule_source == "APPOINTMENT_OVERRIDE"
    assert (staff.model_dump(round_trip=True), supplied.model_dump(round_trip=True)) == before


def test_professor_six_hours_across_three_days_fulfills_target_without_six_day_rule():
    row = assess(weekly(band=WorkloadBand.PROFESSOR, attendance=["D1", "D2", "D3"]),
                 frequency=3, duration=120, days=("D1", "D2", "D3")).staff[0]
    assert row.scheduled_teaching_minutes == row.known_total_work_minutes == 360
    assert row.teaching_days == ("D1", "D2", "D3")
    assert row.target_fulfilled and row.target_status is FulfillmentStatus.MET
    assert row.attendance_status is AttendanceStatus.EXEMPT


@pytest.mark.parametrize("appointment", list(StaffAppointment))
def test_hod_or_dean_four_hours_across_two_days(appointment):
    row = assess(weekly(appointment=appointment, attendance=["D1", "D2"]),
                 frequency=2, duration=120, days=("D1", "D2")).staff[0]
    assert row.known_total_work_minutes == 240 and row.target_fulfilled
    assert row.policy.workload_band is WorkloadBand.LECTURER
    assert row.attendance_status is AttendanceStatus.EXEMPT


def test_assistant_professor_does_not_inherit_professor_attendance_exemption():
    row = assess(weekly(band=WorkloadBand.ASSISTANT_PROFESSOR, attendance=["D1", "D2", "D3"]),
                 frequency=3, duration=120, days=("D1", "D2", "D3")).staff[0]
    assert row.target_fulfilled and not row.policy.campus_day_exempt
    assert row.attendance_status is AttendanceStatus.BELOW_REQUIREMENT


def test_six_campus_days_do_not_require_six_teaching_days():
    data = weekly(work=[credited(600)], attendance=["D1", "D2", "D3", "D4", "D5", "D6"])
    row = assess(data, frequency=3, duration=120, days=("D1", "D2", "D3")).staff[0]
    assert row.scheduled_teaching_minutes == 360 and row.administrative_minutes == 600
    assert row.other_recognized_minutes == 0 and row.known_total_work_minutes == 960
    assert row.target_status is FulfillmentStatus.MET and row.target_fulfilled
    assert len(row.teaching_days) == 3 and len(row.evidenced_campus_days) == 6
    assert row.attendance_status is AttendanceStatus.MET


@pytest.mark.parametrize("band,appointment", [(WorkloadBand.PROFESSOR, None), (WorkloadBand.LECTURER, StaffAppointment.HOD),
                                             (WorkloadBand.LECTURER, StaffAppointment.DEAN)])
def test_attendance_exemption_does_not_waive_workload(band, appointment):
    row = assess(weekly(band=band, appointment=appointment, attendance=[])).staff[0]
    assert row.attendance_status is AttendanceStatus.EXEMPT
    assert row.target_status is FulfillmentStatus.BELOW_TARGET and row.target_fulfilled is False
    assert row.target_shortfall_minutes == row.policy.weekly_target_minutes - 30


def test_targets_are_not_maximum_only_and_excess_is_not_infeasibility():
    short = assess(weekly()).staff[0]
    assert short.target_status is FulfillmentStatus.BELOW_TARGET and short.target_shortfall_minutes == 930
    excess = assess(weekly(work=[credited(960)])).staff[0]
    assert excess.target_status is FulfillmentStatus.EXCEEDED and excess.target_fulfilled
    assert excess.target_shortfall_minutes == 0 and excess.known_total_work_minutes == 990


def test_distinct_admin_and_other_recognized_work_do_not_become_teaching():
    data = weekly(work=[credited(600), credited(330, work_type="OTHER_RECOGNIZED", identity="OTHER")])
    row = assess(data).staff[0]
    assert (row.scheduled_teaching_minutes, row.administrative_minutes, row.other_recognized_minutes) == (30, 600, 330)
    assert row.known_total_work_minutes == row.credited_target_minutes == 960
    assert row.target_status is FulfillmentStatus.MET
    assert row.attendance_status is AttendanceStatus.NOT_VERIFIED


def test_missing_recognized_work_is_unverified_not_fabricated_zero_fulfillment():
    report = assess(weekly(complete=False))
    row = report.staff[0]
    assert row.known_total_work_minutes == 30 and not row.recognized_work_complete
    assert row.target_status is FulfillmentStatus.NOT_VERIFIED and row.target_fulfilled is None
    assert row.target_shortfall_minutes is None and report.all_workload_targets_fulfilled is None


def test_enough_explicit_credit_can_prove_minimum_target_even_if_ledger_is_partial():
    row = assess(weekly(complete=False, work=[credited(930)])).staff[0]
    assert row.target_fulfilled and row.target_status is FulfillmentStatus.MET
    assert not row.recognized_work_complete


def test_configurable_teaching_only_basis_does_not_count_admin_towards_target():
    row = assess(weekly(work=[credited(930)], basis=WorkloadTargetBasis.TEACHING_CONTACT)).staff[0]
    assert row.known_total_work_minutes == 960 and row.credited_target_minutes == 30
    assert row.target_fulfilled is False and row.target_shortfall_minutes == 930


def test_teaching_days_never_supply_implicit_campus_attendance():
    row = assess(weekly(work=[credited(780)]), frequency=6, duration=30,
                 days=("D1", "D2", "D3", "D4", "D5", "D6")).staff[0]
    assert len(row.teaching_days) == 6 and row.evidenced_campus_days == ()
    assert row.attendance_status is AttendanceStatus.NOT_VERIFIED


def test_explicit_empty_attendance_is_shortfall_for_regular_staff():
    row = assess(weekly(attendance=[])).staff[0]
    assert row.attendance_status is AttendanceStatus.BELOW_REQUIREMENT
    assert row.campus_attendance_complete


@pytest.mark.parametrize("count,expected", [(2, AttendanceStatus.NOT_VERIFIED), (6, AttendanceStatus.MET)])
def test_partial_attendance_evidence(count, expected):
    data = weekly(attendance=[f"D{index}" for index in range(count)])
    data.attendance[0].complete = False
    assert assess(data).staff[0].attendance_status is expected


def test_policy_values_are_configurable_without_mutating_preset():
    data = weekly()
    data.policy.band_rules[WorkloadBand.LECTURER] = WorkloadRule(weekly_target_minutes=30, compulsory_campus_days=5)
    row = assess(data).staff[0]
    assert row.target_fulfilled and row.policy.compulsory_campus_days == 5
    assert confirmed_campus_policy().band_rules[WorkloadBand.LECTURER].weekly_target_minutes == 960


def test_instructor_day_requirement_is_unspecified_until_supplied():
    prepared = demand({"OFFER": [("SYNTHETIC-STAFF", "TEAM_MEMBER")]}, staff_types={"SYNTHETIC-STAFF": "INSTRUCTOR"})
    config = windows()
    result = solve_timetable(prepared, config)
    row = assess_weekly_workload(prepared, config, result, weekly(band=WorkloadBand.INSTRUCTOR)).staff[0]
    assert row.policy.weekly_target_minutes == 1200
    assert row.attendance_status is AttendanceStatus.UNSPECIFIED and not row.policy.campus_day_exempt


def test_combined_teaching_minutes_are_counted_once_not_per_group():
    combinations, config, result = solve_combination(combination_inputs())
    data = WeeklyWorkloadInput(week_reference="synthetic:week", policy=confirmed_campus_policy(),
                               profiles=[profile(WorkloadBand.PROFESSOR, staff_id="STAFF")],
                               recognized_work_complete_for=["STAFF"])
    row = assess_weekly_workload(combinations.prepared, config, result, data,
                                 combination_input=combinations).staff[0]
    assert row.scheduled_teaching_minutes == row.known_total_work_minutes == 60
    assert row.target_shortfall_minutes == 300


def test_profiles_missing_for_unused_staff_are_reported_not_guessed():
    report = assess(weekly(work=[credited(930)]), extra_staff=["UNCLASSIFIED"])
    assert report.unassessed_staff_ids == ("UNCLASSIFIED",)
    assert report.all_workload_targets_fulfilled is None
    assert not report.to_dict()["solver_enforced"] and not report.to_dict()["university_ready"]


@pytest.mark.parametrize("band,staff_type", [(WorkloadBand.INSTRUCTOR, "ACADEMIC"), (WorkloadBand.LECTURER, "INSTRUCTOR")])
def test_type_mismatch_preserves_academic_and_instructor_distinction(band, staff_type):
    staff = member()
    staff.staff_type = StaffType(staff_type)
    with pytest.raises(WorkloadInputError, match="distinction"):
        resolve_work_policy(staff, profile(band), confirmed_campus_policy())


def test_hod_and_dean_cannot_be_used_as_academic_workload_bands():
    for appointment in ("HOD", "DEAN"):
        with pytest.raises(ValidationError):
            StaffWorkProfile(staff_id="SYNTHETIC-STAFF", workload_band=appointment, evidence_reference="synthetic")


@pytest.mark.parametrize("appointment", [None, StaffAppointment.HOD])
def test_missing_policy_rule_fails_instead_of_silent_fallback(appointment):
    policy = confirmed_campus_policy()
    if appointment:
        policy.appointment_overrides.pop(appointment)
    else:
        policy.band_rules.pop(WorkloadBand.LECTURER)
    with pytest.raises(WorkloadInputError, match="No explicit rule"):
        resolve_work_policy(member(), profile(appointment=appointment), policy)


@pytest.mark.parametrize("field", ["profile", "work", "attendance", "completeness"])
def test_unknown_staff_references_rejected(field):
    data = weekly()
    if field == "profile":
        data.profiles[0].staff_id = "UNKNOWN"
    elif field == "work":
        data.recognized_work = [credited(30)]
        data.recognized_work[0].staff_id = "UNKNOWN"
    elif field == "attendance":
        data.attendance = [CampusAttendanceRecord(staff_id="UNKNOWN", working_days=[], complete=True, evidence_reference="synthetic")]
    else:
        data.recognized_work_complete_for.add("UNKNOWN")
    with pytest.raises(WorkloadInputError, match="unknown staff"):
        assess(data)


@pytest.mark.parametrize("field", ["profiles", "recognized_work", "attendance"])
def test_duplicate_records_rejected_before_double_counting(field):
    data = weekly(work=[credited(30)], attendance=["DAY"]).model_dump(round_trip=True)
    data[field].append(data[field][0])
    with pytest.raises(ValidationError, match="duplicate"):
        WeeklyWorkloadInput.model_validate(data)


@pytest.mark.parametrize("minutes", [0, -1, True, 1.5, "60"])
def test_invalid_recognized_minutes_rejected(minutes):
    with pytest.raises(ValidationError):
        credited(minutes)


@pytest.mark.parametrize("minutes", [0, -1, True, 1.5, "60"])
def test_invalid_target_minutes_rejected(minutes):
    with pytest.raises(ValidationError):
        WorkloadRule(weekly_target_minutes=minutes)


@pytest.mark.parametrize("days", [0, 8, True, 1.5, "6"])
def test_invalid_compulsory_day_counts_rejected(days):
    with pytest.raises(ValidationError):
        WorkloadRule(weekly_target_minutes=360, compulsory_campus_days=days)


def test_exemption_and_compulsory_days_are_mutually_exclusive():
    with pytest.raises(ValidationError, match="exemption"):
        WorkloadRule(weekly_target_minutes=360, compulsory_campus_days=6, campus_day_exempt=True)


@pytest.mark.parametrize("days", [["D1", "D1"], [" "], [f"D{i}" for i in range(8)]])
def test_invalid_attendance_records_rejected(days):
    with pytest.raises(ValidationError):
        CampusAttendanceRecord(staff_id="SYNTHETIC-STAFF", working_days=days, complete=True, evidence_reference="synthetic")


@pytest.mark.parametrize("status", [SolverStatus.INFEASIBLE, SolverStatus.UNKNOWN, SolverStatus.MODEL_INVALID])
def test_failed_result_cannot_be_presented_as_fulfilled_workload(status):
    prepared, config, result = scheduled()
    with pytest.raises(WorkloadInputError, match="complete successful"):
        assess_weekly_workload(prepared, config, replace(result, status=status, schedule=(), validation=None), weekly())


def test_corrupted_schedule_is_revalidated_before_crediting_teaching():
    prepared, config, result = scheduled()
    corrupt = replace(result, schedule=())
    with pytest.raises(TimetableValidationError, match="missing_occurrence"):
        assess_weekly_workload(prepared, config, corrupt, weekly())


def test_combination_result_requires_original_evidence_inputs():
    combinations, config, result = solve_combination()
    with pytest.raises(WorkloadInputError, match="Combination results"):
        assess_weekly_workload(combinations.prepared, config, result, weekly())


def test_assessment_preserves_staff_policy_evidence_and_solver_output():
    data = weekly(work=[credited(930)], attendance=[f"D{i}" for i in range(6)])
    prepared, config, result = scheduled()
    before = data.model_dump(round_trip=True), prepared.input_snapshot.model_dump(round_trip=True), result.to_dict()
    assess_weekly_workload(prepared, config, result, data)
    assert (data.model_dump(round_trip=True), prepared.input_snapshot.model_dump(round_trip=True), result.to_dict()) == before
