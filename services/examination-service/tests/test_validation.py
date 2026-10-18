"""validate_dataset() accepts the generated data and rejects broken data."""

from __future__ import annotations

import dataclasses
import datetime as dt

import pytest

from app.seed.generator import generate_dataset
from app.seed.records import ModuleSpecialization, Registration, Student, Venue, VenueBlock
from app.seed.validation import validate_dataset


@pytest.fixture(scope="module")
def data():
    return generate_dataset()


def _with(data, **changes):
    return dataclasses.replace(data, **changes)


def _assert_error(data, fragment: str) -> None:
    report = validate_dataset(data)
    assert not report.ok
    assert any(fragment in e for e in report.errors), report.errors


def test_generated_dataset_is_valid(data) -> None:
    report = validate_dataset(data)
    assert report.ok, report.errors


def test_duplicate_student_is_rejected(data) -> None:
    _assert_error(_with(data, students=data.students + (data.students[0],)), "duplicate student_id")


def test_duplicate_module_is_rejected(data) -> None:
    _assert_error(_with(data, modules=data.modules + (data.modules[0],)), "duplicate module_code")


def test_badly_formatted_student_id_is_rejected(data) -> None:
    _assert_error(_with(data, students=data.students + (Student("STU-0001", 1, "IT"),)), "synthetic format")


def test_student_outside_scope_is_rejected(data) -> None:
    _assert_error(_with(data, students=data.students + (Student("IT33000001", 1, "CSNE"),)), "not in scope")


def test_registration_for_unknown_student_is_rejected(data) -> None:
    bad = Registration("IT39999999", data.modules[0].module_code, "normal")
    _assert_error(_with(data, registrations=data.registrations + (bad,)), "unknown student")


def test_registration_for_unknown_module_is_rejected(data) -> None:
    bad = Registration(data.students[0].student_id, "NOPE000", "normal")
    _assert_error(_with(data, registrations=data.registrations + (bad,)), "unknown module")


def test_registration_outside_student_specialization_is_rejected(data) -> None:
    links = data.specializations_by_module()
    student = next(s for s in data.students if s.academic_year == 3 and s.specialization == "SE")
    module = next(m for m in data.modules if m.academic_year == 3 and "SE" not in links[m.module_code])
    bad = Registration(student.student_id, module.module_code, "normal")
    _assert_error(_with(data, registrations=data.registrations + (bad,)), "not offered to the student's specialization")


def test_repeat_registration_for_same_year_is_rejected(data) -> None:
    target = next(r for r in data.registrations if r.registration_type == "normal")
    registrations = tuple(
        dataclasses.replace(r, registration_type="repeat") if r == target else r for r in data.registrations
    )
    _assert_error(_with(data, registrations=registrations), "repeat registration must be for an earlier year")


def test_module_without_specialization_is_rejected(data) -> None:
    code = data.modules[0].module_code
    links = tuple(x for x in data.module_specializations if x.module_code != code)
    _assert_error(_with(data, module_specializations=links), "has no specialization")


def test_link_to_unknown_module_is_rejected(data) -> None:
    links = data.module_specializations + (ModuleSpecialization("NOPE000", "IT"),)
    _assert_error(_with(data, module_specializations=links), "unknown module")


def test_unknown_exam_type_is_rejected(data) -> None:
    bad = dataclasses.replace(data.modules[0], exam_type="practical")
    _assert_error(_with(data, modules=(bad,) + data.modules[1:]), "unknown exam_type")


def test_reading_time_must_match_flag(data) -> None:
    m = data.modules[0]
    bad = dataclasses.replace(m, has_extra_reading_time=not m.has_extra_reading_time)
    _assert_error(_with(data, modules=(bad,) + data.modules[1:]), "reading-time flag")


def _venue(**changes) -> Venue:
    base = dict(venue_id="X100", venue_name="X100", venue_type="lecture_hall", building="X", floor=1,
                normal_capacity=40, exam_capacity=20)
    return Venue(**{**base, **changes})


def test_exam_capacity_above_normal_capacity_is_rejected(data) -> None:
    _assert_error(_with(data, venues=(_venue(exam_capacity=50),) + data.venues), "exam capacity")


def test_exam_capacity_must_be_half_of_full_capacity(data) -> None:
    _assert_error(_with(data, venues=(_venue(normal_capacity=60, exam_capacity=60),) + data.venues),
                  "must be floor(60 x 0.5) = 30")
    _assert_error(_with(data, venues=(_venue(normal_capacity=31, exam_capacity=16),) + data.venues), "= 15")
    assert validate_dataset(_with(data, venues=(_venue(normal_capacity=31, exam_capacity=15),) + data.venues)).ok


def test_unknown_venue_type_is_rejected(data) -> None:
    _assert_error(_with(data, venues=(_venue(venue_type="exam_hall"),) + data.venues), "unknown venue_type")


def test_venue_without_building_is_rejected(data) -> None:
    _assert_error(_with(data, venues=(_venue(building=""),) + data.venues), "building is missing")


def test_unlabelled_simulated_block_is_rejected(data) -> None:
    bad = dataclasses.replace(data.venue_blocks[0], reason="Teaching")
    _assert_error(_with(data, venue_blocks=(bad,) + data.venue_blocks[1:]), "labelled 'SIMULATED'")


def test_computer_based_exam_without_labs_is_rejected(data) -> None:
    venues = tuple(v for v in data.venues if v.venue_type != "computer_lab")
    blocks = tuple(b for b in data.venue_blocks if b.venue_id in {v.venue_id for v in venues})
    _assert_error(_with(data, venues=venues, venue_blocks=blocks), "no venue of a compatible type")


def test_block_with_end_before_start_is_rejected(data) -> None:
    bad = dataclasses.replace(data.venue_blocks[0], start_time=dt.time(15, 0), end_time=dt.time(14, 0))
    _assert_error(_with(data, venue_blocks=(bad,) + data.venue_blocks[1:]), "end time must be after start time")


def test_overlapping_blocks_are_rejected(data) -> None:
    b = data.venue_blocks[0]
    start = (dt.datetime.combine(b.block_date, b.start_time) + dt.timedelta(minutes=30)).time()
    clash = VenueBlock(b.venue_id, b.block_date, start, b.end_time, "maintenance", "test")
    _assert_error(_with(data, venue_blocks=data.venue_blocks + (clash,)), "overlapping blocks")


def test_block_outside_exam_period_is_rejected(data) -> None:
    bad = dataclasses.replace(data.venue_blocks[0], block_date=dt.date(2027, 1, 1))
    _assert_error(_with(data, venue_blocks=(bad,) + data.venue_blocks[1:]), "outside every examination period")


def test_block_for_unknown_venue_is_rejected(data) -> None:
    bad = dataclasses.replace(data.venue_blocks[0], venue_id="V-NONE")
    _assert_error(_with(data, venue_blocks=(bad,) + data.venue_blocks[1:]), "unknown venue")


def test_invalid_exam_period_is_rejected(data) -> None:
    p = data.exam_periods[0]
    bad = dataclasses.replace(p, end_date=p.start_date - dt.timedelta(days=1))
    _assert_error(_with(data, exam_periods=(bad,)), "end_date is before start_date")
