"""Step 8B synthetic policy-enabled physical timetables and evidence reports."""

import json
from pathlib import Path

from schedence_teaching.preparation import prepare_group_combinations
from schedence_teaching.schemas import GroupCombinationInput
from schedence_teaching.solver import (
    SolverConfig, SolverStatus, StaffSchedulingInput, VenueSchedulingInput,
    solve_timetable, validate_staff_policy_result,
)
from schedence_teaching.workload import WeeklyWorkloadInput, confirmed_campus_policy


def run_scenarios():
    source = GroupCombinationInput.model_validate_json(
        Path(__file__).with_name("group-combination-input.example.json").read_text(encoding="utf-8"))
    config = SolverConfig(
        days=[{"day": "SYNTHETIC-DAY", "windows": {"WEEKDAY": [{"start_minute": 480, "end_minute": 960}]}}],
        granularity_minutes=30, max_time_seconds=5.0, random_seed=7)
    outputs = {}
    for name, rooms, band, appointment, admin, complete, cap in [
        ("combined_professor", [("LARGE", 240, 960)], "PROFESSOR", None, 240, True, 360),
        ("separate_hod", [("SMALL", 120, 960)], "LECTURER", "HOD", 240, True, None),
        ("mixed_assistant_professor", [("LARGE", 240, 600), ("SMALL", 120, 960)], "ASSISTANT_PROFESSOR", None, 0, True, 360),
        ("partial_lecturer_evidence", [("LARGE", 240, 960)], "LECTURER", None, 120, False, None),
        ("infeasible_explicit_cap", [("LARGE", 240, 960)], "PROFESSOR", None, 120, True, 239),
    ]:
        scenario_source = source.model_copy(deep=True)
        if name == "combined_professor":
            for requirement in scenario_source.teaching_input.session_requirements:
                requirement.sessions_per_week = 3  # Synthetic six-hour teaching example.
        combinations = prepare_group_combinations(scenario_source)
        workload = WeeklyWorkloadInput(
            week_reference="synthetic:2026-10-12-week", policy=confirmed_campus_policy(),
            profiles=[{"staff_id": "SYNTHETIC-STAFF", "workload_band": band, "appointment": appointment,
                       "evidence_reference": "synthetic:staff-classification"}],
            recognized_work=[{"work_id": "SYNTHETIC-ADMIN", "staff_id": "SYNTHETIC-STAFF",
                              "work_type": "ADMINISTRATIVE", "duration_minutes": admin,
                              "evidence_reference": "synthetic:recognized-admin"}] if admin else [],
            recognized_work_complete_for=["SYNTHETIC-STAFF"] if complete else [])
        staff = StaffSchedulingInput(
            workload_input=workload, day_dates={"SYNTHETIC-DAY": "2026-10-12"},
            policy_effective_from="2026-10-12", policy_effective_until="2026-10-18",
            availability=[{"staff_id": "SYNTHETIC-STAFF", "allowed_teaching_days": ["SYNTHETIC-DAY"],
                           "available_windows": {"SYNTHETIC-DAY": [{"start_minute": 480, "end_minute": 960}]},
                           "blocked_periods": [], "effective_from": "2026-10-12", "effective_until": "2026-10-18",
                           "weekly_teaching_max_minutes": cap}])
        venues = VenueSchedulingInput(
            venues=[{"venue_id": f"SYNTHETIC-{identity}", "venue_name": f"Synthetic {identity}", "capacity": capacity,
                     "venue_type": "Lecture Hall", "building": "Synthetic", "floor": 1} for identity, capacity, _ in rooms],
            unresolved_venue_ids=[], calendars=[{
                "venue_id": f"SYNTHETIC-{identity}", "available_windows": {"SYNTHETIC-DAY": [{"start_minute": 480, "end_minute": end}]},
                "blocked_periods": []} for identity, _, end in rooms])
        result = solve_timetable(combinations.prepared, config, staff_input=staff, venue_input=venues,
                                 combination_input=combinations)
        if result.status in {SolverStatus.OPTIMAL, SolverStatus.FEASIBLE}:
            validate_staff_policy_result(combinations.prepared, config, result, staff_input=staff,
                                          venue_input=venues, combination_input=combinations)
        outputs[name] = result.to_dict()
    return outputs


if __name__ == "__main__":
    print(json.dumps(run_scenarios(), indent=2))
