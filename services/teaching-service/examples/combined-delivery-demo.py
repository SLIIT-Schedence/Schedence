"""Run three synthetic physical timetables; never reads campus workbooks."""

import json
from pathlib import Path

from schedence_teaching.preparation import prepare_group_combinations
from schedence_teaching.schemas import GroupCombinationInput
from schedence_teaching.solver import (
    CombinationSolverConfig, SolverConfig, VenueSchedulingInput, solve_timetable,
    validate_delivery_result,
)


def run_scenarios():
    source = GroupCombinationInput.model_validate_json(
        Path(__file__).with_name("group-combination-input.example.json").read_text(encoding="utf-8"))
    combinations = prepare_group_combinations(source)
    config = SolverConfig(
        days=[{"day": "SYNTHETIC-DAY", "windows": {"WEEKDAY": [{"start_minute": 480, "end_minute": 960}]}}],
        granularity_minutes=30, max_time_seconds=5.0, random_seed=7)
    options = CombinationSolverConfig()
    outputs = {}
    for scenario, rooms in {
        "combined": [("SYNTHETIC-LARGE", 240, 960)],
        "separate": [("SYNTHETIC-SMALL", 120, 960)],
        "mixed": [("SYNTHETIC-LARGE", 240, 600), ("SYNTHETIC-SMALL", 120, 960)],
    }.items():
        venues = VenueSchedulingInput(
            venues=[{"venue_id": identity, "venue_name": identity, "capacity": capacity,
                     "venue_type": "Lecture Hall", "building": "Synthetic", "floor": 1}
                    for identity, capacity, _ in rooms],
            unresolved_venue_ids=[],
            calendars=[{"venue_id": identity,
                        "available_windows": {"SYNTHETIC-DAY": [{"start_minute": 480, "end_minute": end}]},
                        "blocked_periods": []} for identity, _, end in rooms])
        result = solve_timetable(combinations.prepared, config, combination_input=combinations,
                                 combination_config=options, venue_input=venues)
        validate_delivery_result(combinations.prepared, config, result, combination_input=combinations,
                                  combination_config=options, venue_input=venues)
        outputs[scenario] = result.to_dict()
    return outputs


if __name__ == "__main__":
    print(json.dumps(run_scenarios(), indent=2))
