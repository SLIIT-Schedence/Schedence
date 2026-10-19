"""Student seat allocation (Member 3). Seats students inside Member 2's halls; never moves them."""

from app.duty_allocation.student_seating.types import (
    Hall,
    LayoutStatus,
    RoomCapacity,
    Seat,
    SeatAssignment,
    SeatingPlan,
    SeatingResult,
    SeatingStatus,
)
from app.duty_allocation.student_seating.layout import grid_hall, hall_from_mapping, numbered_hall, provisional_hall
from app.duty_allocation.student_seating.policy import PROJECT_CAPACITY_RULES, CapacityRules
from app.duty_allocation.student_seating.validation import validate_seating
from app.duty_allocation.student_seating.service import plan_student_seating
from app.duty_allocation.student_seating.integration import CapacityOverridePlan, apply_capacity_rules

__all__ = [
    "Hall", "LayoutStatus", "RoomCapacity", "Seat", "SeatAssignment", "SeatingPlan", "SeatingResult",
    "SeatingStatus", "grid_hall", "hall_from_mapping", "numbered_hall", "provisional_hall",
    "PROJECT_CAPACITY_RULES", "CapacityRules", "validate_seating", "plan_student_seating",
    "CapacityOverridePlan", "apply_capacity_rules",
]
