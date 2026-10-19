"""The generated synthetic dataset: counts, uniqueness, determinism, realism."""

from __future__ import annotations

import dataclasses
import re
from collections import Counter

import pytest

from app.seed.assumptions import DEFAULT_ASSUMPTIONS, SeedAssumptions
from app.seed.catalog import load_catalog, load_venues
from app.seed.generator import exam_capacity, exam_days, generate_dataset
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
    assert len(data.venues) == 62
    assert len(data.venue_blocks) == 27
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


def test_venues_are_the_campus_catalogue(data) -> None:
    source = {v.venue_id: v for v in load_venues(DEFAULT_ASSUMPTIONS.venue_catalog_path)}
    assert len(source) == 62
    assert {v.venue_id for v in data.venues} == set(source)
    for v in data.venues:
        s = source[v.venue_id]
        assert (v.venue_name, v.venue_type, v.building, v.floor, v.normal_capacity) == (
            s.venue_name, s.venue_type, s.building, s.floor, s.full_capacity)
    assert Counter(v.venue_type for v in data.venues) == {
        "lecture_hall": 31, "smart_classroom": 10, "computer_lab": 19, "byod_lab": 2}


def test_special_venue_codes_are_kept_exactly(data) -> None:
    venues = {v.venue_id: v for v in data.venues}
    for code in ("F1301+F1302", "F1303+F1304", "CyberSecLab", "Emblab", "DClab", "Robotics Lab", "MMlab"):
        assert venues[code].venue_name == code
        assert venues[code].venue_type == "computer_lab"
    assert venues["A504"].venue_name == "A504 Smart Classroom"
    assert venues["G1305"].venue_name == "G1305 Linux Lab"
    assert (venues["CyberSecLab"].building, venues["CyberSecLab"].floor) == ("G", 9)


def test_exam_capacity_is_half_of_full_capacity_rounded_down(data) -> None:
    for v in data.venues:
        assert v.exam_capacity == v.normal_capacity // 2
    examples = {240: 120, 120: 60, 60: 30, 30: 15, 31: 15, 1: 0}
    assert {full: exam_capacity(full) for full in examples} == examples
    assert {v.normal_capacity: v.exam_capacity for v in data.venues} == {240: 120, 120: 60, 60: 30, 30: 15}


def test_written_and_computer_based_venue_rules() -> None:
    a = DEFAULT_ASSUMPTIONS
    assert a.venue_types_for("written", "IT1030") == ("lecture_hall", "smart_classroom")
    assert a.venue_types_for("computer_based", "IT1010") == ("computer_lab",)  # BYOD labs are not used


def test_venue_catalogue_does_not_change_other_data(data) -> None:
    fewer = load_venues(DEFAULT_ASSUMPTIONS.venue_catalog_path)[:40]
    other = generate_dataset(venue_catalog=fewer)
    assert len(other.venues) == 40
    for name in ("modules", "module_specializations", "students", "registrations", "exam_periods",
                 "exam_period_exclusions"):
        assert getattr(other, name) == getattr(data, name), name


def test_simulated_blocks_are_labelled(data) -> None:
    for b in data.venue_blocks:
        assert b.source.startswith("simulated_") and b.reason.startswith("SIMULATED"), b
        assert b.source_ref is None


def test_max_rooms_per_exam_is_configurable(data) -> None:
    assert DEFAULT_ASSUMPTIONS.max_rooms_per_exam is None
    assert validate_dataset(data, dataclasses.replace(DEFAULT_ASSUMPTIONS, max_rooms_per_exam=4)).ok
    report = validate_dataset(data, dataclasses.replace(DEFAULT_ASSUMPTIONS, max_rooms_per_exam=3))
    assert any("IT1100" in e and "4 rooms" in e for e in report.errors), report.errors


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
    assert Counter(b.source for b in data.venue_blocks) == {"simulated_teaching": 22, "simulated_maintenance": 5}


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


def test_current_period_is_a_final_examination(data) -> None:
    [period] = data.exam_periods
    assert period.exam_kind == DEFAULT_ASSUMPTIONS.exam_kind == "FINAL"


def test_period_records_default_to_final() -> None:
    # Older callers that do not pass a kind keep getting a FINAL period.
    from app.seed.records import ExamPeriod

    fields = {f.name: f for f in dataclasses.fields(ExamPeriod)}
    assert fields["exam_kind"].default == "FINAL"


def test_real_modules_keep_final_only_regular_defaults(data) -> None:
    # The catalogue has no MID, Block Mode or pro-rata data: nothing is assumed.
    for m in data.modules:
        assert (m.has_final_exam, m.has_mid_exam, m.mid_duration_minutes, m.delivery_mode, m.block_category) == (
            True, False, None, "regular", None), m.module_code


def test_synthetic_eligibility_settings_mark_only_the_listed_modules(data) -> None:
    first, second, third = (m.module_code for m in data.modules[:3])
    assumptions = SeedAssumptions(synthetic_mid_exams=((first, 60),), synthetic_no_final_modules=(second,),
                                  synthetic_block_modules=((second, "pro_rata"), (third, "full_module")))
    other = {m.module_code: m for m in generate_dataset(assumptions).modules}
    assert other[first].has_mid_exam and other[first].mid_duration_minutes == 60 and other[first].has_final_exam
    assert not other[second].has_final_exam and other[second].block_category == "pro_rata"
    assert other[third].delivery_mode == "block" and other[third].block_category == "full_module"
    assert sum(m.has_mid_exam or m.delivery_mode == "block" or not m.has_final_exam
               for m in other.values()) == 3
    assert validate_dataset(generate_dataset(assumptions), assumptions).ok
