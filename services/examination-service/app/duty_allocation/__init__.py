"""Fairness-aware examination staff duty allocation (Member 3). Consumes scheduler drafts read-only."""

from app.duty_allocation.types import (
    DutyAllocationInput,
    DutyAllocationResult,
    DutyAllocationStatus,
    DutyAssignment,
    DutyRole,
    RoomDutyRequirement,
    StaffAvailability,
    StaffMember,
    StaffUnavailability,
)
from app.duty_allocation.staffing_policy import CONFIRMED_EXAMPLES_POLICY, StaffingPolicy, StaffingRule
from app.duty_allocation.requirements import RequirementCalculation, calculate_requirements
from app.duty_allocation.eligibility_policy import EligibilityPolicy, RoleRule, StaffRestriction
from app.duty_allocation.staff_source import ExcludedStaff, StaffImport, import_staff, page_items
from app.duty_allocation.eligibility import EligibilityReport, Exclusion, RoomEligibility, build_eligibility
from app.duty_allocation.allocation_policy import DutyLimitPolicy
from app.duty_allocation.validation import validate_allocation
from app.duty_allocation.solver import allocate_duties  # imports OR-Tools only when solving
from app.duty_allocation.fairness_policy import (
    AdminWorkload, DayPreference, DayPreferenceKind, DutyFairnessPolicy, DutyHistory, FairnessInputs,
    FairnessWeights, LoadUnit, MissingAdminHours, admin_workload_from_records,
)
from app.duty_allocation.fairness_metrics import FairnessMetrics, measure_fairness
from app.duty_allocation.fairness import DutyFairnessComparison, allocate_fair_duties, compare_duty_fairness

__all__ = [
    "DutyAllocationInput", "DutyAllocationResult", "DutyAllocationStatus", "DutyAssignment",
    "DutyRole", "RoomDutyRequirement", "StaffAvailability", "StaffMember", "StaffUnavailability",
    "CONFIRMED_EXAMPLES_POLICY", "StaffingPolicy", "StaffingRule",
    "RequirementCalculation", "calculate_requirements",
    "EligibilityPolicy", "RoleRule", "StaffRestriction",
    "ExcludedStaff", "StaffImport", "import_staff", "page_items",
    "EligibilityReport", "Exclusion", "RoomEligibility", "build_eligibility",
    "DutyLimitPolicy", "validate_allocation", "allocate_duties",
    "AdminWorkload", "DayPreference", "DayPreferenceKind", "DutyFairnessPolicy", "DutyHistory", "FairnessInputs",
    "FairnessWeights", "LoadUnit", "MissingAdminHours", "admin_workload_from_records",
    "FairnessMetrics", "measure_fairness",
    "DutyFairnessComparison", "allocate_fair_duties", "compare_duty_fairness",
]
