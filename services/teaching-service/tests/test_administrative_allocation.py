"""Separate confirmed teaching requirements, weekly planning, and completion evidence."""

from dataclasses import replace

import pytest
from pydantic import ValidationError

from schedence_teaching.solver import SolverStatus, TimetableValidationError, solve_timetable, validate_staff_policy_result
from schedence_teaching.workload import (
    AdministrativeReservation, AdministrativeReservationStatus, FulfillmentStatus,
    RecognizedWorkRecord, StaffAppointment, WeeklyWorkloadInput, WorkloadBand,
    WorkloadInputError, WorkloadPolicy, WorkloadRule, WorkloadTargetBasis,
    assess_weekly_workload, confirmed_campus_policy, resolve_work_policy,
)

from test_workload_policy import member, profile, scheduled
from test_staff_policy_solver import one


def separate_input(*, band=WorkloadBand.PROFESSOR, appointment=None, admin=0, other=0, complete=True, reservations=()):
    return WeeklyWorkloadInput(
        week_reference="synthetic:weekly-separate-requirements", policy=confirmed_campus_policy(),
        profiles=[profile(band, appointment=appointment)],
        recognized_work=[RecognizedWorkRecord(work_id=f"WORK-{kind}", staff_id="SYNTHETIC-STAFF", work_type=kind,
                                             duration_minutes=minutes, evidence_reference="synthetic:completed-work")
                         for kind, minutes in [("ADMINISTRATIVE", admin), ("OTHER_RECOGNIZED", other)] if minutes],
        recognized_work_complete_for=["SYNTHETIC-STAFF"] if complete else [],
        administrative_reservations=reservations)


def report(inputs=None, *, frequency=3, duration=120):
    prepared, config, result = scheduled(frequency=frequency, duration=duration)
    return assess_weekly_workload(prepared, config, result, inputs or separate_input())


def reservation(*, identity="RESERVATION", staff="SYNTHETIC-STAFF", minutes=240, approved=False):
    return AdministrativeReservation(reservation_id=identity, staff_id=staff, duration_minutes=minutes,
                                      status="APPROVED" if approved else "PROPOSED",
                                      evidence_reference="synthetic:weekly-planning",
                                      approval_reference="synthetic:approval" if approved else None)


@pytest.mark.parametrize("band,appointment,teaching", [(WorkloadBand.PROFESSOR, None, 360),
                                                     (WorkloadBand.LECTURER, StaffAppointment.HOD, 240),
                                                     (WorkloadBand.LECTURER, StaffAppointment.DEAN, 240)])
def test_confirmed_teaching_and_admin_requirements_are_separate(band, appointment, teaching):
    resolved = resolve_work_policy(member(), profile(band, appointment=appointment), confirmed_campus_policy())
    assert resolved.required_teaching_minutes == resolved.weekly_target_minutes == teaching
    assert resolved.administrative_allocation_minutes == 240
    assert resolved.target_basis is WorkloadTargetBasis.TEACHING_CONTACT
    assert resolved.campus_day_exempt


@pytest.mark.parametrize("band", [band for band in WorkloadBand if band is not WorkloadBand.PROFESSOR])
def test_lower_classifications_do_not_receive_automatic_professor_admin_allocation(band):
    resolved = resolve_work_policy(member(band=band), profile(band), confirmed_campus_policy())
    assert resolved.administrative_allocation_minutes is None


@pytest.mark.parametrize("appointment", list(StaffAppointment))
def test_professor_appointment_allocation_is_not_stacked(appointment):
    row = report(separate_input(appointment=appointment), frequency=2).staff[0]
    assert row.policy.workload_band is WorkloadBand.PROFESSOR
    assert row.policy.appointment == appointment.value
    assert row.policy.required_teaching_minutes == 240 and row.administration.allocation_minutes == 240


def test_assistant_professor_hod_receives_explicit_appointment_override_only():
    row = report(separate_input(band=WorkloadBand.ASSISTANT_PROFESSOR, appointment=StaffAppointment.HOD), frequency=2).staff[0]
    assert row.policy.rule_source == "APPOINTMENT_OVERRIDE"
    assert row.policy.workload_band is WorkloadBand.ASSISTANT_PROFESSOR
    assert row.policy.administrative_allocation_minutes == 240


def test_known_professor_inconsistency_four_teaching_plus_two_admin_does_not_fulfill_six_teaching():
    output = report(separate_input(admin=120), frequency=2)
    row = output.staff[0]
    assert row.scheduled_teaching_minutes == 240 and row.known_total_work_minutes == 360
    assert row.credited_target_minutes == 240 and row.target_status is FulfillmentStatus.BELOW_TARGET
    assert row.target_shortfall_minutes == 120 and row.target_fulfilled is False
    assert row.administration.completion_shortfall_minutes == 120 and row.administration.fulfilled is False
    assert output.completion_status == "INCOMPLETE"


@pytest.mark.parametrize("appointment", list(StaffAppointment))
def test_hod_dean_admin_work_cannot_substitute_for_four_teaching_hours(appointment):
    row = report(separate_input(band=WorkloadBand.LECTURER, appointment=appointment, admin=240), frequency=1).staff[0]
    assert row.known_total_work_minutes == 360 and row.target_shortfall_minutes == 120
    assert row.target_fulfilled is False and row.administration.fulfilled is True


def test_other_recognized_work_does_not_replace_teaching_or_administrative_completion():
    row = report(separate_input(other=600), frequency=1).staff[0]
    assert row.known_total_work_minutes == 720
    assert row.target_shortfall_minutes == 240
    assert row.administration.verified_completed_minutes == 0 and row.administration.completion_shortfall_minutes == 240


def test_six_teaching_plus_four_completed_admin_hours_can_be_complete():
    output = report(separate_input(admin=240))
    row = output.staff[0]
    assert row.scheduled_teaching_minutes == 360 and row.known_total_work_minutes == 600
    assert row.target_fulfilled and row.administration.fulfilled
    assert output.all_workload_targets_fulfilled and output.all_administrative_allocations_fulfilled
    assert output.completion_status == "COMPLETE"


@pytest.mark.parametrize("appointment", list(StaffAppointment))
def test_hod_dean_four_teaching_plus_four_completed_admin_hours(appointment):
    output = report(separate_input(band=WorkloadBand.LECTURER, appointment=appointment, admin=240), frequency=2)
    row = output.staff[0]
    assert row.known_total_work_minutes == 480 and output.completion_status == "COMPLETE"


def test_teaching_target_met_does_not_mean_admin_work_completed():
    output = report()
    row = output.staff[0]
    assert row.target_fulfilled and row.administration.fulfilled is False
    assert row.administration.allocation_minutes == 240 and row.administration.verified_completed_minutes == 0
    assert output.all_administrative_allocations_fulfilled is False and output.completion_status == "INCOMPLETE"


def test_allocated_admin_with_missing_ledger_is_not_completion_evidence():
    output = report(separate_input(complete=False))
    row = output.staff[0]
    assert row.administration.allocation_minutes == 240 and row.administration.completion_status == "NOT_VERIFIED"
    assert row.administration.fulfilled is None and row.administration.completion_shortfall_minutes is None
    assert "administrative_work_completion" in row.missing_evidence
    assert output.all_administrative_allocations_fulfilled is None and output.completion_status == "NOT_VERIFIED"


@pytest.mark.parametrize("approved", [False, True])
def test_reserved_admin_minutes_never_count_as_work_completed(approved):
    output = report(separate_input(reservations=[reservation(approved=approved)]))
    admin = output.staff[0].administration
    assert admin.verified_completed_minutes == 0 and admin.completion_shortfall_minutes == 240
    assert admin.approved_reservation_minutes == (240 if approved else 0)
    assert admin.proposed_reservation_minutes == (0 if approved else 240)
    assert admin.unreserved_allocation_minutes == (0 if approved else 240)
    assert not admin.to_dict()["hard_blocks_generated"] and not admin.to_dict()["calendar_slots_assigned"]
    assert output.completion_status == "INCOMPLETE"


@pytest.mark.parametrize("minutes,status,remaining", [(60, "PARTIALLY_RESERVED", 180),
                                                     (240, "FULLY_RESERVED", 0), (300, "OVER_RESERVED", 0)])
def test_reservation_totals_are_planning_metadata(minutes, status, remaining):
    admin = report(separate_input(reservations=[reservation(minutes=minutes, approved=True)])).staff[0].administration
    assert admin.reservation_status == status and admin.unreserved_allocation_minutes == remaining
    assert admin.verified_completed_minutes == 0


def test_multiple_planning_records_keep_pending_and_approved_totals_separate():
    records = [reservation(identity="PROPOSED", minutes=120), reservation(identity="APPROVED-1", minutes=60, approved=True),
               reservation(identity="APPROVED-2", minutes=120, approved=True)]
    admin = report(separate_input(reservations=records)).staff[0].administration
    assert admin.proposed_reservation_minutes == 120 and admin.approved_reservation_minutes == 180
    assert admin.unreserved_allocation_minutes == 60


def test_administrative_allocation_is_configurable():
    inputs = separate_input(admin=180)
    inputs.policy = confirmed_campus_policy(administrative_allocation_minutes=180)
    row = report(inputs).staff[0]
    assert row.policy.required_teaching_minutes == 360 and row.administration.allocation_minutes == 180
    assert row.administration.fulfilled
    assert confirmed_campus_policy().band_rules[WorkloadBand.PROFESSOR].administrative_allocation_minutes == 240


@pytest.mark.parametrize("value", [-1, True, 1.5, "240"])
def test_invalid_policy_allocation_rejected(value):
    with pytest.raises(ValidationError):
        confirmed_campus_policy(administrative_allocation_minutes=value)


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "240"])
def test_invalid_reservation_duration_rejected(value):
    with pytest.raises(ValidationError):
        reservation(minutes=value)


def test_approved_reservation_requires_explicit_approval_reference():
    payload = reservation(approved=True).model_dump(round_trip=True)
    payload["approval_reference"] = None
    with pytest.raises(ValidationError, match="approval reference"):
        AdministrativeReservation.model_validate(payload)


def test_proposed_reservation_cannot_claim_approval():
    payload = reservation().model_dump(round_trip=True)
    payload["approval_reference"] = "unapproved"
    with pytest.raises(ValidationError):
        AdministrativeReservation.model_validate(payload)


@pytest.mark.parametrize("field,value", [("day", "DAY"), ("start_minute", 480), ("end_minute", 720)])
def test_foundation_does_not_accept_or_invent_calendar_booking_fields(field, value):
    payload = reservation().model_dump(round_trip=True)
    payload[field] = value
    with pytest.raises(ValidationError):
        AdministrativeReservation.model_validate(payload)


def test_duplicate_planning_records_rejected_before_double_counting():
    payload = separate_input(reservations=[reservation()]).model_dump(round_trip=True)
    payload["administrative_reservations"].append(payload["administrative_reservations"][0])
    with pytest.raises(ValidationError, match="duplicate"):
        WeeklyWorkloadInput.model_validate(payload)


def test_unknown_staff_reservation_rejected():
    with pytest.raises(WorkloadInputError, match="unknown staff"):
        report(separate_input(reservations=[reservation(staff="UNKNOWN")]))


def test_lower_rank_reservation_requires_explicit_allocation_policy():
    with pytest.raises(WorkloadInputError, match="explicit applicable allocation"):
        report(separate_input(band=WorkloadBand.ASSISTANT_PROFESSOR, reservations=[reservation()]))


def test_old_serialized_combined_policy_and_input_still_validate_and_keep_old_semantics():
    policy = confirmed_campus_policy(target_basis=WorkloadTargetBasis.TOTAL_RECOGNIZED_WORK).model_dump(round_trip=True)
    for mapping in (policy["band_rules"], policy["appointment_overrides"]):
        for rule in mapping.values():
            rule.pop("administrative_allocation_minutes")
    inputs = separate_input(admin=120).model_dump(round_trip=True)
    inputs["policy"] = policy
    inputs.pop("administrative_reservations")
    validated = WeeklyWorkloadInput.model_validate(inputs)
    row = report(validated, frequency=2).staff[0]
    assert row.target_fulfilled and row.credited_target_minutes == 360
    assert row.policy.required_teaching_minutes is None and row.policy.administrative_allocation_minutes is None
    assert row.to_dict()["legacy_combined_target_policy"]


def test_explicit_legacy_preset_does_not_silently_ignore_custom_allocation():
    with pytest.raises(ValueError, match="legacy combined preset"):
        confirmed_campus_policy(target_basis=WorkloadTargetBasis.TOTAL_RECOGNIZED_WORK, administrative_allocation_minutes=180)


@pytest.mark.parametrize("approved", [False, True])
def test_reservations_do_not_modify_cp_sat_times_or_create_staff_blocks(approved):
    prepared, config, staff = one()
    staff.workload_input.policy = confirmed_campus_policy()
    staff.workload_input.profiles[0].workload_band = WorkloadBand.PROFESSOR
    before = solve_timetable(prepared, config, staff_input=staff)
    staff.workload_input.administrative_reservations = [reservation(staff="STAFF", approved=approved)]
    after = solve_timetable(prepared, config, staff_input=staff)
    assert before.status is after.status is SolverStatus.OPTIMAL
    assert before.schedule == after.schedule
    assert after.workload_report.staff[0].administration.verified_completed_minutes == 0


def test_independent_validator_rejects_admin_completion_claimed_from_reservation():
    prepared, config, staff = one()
    staff.workload_input.policy = confirmed_campus_policy()
    staff.workload_input.profiles[0].workload_band = WorkloadBand.PROFESSOR
    result = solve_timetable(prepared, config, staff_input=staff)
    row = result.workload_report.staff[0]
    forged = replace(row.administration, verified_completed_minutes=240, fulfilled=True, completion_shortfall_minutes=0)
    corrupt = replace(result, workload_report=replace(result.workload_report, staff=(replace(row, administration=forged),)))
    with pytest.raises(TimetableValidationError, match="workload_summary_changed"):
        validate_staff_policy_result(prepared, config, corrupt, staff_input=staff)


def test_source_profiles_policies_evidence_and_reservations_are_preserved():
    inputs = separate_input(reservations=[reservation()], admin=120)
    before = inputs.model_dump(round_trip=True)
    report(inputs)
    assert inputs.model_dump(round_trip=True) == before
