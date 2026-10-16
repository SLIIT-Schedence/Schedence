from types import SimpleNamespace

import pytest

from schedence_teaching.persistence.source_review import HEADERS, validate_review_rows


def fixture_rows():
    source = SimpleNamespace(location=SimpleNamespace(row=60), contract_fields={"academic_year": 4},
        source_values=dict(Module_Code="TEST4000", Module_Name="Synthetic module", Year=4,
            Semester="Semester 1", Specialization="IT", Lecture_Hours_Week=2,
            Tutorial_Hours_Week=0, Workshop_Hours_Week=0, Lab_Hours_Week=2))
    values = dict(zip(HEADERS, (60, "TEST4000", "Synthetic module", 4, "Semester 1", "IT",
        2, 0, 0, 2, 4, "NONE", "UNCLASSIFIED", "YES", "Synthetic test review")))
    return source, values


def test_review_candidate_is_not_an_academic_elective_classification():
    source, values = fixture_rows()
    result, = validate_review_rows([(6, values)], [source])
    assert result.pp1_test_candidate and result.academic_classification == "UNCLASSIFIED"


def test_review_can_exclude_a_candidate_without_removing_its_academic_source():
    source, values = fixture_rows()
    values["PP1_TEST_CANDIDATE"] = "NO"
    result, = validate_review_rows([(6, values)], [source])
    assert not result.pp1_test_candidate and source.source_values["Lecture_Hours_Week"] == 2


@pytest.mark.parametrize("column,value", [("Module_Code", "CHANGED"), ("Module_Name", "Changed"),
    ("Year", 3), ("Semester", "Semester 2"), ("Specializations", "SE"), ("Lecture_h", 1),
    ("Tutorial_h", 1), ("Workshop_h", 1), ("Lab_h", 0), ("Total_Contact_h", 99),
    ("Academic_Classification", "UNKNOWN"), ("PP1_TEST_CANDIDATE", "MAYBE"), ("Source_Row", 61)])
def test_review_rejects_inconsistent_or_unknown_fields(column, value):
    source, values = fixture_rows()
    values[column] = value
    with pytest.raises(ValueError):
        validate_review_rows([(6, values)], [source])


def test_review_rejects_duplicate_rows():
    source, values = fixture_rows()
    with pytest.raises(ValueError, match="repeated"):
        validate_review_rows([(6, values), (7, values)], [source])


def test_review_rejects_missing_source_coverage():
    source, _ = fixture_rows()
    with pytest.raises(ValueError, match="cover"):
        validate_review_rows([], [source])
