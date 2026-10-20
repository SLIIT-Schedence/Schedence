"""Executable Step 9B demonstrations. All identifiers, dates and windows are synthetic."""

from datetime import date
import json
from pathlib import Path

from schedence_teaching.preparation import prepare_group_combinations
from schedence_teaching.schemas import GroupCombinationInput
from schedence_teaching.solver import (
    SchedulingDay, SolverConfig, StaffAvailability, StaffSchedulingInput, TimeWindow,
    WorkPeriodSolverConfig, solve_timetable, validate_work_period_result,
)
from schedence_teaching.workload import (
    AdministrativeTimeReservation, FlexibleAdministrativePolicy, FlexibleAdministrativeRequest,
    ResearchDayPolicy, ResearchDayReservation, StaffWorkProfile, WeeklyWorkloadInput,
    WorkPeriodInput, confirmed_campus_policy, prepare_work_periods,
)


def demonstrate(label, *, flexible=False, infeasible=False):
    source = json.loads(Path(__file__).with_name("group-combination-input.example.json").read_text(encoding="utf-8"))
    for row in source["teaching_input"]["session_requirements"]:
        row["sessions_per_week"] = 3
    combinations = prepare_group_combinations(GroupCombinationInput.model_validate(source))
    first, last = date(2026, 10, 19), date(2026, 10, 25)
    staff_id = "SYNTHETIC-STAFF"
    config = SolverConfig(days=(SchedulingDay(day="MONDAY", windows={"WEEKDAY": [
        TimeWindow(start_minute=0, end_minute=480 if infeasible else 660)]}),),
        granularity_minutes=30, max_time_seconds=5.0, random_seed=7)
    workload = WeeklyWorkloadInput(week_reference="synthetic:demo-week", policy=confirmed_campus_policy(),
        profiles=[StaffWorkProfile(staff_id=staff_id, workload_band="PROFESSOR", evidence_reference="synthetic:classification")])
    staff = StaffSchedulingInput(workload_input=workload, day_dates={"MONDAY": first},
        policy_effective_from=first, policy_effective_until=last,
        availability=[StaffAvailability(staff_id=staff_id, allowed_teaching_days={"MONDAY"},
            available_windows={"MONDAY": [TimeWindow(start_minute=0, end_minute=660)]},
            blocked_periods=[], effective_from=first, effective_until=last)])
    options = WorkPeriodSolverConfig(day_weekdays={"MONDAY": "MONDAY"}, day_streams={"MONDAY": {"WEEKDAY"}})
    provenance = {"source": "synthetic-demo", "reference": "synthetic:source"}
    auth = {"staff_id": staff_id, "status": "APPROVED", "approval_reference": "synthetic:approval",
            "provenance": provenance, "effective_from": first, "effective_until": last}
    periods = WorkPeriodInput(week_reference=workload.week_reference, week_start=first, week_end=last)
    periods.research_policies = [ResearchDayPolicy(**auth, policy_reference="synthetic:research",
        allow_full_day=True, allow_partial_day=True, allow_hard_protection=True)]
    periods.research_reservations = [ResearchDayReservation(**auth, policy_reference="synthetic:research",
        reservation_id="SYNTHETIC-RESEARCH", on_date=first, full_day=False,
        windows=[TimeWindow(start_minute=0, end_minute=60)], protection="HARD")]
    if flexible:
        periods.flexible_policies = [FlexibleAdministrativePolicy(**auth, policy_reference="synthetic:flex",
            administrative_policy_reference=workload.policy.policy_reference,
            flexible_scheduling_permitted=True, allow_hard_protection=True, reservation_required=True)]
        periods.flexible_requests = [FlexibleAdministrativeRequest(**auth, policy_reference="synthetic:flex",
            request_id="SYNTHETIC-FLEX", planned_duration_minutes=240, protection="HARD", allow_split=False,
            eligible_windows=[{"on_date": first, "start_minute": 60, "end_minute": 480 if infeasible else 660}])]
    else:
        periods.administrative_periods = [AdministrativeTimeReservation(**auth,
            policy_reference=workload.policy.policy_reference, reservation_id="SYNTHETIC-ADMIN",
            on_date=first, window=TimeWindow(start_minute=60, end_minute=300),
            planned_duration_minutes=240, protection="HARD")]
    prepared_periods = prepare_work_periods(combinations.input_snapshot.teaching_input, workload, periods)
    inputs = {"combination_input": combinations, "staff_input": staff,
              "work_periods": prepared_periods, "work_period_config": options}
    result = solve_timetable(combinations.prepared, config, **inputs)
    validate_work_period_result(combinations.prepared, config, result, **inputs)
    row = result.work_period_report.staff[0].to_dict() if result.work_period_report else None
    print(json.dumps({"example": label, "status": result.status.value,
        "teaching": [{"day": item.day, "start_minute": item.start_minute, "end_minute": item.end_minute,
                      "staff_id": item.assigned_staff_id, "venue_id": item.assigned_venue_id,
                      "covered_occurrences": [row.occurrence_id for row in item.covered_sessions]}
                     for item in result.schedule],
        "flexible_administration": [item.to_dict() for item in result.administrative_schedule],
        "fixed_administration": [row.to_dict() for row in result.work_period_report.fixed_administrative_periods] if result.work_period_report else [],
        "research": [row.to_dict() for row in result.work_period_report.research_periods] if result.work_period_report else [],
        "staff_summary": row, "objective_value": result.objective_value,
        "objective_status": result.objective_status, "runtime_seconds": result.runtime_seconds,
        "independently_validated": True, "university_ready": False}, indent=2))
    return result


def main():
    demonstrate("hard-protection")
    demonstrate("flexible-administration", flexible=True)
    demonstrate("infeasible-hard-protection", infeasible=True)
    demonstrate("infeasible-mandatory-flexible", flexible=True, infeasible=True)


if __name__ == "__main__":
    main()
