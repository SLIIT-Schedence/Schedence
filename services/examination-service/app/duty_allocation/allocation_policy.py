"""Configurable duty-load limits for the allocator. Not SLIIT policy: values are supplied.

A duty is one staff member working one room in one session in one role. A day is the
session's start date in the period's local calendar. None means "no limit configured".
The minimum break between duties is EligibilityPolicy.minimum_gap_minutes, not repeated here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from app.duty_allocation.types import DUTY_INPUT
from app.scheduling.contracts import SchedulingValidationError, Violation


def _fail(message: str) -> None:
    raise SchedulingValidationError((Violation(DUTY_INPUT, message),))


@dataclass(frozen=True)
class DutyLimitPolicy:
    max_duties_per_day: int | None
    max_duties_per_period: int | None
    source: str

    def __post_init__(self) -> None:
        for name in ("max_duties_per_day", "max_duties_per_period"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 1):
                _fail(f"{name} must be None or a positive int.")
        if type(self.source) is not str or not self.source.strip():
            _fail("duty limit policy source must be a non-empty string.")

    @classmethod
    def from_mapping(cls, data: Mapping) -> DutyLimitPolicy:
        """{"source": "...", "max_duties_per_day": 2, "max_duties_per_period": null}"""
        keys = {"source", "max_duties_per_day", "max_duties_per_period"}
        if not isinstance(data, Mapping) or set(data) != keys:
            _fail("duty limit config needs exactly source, max_duties_per_day, max_duties_per_period.")
        return cls(data["max_duties_per_day"], data["max_duties_per_period"], data["source"])
