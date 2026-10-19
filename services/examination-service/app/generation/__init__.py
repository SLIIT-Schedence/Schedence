"""On-request examination timetable generation (reuses the existing scheduler; saves nothing)."""

from app.generation.service import GenerationOutcome, GenerationRequest, TimetableGenerator

__all__ = ["GenerationOutcome", "GenerationRequest", "TimetableGenerator"]
