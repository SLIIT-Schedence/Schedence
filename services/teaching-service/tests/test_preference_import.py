"""Sanitized Department_Aligned cells; original importer/staging remain unchanged."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from schedence_teaching.excel_import import import_wide_excel
from schedence_teaching.excel_import.cli import main
from schedence_teaching.excel_import.preference_adapter import map_lecturer_preferences
from schedence_teaching.excel_import.preference_config import LecturerPreferenceMapping
from schedence_teaching.preferences import PreferenceCategory

from test_wide_excel_import import wide_dataset, change


def mapping(**updates):
    return LecturerPreferenceMapping.model_validate({"layout": "department-aligned-preferences-v1",
        "source_reference": "synthetic:inspected-layout", **updates})


def codes(result):
    return {row.code for row in result.issues}


def test_clear_source_preferences_map_without_inventing_dates_stream_weights_or_availability(wide_dataset):
    change(wide_dataset, "preferences", "Monday_Morning", "Preferred")
    change(wide_dataset, "preferences", "Monday_Afternoon", "Not Preferred")
    change(wide_dataset, "preferences", "Tuesday_Morning", "Not Available")
    source = import_wide_excel(wide_dataset["config"])
    before = deepcopy(source)
    workbook_bytes = {key: path.read_bytes() for key, path in wide_dataset["paths"].items()}
    result = map_lecturer_preferences(source, mapping())
    assert result.valid and len(result.candidates) == 7 and result.preferences == []
    assert result.mapped_staff_count == 5
    assert [row.staff_id for row in result.candidates[:3]] == ["LEC001"] * 3
    assert all(row.penalty_weight is row.strength is row.stream is row.confirmation_status is None for row in result.candidates)
    assert {row.category for row in result.candidates} == {PreferenceCategory.WORKING_DAY, PreferenceCategory.TIME_OF_DAY}
    assert {"availability_confirmation_required", "ambiguous_weekend_work_pattern", "attendance_assertion_separate",
        "weekend_availability_assertion_separate", "preference_scope_required"} <= codes(result)
    slot = result.candidates[0]
    assert slot.time_window is None and slot.time_of_day.value == "MORNING"
    assert slot.provenance.column == "Monday_Morning" and slot.provenance.row == 5
    assert slot.provenance.worksheet == "Lecturer_Preferences"
    assert slot.preference_id.startswith("source-preference:")  # Diagnostic identity, not an authoritative DB ID.
    assert source == before
    assert workbook_bytes == {key: path.read_bytes() for key, path in wide_dataset["paths"].items()}


def test_scope_is_only_applied_when_supplied_explicitly(wide_dataset):
    source = import_wide_excel(wide_dataset["config"])
    result = map_lecturer_preferences(source, mapping(scope={"week_reference": "synthetic:approved-week"}))
    assert len(result.preferences) == len(result.candidates) == 5
    assert all(row.scope.week_reference == "synthetic:approved-week" for row in result.preferences)
    assert "preference_scope_required" not in codes(result)
    assert all(row.direction.value == "AVOID" and {day.value for day in row.days} == {"SUNDAY"} for row in result.preferences)


def test_weekend_pattern_requires_confirmed_interpretation_not_automatic_exclusivity(wide_dataset):
    source = import_wide_excel(wide_dataset["config"])
    result = map_lecturer_preferences(source, mapping(
        weekend_pattern_values={"Saturday Only": {"SATURDAY"}}, weekend_mapping_confirmation_reference="synthetic:soft-weekend-confirmation"))
    weekend = [row for row in result.candidates if row.category is PreferenceCategory.WEEKEND_DAY]
    assert len(weekend) == 5 and all(row.direction.value == "PREFER" for row in weekend)
    assert all(row.provenance.interpretation_reference == "synthetic:soft-weekend-confirmation" for row in weekend)
    assert all(row.penalty_weight is None for row in weekend)
    assert not result.summary()["hard_availability_generated"]
    with pytest.raises(ValidationError, match="confirmation reference"):
        mapping(weekend_pattern_values={"Saturday Only": {"SATURDAY"}})


@pytest.mark.parametrize("value,expected", [(None, "missing_preference_value"), ("Unknown selection", "unmapped_preference_value"),
    ("No Preference", "unmapped_preference_value")])
def test_missing_or_ambiguous_slot_values_are_reported_without_fabrication(wide_dataset, value, expected):
    change(wide_dataset, "preferences", "Monday_Morning", value)
    result = map_lecturer_preferences(import_wide_excel(wide_dataset["config"]), mapping())
    assert expected in codes(result)
    assert not any(row.provenance.column == "Monday_Morning" for row in result.candidates)
    issue = next(row for row in result.issues if row.code == expected)
    assert (issue.workbook, issue.worksheet, issue.row, issue.column) == (
        wide_dataset["paths"]["preferences"].name, "Lecturer_Preferences", 5, "Monday_Morning")


@pytest.mark.parametrize("value", [None, "Unknown day", "No Preference"])
def test_missing_or_unspecified_free_day_never_becomes_synthetic_day(wide_dataset, value):
    change(wide_dataset, "preferences", "Preferred_Free_Day", value)
    result = map_lecturer_preferences(import_wide_excel(wide_dataset["config"]), mapping())
    assert len(result.candidates) == 4
    assert not any(row.staff_id == "LEC001" for row in result.candidates)


@pytest.mark.parametrize("field,value", [("Staff_ID", "UNKNOWN"), ("Staff_ID", "INS001"),
    ("Staff_Name", "Other synthetic identity"), ("Department", "Other department"), ("Position", "Professor")])
def test_invalid_source_identity_or_profile_is_not_mapped(wide_dataset, field, value):
    change(wide_dataset, "preferences", field, value)
    result = map_lecturer_preferences(import_wide_excel(wide_dataset["config"]), mapping())
    assert not result.valid and len(result.candidates) == 4
    assert not any(row.provenance.row == 5 for row in result.candidates)


def test_duplicate_source_ids_are_preserved_as_diagnostics_without_choosing_one(wide_dataset):
    source = import_wide_excel(wide_dataset["config"])
    duplicate = deepcopy(source.source_preferences[0])
    duplicate.location = replace(duplicate.location, row=99)
    source.source_preferences.append(duplicate)
    result = map_lecturer_preferences(source, mapping())
    assert "duplicate_preference_staff" in codes(result)
    assert not any(row.staff_id == "LEC001" for row in result.candidates)


def test_formula_cells_do_not_generate_preferences(wide_dataset):
    change(wide_dataset, "preferences", "Monday_Morning", '=IF(TRUE,"Preferred","Not Preferred")')
    result = map_lecturer_preferences(import_wide_excel(wide_dataset["config"]), mapping())
    assert not result.valid and "unsupported_cell" in codes(result)
    assert not any(row.provenance.column == "Monday_Morning" for row in result.candidates)


def test_safe_summary_has_no_personal_values_or_staff_ids(wide_dataset):
    result = map_lecturer_preferences(import_wide_excel(wide_dataset["config"]), mapping())
    text = json.dumps(result.summary())
    for value in ("LEC001", "INS001", "Synthetic Person", "Private synthetic preference"):
        assert value not in text
    assert not result.summary()["ready_for_weekly_scoring"]
    assert result.summary()["candidates_without_explicit_weight"] == 5


def test_real_layout_example_configuration_is_loadable_without_authority_claims():
    path = Path(__file__).resolve().parents[1] / "examples" / "department-aligned-preference-mapping.example.json"
    config = LecturerPreferenceMapping.model_validate_json(path.read_text(encoding="utf-8"))
    assert len(config.slot_columns) == 14
    assert config.scope is config.stream is config.confirmation_status is None
    assert config.weekend_pattern_values == {}


@pytest.mark.parametrize("updates", [
    {"weekend_pattern_values": {"Saturday Only": {"MONDAY"}}, "weekend_mapping_confirmation_reference": "synthetic:invalid"},
    {"free_day_column": "Weekend_Work_Pattern"},
    {"slot_columns": {"Monday_Morning": {"day": "MONDAY", "time_of_day": "MORNING"},
        "Monday_Afternoon": {"day": "MONDAY", "time_of_day": "MORNING"}}}])
def test_ambiguous_configurations_are_rejected(updates):
    with pytest.raises(ValidationError):
        mapping(**updates)


def test_cli_adds_optional_preference_summary_without_printing_source_records(wide_dataset, tmp_path, capsys):
    wide_path = tmp_path / "wide.json"
    wide_path.write_text(wide_dataset["config"].model_dump_json(), encoding="utf-8")
    preference_path = tmp_path / "preferences.json"
    preference_path.write_text(mapping().model_dump_json(), encoding="utf-8")
    assert main(["--config", str(wide_path), "--preference-config", str(preference_path)]) == 0
    text = capsys.readouterr().out
    summary = json.loads(text)
    assert summary["preference_mapping"]["soft_preference_candidates"] == 5
    assert summary["imported_counts"]["lecturer_preferences"] == 5
    assert "Synthetic Person" not in text and "LEC001" not in text


def test_cli_invalid_preference_mapping_is_reported_safely(wide_dataset, tmp_path, capsys):
    wide_path = tmp_path / "wide.json"
    wide_path.write_text(wide_dataset["config"].model_dump_json(), encoding="utf-8")
    bad = tmp_path / "bad.json"
    bad.write_text('{"private":"must-not-print"}', encoding="utf-8")
    assert main(["--config", str(wide_path), "--preference-config", str(bad)]) == 2
    text = capsys.readouterr().out
    assert "must-not-print" not in text and not json.loads(text)["preference_mapping"]["valid"]


def test_two_preferred_day_parts_preserve_cells_and_explicit_alternative_identity(wide_dataset):
    change(wide_dataset, "preferences", "Monday_Morning", "Preferred")
    change(wide_dataset, "preferences", "Monday_Afternoon", "Preferred")
    result = map_lecturer_preferences(import_wide_excel(wide_dataset["config"]),
        mapping(scope={"week_reference": "SYNTHETIC-WEEK"}))
    parts = [row for row in result.preferences if row.category is PreferenceCategory.TIME_OF_DAY]
    assert len(parts) == 2 and parts[0].alternative_group_id == parts[1].alternative_group_id
    assert {row.provenance.column for row in parts} == {"Monday_Morning", "Monday_Afternoon"}
    from schedence_teaching.preferences import LecturerPreferenceInput
    LecturerPreferenceInput(preferences=result.preferences)
