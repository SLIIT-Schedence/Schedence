"""Pure weekly fulfillment assessment; never invents campus hours or work credits."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from schedence_teaching.solver.results import SolverResult

from pydantic import ValidationError

from schedence_teaching.schemas import StaffMember, StaffType

from .contracts import (
    RecognizedWorkType, StaffWorkProfile, WeeklyWorkloadInput, WorkloadBand, WorkloadPolicy,
    WorkloadTargetBasis,
)
from .administration import AdministrativeAllocationAssessment, assess_administrative_allocation


class WorkloadInputError(ValueError):
    pass


class FulfillmentStatus(StrEnum):
    BELOW_TARGET = "BELOW_TARGET"
    MET = "MET"
    EXCEEDED = "EXCEEDED"
    NOT_VERIFIED = "NOT_VERIFIED"


class AttendanceStatus(StrEnum):
    MET = "MET"
    BELOW_REQUIREMENT = "BELOW_REQUIREMENT"
    NOT_VERIFIED = "NOT_VERIFIED"
    EXEMPT = "EXEMPT"
    UNSPECIFIED = "UNSPECIFIED"


@dataclass(frozen=True)
class ResolvedWorkPolicy:
    staff_id: str
    workload_band: WorkloadBand
    appointment: str | None
    policy_reference: str
    rule_source: str
    target_basis: WorkloadTargetBasis
    weekly_target_minutes: int
    compulsory_campus_days: int | None
    campus_day_exempt: bool
    required_teaching_minutes: int | None = None
    administrative_allocation_minutes: int | None = None


@dataclass(frozen=True)
class StaffWorkloadAssessment:
    policy: ResolvedWorkPolicy
    scheduled_teaching_minutes: int
    administrative_minutes: int
    other_recognized_minutes: int
    known_total_work_minutes: int
    recognized_work_complete: bool
    credited_target_minutes: int
    target_status: FulfillmentStatus
    target_fulfilled: bool | None
    target_shortfall_minutes: int | None
    teaching_days: tuple[str, ...]
    evidenced_campus_days: tuple[str, ...]
    campus_attendance_complete: bool
    attendance_status: AttendanceStatus
    daily_teaching_minutes: tuple[tuple[str, int], ...] = ()
    administration: AdministrativeAllocationAssessment | None = None

    @property
    def missing_evidence(self) -> tuple[str, ...]:
        missing = []
        if not self.recognized_work_complete:
            missing.append("recognized_work_ledger")
        if self.administration is not None and self.administration.completion_status == "NOT_VERIFIED":
            missing.append("administrative_work_completion")
        if self.attendance_status is AttendanceStatus.NOT_VERIFIED:
            missing.append("campus_attendance")
        elif self.attendance_status is AttendanceStatus.UNSPECIFIED:
            missing.append("campus_attendance_policy")
        return tuple(missing)

    def to_dict(self):
        return {**asdict(self), "administration": self.administration.to_dict() if self.administration is not None else None,
                "missing_evidence": list(self.missing_evidence),
                "legacy_combined_target_policy": self.policy.target_basis is WorkloadTargetBasis.TOTAL_RECOGNIZED_WORK,
                "verified_administrative_minutes": self.administrative_minutes,
                "verified_other_recognized_minutes": self.other_recognized_minutes,
                "verified_total_minutes": self.known_total_work_minutes,
                "remaining_uncredited_target_minutes": max(self.policy.weekly_target_minutes - self.credited_target_minutes, 0)}


@dataclass(frozen=True)
class WeeklyWorkloadReport:
    week_reference: str
    staff: tuple[StaffWorkloadAssessment, ...]
    unassessed_staff_ids: tuple[str, ...]
    staff_constraints_enforced: bool = False

    @property
    def completion_status(self) -> str:
        if any(row.target_fulfilled is False or row.attendance_status is AttendanceStatus.BELOW_REQUIREMENT
               or (row.administration is not None and row.administration.fulfilled is False) for row in self.staff):
            return "INCOMPLETE"
        if self.unassessed_staff_ids or any(row.missing_evidence for row in self.staff):
            return "NOT_VERIFIED"
        return "COMPLETE"

    @property
    def all_administrative_allocations_fulfilled(self) -> bool | None:
        values = [row.administration.fulfilled for row in self.staff
                  if row.administration is not None and row.administration.allocation_minutes is not None]
        if False in values:
            return False
        return None if self.unassessed_staff_ids or None in values else True

    @property
    def all_workload_targets_fulfilled(self) -> bool | None:
        values = [record.target_fulfilled for record in self.staff]
        if False in values:
            return False
        return None if self.unassessed_staff_ids or None in values else True

    def to_dict(self):
        return {"week_reference": self.week_reference, "staff": [record.to_dict() for record in self.staff],
                "unassessed_staff_ids": list(self.unassessed_staff_ids),
                "all_workload_targets_fulfilled": self.all_workload_targets_fulfilled,
                "all_administrative_allocations_fulfilled": self.all_administrative_allocations_fulfilled,
                "completion_status": self.completion_status,
                "staff_availability_and_caps_enforced": self.staff_constraints_enforced,
                "solver_enforced": False, "university_ready": False}


def _snapshot(value, model):
    if not isinstance(value, model):
        raise WorkloadInputError(f"Expected {model.__name__}.")
    try:
        return model.model_validate(value.model_dump(round_trip=True))
    except ValidationError as error:
        details = error.errors(include_input=False, include_context=False, include_url=False)
        raise WorkloadInputError("; ".join(f"{row['loc']}: {row['msg']}" for row in details)) from None


def resolve_work_policy(staff: StaffMember, profile: StaffWorkProfile, policy: WorkloadPolicy) -> ResolvedWorkPolicy:
    """Explicit classification and appointment override; never parse position/name."""
    staff, profile, policy = (_snapshot(staff, StaffMember), _snapshot(profile, StaffWorkProfile),
                              _snapshot(policy, WorkloadPolicy))
    if staff.staff_id != profile.staff_id:
        raise WorkloadInputError("Work profile must reference the supplied staff ID exactly.")
    if (staff.staff_type is StaffType.INSTRUCTOR) != (profile.workload_band is WorkloadBand.INSTRUCTOR):
        raise WorkloadInputError("Workload band must preserve the academic/instructor distinction.")
    rule = policy.staff_overrides.get(staff.staff_id) or (
        policy.appointment_overrides.get(profile.appointment) if profile.appointment is not None
        else policy.band_rules.get(profile.workload_band))
    if rule is None:
        raise WorkloadInputError("No explicit rule for the supplied workload band or appointment; no fallback is assumed.")
    return ResolvedWorkPolicy(
        staff.staff_id, profile.workload_band, profile.appointment.value if profile.appointment else None,
        policy.policy_reference, "STAFF_OVERRIDE" if staff.staff_id in policy.staff_overrides else
            ("APPOINTMENT_OVERRIDE" if profile.appointment else "WORKLOAD_BAND"),
        policy.target_basis, rule.weekly_target_minutes, rule.compulsory_campus_days, rule.campus_day_exempt,
        rule.weekly_target_minutes if policy.target_basis is WorkloadTargetBasis.TEACHING_CONTACT else None,
        rule.administrative_allocation_minutes,
    )


def assess_weekly_workload(prepared, config, result: SolverResult, workload_input: WeeklyWorkloadInput, *,
                           venue_input=None, combination_input=None, combination_config=None,
                           staff_input=None, work_periods=None, work_period_config=None, preference_input=None,
                           preference_calendar=None, preference_policy=None, preference_config=None,
                           fairness_policy=None, fairness_calendar=None, fairness_config=None,
                           consecutive_policy=None, consecutive_calendar=None, consecutive_config=None,
                           student_lunch_policy=None, student_lunch_calendar=None, student_lunch_config=None) -> WeeklyWorkloadReport:
    """Assess a complete, revalidated schedule; evidence is for the referenced run week."""
    from schedence_teaching.solver.fairness_validation import fairness_validation_requested
    from schedence_teaching.solver.consecutive_validation import consecutive_validation_requested
    from schedence_teaching.solver.lunch_validation import student_lunch_validation_requested
    if (student_lunch_validation_requested(result, student_lunch_policy, student_lunch_calendar, student_lunch_config)
            or fairness_validation_requested(result, fairness_policy, fairness_calendar, fairness_config)
            or consecutive_validation_requested(result, consecutive_policy, consecutive_calendar, consecutive_config)):
        from schedence_teaching.solver.evaluation import validate_evaluation_result
        validate_evaluation_result(prepared, config, result, venue_input=venue_input,
            combination_input=combination_input, combination_config=combination_config,
            staff_input=staff_input, work_periods=work_periods, work_period_config=work_period_config,
            preference_input=preference_input, preference_calendar=preference_calendar,
            preference_policy=preference_policy, preference_config=preference_config,
            fairness_policy=fairness_policy, fairness_calendar=fairness_calendar, fairness_config=fairness_config,
            consecutive_policy=consecutive_policy, consecutive_calendar=consecutive_calendar, consecutive_config=consecutive_config,
            student_lunch_policy=student_lunch_policy, student_lunch_calendar=student_lunch_calendar, student_lunch_config=student_lunch_config)
        if staff_input is None:
            if result.fairness_optimization_enabled or result.consecutive_optimization_enabled:
                raise WorkloadInputError("Optimized assessment requires the original staff workload input.")
            return _assess_validated_schedule(prepared, result, workload_input)
        if workload_input != staff_input.workload_input:
            raise WorkloadInputError("Assessment requires the original staff workload input.")
        return _assess_validated_schedule(prepared, result, workload_input, staff_constraints_enforced=True)
    from schedence_teaching.solver import SolverResult, SolverStatus, validate_delivery_result, validate_timetable
    from schedence_teaching.solver.inputs import checked_inputs
    from schedence_teaching.solver.staff_validation import validate_staff_policy_result
    inputs = _snapshot(workload_input, WeeklyWorkloadInput)
    prepared, config = checked_inputs(prepared, config)
    if (not isinstance(result, SolverResult) or type(result.status) is not SolverStatus
            or result.status not in {SolverStatus.OPTIMAL, SolverStatus.FEASIBLE}
            or result.validation is None):
        raise WorkloadInputError("Workload assessment requires a complete successful solver result.")
    if (work_periods is not None or work_period_config is not None or result.work_period_enabled) and staff_input is None:
        raise WorkloadInputError("Work-period assessment requires original staff scheduling and calendar inputs.")
    if staff_input is not None or result.staff_policy_enabled:
        if staff_input is None or inputs != staff_input.workload_input:
            raise WorkloadInputError("Policy-enabled assessment requires the original staff and workload inputs.")
        validate_staff_policy_result(prepared, config, result, staff_input=staff_input,
                                     venue_input=venue_input, combination_input=combination_input,
                                     combination_config=combination_config, work_periods=work_periods,
                                     work_period_config=work_period_config, preference_input=preference_input,
                                     preference_calendar=preference_calendar, preference_policy=preference_policy,
                                     preference_config=preference_config)
    elif combination_input is not None:
        validate_delivery_result(prepared, config, result, combination_input=combination_input,
                                 combination_config=combination_config, venue_input=venue_input,
                                 preference_input=preference_input, preference_calendar=preference_calendar,
                                 preference_policy=preference_policy, preference_config=preference_config)
    else:
        if result.preference_optimization_enabled or any(row is not None for row in
            (preference_input, preference_calendar, preference_policy, preference_config)):
            from schedence_teaching.solver.preference_validation import validate_preference_result
            validate_preference_result(prepared, config, result, preference_input=preference_input,
                preference_calendar=preference_calendar, preference_policy=preference_policy,
                preference_config=preference_config, venue_input=venue_input)
        if result.combination_enabled or combination_config is not None:
            raise WorkloadInputError("Combination results require their original combination validation inputs.")
        summary = validate_timetable(prepared, config, result.schedule, venue_input=venue_input)
        if result.validation != summary or result.venue_enabled != (venue_input is not None):
            raise WorkloadInputError("Result validation/mode does not match its schedule and supplied inputs.")
    return _assess_validated_schedule(prepared, result, inputs, staff_constraints_enforced=staff_input is not None)


def checked_workload_input(prepared, workload_input):
    """Validate source references/profiles before solving or crediting any work."""
    inputs = _snapshot(workload_input, WeeklyWorkloadInput)
    staff = {row.staff_id: row for row in prepared.input_snapshot.staff_members}
    referenced = (set(inputs.policy.staff_overrides) | {row.staff_id for row in inputs.profiles} | {row.staff_id for row in inputs.recognized_work}
                  | {row.staff_id for row in inputs.attendance} | inputs.recognized_work_complete_for
                  | {row.staff_id for row in inputs.administrative_reservations})
    if referenced - staff.keys():
        raise WorkloadInputError("Weekly workload evidence references unknown staff IDs.")
    policies = {profile.staff_id: resolve_work_policy(staff[profile.staff_id], profile, inputs.policy)
                for profile in inputs.profiles}
    for reservation in inputs.administrative_reservations:
        policy = policies.get(reservation.staff_id)
        if policy is None or policy.administrative_allocation_minutes is None:
            raise WorkloadInputError("Administrative reservations require an explicit applicable allocation policy and staff profile.")
    return inputs


def _assess_validated_schedule(prepared, result, workload_input, *, staff_constraints_enforced=False):
    """Shared pure calculation, called only after source/schedule validation."""
    from schedence_teaching.solver.results import ScheduledDelivery

    inputs = checked_workload_input(prepared, workload_input)
    staff = {row.staff_id: row for row in prepared.input_snapshot.staff_members}
    teaching, teaching_days, daily = defaultdict(int), defaultdict(set), defaultdict(lambda: defaultdict(int))
    for delivery in result.schedule:
        duration = (delivery.duration_minutes if isinstance(delivery, ScheduledDelivery)
                    else delivery.session.duration_minutes)
        teaching[delivery.assigned_staff_id] += duration  # One physical shared delivery, once.
        teaching_days[delivery.assigned_staff_id].add(delivery.day)
        daily[delivery.assigned_staff_id][delivery.day] += duration
    recognized = defaultdict(lambda: defaultdict(int))
    for record in inputs.recognized_work:
        recognized[record.staff_id][record.work_type] += record.duration_minutes
    attendance = {row.staff_id: row for row in inputs.attendance}
    reservations = defaultdict(list)
    for record in inputs.administrative_reservations:
        reservations[record.staff_id].append(record)
    assessments = []
    for profile in sorted(inputs.profiles, key=lambda row: row.staff_id):
        identity = profile.staff_id
        policy = resolve_work_policy(staff[identity], profile, inputs.policy)
        admin = recognized[identity][RecognizedWorkType.ADMINISTRATIVE]
        other = recognized[identity][RecognizedWorkType.OTHER_RECOGNIZED]
        total = teaching[identity] + admin + other
        complete = identity in inputs.recognized_work_complete_for
        administration = assess_administrative_allocation(policy.administrative_allocation_minutes,
                                                           reservations[identity], admin, complete)
        credit = total if policy.target_basis is WorkloadTargetBasis.TOTAL_RECOGNIZED_WORK else teaching[identity]
        credit_complete = complete or policy.target_basis is WorkloadTargetBasis.TEACHING_CONTACT
        if credit >= policy.weekly_target_minutes:
            status = FulfillmentStatus.MET if credit == policy.weekly_target_minutes else FulfillmentStatus.EXCEEDED
            fulfilled, shortfall = True, 0  # Excess is reported, never treated as a cap violation.
        elif credit_complete:
            status, fulfilled, shortfall = FulfillmentStatus.BELOW_TARGET, False, policy.weekly_target_minutes - credit
        else:
            status, fulfilled, shortfall = FulfillmentStatus.NOT_VERIFIED, None, None
        evidence = attendance.get(identity)
        days = tuple(sorted(evidence.working_days)) if evidence else ()
        attendance_complete = evidence.complete if evidence else False
        if policy.campus_day_exempt:
            attendance_status = AttendanceStatus.EXEMPT
        elif policy.compulsory_campus_days is None:
            attendance_status = AttendanceStatus.UNSPECIFIED
        elif len(days) >= policy.compulsory_campus_days:
            attendance_status = AttendanceStatus.MET
        elif attendance_complete:
            attendance_status = AttendanceStatus.BELOW_REQUIREMENT
        else:
            attendance_status = AttendanceStatus.NOT_VERIFIED
        assessments.append(StaffWorkloadAssessment(
            policy, teaching[identity], admin, other, total, complete, credit, status, fulfilled, shortfall,
            tuple(sorted(teaching_days[identity])), days, attendance_complete, attendance_status,
            tuple(sorted(daily[identity].items())),
            administration,
        ))
    assessed = {row.staff_id for row in inputs.profiles}
    return WeeklyWorkloadReport(inputs.week_reference, tuple(assessments), tuple(sorted(staff.keys() - assessed)),
                                staff_constraints_enforced)
