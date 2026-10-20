"""Immutable, source-preserving consecutive-teaching diagnostics."""

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date
import json

from schedence_teaching.schemas import Stream
from schedence_teaching.workload.metrics import TeachingMetrics


@dataclass(frozen=True)
class SessionGap:
    previous_delivery_id: str
    next_delivery_id: str
    previous_end_minute: int
    next_start_minute: int
    gap_minutes: int
    is_lecture_pair: bool


@dataclass(frozen=True)
class TeachingRun:
    delivery_ids: tuple[str, ...]
    start_minute: int
    end_minute: int
    teaching_minutes: int
    session_count: int


@dataclass(frozen=True)
class DayStreamSequence:
    day: str
    on_date: date
    stream: Stream
    delivery_ids: tuple[str, ...]
    teaching_minutes: int
    gaps: tuple[SessionGap, ...]
    uninterrupted_runs: tuple[TeachingRun, ...]
    back_to_back_pairs: int
    back_to_back_lecture_pairs: int
    maximum_consecutive_sessions: int
    maximum_continuous_minutes: int
    preferred_break_shortfall_minutes: int | None
    excess_continuous_minutes: int | None
    confirmed_hard_break_violations: tuple[SessionGap, ...]
    policy_applicable: bool = True


@dataclass(frozen=True)
class EntityConsecutiveAssessment:
    kind: str
    entity_id: str
    scoring_enabled: bool
    teaching: TeachingMetrics
    direct_delivery_ids: tuple[str, ...]
    inherited_delivery_ids: tuple[str, ...]
    sequences: tuple[DayStreamSequence, ...]
    back_to_back_pairs: int
    back_to_back_lecture_pairs: int
    maximum_consecutive_sessions: int
    maximum_continuous_minutes: int
    gap_distribution: tuple[tuple[int, int], ...]
    stream_transitions_not_compared: int
    penalty_components: tuple[tuple[str, int | None], ...]
    known_soft_penalty: int
    total_soft_penalty: int | None
    scoring_complete: bool | None
    confirmed_hard_break_violation_count: int
    confirmed_hard_break_compliant: bool | None
    diagnostics: tuple[str, ...]


def json_value(value):
    """Exact integer output; used for strict report regeneration checks as well."""
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: json_value(row) for key, row in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_value(row) for row in value]
    return value


@dataclass(frozen=True)
class ConsecutiveReport:
    policy_snapshot_json: str
    calendar_snapshot_json: str
    assigned_physical_delivery_count: int
    scheduled_teaching_minutes: int
    lecturers: tuple[EntityConsecutiveAssessment, ...]
    student_groups: tuple[EntityConsecutiveAssessment, ...]
    known_soft_penalty: int
    total_soft_penalty: int | None
    scoring_complete: bool | None
    confirmed_hard_break_violation_count: int
    confirmed_hard_break_compliant: bool | None
    hard_validation_checks: tuple[str, ...]
    diagnostics: tuple[str, ...]
    cp_sat_consecutive_objective_enabled: bool = False
    confirmed_hard_break_enforced_by_solver: bool = False

    def to_dict(self, *, include_lecturer_details=False, include_student_group_details=False):
        def summary(rows):
            gaps = Counter()
            for row in rows:
                gaps.update(dict(row.gap_distribution))
            return {"entity_count": len(rows), "back_to_back_pairs": sum(row.back_to_back_pairs for row in rows),
                "back_to_back_lecture_pairs": sum(row.back_to_back_lecture_pairs for row in rows),
                "maximum_consecutive_sessions": max((row.maximum_consecutive_sessions for row in rows), default=0),
                "maximum_continuous_minutes": max((row.maximum_continuous_minutes for row in rows), default=0),
                "gap_distribution": {str(minutes): count for minutes, count in sorted(gaps.items())},
                "stream_transitions_not_compared": sum(row.stream_transitions_not_compared for row in rows)}
        output = {"policy": json.loads(self.policy_snapshot_json), "academic_week": json.loads(self.calendar_snapshot_json),
            "assigned_physical_delivery_count": self.assigned_physical_delivery_count,
            "scheduled_teaching_minutes": self.scheduled_teaching_minutes,
            "lecturer_summary": summary(self.lecturers), "student_group_summary": summary(self.student_groups),
            "known_soft_penalty": self.known_soft_penalty, "total_soft_penalty": self.total_soft_penalty,
            "scoring_complete": self.scoring_complete,
            "confirmed_hard_break_violation_count": self.confirmed_hard_break_violation_count,
            "confirmed_hard_break_compliant": self.confirmed_hard_break_compliant,
            "hard_validation_checks": list(self.hard_validation_checks), "diagnostics": list(self.diagnostics),
            "cp_sat_consecutive_objective_enabled": self.cp_sat_consecutive_objective_enabled,
            "confirmed_hard_break_enforced_by_solver": self.confirmed_hard_break_enforced_by_solver,
            "gap_scope": "elapsed_time_between_adjacent_same_date_same_stream_teaching_not_verified_rest",
            "group_attendance_scope": "direct_and_ancestor_requirements_not_sibling_union",
            "hard_constraints_overridden": False, "university_ready": False}
        if include_lecturer_details:
            output["lecturers"] = json_value([asdict(row) for row in self.lecturers])
        if include_student_group_details:
            output["student_groups"] = json_value([asdict(row) for row in self.student_groups])
        return output
