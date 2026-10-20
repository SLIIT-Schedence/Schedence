"""Physical administrative bookings and evidence-aware work-period reporting."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from schedence_teaching.workload.period_contracts import PeriodProtection, PeriodProvenance
from schedence_teaching.workload.period_preparation import NormalizedWorkInterval, WorkPeriodDiagnostic


@dataclass(frozen=True)
class ScheduledAdministrativePeriod:
    block_id: str
    request_id: str
    staff_id: str
    day: str
    on_date: date
    start_minute: int
    end_minute: int
    institution_id: str | None
    policy_reference: str
    appointment_id: str | None
    protection: PeriodProtection
    approval_reference: str
    provenance: PeriodProvenance

    def to_dict(self):
        return {**vars(self), "on_date": self.on_date.isoformat(), "protection": self.protection.value,
                "provenance": self.provenance.model_dump(mode="json"),
                "reserved_minutes": self.end_minute - self.start_minute,
                "work_minutes_credited": 0, "attendance_inferred": False}


@dataclass(frozen=True)
class StaffWorkPeriodSummary:
    staff_id: str
    required_teaching_minutes: int | None
    scheduled_teaching_minutes: int
    administrative_allocation_minutes: int | None
    fixed_reserved_minutes: int
    flexible_reserved_minutes: int
    reserved_administrative_minutes: int
    unreserved_allocation_minutes: int | None
    excess_reserved_minutes: int | None
    verified_completed_administrative_minutes: int
    verified_other_recognized_minutes: int
    teaching_requirement_status: str
    administrative_completion_status: str
    administrative_completion_fulfilled: bool | None
    evidenced_campus_days: tuple[str, ...]
    campus_attendance_status: str
    missing_evidence: tuple[str, ...]

    def to_dict(self):
        return vars(self).copy()


@dataclass(frozen=True)
class WorkPeriodReport:
    week_reference: str
    fixed_administrative_periods: tuple[NormalizedWorkInterval, ...]
    research_periods: tuple[NormalizedWorkInterval, ...]
    soft_preferences: tuple[NormalizedWorkInterval, ...]
    omitted_optional_request_ids: tuple[str, ...]
    diagnostics: tuple[WorkPeriodDiagnostic, ...]
    staff: tuple[StaffWorkPeriodSummary, ...]
    unassessed_staff_ids: tuple[str, ...]

    def to_dict(self):
        return {"week_reference": self.week_reference,
                "fixed_administrative_periods": [row.to_dict() for row in self.fixed_administrative_periods],
                "research_periods": [row.to_dict() for row in self.research_periods],
                "soft_preferences": [row.to_dict() for row in self.soft_preferences],
                "omitted_optional_request_ids": list(self.omitted_optional_request_ids),
                "diagnostics": [vars(row) for row in self.diagnostics],
                "staff": [row.to_dict() for row in self.staff],
                "unassessed_staff_ids": list(self.unassessed_staff_ids),
                "hard_protection_enforced": True, "flexible_reservations_validated": True,
                "completed_work_inferred": False, "attendance_inferred": False,
                "teaching_targets_enforced_as_caps": False, "university_ready": False}


def work_period_report(periods, administrative_schedule, workload_report):
    """No work credit from reservations; legacy untimed plans are never added twice."""
    fixed = tuple(row for row in periods.protected_intervals if row.kind == "ADMINISTRATIVE")
    research = tuple(row for row in periods.protected_intervals if row.kind == "RESEARCH")
    fixed_minutes, flexible_minutes = defaultdict(int), defaultdict(int)
    for row in fixed:
        fixed_minutes[row.staff_id] += row.end_minute - row.start_minute
    for row in administrative_schedule:
        flexible_minutes[row.staff_id] += row.end_minute - row.start_minute
    staff = []
    for row in workload_report.staff:
        identity, allocation = row.policy.staff_id, row.policy.administrative_allocation_minutes
        reserved = fixed_minutes[identity] + flexible_minutes[identity]
        staff.append(StaffWorkPeriodSummary(
            identity, row.policy.required_teaching_minutes, row.scheduled_teaching_minutes, allocation,
            fixed_minutes[identity], flexible_minutes[identity], reserved,
            max(allocation - reserved, 0) if allocation is not None else None,
            max(reserved - allocation, 0) if allocation is not None else None,
            row.administrative_minutes, row.other_recognized_minutes,
            row.target_status.value if row.policy.required_teaching_minutes is not None else "UNSPECIFIED",
            row.administration.completion_status, row.administration.fulfilled,
            row.evidenced_campus_days, row.attendance_status.value, row.missing_evidence))
    selected = {row.request_id for row in administrative_schedule}
    omitted = tuple(row.request_id for row in periods.flexible_candidates
                    if not row.reservation_required and row.request_id not in selected)
    return WorkPeriodReport(periods.input_snapshot.week_reference, fixed, research, periods.soft_preferences,
                            omitted, periods.diagnostics, tuple(staff), workload_report.unassessed_staff_ids)
