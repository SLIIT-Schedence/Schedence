"""Staffing rules and per-room duty requirement calculation from scheduler drafts."""

from __future__ import annotations

import datetime as dt
import json

import pytest

from app.duty_allocation import (
    CONFIRMED_EXAMPLES_POLICY, DutyAllocationInput, RoomDutyRequirement, StaffMember, StaffingPolicy,
    StaffingRule, calculate_requirements,
)
from app.duty_allocation.requirements import UNCOVERED_ROOM
from app.scheduling.contracts import SchedulingValidationError
from app.scheduling.types import DraftTimetable, ExamSession, RoomAllocation, SeatAllocation

START = dt.datetime(2026, 11, 2, 8, 30)
END = dt.datetime(2026, 11, 2, 10, 30)


def room(venue_id: str, students: int) -> RoomAllocation:
    return RoomAllocation(venue_id, tuple(SeatAllocation(f"{venue_id}-S{n}", n) for n in range(1, students + 1)))


def draft(*sessions: tuple[str, tuple[RoomAllocation, ...]]) -> DraftTimetable:
    return DraftTimetable("TEST", "fp-1", tuple(ExamSession(sid, "IT1010", "TEST", START, END, rooms)
                                                for sid, rooms in sessions))


# --- Confirmed SLIIT examples -------------------------------------------------------------

@pytest.mark.parametrize("students,supervisors,invigilators", [(60, 1, 1), (150, 2, 2)])
def test_confirmed_examples(students, supervisors, invigilators):
    calc = calculate_requirements(draft(("X1", (room("R1", students),))), CONFIRMED_EXAMPLES_POLICY)
    assert calc.is_complete
    assert calc.requirements == (RoomDutyRequirement("X1", "R1", supervisors, invigilators),)


@pytest.mark.parametrize("students", [0, 1, 59, 61, 100, 149, 151, 300])
def test_unconfirmed_counts_are_reported_not_guessed(students):
    calc = calculate_requirements(draft(("X1", (room("R1", students),))), CONFIRMED_EXAMPLES_POLICY)
    assert calc.requirements == ()
    assert [v.constraint for v in calc.violations] == [UNCOVERED_ROOM]
    assert f"{students} students" in calc.violations[0].message


def test_each_room_is_calculated_separately_and_reports_all_gaps():
    timetable = draft(("X2", (room("R3", 150), room("R1", 60))), ("X1", (room("R2", 61),)))
    calc = calculate_requirements(timetable, CONFIRMED_EXAMPLES_POLICY)
    assert calc.requirements == (RoomDutyRequirement("X2", "R1", 1, 1), RoomDutyRequirement("X2", "R3", 2, 2))
    assert len(calc.violations) == 1 and "X1 room R2" in calc.violations[0].message
    assert (calc.total_supervisors, calc.total_invigilators) == (3, 3)
    assert not calc.is_complete


def test_complete_calculation_feeds_duty_allocation_input():
    timetable = draft(("X1", (room("R1", 60), room("R2", 150))))
    calc = calculate_requirements(timetable, CONFIRMED_EXAMPLES_POLICY)
    data = DutyAllocationInput(timetable, (StaffMember("00000000-0000-4000-8000-000000000001", "EMP1", "Staff A"),), calc.requirements)
    assert data.room_keys() == {r.room_key for r in calc.requirements}


def test_draft_is_not_modified():
    timetable = draft(("X1", (room("R1", 60),)))
    before = repr(timetable)
    calculate_requirements(timetable, CONFIRMED_EXAMPLES_POLICY)
    assert repr(timetable) == before


# --- Configurable institutional rules ----------------------------------------------------

def test_policy_from_config_uses_institution_bands():
    config = json.loads(json.dumps({"source": "test institution", "rules": [
        {"min_students": 1, "max_students": 60, "supervisors": 1, "invigilators": 1},
        {"min_students": 61, "supervisors": 2, "invigilators": 3},
    ]}))
    policy = StaffingPolicy.from_mapping(config)
    calc = calculate_requirements(draft(("X1", (room("R1", 30), room("R2", 500)))), policy)
    assert calc.requirements == (RoomDutyRequirement("X1", "R1", 1, 1), RoomDutyRequirement("X1", "R2", 2, 3))
    assert calc.policy_source == "test institution"


def test_rules_are_sorted_and_lookup_respects_inclusive_bounds():
    policy = StaffingPolicy((StaffingRule(61, 100, 2, 2), StaffingRule(1, 60, 1, 1)), "test")
    assert [r.min_students for r in policy.rules] == [1, 61]
    assert policy.rule_for(60).supervisors == 1 and policy.rule_for(61).supervisors == 2
    assert policy.rule_for(101) is None and policy.rule_for(0) is None


@pytest.mark.parametrize("rules", [
    (StaffingRule(1, 60, 1, 1), StaffingRule(60, 100, 2, 2)),
    (StaffingRule(1, None, 1, 1), StaffingRule(200, 300, 2, 2)),
])
def test_overlapping_rules_are_rejected(rules):
    with pytest.raises(SchedulingValidationError, match="overlap"):
        StaffingPolicy(rules, "test")


@pytest.mark.parametrize("args", [(-1, 10, 1, 1), (10, 5, 1, 1), (1, 10, -1, 1), (1, 10, 1, 1.5), (1, 10, True, 1)])
def test_invalid_rules_are_rejected(args):
    with pytest.raises(SchedulingValidationError):
        StaffingRule(*args)


@pytest.mark.parametrize("config", [
    {}, {"rules": "x"}, {"rules": []}, {"rules": [{"min_students": 1}]},
    {"source": "t", "rules": [{"min_students": 1, "supervisors": 1, "invigilators": 1, "extra": 1}]},
    {"rules": [{"min_students": 1, "supervisors": 1, "invigilators": 1}]},
])
def test_invalid_config_is_rejected(config):
    with pytest.raises(SchedulingValidationError):
        StaffingPolicy.from_mapping(config)


def test_calculate_requirements_rejects_wrong_arguments():
    with pytest.raises(SchedulingValidationError):
        calculate_requirements("draft", CONFIRMED_EXAMPLES_POLICY)
    with pytest.raises(SchedulingValidationError):
        calculate_requirements(draft(), {"rules": []})
