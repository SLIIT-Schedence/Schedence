"""Synthetic native before/after comparison; no campus break policy is asserted."""

from datetime import date
import json

from schedence_teaching.consecutive import ConsecutivePolicy, assess_consecutive_sessions
from schedence_teaching.fairness import FairnessPolicy
from schedence_teaching.preferences import (
    LecturerPreferenceInput, PreferenceCalendar, PreferenceScoringPolicy, prepare_lecturer_preferences,
)
from schedence_teaching.preparation import prepare_timetable_input
from schedence_teaching.schemas import TeachingTimetableInput
from schedence_teaching.solver import (
    ConsecutiveOptimizationConfig, FairnessOptimizationConfig, PreferenceOptimizationConfig,
    SolverConfig, StaffSchedulingInput, solve_timetable, validate_consecutive_result,
)
from schedence_teaching.workload import WeeklyWorkloadInput, confirmed_campus_policy


def main():
    first, last, week = date(2026, 10, 19), date(2026, 10, 25), "SYNTHETIC-WEEK"
    prepared = prepare_timetable_input(TeachingTimetableInput(
        staff_members=[dict(staff_id="SYNTHETIC-P", staff_name="Synthetic Professor", position="Professor", staff_type="ACADEMIC")],
        module_offerings=[dict(offering_id="SYNTHETIC-OFFER", module_code="SYNTHETIC-CODE", module_name="Example only",
            academic_year=2, semester=1, specialization="Synthetic", degree_type="Synthetic", stream="WEEKDAY")],
        teaching_team_members=[dict(offering_id="SYNTHETIC-OFFER", staff_id="SYNTHETIC-P", role="LIC")],
        student_groups=[dict(group_id="SYNTHETIC-GROUP", academic_year=2, semester=1, stream="WEEKDAY", student_count=20)],
        session_requirements=[dict(requirement_id="SYNTHETIC-REQ", offering_id="SYNTHETIC-OFFER", group_id="SYNTHETIC-GROUP",
            session_type="LECTURE", duration_minutes=30, sessions_per_week=3)]))
    config = SolverConfig(days=[dict(day="MONDAY", windows={"WEEKDAY": [dict(start_minute=0, end_minute=120)]})],
        granularity_minutes=15, max_time_seconds=5, random_seed=7)
    workload = WeeklyWorkloadInput(week_reference=week, policy=confirmed_campus_policy(), profiles=[
        dict(staff_id="SYNTHETIC-P", workload_band="PROFESSOR", evidence_reference="synthetic:classification")])
    staff = StaffSchedulingInput(workload_input=workload, day_dates={"MONDAY": first},
        policy_effective_from=first, policy_effective_until=last, availability=[dict(staff_id="SYNTHETIC-P",
            allowed_teaching_days={"MONDAY"}, available_windows={"MONDAY": [dict(start_minute=0, end_minute=120)]},
            blocked_periods=[], effective_from=first, effective_until=last)])
    calendar = PreferenceCalendar(week_reference=week, week_start=first, week_end=last,
        day_dates=staff.day_dates, synthetic_data=True)
    preferences = prepare_lecturer_preferences(prepared.input_snapshot, LecturerPreferenceInput(preferences=[
        dict(preference_id="SYNTHETIC-DAY", staff_id="SYNTHETIC-P", category="WORKING_DAY", direction="PREFER",
            days={"MONDAY"}, scope=dict(week_reference=week), confirmation_status="CONFIRMED", penalty_weight=1,
            weight_reference="synthetic:test-weight", provenance=dict(source="synthetic", reference="example-only"))]))
    original = dict(staff_input=staff, preference_input=preferences, preference_calendar=calendar,
        preference_policy=PreferenceScoringPolicy(policy_reference="synthetic:preferences", weight_mode="CONFIRMED_WEIGHTS"),
        preference_config=PreferenceOptimizationConfig(priority_policy_reference="synthetic:existing-order",
            calendar_semantics_reference="synthetic:explicit-map", day_streams={"MONDAY": {"WEEKDAY"}},
            unscoped_preferences_apply_to_all_streams=True),
        fairness_policy=FairnessPolicy(policy_reference="synthetic:fairness", week_reference=week,
            provenance=dict(source="synthetic", reference="example-only"), comparison_scope="EXPLICIT_GROUPS",
            comparison_groups=[dict(group_id="SYNTHETIC-COHORT", staff_ids=["SYNTHETIC-P"], reason="Example only")]),
        fairness_calendar=calendar, fairness_config=FairnessOptimizationConfig(priority="EXISTING_OBJECTIVES_THEN_FAIRNESS",
            priority_policy_reference="synthetic:fairness-priority", cohort_rule="INPUT_ELIGIBILITY_AND_FIXED_CALENDAR",
            deviation_reference="TEACHING_TARGET"))
    policy = ConsecutivePolicy(policy_reference="synthetic:consecutive-example", week_reference=week,
        provenance=dict(source="synthetic", reference="example-only-not-campus-policy"),
        score_student_groups=True, soft_criteria={"BACK_TO_BACK", "SHORT_BREAK", "EXCESS_CONTINUOUS_MINUTES"},
        preferred_minimum_break_minutes=15, maximum_preferred_continuous_minutes=60,
        back_to_back_penalty=5, short_break_minute_penalty=2, excess_continuous_minute_penalty=3,
        idle_gap_minute_penalty=1, idle_gap_policy_reference="synthetic:explicit-compactness")
    consecutive = dict(consecutive_policy=policy, consecutive_calendar=calendar,
        consecutive_config=ConsecutiveOptimizationConfig(priority="EXISTING_OBJECTIVES_THEN_CONSECUTIVE",
            priority_policy_reference="synthetic:consecutive-last"))
    baseline = solve_timetable(prepared, config, **original)
    optimized = solve_timetable(prepared, config, **original, **consecutive)
    validate_consecutive_result(prepared, config, optimized, **original, **consecutive)
    before = assess_consecutive_sessions(prepared, config, baseline, calendar, policy, **original)
    after = assess_consecutive_sessions(prepared, config, optimized, calendar, policy, **original, **consecutive)

    def summary(result, assessment):
        bookings = sorted(result.schedule, key=lambda row: row.start_minute)
        starts, ends = [row.start_minute for row in bookings], [row.end_minute for row in bookings]
        fairness = result.fairness_report
        return dict(status=result.status.value, lecturer_assignments=[row.assigned_staff_id for row in bookings],
            student_group_timetable=[dict(group_id=row.session.group_id, occurrence_id=row.session.occurrence_id,
                start=row.start_minute, end=row.end_minute) for row in bookings],
            lecturer_back_to_back_pairs=sum(row.back_to_back_pairs for row in assessment.lecturers),
            student_group_back_to_back_pairs=sum(row.back_to_back_pairs for row in assessment.student_groups),
            maximum_continuous_minutes=max(row.maximum_continuous_minutes for row in assessment.lecturers),
            consecutive_penalty=assessment.total_soft_penalty,
            lecturer_teaching_days=sum(row.teaching.teaching_day_count for row in assessment.lecturers),
            daily_span_minutes=ends[-1]-starts[0], idle_gaps=[b-a for a, b in zip(ends, starts[1:])],
            delivery_count=len(bookings), contact_minutes=assessment.scheduled_teaching_minutes,
            preference_penalty=result.preference_report.score.total_weighted_penalty,
            maximum_normalized_workload_deviation=str(fairness.maximum_normalized_target_deviation),
            aggregate_normalized_workload_deviation=str(fairness.aggregate_normalized_target_deviation),
            runtime_seconds=round(result.runtime_seconds, 4))

    output = dict(example="synthetic-consecutive-optimization", baseline=summary(baseline, before),
        optimized=summary(optimized, after), actual_penalty_improved=after.total_soft_penalty < before.total_soft_penalty,
        priority_order=list(optimized.consecutive_report.priority_order),
        stages=[dict(criterion=row.criterion, status=row.status, objective_value=row.objective_value)
            for row in optimized.consecutive_report.stages],
        independently_validated=True, synthetic=True, university_ready=False)
    print(json.dumps(output, indent=2))
    return output


if __name__ == "__main__":
    main()
