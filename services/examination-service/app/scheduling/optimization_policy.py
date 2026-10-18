"""Approved prototype workload settings; no institutional or clinical claims."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, fields
from fractions import Fraction

from app.scheduling.contracts import SchedulingValidationError, Violation

SCORING_VERSION = "prototype-workload-v1"


@dataclass(frozen=True)
class OptimizationPolicy:
    s1_weight: int = 25
    s2_weight: int = 20
    s3_weight: int = 30
    s4_weight: int = 15
    s5_weight: int = 10
    short_gap_minutes: int = 12 * 60
    preparation_target_minutes: int = 48 * 60
    difficult_target_minutes: int = 72 * 60
    high_difficulty_minimum: int = 4
    window_days: int = 3
    preferred_window_exams: int = 2
    difficulty_weight: int = 50
    credits_weight: int = 25
    duration_weight: int = 25
    reference_difficulty: int = 3
    reference_credits: int = 4
    reference_duration_minutes: int = 120
    score_scale: int = 1000
    load_scale: int = 1000
    baseline_budget_percent: int = 10

    def __post_init__(self) -> None:
        errors = []
        for f in fields(self):
            value = getattr(self, f.name)
            if type(value) is not int or not 0 <= value <= 2**31 - 1:
                errors.append(Violation("OPTIMIZATION", f"{f.name} must be a non-negative signed 32-bit integer."))
        if errors:
            raise SchedulingValidationError(tuple(errors))
        if not 0 < self.short_gap_minutes < self.preparation_target_minutes < self.difficult_target_minutes:
            errors.append(Violation("OPTIMIZATION", "Preparation thresholds must satisfy 0 < short < preparation < difficult."))
        if not 1 <= self.high_difficulty_minimum <= 5 or not 1 <= self.reference_difficulty <= 5:
            errors.append(Violation("OPTIMIZATION", "Difficulty thresholds and reference must be between 1 and 5."))
        for name in ("window_days", "reference_credits", "reference_duration_minutes", "score_scale", "load_scale"):
            if getattr(self, name) < 1:
                errors.append(Violation("OPTIMIZATION", f"{name} must be positive."))
        if self.difficulty_weight + self.credits_weight + self.duration_weight == 0:
            errors.append(Violation("OPTIMIZATION", "At least one workload coefficient must be positive."))
        if not 1 <= self.baseline_budget_percent <= 99:
            errors.append(Violation("OPTIMIZATION", "baseline_budget_percent must be between 1 and 99."))
        if errors:
            raise SchedulingValidationError(tuple(errors))

    @property
    def weights(self) -> tuple[int, ...]:
        return (self.s1_weight, self.s2_weight, self.s3_weight, self.s4_weight, self.s5_weight)

    @property
    def fingerprint(self) -> str:
        content = json.dumps({"version": SCORING_VERSION, **asdict(self)}, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(content.encode()).hexdigest()

    @classmethod
    def from_mapping(cls, values: dict) -> OptimizationPolicy:
        if type(values) is not dict or any(type(key) is not str for key in values):
            raise SchedulingValidationError((Violation("OPTIMIZATION", "Optimization settings must be a JSON object."),))
        unknown = set(values) - {f.name for f in fields(cls)}
        if unknown:
            raise SchedulingValidationError((Violation("OPTIMIZATION", "Unknown optimization settings: " + ", ".join(sorted(unknown))),))
        return cls(**values)


def workload_units(module, policy: OptimizationPolicy) -> int:
    """Exact rational arithmetic, then round half up once to load_scale units."""
    parts = (Fraction(policy.difficulty_weight * module.difficulty, policy.reference_difficulty)
             + Fraction(policy.credits_weight * module.credits, policy.reference_credits)
             + Fraction(policy.duration_weight * (module.duration_minutes + module.reading_time_minutes),
                        policy.reference_duration_minutes))
    value = 100 * policy.load_scale * parts / (policy.difficulty_weight + policy.credits_weight + policy.duration_weight)
    return (2 * value.numerator + value.denominator) // (2 * value.denominator)
