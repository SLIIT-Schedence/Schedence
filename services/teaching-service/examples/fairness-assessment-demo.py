"""Synthetic post-solve fairness demonstration; no source datasets or database."""

import json
from datetime import date

from schedence_teaching.fairness import FairnessPolicy, assess_fairness, validate_fairness_report
from schedence_teaching.preferences import PreferenceCalendar
from schedence_teaching.preparation import prepare_timetable_input
from schedence_teaching.schemas import TeachingTimetableInput
from schedence_teaching.solver import SolverConfig, StaffSchedulingInput, solve_timetable
from schedence_teaching.workload import WeeklyWorkloadInput, confirmed_campus_policy


def main():
    first, last = date(2026, 10, 19), date(2026, 10, 25)
    roles = [("SYNTHETIC-L", "LECTURER", 720), ("SYNTHETIC-D", "DR", 360), ("SYNTHETIC-P", "PROFESSOR", 360)]
    prepared = prepare_timetable_input(TeachingTimetableInput(
        staff_members=[dict(staff_id=identity, staff_name=identity, position=band, staff_type="ACADEMIC")
                       for identity, band, _ in roles],
        module_offerings=[dict(offering_id=f"O-{index}", module_code=f"SYN-{index}", module_name="Synthetic content",
            academic_year=2, semester=1, specialization="Synthetic", degree_type="Synthetic", stream="WEEKDAY")
            for index in range(3)],
        teaching_team_members=[dict(offering_id=f"O-{index}", staff_id=identity, role="LIC")
                               for index, (identity, _, _) in enumerate(roles)],
        student_groups=[dict(group_id=f"G-{index}", academic_year=2, semester=1, stream="WEEKDAY", student_count=20)
                        for index in range(3)],
        session_requirements=[dict(requirement_id=f"R-{index}", offering_id=f"O-{index}", group_id=f"G-{index}",
            session_type="LECTURE", duration_minutes=30, sessions_per_week=minutes // 30)
            for index, (_, _, minutes) in enumerate(roles)]))
    config = SolverConfig(days=[dict(day="MONDAY", windows={"WEEKDAY": [dict(start_minute=0, end_minute=720)]})],
        granularity_minutes=15, max_time_seconds=5, random_seed=7)
    workload = WeeklyWorkloadInput(week_reference="SYNTHETIC-WEEK", policy=confirmed_campus_policy(),
        profiles=[dict(staff_id=identity, workload_band=band, evidence_reference="synthetic:classification")
                  for identity, band, _ in roles])
    staff = StaffSchedulingInput(workload_input=workload, day_dates={"MONDAY": first},
        policy_effective_from=first, policy_effective_until=last,
        availability=[dict(staff_id=identity, allowed_teaching_days={"MONDAY"},
            available_windows={"MONDAY": [dict(start_minute=0, end_minute=720)]}, blocked_periods=[],
            effective_from=first, effective_until=last) for identity, _, _ in roles])
    calendar = PreferenceCalendar(week_reference=workload.week_reference, week_start=first, week_end=last,
        day_dates=staff.day_dates, synthetic_data=True)
    policy = FairnessPolicy(policy_reference="synthetic:fairness-example", week_reference=workload.week_reference,
        provenance=dict(source="synthetic", reference="demonstration-only-not-campus-fairness-policy"),
        comparison_scope="EXPLICIT_GROUPS", include_variance=True,
        comparison_groups=[dict(group_id="SYNTHETIC-COMPARISON", staff_ids=[row[0] for row in roles],
                                reason="Explicit demonstration of role normalization across distinct eligibility pools")])
    result = solve_timetable(prepared, config, staff_input=staff)
    report = assess_fairness(prepared, config, result, calendar, policy, staff_input=staff)
    validate_fairness_report(report, prepared, config, result, calendar, policy, staff_input=staff)
    for row in report.staff:
        print(f"synthetic {row.workload_band}: {row.teaching.scheduled_teaching_minutes}/{row.target.target_minutes} = {row.target.utilization}")
    print(json.dumps(report.to_dict(), indent=2))


if __name__ == "__main__":
    main()
