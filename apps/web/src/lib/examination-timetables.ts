import type { Tone } from "@/lib/audit-events";
import type { ExamModule, ExamPeriod, SavedTimetable, SavedTimetableDetail, TimetableStatus } from "@/lib/examination";
import type { GenerationSession } from "@/lib/examination-generation-types";

// Pure helpers for saved FINAL timetables. Safe on the server and in tests; no requests, no student data.

export const TIMETABLE_STATUSES: TimetableStatus[] = ["DRAFT", "APPROVED", "PUBLISHED", "ARCHIVED"];
export const TIMETABLE_PAGE_SIZE = 25;
/** The API's largest page; a merged semester listing never asks for more. */
const MAX_API_PAGE = 500;

/** Backend messages can quote a student identifier; never show one in general views. */
export const redactStudentIds = (value: string) => value.replace(/[A-Z]{2}[0-9]{8}/gi, "[student identifier omitted]");

export const STATUS_LABELS: Record<TimetableStatus, string> = {
  DRAFT: "Draft", APPROVED: "Approved", PUBLISHED: "Published", ARCHIVED: "Archived",
};
export const STATUS_TONES: Record<TimetableStatus, Tone> = {
  DRAFT: "neutral", APPROVED: "primary", PUBLISHED: "accent", ARCHIVED: "neutral",
};

// --- Workflow reason (backend: 5-500 printable characters, no control characters) ---------------

export const REASON_MIN = 5;
export const REASON_MAX = 500;

/** Line breaks and repeated spaces become single spaces: the backend accepts one line only. */
export const normalizeReason = (raw: string) => raw.replace(/\s+/g, " ").trim();

export function reasonError(reason: string): string | null {
  if (reason.length < REASON_MIN || reason.length > REASON_MAX) {
    return `Enter a reason of ${REASON_MIN}–${REASON_MAX} characters. It is kept in the timetable history.`;
  }
  if (/[\x00-\x1f\x7f]/.test(reason)) return "Remove tabs and other control characters from the reason.";
  return null;
}

// --- Saved timetable list filters --------------------------------------------------------------

export type TimetableFilters = { semester?: 1 | 2; period?: string; status?: TimetableStatus; page: number };
export type FinalPeriodOption = Pick<ExamPeriod, "period_code" | "period_name" | "semester" | "start_date">;

const one = (value: string | string[] | undefined) => (Array.isArray(value) ? value[0] : value);

/** Only known values are kept: an unknown cycle, status or semester is ignored, never sent to the API. */
export function readTimetableFilters(params: Record<string, string | string[] | undefined>, periods: FinalPeriodOption[]): TimetableFilters {
  const semesterText = one(params.semester);
  const semester = semesterText === "1" ? 1 : semesterText === "2" ? 2 : undefined;
  const periodText = one(params.period);
  const period = periods.find((item) => item.period_code === periodText && (!semester || item.semester === semester))?.period_code;
  const statusText = one(params.status);
  const status = TIMETABLE_STATUSES.find((item) => item === statusText);
  const pageValue = Number.parseInt(one(params.page) ?? "1", 10);
  const page = Number.isFinite(pageValue) && pageValue >= 1 && pageValue <= 1_000 ? pageValue : 1;
  return { semester, period, status, page };
}

/**
 * The API filters by one cycle. A semester filter without a cycle becomes one query per cycle of that
 * semester; their pages are merged newest first. `null` means no FINAL cycle has that semester.
 */
export function timetableQueries(filters: TimetableFilters, periods: FinalPeriodOption[]) {
  const offset = (filters.page - 1) * TIMETABLE_PAGE_SIZE;
  if (filters.period || !filters.semester) {
    return [{ period_code: filters.period, status: filters.status, limit: TIMETABLE_PAGE_SIZE, offset }];
  }
  const codes = periods.filter((item) => item.semester === filters.semester).map((item) => item.period_code);
  if (!codes.length) return null;
  const limit = Math.min(offset + TIMETABLE_PAGE_SIZE, MAX_API_PAGE);
  return codes.map((code) => ({ period_code: code, status: filters.status, limit, offset: 0 }));
}

export function mergeTimetablePages(pages: { items: SavedTimetable[]; total: number }[], filters: TimetableFilters, merged: boolean) {
  const total = pages.reduce((sum, page) => sum + page.total, 0);
  if (!merged) return { items: pages[0]?.items ?? [], total };
  const offset = (filters.page - 1) * TIMETABLE_PAGE_SIZE;
  const items = pages.flatMap((page) => page.items)
    .sort((a, b) => b.created_at.localeCompare(a.created_at) || b.timetable_id - a.timetable_id)
    .slice(offset, offset + TIMETABLE_PAGE_SIZE);
  return { items, total };
}

// --- Saved timetable detail ----------------------------------------------------------------------

/** Explicitly picked display fields; specializations come from the module catalog. */
export function savedSessions(detail: Pick<SavedTimetableDetail, "sessions">, catalog: Pick<ExamModule, "module_code" | "specializations">[]): GenerationSession[] {
  const specializations = new Map(catalog.map((module) => [module.module_code, module.specializations]));
  return detail.sessions.map((session) => ({
    academic_year: session.academic_year, semester: session.semester, delivery_mode: session.delivery_mode,
    specializations: specializations.get(session.module_code)?.map(redactStudentIds),
    normal_candidates: session.normal_candidates, repeat_candidates: session.repeat_candidates,
    date: session.date, start_time: session.start_time, end_time: session.end_time,
    module_code: redactStudentIds(session.module_code), module_name: redactStudentIds(session.module_name),
    session_number: session.session_number, session_count: session.session_count, candidates: session.candidates,
    rooms: session.rooms.map((room) => ({ venue_id: redactStudentIds(room.venue_id), venue_name: redactStudentIds(room.venue_name), seats: room.seats })),
  }));
}

/** Who made a change: "You" for the signed-in user, otherwise a short Identity Service user ID. */
export function actorLabel(userId: string | null, me: string | undefined): string {
  if (!userId) return "System (migration)";
  if (me && userId === me) return "You";
  return `User ${userId.slice(0, 8)}`;
}
