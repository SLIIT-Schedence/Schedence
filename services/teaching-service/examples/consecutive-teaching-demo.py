"""Synthetic consecutive teaching assessment; no optimization or campus break rule."""

from datetime import date
import json

from schedence_teaching.consecutive import ConsecutivePolicy, assess_consecutive_sessions, validate_consecutive_report
from schedence_teaching.preferences import PreferenceCalendar
from schedence_teaching.preparation import prepare_timetable_input
from schedence_teaching.schemas import TeachingTimetableInput
from schedence_teaching.solver import SolverConfig, solve_timetable


def main():
    prepared = prepare_timetable_input(TeachingTimetableInput(
        staff_members=[dict(staff_id="SYNTHETIC-STAFF", staff_name="Synthetic staff", position="Synthetic", staff_type="ACADEMIC")],
        module_offerings=[dict(offering_id="SYNTHETIC-OFFERING", module_code="SYNTHETIC", module_name="Synthetic content",
            academic_year=2, semester=1, specialization="Synthetic", degree_type="Synthetic", stream="WEEKDAY")],
        teaching_team_members=[dict(offering_id="SYNTHETIC-OFFERING", staff_id="SYNTHETIC-STAFF", role="LIC")],
        student_groups=[dict(group_id="SYNTHETIC-GROUP", academic_year=2, semester=1, stream="WEEKDAY", student_count=20)],
        session_requirements=[dict(requirement_id="SYNTHETIC-REQ", offering_id="SYNTHETIC-OFFERING", group_id="SYNTHETIC-GROUP",
            session_type="LECTURE", duration_minutes=30, sessions_per_week=3)]))
    config = SolverConfig(days=[dict(day="MONDAY", windows={"WEEKDAY": [dict(start_minute=540, end_minute=630)]})],
        granularity_minutes=15, max_time_seconds=5, random_seed=7)
    calendar = PreferenceCalendar(week_reference="SYNTHETIC-WEEK", week_start=date(2026, 10, 19),
        week_end=date(2026, 10, 25), day_dates={"MONDAY": date(2026, 10, 19)}, synthetic_data=True)
    policy = ConsecutivePolicy(policy_reference="synthetic:consecutive-example", week_reference=calendar.week_reference,
        provenance=dict(source="synthetic", reference="example-only-not-campus-policy"),
        soft_criteria={"BACK_TO_BACK", "SHORT_BREAK", "EXCESS_CONTINUOUS_MINUTES"},
        preferred_minimum_break_minutes=15, maximum_preferred_continuous_minutes=60,
        back_to_back_penalty=5, short_break_minute_penalty=2, excess_continuous_minute_penalty=3)
    result = solve_timetable(prepared, config)
    report = assess_consecutive_sessions(prepared, config, result, calendar, policy)
    validate_consecutive_report(report, prepared, config, result, calendar, policy)
    row = report.lecturers[0]
    output = {"synthetic": True, "solver_status": result.status.value,
        "teaching_intervals": [[item.start_minute, item.end_minute] for item in sorted(result.schedule, key=lambda item: item.start_minute)],
        "penalty_components": dict(row.penalty_components),
        **report.to_dict(include_lecturer_details=True, include_student_group_details=True)}
    print(json.dumps(output, indent=2))
    return output


if __name__ == "__main__":
    main()
