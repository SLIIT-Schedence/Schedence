"""Versioned persistence envelopes composed from existing scheduling contracts.

These are historical input documents, not another academic master database.
No database package is imported here.
"""

from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, JsonValue, model_validator, model_serializer

from schedence_teaching.consecutive import ConsecutivePolicy
from schedence_teaching.fairness import FairnessPolicy
from schedence_teaching.integration.contracts import SourceOwner
from schedence_teaching.lunch import StudentLunchPolicy
from schedence_teaching.preferences import LecturerPreferenceInput, PreferenceCalendar, PreferenceScoringPolicy
from schedence_teaching.schemas import GroupCombinationInput, Stream, TeachingTimetableInput
from schedence_teaching.schemas.models import AcademicYear, InputContract, PositiveInteger, RequiredText, Semester
from schedence_teaching.solver import (
    CombinationSolverConfig, ConsecutiveOptimizationConfig, FairnessOptimizationConfig,
    PreferenceOptimizationConfig, SolverConfig, StaffSchedulingInput,
    StudentLunchEnforcementConfig, VenueSchedulingInput, WorkPeriodSolverConfig,
)
from schedence_teaching.workload import WorkPeriodInput

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$", strict=True)]


class ProvenanceKind(StrEnum):
    UNIVERSITY_SOURCED = "UNIVERSITY_SOURCED"
    APPROVED_PP1_ESTIMATE = "APPROVED_PP1_ESTIMATE"
    SYNTHETIC_PP1 = "SYNTHETIC_PP1"
    EXPERIMENTAL_CONFIGURATION = "EXPERIMENTAL_CONFIGURATION"
    USER_CONFIRMED_CORRECTION = "USER_CONFIRMED_CORRECTION"
    SERVICE_EXPORT = "SERVICE_EXPORT"


class SourceDocument(InputContract):
    label: RequiredText
    sha256: Sha256
    owner: SourceOwner
    provenance_kind: ProvenanceKind
    # Source rows, original status, subsequent confirmation, allocation algorithm,
    # registration seed and policy revisions can coexist without overwriting source.
    provenance: dict[str, JsonValue]


class ExternalReference(InputContract):
    owner: SourceOwner
    record_kind: RequiredText
    local_reference: RequiredText
    authoritative_id: UUID | None = None
    source_reference: RequiredText


class ScopeDocument(InputContract):
    scope_key: RequiredText
    academic_year: AcademicYear
    semester: Semester
    stream: Stream
    specialization: RequiredText
    student_count: PositiveInteger | None = None
    scope_status: Literal["SUPPORTED", "BLOCKED_MISSING_ACADEMIC_MAPPING"]
    group_references: tuple[RequiredText, ...] = ()

    @model_validator(mode="after")
    def valid_groups(self):
        if len(set(self.group_references)) != len(self.group_references):
            raise ValueError("scope group references must be unique")
        if (self.scope_status == "SUPPORTED") != bool(self.group_references):
            raise ValueError("supported scopes need groups; blocked scopes must not invent groups")
        return self


class IssueDocument(InputContract):
    issue_code: RequiredText
    severity: Literal["INFO", "WARNING", "BLOCKER"]
    scope_key: RequiredText | None = None
    details: dict[str, JsonValue]


class SchedulingContexts(InputContract):
    """Original optional solver inputs, never pickled prepared Python objects."""

    venue_input: VenueSchedulingInput | None = None
    staff_input: StaffSchedulingInput | None = None
    combination_input: GroupCombinationInput | None = None
    combination_config: CombinationSolverConfig | None = None
    work_period_input: WorkPeriodInput | None = None
    work_period_config: WorkPeriodSolverConfig | None = None
    preference_input: LecturerPreferenceInput | None = None
    preference_policy: PreferenceScoringPolicy | None = None
    preference_config: PreferenceOptimizationConfig | None = None
    fairness_policy: FairnessPolicy | None = None
    fairness_config: FairnessOptimizationConfig | None = None
    consecutive_policy: ConsecutivePolicy | None = None
    consecutive_config: ConsecutiveOptimizationConfig | None = None
    student_lunch_policy: StudentLunchPolicy | None = None
    student_lunch_config: StudentLunchEnforcementConfig | None = None

    @model_validator(mode="after")
    def paired_features(self):
        for names in (
            ("combination_input", "combination_config"),
            ("work_period_input", "work_period_config"),
            ("preference_input", "preference_policy", "preference_config"),
            ("fairness_policy", "fairness_config"),
            ("consecutive_policy", "consecutive_config"),
            ("student_lunch_policy", "student_lunch_config"),
        ):
            values = [getattr(self, name) is not None for name in names]
            if any(values) and not all(values):
                raise ValueError(f"feature needs all original inputs: {', '.join(names)}")
        if self.work_period_input is not None and self.staff_input is None:
            raise ValueError("work periods require original staff policy input")
        return self


class SnapshotDocument(InputContract):
    schema_version: Literal[1] = 1
    institution_id: UUID
    academic_term_reference: RequiredText
    timezone_name: RequiredText
    calendar_label: Literal["PP1_TEST_CALENDAR", "PP1_EXPERIMENTAL_CALENDAR", "SUPPLIED_CALENDAR"]
    calendar: PreferenceCalendar
    teaching_input: TeachingTimetableInput
    solver_config: SolverConfig
    contexts: SchedulingContexts = Field(default_factory=SchedulingContexts)
    group_specializations: dict[RequiredText, RequiredText]
    scopes: tuple[ScopeDocument, ...] = Field(min_length=1)
    sources: tuple[SourceDocument, ...] = Field(min_length=1)
    issues: tuple[IssueDocument, ...] = ()
    external_references: tuple[ExternalReference, ...] = ()
    resolved_policy_digest: Sha256 | None = None

    @model_serializer(mode='wrap')
    def historical_payload(self, handler):
        result = handler(self)
        if self.resolved_policy_digest is None:
            result.pop('resolved_policy_digest', None)
        return result

    @model_validator(mode="after")
    def consistent_snapshot(self):
        if self.solver_config.resolved_policy is not None or self.resolved_policy_digest is not None:
            from .policy import verify_snapshot_policy
            verify_snapshot_policy(self)
        if self.institution_id.int == 0:
            raise ValueError("an institution identifier must be explicitly supplied")
        try:
            ZoneInfo(self.timezone_name)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("timezone_name must be an IANA timezone") from None
        if set(self.calendar.day_dates) != {day.day for day in self.solver_config.days}:
            raise ValueError("calendar must date every configured scheduling day exactly")
        if self.contexts.staff_input is not None and self.contexts.staff_input.day_dates != self.calendar.day_dates:
            raise ValueError("snapshot and staff calendars must use the same actual dates")
        if len({source.label for source in self.sources}) != len(self.sources):
            raise ValueError("source labels must be unique within a snapshot")
        references = [(ref.owner, ref.record_kind, ref.local_reference) for ref in self.external_references]
        if len(set(references)) != len(references):
            raise ValueError("external reference mappings must be unambiguous")
        scopes = {scope.scope_key: scope for scope in self.scopes}
        if len(scopes) != len(self.scopes):
            raise ValueError("scope keys must be unique within a snapshot")
        if any(issue.scope_key is not None and issue.scope_key not in scopes for issue in self.issues):
            raise ValueError("issue references an unknown scope")
        groups = {group.group_id: group for group in self.teaching_input.student_groups}
        if set(self.group_specializations) != set(groups):
            raise ValueError("specialization mappings must cover exactly the supplied groups")
        covered = set()
        for scope in self.scopes:
            for reference in scope.group_references:
                group = groups.get(reference)
                if group is None or reference in covered:
                    raise ValueError("scope references an unknown or multiply covered group")
                if (group.academic_year, group.semester, group.stream, self.group_specializations[reference]) != (
                    scope.academic_year, scope.semester, scope.stream, scope.specialization
                ):
                    raise ValueError("scope and original group metadata must agree")
                covered.add(reference)
        if covered != set(groups):
            raise ValueError("every supplied group must belong to a supported scope")
        if self.contexts.combination_input is not None:
            if self.contexts.combination_input.teaching_input != self.teaching_input:
                raise ValueError("combination and snapshot teaching inputs must match")
        return self
