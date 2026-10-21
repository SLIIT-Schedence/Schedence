import "server-only";
import { cache } from "react";
import { examinationRequest } from "@/lib/examination-api";

// Read contracts from services/examination-service/app/api/schemas.py.
export type ExamPeriod = {
  period_code: string;
  period_name: string;
  academic_year_label: string;
  semester: number;
  exam_kind: "MID" | "FINAL";
  start_date: string;
  end_date: string;
  exam_days: string[];
  day_start_time: string;
  day_end_time: string;
  include_saturdays: boolean;
  include_sundays: boolean;
  exclusions: { excluded_date: string; reason: string }[];
  fingerprint: string | null;
};
export type ExamModule = {
  module_code: string; module_name: string; academic_year: number; semester: number;
  specializations: string[]; candidate_count: number; delivery_mode: "regular" | "block";
};
/** Additional fields returned by ModuleOut. Missing metadata stays unavailable in the UI. */
export type ExamModuleDetails = ExamModule & {
  credits?: number | null; difficulty?: number | null; exam_type?: string | null;
  duration_minutes?: number | null; reading_time_minutes?: number | null;
  has_extra_reading_time?: boolean | null; has_final_exam?: boolean | null; has_mid_exam?: boolean | null;
  mid_duration_minutes?: number | null; block_category?: string | null;
};
export type ExamVenue = {
  venue_id: string;
  exam_capacity: number;
  available_for_exams: boolean;
};
/** VenueOut metadata; unavailable fields are never replaced with capacity estimates. */
export type ExamVenueDetails = ExamVenue & {
  venue_name?: string | null; building?: string | null; floor?: number | null; venue_type?: string | null;
  normal_capacity?: number | null; exam_seating_percentage?: number | null;
  exam_seating_rule?: "default" | "venue" | null;
};
export type ExamVenueBlock = {
  venue_id: string; block_date: string; start_time: string; end_time: string;
  source: string; reason: string; source_ref: string | null;
};
export type ExamRegistration = { student_id: string; module_code: string; registration_type: string };
export type TimetableStatus = "DRAFT" | "APPROVED" | "PUBLISHED" | "ARCHIVED";
/** SavedTimetableSummaryOut: a list row, without sessions or student data. */
export type SavedTimetable = {
  timetable_id: number;
  period_code: string;
  period_name: string;
  exam_kind: "MID" | "FINAL";
  draft_number: number;
  status: TimetableStatus;
  revision: number;
  mode: "feasibility" | "optimized";
  selection: "full_cycle" | "partial_cycle" | "listed_modules";
  solver_status: "OPTIMAL" | "FEASIBLE";
  settings_version: number;
  seating_ruleset_version: number;
  validation_passed: boolean;
  summary: {
    sessions: number; exam_days_used: number; room_allocations: number; seat_allocations: number;
    multi_room_exams: number; multi_session_modules: number;
  };
  created_by: string;
  created_at: string;
  approved_by: string | null;
  approved_at: string | null;
  published_by: string | null;
  published_at: string | null;
  content_fingerprint: string | null;
  source_timetable_id: number | null;
};
/** SavedTimetableOut: sessions with rooms and seat counts. The API never includes student IDs here. */
export type SavedTimetableDetail = SavedTimetable & {
  module_codes: string[] | null;
  solver: { mode: "feasibility" | "optimized"; time_limit_seconds: number; wall_time_seconds: number };
  inputs: {
    scheduling_preferences?: import("@/lib/examination-settings").PreferencesSnapshot | null;
    period_code: string; period_name: string; exam_kind: "MID" | "FINAL"; start_date: string; end_date: string;
    exam_days: string[]; modules: number; repeat_only_modules: number; students: number; registrations: number;
    // The calendar used; absent in timetables saved before per-generation calendars.
    calendar_source?: "period" | "selected"; day_start_time?: string | null; day_end_time?: string | null;
    include_saturdays?: boolean | null; include_sundays?: boolean | null; exclusions?: { excluded_date: string; reason: string }[];
  };
  validation: { passed: boolean | null; rules_checked: string[]; violations: { rule: string; message: string }[] };
  sessions: {
    session_id: string; module_code: string; module_name: string; exam_type: string;
    candidate_scope: "all" | "repeat_only"; academic_year: number; semester: number; delivery_mode: "regular" | "block";
    session_number: number; session_count: number; date: string; start_time: string; end_time: string;
    candidates: number; normal_candidates: number; repeat_candidates: number;
    rooms: { venue_id: string; venue_name: string; venue_type: string; seats: number; exam_capacity: number }[];
  }[];
  workload: { total_weighted_penalty: number } | null;
  excluded_registrations: { note: string; total: number; normal: number; repeat: number };
  notices: string[];
};
export type TimetableHistoryEntry = {
  history_id: number;
  action: "create" | "approve" | "publish" | "archive";
  from_status: TimetableStatus | null;
  to_status: TimetableStatus;
  revision: number;
  changed_by: string | null;
  changed_at: string;
  reason: string;
};
export type Page<T> = { items: T[]; total: number; limit: number; offset: number };
export type TimetableListFilter = { period_code?: string; status?: TimetableStatus; limit: number; offset: number };

// React cache deduplicates these reads within a render, never between users or requests.
export const listExamPeriods = cache(() => examinationRequest<ExamPeriod[]>("/api/v1/exam-periods"));
export const listExamModules = cache(() => examinationRequest<ExamModuleDetails[]>("/api/v1/modules"));
export const getExamModule = cache((code: string) => examinationRequest<ExamModuleDetails>(
  `/api/v1/modules/${encodeURIComponent(code)}`,
  { sessionExpiredPath: `/login?error=SessionExpired&callbackUrl=${encodeURIComponent(`/exams/modules/${code}`)}` },
));
export const listExamVenues = cache(() => examinationRequest<ExamVenueDetails[]>("/api/v1/venues"));
export const listExamVenueBlocks = cache((venueId: string) => examinationRequest<ExamVenueBlock[]>(
  "/api/v1/venue-blocks", { query: { venue_id: venueId } },
));
/** Assignment inspection includes MID/FINAL and all statuses; versions are never combined. */
export const listVenueTimetables = cache(() => examinationRequest<Page<SavedTimetable>>(
  "/api/v1/timetables", { query: { limit: 100, offset: 0 } },
));
// Only the total is displayed; do not send registration records to a client component.
export const getExamRegistrationTotal = cache(async () => {
  const result = await examinationRequest<Page<ExamRegistration>>("/api/v1/registrations", { query: { limit: 1, offset: 0 } });
  return result.ok ? { ok: true as const, data: result.data.total } : result;
});
/** A period's cycle mapping: the modules examined in it, and which candidates sit them. */
export const listExamPeriodModules = cache((periodCode: string) =>
  examinationRequest<{ period_code: string; module_code: string; candidate_scope: "all" | "repeat_only" }[]>(
    `/api/v1/exam-periods/${encodeURIComponent(periodCode)}/modules`));
export const listRecentExamTimetables = cache(() => examinationRequest<Page<SavedTimetable>>("/api/v1/timetables", { query: { limit: 5, offset: 0 } }));

/** Saved FINAL timetables, newest first (examinations:read). */
export function listSavedTimetables({ period_code, status, limit, offset }: TimetableListFilter) {
  return examinationRequest<Page<SavedTimetable>>("/api/v1/timetables", {
    query: { exam_kind: "FINAL", period_code, status, limit, offset },
  });
}

const timetableLogin = (id: number) => `/login?error=SessionExpired&callbackUrl=${encodeURIComponent(`/exams/timetables/${id}`)}`;

export const getSavedTimetable = cache((id: number) =>
  examinationRequest<SavedTimetableDetail>(`/api/v1/timetables/${id}`, { sessionExpiredPath: timetableLogin(id) }));

export const getTimetableHistory = cache((id: number) =>
  examinationRequest<TimetableHistoryEntry[]>(`/api/v1/timetables/${id}/history`, { sessionExpiredPath: timetableLogin(id) }));
