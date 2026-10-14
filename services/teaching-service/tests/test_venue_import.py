"""Synthetic venues match inspected headers without using campus source records."""

import hashlib
import json

import pytest
from openpyxl import Workbook
from pydantic import ValidationError

from schedence_teaching.excel_import import VenueImportConfig, import_venue_excel
from schedence_teaching.excel_import.cli import main
from schedence_teaching.schemas import Venue, VenueType, VenueVerification


HEADERS = ["Venue_ID", "Venue_Name", "Venue_Type", "Capacity", "Building", "Floor",
           "Lab_Specialization", "Type_Data_Status", "Capacity_Data_Status", "Location_Data_Status", "Notes"]
COLUMNS = dict(zip(Venue.model_fields, HEADERS[:7], strict=True))
CONFIRMED_FIELDS = ["venue_id", "venue_name", "venue_type", "capacity", "provided_location"]


@pytest.fixture
def dataset(tmp_path):
    path = tmp_path / "synthetic-venues.xlsx"
    records = [
        ["SYNTH-A", "Synthetic lecture", "Lecture Hall", 120, "Synthetic", 1, "General",
         "SAMPLE / VERIFY", "RANDOM SAMPLE / VERIFY", "Older source label", "Synthetic old notes"],
        ["SYNTH-B", "Synthetic lab", "Computer Lab", 30, "To Verify", "To Verify", "Synthetic specialty",
         "INFERRED FROM LAB NAME", "RANDOM SAMPLE / VERIFY", "Not derivable from room name", "Private synthetic note"],
        ["SYNTH-C+SYNTH-D", "Synthetic combined lab", "BYOD Lab", 60, "Synthetic", 2, "BYOD",
         "USER PROVIDED", "USER PROVIDED", "Older source label", "Synthetic combined venue"],
        ["SYNTH-E", "Synthetic smart classroom", "Smart Classroom", 240, "Synthetic", "Basement", "General",
         "USER PROVIDED", "USER PROVIDED", "Older source label", None],
    ]
    config = VenueImportConfig(
        layout="venue-v1", venues={"workbook": path.name, "worksheet": "Venue_Master", "header_row": 1},
        columns=COLUMNS, source_metadata_columns=HEADERS[7:],
        unknown_location_values=["To Verify"], verification_file="confirmation.json",
    )
    data = {"path": path, "records": records, "headers": HEADERS.copy(), "config": config, "base": tmp_path}
    save(data)
    confirm(data)
    return data


def save(data):
    book = Workbook()
    sheet = book.active
    sheet.title = "Venue_Master"
    sheet.append(data["headers"])
    for record in data["records"]:
        sheet.append(record)
    book.save(data["path"])
    book.close()


def confirm(data, **overrides):
    payload = {
        "status": "campus_confirmed_development", "evidence_source": "project_owner_reports_campus_confirmation",
        "statement": "Synthetic confirmation for tests only.",
        "workbook_sha256": hashlib.sha256(data["path"].read_bytes()).hexdigest(),
        "record_count": len(data["records"]), "confirmed_fields": CONFIRMED_FIELDS,
        **overrides,
    }
    (data["base"] / "confirmation.json").write_text(json.dumps(payload), encoding="utf-8")


def read(data):
    return import_venue_excel(data["config"], base_dir=data["base"])


def codes(result):
    return {issue.code for issue in result.issues}


def test_confirmed_venues_preserve_source_values_and_old_labels(dataset):
    before = dataset["path"].read_bytes()
    result = read(dataset)
    assert result.valid and result.verification_applied and len(result.venues) == 4
    assert [row.source_values for row in result.source_venues] == [
        dict(zip(HEADERS, row, strict=True)) for row in dataset["records"]
    ]
    assert result.source_venues[0].source_values["Capacity_Data_Status"] == "RANDOM SAMPLE / VERIFY"
    assert result.summary()["data_status"] == "campus_confirmed_development"
    assert dataset["path"].read_bytes() == before
    for venue, row in zip(result.venues, dataset["records"], strict=True):
        assert (venue.venue_id, venue.venue_name, venue.venue_type.value, venue.capacity) == tuple(row[:4])


def test_aggregate_report_and_separate_missing_requirements(dataset):
    summary = read(dataset).summary()
    assert summary["venue_type_counts"] == {kind.value: 1 for kind in VenueType}
    assert summary["capacity_counts"] == {"120": 1, "30": 1, "60": 1, "240": 1}
    assert summary["missing_location_count"] == 1
    assert not summary["university_ready"] and not summary["ready_for_scheduling"]
    assert {"missing_location", "combined_room_booking_unknown", "venue_availability_not_supplied",
            "blocked_periods_not_supplied"} <= set(summary["issue_counts"])
    assert "Private synthetic note" not in json.dumps(summary)


def test_unknown_location_is_not_inferred_and_combined_venue_is_not_split(dataset):
    result = read(dataset)
    assert result.venues[1].building is None and result.venues[1].floor is None
    assert result.source_venues[1].source_values["Floor"] == "To Verify"
    assert result.venues[2].venue_id == "SYNTH-C+SYNTH-D" and result.venues[2].capacity == 60
    assert result.venues[3].floor == "Basement"
    assert "availability" not in Venue.model_fields and "constituent_rooms" not in Venue.model_fields


def test_supplied_locations_resolve_warnings_despite_older_source_status_labels(dataset):
    dataset["records"][1][4:6] = ["Synthetic confirmed building", 11]
    save(dataset)
    confirm(dataset)
    result = read(dataset)
    assert result.valid and result.verification_applied and len(result.venues) == 4
    assert (result.venues[1].building, result.venues[1].floor) == ("Synthetic confirmed building", 11)
    assert result.source_venues[1].source_values["Location_Data_Status"] == "Not derivable from room name"
    assert "missing_location" not in codes(result)
    summary = result.summary()
    assert summary["missing_location_count"] == 0
    assert not any("Missing building/floor" in requirement for requirement in summary["outstanding_requirements"])
    assert summary["issue_counts"] == {
        "combined_room_booking_unknown": 1,
        "venue_availability_not_supplied": 1,
        "blocked_periods_not_supplied": 1,
    }


@pytest.mark.parametrize("column,value", [
    (0, None), (0, " "), (0, 12), (1, None), (2, "Unconfigured room type"),
    (3, 0), (3, -1), (3, True), (3, "120"), (3, 120.5),
])
def test_invalid_required_values_are_located_and_not_imported(dataset, column, value):
    dataset["records"][0][column] = value
    save(dataset)
    confirm(dataset)
    result = read(dataset)
    assert not result.valid and not result.verification_applied and len(result.venues) == 3
    issue = next(issue for issue in result.issues if issue.code == "invalid_venue")
    assert (issue.workbook, issue.worksheet, issue.row, issue.column) == (
        dataset["path"].name, "Venue_Master", 2, HEADERS[column],
    )


@pytest.mark.parametrize("value", ["=60+60", "#VALUE!"])
def test_formula_and_error_cells_are_rejected_without_evaluation(dataset, value):
    dataset["records"][0][3] = value
    save(dataset)
    confirm(dataset)
    result = read(dataset)
    assert "unsupported_cell" in codes(result) and len(result.venues) == 3


@pytest.mark.parametrize("different_values", [False, True])
def test_duplicate_ids_keep_every_source_record_but_accept_neither_candidate(dataset, different_values):
    row = dataset["records"][0].copy()
    if different_values:
        row[1], row[3] = "Different synthetic name", 60
    dataset["records"].append(row)
    save(dataset)
    confirm(dataset)
    result = read(dataset)
    assert not result.valid and len(result.source_venues) == 5 and len(result.venues) == 3
    assert all(venue.venue_id != "SYNTH-A" for venue in result.venues)
    assert {issue.row for issue in result.issues if issue.code == "duplicate_venue_id"} == {2, 6}


@pytest.mark.parametrize("problem", ["missing", "duplicate"])
def test_missing_or_duplicate_headers_are_rejected(dataset, problem):
    dataset["headers"][3] = "Unmapped" if problem == "missing" else "Venue_ID"
    save(dataset)
    confirm(dataset)
    result = read(dataset)
    assert not result.valid and result.venues == []
    assert ("missing_column" if problem == "missing" else "duplicate_column") in codes(result)


def test_unconfirmed_import_does_not_claim_campus_confirmation(dataset):
    dataset["config"].verification_file = None
    result = read(dataset)
    assert result.valid and not result.verification_applied
    assert "verification_not_supplied" in codes(result)
    assert result.summary()["data_status"] == "development_unverified"


@pytest.mark.parametrize("problem,code", [
    ("hash", "verification_source_mismatch"), ("count", "verification_count_mismatch"),
    ("missing", "invalid_verification"), ("invalid", "invalid_verification"),
])
def test_invalid_confirmation_never_applies_to_dataset(dataset, problem, code):
    if problem == "hash":
        confirm(dataset, workbook_sha256="0" * 64)
    elif problem == "count":
        confirm(dataset, record_count=62)
    elif problem == "missing":
        (dataset["base"] / "confirmation.json").unlink()
    else:
        confirm(dataset, confirmed_fields=["availability"])
    result = read(dataset)
    assert not result.valid and not result.verification_applied and code in codes(result)


def test_confirmation_does_not_apply_after_source_values_change(dataset):
    dataset["records"][0][3] = 121
    save(dataset)  # Confirmation still refers to original bytes.
    result = read(dataset)
    assert not result.verification_applied and "verification_source_mismatch" in codes(result)
    assert result.venues[0].capacity == 121  # Import never restores old values or makes up replacements.


def test_source_edit_during_read_invalidates_all_imported_venues(dataset, monkeypatch):
    from schedence_teaching.excel_import import venue_adapter
    original = venue_adapter.read_source
    def changed_source(*args):
        records = original(*args)
        dataset["records"][0][3] = 121
        save(dataset)
        return records
    monkeypatch.setattr(venue_adapter, "read_source", changed_source)
    result = read(dataset)
    assert not result.valid and not result.verification_applied and result.venues == []
    assert "source_changed" in codes(result)


@pytest.mark.parametrize("problem", ["missing", "corrupt", "unsupported", "worksheet"])
def test_invalid_workbook_is_reported(dataset, problem):
    if problem == "missing":
        dataset["path"].unlink()
    elif problem == "corrupt":
        dataset["path"].write_bytes(b"not xlsx")
    elif problem == "unsupported":
        target = dataset["base"] / "unsupported.xls"
        target.write_bytes(dataset["path"].read_bytes())
        dataset["config"].venues.workbook = target
    else:
        dataset["config"].venues.worksheet = "Absent"
    result = read(dataset)
    assert not result.valid and result.venues == [] and not result.verification_applied


def test_empty_worksheet_is_reported_without_invented_venues(dataset):
    dataset["records"] = []
    save(dataset)
    dataset["config"].verification_file = None
    result = read(dataset)
    assert result.valid and result.venues == [] and "empty_table" in codes(result)


@pytest.mark.parametrize("problem", ["missing", "unknown", "duplicate", "metadata_overlap"])
def test_mapping_must_be_explicit_and_unambiguous(dataset, problem):
    payload = dataset["config"].model_dump(mode="json")
    if problem == "missing":
        del payload["columns"]["capacity"]
    elif problem == "unknown":
        payload["columns"]["invented_availability"] = "Unknown"
    elif problem == "duplicate":
        payload["columns"]["capacity"] = "Venue_ID"
    else:
        payload["source_metadata_columns"].append("Venue_ID")
    with pytest.raises(ValidationError):
        VenueImportConfig.model_validate(payload)


def test_confirmation_fields_are_explicit_and_unique(dataset):
    payload = json.loads((dataset["base"] / "confirmation.json").read_text())
    payload["confirmed_fields"].append("capacity")
    with pytest.raises(ValidationError, match="duplicates"):
        VenueVerification.model_validate(payload)


def test_cli_runs_venue_dry_run_without_outputting_source_records(dataset, capsys):
    config = dataset["base"] / "mapping.json"
    config.write_text(dataset["config"].model_dump_json(), encoding="utf-8")
    assert main(["--config", str(config)]) == 0
    output = capsys.readouterr().out
    report = json.loads(output)
    assert report["imported_counts"] == {"venues": 4} and report["verification_applied"]
    assert "Synthetic lecture" not in output and "Private synthetic note" not in output
    confirm(dataset, workbook_sha256="0" * 64)
    assert main(["--config", str(config)]) == 1
    assert not json.loads(capsys.readouterr().out)["verification_applied"]
