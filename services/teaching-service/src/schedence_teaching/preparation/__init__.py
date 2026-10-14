"""Public pure-Python group hierarchy and session-demand preparation functions."""

from .errors import PreparationError, PreparationIssue
from .hierarchy import GroupHierarchy, build_group_hierarchy, groups_conflict
from .sessions import (
    PreparationSummary, PreparedTimetableInput, UnscheduledSession,
    expand_session_requirements, prepare_timetable_input,
)
from .combination_results import (
    CombinationDiagnostic, CombinedLectureOccurrence, DeliveryCoverageSummary,
    GroupCombinationCandidate, PreparedGroupCombinations,
)
from .combinations import prepare_group_combinations
from .combination_coverage import validate_delivery_selection

__all__ = [
    "GroupHierarchy", "PreparationError", "PreparationIssue", "PreparationSummary",
    "PreparedTimetableInput", "UnscheduledSession", "build_group_hierarchy",
    "expand_session_requirements", "groups_conflict", "prepare_timetable_input",
    "CombinationDiagnostic", "CombinedLectureOccurrence", "DeliveryCoverageSummary",
    "GroupCombinationCandidate", "PreparedGroupCombinations", "prepare_group_combinations",
    "validate_delivery_selection",
]
