"""Campus venue import: cleaning rules and the committed catalogue (no database)."""

from __future__ import annotations

import csv
from pathlib import Path

from app.seed.assumptions import DEFAULT_ASSUMPTIONS
from app.seed.venue_import import clean_venues, exam_capacity, import_venues

T = "\t"


def _line(*fields: object) -> str:
    return T.join(str(f) for f in fields)


def test_six_field_row_is_kept_exactly() -> None:
    result = clean_venues([_line("A504", "A504 Smart Classroom", "Smart Classroom", 120, "A", 5)])
    [venue] = result.venues
    assert (venue.venue_id, venue.venue_name, venue.venue_type, venue.building, venue.floor, venue.full_capacity) == (
        "A504", "A504 Smart Classroom", "smart_classroom", "A", 5, 120)
    assert result.ok and not result.corrections


def test_missing_name_column_uses_the_code_and_is_logged() -> None:
    result = clean_venues([_line("F1301+F1302", "Computer Lab", 30, "F", 13)])
    [venue] = result.venues
    assert venue.venue_id == venue.venue_name == "F1301+F1302"
    [correction] = result.corrections
    assert (correction.field, correction.corrected_value) == ("venue_name", "F1301+F1302")


def test_special_codes_are_not_changed() -> None:
    result = clean_venues([_line("Robotics Lab", "Robotics Lab", "Computer Lab", 30, "F", 11),
                           _line("CyberSecLab", "CyberSecLab", "Computer Lab", 30, "G", 9)])
    assert [v.venue_id for v in result.venues] == ["Robotics Lab", "CyberSecLab"]


def test_whitespace_is_trimmed_and_logged() -> None:
    result = clean_venues([_line(" G601 ", "G601", "Lecture Hall ", 240, "G", 6)])
    assert result.venues[0].venue_id == "G601" and result.venues[0].venue_type == "lecture_hall"
    assert {c.field for c in result.corrections} == {"whitespace"}


def test_exact_duplicate_line_is_removed_once() -> None:
    row = _line("B402", "B402", "Computer Lab", 60, "B", 4)
    result = clean_venues([row, "", row])
    assert len(result.venues) == 1 and len(result.removed_duplicates) == 1 and result.ok


def test_conflicting_records_are_never_merged() -> None:
    result = clean_venues([_line("E102", "E102", "Computer Lab", 60, "E", 1),
                           _line("E102", "E102", "Computer Lab", 30, "E", 1)])
    assert not result.ok and result.venues == []
    assert "conflicting records" in result.rejected[0].issue


def test_codes_differing_only_by_case_are_flagged() -> None:
    result = clean_venues([_line("MMlab", "MMlab", "Computer Lab", 30, "G", 13),
                           _line("MMLab", "MM Lab", "Computer Lab", 30, "G", 13)])
    assert not result.ok and all("only by case" in p.issue for p in result.rejected)


def test_bad_rows_are_rejected_not_guessed() -> None:
    result = clean_venues([_line("X1", "X1", "Auditorium", 100, "X", 1),
                           _line("X2", "X2", "Lecture Hall", "lots", "X", 1),
                           _line("X3", "X3", "Lecture Hall", 0, "X", 1),
                           _line("X4", "Lecture Hall", 100)])
    assert [p.source_line for p in result.rejected] == [1, 2, 3, 4]
    assert result.venues == []


def test_failed_import_writes_nothing(tmp_path: Path) -> None:
    source = tmp_path / "venue.txt"
    source.write_text(_line("X1", "X1", "Auditorium", 100, "X", 1) + "\n", encoding="utf-8")
    assert not import_venues(source, tmp_path / "out").ok
    assert not (tmp_path / "out").exists()


def test_import_writes_catalogue_and_report(tmp_path: Path) -> None:
    source = tmp_path / "venue.txt"
    source.write_text("\r\n".join([_line("A304", "A304", "Lecture Hall", 120, "A", 3),
                                   _line("F1303+F1304", "Computer Lab", 30, "F", 13)]) + "\r\n", encoding="utf-8")
    result = import_venues(source, tmp_path)
    assert result.ok
    rows = list(csv.DictReader((tmp_path / "venue_catalog.csv").open(encoding="utf-8")))
    assert [r["venue_id"] for r in rows] == ["A304", "F1303+F1304"]
    report = (tmp_path / "venue_cleaning_report.md").read_text(encoding="utf-8")
    assert "name column missing" in report and "| lecture_hall | 1 | 120 | 60 |" in report


def test_exam_capacity_rule() -> None:
    assert [exam_capacity(c) for c in (240, 120, 60, 30, 45)] == [120, 60, 30, 15, 22]


# --- The committed catalogue ----------------------------------------------------------


def _committed() -> list[dict[str, str]]:
    with DEFAULT_ASSUMPTIONS.venue_catalog_path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_committed_catalogue_has_62_unique_venues() -> None:
    rows = _committed()
    assert len(rows) == 62
    assert len({r["venue_id"] for r in rows}) == 62
    assert len({r["venue_name"] for r in rows}) == 62
    assert {r["venue_type"] for r in rows} == {"lecture_hall", "smart_classroom", "computer_lab", "byod_lab"}
    assert all(r["building"] and r["floor"].isdigit() and int(r["full_capacity"]) > 0 for r in rows)


def test_committed_catalogue_keeps_supplied_values() -> None:
    rows = {r["venue_id"]: r for r in _committed()}
    assert (rows["F303"]["full_capacity"], rows["F303"]["building"], rows["F303"]["floor"]) == ("240", "F", "3")
    assert rows["G1305"]["venue_name"] == "G1305 Linux Lab"
    assert rows["Robotics Lab"]["venue_type"] == "computer_lab"
    assert rows["F1303+F1304"]["venue_name"] == "F1303+F1304"
    assert rows["G1105"]["venue_type"] == "byod_lab"
