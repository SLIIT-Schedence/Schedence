"""Unscheduled delivery alternatives retain exact original occurrence coverage."""

from dataclasses import dataclass

from schedence_teaching.schemas import GroupCombinationInput, Stream

from .sessions import PreparedTimetableInput, UnscheduledSession


@dataclass(frozen=True)
class CombinedLectureOccurrence:
    occurrence_id: str
    occurrence_number: int
    covered_sessions: tuple[UnscheduledSession, ...]
    combined_student_count: int
    eligible_staff_ids: tuple[str, ...]

    @property
    def duration_minutes(self) -> int:
        return self.covered_sessions[0].duration_minutes

    def to_dict(self) -> dict:
        return {
            "occurrence_id": self.occurrence_id, "occurrence_number": self.occurrence_number,
            "covered_occurrence_ids": [session.occurrence_id for session in self.covered_sessions],
            "covered_sessions": [session.to_dict() for session in self.covered_sessions],
            "combined_student_count": self.combined_student_count,
            "eligible_staff_ids": list(self.eligible_staff_ids),
            "duration_minutes": self.duration_minutes,
        }


@dataclass(frozen=True)
class GroupCombinationCandidate:
    candidate_id: str
    requirement_ids: tuple[str, ...]
    group_ids: tuple[str, ...]
    offering_ids: tuple[str, ...]
    specialization: str
    academic_year: int
    semester: int
    stream: Stream
    content_kind: str
    content_reference: str
    duration_minutes: int
    sessions_per_week: int
    combined_student_count: int
    eligible_staff_ids: tuple[str, ...]
    population_evidence_references: tuple[str, ...]
    occurrences: tuple[CombinedLectureOccurrence, ...]

    def to_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id, "requirement_ids": list(self.requirement_ids),
            "group_ids": list(self.group_ids), "offering_ids": list(self.offering_ids),
            "specialization": self.specialization, "academic_year": self.academic_year,
            "semester": self.semester, "stream": self.stream.value, "session_type": "LECTURE",
            "content_kind": self.content_kind, "content_reference": self.content_reference,
            "duration_minutes": self.duration_minutes, "sessions_per_week": self.sessions_per_week,
            "combined_student_count": self.combined_student_count,
            "eligible_staff_ids": list(self.eligible_staff_ids),
            "population_evidence_references": list(self.population_evidence_references),
            "weekly_delivery_count": len(self.occurrences),
            "weekly_delivery_minutes": self.duration_minutes * len(self.occurrences),
            "occurrences": [occurrence.to_dict() for occurrence in self.occurrences],
        }


@dataclass(frozen=True)
class CombinationDiagnostic:
    code: str
    requirement_ids: tuple[str, ...]
    message: str

    def to_dict(self) -> dict:
        return {"code": self.code, "requirement_ids": list(self.requirement_ids), "message": self.message}


@dataclass(frozen=True)
class PreparedGroupCombinations:
    input_snapshot: GroupCombinationInput
    prepared: PreparedTimetableInput
    candidates: tuple[GroupCombinationCandidate, ...]
    diagnostics: tuple[CombinationDiagnostic, ...]
    max_candidates: int

    @property
    def separate_sessions(self) -> tuple[UnscheduledSession, ...]:
        return self.prepared.sessions

    def to_dict(self) -> dict:
        return {
            "original_group_count": self.prepared.summary.total_student_groups,
            "original_requirement_count": self.prepared.summary.session_requirement_count,
            "original_weekly_occurrence_count": len(self.separate_sessions),
            "candidate_count": len(self.candidates),
            "separate_sessions": [session.to_dict() for session in self.separate_sessions],
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "diagnostics": [diagnostic.to_dict() for diagnostic in self.diagnostics],
            "alternatives_only": True, "university_ready": False,
        }


@dataclass(frozen=True)
class DeliveryCoverageSummary:
    original_occurrences_covered: int
    combined_deliveries: int
    separate_deliveries: int
    delivery_count: int
    delivery_contact_minutes: int

    def to_dict(self) -> dict:
        return {**vars(self), "exact_coverage": True, "scheduled": False, "university_ready": False}
