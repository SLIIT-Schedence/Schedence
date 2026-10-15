"""Scheduling snapshots and source attestations; no academic master records."""

from datetime import date
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import Field, StrictBool, StrictInt, model_validator

from schedence_teaching.consecutive import ConsecutivePolicy
from schedence_teaching.excel_import.staging import SourceLocation
from schedence_teaching.fairness import FairnessPolicy
from schedence_teaching.lunch import StudentLunchPolicy
from schedence_teaching.preferences import LecturerPreferenceInput, PreferenceCalendar, PreferenceScoringPolicy
from schedence_teaching.schemas import GroupCombinationInput
from schedence_teaching.schemas.models import InputContract, RequiredText, Semester
from schedence_teaching.solver import (
    CombinationSolverConfig, ConsecutiveOptimizationConfig, FairnessOptimizationConfig,
    PreferenceOptimizationConfig, SolverConfig, StaffSchedulingInput,
    StudentLunchEnforcementConfig, VenueSchedulingInput, WorkPeriodSolverConfig,
)
from schedence_teaching.workload import PeriodProvenance, WorkPeriodInput


class ReadinessStatus(StrEnum):
    CONFIRMED = "CONFIRMED"
    MISSING = "MISSING"
    AMBIGUOUS = "AMBIGUOUS"
    UNVERIFIED = "UNVERIFIED"
    INVALID = "INVALID"


class InputMode(StrEnum):
    ACTUAL_CAMPUS = "ACTUAL_CAMPUS"
    SYNTHETIC = "SYNTHETIC"


class SourceOwner(StrEnum):
    ACADEMIC = "ACADEMIC_DATA_SERVICE"
    RESOURCE = "RESOURCE_SERVICE"
    TEACHING = "TEACHING_SERVICE"
    IDENTITY = "IDENTITY_SERVICE"


class SourceAttestation(InputContract):
    """A caller-supplied verification claim, not authentication or approval."""

    owner: SourceOwner
    institution_id: UUID
    status: ReadinessStatus
    provenance: PeriodProvenance
    confirmation_reference: RequiredText | None = None
    effective_from: date
    effective_until: date
    synthetic: StrictBool
    location: SourceLocation | None = None
    columns: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_scope(self):
        if self.effective_until < self.effective_from:
            raise ValueError("source validity must be ordered")
        if self.location is not None and (self.location.row < 1 or not self.location.workbook.strip()
                                          or not self.location.worksheet.strip()):
            raise ValueError("source location needs workbook, worksheet and positive row")
        return self


class InputEvidence(SourceAttestation):
    # Exact structural path; no wildcard matching or guessed workbook row offsets.
    path: tuple[RequiredText | StrictInt, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def nonnegative_indices(self):
        if any(type(part) is int and part < 0 for part in self.path):
            raise ValueError("source indices must be nonnegative")
        return self


class SchedulingSubmission(InputContract):
    """Raw payload permits useful diagnostics for incomplete upstream exports."""

    inputs: dict[str, Any]
    evidence: list[InputEvidence]


class NormalizedSchedulingInput(InputContract):
    """Full-policy integration profile composed from existing public contracts.

    Calendar academic term and StudentGroup.academic_year (study year) differ.
    No policy feature is silently omitted in this profile; existing direct solver
    callers retain their optional feature contracts unchanged.
    """

    institution_id: UUID
    mode: InputMode
    academic_term_reference: RequiredText
    semester: Semester
    demand_complete: StrictBool
    academic_staff_references: dict[RequiredText, UUID]
    calendar: PreferenceCalendar
    solver_config: SolverConfig
    combination_input: GroupCombinationInput
    combination_config: CombinationSolverConfig
    venue_input: VenueSchedulingInput
    staff_input: StaffSchedulingInput
    work_period_input: WorkPeriodInput
    work_period_config: WorkPeriodSolverConfig
    preference_input: LecturerPreferenceInput
    preference_policy: PreferenceScoringPolicy
    preference_config: PreferenceOptimizationConfig
    fairness_policy: FairnessPolicy
    fairness_config: FairnessOptimizationConfig
    consecutive_policy: ConsecutivePolicy
    consecutive_config: ConsecutiveOptimizationConfig
    student_lunch_policy: StudentLunchPolicy
    student_lunch_config: StudentLunchEnforcementConfig

    @property
    def teaching_input(self):
        return self.combination_input.teaching_input


def expected_owner(root: str) -> SourceOwner:
    if root == "venue_input":
        return SourceOwner.RESOURCE
    if root in {"mode", "solver_config", "combination_config", "work_period_config",
                "preference_policy", "preference_config", "fairness_policy", "fairness_config",
                "consecutive_policy", "consecutive_config", "student_lunch_config"}:
        return SourceOwner.TEACHING
    return SourceOwner.ACADEMIC
