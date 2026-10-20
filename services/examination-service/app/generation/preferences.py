"""Validated per-timetable overrides and their immutable, versioned JSONB snapshot.

Only the existing preference controls are writable. Venue compatibility, module
exceptions, precision scales and automatic fairness relaxation remain inherited.
No setting can turn off a hard rule. Legacy inputs without this snapshot retain
the active-default checks they have always used.
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy

from app.scheduling.configuration import SchedulingConfiguration
from app.scheduling.contracts import SchedulingValidationError
from app.seating.rules import check_percentage

FORMAT = "timetable-preferences-v1"
ALLOWED = {
    "scheduling": {"permitted_start_times", "minimum_break_minutes", "max_rooms_per_exam", "max_sessions_per_exam"},
    "solver": {"time_limit_seconds", "random_seed", "num_search_workers"},
    "optimization": {"s1_weight", "s2_weight", "s3_weight", "s4_weight", "s5_weight", "short_gap_minutes",
                     "preparation_target_minutes", "difficult_target_minutes", "high_difficulty_minimum", "window_days",
                     "preferred_window_exams", "difficulty_weight", "credits_weight", "duration_weight",
                     "reference_difficulty", "reference_credits", "reference_duration_minutes", "baseline_budget_percent"},
    "fairness": {"enabled", "max_individual_increase_points", "protect_worst_student", "max_worst_increase_points"},
    "seating": {"default_percentage"},
}


def _preferences_document(base: SchedulingConfiguration, preferences: dict) -> dict:
    if type(preferences) is not dict or set(preferences) - ALLOWED.keys():
        raise ValueError("Scheduling preferences must contain only scheduling, solver, optimization, fairness or seating.")
    document = base.to_document()
    for section, values in preferences.items():
        if type(values) is not dict or set(values) - ALLOWED[section]:
            raise ValueError(f"Unsupported {section} preferences. H1-H11 and technical safety settings cannot be disabled.")
        if section == "seating":
            if "default_percentage" in values:
                check_percentage(values["default_percentage"])
        else:
            document[section].update(deepcopy(values))
    return document


def effective_configuration(base: SchedulingConfiguration, preferences: dict) -> SchedulingConfiguration:
    document = _preferences_document(base, preferences)
    try:
        configuration = SchedulingConfiguration.from_document(document)
    except SchedulingValidationError as exc:
        raise ValueError("; ".join(v.message for v in exc.violations)) from None
    if configuration.solver.time_limit_seconds > 300 and "time_limit_seconds" in preferences.get("solver", {}):
        raise ValueError("Per-timetable solver time limits cannot exceed 300 seconds.")
    return configuration


def validate_preferences(preferences: dict) -> dict:
    # Schema checks shape and the writable boundary. Cross-field values must be
    # validated against actual institution defaults, not hypothetical built-ins.
    # The generator does that before loading scheduling inputs or starting CP-SAT.
    _preferences_document(SchedulingConfiguration(), preferences)
    return deepcopy(preferences)


def _fingerprint(document: dict) -> str:
    return hashlib.sha256(json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def snapshot_document(configuration: SchedulingConfiguration, capacities) -> dict:
    document = {
        "format": FORMAT, "settings": configuration.to_document(),
        "seating": {"default_percentage": capacities.default_percentage,
                    "venue_overrides": {v.venue_id: v.percentage for v in capacities.venues if v.rule_scope == "venue"}},
        "source_capacity_fingerprint": capacities.source_fingerprint or capacities.fingerprint,
        "capacity_fingerprint": capacities.fingerprint, "seating_ruleset_version": capacities.ruleset_version,
    }
    return {**document, "fingerprint": _fingerprint(document)}


def saved_preferences(inputs: dict | None) -> tuple[SchedulingConfiguration, dict] | None:
    """Fail closed on malformed/newer snapshots; absent/null is the legacy contract."""
    if not isinstance(inputs, dict):
        raise ValueError("The saved inputs cannot be read.")
    document = inputs.get("scheduling_preferences")
    if document is None:
        return None
    keys = {"format", "settings", "seating", "source_capacity_fingerprint", "capacity_fingerprint",
            "seating_ruleset_version", "fingerprint"}
    if type(document) is not dict or set(document) != keys or document["format"] != FORMAT:
        raise ValueError("The saved scheduling preferences format cannot be read.")
    if document["fingerprint"] != _fingerprint({k: v for k, v in document.items() if k != "fingerprint"}):
        raise ValueError("The saved scheduling preferences fingerprint does not match.")
    try:
        configuration = SchedulingConfiguration.from_document(document["settings"])
    except SchedulingValidationError:
        raise ValueError("The saved scheduling preferences are invalid.") from None
    seating = document["seating"]
    if type(seating) is not dict or set(seating) != {"default_percentage", "venue_overrides"}:
        raise ValueError("The saved seating preferences are invalid.")
    check_percentage(seating["default_percentage"])
    if type(seating["venue_overrides"]) is not dict:
        raise ValueError("The saved venue seating preferences are invalid.")
    for percentage in seating["venue_overrides"].values():
        check_percentage(percentage)
    return configuration, document


def checked_saved_preferences(header: dict) -> tuple[SchedulingConfiguration, dict] | None:
    restored = saved_preferences(header.get("inputs"))
    if restored is not None:
        configuration, document = restored
        if (configuration.fingerprint != header["settings_fingerprint"]
                or document["capacity_fingerprint"] != header["capacity_fingerprint"]
                or document["seating_ruleset_version"] != header["seating_ruleset_version"]):
            raise ValueError("The saved preferences do not match the timetable's recorded fingerprints.")
    return restored
