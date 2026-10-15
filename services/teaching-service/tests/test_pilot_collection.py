"""Completed collection packs use only synthetic records and temporary files."""

import csv
from copy import deepcopy
from pathlib import Path
import runpy

import pytest

from schedence_teaching.collection import validate_collection_pack, write_collection_pack
from schedence_teaching.collection.__main__ import main
from schedence_teaching.collection.reader import windows
from schedence_teaching.collection.specification import POLICY_KEYS, TABLES, headers
from schedence_teaching.excel_import.staging import (
    SourceLocation, SourceModuleRecord, SourceTeamAssignment, WideImportResult,
)
from schedence_teaching.integration import assess_readiness
from schedence_teaching.schemas import StaffMember, TeachingRole


@pytest.fixture(scope="module")
def fixture_module():
    return runpy.run_path(str(Path(__file__).resolve().parents[1] / "examples" / "pilot_collection_fixture.py"))


@pytest.fixture
def pilot(tmp_path, fixture_module):
    directory = tmp_path / "completed-synthetic"
    base, institution = fixture_module["write_synthetic_pilot"](directory)
    return directory, base, institution


def rewrite(directory, table, transform):
    file = directory / f"{table}.csv"
    with file.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    transform(rows)
    with file.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers(table), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def check(pilot, **kwargs):
    directory, base, institution = pilot
    return validate_collection_pack(directory, base, expected_institution_id=institution,
        actual_campus=kwargs.pop("actual_campus", False), **kwargs)


def test_complete_pack_reuses_gate_without_solver_and_preserves_base(pilot, monkeypatch):
    from ortools.sat.python import cp_model
    monkeypatch.setattr(cp_model.CpSolver, "solve", lambda *args: pytest.fail("CSV readiness must not run CP"))
    before = deepcopy(pilot[1])
    result = check(pilot)
    assert result.readiness.ready_for_generation, result.readiness.to_dict()
    assert not result.readiness.ready_for_actual_campus
    assert result.submission is not None
    assessment = assess_readiness(result.submission, expected_institution_id=pilot[2], actual_campus=False)
    assert len(assessment.preparation.prepared.sessions) == 7
    assert len(assessment.preparation.solver_inputs["combination_input"].candidates) == 1
    assert pilot[1] == before
    assert result.submission.inputs["venue_input"]["venues"] == before.inputs["venue_input"]["venues"]
    assert result.submission.inputs["combination_input"]["teaching_input"]["teaching_team_members"] == before.inputs["combination_input"]["teaching_input"]["teaching_team_members"]


@pytest.mark.parametrize("table,column,value", [
    ("offerings", "Offering ID", ""), ("offerings", "Stream", "EVENING"),
    ("student-groups", "Student Count", ""), ("student-groups", "Student Count", "0"),
    ("student-groups", "Student Count", "-1"), ("student-groups", "Study Year", "5"),
    ("student-groups", "Parent Group ID", "UNKNOWN-GROUP"),
    ("session-requirements", "Sessions Per Week", ""), ("session-requirements", "Duration Minutes", ""),
    ("session-requirements", "Duration Minutes", "1.5"),
    ("staff-availability", "Available Windows", ""), ("staff-availability", "Blocked Periods", ""),
    ("venue-calendars", "Available Windows", ""), ("venue-calendars", "Blocked Periods", ""),
    ("population-evidence", "Confirmation Status", "UNVERIFIED"),
    ("population-evidence", "Population Evidence Reference", ""),
])
def test_bad_cells_fail_and_preserve_csv_location(pilot, table, column, value):
    rewrite(pilot[0], table, lambda rows: rows[0].update({column: value}))
    result = check(pilot)
    assert not result.readiness.ready_for_generation
    assert result.submission is None
    assert any(row.workbook == f"{table}.csv" and row.row == 2 for row in result.readiness.diagnostics)


@pytest.mark.parametrize("table", ["staff-availability", "venue-calendars"])
def test_missing_calendar_day_blocks(pilot, table):
    rewrite(pilot[0], table, lambda rows: rows.pop(0))
    assert any(row.code == "calendar_coverage" for row in check(pilot).readiness.diagnostics)


@pytest.mark.parametrize("column,value", [("Decision", ""), ("Confirmation Status", "UNVERIFIED"),
    ("Applicable Dates", ""), ("Applicable Streams", ""), ("Applicable Streams", "EVENING")])
def test_unconfirmed_lunch_never_becomes_ready(pilot, column, value):
    rewrite(pilot[0], "policy-confirmations", lambda rows: next(row for row in rows if row["Policy Key"] == "student_lunch").update({column: value}))
    assert not check(pilot).readiness.ready_for_generation


def test_unknown_population_is_not_disjoint(pilot):
    rewrite(pilot[0], "population-evidence", lambda rows: rows[0].update({"Relationship": "UNKNOWN"}))
    assert not check(pilot).readiness.ready_for_generation


def test_duplicate_module_codes_remain_separate(pilot):
    rewrite(pilot[0], "offerings", lambda rows: [row.update({"Module Code": "IT2100"}) for row in rows[:2]])
    result = check(pilot)
    assert result.readiness.ready_for_generation
    offerings = result.submission.inputs["combination_input"]["teaching_input"]["module_offerings"]
    assert len(offerings) == 3 and offerings[0]["offering_id"] != offerings[1]["offering_id"]


def test_parent_cycle_is_rejected(pilot):
    def cycle(rows):
        rows[0]["Parent Group ID"] = rows[2]["Group ID"]
    rewrite(pilot[0], "student-groups", cycle)
    result = check(pilot)
    assert not result.readiness.ready_for_generation
    assert any(row.code == "group_hierarchy" and row.column == "Parent Group ID" and row.row is not None
        for row in result.readiness.diagnostics)


def test_approval_for_unrelated_policy_is_rejected(pilot):
    rewrite(pilot[0], "policy-confirmations", lambda rows: next(row for row in rows if row["Policy Key"] == "fairness").update({"Policy Reference": "unrelated-policy"}))
    assert any(row.code == "policy_reference" for row in check(pilot).readiness.diagnostics)


def test_none_cannot_erase_existing_preferences(pilot):
    rewrite(pilot[0], "policy-confirmations", lambda rows: next(row for row in rows if row["Policy Key"] == "preferences").update({"Decision": "NONE"}))
    assert any(row.code == "policy_none_conflict" for row in check(pilot).readiness.diagnostics)


def test_synthetic_payload_cannot_satisfy_real_request(pilot):
    result = check(pilot, actual_campus=True)
    assert not result.readiness.ready_for_actual_campus and result.submission is None
    assert any(row.code == "input_mode" for row in result.readiness.diagnostics)


def test_changed_confirmed_workload_is_rejected(pilot):
    pilot[1].inputs["staff_input"]["workload_input"]["policy"]["band_rules"]["PROFESSOR"]["weekly_target_minutes"] = 240
    assert any(row.code == "confirmed_workload_values" for row in check(pilot).readiness.diagnostics)


def test_known_lunch_periods_and_workload_stay_unchanged(pilot):
    result = check(pilot)
    raw = result.submission.inputs
    actual = [(row["academic_year"], row["window"]["start_minute"], row["window"]["end_minute"]) for row in raw["student_lunch_policy"]["year_periods"]]
    assert actual == [(1, 750, 810), (2, 750, 810), (3, 810, 870), (4, 810, 870)]
    assert raw["staff_input"]["workload_input"]["policy"] == pilot[1].inputs["staff_input"]["workload_input"]["policy"]


def test_blank_pack_has_no_invented_records_or_examples(tmp_path):
    write_collection_pack(tmp_path)
    for table in TABLES:
        with (tmp_path / f"{table}.csv").open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        if table == "policy-confirmations":
            assert {row["Policy Key"] for row in rows} == set(POLICY_KEYS)
            assert all(all(not value for key, value in row.items() if key != "Policy Key") for row in rows)
        else:
            assert rows == []
    assert (tmp_path / "data-dictionary.csv").is_file()


def test_existing_collection_files_are_never_overwritten(tmp_path):
    original = tmp_path / "offerings.csv"
    original.write_text("preserved", encoding="utf-8")
    with pytest.raises(FileExistsError):
        write_collection_pack(tmp_path)
    assert original.read_text(encoding="utf-8") == "preserved"


def test_header_and_file_failures_are_visible(pilot):
    (pilot[0] / "staff-availability.csv").unlink()
    (pilot[0] / "offerings.csv").write_text("Offering ID,Offering ID\n", encoding="utf-8")
    result = check(pilot)
    assert {"template_file", "template_headers"} <= {row.code for row in result.readiness.diagnostics}


@pytest.mark.parametrize("value", ["09:00-09:00", "12:00-11:00", "24:00-24:00", "09:60-10:00", "9:00-10:00", ""])
def test_window_formats_reject_invalid_or_unknown(value):
    with pytest.raises(ValueError):
        windows(value)


def test_none_and_midnight_are_explicit():
    assert windows("NONE") == []
    assert windows("23:00-24:00") == [dict(start_minute=1380, end_minute=1440)]


def source_for(base):
    teaching = base.inputs["combination_input"]["teaching_input"]
    source = WideImportResult(staff_members=[StaffMember.model_validate(row) for row in teaching["staff_members"]])
    locations = {}
    for index, offering in enumerate(teaching["module_offerings"]):
        location = SourceLocation("synthetic-original.xlsx", "Modules", index+2)
        locations[offering["offering_id"]] = location
        source.source_modules.append(SourceModuleRecord(location, {}, contract_fields={key: value for key, value in offering.items()
            if key not in {"offering_id", "stream"}}))
    for row in teaching["teaching_team_members"]:
        source.source_assignments.append(SourceTeamAssignment(locations[row["offering_id"]], "Staff ID", "Name",
            row["staff_id"], "Synthetic", TeachingRole(row["role"])))
    return source, locations


def test_private_prefill_leaves_unknowns_blank_and_source_intact(pilot, tmp_path):
    source, _ = source_for(pilot[1])
    source.source_modules[0].contract_fields["module_code"] = "IT2100"
    source.source_modules[1].contract_fields["module_code"] = "IT2100"
    before = deepcopy(source)
    output = tmp_path / "private-review"
    write_collection_pack(output, academic_source=source)
    with (output / "offerings.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["Module Code"] == rows[1]["Module Code"] == "IT2100"
    assert rows[0]["Source Row"] != rows[1]["Source Row"]
    assert all(row["Offering ID"] == row["Stream"] == row["Confirmation Status"] == "" for row in rows)
    assert source == before


def attach_source(pilot):
    source, locations = source_for(pilot[1])
    def fill(rows):
        for row in rows:
            location = locations[row["Offering ID"]]
            row.update({"Source Workbook": location.workbook, "Source Worksheet": location.worksheet, "Source Row": str(location.row)})
    rewrite(pilot[0], "offerings", fill)
    return source


def test_original_teams_are_bound_by_source_rows_without_reassignment(pilot):
    source = attach_source(pilot)
    result = check(pilot, academic_source=source)
    assert result.readiness.ready_for_generation, result.readiness.to_dict()
    teams = result.submission.inputs["combination_input"]["teaching_input"]["teaching_team_members"]
    assert teams == pilot[1].inputs["combination_input"]["teaching_input"]["teaching_team_members"]


def test_known_reference_value_cannot_be_changed_in_review_copy(pilot):
    source = attach_source(pilot)
    rewrite(pilot[0], "offerings", lambda rows: rows[0].update({"Module Name": "Unexpected replacement"}))
    result = check(pilot, academic_source=source)
    assert any(row.code == "changed_source_value" and row.column == "Module Name" for row in result.readiness.diagnostics)


def test_cli_defaults_to_actual_and_requires_explicit_synthetic_flag(pilot, tmp_path, capsys):
    base_path = tmp_path / "synthetic-base.json"
    base_path.write_text(pilot[1].model_dump_json(), encoding="utf-8")
    args = ["validate", str(pilot[0]), "--base-submission", str(base_path), "--institution-id", str(pilot[2])]
    assert main(args) == 1
    assert main(args + ["--synthetic"]) == 0
    assert "Synthetic Staff" not in capsys.readouterr().out


def add_row(directory, table, values, *, template_row):
    with (directory / f"{table}.csv").open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers(table), lineterminator="\n")
        row = {key: template_row[key] for key in ("Confirmation Status", "Confirmation Reference", "Source",
            "Source Reference", "Valid From", "Valid Until")}
        writer.writerow({**row, **values})


def approval_row(pilot):
    with (pilot[0] / "policy-confirmations.csv").open(encoding="utf-8", newline="") as handle:
        return next(csv.DictReader(handle))


def test_preference_weight_scope_and_identity_are_explicit(pilot):
    preference = pilot[1].inputs["preference_input"]["preferences"][0]
    add_row(pilot[0], "preference-approvals", {"Preference ID": preference["preference_id"], "Staff ID": preference["staff_id"],
        "Academic Week": pilot[1].inputs["calendar"]["week_reference"], "Stream": "WEEKDAY", "Penalty Weight": "7",
        "Weight Reference": "synthetic:new-approved-weight"}, template_row=approval_row(pilot))
    result = check(pilot)
    assert result.readiness.ready_for_generation, result.readiness.to_dict()
    updated = result.submission.inputs["preference_input"]["preferences"][0]
    assert updated["penalty_weight"] == 7
    assert updated["staff_id"] == preference["staff_id"]
    assert preference["penalty_weight"] == 3


def test_preference_rows_cannot_create_missing_preferences(pilot):
    add_row(pilot[0], "preference-approvals", {"Preference ID": "UNKNOWN", "Staff ID": "UNKNOWN",
        "Academic Week": "synthetic-week", "Stream": "WEEKDAY", "Penalty Weight": "7", "Weight Reference": "synthetic:new"},
        template_row=approval_row(pilot))
    assert any(row.code == "preference_reference" for row in check(pilot).readiness.diagnostics)


def test_approved_numeric_day_part_is_mapped_without_inference(pilot):
    add_row(pilot[0], "time-of-day-windows", {"Day Part": "MORNING", "Time Window": "09:00-12:00",
        "Policy Reference": "synthetic:approved-morning"}, template_row=approval_row(pilot))
    result = check(pilot)
    assert result.readiness.ready_for_generation
    assert result.submission.inputs["preference_policy"]["time_of_day_windows"] == {
        "MORNING": dict(start_minute=540, end_minute=720)}


def test_fairness_cohort_membership_requires_explicit_approved_ids(pilot):
    fairness = pilot[1].inputs["fairness_policy"]
    for cohort in fairness["comparison_groups"]:
        add_row(pilot[0], "fairness-cohorts", {"Cohort ID": cohort["group_id"], "Staff IDs": ";".join(cohort["staff_ids"]),
            "Reason": cohort["reason"], "Policy Reference": fairness["policy_reference"]}, template_row=approval_row(pilot))
    result = check(pilot)
    assert result.readiness.ready_for_generation
    assert result.submission.inputs["fairness_policy"]["comparison_groups"] == [
        {**cohort, "staff_ids": list(cohort["staff_ids"])} for cohort in fairness["comparison_groups"]]


def test_availability_does_not_replace_existing_cap_provenance(pilot):
    result = check(pilot)
    evidence = next(row for row in result.submission.evidence if row.path == ("staff_input", "availability", 0, "weekly_teaching_max_minutes"))
    assert evidence.provenance.reference == "synthetic:12a-fixture"
    assert result.submission.inputs["staff_input"]["availability"][0]["weekly_teaching_max_minutes"] == 300


def test_supplied_original_staff_attributes_are_not_overwritten(pilot):
    source = attach_source(pilot)
    pilot[1].inputs["combination_input"]["teaching_input"]["staff_members"][0]["staff_name"] = "Unexpected replacement"
    assert any(row.code == "changed_staff_source" for row in check(pilot, academic_source=source).readiness.diagnostics)


def test_public_pack_and_dictionary_match_mapping_specification():
    folder = Path(__file__).resolve().parents[1] / "data-collection" / "templates"
    for table in TABLES:
        with (folder / f"{table}.csv").open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            assert reader.fieldnames == headers(table)
            rows = list(reader)
            if table == "policy-confirmations":
                assert len(rows) == 8
                assert all(all(not value for key, value in row.items() if key != "Policy Key") for row in rows)
            else:
                assert rows == []
    with (folder / "data-dictionary.csv").open(encoding="utf-8", newline="") as handle:
        dictionary = list(csv.DictReader(handle))
    assert {(row["Template"], row["Column"]) for row in dictionary} == {
        (f"{table}.csv", header) for table in TABLES for header in headers(table)}
