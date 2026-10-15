"""Synthetic before/after native fairness optimization; no datasets or database."""

from datetime import date
from fractions import Fraction
import json

from schedence_teaching.fairness import FairnessPolicy, assess_fairness
from schedence_teaching.preferences import (
    LecturerPreferenceInput, PreferenceCalendar, PreferenceScoringPolicy, prepare_lecturer_preferences,
)
from schedence_teaching.preparation import prepare_timetable_input
from schedence_teaching.schemas import TeachingTimetableInput
from schedence_teaching.solver import (
    FairnessOptimizationConfig, PreferenceOptimizationConfig, SolverConfig, StaffSchedulingInput,
    solve_timetable, validate_fairness_result,
)
from schedence_teaching.workload import WeeklyWorkloadInput, confirmed_campus_policy


def main():
    first, last, week = date(2026, 10, 19), date(2026, 10, 25), "SYNTHETIC-WEEK"
    prepared = prepare_timetable_input(TeachingTimetableInput(
        staff_members=[dict(staff_id=identity, staff_name=identity, position=band, staff_type="ACADEMIC")
            for identity, band in (("SYNTHETIC-D", "DR"), ("SYNTHETIC-P", "PROFESSOR"))],
        module_offerings=[dict(offering_id=identity, module_code="SYNTHETIC-SHARED-CODE", module_name=identity,
            academic_year=2, semester=1, specialization="Synthetic", degree_type="Synthetic", stream="WEEKDAY")
            for identity in ("BASE", "SHARED")],
        teaching_team_members=[dict(offering_id=offering, staff_id=identity, role="TEAM_MEMBER")
            for offering, identity in (("BASE", "SYNTHETIC-P"), ("SHARED", "SYNTHETIC-P"), ("SHARED", "SYNTHETIC-D"))],
        student_groups=[dict(group_id=f"GROUP-{identity}", academic_year=2, semester=1, stream="WEEKDAY", student_count=20)
            for identity in ("BASE", "SHARED")],
        session_requirements=[dict(requirement_id=identity, offering_id=identity, group_id=f"GROUP-{identity}",
            session_type="LECTURE", duration_minutes=30, sessions_per_week=count)
            for identity, count in (("BASE", 6), ("SHARED", 14))]))
    config = SolverConfig(days=[dict(day="MONDAY", windows={"WEEKDAY": [dict(start_minute=0, end_minute=720)]})],
        granularity_minutes=15, max_time_seconds=5, random_seed=7)
    workload = WeeklyWorkloadInput(week_reference=week, policy=confirmed_campus_policy(), profiles=[
        dict(staff_id=identity, workload_band=band, evidence_reference="synthetic:classification")
        for identity, band in (("SYNTHETIC-D", "DR"), ("SYNTHETIC-P", "PROFESSOR"))])
    staff = StaffSchedulingInput(workload_input=workload, day_dates={"MONDAY": first},
        policy_effective_from=first, policy_effective_until=last, availability=[
            dict(staff_id=row.staff_id, allowed_teaching_days={"MONDAY"}, available_windows={
                "MONDAY": [dict(start_minute=0, end_minute=720)]}, blocked_periods=[], effective_from=first, effective_until=last)
            for row in workload.profiles])
    calendar = PreferenceCalendar(week_reference=week, week_start=first, week_end=last, day_dates=staff.day_dates, synthetic_data=True)
    preferences = prepare_lecturer_preferences(prepared.input_snapshot, LecturerPreferenceInput(preferences=[
        dict(preference_id=f"DAY-{row.staff_id}", staff_id=row.staff_id, category="WORKING_DAY", direction="PREFER",
            days={"MONDAY"}, scope=dict(week_reference=week), confirmation_status="CONFIRMED", penalty_weight=1,
            weight_reference="synthetic:confirmed-test-weight", provenance=dict(source="synthetic", reference="example-only"))
        for row in workload.profiles]))
    preference_policy = PreferenceScoringPolicy(policy_reference="synthetic:confirmed-test-weights", weight_mode="CONFIRMED_WEIGHTS")
    original = dict(staff_input=staff, preference_input=preferences, preference_calendar=calendar, preference_policy=preference_policy,
        preference_config=PreferenceOptimizationConfig(priority_policy_reference="synthetic:teaching-before-preferences",
            calendar_semantics_reference="synthetic:day-stream-map", day_streams={"MONDAY": {"WEEKDAY"}},
            unscoped_preferences_apply_to_all_streams=True))
    policy = FairnessPolicy(policy_reference="synthetic:target-fairness", week_reference=week,
        provenance=dict(source="synthetic", reference="development-only"), comparison_scope="EXPLICIT_GROUPS",
        comparison_groups=[dict(group_id="SYNTHETIC-COHORT", staff_ids=[row.staff_id for row in workload.profiles],
                                reason="synthetic approved example cohort")])
    fairness = dict(fairness_policy=policy, fairness_calendar=calendar, fairness_config=FairnessOptimizationConfig(
        priority="EXISTING_OBJECTIVES_THEN_FAIRNESS", priority_policy_reference="synthetic:fairness-last",
        cohort_rule="INPUT_ELIGIBILITY_AND_FIXED_CALENDAR", deviation_reference="TEACHING_TARGET"))
    baseline = solve_timetable(prepared, config, **original)
    optimized = solve_timetable(prepared, config, **original, **fairness)
    validate_fairness_result(prepared, config, optimized, **original, **fairness)
    before = assess_fairness(prepared, config, baseline, calendar, policy, **original)
    after = assess_fairness(prepared, config, optimized, calendar, policy, **original, **fairness)

    def summary(result, assessment):
        deviations = [row.target.absolute_normalized_target_deviation for row in assessment.staff]
        return dict(status=result.status.value, delivery_count=len(result.schedule),
            teaching_minutes=assessment.scheduled_teaching_minutes,
            preference_penalty=result.preference_report.score.total_weighted_penalty,
            maximum_normalized_target_deviation=str(max(deviations)),
            aggregate_normalized_target_deviation=str(sum(deviations, Fraction())),
            staff=[dict(staff_id=row.staff_id, delivery_count=row.teaching.assigned_delivery_count,
                teaching_minutes=row.teaching.scheduled_teaching_minutes, target_minutes=row.target.target_minutes,
                utilization=str(row.target.utilization)) for row in assessment.staff])
    output = dict(example="synthetic-fairness-optimization", baseline=summary(baseline, before),
        optimized=summary(optimized, after),
        fairness_improved=optimized.fairness_report.maximum_normalized_target_deviation <
            max(row.target.absolute_normalized_target_deviation for row in before.staff),
        objective_order=list(optimized.fairness_report.priority_order),
        stages=[dict(criterion=row.criterion, status=row.status, objective_value=row.objective_value)
                for row in optimized.fairness_report.stages], independently_validated=True, university_ready=False)
    print(json.dumps(output, indent=2))
    return output


if __name__ == "__main__":
    main()
