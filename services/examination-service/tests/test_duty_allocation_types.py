"""Duty-allocation domain types: shape checks and compatibility with scheduler drafts."""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import FrozenInstanceError

import pytest

from app.duty_allocation import (
    DutyAllocationInput, DutyAllocationResult, DutyAllocationStatus, DutyAssignment, DutyRole,
    RoomDutyRequirement, StaffAvailability, StaffMember, StaffUnavailability,
)
from app.duty_allocation.types import DUTY_INPUT
from app.scheduling.contracts import SchedulingValidationError
from app.scheduling.types import DraftTimetable, ExamSession, RoomAllocation, SeatAllocation

START = dt.datetime(2026, 11, 2, 8, 30)
END = dt.datetime(2026, 11, 2, 10, 30)


def sid(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"staff/{name}"))


A, B, Z = sid("A"), sid("B"), sid("Z")


def draft() -> DraftTimetable:
    rooms = (RoomAllocation("R1", (SeatAllocation("S1", 1),)), RoomAllocation("R2", (SeatAllocation("S2", 1),)))
    return DraftTimetable("TEST", "fp-1", (ExamSession("X1", "IT1010", "TEST", START, END, rooms),))


def staff(*ids: str) -> tuple[StaffMember, ...]:
    return tuple(StaffMember(i, f"EMP-{n}", f"Staff {n}") for n, i in enumerate(ids))


def requirements() -> tuple[RoomDutyRequirement, ...]:
    return RoomDutyRequirement("X1", "R1", 1, 1), RoomDutyRequirement("X1", "R2", 2, 2)


def violations(excinfo) -> list[str]:
    return [v.constraint for v in excinfo.value.violations]


def test_valid_input_accepts_scheduler_draft_and_freezes_sequences():
    data = DutyAllocationInput(draft(), list(staff(A, B)), list(requirements()),
                               [StaffUnavailability(A, START, END, "leave")], [StaffAvailability(B, START, END)])
    assert data.room_keys() == {("X1", "R1"), ("X1", "R2")}
    assert all(isinstance(getattr(data, f), tuple) for f in ("staff", "requirements", "unavailability", "availability"))
    with pytest.raises(FrozenInstanceError):
        data.staff = ()


def test_staff_defaults_to_both_roles_and_rejects_bad_roles():
    assert StaffMember(A, "EMP1", "Staff A").eligible_roles == (DutyRole.SUPERVISOR, DutyRole.INVIGILATOR)
    member = StaffMember(A, "EMP1", "Staff A", "Senior Lecturer", [DutyRole.INVIGILATOR])
    assert member.eligible_roles == (DutyRole.INVIGILATOR,) and member.staff_number == "EMP1"
    for roles in ((), (DutyRole.SUPERVISOR, DutyRole.SUPERVISOR)):
        with pytest.raises(SchedulingValidationError):
            StaffMember(A, "EMP1", "Staff A", eligible_roles=roles)
    with pytest.raises(SchedulingValidationError):
        StaffMember(A, "EMP1", "Staff A", eligible_roles=("SUPERVISOR",))


@pytest.mark.parametrize("kwargs", [
    {"staff_id": ""}, {"staff_id": "EMP1"}, {"staff_id": A.upper()}, {"staff_number": ""},
    {"full_name": "  "}, {"academic_rank": ""},
])
def test_staff_rejects_bad_ids_and_blank_text(kwargs):
    values = {"staff_id": A, "staff_number": "EMP1", "full_name": "Staff A"} | kwargs
    with pytest.raises(SchedulingValidationError) as excinfo:
        StaffMember(**values)
    assert violations(excinfo) == [DUTY_INPUT]


@pytest.mark.parametrize("record", [StaffUnavailability, StaffAvailability])
def test_time_windows_require_ordered_naive_datetimes(record):
    for start, end in ((END, START), (START, START), (START.date(), END),
                       (START.replace(tzinfo=dt.timezone.utc), END.replace(tzinfo=dt.timezone.utc))):
        with pytest.raises(SchedulingValidationError):
            record(A, start, end)


def test_window_overlap_and_containment():
    off = StaffUnavailability(A, START, END)
    assert off.overlaps(END - dt.timedelta(minutes=1), END + dt.timedelta(hours=1))
    assert not off.overlaps(END, END + dt.timedelta(hours=1))  # touching is not overlapping
    window = StaffAvailability(A, START, END)
    assert window.contains(START, END) and not window.contains(START, END + dt.timedelta(minutes=1))


@pytest.mark.parametrize("supervisors,invigilators", [(-1, 0), (0, 1.0), (True, 1)])
def test_requirement_counts_must_be_non_negative_ints(supervisors, invigilators):
    with pytest.raises(SchedulingValidationError):
        RoomDutyRequirement("X1", "R1", supervisors, invigilators)


def test_requirement_reports_count_per_role():
    req = RoomDutyRequirement("X1", "R1", 2, 3)
    assert req.room_key == ("X1", "R1")
    assert req.required(DutyRole.SUPERVISOR) == 2 and req.required(DutyRole.INVIGILATOR) == 3


def test_input_rejects_duplicate_staff_and_unknown_time_windows():
    with pytest.raises(SchedulingValidationError, match="unique"):
        DutyAllocationInput(draft(), staff(A, A), requirements())
    with pytest.raises(SchedulingValidationError, match=f"unavailability references unknown staff {Z}"):
        DutyAllocationInput(draft(), staff(A), requirements(), (StaffUnavailability(Z, START, END),))
    with pytest.raises(SchedulingValidationError, match=f"availability references unknown staff {Z}"):
        DutyAllocationInput(draft(), staff(A), requirements(), availability=(StaffAvailability(Z, START, END),))


def test_input_requires_exactly_one_requirement_per_timetable_room():
    one, two = requirements()
    with pytest.raises(SchedulingValidationError, match="no duty requirement"):
        DutyAllocationInput(draft(), staff(A), (one,))
    with pytest.raises(SchedulingValidationError, match="one row per"):
        DutyAllocationInput(draft(), staff(A), (one, one, two))
    with pytest.raises(SchedulingValidationError, match="not a room in the timetable"):
        DutyAllocationInput(draft(), staff(A), (one, two, RoomDutyRequirement("X1", "R9", 1, 1)))


def test_input_rejects_wrong_record_types():
    with pytest.raises(SchedulingValidationError):
        DutyAllocationInput("draft", staff(A), requirements())
    with pytest.raises(SchedulingValidationError):
        DutyAllocationInput(draft(), ({"staff_id": A},), requirements())
    with pytest.raises(SchedulingValidationError):
        DutyAllocationInput(draft(), set(staff(A)), requirements())


def test_assignment_and_result_shapes():
    assignment = DutyAssignment("X1", "R1", A, DutyRole.SUPERVISOR)
    result = DutyAllocationResult(DutyAllocationStatus.FEASIBLE, "fp-1", [assignment], diagnostics=["ok"])
    assert result.assignments == (assignment,) and result.diagnostics == ("ok",)
    with pytest.raises(SchedulingValidationError):
        DutyAssignment("X1", "R1", A, "SUPERVISOR")
    with pytest.raises(SchedulingValidationError):
        DutyAssignment("X1", "R1", "EMP1", DutyRole.SUPERVISOR)
    with pytest.raises(SchedulingValidationError):
        DutyAllocationResult("FEASIBLE", "fp-1")
    with pytest.raises(SchedulingValidationError):
        DutyAllocationResult(DutyAllocationStatus.INFEASIBLE, "")
