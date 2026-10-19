"""Configurable institutional staffing rules: students in a room -> Supervisors and Invigilators.

Rules are explicit student-count bands supplied by the institution. A room whose count is
not covered by any band is reported, never guessed. The only confirmed SLIIT examples are
60 students -> 1 + 1 and 150 students -> 2 + 2, so the prototype policy covers exactly those
counts and nothing else until the institution supplies real bands.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from app.duty_allocation.types import DUTY_INPUT
from app.scheduling.contracts import SchedulingValidationError, Violation, frozen_sequence


def _is_int(value: object) -> bool:
    return type(value) is int


@dataclass(frozen=True)
class StaffingRule:
    min_students: int
    max_students: int | None  # inclusive; None means no upper limit
    supervisors: int
    invigilators: int

    def __post_init__(self) -> None:
        errors = []
        if not _is_int(self.min_students) or self.min_students < 0:
            errors.append("min_students must be a non-negative int.")
        if self.max_students is not None and (not _is_int(self.max_students) or
                                              (_is_int(self.min_students) and self.max_students < self.min_students)):
            errors.append("max_students must be None or an int >= min_students.")
        for name in ("supervisors", "invigilators"):
            if not _is_int(getattr(self, name)) or getattr(self, name) < 0:
                errors.append(f"{name} must be a non-negative int.")
        if errors:
            raise SchedulingValidationError(tuple(Violation(DUTY_INPUT, f"staffing rule: {e}") for e in errors))

    def covers(self, students: int) -> bool:
        return self.min_students <= students and (self.max_students is None or students <= self.max_students)

    def describe(self) -> str:
        upper = "+" if self.max_students is None else f"-{self.max_students}"
        return f"{self.min_students}{upper} students"


@dataclass(frozen=True)
class StaffingPolicy:
    rules: tuple[StaffingRule, ...]
    source: str  # where the rules came from, e.g. "SLIIT confirmed examples"

    def __post_init__(self) -> None:
        rules = frozen_sequence(self.rules, "staffing rules", StaffingRule)
        if type(self.source) is not str or not self.source.strip():
            raise SchedulingValidationError((Violation(DUTY_INPUT, "staffing policy source must be a non-empty string."),))
        if not rules:
            raise SchedulingValidationError((Violation(DUTY_INPUT, "staffing policy needs at least one rule."),))
        ordered = tuple(sorted(rules, key=lambda r: r.min_students))
        for before, after in zip(ordered, ordered[1:]):
            if before.max_students is None or before.max_students >= after.min_students:
                raise SchedulingValidationError((Violation(
                    DUTY_INPUT, f"staffing rules overlap: {before.describe()} and {after.describe()}."),))
        object.__setattr__(self, "rules", ordered)

    def rule_for(self, students: int) -> StaffingRule | None:
        return next((r for r in self.rules if r.covers(students)), None)

    @classmethod
    def from_mapping(cls, data: Mapping) -> StaffingPolicy:
        """Build from configuration, e.g. parsed JSON:
        {"source": "...", "rules": [{"min_students": 1, "max_students": 60, "supervisors": 1, "invigilators": 1}]}"""
        if not isinstance(data, Mapping) or not isinstance(data.get("rules"), list):
            raise SchedulingValidationError((Violation(DUTY_INPUT, "staffing config needs a 'rules' list."),))
        keys = ("min_students", "max_students", "supervisors", "invigilators")
        rules = []
        for index, row in enumerate(data["rules"]):
            if not isinstance(row, Mapping) or set(row) - set(keys) or any(k not in row for k in keys if k != "max_students"):
                raise SchedulingValidationError((Violation(
                    DUTY_INPUT, f"staffing config rules[{index}] must have exactly {', '.join(keys)} (max_students optional)."),))
            rules.append(StaffingRule(row["min_students"], row.get("max_students"), row["supervisors"], row["invigilators"]))
        return cls(tuple(rules), data.get("source"))


# Only the two staffing examples confirmed for SLIIT. Any other count is reported as uncovered.
CONFIRMED_EXAMPLES_POLICY = StaffingPolicy(
    rules=(StaffingRule(60, 60, 1, 1), StaffingRule(150, 150, 2, 2)),
    source="SLIIT confirmed examples (60 -> 1+1, 150 -> 2+2); prototype only",
)
