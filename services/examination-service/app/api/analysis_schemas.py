"""Public aggregate evaluation contract. No student records or identifiers."""
from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict


class PublicAnalysisModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GapStatisticsOut(PublicAnalysisModel):
    students: int
    registrations: int
    normal_registrations: int
    repeat_registrations: int
    students_with_consecutive_exams: int
    consecutive_gap_count: int
    total_consecutive_gap_minutes: int
    mean_gap_minutes: float | None
    median_gap_minutes: float | None
    minimum_gap_minutes: int | None
    lower_decile_gap_minutes: int | None
    short_consecutive_gaps: int
    all_exam_student_pairs: int
    short_gap_student_pairs: int
    difficult_exam_student_pairs: int
    difficult_pairs_below_target: int
    same_day_student_pairs: int
    mean_weighted_workload: float | None


class AnalysisCohortOut(PublicAnalysisModel):
    academic_year: int | None
    specialization: str | None
    registration_group: Literal["all", "normal_only", "includes_repeat"]
    statistics: GapStatisticsOut


class AnalysisExamOut(PublicAnalysisModel):
    module_code: str
    session_number: int
    date: dt.date
    start_time: dt.time
    end_time: dt.time


class DifficultPairOut(PublicAnalysisModel):
    first: AnalysisExamOut
    second: AnalysisExamOut
    gap_minutes: int
    below_target: bool
    shared_candidates: int
    normal_only_candidates: int
    repeat_candidates: int


class AnalysisValidationOut(PublicAnalysisModel):
    passed: bool
    rules_checked: list[str]


class AnalysisPenaltyOut(PublicAnalysisModel):
    constraint: str
    weight: int
    penalty_points: float
    weighted_points: float


class AnalysisWorkloadOut(PublicAnalysisModel):
    objective_units: int
    total_weighted_penalty: float
    penalties: list[AnalysisPenaltyOut]


class AnalysisRunOut(PublicAnalysisModel):
    input_fingerprint: str
    configuration_fingerprint: str
    validation: AnalysisValidationOut
    workload: AnalysisWorkloadOut | None
    statistics: GapStatisticsOut
    cohorts: list[AnalysisCohortOut]
    cohorts_total: int
    cohorts_truncated: bool
    difficult_pairs: list[DifficultPairOut]
    difficult_pair_rows_total: int
    difficult_pair_rows_truncated: bool


class AnalysisModuleOut(PublicAnalysisModel):
    module_code: str
    module_name: str
    academic_year: int
    semester: int
    specializations: list[str]
    difficulty: int | None
    difficult: bool | None


class AnalysisPolicyOut(PublicAnalysisModel):
    high_difficulty_minimum: int
    short_gap_minutes: int
    preparation_target_minutes: int
    difficult_target_minutes: int
    scoring_version: str
    scoring_fingerprint: str


class AnalysisLimitsOut(PublicAnalysisModel):
    max_student_pairs: int
    max_pair_rows: int
    max_cohorts: int


class EvaluationAnalysisOut(PublicAnalysisModel):
    version: Literal["preparation-gap-v1"]
    input_fingerprint: str
    configuration_fingerprint: str
    policy: AnalysisPolicyOut
    modules: list[AnalysisModuleOut]
    limits: AnalysisLimitsOut
    cohort_membership_available: int
    baseline: AnalysisRunOut | None
    selected: AnalysisRunOut | None
    baseline_unavailable_reason: str | None
    selected_unavailable_reason: str | None
