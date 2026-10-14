"""Teaching-service policy inputs; academic ranks/appointments remain upstream data."""

from enum import StrEnum
from datetime import date
from typing import Annotated, Self

from pydantic import Field, StrictBool, model_validator, model_serializer

from schedence_teaching.schemas.models import InputContract, PositiveInteger, RequiredText


class WorkloadBand(StrEnum):
    INSTRUCTOR = "INSTRUCTOR"
    ASSISTANT_LECTURER = "ASSISTANT_LECTURER"
    LECTURER = "LECTURER"
    SENIOR_LECTURER = "SENIOR_LECTURER"
    DR = "DR"
    ASSISTANT_PROFESSOR = "ASSISTANT_PROFESSOR"
    PROFESSOR = "PROFESSOR"


class StaffAppointment(StrEnum):
    HOD = "HOD"
    DEAN = "DEAN"


class WorkloadTargetBasis(StrEnum):
    TEACHING_CONTACT = "TEACHING_CONTACT"
    TOTAL_RECOGNIZED_WORK = "TOTAL_RECOGNIZED_WORK"


class RecognizedWorkType(StrEnum):
    ADMINISTRATIVE = "ADMINISTRATIVE"
    OTHER_RECOGNIZED = "OTHER_RECOGNIZED"


class WorkloadRule(InputContract):
    # Legacy name retained; TEACHING_CONTACT policies interpret this as teaching only.
    weekly_target_minutes: PositiveInteger
    administrative_allocation_minutes: Annotated[int, Field(strict=True, ge=0)] | None = None
    compulsory_campus_days: Annotated[int, Field(strict=True, ge=1, le=7)] | None = None
    campus_day_exempt: StrictBool = False

    @model_validator(mode="after")
    def consistent_attendance_rule(self) -> Self:
        if self.campus_day_exempt and self.compulsory_campus_days is not None:
            raise ValueError("a campus-day exemption cannot also require compulsory days")
        return self


class WorkloadPolicy(InputContract):
    policy_reference: RequiredText
    # Required: no silent assumption that teaching and recognized total targets coincide.
    target_basis: WorkloadTargetBasis
    band_rules: dict[WorkloadBand, WorkloadRule]
    appointment_overrides: dict[StaffAppointment, WorkloadRule]
    staff_overrides: dict[RequiredText, WorkloadRule] = Field(default_factory=dict)

    @model_serializer(mode='wrap')
    def historical_payload(self, handler):
        result = handler(self)
        if not self.staff_overrides:
            result.pop('staff_overrides', None)
        return result


class StaffWorkProfile(InputContract):
    staff_id: RequiredText
    workload_band: WorkloadBand
    # This supplements, never changes, the existing StaffMember.position/rank.
    appointment: StaffAppointment | None = None
    evidence_reference: RequiredText


class RecognizedWorkRecord(InputContract):
    work_id: RequiredText
    staff_id: RequiredText
    work_type: RecognizedWorkType
    duration_minutes: PositiveInteger
    evidence_reference: RequiredText
    # Optional for legacy evidence. New timed evidence can be checked for overlap.
    work_date: date | None = None
    start_minute: Annotated[int, Field(strict=True, ge=0, lt=1440)] | None = None
    end_minute: Annotated[int, Field(strict=True, gt=0, le=1440)] | None = None
    credit_source_id: RequiredText | None = None

    @model_validator(mode="after")
    def consistent_timed_evidence(self) -> Self:
        supplied = (self.work_date is not None, self.start_minute is not None, self.end_minute is not None)
        if any(supplied) and not all(supplied):
            raise ValueError("timed work evidence requires date, start and end together")
        if all(supplied):
            from schedence_teaching.solver.config import TimeWindow
            window = TimeWindow(start_minute=self.start_minute, end_minute=self.end_minute)
            if self.duration_minutes != window.end_minute - window.start_minute:
                raise ValueError("recognized duration must equal the timed evidence interval")
        return self


class AdministrativeReservationStatus(StrEnum):
    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"


class AdministrativeReservation(InputContract):
    """Weekly planning metadata only; no calendar slot or completed-work credit."""

    reservation_id: RequiredText
    staff_id: RequiredText
    duration_minutes: PositiveInteger
    status: AdministrativeReservationStatus
    evidence_reference: RequiredText
    approval_reference: RequiredText | None = None

    @model_validator(mode="after")
    def explicit_approval(self) -> Self:
        if self.status is AdministrativeReservationStatus.APPROVED and self.approval_reference is None:
            raise ValueError("approved administrative reservations require an approval reference")
        if self.status is AdministrativeReservationStatus.PROPOSED and self.approval_reference is not None:
            raise ValueError("proposed administrative reservations cannot claim approval")
        return self


class CampusAttendanceRecord(InputContract):
    staff_id: RequiredText
    working_days: tuple[RequiredText, ...]
    complete: StrictBool
    evidence_reference: RequiredText

    @model_validator(mode="after")
    def distinct_days(self) -> Self:
        if len(set(self.working_days)) != len(self.working_days):
            raise ValueError("campus attendance days must be unique")
        if len(self.working_days) > 7:
            raise ValueError("weekly campus attendance cannot contain more than seven days")
        return self


class WeeklyWorkloadInput(InputContract):
    week_reference: RequiredText
    policy: WorkloadPolicy
    profiles: list[StaffWorkProfile]
    recognized_work: list[RecognizedWorkRecord] = Field(default_factory=list)
    # Missing completeness means missing work may exist; [] is not proof of zero work.
    recognized_work_complete_for: set[RequiredText] = Field(default_factory=set)
    attendance: list[CampusAttendanceRecord] = Field(default_factory=list)
    administrative_reservations: list[AdministrativeReservation] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_records(self) -> Self:
        for records, field in ((self.profiles, "staff_id"), (self.recognized_work, "work_id"),
                               (self.attendance, "staff_id"), (self.administrative_reservations, "reservation_id")):
            identities = [getattr(record, field) for record in records]
            if len(set(identities)) != len(identities):
                raise ValueError(f"duplicate {field} in weekly workload records")
        sources = [(row.staff_id, row.credit_source_id) for row in self.recognized_work if row.credit_source_id is not None]
        if len(set(sources)) != len(sources):
            raise ValueError("duplicate work credit source for the same staff member")
        timed = {}
        for row in self.recognized_work:
            if row.work_date is not None:
                timed.setdefault((row.staff_id, row.work_date), []).append(row)
        for records in timed.values():
            ordered = sorted(records, key=lambda row: row.start_minute)
            if any(first.end_minute > second.start_minute for first, second in zip(ordered, ordered[1:])):
                raise ValueError("overlapping recognized work evidence cannot be counted twice")
        return self


def confirmed_campus_policy(*, target_basis: WorkloadTargetBasis = WorkloadTargetBasis.TEACHING_CONTACT,
                            administrative_allocation_minutes: int = 240) -> WorkloadPolicy:
    """Separate confirmed teaching/admin requirements; explicit TOTAL mode is legacy."""
    target_basis = WorkloadTargetBasis(target_basis)
    budget = WorkloadRule(weekly_target_minutes=1,
                          administrative_allocation_minutes=administrative_allocation_minutes).administrative_allocation_minutes
    legacy = target_basis is WorkloadTargetBasis.TOTAL_RECOGNIZED_WORK
    if legacy and budget != 240:
        raise ValueError("custom administrative allocations require a separate teaching policy, not the legacy combined preset")
    hours = {
        WorkloadBand.INSTRUCTOR: 20, WorkloadBand.ASSISTANT_LECTURER: 18,
        WorkloadBand.LECTURER: 16, WorkloadBand.SENIOR_LECTURER: 12,
        WorkloadBand.DR: 8, WorkloadBand.ASSISTANT_PROFESSOR: 6, WorkloadBand.PROFESSOR: 6,
    }
    return WorkloadPolicy(
        policy_reference=("explicit-legacy-combined-workload-compatibility" if legacy else
                          "user-confirmed-separate-teaching-and-administrative-requirements"),
        target_basis=target_basis,
        band_rules={band: WorkloadRule(
            weekly_target_minutes=hours * 60,
            administrative_allocation_minutes=budget if band is WorkloadBand.PROFESSOR and not legacy else None,
            compulsory_campus_days=None if band in {WorkloadBand.INSTRUCTOR, WorkloadBand.PROFESSOR} else 6,
            campus_day_exempt=band is WorkloadBand.PROFESSOR,
        ) for band, hours in hours.items()},
        appointment_overrides={appointment: WorkloadRule(weekly_target_minutes=240, campus_day_exempt=True,
                                                        administrative_allocation_minutes=budget if not legacy else None)
                               for appointment in StaffAppointment},
    )
