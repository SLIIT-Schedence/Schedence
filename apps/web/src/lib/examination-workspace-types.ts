import type { ExamPeriod, ExamModule } from "@/lib/examination";
import type { ExaminationResult } from "@/lib/examination-api";

export type PeriodDefinition = ExamPeriod;
/** A mapped module. Normal/repeat counts are exact per module, or null when the split could not be read. */
export type AcademicModule = ExamModule & { normal_candidates: number | null; repeat_candidates: number | null };
export type CycleModule = { period_code: string; module_code: string; candidate_scope: "all" | "repeat_only" };
export type SchedulingReview = {
  source: "defaults" | "saved"; status: "valid" | "invalid"; version: number; fingerprint: string | null;
  mandatory_rules: string[]; problems: { rule: string; message: string }[];
  settings: Record<string, Record<string, unknown>>;
};
export type SeatingReview = {
  ruleset_version: number; default: { percentage: number }; venue_count: number;
  total_normal_capacity: number; total_exam_capacity: number; unavailable_venues: string[];
  venue_overrides: { venue_id: string | null; percentage: number }[];
};
export type CycleReview = {
  period: PeriodDefinition; mapping: CycleModule[]; modules: AcademicModule[];
  scheduling: SchedulingReview; seating: SeatingReview;
  draft_count: number | null; locked: boolean | null; warnings: string[];
};
export type ReviewResult = ExaminationResult<CycleReview>;
export type PeriodEdit = {
  expected_fingerprint: string; start_date: string; end_date: string;
  include_saturdays: boolean; include_sundays: boolean;
  exclusions: { excluded_date: string; reason: string }[];
};
export type PeriodChange = {
  dry_run: boolean; changed: boolean; changed_fields: string[]; period: PeriodDefinition;
  draft_timetables: number; notices: string[];
};
export type PeriodChangeResult = ExaminationResult<{ change: PeriodChange; preview_token?: string }>;
