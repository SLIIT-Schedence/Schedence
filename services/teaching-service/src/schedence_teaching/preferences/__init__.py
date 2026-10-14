"""Soft preference contracts, preparation and validated post-solve evaluation."""

from .contracts import (
    LecturerPreference, LecturerPreferenceInput, PreferenceCalendar, PreferenceCategory,
    PreferenceConfirmation, PreferenceDirection, PreferenceExpression, PreferenceProvenance,
    PreferenceScope, PreferenceScoringPolicy, PreferenceWeightMode, TimeOfDay,
)
from .preparation import (
    PreferenceDiagnostic, PreferenceInputError, PreparedLecturerPreferences, prepare_lecturer_preferences,
)
from .results import PreferenceEvaluation, PreferenceSatisfaction, PreferenceScoreReport, StaffPreferenceSummary
from .scoring import score_lecturer_preferences

__all__ = [
    "LecturerPreference", "LecturerPreferenceInput", "PreferenceCalendar", "PreferenceCategory",
    "PreferenceConfirmation", "PreferenceDirection", "PreferenceExpression", "PreferenceProvenance",
    "PreferenceScope", "PreferenceScoringPolicy", "PreferenceWeightMode", "TimeOfDay",
    "PreferenceDiagnostic", "PreferenceInputError", "PreparedLecturerPreferences", "prepare_lecturer_preferences",
    "PreferenceEvaluation", "PreferenceSatisfaction", "PreferenceScoreReport", "StaffPreferenceSummary",
    "score_lecturer_preferences",
]
