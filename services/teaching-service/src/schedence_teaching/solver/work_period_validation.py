"""Independent calendar, protection, reservation and evidence checks in source minutes."""

import json
from collections import defaultdict
from datetime import date
from itertools import combinations
from math import isfinite

from schedence_teaching.workload.assessment import _assess_validated_schedule

from .inputs import checked_inputs
from .results import ScheduleIssue, SolverResult, SolverStatus, TimetableValidationError
from .staff_inputs import checked_staff_inputs
from .work_period_inputs import checked_work_period_inputs
from .work_period_results import ScheduledAdministrativePeriod, WorkPeriodReport, work_period_report


def validate_work_period_outputs(prepared, config, result, *, staff_input, work_periods=None,
                                 work_period_config=None):
    """Called after existing teaching/venue/coverage checks; never consult model builders."""
    issues = []
    if type(result.work_period_enabled) is not bool or result.work_period_enabled != (work_periods is not None):
        raise TimetableValidationError([ScheduleIssue("work_period_mode_changed", "Work-period validation requires original mode and source inputs.")])
    if work_periods is None:
        if work_period_config is not None or result.administrative_schedule != () or result.work_period_report is not None:
            raise TimetableValidationError([ScheduleIssue("unexpected_work_period_output", "Administrative bookings/reports require original work-period inputs.")])
        return
    periods, _ = checked_work_period_inputs(prepared, config, staff_input, work_periods, work_period_config)
    if (type(result.runtime_seconds) not in {int, float} or not isfinite(result.runtime_seconds)
            or result.runtime_seconds < 0 or result.failure_reason is not None):
        issues.append(ScheduleIssue("work_period_result_metadata", "Successful result needs a finite runtime and no failure reason."))
    if type(result.administrative_schedule) is not tuple:
        raise TimetableValidationError([ScheduleIssue("invalid_admin_schedule", "Administrative schedule must be a tuple of physical bookings.")])
    candidates = {row.request_id: row for row in periods.flexible_candidates}
    grouped, identifiers = defaultdict(list), set()
    commitments = defaultdict(list)
    for row in periods.protected_intervals:
        commitments[(row.staff_id, row.on_date)].append((row.start_minute, row.end_minute, "fixed:" + row.interval_id))
    for row in result.schedule:
        on_date = staff_input.day_dates[row.day]
        for fixed in periods.protected_intervals:
            if (row.assigned_staff_id == fixed.staff_id and on_date == fixed.on_date
                    and row.start_minute < fixed.end_minute and fixed.start_minute < row.end_minute):
                issues.append(ScheduleIssue("teaching_protected_period", "Selected teaching overlaps approved hard administrative/research protection."))
        commitments[(row.assigned_staff_id, on_date)].append((row.start_minute, row.end_minute, "teaching"))
    for row in result.administrative_schedule:
        if not isinstance(row, ScheduledAdministrativePeriod) or type(row.request_id) is not str or row.request_id not in candidates:
            issues.append(ScheduleIssue("unauthorized_administrative_booking", "Booking does not reference an approved prepared flexible request."))
            continue
        source = candidates[row.request_id]
        if (type(row.block_id) is not str or type(row.day) is not str or row.day not in staff_input.day_dates
                or type(row.on_date) is not date or row.on_date != staff_input.day_dates.get(row.day)
                or type(row.start_minute) is not int or type(row.end_minute) is not int
                or not 0 <= row.start_minute < row.end_minute <= 1440
                or row.start_minute % config.granularity_minutes or row.end_minute % config.granularity_minutes):
            issues.append(ScheduleIssue("invalid_administrative_time", "Booking needs an explicitly mapped date/day and valid grid-aligned interval."))
            continue
        metadata = ("staff_id", "institution_id", "policy_reference", "appointment_id", "protection", "approval_reference", "provenance")
        if any(type(getattr(row, key)) is not type(getattr(source, key)) or getattr(row, key) != getattr(source, key) for key in metadata):
            issues.append(ScheduleIssue("administrative_metadata_changed", "Booking must preserve staff, authorization, institution and provenance."))
            continue
        if row.block_id in identifiers:
            issues.append(ScheduleIssue("duplicate_administrative_block", "Each physical administrative booking must be unique."))
        identifiers.add(row.block_id)
        if not any(window.on_date == row.on_date and window.start_minute <= row.start_minute and row.end_minute <= window.end_minute
                   for window in source.eligible_windows):
            issues.append(ScheduleIssue("outside_administrative_windows", "Booking must fit one approved, effective eligible window."))
        if source.allow_split and row.end_minute - row.start_minute < source.minimum_period_minutes:
            issues.append(ScheduleIssue("administrative_minimum_block", "Administrative fragment is smaller than the approved minimum."))
        grouped[row.request_id].append(row)
        commitments[(row.staff_id, row.on_date)].append((row.start_minute, row.end_minute, "flexible:" + row.block_id))
    for identity, source in candidates.items():
        selected = sorted(grouped[identity], key=lambda row: (row.on_date, row.start_minute, row.end_minute))
        if source.reservation_required and not selected:
            issues.append(ScheduleIssue("missing_mandatory_administration", "Mandatory approved flexible request has no scheduled reservation."))
        if not selected:
            continue
        if sum(row.end_minute - row.start_minute for row in selected) != source.planned_duration_minutes:
            issues.append(ScheduleIssue("administrative_duration", "Selected request must reserve exactly its approved duration; optional requests are all-or-none."))
        if not source.allow_split and len(selected) != 1:
            issues.append(ScheduleIssue("unauthorized_administrative_split", "Continuous reservation cannot be fragmented."))
        if any(row.block_id != f"administrative:{identity}:{index}" for index, row in enumerate(selected, 1)):
            issues.append(ScheduleIssue("administrative_block_identity", "Block identifiers must use the request and chronological occurrence ordinal."))
    for rows in commitments.values():
        for first, second in combinations(rows, 2):
            # Teaching/teaching already checked by existing global resource validators.
            if first[2] == second[2] == "teaching":
                continue
            if first[0] < second[1] and second[0] < first[1]:
                issues.append(ScheduleIssue("work_period_staff_clash", "Teaching/admin/research commitments overlap for one staff member."))
    if issues:
        raise TimetableValidationError(issues)
    canonical_workload = _assess_validated_schedule(prepared, result, staff_input.workload_input, staff_constraints_enforced=True)
    canonical_report = work_period_report(periods, result.administrative_schedule, canonical_workload)
    try:
        matches = (isinstance(result.work_period_report, WorkPeriodReport)
                   and json.dumps(result.work_period_report.to_dict(), sort_keys=True) == json.dumps(canonical_report.to_dict(), sort_keys=True))
    except (AttributeError, TypeError, ValueError):
        matches = False
    if not matches:
        raise TimetableValidationError([ScheduleIssue("work_period_summary_changed", "Protected sources, reserved duration, teaching totals or completion/attendance evidence differ from independent recomputation.")])


def validate_work_period_result(prepared, config, result, *, staff_input, work_periods,
                                work_period_config, venue_input=None, combination_input=None,
                                combination_config=None, preference_input=None, preference_calendar=None,
                                preference_policy=None, preference_config=None,
                                fairness_policy=None, fairness_calendar=None, fairness_config=None,
                                consecutive_policy=None, consecutive_calendar=None, consecutive_config=None,
        student_lunch_policy=None, student_lunch_calendar=None, student_lunch_config=None):
    """Full public result validator, including empty failure outputs and original resources."""
    from .lunch_validation import student_lunch_validation_requested, validate_student_lunch_result
    if student_lunch_validation_requested(result, student_lunch_policy, student_lunch_calendar, student_lunch_config):
        return validate_student_lunch_result(prepared, config, result, student_lunch_policy=student_lunch_policy,
            student_lunch_calendar=student_lunch_calendar, student_lunch_config=student_lunch_config,
            venue_input=venue_input, combination_input=combination_input, combination_config=combination_config,
            staff_input=staff_input, work_periods=work_periods, work_period_config=work_period_config,
            preference_input=preference_input, preference_calendar=preference_calendar, preference_policy=preference_policy,
            preference_config=preference_config, fairness_policy=fairness_policy, fairness_calendar=fairness_calendar,
            fairness_config=fairness_config, consecutive_policy=consecutive_policy, consecutive_calendar=consecutive_calendar,
            consecutive_config=consecutive_config)
    from .consecutive_validation import consecutive_validation_requested, validate_consecutive_result
    if consecutive_validation_requested(result, consecutive_policy, consecutive_calendar, consecutive_config):
        return validate_consecutive_result(prepared, config, result, consecutive_policy=consecutive_policy,
            consecutive_calendar=consecutive_calendar, consecutive_config=consecutive_config,
            venue_input=venue_input, combination_input=combination_input, combination_config=combination_config,
            staff_input=staff_input, work_periods=work_periods, work_period_config=work_period_config,
            preference_input=preference_input, preference_calendar=preference_calendar,
            preference_policy=preference_policy, preference_config=preference_config,
            fairness_policy=fairness_policy, fairness_calendar=fairness_calendar, fairness_config=fairness_config)
    from .fairness_validation import fairness_validation_requested, validate_fairness_result
    if fairness_validation_requested(result, fairness_policy, fairness_calendar, fairness_config):
        return validate_fairness_result(prepared, config, result, fairness_policy=fairness_policy,
            fairness_calendar=fairness_calendar, fairness_config=fairness_config,
            venue_input=venue_input, combination_input=combination_input, combination_config=combination_config,
            staff_input=staff_input, work_periods=work_periods, work_period_config=work_period_config,
            preference_input=preference_input, preference_calendar=preference_calendar,
            preference_policy=preference_policy, preference_config=preference_config)
    from .staff_validation import validate_staff_policy_result
    from .deliveries import checked_combinations
    from .venue_inputs import checked_venue_inputs
    from .results import SolverInputError

    prepared, config = checked_inputs(prepared, config)
    staff_input = checked_staff_inputs(prepared, config, staff_input)
    checked_work_period_inputs(prepared, config, staff_input, work_periods, work_period_config)
    if combination_config is not None and combination_input is None:
        raise SolverInputError("Combination config requires original combination input.")
    objective_mode = None
    if combination_input is not None:
        _, options = checked_combinations(prepared, combination_input, combination_config)
        objective_mode = options.objective.value
    if venue_input is not None:
        checked_venue_inputs(prepared, config, venue_input)
    if not isinstance(result, SolverResult) or type(result.status) is not SolverStatus:
        raise TimetableValidationError([ScheduleIssue("invalid_result", "Expected a typed SolverResult.")])
    if result.status not in {SolverStatus.OPTIMAL, SolverStatus.FEASIBLE}:
        if result.preference_optimization_enabled or any(row is not None for row in
            (preference_input, preference_calendar, preference_policy, preference_config, result.preference_report)):
            from .preference_validation import validate_preference_result
            return validate_preference_result(prepared, config, result, staff_input=staff_input,
                work_periods=work_periods, work_period_config=work_period_config, venue_input=venue_input,
                combination_input=combination_input, combination_config=combination_config, preference_input=preference_input,
                preference_calendar=preference_calendar, preference_policy=preference_policy, preference_config=preference_config)
        if (result.schedule != () or result.administrative_schedule != () or result.validation is not None
                or result.workload_report is not None or result.work_period_report is not None
                or result.objective_value is not None or result.objective_status is not None
                or result.work_period_enabled is not True or result.staff_policy_enabled is not True
                or result.combination_enabled is not (combination_input is not None)
                or result.venue_enabled is not (venue_input is not None)
                or type(result.original_occurrence_count) is not int or result.original_occurrence_count != len(prepared.sessions)
                or result.objective_mode != objective_mode or type(result.failure_reason) is not str
                or not result.failure_reason.strip() or type(result.runtime_seconds) not in {int, float}
                or not isfinite(result.runtime_seconds) or result.runtime_seconds < 0):
            raise TimetableValidationError([ScheduleIssue("partial_failure_output", "Unsuccessful runs must emit no timetable, administrative bookings, fulfillment report or objective value.")])
        return None
    return validate_staff_policy_result(prepared, config, result, staff_input=staff_input,
                                        venue_input=venue_input, combination_input=combination_input,
                                        combination_config=combination_config, work_periods=work_periods,
                                        work_period_config=work_period_config, preference_input=preference_input,
                                        preference_calendar=preference_calendar, preference_policy=preference_policy,
                                        preference_config=preference_config)
