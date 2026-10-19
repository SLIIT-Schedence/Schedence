"""Exam capacity percentages by venue type, reusing Member 2's floor rule.

exam capacity = floor(full capacity x percentage / 100), computed with Member 2's
app.seating.rules.effective_exam_capacity. A venue type with no configured percentage has
no capacity: rooms of that type are reported, never guessed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from app.duty_allocation.student_seating.types import _fail
from app.seating.rules import check_percentage, effective_exam_capacity


@dataclass(frozen=True)
class CapacityRules:
    percentages: tuple[tuple[str, int], ...]  # (venue_type, percentage 1-100)
    source: str

    def __post_init__(self) -> None:
        if type(self.source) is not str or not self.source.strip():
            _fail("capacity rules source must be a non-empty string.")
        rows = tuple(self.percentages.items()) if isinstance(self.percentages, Mapping) else tuple(self.percentages)
        types = [t for t, _ in rows]
        if not rows or len(set(types)) != len(types) or any(type(t) is not str or not t.strip() for t in types):
            _fail("capacity rules need unique, non-empty venue types.")
        for venue_type, percentage in rows:
            try:
                check_percentage(percentage)
            except ValueError as exc:
                _fail(f"capacity rule for {venue_type}: {exc}.")
        object.__setattr__(self, "percentages", tuple(sorted(rows)))

    def percentage(self, venue_type: str) -> int | None:
        return dict(self.percentages).get(venue_type)

    def exam_capacity(self, venue_type: str, full_capacity: int) -> int | None:
        percentage = self.percentage(venue_type)
        return None if percentage is None else effective_exam_capacity(full_capacity, percentage)

    @classmethod
    def from_mapping(cls, data: Mapping) -> CapacityRules:
        """{"source": "...", "percentages": {"computer_lab": 90, "lecture_hall": 50}}"""
        if not isinstance(data, Mapping) or set(data) != {"source", "percentages"} \
                or not isinstance(data["percentages"], Mapping):
            _fail("capacity config needs exactly source and a percentages mapping.")
        return cls(tuple(data["percentages"].items()), data["source"])


# The user's current project configuration (2026-10-19), not verified SLIIT policy.
# smart_classroom and byod_lab are deliberately absent: no percentage has been given.
PROJECT_CAPACITY_RULES = CapacityRules(
    (("computer_lab", 90), ("lecture_hall", 50)),
    "Member 3 project configuration: computer_lab 90%, lecture_hall 50% (not verified institutional policy)",
)
