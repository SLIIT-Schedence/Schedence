"""The generated synthetic dataset: counts, uniqueness, determinism, realism."""

from __future__ import annotations

import dataclasses
import re
from collections import Counter

import pytest

from app.seed.assumptions import DEFAULT_ASSUMPTIONS, SeedAssumptions
from app.seed.catalog import load_catalog
from app.seed.generator import exam_days, generate_dataset
from app.seed.validation import validate_dataset

SCOPE = set(DEFAULT_ASSUMPTIONS.specializations)


@pytest.fixture(scope="module")
def catalog():
    return load_catalog(DEFAULT_ASSUMPTIONS.catalog_path)


@pytest.fixture(scope="module")
def data():
    return generate_dataset()


def test_record_counts(data, catalog) -> None:
    in_scope = [m for m in catalog if m.semester == 2 and set(m.specializations) & SCOPE]
    assert len(data.modules) == len(in_scope) >= 30
    assert len(data.students) == sum(size for _, _, size in DEFAULT_ASSUMPTIONS.cohort_sizes) == 390
    assert 1200 <= len(data.registrations) <= 2000
    assert len(data.venues) == 9
    assert 15 <= len(data.venue_blocks) <= 30
    assert len(data.exam_periods) == 1


def test_modules_are_real_catalogue_records(data, catalog) -> None:
    by_code = {m.module_code: m for m in catalog}
    for m in data.modules:
        source = by_code[m.module_code]
        assert (m.module_name, m.academic_year, m.semester, m.credits, m.difficulty, m.exam_type,
                m.duration_minutes, m.has_extra_reading_time) == (
            source.module_name, source.academic_year, source.semester, source.credits, source.difficulty,
            source.exam_type, source.duration_minutes, source.has_extra_reading_time)
    assert not any(m.module_code.startswith("SYN") for m in data.modules)


def test_corrected_module_is_used(data) -> None:
    names = {m.module_code: m.module_name for m in data.modules}
    assert names["IT3041"] == "Information Retrieval and Web Analytics"
    assert "IT3060" not in names  # IT3060 is Human Computer Interaction (Semester 1)


def test_all_exam_types_are_present(data) -> None:
    assert {m.exam_type for m in data.modules} == {"written", "computer_based", "hybrid"}


def test_reading_time_follows_flag(data) -> None:
    for m in data.modules:
        assert m.reading_time_minutes == (DEFAULT_ASSUMPTIONS.reading_time_minutes if m.has_extra_reading_time else 0)
    assert any(m.has_extra_reading_time for m in data.modules)


def test_every_module_specialization_is_preserved(data, catalog) -> None:
    by_code = {m.module_code: set(m.specializations) for m in catalog}
    links = data.specializations_by_module()
    for m in data.modules:
        assert links[m.module_code] == by_code[m.module_code]
    assert any(len(s) > 1 for s in links.values())


def test_student_ids_are_unique_synthetic_and_match_intake(data) -> None:
    ids = [s.student_id for s in data.students]
    assert len(ids) == len(set(ids))
    for s in data.students:
        assert re.fullmatch(r"IT3[0-3]\d{6}", s.student_id), s.student_id
        assert int(s.student_id[2:4]) == 30 + (4 - s.academic_year)
        assert s.specialization in SCOPE


def test_registrations_reference_known_students_and_modules(data) -> None:
    students = {s.student_id: s for s in data.students}
    links = data.specializations_by_module()
    pairs = [(r.student_id, r.module_code) for r in data.registrations]
    assert len(pairs) == len(set(pairs))
    for r in data.registrations:
        assert students[r.student_id].specialization in links[r.module_code]


def test_normal_registrations_match_year(data) -> None:
    students = {s.student_id: s for s in data.students}
    modules = {m.module_code: m for m in data.modules}
    for r in data.registrations:
        if r.registration_type == "normal":
            assert modules[r.module_code].academic_year == students[r.student_id].academic_year


def test_repeat_registrations_are_cross_year(data) -> None:
    students = {s.student_id: s for s in data.students}
    modules = {m.module_code: m for m in data.modules}
    repeats = [r for r in data.registrations if r.registration_type == "repeat"]
    assert len(repeats) >= 40
    for r in repeats:
        assert modules[r.module_code].academic_year < students[r.student_id].academic_year
    for student_id in {r.student_id for r in repeats}:
        assert any(r.student_id == student_id and r.registration_type == "normal" for r in data.registrations)


def test_every_student_and_module_has_registrations(data) -> None:
    assert {r.student_id for r in data.registrations} == {s.student_id for s in data.students}
    assert all(n > 0 for n in data.registration_counts().values())


def test_registration_counts_are_derived_from_records(data) -> None:
    counts = data.registration_counts()
    assert sum(counts.values()) == len(data.registrations)
    assert counts == dict(Counter(r.module_code for r in data.registrations))


def test_venue_capacities_and_compatibility(data) -> None:
    for v in data.venues:
        assert 0 < v.exam_capacity <= v.normal_capacity
    for m in data.modules:
        allowed = DEFAULT_ASSUMPTIONS.venue_types_for(m.exam_type, m.module_code)
        assert any(v.venue_type in allowed for v in data.venues), m.module_code


def test_hybrid_venue_rule_is_configurable() -> None:
    a = DEFAULT_ASSUMPTIONS
    assert "lecture_hall" in a.venue_types_for("hybrid", "IT1050")  # no lab required by default
    strict = dataclasses.replace(a, hybrid_lab_required_modules=("IT1050",))
    assert strict.venue_types_for("hybrid", "IT1050") == ("computer_lab",)
    assert "lecture_hall" in strict.venue_types_for("hybrid", "IT1080")
    labs_only = dataclasses.replace(a, hybrid_venue_types=("computer_lab",))
    assert labs_only.venue_types_for("hybrid", "IT1080") == ("computer_lab",)
    assert a.venue_types_for("computer_based", "IT1010") == ("computer_lab",)


def test_venue_blocks_have_valid_ranges_and_no_overlaps(data) -> None:
    period = data.exam_periods[0]
    days = set(exam_days(period, data.exam_period_exclusions))
    seen: dict[tuple, list] = {}
    for b in data.venue_blocks:
        assert b.start_time < b.end_time
        assert period.day_start_time <= b.start_time and b.end_time <= period.day_end_time
        assert b.block_date in days
        for other in seen.get((b.venue_id, b.block_date), []):
            assert b.end_time <= other.start_time or other.end_time <= b.start_time
        seen.setdefault((b.venue_id, b.block_date), []).append(b)
    assert {b.source for b in data.venue_blocks} == {"teaching_timetable", "maintenance"}


def test_exam_period_has_exam_days_and_excludes_weekends(data) -> None:
    days = exam_days(data.exam_periods[0], data.exam_period_exclusions)
    assert len(days) >= 5
    assert all(d.weekday() < 5 for d in days)
    assert not {e.excluded_date for e in data.exam_period_exclusions} & set(days)


def test_dataset_contains_hard_scheduling_cases(data) -> None:
    counts = data.registration_counts()
    largest_lab = max(v.exam_capacity for v in data.venues if v.venue_type == "computer_lab")
    # A computer-based exam larger than any single lab: needs several labs or sessions.
    assert any(m.exam_type == "computer_based" and counts[m.module_code] > largest_lab for m in data.modules)
    # Modules shared across specializations create cross-programme clashes.
    assert max(counts.values()) > 100
    assert {m.difficulty for m in data.modules} >= {2, 3, 4, 5}


def test_generation_is_deterministic() -> None:
    assert generate_dataset() == generate_dataset()


def test_different_seed_changes_dataset_but_stays_valid() -> None:
    assumptions = dataclasses.replace(SeedAssumptions(), random_seed=1)
    other = generate_dataset(assumptions)
    assert other != generate_dataset()
    assert validate_dataset(other, assumptions).ok
