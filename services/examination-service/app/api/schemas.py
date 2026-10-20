"""Response models of the examination API (built from ``app.sources`` records)."""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.deps import MODULE_CODE, PERIOD_CODE
from app.exam_periods.rules import MAX_EXCLUSIONS, NEW_PERIOD_CODE, ExamCalendar, build_period
from app.module_details.rules import ExamDetails
from app.generation.service import MAX_API_TIME_LIMIT_SECONDS, MAX_MODULES
from app.generation.preferences import validate_preferences
from app.scheduling.configuration import SchedulingConfiguration
from app.seating.rules import MAX_PERCENTAGE, MIN_PERCENTAGE
from app.sources.types import ExamPeriod
from app.api.analysis_schemas import EvaluationAnalysisOut
from app.api.cross_timetable_schemas import CrossTimetableOut

T = TypeVar("T")


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ModuleOut(_Out):
    module_code: str
    module_name: str
    academic_year: int
    semester: int
    specializations: list[str]
    credits: int
    difficulty: int
    exam_type: str
    duration_minutes: int = Field(description="FINAL examination duration")
    has_extra_reading_time: bool
    reading_time_minutes: int
    has_final_exam: bool
    has_mid_exam: bool
    mid_duration_minutes: int | None = Field(description="MID examination duration; null without a MID exam")
    delivery_mode: Literal["regular", "block"]
    block_category: Literal["pro_rata", "full_module"] | None = Field(
        description="Only for block modules (label from the data; not an official classification)")
    candidate_count: int
    fingerprint: str | None = Field(
        None, description="Version of the examination details; send it as expected_fingerprint with PUT exam-details")


class StudentOut(_Out):
    student_id: str
    academic_year: int
    specialization: str


class RegistrationOut(_Out):
    student_id: str
    module_code: str
    registration_type: str


class StudentRegistrationOut(_Out):
    module_code: str
    registration_type: str


class StudentDetailOut(StudentOut):
    registrations: list[StudentRegistrationOut]


class VenueOut(_Out):
    venue_id: str
    venue_name: str
    venue_type: str
    building: str
    floor: int
    normal_capacity: int = Field(description="Full seating capacity")
    exam_seating_percentage: int = Field(description="Share of the full capacity usable in an exam (1-100)")
    exam_seating_rule: Literal["default", "venue"] = Field(description="Where the percentage comes from")
    exam_capacity: int = Field(description="Effective exam seats: floor(full capacity x percentage / 100)")
    available_for_exams: bool = Field(description="False when the effective exam capacity is 0")
    seed_exam_capacity: int = Field(description="Legacy stored value (always 50%); not used for scheduling")


class VenueBlockOut(_Out):
    venue_id: str
    block_date: dt.date
    start_time: dt.time
    end_time: dt.time
    source: str
    reason: str
    source_ref: str | None


class ExamPeriodExclusionOut(_Out):
    excluded_date: dt.date
    reason: str


ExamKind = Literal["MID", "FINAL"]


class ExamPeriodOut(_Out):
    period_code: str
    period_name: str
    academic_year_label: str
    semester: int
    exam_kind: ExamKind
    start_date: dt.date
    end_date: dt.date
    day_start_time: dt.time
    day_end_time: dt.time
    include_saturdays: bool
    include_sundays: bool
    exclusions: list[ExamPeriodExclusionOut]
    exam_days: list[dt.date]
    fingerprint: str | None = Field(
        None, description="Version of this definition; send it as expected_fingerprint with PUT")


class ExamPeriodModuleOut(_Out):
    period_code: str
    module_code: str
    candidate_scope: Literal["all", "repeat_only"] = Field(
        description="all: normal and repeat registrations; repeat_only: only repeat registrations")


# --- Examination seating settings --------------------------------------------------

Percentage = Annotated[int, Field(ge=MIN_PERCENTAGE, le=MAX_PERCENTAGE, description="Whole percentage 1-100")]
Version = Annotated[int, Field(ge=1, le=2_147_483_647)]
# 5-500 printable characters; no control characters, no leading/trailing spaces.
REASON_PATTERN = r"^[^\s\x00-\x1f\x7f](?:[^\x00-\x1f\x7f]*[^\s\x00-\x1f\x7f])?$"
REASON_MIN, REASON_MAX = 5, 500
Reason = Annotated[str, Field(min_length=REASON_MIN, max_length=REASON_MAX, pattern=REASON_PATTERN,
                              description="Why the rule changes (kept in the history)")]


class _In(BaseModel):
    # strict: 50.0, "50" or true are not accepted as a percentage.
    model_config = ConfigDict(extra="forbid", strict=True)


class SetDefaultSeatingIn(_In):
    percentage: Percentage
    reason: Reason
    expected_version: Version = Field(description="The default rule's version you last read (409 if it changed)")


class SetVenueSeatingIn(_In):
    percentage: Percentage
    reason: Reason
    expected_version: Version | None = Field(
        description="The override's version you last read; null to create a new override"
    )


class SeatingRuleOut(_Out):
    scope: Literal["default", "venue"]
    venue_id: str | None
    percentage: int
    version: int
    reason: str
    updated_by: UUID | None = Field(description="Identity Service user ID; null = set by a migration")
    updated_at: dt.datetime


class SeatingHistoryOut(_Out):
    history_id: int
    action: Literal["create", "update", "delete"]
    scope: Literal["default", "venue"]
    venue_id: str | None
    old_percentage: int | None
    new_percentage: int | None
    version: int
    changed_by: UUID | None
    changed_at: dt.datetime
    reason: str


class ExamSeatingSettingsOut(BaseModel):
    min_percentage: int = MIN_PERCENTAGE
    max_percentage: int = MAX_PERCENTAGE
    ruleset_version: int = Field(description="Changes whenever any rule changes")
    default: SeatingRuleOut
    venue_overrides: list[SeatingRuleOut]
    venue_count: int
    total_normal_capacity: int
    total_exam_capacity: int
    unavailable_venues: list[str] = Field(description="Venues whose effective exam capacity is 0")
    history: list[SeatingHistoryOut] = Field(description="Newest first")


class VenueSeatingChangeOut(_Out):
    venue_id: str
    normal_capacity: int
    old_percentage: int
    new_percentage: int
    old_exam_capacity: int
    new_exam_capacity: int
    available_for_exams: bool


class SeatingChangeOut(_Out):
    dry_run: bool
    changed: bool = Field(description="False when the rule already had this value (nothing saved)")
    action: Literal["create", "update", "delete"] | None
    scope: Literal["default", "venue"]
    venue_id: str | None
    old_percentage: int | None
    new_percentage: int | None
    version: int | None = Field(description="Rule version after the change (dry run: the version it would get)")
    history_id: int | None
    changed_by: UUID | None
    affected_venues: list[VenueSeatingChangeOut]
    newly_unavailable_venues: list[str]
    total_exam_capacity_before: int
    total_exam_capacity_after: int


# --- Examination scheduling settings -----------------------------------------------

SettingsVersion = Annotated[int, Field(ge=0, le=2_147_483_647)]


class SetSchedulingSettingsIn(_In):
    settings: dict[str, Any] = Field(
        description="The complete settings document (as returned by GET). Every section and key is required; "
                    "unknown keys are rejected. Hard rules H1-H11 cannot be configured.",
        examples=[SchedulingConfiguration().to_document()],
    )
    reason: Reason
    expected_version: SettingsVersion = Field(
        description="The settings version you last read (0 = nothing saved yet; 409 if it changed)"
    )


class SettingsProblemOut(_Out):
    rule: str
    message: str


class SchedulingSettingsHistoryOut(_Out):
    history_id: int
    action: Literal["create", "update", "repair"]
    version: int
    changed_fields: list[str]
    changed_by: UUID
    changed_at: dt.datetime
    reason: str


class SchedulingSettingsOut(_Out):
    institution_id: UUID
    source: Literal["defaults", "saved"]
    status: Literal["valid", "invalid"] = Field(
        description="invalid: the saved document fails validation; the scheduler will not use it until an "
                    "admin repairs it (PUT with replace_invalid=true)")
    problems: list[SettingsProblemOut] = Field(description="Why the saved document is invalid (empty when valid)")
    version: int
    format: str = Field(description="Format of the saved document (or of the defaults)")
    fingerprint: str | None = Field(description="Fingerprint of the valid configuration; null when invalid")
    reason: str | None
    updated_by: UUID | None
    updated_at: dt.datetime | None
    settings: dict[str, Any] = Field(description="The configuration; when invalid, the saved document as stored")
    mandatory_rules: list[str]
    history: list[SchedulingSettingsHistoryOut]


class SchedulingSettingsChangeOut(_Out):
    dry_run: bool
    changed: bool
    action: Literal["create", "update", "repair"] | None
    version: int
    history_id: int | None
    changed_fields: list[str]
    changed_by: UUID | None
    fingerprint: str
    settings: dict[str, Any]


# --- Examination period management ------------------------------------------------

PeriodText = Annotated[str, Field(min_length=3, max_length=120, pattern=REASON_PATTERN)]
# Dates and times arrive as JSON strings ("2027-03-01", "08:30"), so they are parsed, not strict.
PeriodDate = Annotated[dt.date, Field(strict=False)]
PeriodTime = Annotated[dt.time, Field(strict=False, description="Local time HH:MM (whole minutes, no time zone)")]


class ExamPeriodExclusionIn(_In):
    excluded_date: PeriodDate
    reason: PeriodText


class _ExamPeriodFieldsIn(_In):
    period_name: PeriodText
    academic_year_label: str = Field(min_length=9, max_length=9, description="Two consecutive years, e.g. 2026/2027")
    semester: Literal[1, 2]
    exam_kind: ExamKind
    start_date: PeriodDate
    end_date: PeriodDate
    day_start_time: PeriodTime
    day_end_time: PeriodTime
    include_saturdays: bool
    include_sundays: bool
    exclusions: list[ExamPeriodExclusionIn] = Field(
        max_length=MAX_EXCLUSIONS, description="Every non-exam date inside the period (replaces the stored list)")

    def to_period(self, period_code: str) -> ExamPeriod:
        fields = self.model_dump(exclude={"exclusions", "period_code", "expected_fingerprint"})
        return build_period(period_code=period_code, **fields,
                            exclusions=[(e.excluded_date, e.reason) for e in self.exclusions])


class CreateExamPeriodIn(_ExamPeriodFieldsIn):
    period_code: str = Field(max_length=32, pattern=NEW_PERIOD_CODE, description="e.g. PP1-2027-S1-MID")


class UpdateExamPeriodIn(_ExamPeriodFieldsIn):
    expected_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$",
                                      description="The period's fingerprint you last read (409 if it changed)")


class ExamPeriodChangeOut(_Out):
    dry_run: bool
    action: Literal["create", "update"]
    changed: bool = Field(description="False when the period already had these values (nothing saved)")
    changed_fields: list[str]
    period: ExamPeriodOut
    draft_timetables: int = Field(description="Saved DRAFT timetables of this period (stale after a change)")
    notices: list[str]


# --- Module examination details --------------------------------------------------


class UpdateModuleExamDetailsIn(_In):
    """The complete examination details of a module (every field required; ranges are checked by the service)."""

    exam_type: Literal["written", "computer_based", "hybrid"]
    duration_minutes: int = Field(description="FINAL examination writing time (15-480)")
    reading_time_minutes: int = Field(description="Added to every sitting, MID and FINAL (0-60)")
    difficulty: int = Field(description="1-5")
    has_final_exam: bool
    has_mid_exam: bool
    mid_duration_minutes: int | None = Field(description="Required with a MID exam (15-480); null without one")
    delivery_mode: Literal["regular", "block"]
    block_category: Literal["pro_rata", "full_module"] | None = Field(description="Required for block modules only")
    expected_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$",
                                      description="The module's fingerprint you last read (409 if it changed)")

    def to_details(self) -> ExamDetails:
        return ExamDetails(**self.model_dump(exclude={"expected_fingerprint"}))


class ModuleExamDetailsChangeOut(_Out):
    dry_run: bool
    changed: bool = Field(description="False when the module already had these values (nothing saved)")
    changed_fields: list[str]
    module: ModuleOut
    draft_timetables: int = Field(description="Saved DRAFT timetables that use this module (stale after a change)")
    notices: list[str]


# --- Timetable generation (not saved) -------------------------------------------------

ModuleCode = Annotated[str, Field(pattern=MODULE_CODE)]


class GenerationCalendarIn(_In):
    """Dates, daily hours, weekends and excluded dates for one generation; the saved period is not changed."""

    start_date: PeriodDate
    end_date: PeriodDate
    day_start_time: PeriodTime
    day_end_time: PeriodTime
    include_saturdays: bool
    include_sundays: bool
    exclusions: list[ExamPeriodExclusionIn] = Field(max_length=MAX_EXCLUSIONS, description="Non-exam dates inside the range")

    def to_calendar(self) -> ExamCalendar:
        return ExamCalendar(start_date=self.start_date, end_date=self.end_date, day_start_time=self.day_start_time,
                            day_end_time=self.day_end_time, include_saturdays=self.include_saturdays,
                            include_sundays=self.include_sundays,
                            exclusions=tuple((e.excluded_date, e.reason) for e in self.exclusions))


class GenerateTimetableIn(_In):
    include_analysis: bool = Field(False, description="Generate only: optional bounded aggregate preparation-gap analysis. Never saved to a timetable.")
    scheduling_preferences: dict[str, Any] | None = Field(
        None, description="Optional per-timetable overrides. Omit for legacy active-default behavior; {} freezes the current defaults. Never changes institution settings.")

    @field_validator("scheduling_preferences")
    @classmethod
    def _preferences(cls, value: dict | None) -> dict | None:
        return None if value is None else validate_preferences(value)

    period_code: str = Field(pattern=PERIOD_CODE, description="Exam period, e.g. PP1-2026-S2-FINAL")
    calendar: GenerationCalendarIn | None = Field(
        None, description="Optional exam calendar for this generation (validated like a period). Omit to use the "
                          "saved period's calendar. Saved drafts keep it, and approval re-checks against it.")
    module_codes: list[ModuleCode] | None = Field(
        None, min_length=1, max_length=MAX_MODULES,
        description="Optional. Omit to schedule the full examination cycle (the period's configured module "
                    "mapping). List modules for a partial run: they must all be in the cycle. A period without "
                    "a mapping accepts listed modules of its own semester only.")
    mode: Literal["feasibility", "optimized"] = Field(
        "feasibility", description="feasibility: H1-H11 only. optimized: also S1-S5 and the configured fairness.")
    time_limit_seconds: float | None = Field(
        None, gt=0, le=MAX_API_TIME_LIMIT_SECONDS,
        description="Optional; may only lower the saved solver time limit")

    @field_validator("module_codes")
    @classmethod
    def _unique(cls, codes: list[str] | None) -> list[str] | None:
        if codes is not None and len(set(codes)) != len(codes):
            raise ValueError("module_codes must not contain duplicates")
        return codes


class GeneratedRoomOut(_Out):
    venue_id: str
    venue_name: str
    venue_type: str
    seats: int = Field(description="Logical seats allocated in this room")
    exam_capacity: int = Field(description="Effective exam capacity of the room")


class GeneratedSessionOut(_Out):
    session_id: str
    module_code: str
    module_name: str
    exam_type: str
    candidate_scope: Literal["all", "repeat_only"] = Field(
        description="From the cycle mapping: all (normal and repeat candidates) or repeat_only. "
                    "all when the period has no cycle mapping")
    academic_year: int
    semester: int
    delivery_mode: Literal["regular", "block"]
    session_number: int = Field(description="Session N of this module, in time order (1 when not split)")
    session_count: int = Field(description="Sessions of this module: more than 1 only when one sitting cannot "
                                           "seat every candidate")
    session_label: str = Field(description='e.g. "Session 01"')
    date: dt.date
    start_time: dt.time
    end_time: dt.time
    start: dt.datetime = Field(description="Local calendar time of the exam period (no time zone)")
    end: dt.datetime
    duration_minutes: int
    reading_time_minutes: int
    candidates: int
    normal_candidates: int = Field(description="Candidates seated in this session with a normal registration")
    repeat_candidates: int = Field(description="Candidates seated in this session with a repeat registration")
    rooms: list[GeneratedRoomOut]


class GenerationValidationOut(_Out):
    passed: bool | None = Field(description="null when the solver returned no timetable to validate")
    rules_checked: list[str]
    violations: list[SettingsProblemOut]


class GenerationSummaryOut(_Out):
    sessions: int
    exam_days_used: int
    room_allocations: int
    seat_allocations: int
    multi_room_exams: int
    multi_session_modules: int = Field(description="Modules split into several sessions for capacity")


class PenaltyOut(_Out):
    constraint: str
    weight: int
    penalty_points: float
    weighted_points: float


class WorkloadMetricsOut(_Out):
    """Estimated S1-S5 workload scores (prototype; not a validated stress measure)."""

    objective_units: int
    total_weighted_penalty: float
    penalties: list[PenaltyOut]
    same_day_student_pairs: int
    student_days_with_multiple_exams: int
    short_gap_student_pairs: int
    difficult_pairs_below_target: int
    minimum_gap_minutes: int | None
    median_gap_minutes: float | None
    lower_decile_gap_minutes: int | None
    maximum_window_exams: int
    maximum_window_load_points: float
    worst_student_weighted_penalty: float
    repeat_student_mean_weighted_penalty: float | None


class FairnessOut(_Out):
    protection_applied: bool
    limits_satisfied: bool | None
    students: int
    students_improved: int
    students_worsened: int
    students_unchanged: int
    baseline_worst_normalized_penalty: float
    selected_worst_normalized_penalty: float
    maximum_individual_increase_points: float
    worst_student_increase_points: float


class OptimizationOut(_Out):
    status: str | None
    improved: bool
    baseline: WorkloadMetricsOut | None
    selected: WorkloadMetricsOut | None
    objective_bound_units: float | None
    relative_gap: float | None
    fairness: FairnessOut | None


class GenerationInputsOut(_Out):
    batch: dict[str, str] | None = Field(None, description="Both-Semesters generation context; informational, never an approval or reservation.")
    cross_timetable: CrossTimetableOut | None = Field(
        None, description="APPROVED / PUBLISHED timetables of other cycles that were applied as fixed constraints "
                          "(null for timetables saved before cross-cycle checks, and for offline runs).")
    scheduling_preferences: dict[str, Any] | None = Field(
        None, description="Versioned complete effective settings and seating snapshot; null for legacy timetables.")
    period_code: str
    period_name: str
    exam_kind: ExamKind
    calendar_source: Literal["period", "selected"] = Field(
        "period", description="period: the saved period's calendar; selected: the calendar sent with the request")
    start_date: dt.date
    end_date: dt.date
    day_start_time: dt.time | None = Field(None, description="Daily exam hours used (null in older saved timetables)")
    day_end_time: dt.time | None = None
    include_saturdays: bool | None = None
    include_sundays: bool | None = None
    exclusions: list[ExamPeriodExclusionOut] = Field(default_factory=list, description="Excluded dates used")
    exam_days: list[dt.date]
    selection: Literal["full_cycle", "partial_cycle", "listed_modules"] = Field(
        description="full_cycle / partial_cycle: from the period's module mapping; listed_modules: no mapping")
    modules: int
    repeat_only_modules: int = Field(description="Cycle modules examined only for repeat candidates")
    students: int = Field(description="Students registered for at least one selected module")
    registrations: int
    repeat_registrations: int
    venues: int
    input_fingerprint: str
    capacity_ruleset_version: int
    capacity_fingerprint: str
    settings_version: int = Field(description="Scheduling settings version used (0 = built-in defaults)")
    settings_fingerprint: str


class SolverRunOut(_Out):
    mode: Literal["feasibility", "optimized"]
    time_limit_seconds: float
    num_search_workers: int
    random_seed: int
    wall_time_seconds: float
    solver_version: str | None


class ExcludedModuleOut(_Out):
    module_code: str
    module_name: str
    academic_year: int
    semester: int
    normal_registrations: int
    repeat_registrations: int
    other_cycles: list[str] = Field(description="Other examination cycles (periods) whose mapping includes this module")


class ExcludedRegistrationsOut(_Out):
    """Counts only; no student identifiers."""

    note: str
    total: int
    normal: int
    repeat: int
    modules: list[ExcludedModuleOut]


class GeneratedTimetableOut(_Out):
    analysis: EvaluationAnalysisOut | None = None
    status: Literal["OPTIMAL", "FEASIBLE", "INFEASIBLE", "UNKNOWN", "MODEL_INVALID"]
    generated: bool = Field(description="True only when a validated timetable is returned in sessions")
    persisted: Literal[False] = Field(False, description="Generated timetables are not saved yet")
    solver: SolverRunOut
    inputs: GenerationInputsOut
    validation: GenerationValidationOut
    summary: GenerationSummaryOut
    sessions: list[GeneratedSessionOut]
    workload: WorkloadMetricsOut | None = Field(description="S1-S5 scores of the returned timetable")
    optimization: OptimizationOut | None = Field(description="Only for mode=optimized")
    violations: list[SettingsProblemOut] = Field(description="Problems reported by the solver run")
    diagnostics: list[str]
    notices: list[str]
    excluded_registrations: ExcludedRegistrationsOut = Field(
        description="Potentially excluded registrations: their module is not in this cycle's mapping, so they were "
                    "not scheduled. Some may belong to another cycle. Empty for periods without a mapping")


# --- Saved timetables (drafts) ------------------------------------------------------------------------------

TimetableStatus = Literal["DRAFT", "APPROVED", "PUBLISHED", "ARCHIVED"]


class SavedTimetableSummaryOut(_Out):
    """One saved timetable without sessions or student data (list rows)."""

    timetable_id: int
    period_code: str
    period_name: str
    exam_kind: ExamKind
    draft_number: int = Field(description="1, 2, 3 ... per examination cycle; older drafts are never overwritten")
    status: TimetableStatus = Field(description="DRAFT for every saved timetable until Approve / Publish exist")
    revision: int
    mode: Literal["feasibility", "optimized"]
    selection: Literal["full_cycle", "partial_cycle", "listed_modules"]
    solver_status: Literal["OPTIMAL", "FEASIBLE"]
    solver_version: str | None
    input_fingerprint: str
    settings_version: int = Field(description="Scheduling settings version used (0 = built-in defaults)")
    settings_fingerprint: str
    seating_ruleset_version: int
    capacity_fingerprint: str
    validation_passed: bool
    summary: GenerationSummaryOut
    created_by: UUID
    created_at: dt.datetime
    approved_by: UUID | None
    approved_at: dt.datetime | None
    published_by: UUID | None
    published_at: dt.datetime | None
    content_fingerprint: str | None = Field(
        None, description="SHA-256 of the saved sessions, rooms and seats; null if saved before migration 0011")
    source_timetable_id: int | None = Field(None, description="The timetable this draft was regenerated from")


class SavedTimetableOut(SavedTimetableSummaryOut):
    """A saved timetable with its sessions and rooms (seat counts only, no student IDs)."""

    module_codes: list[str] | None = Field(description="Modules requested; null = the full configured cycle")
    solver: SolverRunOut
    inputs: GenerationInputsOut
    validation: GenerationValidationOut
    sessions: list[GeneratedSessionOut]
    workload: WorkloadMetricsOut | None
    optimization: OptimizationOut | None
    excluded_registrations: ExcludedRegistrationsOut
    notices: list[str]


WorkflowReason = Annotated[str, Field(min_length=REASON_MIN, max_length=REASON_MAX, pattern=REASON_PATTERN,
                                      description="Why (kept in the timetable history)")]


class ApproveTimetableIn(_In):
    expected_revision: Version = Field(description="The timetable revision you last read (409 if it changed)")
    reason: WorkflowReason


class PublishTimetableIn(ApproveTimetableIn):
    replace_published: bool = Field(
        False, description="Archive the cycle's currently published timetable and publish this one instead")


class RegenerateTimetableIn(_In):
    mode: Literal["feasibility", "optimized"] | None = Field(
        None, description="Optional; defaults to the mode of the timetable being regenerated")
    time_limit_seconds: float | None = Field(
        None, gt=0, le=MAX_API_TIME_LIMIT_SECONDS,
        description="Optional; may only lower the saved solver time limit")


class TimetableHistoryOut(_Out):
    history_id: int
    action: Literal["create", "approve", "publish", "archive"]
    from_status: TimetableStatus | None
    to_status: TimetableStatus
    revision: int = Field(description="Timetable revision after the change")
    changed_by: UUID | None
    changed_at: dt.datetime
    reason: str
    details: dict[str, Any] = Field(description="What was checked (fingerprints, versions, rules)")


class TimetableStatusChangeOut(_Out):
    timetable: SavedTimetableSummaryOut
    history: TimetableHistoryOut
    archived_timetable: SavedTimetableSummaryOut | None = Field(
        description="The previously published timetable, archived by replace_published")
    checks: list[str] = Field(description="What was verified before the change")


class SavedSeatOut(_Out):
    session_id: str
    venue_id: str
    seat_number: int = Field(description="Logical seat, not a physical room layout")
    student_id: str
    registration_type: Literal["normal", "repeat"]


class PageOut(_Out, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


class HealthOut(BaseModel):
    status: str
    service: str


class DatabaseHealthOut(BaseModel):
    status: str
    latency_ms: float | None
    error: str | None
