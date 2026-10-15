"""Authoritative input foundation, fail-closed readiness and existing solver bridge."""

from .adapters import (
    AcademicAdaptation, AcademicSourceRow, AcademicSourceTable, AdaptedAcademicStaff,
    adapt_academic_staff, adapt_academic_tables, adapt_administrative_resolution,
)
from .contracts import (
    InputEvidence, InputMode, NormalizedSchedulingInput, ReadinessStatus,
    SchedulingSubmission, SourceAttestation, SourceOwner,
)
from .diagnostics import ReadinessDiagnostic, ReadinessReport
from .readiness import ReadinessAssessment, assess_readiness
from .service import SchedulingIntegrationResult, run_scheduling

__all__ = [
    "AcademicAdaptation", "AcademicSourceRow", "AcademicSourceTable", "AdaptedAcademicStaff",
    "adapt_academic_staff", "adapt_academic_tables", "adapt_administrative_resolution", "InputEvidence", "InputMode",
    "NormalizedSchedulingInput", "ReadinessStatus", "SchedulingSubmission", "SourceAttestation",
    "SourceOwner", "ReadinessDiagnostic", "ReadinessReport", "ReadinessAssessment",
    "assess_readiness", "SchedulingIntegrationResult", "run_scheduling",
]
