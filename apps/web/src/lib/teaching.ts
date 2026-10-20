import "server-only";
import { type ApiError, type ApiResult, type RequestOptions, type Service, serviceRequest, serviceUrl } from "@/lib/service-api";

// Read side of the Teaching Timetables view. teaching-service verifies the caller's token and Identity grants,
// scopes every read to one institution and withholds unpublished drafts from lecturers and students, so the
// web app only passes the institution the Academic Admin selected.

export const TEACHING_SERVICE = "teaching timetable";

const TEACHING: Service = {
  baseUrl: () => serviceUrl("TEACHING_SERVICE_URL", "http://localhost:8004"),
  unreachable: "The teaching timetable service didn't respond.",
};

function teachingRequest<T>(path: string, options?: RequestOptions) {
  return serviceRequest<T>(TEACHING, `/api/v1/teaching${path}`, options);
}

// ------------------------------------------------------------------------------------------ records

export type Stream = "WEEKDAY" | "WEEKEND";

type Page<T> = { items: T[]; total: number; page: number; page_size: number };

export type TimetableScope = {
  academic_year: number;
  semester: number;
  stream: Stream;
  specialization: string;
  student_groups: string[];
  scope_status: string;
};

export type TimetableSummary = {
  institution_id: string;
  version_id: string;
  series_id: string;
  version_number: number;
  lifecycle_status: string;
  storage_status: string;
  solver_status: string;
  validation_status: string;
  academic_complete: boolean;
  matched_occurrences: number;
  scopes: TimetableScope[];
  calendar: {
    label: string;
    week_reference: string;
    week_start: string;
    week_end: string;
    timezone: string;
    synthetic_data: boolean;
    teaching_windows: { day: string; teaching_date: string; stream: Stream; start_minute: number; end_minute: number; delivery_mode: string }[];
  };
  institution_policy_revision?: string;
  full_scope_expected_occurrences?: number;
  /** Present only on local development previews of unpublished experimental drafts. */
  preview_label?: string;
};

export type Coverage = {
  occurrence_id: string;
  module_code: string;
  module_name: string;
  group_id: string;
  session_type: string;
  duration_minutes: number;
  academic_year: number;
  semester: number;
  stream: Stream;
  specialization: string;
};

export type TimetableSession = {
  delivery_reference: string;
  teaching_date: string;
  day: string;
  start_minute: number;
  end_minute: number;
  start_time: string;
  end_time: string;
  delivery_mode: string;
  lecturer: { staff_id: string; staff_name: string };
  venue: { venue_id: string; venue_name: string; venue_type: string; capacity: number; building: string | null; floor: number | string | null } | null;
  coverage: Coverage[];
};

export type OptimizationSummary = {
  plan_reference: string;
  priority_order: string[];
  stages: { criterion: string; status: string; objective_value: number | null }[];
  proven_stage_count: number;
  all_stages_proven_optimal: boolean;
};

export type TimetableValidation = {
  version_id: string;
  status: string;
  expected_occurrences: number;
  covered_occurrences: number;
  actual_deliveries: number;
  unscheduled_occurrences: number;
  hard_violations: number;
  academic_complete: boolean;
  unresolved_issues: { code: string; severity: string; scope_key: string | null }[];
  blocked_scopes: { academic_year: number; semester: number; stream: string; specialization: string; student_count: number | null }[];
  research_pending: boolean;
  experimental_calendar: boolean;
  optimization?: OptimizationSummary;
};

type Filters = {
  academic_year?: number;
  semester?: number;
  stream?: Stream;
  specialization?: string;
  student_group?: string;
  lecturer?: string;
  venue?: string;
};

// --------------------------------------------------------------------------------------------- reads

export function listTimetables(institutionId: string) {
  return teachingRequest<Page<TimetableSummary>>("/timetables", { query: { institution_id: institutionId, page_size: 100 } });
}

export function getValidation(institutionId: string, versionId: string) {
  return teachingRequest<TimetableValidation>(`/timetables/${encodeURIComponent(versionId)}/validation`, {
    query: { institution_id: institutionId },
  });
}

/** The API pages sessions 100 at a time; a semester has a few hundred, so this reads at most 20 pages. */
const MAX_SESSION_PAGES = 20;

export async function allSessions(institutionId: string, versionId: string, filters: Filters = {}): Promise<ApiResult<TimetableSession[]>> {
  const items: TimetableSession[] = [];
  for (let page = 1; page <= MAX_SESSION_PAGES; page++) {
    const result = await teachingRequest<Page<TimetableSession>>(`/timetables/${encodeURIComponent(versionId)}/sessions`, {
      query: { institution_id: institutionId, ...filters, page, page_size: 100 },
    });
    if (!result.ok) return result;
    items.push(...result.data.items);
    if (items.length >= result.data.total || result.data.items.length === 0) return { ok: true, data: items };
  }
  return { ok: false, error: { status: 0, code: "TOO_MANY_SESSIONS", message: "This timetable has more sessions than the viewer reads." } };
}

// ------------------------------------------------------------------------------------------ the view

export type ViewMode = "group" | "lecturer" | "venue";

export type ViewParams = {
  semester?: string;
  version?: string;
  year?: string;
  stream?: string;
  specialization?: string;
  group?: string;
  view?: string;
  lecturer?: string;
  venue?: string;
};

export type Option = { value: string; label: string };

export type TimetableView = {
  versions: TimetableSummary[];
  semesters: number[];
  semester: number;
  semesterVersions: TimetableSummary[];
  version: TimetableSummary;
  validation: ApiResult<TimetableValidation>;
  years: number[];
  year: number;
  streams: Stream[];
  stream: Stream;
  specializations: string[];
  specialization: string;
  groups: Option[];
  group: string;
  view: ViewMode;
  lecturers: Option[];
  lecturer: string;
  venues: Option[];
  venue: string;
  sessions: TimetableSession[];
};

export type ViewResult = { status: "empty" } | { status: "error"; error: ApiError } | { status: "ok"; view: TimetableView };

const unique = <T,>(values: T[]) => [...new Set(values)];
const integer = (value: string | undefined) => (value && /^\d{1,2}$/.test(value) ? Number(value) : undefined);
const pick = <T,>(wanted: T | undefined, options: T[]) => (wanted !== undefined && options.includes(wanted) ? wanted : options[0]);

/** "PLAN-…%3AACADEMIC-TRACK%3AY1S2%3AWEEKDAY%3ACOMMON_FOUNDATION-G001.L01" → "Common foundation G001 · lab L01". */
export function groupLabel(groupId: string): string {
  let text = groupId;
  try {
    text = decodeURIComponent(groupId);
  } catch {
    // Keep the raw identifier.
  }
  const tail = text.split(":").pop() ?? text;
  const match = /^(.*)-(G\d+)(?:\.(L\d+))?$/.exec(tail);
  if (!match) return tail;
  const track = match[1].replaceAll("_", " ").toLowerCase();
  return `${track.charAt(0).toUpperCase()}${track.slice(1)} ${match[2]}${match[3] ? ` · lab ${match[3]}` : ""}`;
}

/**
 * Everything the viewer shows, from authorized reads only: the institution's validated versions, the chosen
 * semester/version/year/stream/group, and the matching sessions. Unknown or out-of-range selections fall back
 * to the first available value, so a URL can never request a scope the version does not contain.
 */
export type VersionChoice = {
  versions: TimetableSummary[];
  semesters: number[];
  semester: number;
  semesterVersions: TimetableSummary[];
  version: TimetableSummary;
};

export type ChoiceResult = { status: "empty" } | { status: "error"; error: ApiError } | { status: "ok"; choice: VersionChoice };

/** The institution's independently validated versions, and the chosen semester and version (first by default). */
export async function chooseVersion(institutionId: string, params: { semester?: string; version?: string }): Promise<ChoiceResult> {
  const listing = await listTimetables(institutionId);
  if (!listing.ok) return { status: "error", error: listing.error };
  const versions = listing.data.items.filter((item) => item.validation_status === "PASSED" && item.scopes.length > 0);
  if (versions.length === 0) return { status: "empty" };
  const semesters = unique(versions.flatMap((item) => item.scopes.map((scope) => scope.semester))).sort();
  const semester = pick(integer(params.semester), semesters);
  // Within a semester, the version covering the most academic years comes first.
  const coverage = (item: TimetableSummary) => new Set(item.scopes.filter((scope) => scope.semester === semester).map((scope) => scope.academic_year)).size;
  const semesterVersions = versions.filter((item) => coverage(item) > 0).sort((a, b) => coverage(b) - coverage(a));
  const version = semesterVersions.find((item) => item.version_id === params.version) ?? semesterVersions[0];
  return { status: "ok", choice: { versions, semesters, semester, semesterVersions, version } };
}

export async function loadTimetableView(institutionId: string, params: ViewParams): Promise<ViewResult> {
  const chosen = await chooseVersion(institutionId, params);
  if (chosen.status !== "ok") return chosen;
  const { versions, semesters, semester, semesterVersions, version } = chosen.choice;

  const scopes = version.scopes.filter((scope) => scope.semester === semester);
  const years = unique(scopes.map((scope) => scope.academic_year)).sort();
  const year = pick(integer(params.year), years);
  const streams = unique(scopes.filter((scope) => scope.academic_year === year).map((scope) => scope.stream)).sort() as Stream[];
  const stream = pick(params.stream as Stream | undefined, streams);
  const yearScopes = scopes.filter((scope) => scope.academic_year === year && scope.stream === stream);
  const specializations = unique(yearScopes.map((scope) => scope.specialization)).sort();
  const specialization = pick(params.specialization, specializations);
  const groupIds = unique(yearScopes.filter((scope) => scope.specialization === specialization).flatMap((scope) => scope.student_groups)).sort();
  const groups = groupIds.map((id) => ({ value: id, label: groupLabel(id) }));
  const group = pick(params.group, groupIds) ?? "";
  const view: ViewMode = params.view === "lecturer" || params.view === "venue" ? params.view : "group";

  const [validation, semesterSessions] = await Promise.all([
    getValidation(institutionId, version.version_id),
    allSessions(institutionId, version.version_id, { semester }),
  ]);
  if (!semesterSessions.ok) return { status: "error", error: semesterSessions.error };

  const lecturers = uniqueOptions(semesterSessions.data.map((row) => ({ value: row.lecturer.staff_id, label: row.lecturer.staff_name })));
  const venues = uniqueOptions(
    semesterSessions.data.flatMap((row) => (row.venue ? [{ value: row.venue.venue_id, label: row.venue.venue_name }] : [])),
  );
  const lecturer = pick(params.lecturer, lecturers.map((row) => row.value)) ?? "";
  const venue = pick(params.venue, venues.map((row) => row.value)) ?? "";

  let sessions: TimetableSession[];
  if (view === "group") {
    // The service's group filter includes every clashing parent and lab subgroup, as the solver does.
    const result = group
      ? await allSessions(institutionId, version.version_id, { semester, academic_year: year, stream, student_group: group })
      : { ok: true as const, data: [] };
    if (!result.ok) return { status: "error", error: result.error };
    sessions = result.data;
  } else if (view === "lecturer") {
    sessions = semesterSessions.data.filter((row) => row.lecturer.staff_id === lecturer);
  } else {
    sessions = semesterSessions.data.filter((row) => row.venue?.venue_id === venue);
  }

  return {
    status: "ok",
    view: {
      versions,
      semesters,
      semester,
      semesterVersions,
      version,
      validation,
      years,
      year,
      streams,
      stream,
      specializations,
      specialization,
      groups,
      group,
      view,
      lecturers,
      lecturer,
      venues,
      venue,
      sessions: sessions.toSorted((a, b) => a.teaching_date.localeCompare(b.teaching_date) || a.start_minute - b.start_minute),
    },
  };
}

function uniqueOptions(options: Option[]): Option[] {
  const seen = new Map<string, string>();
  for (const option of options) if (!seen.has(option.value)) seen.set(option.value, option.label);
  return [...seen].map(([value, label]) => ({ value, label })).sort((a, b) => a.label.localeCompare(b.label));
}

export const SESSION_TYPES: Record<string, string> = { LECTURE: "Lecture", TUTORIAL: "Tutorial", LAB: "Lab", WORKSHOP: "Workshop" };

export const STAGES: Record<string, string> = {
  DELIVERIES: "Deliveries",
  CONTACT_MINUTES: "Contact minutes",
  MAXIMUM_OVER_TARGET_MINUTES: "Peak workload over target",
  TOTAL_OVER_TARGET_MINUTES: "Total workload over target",
  MAXIMUM_TARGET_DEVIATION: "Largest target deviation",
  MAX_STUDENT_DAILY_MINUTES: "Longest student day",
  MAX_STAFF_DAILY_MINUTES: "Longest staff day",
  THIN_TEACHING_DAYS: "Thin teaching days",
  CONSECUTIVE_PENALTY: "Consecutive teaching",
  PREFERENCE_PENALTY: "Lecturer preferences",
  AGGREGATE_TARGET_DEVIATION: "Overall workload fairness",
};

// ------------------------------------------------------------------------------- lecturer workload report

/** An exact ratio as the service reports it (numerator/denominator), with its decimal value. */
export type Ratio = { numerator: number; denominator: number; value: number };

export type WorkloadStaff = {
  staff_id: string;
  staff_name: string;
  staff_type: "ACADEMIC" | "INSTRUCTOR" | string;
  source_position: string | null;
  academic_rank: string | null;
  appointment: string | null;
  policy_reference: string | null;
  rule_source: string | null;
  lecture_hours: number;
  tutorial_hours: number;
  workshop_hours: number;
  lab_hours: number;
  assigned_teaching_hours: number;
  online_hours: number;
  physical_hours: number;
  weekday_hours: number;
  weekend_hours: number;
  assigned_delivery_count: number;
  assigned_module_offering_count: number;
  teaching_day_count: number;
  teaching_dates: string[];
  maximum_daily_teaching_minutes: number;
  longest_continuous_teaching_minutes: number;
  weekly_teaching_target_hours: number | null;
  difference_from_target_hours: number | null;
  target_status: "BELOW_TARGET" | "MET" | "EXCEEDED" | "NOT_VERIFIED" | null;
  target_utilization: Ratio | null;
  weekly_teaching_cap_hours: number | null;
  daily_teaching_cap_minutes: number | null;
  administrative_allocation_hours: number | null;
  timed_reserved_admin_hours: number | null;
  timed_reserved_research_hours: number | null;
  hard_cap_violated: boolean;
  weekly_teaching_day_limit: number | null;
  teaching_day_limit_violated: boolean;
  current_policy_status: string;
};

export type FairnessComparison = {
  academic_rank: string | null;
  appointment: string | null;
  staff_count: number;
  mean_utilization: Ratio | null;
  variance: Ratio | null;
  mean_absolute_deviation: Ratio | null;
  maximum_deviation: Ratio | null;
  maximum_relative_deviation: Ratio | null;
  interpretation: string;
};

export type StoredFairness = {
  enabled: boolean;
  objective_status: string;
  all_stages_proven_optimal: boolean;
  priority_order: string[];
  stages: { criterion: string; objective_value: number | null; status: string }[];
  strategy: string;
  university_ready: boolean;
  priority_is_development_configuration: boolean;
};

export type WorkloadReport = {
  version_id: string;
  logical_status: string;
  validation_passed: boolean;
  hard_violations: number;
  academic_complete: boolean;
  week_start: string;
  week_end: string;
  institution_policy_revision: string | null;
  current_teaching_policy: { policy_reference: string; current_policy_status: string; teaching_day_limit_violations: number; unresolved_profile_count: number };
  limitations: { research_pending: boolean; experimental_calendar: boolean; testing_inputs: boolean; academic_complete: boolean };
  staff: WorkloadStaff[];
  totals: {
    staff_count: number;
    academic_staff_count: number;
    instructor_count: number;
    zero_assigned_count: number;
    assigned_teaching_hours: number;
    staff_with_hard_cap_violations: number;
    unique_actual_deliveries: number;
  };
  fairness: {
    descriptive_comparisons: FairnessComparison[];
    source_policy_assessment_available: boolean;
    stored_assessment: StoredFairness | null;
    missing_target_staff_ids: string[];
  };
  notes: string[];
};

/** The service's lecturer workload report for one validated version (institution-wide Teaching read access only). */
export function getWorkloadReport(institutionId: string, versionId: string, filters: { semester?: number } = {}) {
  return teachingRequest<WorkloadReport>("/reports/lecturer-workload", {
    query: { institution_id: institutionId, version_id: versionId, ...filters },
  });
}

export const RANKS: Record<string, string> = {
  INSTRUCTOR: "Instructor",
  ASSISTANT_LECTURER: "Assistant Lecturer",
  LECTURER: "Lecturer",
  SENIOR_LECTURER: "Senior Lecturer",
  DR: "Doctorate holder",
  ASSISTANT_PROFESSOR: "Assistant Professor",
  PROFESSOR: "Professor",
};

export const rankLabel = (rank: string | null) => (rank ? (RANKS[rank] ?? rank.replaceAll("_", " ").toLowerCase()) : "Rank not recorded");
