"""Normalize explicitly authorized work protection; no CP-SAT or booking decisions."""

from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import combinations

from pydantic import ValidationError

from schedence_teaching.preparation import PreparationError, PreparationIssue, prepare_timetable_input

from .assessment import WorkloadInputError, checked_workload_input, resolve_work_policy
from .contracts import WeeklyWorkloadInput
from .period_contracts import (
    AdministrativeAggregateStatus, PeriodApprovalStatus, PeriodProtection,
    PeriodProvenance, Weekday, WorkPeriodInput,
)


@dataclass(frozen=True)
class NormalizedWorkInterval:
    interval_id: str
    source_id: str
    staff_id: str
    institution_id: str | None
    kind: str
    on_date: date
    start_minute: int
    end_minute: int
    full_day: bool
    protection: PeriodProtection
    policy_reference: str
    appointment_id: str | None
    approval_reference: str
    provenance: PeriodProvenance

    def to_dict(self):
        return {**vars(self), "on_date": self.on_date.isoformat(), "protection": self.protection.value,
                "provenance": self.provenance.model_dump(mode="json"), "work_minutes_credited": 0}


@dataclass(frozen=True)
class NormalizedEligibleWindow:
    on_date: date
    start_minute: int
    end_minute: int


@dataclass(frozen=True)
class FlexibleAdministrativeCandidate:
    request_id: str
    staff_id: str
    institution_id: str | None
    policy_reference: str
    appointment_id: str | None
    planned_duration_minutes: int
    protection: PeriodProtection
    allow_split: bool
    minimum_period_minutes: int | None
    eligible_windows: tuple[NormalizedEligibleWindow, ...]
    approval_reference: str
    provenance: PeriodProvenance
    reservation_required: bool | None = None

    def to_dict(self):
        return {**vars(self), "protection": self.protection.value,
                "eligible_windows": [{**vars(row), "on_date": row.on_date.isoformat()} for row in self.eligible_windows],
                "provenance": self.provenance.model_dump(mode="json"),
                "scheduled": False, "work_minutes_credited": 0}


@dataclass(frozen=True)
class WorkPeriodDiagnostic:
    code: str
    source_ids: tuple[str, ...]
    category: str
    message: str


@dataclass(frozen=True)
class PreparedWorkPeriods:
    input_snapshot: WorkPeriodInput
    workload_snapshot: WeeklyWorkloadInput
    protected_intervals: tuple[NormalizedWorkInterval, ...]
    soft_preferences: tuple[NormalizedWorkInterval, ...]
    flexible_candidates: tuple[FlexibleAdministrativeCandidate, ...]
    diagnostics: tuple[WorkPeriodDiagnostic, ...]

    def to_dict(self):
        counts = Counter(row.category for row in self.diagnostics)
        return {"week_reference": self.input_snapshot.week_reference,
                "approved_hard_interval_count": len(self.protected_intervals),
                "approved_soft_interval_count": len(self.soft_preferences),
                "eligible_flexible_request_count": len(self.flexible_candidates),
                "diagnostic_counts": dict(sorted(counts.items())),
                "protected_intervals": [row.to_dict() for row in self.protected_intervals],
                "soft_preferences": [row.to_dict() for row in self.soft_preferences],
                "flexible_candidates": [row.to_dict() for row in self.flexible_candidates],
                "diagnostics": [vars(row) for row in self.diagnostics],
                "solver_enforced": False, "work_minutes_credited": 0, "attendance_inferred": False,
                "university_ready": False}


def _active(record, day):
    return record.effective_from <= day and (record.effective_until is None or day <= record.effective_until)


def _selected_dates(record, inputs):
    days = (inputs.week_start + timedelta(days=index) for index in range((inputs.week_end - inputs.week_start).days + 1))
    weekdays = list(Weekday)
    return tuple(day for day in days if _active(record, day) and
                 (day == record.on_date if record.on_date is not None else weekdays[day.weekday()] is record.weekday))


def _union_windows(windows):
    """Union candidate windows only; never merge reservation provenance or credited work."""
    merged = []
    for row in sorted(windows, key=lambda value: (value.on_date, value.start_minute, value.end_minute)):
        if merged and merged[-1].on_date == row.on_date and row.start_minute <= merged[-1].end_minute:
            previous = merged[-1]
            merged[-1] = NormalizedEligibleWindow(previous.on_date, previous.start_minute, max(previous.end_minute, row.end_minute))
        else:
            merged.append(NormalizedEligibleWindow(row.on_date, row.start_minute, row.end_minute))
    return tuple(merged)


def _exclude_protected_windows(windows, protected, staff_id):
    remaining = list(windows)
    for interval in protected:
        if interval.staff_id != staff_id:
            continue
        updated = []
        for window in remaining:
            if window.on_date != interval.on_date or window.end_minute <= interval.start_minute or interval.end_minute <= window.start_minute:
                updated.append(window)
                continue
            if window.start_minute < interval.start_minute:
                updated.append(NormalizedEligibleWindow(window.on_date, window.start_minute, interval.start_minute))
            if interval.end_minute < window.end_minute:
                updated.append(NormalizedEligibleWindow(window.on_date, interval.end_minute, window.end_minute))
        remaining = updated
    return tuple(remaining)


def prepare_work_periods(teaching_input, workload_input, period_input: WorkPeriodInput, *, max_intervals=10_000):
    """Validate snapshots and produce future solver inputs, never apply teaching blocks."""
    if type(max_intervals) is not int or max_intervals <= 0:
        raise ValueError("max_intervals must be a positive integer")
    if not isinstance(period_input, WorkPeriodInput):
        raise PreparationError([PreparationIssue("invalid_input", (), "Expected WorkPeriodInput.")])
    prepared = prepare_timetable_input(teaching_input)
    try:
        inputs = WorkPeriodInput.model_validate(period_input.model_dump(round_trip=True))
        workload = checked_workload_input(prepared, workload_input)
    except ValidationError as error:
        raise PreparationError([PreparationIssue(row["type"], tuple(row["loc"]), row["msg"])
                                for row in error.errors(include_input=False, include_context=False, include_url=False)]) from None
    except WorkloadInputError as error:
        raise PreparationError([PreparationIssue("invalid_workload_input", (), str(error))]) from None
    issues, diagnostics, hard, soft, flexible = [], [], [], [], []

    def issue(code, identity, message):
        issues.append(PreparationIssue(code, ("work_periods", identity), message))

    def note(code, identity, category, message):
        diagnostics.append(WorkPeriodDiagnostic(code, (identity,), category, message))

    if inputs.week_reference != workload.week_reference:
        issue("week_reference_mismatch", inputs.week_reference, "Work periods and workload evidence must reference the same academic week.")
    staff = {row.staff_id: row for row in prepared.input_snapshot.staff_members}
    profiles = {row.staff_id: row for row in workload.profiles}
    allocations = {identity: resolve_work_policy(staff[identity], row, workload.policy).administrative_allocation_minutes
                   for identity, row in profiles.items()}
    records = (inputs.appointments + inputs.administrative_resolutions + inputs.administrative_periods
               + inputs.research_policies + inputs.research_reservations + inputs.flexible_policies + inputs.flexible_requests)
    for identity in inputs.staff_institutions:
        if identity not in staff:
            issue("unknown_staff", identity, "Institution mapping references unknown staff.")
    for row in records:
        if row.staff_id not in staff:
            issue("unknown_staff", row.staff_id, "Work period or policy references unknown staff.")
        known_institution = inputs.staff_institutions.get(row.staff_id)
        if row.institution_id is not None and known_institution is not None and row.institution_id != known_institution:
            issue("institution_mismatch", row.staff_id, "Source institution differs from the supplied staff mapping.")
    appointments = {row.appointment_id: row for row in inputs.appointments}
    for row in inputs.administrative_periods + inputs.flexible_requests:
        if row.appointment_id is not None:
            appointment = appointments.get(row.appointment_id)
            if appointment is None or appointment.staff_id != row.staff_id:
                issue("invalid_appointment_reference", getattr(row, "reservation_id", getattr(row, "request_id", "")),
                      "Period must reference this staff member's known appointment, including pending records.")
    for row in inputs.administrative_resolutions:
        for identity in row.appointment_ids:
            appointment = appointments.get(identity)
            if appointment is None or appointment.staff_id != row.staff_id:
                issue("invalid_resolution_appointment", identity, "Resolution must reference this staff member's known appointments.")
            elif row.institution_id is not None and appointment.institution_id is not None and row.institution_id != appointment.institution_id:
                issue("institution_mismatch", identity, "Resolution and appointment institutions differ.")
    for first, second in combinations(inputs.administrative_resolutions, 2):
        if first.staff_id == second.staff_id and any(_active(first, day) and _active(second, day)
            for day in (inputs.week_start + timedelta(days=i) for i in range((inputs.week_end - inputs.week_start).days + 1))):
            issue("conflicting_policy_resolutions", first.staff_id, "Multiple administrative resolutions are applicable on the same date; no priority is inferred.")
    if issues:
        raise PreparationError(issues)

    def approved(record, identity):
        if record.status is PeriodApprovalStatus.APPROVED:
            return True
        category = "IGNORED" if record.status in {PeriodApprovalStatus.CANCELLED, PeriodApprovalStatus.EXPIRED} else "PENDING"
        note("reservation_" + record.status.value.lower(), identity, category, "Record is not currently approved; no protected interval is produced.")
        return False

    def institutions_match(first, second, identity):
        if first is not None and second is not None and first != second:
            issue("institution_mismatch", identity, "Reservation and authority institutions differ.")
            return False
        return True

    def administrative_authority(record, day, identity, policy_reference):
        allocation = allocations.get(record.staff_id)
        if allocation is None:
            note("unverified_admin_allocation", identity, "UNVERIFIED", "No explicit administrative allocation/profile is available.")
            return False
        appointment_id = record.appointment_id
        selected = appointments.get(appointment_id) if appointment_id is not None else None
        if appointment_id is not None:
            if selected is None or selected.staff_id != record.staff_id:
                issue("invalid_appointment_reference", identity, "Period must reference this staff member's existing appointment.")
                return False
            if selected.policy_reference != policy_reference:
                issue("appointment_policy_mismatch", identity, "Period policy reference differs from the appointment policy snapshot.")
                return False
            if not institutions_match(record.institution_id, selected.institution_id, identity):
                return False
            if not _active(selected, day) or not (selected.policy_effective_from <= day and
                    (selected.policy_effective_until is None or day <= selected.policy_effective_until)):
                note("appointment_not_applicable", identity, "UNVERIFIED", "Appointment or its policy is not applicable on the requested date.")
                return False
        elif policy_reference != workload.policy.policy_reference:
            note("unverified_admin_policy", identity, "UNVERIFIED", "Policy reference is not the supplied administrative allocation policy.")
            return False
        if profiles[record.staff_id].appointment is not None and selected is None:
            note("missing_appointment_applicability", identity, "UNVERIFIED", "The appointment override needs a dated appointment snapshot and explicit reference.")
            return False
        active_rows = [row for row in inputs.appointments if row.staff_id == record.staff_id and _active(row, day)]
        active = {row.appointment_id for row in active_rows}
        if any(not (row.policy_effective_from <= day and
                    (row.policy_effective_until is None or day <= row.policy_effective_until)) for row in active_rows):
            note("unverified_concurrent_appointment_policy", identity, "UNVERIFIED", "An active appointment has no applicable policy snapshot; reconcile it before protecting time.")
            return False
        resolutions = [row for row in inputs.administrative_resolutions if row.staff_id == record.staff_id and _active(row, day)]
        if len(active) > 1 or active and resolutions:
            resolution = resolutions[0] if len(resolutions) == 1 else None
            if (resolution is None or resolution.status is not AdministrativeAggregateStatus.COMPUTED
                    or set(resolution.appointment_ids) != active):
                note("unresolved_multiple_appointments" if len(active) > 1 else "unresolved_administrative_policy",
                     identity, "UNVERIFIED", "Administrative appointment policy needs an explicit upstream-computed resolution covering every active appointment.")
                return False
            if not institutions_match(record.institution_id, resolution.institution_id, identity):
                return False
            if resolution.allocation_minutes != allocation:
                issue("allocation_resolution_mismatch", identity, "Resolved source allocation differs from the configured workload policy; reconcile inputs explicitly.")
                return False
        return True

    def add_interval(record, day, window, index, kind, full_day=False, authority_institution=None):
        interval = NormalizedWorkInterval(
            f"{kind}:{record.reservation_id}:{day.isoformat()}:{index}", record.reservation_id, record.staff_id,
            record.institution_id or authority_institution or inputs.staff_institutions.get(record.staff_id),
            kind, day, window[0], window[1], full_day, record.protection,
            record.policy_reference, getattr(record, "appointment_id", None), record.approval_reference, record.provenance)
        (hard if record.protection is PeriodProtection.HARD else soft).append(interval)
        if len(hard) + len(soft) > max_intervals:
            issue("interval_limit_exceeded", record.reservation_id, "Explicit interval limit exceeded; no truncated preparation is returned.")
            raise PreparationError(issues)

    research_policies = {(row.staff_id, row.policy_reference): row for row in inputs.research_policies}
    for record in inputs.administrative_periods + inputs.research_reservations:
        identity = record.reservation_id
        if record.week_reference is not None and record.week_reference != inputs.week_reference:
            note("different_academic_week", identity, "IGNORED", "Record belongs to another explicitly referenced week.")
            continue
        if not approved(record, identity):
            continue
        days = _selected_dates(record, inputs)
        if not days:
            note("outside_effective_week", identity, "IGNORED", "No requested date is applicable in this academic week.")
            continue
        if record in inputs.administrative_periods:
            for day in days:
                if administrative_authority(record, day, identity, record.policy_reference):
                    authority_institution = appointments[record.appointment_id].institution_id if record.appointment_id is not None else None
                    add_interval(record, day, (record.window.start_minute, record.window.end_minute), 1, "ADMINISTRATIVE",
                                 authority_institution=authority_institution)
        else:
            policy = research_policies.get((record.staff_id, record.policy_reference))
            if policy is None or policy.staff_id != record.staff_id:
                note("unverified_research_policy", identity, "UNVERIFIED", "Research protection needs an explicit applicable staff policy; rank grants none.")
                continue
            if not approved(policy, identity) or not institutions_match(record.institution_id, policy.institution_id, identity):
                continue
            if (record.full_day and not policy.allow_full_day or not record.full_day and not policy.allow_partial_day
                    or record.protection is PeriodProtection.HARD and not policy.allow_hard_protection):
                issue("research_permission_mismatch", identity, "Requested research protection is not permitted by the explicit policy.")
                continue
            for day in days:
                if not _active(policy, day):
                    note("research_policy_not_applicable", identity, "UNVERIFIED", "Research policy is not effective on the requested date.")
                    continue
                if policy.allowed_weekdays is not None and list(Weekday)[day.weekday()] not in policy.allowed_weekdays:
                    issue("research_weekday_not_permitted", identity, "Requested weekday is not permitted by the explicit research policy.")
                    continue
                windows = [(0, 1440)] if record.full_day else [(row.start_minute, row.end_minute) for row in record.windows]
                for index, window in enumerate(windows, 1):
                    add_interval(record, day, window, index, "RESEARCH", record.full_day, policy.institution_id)

    flexible_policies = {(row.staff_id, row.policy_reference): row for row in inputs.flexible_policies}
    for record in inputs.flexible_requests:
        identity = record.request_id
        if record.week_reference is not None and record.week_reference != inputs.week_reference:
            note("different_academic_week", identity, "IGNORED", "Flexible request belongs to another academic week.")
            continue
        if not approved(record, identity):
            continue
        policy = flexible_policies.get((record.staff_id, record.policy_reference))
        if policy is None or policy.staff_id != record.staff_id:
            note("unverified_flexible_policy", identity, "UNVERIFIED", "Flexible scheduling needs an explicit permission policy.")
            continue
        if not approved(policy, identity) or not institutions_match(record.institution_id, policy.institution_id, identity):
            continue
        if not policy.flexible_scheduling_permitted or record.protection is PeriodProtection.HARD and not policy.allow_hard_protection:
            issue("flexible_scheduling_not_permitted", identity, "Explicit policy does not authorize the requested flexible scheduling/protection.")
            continue
        windows, authority_ok = [], True
        for window in record.eligible_windows:
            if not inputs.week_start <= window.on_date <= inputs.week_end or not _active(policy, window.on_date):
                note("flexible_window_outside_effective_week", identity, "IGNORED", "Eligible window is outside this week or permission policy dates.")
                continue
            if administrative_authority(record, window.on_date, identity, policy.administrative_policy_reference):
                windows.append(window)
            else:
                authority_ok = False
        if not authority_ok:
            continue
        if not windows:
            note("no_applicable_flexible_windows", identity, "UNVERIFIED", "No eligible window is applicable; no calendar window is invented.")
            continue
        if record.planned_duration_minutes > allocations[record.staff_id]:
            issue("flexible_allocation_exceeded", identity, "Requested flexible duration exceeds the explicit administrative allocation.")
            continue
        # Overlapping candidate windows are a union, not additive available work time.
        windows = _union_windows(windows)
        trimmed = _exclude_protected_windows(windows, hard, record.staff_id)
        if trimmed != windows:
            note("flexible_windows_trimmed", identity, "WARNING", "Eligible candidates exclude supplied approved hard protection; no administrative slot is selected.")
        windows = trimmed
        lengths = [row.end_minute - row.start_minute for row in windows]
        if record.allow_split:
            usable = sorted((length for length in lengths if length >= record.minimum_period_minutes), reverse=True)
            total, possible = 0, False
            for count, length in enumerate(usable, 1):
                total += length
                if count * record.minimum_period_minutes <= record.planned_duration_minutes <= total:
                    possible = True
                    break
        else:
            possible = bool(lengths) and max(lengths) >= record.planned_duration_minutes
        if not possible and policy.reservation_required is None:
            issue("insufficient_flexible_windows", identity, "Explicit eligible windows cannot contain the requested duration.")
            continue
        if not possible:
            note("insufficient_flexible_windows", identity, "WARNING",
                 "Eligible time is insufficient; booking will be infeasible if mandatory, otherwise omitted.")
        flexible.append(FlexibleAdministrativeCandidate(identity, record.staff_id,
            record.institution_id or policy.institution_id or inputs.staff_institutions.get(record.staff_id),
            record.policy_reference, record.appointment_id, record.planned_duration_minutes, record.protection,
            record.allow_split, record.minimum_period_minutes, windows, record.approval_reference, record.provenance,
            policy.reservation_required))

    for first, second in combinations(hard, 2):
        if first.staff_id == second.staff_id and first.on_date == second.on_date and first.start_minute < second.end_minute and second.start_minute < first.end_minute:
            issue("conflicting_protected_periods", first.source_id, f"Approved hard reservations {first.source_id} and {second.source_id} overlap; no precedence is inferred.")
    for first, second in combinations(hard + soft, 2):
        if (first.protection is PeriodProtection.SOFT or second.protection is PeriodProtection.SOFT) and first.staff_id == second.staff_id and first.on_date == second.on_date and first.start_minute < second.end_minute and second.start_minute < first.end_minute:
            diagnostics.append(WorkPeriodDiagnostic("soft_preference_overlap", (first.source_id, second.source_id), "WARNING",
                                                    "Overlapping soft preference is retained separately and never becomes a hard restriction."))
    for record in inputs.administrative_periods + inputs.research_reservations:
        if record.status not in {PeriodApprovalStatus.PENDING, PeriodApprovalStatus.PROPOSED}:
            continue
        if record.week_reference is not None and record.week_reference != inputs.week_reference:
            continue
        windows = ([(record.window.start_minute, record.window.end_minute)] if record in inputs.administrative_periods else
                   [(0, 1440)] if record.full_day else [(row.start_minute, row.end_minute) for row in record.windows])
        for interval in hard:
            if interval.staff_id == record.staff_id and interval.on_date in _selected_dates(record, inputs) and any(
                start < interval.end_minute and interval.start_minute < end for start, end in windows
            ):
                diagnostics.append(WorkPeriodDiagnostic("pending_reservation_conflict", (record.reservation_id, interval.source_id),
                    "WARNING", "Pending reservation overlaps approved protection; it is reported without creating a restriction."))
    if any(row.work_date is None for row in workload.recognized_work):
        note("untimed_work_evidence", workload.week_reference, "UNVERIFIED", "Legacy untimed work credits are preserved; their temporal non-overlap cannot be independently established.")
    if any(row.work_date is not None and not inputs.week_start <= row.work_date <= inputs.week_end for row in workload.recognized_work):
        issue("work_evidence_outside_week", workload.week_reference, "Timed work evidence lies outside the explicitly assessed week.")
    if issues:
        raise PreparationError(issues)
    order = lambda row: (row.staff_id, row.on_date, row.start_minute, row.end_minute, row.interval_id)
    return PreparedWorkPeriods(inputs, workload, tuple(sorted(hard, key=order)), tuple(sorted(soft, key=order)),
                              tuple(sorted(flexible, key=lambda row: row.request_id)),
                              tuple(sorted(set(diagnostics), key=lambda row: (row.category, row.code, row.source_ids))))
