"""Seat allocation inside Member 2's halls, and the independent validator.

Students, venues and halls are SYNTHETIC fixtures; capacity percentages are the user's
project configuration, not verified SLIIT policy.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from dataclasses import replace

import pytest

from app.duty_allocation.student_seating import (
    PROJECT_CAPACITY_RULES, LayoutStatus, SeatingStatus, grid_hall, numbered_hall, plan_student_seating,
    provisional_hall, validate_seating,
)
from app.duty_allocation.student_seating.allocator import MISSING_VENUE, NO_CAPACITY_RULE, OVER_CAPACITY
from app.scheduling.types import DraftTimetable, ExamSession, RoomAllocation, SeatAllocation
from app.seating.snapshot import CapacitySnapshot, SnapshotVenue
from app.sources.types import Venue

SYN = "SYNTHETIC test fixture"
START, END = dt.datetime(2026, 11, 2, 8, 30), dt.datetime(2026, 11, 2, 10, 30)


def venue(venue_id, venue_type, full) -> Venue:
    return Venue(venue_id, venue_id, venue_type, "X", 1, full, full // 2)


LAB60, LAB40 = venue("LAB60", "computer_lab", 60), venue("LAB40", "computer_lab", 40)
HALL120, HALL60 = venue("HALL120", "lecture_hall", 120), venue("HALL60", "lecture_hall", 60)
SMART = venue("SMART", "smart_classroom", 40)
VENUES = [LAB60, LAB40, HALL120, HALL60, SMART]


def room(venue_id, count, prefix="IT2600") -> RoomAllocation:
    # Member 2 numbers seats 1..n; IT numbers are deliberately not in seat order.
    ids = [f"{prefix}{(n * 37) % 1000:04}" for n in range(1, count + 1)]
    return RoomAllocation(venue_id, tuple(SeatAllocation(sid, n) for n, sid in enumerate(ids, 1)))


def exam(session_id, *rooms, start=START, end=END, module="IT1010") -> ExamSession:
    return ExamSession(session_id, module, "TEST", start, end, tuple(rooms))


def draft(*sessions) -> DraftTimetable:
    return DraftTimetable("TEST", "fp-1", tuple(sessions))


def seat(d, halls=(), venues=VENUES, member2=None):
    result = plan_student_seating(d, venues, PROJECT_CAPACITY_RULES, halls, member2)
    if result.status is SeatingStatus.COMPLETE:  # always re-check independently
        all_halls = {h.venue_id: h for h in halls}
        for v in venues:
            all_halls.setdefault(v.venue_id, provisional_hall(v.venue_id, v.normal_capacity))
        assert validate_seating(d, all_halls, {v.venue_id: v for v in venues}, PROJECT_CAPACITY_RULES, result.plan) == ()
    return result


def codes(result) -> list[str]:
    return [v.constraint for v in result.violations]


# --- Capacity examples: maximum students per room ---------------------------------------------------

@pytest.mark.parametrize("v,limit", [(LAB60, 54), (LAB40, 36), (HALL120, 60), (HALL60, 30)])
def test_room_seats_exactly_up_to_its_exam_capacity(v, limit):
    full = seat(draft(exam("X1", room(v.venue_id, limit))))
    assert full.status is SeatingStatus.COMPLETE and len(full.plan.assignments) == limit
    assert full.rooms[0].exam_capacity == limit
    over = seat(draft(exam("X1", room(v.venue_id, limit + 1))))
    assert over.status is SeatingStatus.INCOMPLETE and codes(over) == [OVER_CAPACITY] and over.plan is None
    assert f"placed {limit + 1} students; limit is {limit}" in over.violations[0].message


def test_unknown_venue_type_is_not_guessed():
    result = seat(draft(exam("X1", room("SMART", 5))))
    assert codes(result) == [NO_CAPACITY_RULE] and "smart_classroom" in result.violations[0].message
    assert result.rooms[0].percentage is None


def test_missing_venue_record_is_reported():
    assert codes(seat(draft(exam("X1", room("NOWHERE", 3))))) == [MISSING_VENUE]


# --- Allocation by IT number --------------------------------------------------------------------

def test_students_seated_by_it_number_with_required_output_fields():
    result = seat(draft(exam("X1", room("HALL60", 4))))
    rows = result.plan.assignments
    assert [a.student_id for a in rows] == sorted(a.student_id for a in rows)
    first = rows[0]
    assert (first.module_code, first.exam_start, first.exam_end, first.venue_id) == ("IT1010", START, END, "HALL60")
    assert first.seat_number == 1 and first.seat_label == "1"


# --- Provisional vs verified seat numbers ----------------------------------------------------------

def test_auto_numbered_seats_are_marked_provisional_not_verified():
    result = seat(draft(exam("X1", room("HALL60", 4))))
    assert result.plan.provisional_halls == ("HALL60",) and result.plan.synthetic_halls == ()
    assert not any(a.seat_verified for a in result.plan.assignments) and not result.plan.all_seats_verified
    assert any("PROVISIONAL seat numbers (auto-numbered, unverified" in d for d in result.diagnostics)


def test_only_a_verified_layout_gives_verified_seats():
    # Status VERIFIED is used here only to test the flag; this fixture is not a real venue.
    verified = numbered_hall("HALL60", 60, source="test-only layout", status=LayoutStatus.VERIFIED)
    result = seat(draft(exam("X1", room("HALL60", 4), room("LAB60", 4))), halls=[verified])
    by_hall = {a.venue_id: a.seat_verified for a in result.plan.assignments}
    assert by_hall == {"HALL60": True, "LAB60": False}
    assert result.plan.provisional_halls == ("LAB60",)


def test_half_capacity_halls_spread_students_over_every_other_seat():
    result = seat(draft(exam("X1", room("HALL60", 30))))
    assert [a.seat_number for a in result.plan.assignments] == list(range(1, 61, 2))


def test_ninety_percent_labs_leave_one_seat_in_ten():
    result = seat(draft(exam("X1", room("LAB60", 54))))
    used = {a.seat_number for a in result.plan.assignments}
    assert len(used) == 54 and len(set(range(1, 61)) - used) == 6


def test_fewer_students_than_capacity_uses_the_same_spacing():
    result = seat(draft(exam("X1", room("HALL120", 10))))
    assert [a.seat_number for a in result.plan.assignments] == list(range(1, 21, 2))


def test_students_stay_in_member2_halls_and_draft_is_unchanged():
    d = draft(exam("X1", room("LAB60", 20, "IT2601"), room("HALL120", 40, "IT2602")))
    before = repr(d)
    result = seat(d)
    assert repr(d) == before
    assert all(a.venue_id == ("LAB60" if a.student_id.startswith("IT2601") else "HALL120")
               for a in result.plan.assignments)


def test_same_hall_in_two_sessions_reuses_seat_numbers_per_session():
    later = dict(start=START + dt.timedelta(hours=5), end=END + dt.timedelta(hours=5), module="IT2020")
    result = seat(draft(exam("X1", room("HALL60", 30)), exam("X2", room("HALL60", 30, "IT2700"), **later)))
    assert result.status is SeatingStatus.COMPLETE
    assert Counter(a.session_id for a in result.plan.assignments) == {"X1": 30, "X2": 30}


# --- Blocked seats and physical layouts ----------------------------------------------------------------

def test_blocked_seats_are_never_used():
    hall = numbered_hall("HALL60", 60, blocked=[1, 3, 5], source=SYN, status=LayoutStatus.SYNTHETIC)
    result = seat(draft(exam("X1", room("HALL60", 28))), halls=[hall])
    assert not {1, 3, 5} & {a.seat_number for a in result.plan.assignments}
    assert any("SYNTHETIC" in d for d in result.diagnostics)


def test_blocked_seats_lower_the_limit_below_exam_capacity():
    hall = numbered_hall("LAB40", 40, blocked=list(range(1, 7)), source=SYN, status=LayoutStatus.SYNTHETIC)  # 34 usable < 36
    result = seat(draft(exam("X1", room("LAB40", 35))), halls=[hall])
    assert codes(result) == [OVER_CAPACITY] and "usable seats 34" in result.violations[0].message


def test_grid_hall_gives_row_labels():
    hall = grid_hall("HALL60", [chr(65 + r) for r in range(6)], 10, source=SYN, status=LayoutStatus.SYNTHETIC)
    result = seat(draft(exam("X1", room("HALL60", 3))), halls=[hall])
    assert [a.seat_label for a in result.plan.assignments] == ["A1", "A3", "A5"]


def test_hall_size_mismatch_with_member2_capacity_is_reported():
    hall = numbered_hall("HALL60", 50, source=SYN, status=LayoutStatus.SYNTHETIC)
    result = seat(draft(exam("X1", room("HALL60", 10))), halls=[hall])
    assert any("hall has 50 seats; Member 2's full capacity is 60" in d for d in result.diagnostics)


# --- Mismatch with the capacity Member 2 scheduled with -------------------------------------------------

def test_mismatch_with_member2_capacity_snapshot_is_reported_not_fixed():
    snap = CapacitySnapshot(1, dt.datetime(2026, 10, 19, tzinfo=dt.UTC), 50,
                            (SnapshotVenue("LAB60", "computer_lab", 60, 50, "default", 30),), "fp")
    result = seat(draft(exam("X1", room("LAB60", 30))), member2=snap)
    assert result.status is SeatingStatus.COMPLETE and len(result.plan.assignments) == 30
    assert any("Member 2 scheduled with exam capacity 30, configured 54; 30 students kept" in d
               for d in result.diagnostics)


# --- Independent validator -------------------------------------------------------------------------

@pytest.fixture
def seated():
    d = draft(exam("X1", room("HALL60", 6)))
    hall = numbered_hall("HALL60", 60, blocked=[60], source=SYN, status=LayoutStatus.SYNTHETIC)
    result = seat(d, halls=[hall])
    return d, {"HALL60": hall}, {v.venue_id: v for v in VENUES}, result.plan


def check(seated, plan):
    d, halls, venues, _ = seated
    return sorted({v.constraint for v in validate_seating(d, halls, venues, PROJECT_CAPACITY_RULES, plan)})


def broken(plan, index, **changes):
    rows = list(plan.assignments)
    rows[index] = replace(rows[index], **changes)
    return replace(plan, assignments=tuple(rows))


def test_validator_accepts_allocator_output(seated):
    assert check(seated, seated[3]) == []


def test_validator_catches_duplicate_seat(seated):
    plan = seated[3]
    other = plan.assignments[1]
    assert check(seated, broken(plan, 0, seat_number=other.seat_number, seat_label=other.seat_label)) == ["S4"]


def test_validator_catches_unseated_moved_and_double_seated_students(seated):
    plan = seated[3]
    assert check(seated, replace(plan, assignments=plan.assignments[1:])) == ["S1"]
    assert "S1" in check(seated, broken(plan, 0, venue_id="LAB60"))
    assert "S1" in check(seated, replace(plan, assignments=plan.assignments + (replace(plan.assignments[0], seat_number=59, seat_label="59"),)))


def test_validator_catches_missing_and_blocked_seats(seated):
    plan = seated[3]
    assert check(seated, broken(plan, 0, seat_number=99, seat_label="99")) == ["S2"]
    assert check(seated, broken(plan, 0, seat_number=3, seat_label="X")) == ["S2"]  # seat 3 is free; label wrong
    assert check(seated, broken(plan, 0, seat_number=60, seat_label="60")) == ["S3"]


def test_validator_rejects_seats_falsely_marked_verified(seated):
    assert check(seated, broken(seated[3], 0, seat_verified=True)) == ["S7"]  # synthetic hall


def test_validator_catches_wrong_module_or_time(seated):
    assert check(seated, broken(seated[3], 0, module_code="IT9999")) == ["S6"]
    assert check(seated, broken(seated[3], 0, exam_start=START + dt.timedelta(hours=1))) == ["S6"]


def test_validator_catches_capacity_overflow():
    d = draft(exam("X1", room("HALL60", 31)))
    hall = numbered_hall("HALL60", 60, source=SYN, status=LayoutStatus.SYNTHETIC)
    from app.duty_allocation.student_seating import SeatAssignment, SeatingPlan
    rows = tuple(SeatAssignment(s.student_id, "IT1010", "X1", START, END, "HALL60", n, str(n), False)
                 for n, s in enumerate(d.sessions[0].rooms[0].seats, 1))
    plan = SeatingPlan("TEST", "cfg", (), ("HALL60",), rows)
    codes_ = {v.constraint for v in validate_seating(d, {"HALL60": hall}, {"HALL60": HALL60}, PROJECT_CAPACITY_RULES, plan)}
    assert codes_ == {"S5"}


def test_invalid_inputs():
    d = draft(exam("X1", room("HALL60", 2)))
    assert plan_student_seating("draft", VENUES, PROJECT_CAPACITY_RULES).status is SeatingStatus.INVALID
    assert plan_student_seating(d, VENUES, {"lecture_hall": 50}).status is SeatingStatus.INVALID
    assert plan_student_seating(d, ["venue"], PROJECT_CAPACITY_RULES).status is SeatingStatus.INVALID
    assert plan_student_seating(d, VENUES, PROJECT_CAPACITY_RULES, member2_capacities={}).status is SeatingStatus.INVALID
