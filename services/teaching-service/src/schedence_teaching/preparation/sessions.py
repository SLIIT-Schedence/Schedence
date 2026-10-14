"""Deterministic unscheduled occurrences and aggregate teaching-demand summaries."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from schedence_teaching.schemas import SessionType, Stream, TeachingTimetableInput

from .errors import PreparationError
from .hierarchy import GroupHierarchy, _build_hierarchy
from .validation import period_issues, validated_snapshot


@dataclass(frozen=True)
class UnscheduledSession:
    occurrence_id: str
    requirement_id: str
    offering_id: str
    group_id: str
    session_type: SessionType
    duration_minutes: int
    occurrence_number: int
    stream: Stream

    def to_dict(self) -> dict:
        return {
            "occurrence_id": self.occurrence_id, "requirement_id": self.requirement_id,
            "offering_id": self.offering_id, "group_id": self.group_id,
            "session_type": self.session_type.value, "duration_minutes": self.duration_minutes,
            "occurrence_number": self.occurrence_number, "stream": self.stream.value,
        }


@dataclass(frozen=True)
class PreparationSummary:
    total_student_groups: int
    main_groups: int
    subgroups: int
    session_requirement_count: int
    expanded_weekly_session_count: int
    weekly_contact_minutes: int
    session_type_counts: Mapping[SessionType, int]
    stream_counts: Mapping[Stream, int]

    def to_dict(self) -> dict:
        return {
            "total_student_groups": self.total_student_groups, "main_groups": self.main_groups,
            "subgroups": self.subgroups, "session_requirement_count": self.session_requirement_count,
            "expanded_weekly_session_count": self.expanded_weekly_session_count,
            "weekly_contact_minutes": self.weekly_contact_minutes,
            "session_type_counts": {kind.value: count for kind, count in self.session_type_counts.items()},
            "stream_counts": {stream.value: count for stream, count in self.stream_counts.items()},
        }


@dataclass(frozen=True)
class PreparedTimetableInput:
    input_snapshot: TeachingTimetableInput
    hierarchy: GroupHierarchy
    sessions: tuple[UnscheduledSession, ...]
    summary: PreparationSummary

    @property
    def sessions_by_stream(self) -> Mapping[Stream, tuple[UnscheduledSession, ...]]:
        """Convenient views of one global demand set, not separate resource pools."""
        return MappingProxyType({
            stream: tuple(session for session in self.sessions if session.stream == stream)
            for stream in Stream
        })


def _compatible_requirements(inputs: TeachingTimetableInput) -> None:
    offerings = {offering.offering_id: offering for offering in inputs.module_offerings}
    groups = {group.group_id: group for group in inputs.student_groups}
    issues = []
    for index, requirement in enumerate(inputs.session_requirements):
        issues.extend(period_issues(
            groups[requirement.group_id], offerings[requirement.offering_id],
            code="offering_group_period_mismatch", location=("session_requirements", index),
            description=f"Requirement {requirement.requirement_id}: group {requirement.group_id} and offering {requirement.offering_id}",
        ))
    if issues:
        raise PreparationError(issues)


def _expand(inputs: TeachingTimetableInput) -> tuple[UnscheduledSession, ...]:
    streams = {offering.offering_id: offering.stream for offering in inputs.module_offerings}
    return tuple(
        UnscheduledSession(
            occurrence_id=f"{requirement.requirement_id}:{number}",
            requirement_id=requirement.requirement_id, offering_id=requirement.offering_id,
            group_id=requirement.group_id, session_type=requirement.session_type,
            duration_minutes=requirement.duration_minutes, occurrence_number=number,
            stream=streams[requirement.offering_id],
        )
        for requirement in sorted(inputs.session_requirements, key=lambda item: item.requirement_id)
        for number in range(1, requirement.sessions_per_week + 1)
    )


def prepare_timetable_input(inputs: TeachingTimetableInput) -> PreparedTimetableInput:
    """Prepare validated contracts; never assign staff, venues, days or slots."""
    snapshot = validated_snapshot(inputs)
    hierarchy = _build_hierarchy(snapshot.student_groups)
    _compatible_requirements(snapshot)
    sessions = _expand(snapshot)
    type_counts = Counter(session.session_type for session in sessions)
    stream_counts = Counter(session.stream for session in sessions)
    summary = PreparationSummary(
        total_student_groups=len(snapshot.student_groups), main_groups=len(hierarchy.main_group_ids),
        subgroups=len(hierarchy.subgroup_ids), session_requirement_count=len(snapshot.session_requirements),
        expanded_weekly_session_count=len(sessions),
        weekly_contact_minutes=sum(session.duration_minutes for session in sessions),
        session_type_counts=MappingProxyType({kind: type_counts[kind] for kind in SessionType}),
        stream_counts=MappingProxyType({stream: stream_counts[stream] for stream in Stream}),
    )
    return PreparedTimetableInput(snapshot, hierarchy, sessions, summary)


def expand_session_requirements(inputs: TeachingTimetableInput) -> tuple[UnscheduledSession, ...]:
    """Return only the occurrences after the same complete preparation checks."""
    return prepare_timetable_input(inputs).sessions
