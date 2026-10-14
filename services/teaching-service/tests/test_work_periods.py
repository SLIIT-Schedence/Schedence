"""Synthetic administrative/research preparation, with no rank-derived calendar policy."""

from datetime import date
from contextlib import contextmanager
from pathlib import Path
import runpy

import pytest
from pydantic import ValidationError

from schedence_teaching.preparation import PreparationError
from schedence_teaching.solver import solve_timetable
from schedence_teaching.workload import (
    AdministrativeAggregateStatus, AdministrativePolicyResolution, AdministrativeTimeReservation,
    AppointmentApplicability, FlexibleAdministrativePolicy, FlexibleAdministrativeRequest,
    PeriodApprovalStatus, PeriodProtection, RecognizedWorkRecord, ResearchDayPolicy,
    ResearchDayReservation, StaffWorkProfile, WeeklyWorkloadInput, WorkloadBand,
    WorkPeriodInput, confirmed_campus_policy, prepare_work_periods, resolve_work_policy,
)

from test_staff_solver import demand, windows


FIRST, LAST = date(2026, 10, 19), date(2026, 10, 25)
PROVENANCE = {"source": "synthetic-fixture", "reference": "synthetic:source-evidence", "source_record_id": "SOURCE-RECORD"}


def context(*, band="PROFESSOR", appointment=None):
    teaching = demand({"OFFER": [("STAFF", "LIC")]}).input_snapshot
    workload = WeeklyWorkloadInput(
        week_reference="SYNTHETIC-WEEK", policy=confirmed_campus_policy(),
        profiles=[StaffWorkProfile(staff_id="STAFF", workload_band=band, appointment=appointment,
                                   evidence_reference="synthetic:classification")])
    periods = WorkPeriodInput(week_reference="SYNTHETIC-WEEK", week_start=FIRST, week_end=LAST)
    return teaching, workload, periods


def authorization(policy, *, status="APPROVED", **updates):
    return {"staff_id": "STAFF", "policy_reference": policy, "status": status,
            "approval_reference": "synthetic:approval" if status == "APPROVED" else None,
            "provenance": PROVENANCE, "effective_from": FIRST, "effective_until": LAST, **updates}


def admin(workload, *, identity="ADMIN", **updates):
    payload = {**authorization(workload.policy.policy_reference), "reservation_id": identity,
               "on_date": FIRST, "window": {"start_minute": 540, "end_minute": 780},
               "planned_duration_minutes": 240, "protection": "HARD", **updates}
    return AdministrativeTimeReservation.model_validate(payload)


def research_policy(**updates):
    return ResearchDayPolicy.model_validate({**authorization("synthetic:research"), "allow_full_day": True,
                                             "allow_partial_day": True, "allow_hard_protection": True, **updates})


def research(*, identity="RESEARCH", **updates):
    return ResearchDayReservation.model_validate({**authorization("synthetic:research"), "reservation_id": identity,
        "on_date": date(2026, 10, 23), "full_day": True, "protection": "HARD", **updates})


def flexible_policy(workload, **updates):
    return FlexibleAdministrativePolicy.model_validate({**authorization("synthetic:flexible"),
        "administrative_policy_reference": workload.policy.policy_reference,
        "flexible_scheduling_permitted": True, "allow_hard_protection": True, **updates})


def flexible_request(**updates):
    return FlexibleAdministrativeRequest.model_validate({**authorization("synthetic:flexible"), "request_id": "FLEX",
        "planned_duration_minutes": 240, "protection": "HARD", "allow_split": False,
        "eligible_windows": [{"on_date": FIRST, "start_minute": 540, "end_minute": 780}], **updates})


def appointment(*, identity="APPT-HOD", **updates):
    return AppointmentApplicability.model_validate({"appointment_id": identity, "staff_id": "STAFF",
        "position_code": "HEAD_OF_DEPARTMENT", "policy_reference": "ads:hod-policy",
        "effective_from": FIRST, "effective_until": LAST, "policy_effective_from": FIRST,
        "policy_effective_until": LAST, "provenance": PROVENANCE, **updates})


def codes(result):
    return {row.code for row in result.diagnostics}


def prepare(teaching, workload, periods):
    return prepare_work_periods(teaching, workload, periods)


@contextmanager
def raises_issue(code):
    with pytest.raises(PreparationError) as caught:
        yield
    assert code in {row.code for row in caught.value.issues}


@pytest.mark.parametrize("band,appointment_name,teaching_minutes", [("PROFESSOR", None, 360),
    ("LECTURER", "HOD", 240), ("LECTURER", "DEAN", 240)])
def test_existing_confirmed_separate_requirements_and_attendance_exemptions_preserved(band, appointment_name, teaching_minutes):
    teaching, workload, periods = context(band=band, appointment=appointment_name)
    resolved = resolve_work_policy(teaching.staff_members[0], workload.profiles[0], workload.policy)
    assert resolved.required_teaching_minutes == teaching_minutes and resolved.administrative_allocation_minutes == 240
    assert resolved.campus_day_exempt and resolved.workload_band.value == band
    assert prepare(teaching, workload, periods).protected_intervals == ()


def test_approved_admin_period_normalizes_exact_supplied_values_and_provenance():
    teaching, workload, periods = context()
    periods.administrative_periods = [admin(workload)]
    result = prepare(teaching, workload, periods)
    interval = result.protected_intervals[0]
    assert interval.source_id == "ADMIN" and interval.on_date == FIRST
    assert (interval.start_minute, interval.end_minute) == (540, 780)
    assert interval.policy_reference == workload.policy.policy_reference
    assert interval.approval_reference == "synthetic:approval"
    assert interval.provenance.source_record_id == "SOURCE-RECORD"
    assert result.to_dict()["work_minutes_credited"] == 0
    assert not result.to_dict()["attendance_inferred"] and not result.to_dict()["solver_enforced"]


@pytest.mark.parametrize("status,category", [("PENDING", "PENDING"), ("PROPOSED", "PENDING"),
                                          ("CANCELLED", "IGNORED"), ("EXPIRED", "IGNORED")])
def test_nonapproved_reservations_never_produce_hard_protection(status, category):
    teaching, workload, periods = context()
    periods.administrative_periods = [admin(workload, status=status, approval_reference=None)]
    result = prepare(teaching, workload, periods)
    assert not result.protected_intervals and result.diagnostics[0].category == category


def test_cancelled_record_can_preserve_historical_approval_reference():
    teaching, workload, periods = context()
    periods.administrative_periods = [admin(workload, status="CANCELLED")]
    result = prepare(teaching, workload, periods)
    assert result.input_snapshot.administrative_periods[0].approval_reference == "synthetic:approval"
    assert not result.protected_intervals


def test_approved_but_effectively_expired_period_is_ignored_without_changing_source_status():
    teaching, workload, periods = context()
    periods.administrative_periods = [admin(workload, on_date=date(2026, 10, 12),
        effective_from=date(2026, 10, 12), effective_until=date(2026, 10, 18))]
    result = prepare(teaching, workload, periods)
    assert not result.protected_intervals and "outside_effective_week" in codes(result)
    assert result.input_snapshot.administrative_periods[0].status is PeriodApprovalStatus.APPROVED


def test_recurring_weekday_uses_supplied_week_dates_and_stable_ids():
    teaching, workload, periods = context()
    periods.administrative_periods = [admin(workload, on_date=None, weekday="WEDNESDAY")]
    first, second = prepare(teaching, workload, periods), prepare(teaching, workload, periods)
    assert first.protected_intervals == second.protected_intervals
    assert first.protected_intervals[0].on_date == date(2026, 10, 21)


def test_other_academic_week_is_not_silently_applied():
    teaching, workload, periods = context()
    periods.administrative_periods = [admin(workload, week_reference="OTHER-WEEK")]
    assert "different_academic_week" in codes(prepare(teaching, workload, periods))
    periods.week_reference = "MISMATCH"
    with raises_issue("week_reference_mismatch"):
        prepare(teaching, workload, periods)


def test_approved_soft_admin_period_is_a_preference_only():
    teaching, workload, periods = context()
    periods.administrative_periods = [admin(workload, protection="SOFT")]
    result = prepare(teaching, workload, periods)
    assert not result.protected_intervals and result.soft_preferences[0].protection is PeriodProtection.SOFT


def test_full_day_research_protects_calendar_date_without_claiming_1440_work_minutes():
    teaching, workload, periods = context()
    periods.research_policies, periods.research_reservations = [research_policy()], [research()]
    result = prepare(teaching, workload, periods)
    interval = result.protected_intervals[0]
    assert interval.full_day and interval.kind == "RESEARCH"
    assert (interval.start_minute, interval.end_minute) == (0, 1440)
    assert interval.to_dict()["work_minutes_credited"] == 0


def test_partial_day_research_preserves_explicit_windows_only():
    teaching, workload, periods = context()
    periods.research_policies = [research_policy()]
    periods.research_reservations = [research(full_day=False, windows=[
        {"start_minute": 540, "end_minute": 600}, {"start_minute": 660, "end_minute": 720}])]
    result = prepare(teaching, workload, periods)
    assert [(row.start_minute, row.end_minute) for row in result.protected_intervals] == [(540, 600), (660, 720)]


def test_soft_research_never_becomes_hard_protection():
    teaching, workload, periods = context()
    periods.research_policies, periods.research_reservations = [research_policy()], [research(protection="SOFT")]
    result = prepare(teaching, workload, periods)
    assert not result.protected_intervals and len(result.soft_preferences) == 1


@pytest.mark.parametrize("band", ["PROFESSOR", "ASSISTANT_PROFESSOR", "LECTURER"])
def test_no_research_entitlement_is_inferred_from_rank(band):
    teaching, workload, periods = context(band=band)
    periods.research_reservations = [research()]
    result = prepare(teaching, workload, periods)
    assert not result.protected_intervals and "unverified_research_policy" in codes(result)


@pytest.mark.parametrize("cause", ["policy_pending", "policy_expired", "record_pending"])
def test_research_needs_both_applicable_approved_policy_and_reservation(cause):
    teaching, workload, periods = context()
    policy, record = research_policy(), research()
    if cause == "policy_pending":
        policy.status, policy.approval_reference = PeriodApprovalStatus.PENDING, None
    elif cause == "policy_expired":
        policy.effective_from, policy.effective_until = date(2026, 10, 1), date(2026, 10, 18)
    else:
        record.status, record.approval_reference = PeriodApprovalStatus.PENDING, None
    periods.research_policies, periods.research_reservations = [policy], [record]
    assert not prepare(teaching, workload, periods).protected_intervals


@pytest.mark.parametrize("permission", ["allow_full_day", "allow_hard_protection"])
def test_research_protection_must_be_permitted(permission):
    teaching, workload, periods = context()
    periods.research_policies, periods.research_reservations = [research_policy(**{permission: False})], [research()]
    with raises_issue("research_permission_mismatch"):
        prepare(teaching, workload, periods)


def test_research_weekday_permission_is_explicit():
    teaching, workload, periods = context()
    periods.research_policies, periods.research_reservations = [research_policy(allowed_weekdays=["MONDAY"])], [research()]
    with raises_issue("research_weekday_not_permitted"):
        prepare(teaching, workload, periods)


def test_overlapping_approved_hard_admin_and_research_raise_clear_conflict():
    teaching, workload, periods = context()
    periods.administrative_periods = [admin(workload)]
    periods.research_policies, periods.research_reservations = [research_policy()], [research(on_date=FIRST)]
    with raises_issue("conflicting_protected_periods"):
        prepare(teaching, workload, periods)


def test_overlapping_soft_preference_is_reported_without_overriding_hard():
    teaching, workload, periods = context()
    periods.administrative_periods = [admin(workload)]
    periods.research_policies, periods.research_reservations = [research_policy()], [research(on_date=FIRST, protection="SOFT")]
    result = prepare(teaching, workload, periods)
    assert len(result.protected_intervals) == len(result.soft_preferences) == 1
    assert "soft_preference_overlap" in codes(result)


def test_pending_overlap_is_diagnostic_not_a_hard_conflict():
    teaching, workload, periods = context()
    periods.administrative_periods = [admin(workload), admin(workload, identity="PENDING", status="PENDING", approval_reference=None)]
    result = prepare(teaching, workload, periods)
    assert len(result.protected_intervals) == 1 and "pending_reservation_conflict" in codes(result)


def test_adjacent_protection_intervals_are_legal_and_keep_each_source_id():
    teaching, workload, periods = context()
    periods.administrative_periods = [admin(workload, identity="FIRST", window={"start_minute": 540, "end_minute": 600}, planned_duration_minutes=60),
        admin(workload, identity="SECOND", window={"start_minute": 600, "end_minute": 660}, planned_duration_minutes=60)]
    assert [row.source_id for row in prepare(teaching, workload, periods).protected_intervals] == ["FIRST", "SECOND"]


def test_hod_period_requires_dated_appointment_applicability():
    teaching, workload, periods = context(band="LECTURER", appointment="HOD")
    periods.administrative_periods = [admin(workload)]
    assert "missing_appointment_applicability" in codes(prepare(teaching, workload, periods))
    periods.appointments = [appointment()]
    periods.administrative_periods = [admin(workload, appointment_id="APPT-HOD", policy_reference="ads:hod-policy")]
    result = prepare(teaching, workload, periods)
    assert result.protected_intervals[0].appointment_id == "APPT-HOD"
    assert workload.profiles[0].workload_band is WorkloadBand.LECTURER


@pytest.mark.parametrize("cause", ["appointment_expired", "policy_expired"])
def test_appointment_and_policy_date_applicability(cause):
    teaching, workload, periods = context(band="LECTURER", appointment="HOD")
    snapshot = appointment()
    if cause == "appointment_expired":
        snapshot.effective_from, snapshot.effective_until = date(2026, 10, 1), date(2026, 10, 18)
    else:
        snapshot.policy_effective_from, snapshot.policy_effective_until = date(2026, 10, 1), date(2026, 10, 18)
    periods.appointments = [snapshot]
    periods.administrative_periods = [admin(workload, appointment_id="APPT-HOD", policy_reference="ads:hod-policy")]
    result = prepare(teaching, workload, periods)
    assert not result.protected_intervals and "appointment_not_applicable" in codes(result)


def multi_appointments():
    teaching, workload, periods = context(band="LECTURER", appointment="HOD")
    periods.appointments = [appointment(), appointment(identity="APPT-DEAN", position_code="DEAN", policy_reference="ads:dean-policy")]
    periods.administrative_periods = [admin(workload, appointment_id="APPT-HOD", policy_reference="ads:hod-policy")]
    return teaching, workload, periods


def resolution(**updates):
    return AdministrativePolicyResolution.model_validate({"staff_id": "STAFF", "appointment_ids": ["APPT-HOD", "APPT-DEAN"],
        "status": "COMPUTED", "allocation_minutes": 240, "rule": "REPLACEMENT", "resolution_reference": "ads:computed-aggregate",
        "effective_from": FIRST, "effective_until": LAST, "provenance": PROVENANCE, **updates})


def test_multiple_appointments_are_not_silently_combined():
    teaching, workload, periods = multi_appointments()
    result = prepare(teaching, workload, periods)
    assert not result.protected_intervals and "unresolved_multiple_appointments" in codes(result)
    periods.administrative_resolutions = [resolution()]
    assert len(prepare(teaching, workload, periods).protected_intervals) == 1


def test_not_computed_source_aggregate_stays_unverified():
    teaching, workload, periods = multi_appointments()
    periods.administrative_resolutions = [resolution(status="NOT_COMPUTED", allocation_minutes=None, rule=None, resolution_reference=None)]
    assert "unresolved_multiple_appointments" in codes(prepare(teaching, workload, periods))


def test_source_resolution_conflict_is_reported_not_overwritten():
    teaching, workload, periods = multi_appointments()
    periods.administrative_resolutions = [resolution(allocation_minutes=480, rule="ADDITIVE")]
    with raises_issue("allocation_resolution_mismatch"):
        prepare(teaching, workload, periods)
    assert workload.policy.appointment_overrides[next(iter(workload.policy.appointment_overrides))].administrative_allocation_minutes == 240


def test_incomplete_resolution_does_not_cover_every_concurrent_appointment():
    teaching, workload, periods = multi_appointments()
    periods.administrative_resolutions = [resolution(appointment_ids=["APPT-HOD"])]
    assert "unresolved_multiple_appointments" in codes(prepare(teaching, workload, periods))


def test_overlapping_resolutions_are_rejected_without_guessing_priority():
    teaching, workload, periods = multi_appointments()
    periods.administrative_resolutions = [resolution(), resolution(resolution_reference="ads:another-version")]
    with raises_issue("conflicting_policy_resolutions"):
        prepare(teaching, workload, periods)


def test_explicit_flexible_permission_and_windows_prepare_candidates_only():
    teaching, workload, periods = context()
    periods.flexible_policies, periods.flexible_requests = [flexible_policy(workload)], [flexible_request()]
    result = prepare(teaching, workload, periods)
    assert not result.protected_intervals
    candidate = result.flexible_candidates[0]
    assert candidate.planned_duration_minutes == 240 and candidate.eligible_windows[0].on_date == FIRST
    assert not candidate.to_dict()["scheduled"] and candidate.to_dict()["work_minutes_credited"] == 0


@pytest.mark.parametrize("cause", ["missing", "pending"])
def test_flexible_permission_must_be_explicit_and_approved(cause):
    teaching, workload, periods = context()
    periods.flexible_requests = [flexible_request()]
    if cause == "pending":
        periods.flexible_policies = [flexible_policy(workload, status="PENDING", approval_reference=None)]
    result = prepare(teaching, workload, periods)
    assert not result.flexible_candidates and not result.protected_intervals


def test_explicit_denial_does_not_allow_flexible_scheduling():
    teaching, workload, periods = context()
    periods.flexible_policies, periods.flexible_requests = [flexible_policy(workload, flexible_scheduling_permitted=False)], [flexible_request()]
    with raises_issue("flexible_scheduling_not_permitted"):
        prepare(teaching, workload, periods)


def test_split_candidate_uses_supplied_minimum_and_disjoint_windows():
    teaching, workload, periods = context()
    periods.flexible_policies = [flexible_policy(workload)]
    periods.flexible_requests = [flexible_request(allow_split=True, minimum_period_minutes=60, eligible_windows=[
        {"on_date": FIRST, "start_minute": 540, "end_minute": 660},
        {"on_date": date(2026, 10, 20), "start_minute": 540, "end_minute": 660}])]
    assert len(prepare(teaching, workload, periods).flexible_candidates[0].eligible_windows) == 2


def test_overlapping_flexible_windows_do_not_double_the_time_budget():
    teaching, workload, periods = context()
    periods.flexible_policies = [flexible_policy(workload)]
    periods.flexible_requests = [flexible_request(allow_split=True, minimum_period_minutes=60, eligible_windows=[
        {"on_date": FIRST, "start_minute": 540, "end_minute": 660},
        {"on_date": FIRST, "start_minute": 600, "end_minute": 720}])]
    with raises_issue("insufficient_flexible_windows"):
        prepare(teaching, workload, periods)


def test_split_minimum_makes_some_apparent_total_budgets_infeasible():
    teaching, workload, periods = context()
    periods.flexible_policies = [flexible_policy(workload)]
    periods.flexible_requests = [flexible_request(allow_split=True, planned_duration_minutes=20, minimum_period_minutes=15,
        eligible_windows=[{"on_date": FIRST, "start_minute": 540, "end_minute": 555},
                          {"on_date": FIRST, "start_minute": 600, "end_minute": 615}])]
    with raises_issue("insufficient_flexible_windows"):
        prepare(teaching, workload, periods)


def test_flexible_windows_exclude_supplied_hard_research_protection():
    teaching, workload, periods = context()
    periods.research_policies = [research_policy()]
    periods.research_reservations = [research(on_date=FIRST, full_day=False, windows=[{"start_minute": 540, "end_minute": 600}])]
    periods.flexible_policies = [flexible_policy(workload)]
    periods.flexible_requests = [flexible_request(eligible_windows=[{"on_date": FIRST, "start_minute": 540, "end_minute": 840}])]
    result = prepare(teaching, workload, periods)
    window = result.flexible_candidates[0].eligible_windows[0]
    assert (window.start_minute, window.end_minute) == (600, 840)
    assert "flexible_windows_trimmed" in codes(result)


def test_flexible_request_cannot_exceed_explicit_allocation():
    teaching, workload, periods = context()
    periods.flexible_policies = [flexible_policy(workload)]
    periods.flexible_requests = [flexible_request(planned_duration_minutes=300,
        eligible_windows=[{"on_date": FIRST, "start_minute": 540, "end_minute": 900}])]
    with raises_issue("flexible_allocation_exceeded"):
        prepare(teaching, workload, periods)


@pytest.mark.parametrize("kind", ["admin", "research", "flexible"])
def test_missing_approval_evidence_is_invalid(kind):
    _, workload, _ = context()
    factory = {"admin": lambda: admin(workload, approval_reference=None),
               "research": lambda: research(approval_reference=None),
               "flexible": lambda: flexible_request(approval_reference=None)}[kind]
    with pytest.raises(ValidationError, match="approval evidence"):
        factory()


@pytest.mark.parametrize("cause", ["reversed", "both_selectors", "missing_selector", "outside_effective", "duration_mismatch"])
def test_invalid_admin_dates_selectors_and_duration(cause):
    _, workload, _ = context()
    updates = {"reversed": {"effective_until": date(2026, 10, 18)}, "both_selectors": {"weekday": "MONDAY"},
               "missing_selector": {"on_date": None}, "outside_effective": {"on_date": date(2026, 10, 18)},
               "duration_mismatch": {"planned_duration_minutes": 120}}[cause]
    with pytest.raises(ValidationError):
        admin(workload, **updates)


@pytest.mark.parametrize("updates", [{"full_day": True, "windows": [{"start_minute": 540, "end_minute": 600}]},
                                    {"full_day": False}, {"full_day": 1},
                                    {"full_day": False, "windows": [{"start_minute": 540, "end_minute": 600},
                                                                     {"start_minute": 570, "end_minute": 630}]}])
def test_invalid_research_designation_and_windows(updates):
    with pytest.raises(ValidationError):
        research(**updates)


@pytest.mark.parametrize("updates", [{"eligible_windows": []}, {"allow_split": True},
                                    {"allow_split": False, "minimum_period_minutes": 60},
                                    {"allow_split": True, "minimum_period_minutes": 300}])
def test_flexible_contract_requires_supplied_windows_and_split_rules(updates):
    with pytest.raises(ValidationError):
        flexible_request(**updates)


@pytest.mark.parametrize("kind", ["admin", "research", "appointment", "flexible"])
def test_unknown_staff_references_rejected(kind):
    teaching, workload, periods = context()
    if kind == "admin":
        periods.administrative_periods = [admin(workload, staff_id="UNKNOWN")]
    elif kind == "research":
        periods.research_policies = [research_policy(staff_id="UNKNOWN")]
    elif kind == "appointment":
        periods.appointments = [appointment(staff_id="UNKNOWN")]
    else:
        periods.flexible_requests = [flexible_request(staff_id="UNKNOWN")]
    with raises_issue("unknown_staff"):
        prepare(teaching, workload, periods)


def test_source_institution_is_verified_where_supplied_and_never_generated():
    teaching, workload, periods = context()
    periods.staff_institutions = {"STAFF": "SYNTHETIC-INSTITUTION"}
    periods.administrative_periods = [admin(workload)]
    interval = prepare(teaching, workload, periods).protected_intervals[0]
    assert interval.institution_id == "SYNTHETIC-INSTITUTION"
    periods.administrative_periods = [admin(workload, institution_id="DIFFERENT")]
    with raises_issue("institution_mismatch"):
        prepare(teaching, workload, periods)


def test_pending_appointment_reference_must_still_exist():
    teaching, workload, periods = context()
    periods.administrative_periods = [admin(workload, appointment_id="UNKNOWN", status="PENDING", approval_reference=None)]
    with raises_issue("invalid_appointment_reference"):
        prepare(teaching, workload, periods)


def evidence(identity, start, end, *, kind="ADMINISTRATIVE", day=FIRST, **updates):
    return RecognizedWorkRecord(work_id=identity, staff_id="STAFF", work_type=kind, duration_minutes=end - start,
        work_date=day, start_minute=start, end_minute=end, evidence_reference="synthetic:verified-work", **updates)


def test_overlapping_completion_evidence_is_rejected_across_work_categories():
    _, workload, _ = context()
    payload = workload.model_dump(round_trip=True)
    payload["recognized_work"] = [evidence("ADMIN", 540, 660).model_dump(),
                                   evidence("OTHER", 600, 720, kind="OTHER_RECOGNIZED").model_dump()]
    with pytest.raises(ValidationError, match="overlapping recognized work"):
        WeeklyWorkloadInput.model_validate(payload)


def test_adjacent_completed_work_is_not_double_counted_or_rejected():
    teaching, workload, periods = context()
    workload.recognized_work = [evidence("FIRST", 540, 660), evidence("SECOND", 660, 780)]
    result = prepare(teaching, workload, periods)
    assert len(result.workload_snapshot.recognized_work) == 2


def test_duplicate_credit_source_id_cannot_be_counted_under_two_work_ids():
    _, workload, _ = context()
    payload = workload.model_dump(round_trip=True)
    payload["recognized_work"] = [evidence("FIRST", 540, 660, credit_source_id="SOURCE-CREDIT").model_dump(),
                                   evidence("SECOND", 660, 780, credit_source_id="SOURCE-CREDIT").model_dump()]
    with pytest.raises(ValidationError, match="duplicate work credit source"):
        WeeklyWorkloadInput.model_validate(payload)


def test_mutated_overlapping_evidence_is_caught_before_preparation():
    teaching, workload, periods = context()
    workload.recognized_work = [evidence("FIRST", 540, 660), evidence("SECOND", 600, 720)]
    with pytest.raises(PreparationError, match="overlapping recognized work"):
        prepare(teaching, workload, periods)


def test_legacy_untimed_evidence_is_preserved_and_overlap_uncertainty_is_reported():
    teaching, workload, periods = context()
    workload.recognized_work = [RecognizedWorkRecord(work_id="LEGACY", staff_id="STAFF", work_type="ADMINISTRATIVE",
        duration_minutes=240, evidence_reference="synthetic:legacy-credit")]
    result = prepare(teaching, workload, periods)
    assert "untimed_work_evidence" in codes(result)
    assert result.workload_snapshot.recognized_work[0].duration_minutes == 240


def test_timed_completion_evidence_must_belong_to_the_assessed_week():
    teaching, workload, periods = context()
    workload.recognized_work = [evidence("OTHER-WEEK", 540, 660, day=date(2026, 10, 12))]
    with raises_issue("work_evidence_outside_week"):
        prepare(teaching, workload, periods)


@pytest.mark.parametrize("field,value", [("work_date", None), ("start_minute", None), ("end_minute", None), ("duration_minutes", 60)])
def test_invalid_timed_evidence_contract(field, value):
    payload = evidence("WORK", 540, 660).model_dump()
    payload[field] = value
    with pytest.raises(ValidationError):
        RecognizedWorkRecord.model_validate(payload)


def test_preparation_never_creates_completion_or_attendance_or_changes_solver():
    teaching, workload, periods = context()
    periods.administrative_periods = [admin(workload)]
    before = teaching.model_dump(round_trip=True), workload.model_dump(round_trip=True), periods.model_dump(round_trip=True)
    result = prepare(teaching, workload, periods)
    assert (teaching.model_dump(round_trip=True), workload.model_dump(round_trip=True), periods.model_dump(round_trip=True)) == before
    assert result.workload_snapshot.recognized_work == [] and result.workload_snapshot.attendance == []
    prepared = demand({"OFFER": [("STAFF", "LIC")]})
    assert solve_timetable(prepared, windows()).schedule == solve_timetable(prepared, windows()).schedule


def test_interval_guard_fails_explicitly_without_partial_return():
    teaching, workload, periods = context()
    periods.research_policies = [research_policy()]
    periods.research_reservations = [research(full_day=False, windows=[
        {"start_minute": 540, "end_minute": 600}, {"start_minute": 660, "end_minute": 720}])]
    with raises_issue("interval_limit_exceeded"):
        prepare_work_periods(teaching, workload, periods, max_intervals=1)


def test_duplicate_source_reservation_ids_are_rejected():
    _, workload, periods = context()
    payload = periods.model_dump(round_trip=True)
    payload["administrative_periods"] = [admin(workload).model_dump(), admin(workload).model_dump()]
    with pytest.raises(ValidationError, match="unique"):
        WorkPeriodInput.model_validate(payload)


def test_expired_concurrent_policy_is_not_hidden_by_an_aggregate_claim():
    teaching, workload, periods = multi_appointments()
    periods.appointments[1].policy_effective_from = date(2026, 10, 1)
    periods.appointments[1].policy_effective_until = date(2026, 10, 18)
    periods.administrative_resolutions = [resolution()]
    result = prepare(teaching, workload, periods)
    assert not result.protected_intervals and "unverified_concurrent_appointment_policy" in codes(result)


@pytest.mark.parametrize("computed", [False, True])
def test_single_appointment_source_resolution_must_be_consistent_if_supplied(computed):
    teaching, workload, periods = context(band="LECTURER", appointment="HOD")
    periods.appointments = [appointment()]
    periods.administrative_periods = [admin(workload, appointment_id="APPT-HOD", policy_reference="ads:hod-policy")]
    periods.administrative_resolutions = [resolution(appointment_ids=["APPT-HOD"],
        status="COMPUTED" if computed else "NOT_COMPUTED", allocation_minutes=480 if computed else None)]
    if computed:
        with raises_issue("allocation_resolution_mismatch"):
            prepare(teaching, workload, periods)
    else:
        assert "unresolved_administrative_policy" in codes(prepare(teaching, workload, periods))


def test_policy_references_can_be_shared_across_staff_without_merging_permissions():
    teaching = demand({"A": [("FIRST", "LIC")], "B": [("SECOND", "LIC")]}).input_snapshot
    workload = WeeklyWorkloadInput(week_reference="SYNTHETIC-WEEK", policy=confirmed_campus_policy(),
        profiles=[StaffWorkProfile(staff_id=identity, workload_band="PROFESSOR", evidence_reference="synthetic:profile")
                  for identity in ("FIRST", "SECOND")])
    periods = WorkPeriodInput(week_reference="SYNTHETIC-WEEK", week_start=FIRST, week_end=LAST,
        research_policies=[research_policy(staff_id=identity) for identity in ("FIRST", "SECOND")],
        research_reservations=[research(identity=identity, staff_id=identity) for identity in ("FIRST", "SECOND")])
    result = prepare(teaching, workload, periods)
    assert {row.staff_id for row in result.protected_intervals} == {"FIRST", "SECOND"}


def test_soft_flexible_request_does_not_need_hard_permission_or_create_a_block():
    teaching, workload, periods = context()
    periods.flexible_policies = [flexible_policy(workload, allow_hard_protection=False)]
    periods.flexible_requests = [flexible_request(protection="SOFT")]
    result = prepare(teaching, workload, periods)
    assert result.flexible_candidates[0].protection is PeriodProtection.SOFT and not result.protected_intervals


def test_fixed_period_and_completion_evidence_are_not_added_together_as_work():
    teaching, workload, periods = context()
    periods.administrative_periods = [admin(workload)]
    workload.recognized_work = [evidence("COMPLETED", 540, 780)]
    result = prepare(teaching, workload, periods)
    assert sum(row.duration_minutes for row in result.workload_snapshot.recognized_work) == 240
    assert result.to_dict()["work_minutes_credited"] == 0


def test_legacy_total_target_mode_remains_explicit_and_no_new_admin_rule_is_inferred():
    from schedence_teaching.workload import WorkloadTargetBasis
    teaching, workload, periods = context()
    workload.policy = confirmed_campus_policy(target_basis=WorkloadTargetBasis.TOTAL_RECOGNIZED_WORK)
    periods.administrative_periods = [admin(workload)]
    result = prepare(teaching, workload, periods)
    assert "unverified_admin_allocation" in codes(result) and not result.protected_intervals
    assert result.workload_snapshot.policy.target_basis is WorkloadTargetBasis.TOTAL_RECOGNIZED_WORK


def test_documented_synthetic_work_period_demo_does_not_schedule_or_credit_work():
    outputs = runpy.run_path(str(Path(__file__).parents[1] / "examples" / "work-periods-demo.py"))["run_scenarios"]()
    assert outputs["approved_admin_and_research"]["approved_hard_interval_count"] == 2
    assert outputs["pending_admin"]["approved_hard_interval_count"] == 0
    assert outputs["pending_admin"]["diagnostic_counts"]["PENDING"] == 1
    assert outputs["soft_research"]["approved_soft_interval_count"] == 1
    assert outputs["flexible_admin"]["eligible_flexible_request_count"] == 1
    for output in outputs.values():
        assert output["work_minutes_credited"] == 0 and not output["solver_enforced"]
