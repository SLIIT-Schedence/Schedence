"""Configurable examination seating capacity (rules, history, capacity snapshots)."""

from app.seating.rules import (
    INITIAL_DEFAULT_PERCENTAGE,
    MAX_PERCENTAGE,
    MIN_PERCENTAGE,
    SeatingPolicy,
    SeatingRule,
    VenueCapacity,
    effective_exam_capacity,
)
from app.seating.snapshot import CapacitySnapshot, load_capacity_snapshot, snapshot_session

__all__ = [
    "INITIAL_DEFAULT_PERCENTAGE",
    "MAX_PERCENTAGE",
    "MIN_PERCENTAGE",
    "CapacitySnapshot",
    "SeatingPolicy",
    "SeatingRule",
    "VenueCapacity",
    "effective_exam_capacity",
    "load_capacity_snapshot",
    "snapshot_session",
]
