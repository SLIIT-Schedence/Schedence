"""Catalogue import: cleaning rules, duplicate handling, reports, xlsx reading, committed CSVs."""

from __future__ import annotations

import csv
import zipfile
from pathlib import Path

import pytest

from app.seed.catalog_import import (
    CATALOG_FIELDS,
    DATA_DIR,
    AdminEvidence,
    Decision,
    clean_catalog,
    cleaning_report,
    read_decisions,
)
from app.seed.xlsx import read_workbook, sheet_records

_row_counter = iter(range(2, 10_000))


def exam_row(code, name="Some Module", year=2, semester=2, spec="IT", exam="Written", duration=120,
             reading="No", difficulty=3, credits=4, row=None):
    return {
        "_row": row or next(_row_counter), "Module_Code": code, "Module_Name": name, "Lecturer_Name": "Dr. X",
        "Year": str(year), "Semester": f"Semester {semester}", "Specialization": spec,
        "Module_Credits": str(credits), "Exam_Type": exam, "Exam_Duration_Minutes": str(duration),
        "Extra_Reading_Time": reading, "Difficulty_Level": str(difficulty),
    }


NO_ADMIN = AdminEvidence({}, {})


def _codes(result):
    return [e.module.module_code for e in result.entries]


def test_single_record_is_kept_and_parsed() -> None:
    result = clean_catalog([exam_row("IT2010", exam="Computer-Based", reading="Yes", spec="SE, DS, IT")], NO_ADMIN)
    [entry] = result.entries
    m = entry.module
    assert (m.exam_type, m.has_extra_reading_time, m.specializations) == ("computer_based", True, ("DS", "IT", "SE"))
    assert entry.resolution == "single_record" and entry.admin_check == "not_in_admin"
    assert result.removed == []


def test_hybrid_exam_type_is_supported() -> None:
    assert clean_catalog([exam_row("IT2010", exam="Hybrid")], NO_ADMIN).entries[0].module.exam_type == "hybrid"


def test_duplicates_are_not_resolved_by_code_digit() -> None:
    rows = [exam_row("IT2030", year=1), exam_row("IT2030", year=2, exam="Hybrid")]
    result = clean_catalog(rows, NO_ADMIN)
    assert result.entries == [] and result.removed == []
    review = [r for r in result.review if r.issue == "duplicate_unresolved"]
    assert sorted(r.module.academic_year for r in review) == [1, 2]


def test_duplicate_resolved_when_exactly_one_matches_admin() -> None:
    rows = [exam_row("IE3010", year=2, semester=1), exam_row("IE3010", year=3, semester=1, exam="Hybrid")]
    result = clean_catalog(rows, AdminEvidence({"IE3010": frozenset({(3, 1)})}, {}))
    [entry] = result.entries
    assert entry.resolution == "admin_confirmed" and entry.module.academic_year == 3
    assert [r.uncertain for r in result.removed] == [False]


def test_decision_resolves_duplicate_and_logs_removed_record() -> None:
    rows = [exam_row("SE3020", year=2, semester=1, row=88), exam_row("SE3020", year=3, semester=2, row=135)]
    result = clean_catalog(rows, NO_ADMIN, decisions={"SE3020": Decision((3, 2), "team_decision", "Record B")})
    [entry] = result.entries
    assert entry.resolution == "team_decision" and entry.module.term == (3, 2)
    [removed] = result.removed
    assert (removed.removed.source_row, removed.kept.source_row) == (88, 135)
    assert removed.uncertain and removed.note == "Record B"


def test_decision_matching_nothing_stays_in_review() -> None:
    rows = [exam_row("SE3020", year=2), exam_row("SE3020", year=3)]
    result = clean_catalog(rows, NO_ADMIN, decisions={"SE3020": Decision((4, 1), "team_decision")})
    assert result.entries == [] and result.removed == []
    assert all("matches no candidate" in r.note for r in result.review)


def test_specializations_lost_with_removed_record_are_noted() -> None:
    rows = [exam_row("IT2030", year=1, spec="CSNE, IT"), exam_row("IT2030", year=2, spec="IT")]
    result = clean_catalog(rows, NO_ADMIN, decisions={"IT2030": Decision((2, 2), "team_decision")})
    assert "only in removed record: CSNE" in result.removed[0].note


def test_identical_duplicates_are_merged() -> None:
    result = clean_catalog([exam_row("IT1010"), exam_row("IT1010")], NO_ADMIN)
    assert _codes(result) == ["IT1010"]
    assert result.entries[0].resolution == "identical_records"
    assert [r.uncertain for r in result.removed] == [False]


def test_code_correction_is_applied_and_logged() -> None:
    rows = [
        exam_row("IT3060", "Human Computer Interaction", year=3, semester=1),
        exam_row("IT3060", "Information Retrieval and Web Analytics", year=3, semester=2, spec="DS"),
    ]
    admin = AdminEvidence({}, {"IT3041": "Information Retrieval and Web Analytics"})
    result = clean_catalog(rows, admin)
    assert _codes(result) == ["IT3041", "IT3060"]
    [fix] = result.corrections
    assert (fix.field, fix.original_value, fix.corrected_value) == ("module_code", "IT3060", "IT3041")
    assert "Main Admin dataset lists IT3041" in fix.reason


@pytest.mark.parametrize(
    "wrong, right",
    [
        ("Data Sceince & Analytics", "Data Science & Analytics"),
        ("Software Quality Asuarance", "Software Quality Assurance"),
        ("DataBase Management Systems", "Database Management Systems"),
        ("Wide area Networks", "Wide Area Networks"),
        ("Massive Data Processing and cloud Computing", "Massive Data Processing and Cloud Computing"),
    ],
)
def test_spelling_corrections_keep_original(wrong: str, right: str) -> None:
    result = clean_catalog([exam_row("IT9999", wrong)], NO_ADMIN)
    assert result.entries[0].module.module_name == right
    assert result.corrections[0].original_value == wrong


def test_unicode_ligature_is_normalized_and_logged() -> None:
    result = clean_catalog([exam_row("IE4080", "Software Deﬁned Networks")], NO_ADMIN)
    assert result.entries[0].module.module_name == "Software Defined Networks"
    assert result.corrections[0].reason.startswith("unicode normalization")


def test_admin_year_semester_difference_is_flagged_but_kept() -> None:
    admin = AdminEvidence({"SE3050": frozenset({(3, 2)})}, {})
    result = clean_catalog([exam_row("SE3050", year=3, semester=1)], admin)
    assert result.entries[0].admin_check == "differs"
    assert [r.issue for r in result.review] == ["admin_year_semester_differs"]


def test_invalid_record_is_reported_not_imported() -> None:
    result = clean_catalog([exam_row("IT1010", exam="Oral")], NO_ADMIN)
    assert result.entries == [] and result.review[0].issue == "invalid_record"


def test_lecturer_names_are_never_output() -> None:
    assert not any("lecturer" in f.lower() for f in CATALOG_FIELDS)


def test_read_decisions(tmp_path: Path) -> None:
    path = tmp_path / "d.csv"
    path.write_text(
        "module_code,academic_year,semester,basis,note\nse3020,3,2,team_decision,checked\n", encoding="utf-8"
    )
    assert read_decisions(path) == {"SE3020": Decision((3, 2), "team_decision", "checked")}
    assert read_decisions(tmp_path / "missing.csv") == {}


def test_cleaning_report_lists_corrections_and_removals() -> None:
    rows = [exam_row("SE3020", year=2), exam_row("SE3020", year=3), exam_row("IT9999", "Wide area Networks")]
    result = clean_catalog(rows, NO_ADMIN, decisions={"SE3020": Decision((3, 2), "code_year_rule")})
    report = cleaning_report(result)
    assert "Duplicate records removed: **1** (1 marked uncertain)" in report
    assert "| IT9999 |" in report and "Wide Area Networks" in report


def test_xlsx_reader_reads_shared_and_inline_strings(tmp_path: Path) -> None:
    path = tmp_path / "book.xlsx"
    main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/workbook.xml", f'<workbook xmlns="{main}" xmlns:r="{rel}"><sheets>'
                   '<sheet name="Data" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr("xl/sharedStrings.xml", f'<sst xmlns="{main}"><si><t>Code</t></si><si><t>Year</t></si>'
                   '<si><t>IT1010</t></si></sst>')
        z.writestr("xl/worksheets/sheet1.xml", f'<worksheet xmlns="{main}"><sheetData>'
                   '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
                   '<row r="3"><c r="A3" t="s"><v>2</v></c><c r="B3"><v>1</v></c></row>'
                   '<row r="4"><c r="A4" t="inlineStr"><is><t>SE1010</t></is></c></row>'
                   '</sheetData></worksheet>')
    records = sheet_records(read_workbook(path)["Data"])
    assert records == [{"Code": "IT1010", "Year": "1", "_row": 3}, {"Code": "SE1010", "Year": None, "_row": 4}]


# --- The committed, cleaned files ------------------------------------------------


def _read(name: str) -> list[dict[str, str]]:
    with (DATA_DIR / name).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_committed_catalog_is_clean() -> None:
    catalog = _read("module_catalog.csv")
    codes = [r["module_code"] for r in catalog]
    assert len(codes) == len(set(codes)) == 181  # 180 source codes + IT3041
    names = {r["module_code"]: r["module_name"] for r in catalog}
    assert names["IT3041"] == "Information Retrieval and Web Analytics"
    assert names["IT3060"] == "Human Computer Interaction"
    assert names["IE4080"] == "Software Defined Networks"
    assert all(n.isascii() for n in names.values())
    assert not any(bad in n for n in names.values() for bad in ("Sceince", "Asuarance", "DataBase", " cloud ", " area "))
    assert {r["exam_type"] for r in catalog} == {"written", "computer_based", "hybrid"}


def test_committed_catalog_resolves_every_duplicate() -> None:
    assert not [r for r in _read("module_review.csv") if r["issue"] == "duplicate_unresolved"]
    removed = _read("removed_duplicates.csv")
    assert len(removed) == len({r["module_code"] for r in removed}) == 26
    assert all(r["uncertain"] == "yes" for r in removed)


def test_committed_pp1_decisions_keep_record_b() -> None:
    catalog = {r["module_code"]: r for r in _read("module_catalog.csv")}
    record_b = {"IE2012": "2/2", "IE2022": "2/1", "IT2030": "2/2", "IT3010": "3/1", "IT3011": "3/2",
                "IT3020": "3/1", "IT4020": "4/1", "SE3010": "3/1", "SE3020": "3/2"}
    for code, term in record_b.items():
        assert f"{catalog[code]['academic_year']}/{catalog[code]['semester']}" == term, code
        assert catalog[code]["resolution"] == "team_decision"


def test_committed_corrections_record_originals() -> None:
    originals = {(r["field"], r["original_value"]) for r in _read("catalog_corrections.csv")}
    assert ("module_code", "IT3060") in originals
    assert ("module_name", "Data Sceince & Analytics") in originals
    assert ("module_name", "Software Deﬁned Networks") in originals
