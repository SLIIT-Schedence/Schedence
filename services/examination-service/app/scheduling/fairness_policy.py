"""Optional, approved prototype workload protection; not institutional policy."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, fields

from app.scheduling.contracts import SchedulingValidationError, Violation

FAIRNESS_VERSION = "baseline-relative-v1"


@dataclass(frozen=True)
class FairnessPolicy:
    enabled: bool = False
    max_individual_increase_points: float = 5.0
    protect_worst_student: bool = True
    max_worst_increase_points: float = 0.0
    automatic_relaxation: bool = False

    def __post_init__(self) -> None:
        errors = []
        for name in ("enabled", "protect_worst_student", "automatic_relaxation"):
            if type(getattr(self, name)) is not bool:
                errors.append(Violation("FAIRNESS", f"{name} must be a boolean."))
        for name in ("max_individual_increase_points", "max_worst_increase_points"):
            value = getattr(self, name)
            # Range comparisons also reject NaN/infinity, before float conversion.
            if type(value) not in (int, float) or not 0 <= value <= 100:
                errors.append(Violation("FAIRNESS", f"{name} must be a finite number between 0 and 100."))
            else:
                object.__setattr__(self, name, float(value))
        if self.automatic_relaxation is True:
            errors.append(Violation("FAIRNESS", "Automatic fairness relaxation is not supported; keep automatic_relaxation false."))
        if errors:
            raise SchedulingValidationError(tuple(errors))

    @property
    def fingerprint(self) -> str:
        content = json.dumps({"version": FAIRNESS_VERSION, **asdict(self)}, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(content.encode()).hexdigest()

    @classmethod
    def from_mapping(cls, values: dict) -> FairnessPolicy:
        if type(values) is not dict or any(type(key) is not str for key in values):
            raise SchedulingValidationError((Violation("FAIRNESS", "Fairness settings must be a JSON object."),))
        unknown = set(values) - {f.name for f in fields(cls)}
        if unknown:
            raise SchedulingValidationError((Violation("FAIRNESS", "Unknown fairness settings: " + ", ".join(sorted(unknown))),))
        return cls(**values)
