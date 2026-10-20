"""Synthetic post-solve scoring only; no campus preference weights or hours are asserted."""

from datetime import date
import json
from pathlib import Path

from schedence_teaching.preferences import (
    LecturerPreference, LecturerPreferenceInput, PreferenceCalendar, PreferenceScoringPolicy,
    prepare_lecturer_preferences, score_lecturer_preferences,
)
from schedence_teaching.preparation import prepare_group_combinations
from schedence_teaching.schemas import GroupCombinationInput
from schedence_teaching.solver import SchedulingDay, SolverConfig, TimeWindow, solve_timetable


def main():
    source = json.loads(Path(__file__).with_name("group-combination-input.example.json").read_text(encoding="utf-8"))
    for row in source["teaching_input"]["module_offerings"] + source["teaching_input"]["student_groups"]:
        row["stream"] = "WEEKEND"
    combinations = prepare_group_combinations(GroupCombinationInput.model_validate(source))
    config = SolverConfig(days=(SchedulingDay(day="SUNDAY", windows={"WEEKEND": [
        TimeWindow(start_minute=540, end_minute=660), TimeWindow(start_minute=720, end_minute=840)]}),),
        granularity_minutes=30, max_time_seconds=5.0, random_seed=7)
    result = solve_timetable(combinations.prepared, config, combination_input=combinations)
    base = {"staff_id": "SYNTHETIC-STAFF", "scope": {"week_reference": "synthetic:preference-week"},
            "provenance": {"source": "synthetic-demo", "reference": "synthetic:preferences"}}
    inputs = LecturerPreferenceInput(preferences=[
        LecturerPreference(**base, preference_id="SATURDAY", category="WEEKEND_DAY", direction="PREFER", days={"SATURDAY"}),
        LecturerPreference(**base, preference_id="MORNING", category="TIME_OF_DAY", direction="PREFER", time_of_day="MORNING"),
        LecturerPreference(**base, preference_id="AVOID", category="TEACHING_WINDOW", direction="AVOID",
            time_window=TimeWindow(start_minute=540, end_minute=600)),
    ])
    preferences = prepare_lecturer_preferences(combinations.input_snapshot.teaching_input, inputs)
    calendar = PreferenceCalendar(week_reference="synthetic:preference-week", week_start=date(2026, 10, 19),
        week_end=date(2026, 10, 25), day_dates={"SUNDAY": date(2026, 10, 25)}, synthetic_data=True)
    policy = PreferenceScoringPolicy(policy_reference="synthetic:two-points-per-violation", weight_mode="SYNTHETIC_DEFAULTS",
        synthetic_default_weight=2, time_of_day_windows={
            "MORNING": TimeWindow(start_minute=540, end_minute=720),
            "AFTERNOON": TimeWindow(start_minute=720, end_minute=900)},
        time_window_policy_reference="synthetic:explicit-demo-clock-bands")
    report = score_lecturer_preferences(combinations.prepared, config, result, preferences, calendar, policy,
        combination_input=combinations)
    print(json.dumps({"example": "synthetic-combined-lecture-preferences", "solver_status": result.status.value,
        "unchanged_teaching_objective": result.objective_value, **report.to_dict(include_staff_details=True)}, indent=2))
    return report


if __name__ == "__main__":
    main()
