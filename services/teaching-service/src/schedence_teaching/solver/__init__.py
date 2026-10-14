"""Joint CP-SAT staff/time/venue feasibility and independent timetable validation."""

from .config import SchedulingDay, SolverConfig, TimeWindow
from .engine import solve_timetable
from .deliveries import CombinationSolverConfig, DeliveryObjective
from .delivery_validation import validate_delivery_timetable, validate_delivery_result
from .results import (
    ScheduledDelivery, ScheduledSession, SolverInputError, SolverResult, SolverStatus,
    TimetableValidationError, ValidationSummary,
)
from .validator import validate_timetable
from .venue_inputs import (
    CombinedVenueRelationship, SessionVenueRequirement, SharedVenueResource,
    VenueCalendar, VenueDayInterval, VenueSchedulingInput,
)
from .venue_import import venues_from_import
from .staff_inputs import StaffAvailability, StaffBlockedInterval, StaffSchedulingInput
from .staff_validation import validate_staff_policy_result
from .work_period_inputs import WorkPeriodSolverConfig
from .work_period_results import ScheduledAdministrativePeriod, StaffWorkPeriodSummary, WorkPeriodReport
from .work_period_validation import validate_work_period_result
from .preference_inputs import PreferenceOptimizationConfig, PreferencePriority
from .preference_results import PreferenceOptimizationReport
from .preference_validation import validate_preference_result
from .fairness_inputs import FairnessOptimizationConfig, FairnessPriority, FairnessCohortRule, FairnessDeviationReference
from .fairness_results import FairnessOptimizationReport
from .fairness_validation import validate_fairness_result
from .consecutive_inputs import ConsecutiveOptimizationConfig, ConsecutivePriority
from .consecutive_results import ConsecutiveOptimizationReport
from .consecutive_validation import validate_consecutive_result
from .lunch_inputs import StudentLunchEnforcementConfig
from .lunch_results import StudentLunchEnforcementReport
from .lunch_validation import validate_student_lunch_result

__all__ = [
    "ScheduledSession", "SchedulingDay", "SolverConfig", "SolverInputError", "SolverResult",
    "SolverStatus", "TimeWindow", "TimetableValidationError", "ValidationSummary",
    "solve_timetable", "validate_timetable",
    "CombinedVenueRelationship", "SessionVenueRequirement", "SharedVenueResource",
    "VenueCalendar", "VenueDayInterval", "VenueSchedulingInput", "venues_from_import",
    "CombinationSolverConfig", "DeliveryObjective", "ScheduledDelivery",
    "validate_delivery_timetable", "validate_delivery_result",
    "StaffAvailability", "StaffBlockedInterval", "StaffSchedulingInput", "validate_staff_policy_result",
    "WorkPeriodSolverConfig", "ScheduledAdministrativePeriod", "StaffWorkPeriodSummary", "WorkPeriodReport",
    "validate_work_period_result",
    "PreferenceOptimizationConfig", "PreferencePriority", "PreferenceOptimizationReport", "validate_preference_result",
    "FairnessOptimizationConfig", "FairnessPriority", "FairnessCohortRule", "FairnessDeviationReference",
    "FairnessOptimizationReport", "validate_fairness_result",
    "ConsecutiveOptimizationConfig", "ConsecutivePriority", "ConsecutiveOptimizationReport", "validate_consecutive_result",
    "StudentLunchEnforcementConfig", "StudentLunchEnforcementReport", "validate_student_lunch_result",
]
