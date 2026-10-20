import "server-only";
import { readFile } from "node:fs/promises";
import type { ApiResult } from "@/lib/service-api";
import type { Stream, TimetableSession, TimetableSummary, TimetableValidation, WorkloadReport } from "@/lib/teaching";

// The Historical Experimental Preview: saved, independently validated timetable versions, shown read-only in the
// teaching pages when TEACHING_HISTORICAL_PREVIEW_FILE names their export (development only, never production).
// The export was rendered by teaching-service's own read projections for each version's OWN institution, so it is
// never presented as the signed-in institution's data and no service is ever asked for it.

/** The `source` query value that selects the preview, and the sentinel passed instead of an institution ID. */
export const HISTORICAL_PREVIEW = "historical-preview";
export const HISTORICAL_PREVIEW_LABEL = "Historical Experimental Preview";

export type PreviewSource = {
  institution_id: string;
  institution_code: string;
  note: string;
  kind: "STORED_HISTORICAL_VERSION" | "LOCAL_RUN";
  run: string;
  run_completed: string;
  settings: string;
};

export type IncompleteScope = { academic_year: number; semester: number; stream: Stream; specialization: string; scope_status: string; student_count: number | null };

type PreviewVersion = {
  source: PreviewSource;
  summary: TimetableSummary;
  validation: TimetableValidation;
  sessions: TimetableSession[];
  group_sessions: Record<string, string[]>;
  workload: WorkloadReport;
  incomplete_scopes: IncompleteScope[];
};

type PreviewFile = { label: string; versions: PreviewVersion[] };

const disabled = { ok: false as const, error: { status: 404, code: "PREVIEW_DISABLED", message: "The historical experimental preview is not enabled." } };
const missing = { ok: false as const, error: { status: 404, code: "NOT_FOUND", message: "The requested preview version was not found." } };

export const historicalPreviewEnabled = () => process.env.NODE_ENV !== "production" && Boolean(process.env.TEACHING_HISTORICAL_PREVIEW_FILE);

/** True only when the page asked for the preview and it is enabled; anything else is the institution's own data. */
export const isHistoricalPreview = (source: string | string[] | undefined) => source === HISTORICAL_PREVIEW && historicalPreviewEnabled();

let cached: Promise<PreviewFile> | undefined;

async function load(): Promise<ApiResult<PreviewFile>> {
  if (!historicalPreviewEnabled()) return disabled;
  cached ??= readFile(process.env.TEACHING_HISTORICAL_PREVIEW_FILE!, "utf8").then((text) => JSON.parse(text) as PreviewFile);
  try {
    const data = await cached;
    const valid = data.label === HISTORICAL_PREVIEW_LABEL && data.versions.length > 0 &&
      data.versions.every((row) => row.validation.status === "PASSED" && row.validation.hard_violations === 0);
    if (!valid) {
      return { ok: false, error: { status: 409, code: "PREVIEW_INVALID", message: "The historical preview export is not made of independently validated versions." } };
    }
    return { ok: true, data };
  } catch {
    cached = undefined;
    return { ok: false, error: { status: 503, code: "PREVIEW_UNAVAILABLE", message: "The historical preview export could not be read." } };
  }
}

async function version(versionId: string): Promise<ApiResult<PreviewVersion>> {
  const result = await load();
  if (!result.ok) return result;
  const found = result.data.versions.find((row) => row.summary.version_id === versionId);
  return found ? { ok: true, data: found } : missing;
}

/** Where a preview version came from, and its scopes that were not scheduled (for the notice on every page). */
export async function previewOrigin(versionId: string): Promise<{ source: PreviewSource; incomplete: IncompleteScope[]; researchPending: boolean } | null> {
  const result = await version(versionId);
  return result.ok
    ? { source: result.data.source, incomplete: result.data.incomplete_scopes, researchPending: result.data.validation.research_pending }
    : null;
}

export async function previewTimetables() {
  const result = await load();
  if (!result.ok) return result;
  const items = result.data.versions.map((row) => row.summary);
  return { ok: true as const, data: { items, total: items.length, page: 1, page_size: 100 } };
}

export async function previewValidation(versionId: string): Promise<ApiResult<TimetableValidation>> {
  const result = await version(versionId);
  return result.ok ? { ok: true, data: result.data.validation } : result;
}

export async function previewWorkload(versionId: string): Promise<ApiResult<WorkloadReport>> {
  const result = await version(versionId);
  return result.ok ? { ok: true, data: result.data.workload } : result;
}

export type StaffModule = { module_code: string; module_name: string; session_types: string[]; sessions: number; minutes: number };

export type PreviewStaffVersion = {
  source: PreviewSource;
  semesters: number[];
  versionId: string;
  workload: WorkloadReport["staff"][number];
  modules: StaffModule[];
};

/** Sessions a lecturer teaches in one list of sessions, grouped by module (combined deliveries count once). */
export function modulesTaught(sessions: TimetableSession[], staffNumber: string): StaffModule[] {
  const byModule = new Map<string, StaffModule>();
  for (const session of sessions) {
    if (session.lecturer.staff_id !== staffNumber) continue;
    const subject = session.coverage[0];
    if (!subject) continue;
    const entry = byModule.get(subject.module_code) ?? { module_code: subject.module_code, module_name: subject.module_name, session_types: [], sessions: 0, minutes: 0 };
    if (!entry.session_types.includes(subject.session_type)) entry.session_types.push(subject.session_type);
    entry.sessions += 1;
    entry.minutes += session.end_minute - session.start_minute;
    byModule.set(subject.module_code, entry);
  }
  return [...byModule.values()].sort((a, b) => a.module_code.localeCompare(b.module_code));
}

/**
 * The same person in each preview version: matched by staff number AND full name, because the preview belongs
 * to another (test) institution. Anything that does not match both is left out rather than guessed.
 */
export async function previewStaff(staffNumber: string, fullName: string): Promise<PreviewStaffVersion[]> {
  const result = await load();
  if (!result.ok) return [];
  const name = fullName.trim().toLocaleLowerCase();
  return result.data.versions.flatMap((version) => {
    const workload = version.workload.staff.find((row) => row.staff_id === staffNumber);
    if (!workload || workload.staff_name.trim().toLocaleLowerCase() !== name) return [];
    const semesters = [...new Set(version.summary.scopes.map((scope) => scope.semester))].sort();
    return [{ source: version.source, semesters, versionId: version.summary.version_id, workload, modules: modulesTaught(version.sessions, staffNumber) }];
  });
}

type Filters = { semester?: number; academic_year?: number; stream?: Stream; student_group?: string; lecturer?: string; venue?: string };

/** The service's scope filters: keep the matching coverage rows, and a session only while one remains. */
export async function previewSessions(versionId: string, filters: Filters): Promise<ApiResult<TimetableSession[]>> {
  const result = await version(versionId);
  if (!result.ok) return result;
  const { sessions, group_sessions } = result.data;
  // Group views use the service's own group selection (clashing parents and lab subgroups), exported per group.
  const inGroup = filters.student_group ? new Set(group_sessions[filters.student_group] ?? []) : undefined;
  const data = sessions.flatMap((session) => {
    if (inGroup && !inGroup.has(session.delivery_reference)) return [];
    if (filters.lecturer && session.lecturer.staff_id !== filters.lecturer) return [];
    if (filters.venue && session.venue?.venue_id !== filters.venue) return [];
    const coverage = session.coverage.filter(
      (row) =>
        (!filters.semester || row.semester === filters.semester) &&
        (!filters.academic_year || row.academic_year === filters.academic_year) &&
        (!filters.stream || row.stream === filters.stream),
    );
    return coverage.length ? [{ ...session, coverage }] : [];
  });
  return { ok: true, data };
}
