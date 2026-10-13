"""Read-only examination input data (modules, students, registrations, venues, periods).

The API and the future scheduler depend on :class:`ExamDataSource` only.
"""

from app.sources.base import ExamDataSource
from app.sources.database import DatabaseSource
from app.sources.memory import DatasetSource
from app.sources.types import (
    ExamPeriod,
    ExamPeriodExclusion,
    Module,
    Page,
    Registration,
    Student,
    Venue,
    VenueBlock,
)

__all__ = [
    "DatabaseSource",
    "DatasetSource",
    "ExamDataSource",
    "ExamPeriod",
    "ExamPeriodExclusion",
    "Module",
    "Page",
    "Registration",
    "Student",
    "Venue",
    "VenueBlock",
]
