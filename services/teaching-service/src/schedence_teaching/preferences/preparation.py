"""Snapshot and reference validation, with diagnostic soft-preference conflicts."""

from dataclasses import dataclass
from datetime import date
from itertools import combinations

from pydantic import ValidationError

from schedence_teaching.preparation import prepare_timetable_input

from .contracts import (
    LecturerPreferenceInput, PreferenceCategory, PreferenceConfirmation, PreferenceDirection,
)


class PreferenceInputError(ValueError):
    pass


@dataclass(frozen=True)
class PreferenceDiagnostic:
    code: str
    preference_ids: tuple[str, ...]
    message: str


@dataclass(frozen=True)
class PreparedLecturerPreferences:
    input_snapshot: LecturerPreferenceInput
    diagnostics: tuple[PreferenceDiagnostic, ...]


def preference_units(inputs):
    """Preserve source members while grouping only explicitly declared alternatives."""
    units = {}
    for row in inputs.preferences:
        identity = ("alternatives", row.staff_id, row.alternative_group_id) if row.alternative_group_id is not None else ("record", row.preference_id)
        units.setdefault(identity, []).append(row)
    return tuple(tuple(rows) for rows in units.values())


def snapshot(value, model):
    if not isinstance(value, model):
        raise PreferenceInputError(f"Expected {model.__name__}.")
    try:
        return model.model_validate(value.model_dump(round_trip=True))
    except ValidationError as error:
        raise PreferenceInputError("; ".join(row["msg"] for row in error.errors(
            include_input=False, include_context=False, include_url=False))) from None


def _overlapping_scope(first, second):
    a, b = first.scope, second.scope
    if a.week_reference is not None and b.week_reference is not None and a.week_reference != b.week_reference:
        return False
    if max(a.effective_from or date.min, b.effective_from or date.min) > min(a.effective_until or date.max, b.effective_until or date.max):
        return False
    return first.stream is None or second.stream is None or first.stream == second.stream


def prepare_lecturer_preferences(teaching_input, inputs: LecturerPreferenceInput):
    prepared = prepare_timetable_input(teaching_input)
    inputs = snapshot(inputs, LecturerPreferenceInput)
    staff_ids = {row.staff_id for row in prepared.input_snapshot.staff_members}
    if {row.staff_id for row in inputs.preferences} - staff_ids:
        raise PreferenceInputError("Preferences reference unknown staff IDs; IDs must match supplied staff exactly.")
    diagnostics = []
    active = [row for row in inputs.preferences if row.confirmation_status is not PreferenceConfirmation.REJECTED]
    day_categories = {PreferenceCategory.WORKING_DAY, PreferenceCategory.WEEKEND_DAY}
    for first, second in combinations(active, 2):
        if first.staff_id != second.staff_id or not _overlapping_scope(first, second):
            continue
        if first.alternative_group_id is not None and first.alternative_group_id == second.alternative_group_id:
            continue  # Explicit alternatives are one OR expression, not competing obligations.
        if first.category in day_categories and second.category in day_categories:
            same = bool(first.days & second.days)
        elif first.category == second.category:
            if first.days and second.days and not first.days & second.days:
                continue
            same = (first.time_of_day == second.time_of_day if first.time_of_day is not None else
                    first.time_window.start_minute < second.time_window.end_minute
                    and second.time_window.start_minute < first.time_window.end_minute)
        else:
            continue  # Cross-category clock semantics need configured boundaries, never guessed.
        if same and first.direction != second.direction:
            diagnostics.append(PreferenceDiagnostic("conflicting_preferences", (first.preference_id, second.preference_id),
                "Applicable preferred and avoided selections overlap; both source records remain soft and require review."))
        elif same and first.direction == second.direction and first.category == second.category and first.days == second.days and first.time_window == second.time_window:
            diagnostics.append(PreferenceDiagnostic("repeated_preference_intent", (first.preference_id, second.preference_id),
                "Repeated preference intent is retained as separate records; review before configuring optimization weights."))
        elif first.category in day_categories and second.category in day_categories and first.direction is second.direction is PreferenceDirection.PREFER and not same:
            diagnostics.append(PreferenceDiagnostic("separate_day_preference_records", (first.preference_id, second.preference_id),
                "Separate day records are evaluated separately; put alternative preferred days in one record if that is intended."))
    return PreparedLecturerPreferences(inputs, tuple(diagnostics))
