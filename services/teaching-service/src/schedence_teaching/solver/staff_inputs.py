"""Dated staff teaching calendars plus the existing campus workload contract."""

from datetime import date
from typing import Annotated, Self

from pydantic import Field, StrictBool, model_validator, model_serializer

from schedence_teaching.schemas.models import InputContract, RequiredText
from schedence_teaching.workload.contracts import WeeklyWorkloadInput
from schedence_teaching.workload.assessment import WorkloadInputError, checked_workload_input

from .config import TimeWindow
from .eligibility import candidate_pools
from .results import SolverInputError
from .teaching_policy import TeachingAssignmentPolicy, TeachingDays


TeachingMaximum = Annotated[int, Field(strict=True, ge=0, le=2**60)]


class StaffBlockedInterval(TimeWindow):
    day: RequiredText


class StaffAvailability(InputContract):
    staff_id: RequiredText
    allowed_teaching_days: set[RequiredText]
    available_windows: dict[RequiredText, list[TimeWindow]]
    blocked_periods: list[StaffBlockedInterval]
    effective_from: date
    effective_until: date
    daily_teaching_max_minutes: TeachingMaximum | None = None
    weekly_teaching_max_minutes: TeachingMaximum | None = None
    weekly_teaching_max_days: TeachingDays | None = None

    @model_serializer(mode='wrap')
    def preserve_legacy_payload(self, handler):
        result=handler(self)
        if self.weekly_teaching_max_days is None:result.pop('weekly_teaching_max_days',None)
        return result

    @model_validator(mode="after")
    def explicit_calendar(self) -> Self:
        if self.effective_until < self.effective_from:
            raise ValueError("availability effective_until must not precede effective_from")
        if set(self.available_windows) != self.allowed_teaching_days:
            raise ValueError("every allowed teaching day needs explicit windows (or []); disallowed days must not have windows")
        return self


class StaffSchedulingInput(InputContract):
    workload_input: WeeklyWorkloadInput
    day_dates: dict[RequiredText, date]
    policy_effective_from: date
    policy_effective_until: date
    availability: list[StaffAvailability]
    # Explicit PP1 escape hatch for appointment-only source titles. Availability
    # and supplied caps/protection still apply; missing ranks are never fabricated.
    allow_unclassified_staff_for_testing: StrictBool = False
    # None preserves the original, explicitly historical snapshot semantics.
    assignment_policy: TeachingAssignmentPolicy | None = None

    @model_serializer(mode='wrap')
    def preserve_legacy_payload(self, handler):
        result=handler(self)
        if self.assignment_policy is None:result.pop('assignment_policy',None)
        return result

    @model_validator(mode="after")
    def dates_and_records(self) -> Self:
        if self.policy_effective_until < self.policy_effective_from:
            raise ValueError("policy effective_until must not precede effective_from")
        dates = list(self.day_dates.values())
        if len(set(dates)) != len(dates):
            raise ValueError("scheduling days must map to distinct dates")
        if dates and (max(dates) - min(dates)).days > 6:
            raise ValueError("weekly scheduling dates must fit within seven consecutive calendar days")
        if any(not self.policy_effective_from <= day <= self.policy_effective_until for day in dates):
            raise ValueError("workload policy must be applicable to every scheduling date")
        staff_ids = [row.staff_id for row in self.availability]
        if len(set(staff_ids)) != len(staff_ids):
            raise ValueError("duplicate staff availability")
        return self


def checked_staff_inputs(prepared, config, inputs):
    if not isinstance(inputs, StaffSchedulingInput):
        raise SolverInputError("Staff policy mode requires StaffSchedulingInput.")
    # Use sanitized Pydantic errors; never echo personal input records.
    from pydantic import ValidationError
    try:
        inputs = StaffSchedulingInput.model_validate(inputs.model_dump(round_trip=True))
        checked_workload_input(prepared, inputs.workload_input)
    except ValidationError as error:
        raise SolverInputError("; ".join(row["msg"] for row in error.errors(
            include_input=False, include_context=False, include_url=False))) from None
    except WorkloadInputError as error:
        raise SolverInputError(str(error)) from None
    days = {row.day for row in config.days}
    if inputs.workload_input.policy.staff_overrides and config.resolved_policy is None:
        raise SolverInputError('Staff workload overrides require a resolved institution policy.')
    from schedence_teaching.policy.resolution import verify_component
    try:
        verify_component(config, 'staff_input', inputs)
    except ValueError as error:
        raise SolverInputError(str(error)) from None
    if set(inputs.day_dates) != days:
        raise SolverInputError("Policy mode requires an explicit date for every configured scheduling day.")
    staff_ids = {row.staff_id for row in prepared.input_snapshot.staff_members}
    availability = {row.staff_id: row for row in inputs.availability}
    if availability.keys() - staff_ids:
        raise SolverInputError("Availability references unknown staff IDs.")
    if inputs.assignment_policy is not None:
        policy=inputs.assignment_policy
        if policy.week_reference!=inputs.workload_input.week_reference:
            raise SolverInputError('Assignment policy must use the applicable academic week.')
        offerings={m.offering_id for m in prepared.input_snapshot.module_offerings}
        if (set(policy.staff_teaching_day_limits)-staff_ids or
                any(p.staff_id not in staff_ids or p.offering_id is not None and p.offering_id not in offerings for p in policy.permissions) or
                any(p.staff_id not in staff_ids for p in policy.active_appointments)):
            raise SolverInputError('Assignment policy references unknown staff or offerings.')
    pools = candidate_pools(prepared.input_snapshot)
    required = {staff_id for row in prepared.sessions for staff_id in pools[row.offering_id]}
    if required - availability.keys():
        raise SolverInputError("Missing required staff availability for eligible teaching staff.")
    if (required - {row.staff_id for row in inputs.workload_input.profiles}
            and not inputs.allow_unclassified_staff_for_testing):
        raise SolverInputError("Missing explicit workload profiles for eligible teaching staff.")
    for row in inputs.availability:
        if row.allowed_teaching_days - days or any(block.day not in days for block in row.blocked_periods):
            raise SolverInputError("Staff calendar references an unknown scheduling day.")
    return inputs
