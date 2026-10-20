"""Synthetic preference optimization; all dates, windows and weights are test inputs."""

from datetime import date
import json
from pathlib import Path

from schedence_teaching.preferences import (
    LecturerPreference, LecturerPreferenceInput, PreferenceCalendar, PreferenceScoringPolicy,
    prepare_lecturer_preferences, score_lecturer_preferences,
)
from schedence_teaching.preparation import prepare_group_combinations
from schedence_teaching.schemas import GroupCombinationInput
from schedence_teaching.solver import (
    PreferenceOptimizationConfig, SchedulingDay, SolverConfig, TimeWindow,
    solve_timetable, validate_preference_result,
)


def main():
    source = json.loads(Path(__file__).with_name("group-combination-input.example.json").read_text(encoding="utf-8"))
    for row in source["teaching_input"]["session_requirements"]:
        row["sessions_per_week"] = 1
    combinations = prepare_group_combinations(GroupCombinationInput.model_validate(source))
    config = SolverConfig(days=(SchedulingDay(day="MONDAY", windows={"WEEKDAY": [
        TimeWindow(start_minute=540, end_minute=660), TimeWindow(start_minute=840, end_minute=960)]}),),
        granularity_minutes=30, max_time_seconds=5.0, random_seed=7)
    baseline = solve_timetable(combinations.prepared, config, combination_input=combinations)
    # An explicit synthetic experiment: request the other available day-part.
    # No campus preferences or hours are inferred from the resulting timetable.
    preferred_part = "AFTERNOON" if baseline.schedule[0].start_minute < 720 else "MORNING"
    calendar = PreferenceCalendar(week_reference="synthetic:optimization-week", week_start=date(2026, 10, 19),
        week_end=date(2026, 10, 25), day_dates={"MONDAY": date(2026, 10, 19)}, synthetic_data=True)
    policy = PreferenceScoringPolicy(policy_reference="synthetic:five-per-violation",
        weight_mode="SYNTHETIC_DEFAULTS", synthetic_default_weight=5,
        time_of_day_windows={"MORNING": TimeWindow(start_minute=540, end_minute=720),
                            "AFTERNOON": TimeWindow(start_minute=720, end_minute=1020)},
        time_window_policy_reference="synthetic:explicit-clock-bands")
    preferences = prepare_lecturer_preferences(combinations.input_snapshot.teaching_input,
        LecturerPreferenceInput(preferences=[LecturerPreference(
            preference_id="SYNTHETIC-DAY-PART", staff_id="SYNTHETIC-STAFF", category="TIME_OF_DAY",
            direction="PREFER", time_of_day=preferred_part, days={"MONDAY"}, stream="WEEKDAY",
            scope={"week_reference": calendar.week_reference},
            provenance={"source": "synthetic-demo", "reference": "synthetic:lecturer-choice"})]))
    optimization = PreferenceOptimizationConfig(
        priority_policy_reference="development:delivery-count-contact-minutes-preference-penalty",
        calendar_semantics_reference="synthetic:confirmed-explicit-day-stream-map",
        day_streams={"MONDAY": {"WEEKDAY"}}, unscoped_preferences_apply_to_all_streams=False)
    inputs = dict(combination_input=combinations, preference_input=preferences, preference_calendar=calendar,
                  preference_policy=policy, preference_config=optimization)
    result = solve_timetable(combinations.prepared, config, **inputs)
    validate_preference_result(combinations.prepared, config, result, **inputs)
    before = score_lecturer_preferences(combinations.prepared, config, baseline, preferences, calendar, policy,
                                        combination_input=combinations)
    summary = {
        "example": "synthetic-opt-in-preference-optimization", "preferred_day_part": preferred_part,
        "baseline": {"status": baseline.status.value, "start_minute": baseline.schedule[0].start_minute,
            "actual_deliveries": len(baseline.schedule), "preference_penalty": before.total_weighted_penalty},
        "optimized": {"status": result.status.value, "start_minute": result.schedule[0].start_minute,
            "actual_deliveries": len(result.schedule), "preference_penalty": result.preference_report.score.total_weighted_penalty,
            "actual_teaching_minutes": result.validation.lecturer_contact_minutes,
            "covered_originals": result.validation.assigned_occurrences},
        "objective": result.preference_report.to_dict(), "independently_validated": True, "university_ready": False,
    }
    print(json.dumps(summary, indent=2))
    return summary


if __name__ == "__main__":
    main()
