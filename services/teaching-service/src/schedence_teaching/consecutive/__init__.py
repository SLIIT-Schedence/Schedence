"""Step 11A: independent consecutive teaching foundation, without solver changes."""

from .assessment import ConsecutiveInputError, assess_consecutive_sessions, validate_consecutive_report
from .contracts import AttendanceKind, ConfirmedMinimumBreak, ConsecutiveCriterion, ConsecutivePolicy
from .metrics import detect_consecutive_sequences
from .results import ConsecutiveReport, DayStreamSequence, EntityConsecutiveAssessment, SessionGap, TeachingRun

__all__ = [
    "AttendanceKind", "ConfirmedMinimumBreak", "ConsecutiveCriterion", "ConsecutiveInputError", "ConsecutivePolicy",
    "ConsecutiveReport", "DayStreamSequence", "EntityConsecutiveAssessment", "SessionGap", "TeachingRun",
    "assess_consecutive_sessions", "detect_consecutive_sequences", "validate_consecutive_report",
]
