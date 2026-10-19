"""Venue-type exam capacity rules and hall layouts.

computer_lab 90% / lecture_hall 50% is the user's project configuration, not verified
SLIIT policy. Halls here are SYNTHETIC fixtures.
"""

from __future__ import annotations

import pytest

from app.duty_allocation.student_seating import (
    PROJECT_CAPACITY_RULES, CapacityRules, Hall, LayoutStatus, Seat, grid_hall, hall_from_mapping, numbered_hall,
    provisional_hall,
)
from app.scheduling.contracts import SchedulingValidationError

SYN = "SYNTHETIC test fixture"


@pytest.mark.parametrize("venue_type,full,expected", [
    ("computer_lab", 60, 54), ("computer_lab", 40, 36),
    ("lecture_hall", 120, 60), ("lecture_hall", 60, 30),
    ("computer_lab", 45, 40),  # floor(40.5)
    ("lecture_hall", 99, 49),  # floor(49.5)
])
def test_project_capacity_examples(venue_type, full, expected):
    assert PROJECT_CAPACITY_RULES.exam_capacity(venue_type, full) == expected


@pytest.mark.parametrize("venue_type", ["smart_classroom", "byod_lab", "unknown"])
def test_unconfigured_venue_types_get_no_capacity(venue_type):
    assert PROJECT_CAPACITY_RULES.percentage(venue_type) is None
    assert PROJECT_CAPACITY_RULES.exam_capacity(venue_type, 100) is None


def test_capacity_rules_from_config():
    rules = CapacityRules.from_mapping({"source": "cfg", "percentages": {"computer_lab": 90, "smart_classroom": 75}})
    assert rules.exam_capacity("smart_classroom", 40) == 30 and rules.percentage("lecture_hall") is None


@pytest.mark.parametrize("config", [
    {"source": "cfg", "percentages": {}},
    {"source": "cfg", "percentages": {"computer_lab": 0}},
    {"source": "cfg", "percentages": {"computer_lab": 101}},
    {"source": "cfg", "percentages": {"computer_lab": 90.0}},
    {"source": "", "percentages": {"computer_lab": 90}},
    {"percentages": {"computer_lab": 90}},
])
def test_invalid_capacity_config(config):
    with pytest.raises(SchedulingValidationError):
        CapacityRules.from_mapping(config)


def test_numbered_hall():
    hall = numbered_hall("V1", 5, blocked=[2], source=SYN, status=LayoutStatus.SYNTHETIC)
    assert [s.label for s in hall.seats] == ["1", "2", "3", "4", "5"]
    assert [s.number for s in hall.usable_seats] == [1, 3, 4, 5]


def test_grid_hall_labels_and_blocked_seats():
    hall = grid_hall("V1", ["A", "B"], 3, blocked=["A2"], source=SYN, status=LayoutStatus.SYNTHETIC)
    assert [s.label for s in hall.seats] == ["A1", "A2", "A3", "B1", "B2", "B3"]
    assert hall.seats[4] == Seat(5, "B2", 2, 2) and hall.blocked == (2,)


def test_provisional_hall_is_marked_unverified():
    hall = provisional_hall("V1", 30)
    assert hall.status is LayoutStatus.PROVISIONAL and len(hall.seats) == 30
    assert "PROVISIONAL" in hall.source and "not verified" in hall.source


def test_hall_from_mapping():
    assert len(hall_from_mapping({"venue_id": "V1", "seat_count": 4, "source": SYN, "status": "SYNTHETIC"}).seats) == 4
    grid = hall_from_mapping({"venue_id": "V1", "row_labels": ["A"], "columns": 2, "blocked": ["A1"],
                              "source": SYN, "status": "SYNTHETIC"})
    assert [s.number for s in grid.usable_seats] == [2]


@pytest.mark.parametrize("build", [
    lambda: numbered_hall("V1", 0, source=SYN, status=LayoutStatus.SYNTHETIC),
    lambda: numbered_hall("V1", 3, blocked=[4], source=SYN, status=LayoutStatus.SYNTHETIC),
    lambda: numbered_hall("V1", 3, blocked=[1, 1], source=SYN, status=LayoutStatus.SYNTHETIC),
    lambda: numbered_hall("V1", 3, source="", status=LayoutStatus.SYNTHETIC),
    lambda: grid_hall("V1", ["A"], 2, blocked=["Z9"], source=SYN, status=LayoutStatus.SYNTHETIC),
    lambda: grid_hall("V1", ["A", "A"], 2, source=SYN, status=LayoutStatus.SYNTHETIC),  # duplicate labels
    lambda: Hall("V1", (Seat(2, "x"),), (), SYN, LayoutStatus.SYNTHETIC),  # not numbered from 1
    lambda: Hall("V1", (Seat(1, "x"),), (), SYN, True),  # status must be a LayoutStatus
    lambda: hall_from_mapping({"venue_id": "V1", "seat_count": 3, "columns": 2, "source": SYN, "status": "SYNTHETIC"}),
    lambda: hall_from_mapping({"venue_id": "V1", "seat_count": 3, "source": SYN, "status": "CHECKED"}),
    lambda: hall_from_mapping({"venue_id": "V1", "seat_count": 3, "source": SYN, "synthetic": True}),
])
def test_invalid_halls(build):
    with pytest.raises(SchedulingValidationError):
        build()
