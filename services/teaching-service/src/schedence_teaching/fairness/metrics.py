"""Exact target and distribution arithmetic, without solver or workload-policy rules."""

from dataclasses import dataclass
from fractions import Fraction


@dataclass(frozen=True)
class TeachingTargetMetrics:
    target_minutes: int | None
    utilization: Fraction | None
    shortfall_minutes: int | None
    excess_minutes: int | None
    status: str

    @property
    def absolute_normalized_target_deviation(self) -> Fraction | None:
        return abs(self.utilization - 1) if self.utilization is not None else None


def teaching_target_metrics(actual_minutes: int, target_minutes: int | None) -> TeachingTargetMetrics:
    """Zero is explicit, never a divisor; missing is never replaced by zero.

    Existing WorkloadRule inputs still require positive targets. Accepting zero
    here makes this reusable calculation safe for future authoritative adapters.
    """
    if type(actual_minutes) is not int or actual_minutes < 0:
        raise ValueError("actual teaching minutes must be a nonnegative integer")
    if target_minutes is not None and (type(target_minutes) is not int or target_minutes < 0):
        raise ValueError("teaching target must be a nonnegative integer or None")
    if target_minutes is None:
        return TeachingTargetMetrics(None, None, None, None, "MISSING_TARGET")
    return TeachingTargetMetrics(target_minutes, Fraction(actual_minutes, target_minutes) if target_minutes else None,
        max(target_minutes - actual_minutes, 0), max(actual_minutes - target_minutes, 0),
        "ZERO_TARGET" if target_minutes == 0 else
        "BELOW_TARGET" if actual_minutes < target_minutes else "ABOVE_TARGET" if actual_minutes > target_minutes else "MET")


def distribution(values, weights):
    """Weighted population moments. Empty groups have no invented mean or score."""
    if not values:
        return None, None, None, None, None
    total_weight = sum(weights.values(), Fraction())
    mean = sum((value * weights[key] for key, value in values.items()), Fraction()) / total_weight
    gaps = {key: abs(value - mean) for key, value in values.items()}
    variance = sum((weights[key] * gap**2 for key, gap in gaps.items()), Fraction()) / total_weight
    mad = sum((weights[key] * gap for key, gap in gaps.items()), Fraction()) / total_weight
    maximum = max(gaps.values())
    return mean, variance, mad, maximum, maximum / mean if mean else None
