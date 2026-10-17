"""Offline draft scheduling. Importing this package does not import OR-Tools."""

from app.scheduling.inputs import load_snapshot, load_database_snapshot
from app.scheduling.contracts import SchedulingValidationError
from app.scheduling.policy import SchedulingPolicy, SolverSettings
from app.scheduling.service import generate_timetable
from app.scheduling.types import DraftTimetable, InputSnapshot, SolveResult, SolveStatus
from app.scheduling.validation import validate_timetable

__all__ = [
    "DraftTimetable", "InputSnapshot", "SchedulingPolicy", "SolveResult", "SolveStatus",
    "SolverSettings", "SchedulingValidationError", "generate_timetable", "load_database_snapshot", "load_snapshot", "validate_timetable",
]
