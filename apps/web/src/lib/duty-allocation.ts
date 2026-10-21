import "server-only";
import { cache } from "react";
import { redirect } from "next/navigation";
import { auth } from "@/auth";
import { requireExaminationAdmin } from "@/lib/examination-access";
import type {
  AllocationRun, ChangeRequest, DemoPage, DutyPeriod, DutySession, DutyStaff, DutyStaffDetail, DutyStatus, Meta,
  Requirements, RoomSeats, Roster, RosterDetail, RosterHistory, RosterPage, RosterStatus, RunFairness, SeatingDraft,
  SeatingDraftRoom, SeatingRun, Violation, WorkloadPage,
} from "@/lib/duty-allocation-types";

// Server-side reads of the duty-allocation API. Same rules as lib/examination-api.ts: the Examination Admin role
// is checked first, the access token never leaves the server, nothing is cached between requests, and a 401 sends
// the user to sign in again. The duty-allocation API answers errors as {detail, code, problems?, details?}, so
// it gets its own reader here instead of the timetable one.

export type DutyApiError = {
  status: number;
  code: string;
  message: string;
  problems: string[];
  violations: Violation[];
  reasons: string[];
};
export type DutyResult<T> = { ok: true; data: T } | { ok: false; error: DutyApiError };

const BASE = "/api/v1/duty-allocation";
const DEFAULT_TIMEOUT_MS = 15_000;
const SESSION_EXPIRED = "/login?error=SessionExpired&callbackUrl=%2Fexams%2Fduties";

export const PERIOD_PATTERN = /^[A-Z0-9-]{1,32}$/;
export const RUN_PATTERN = /^[0-9a-f]{32}$/;
export const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export const ID_TEXT_PATTERN = /^[^\s/?#\\]{1,64}$/;

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function readError(status: number, body: unknown): DutyApiError {
  const error: DutyApiError = {
    status, code: `HTTP_${status}`, message: `Examination Service request failed (HTTP ${status}).`,
    problems: [], violations: [], reasons: [],
  };
  if (!record(body)) return error;
  if (typeof body.code === "string") error.code = body.code;
  if (typeof body.detail === "string") error.message = body.detail;
  if (Array.isArray(body.detail)) {
    // FastAPI request validation. Only field names and messages: never echo the rejected input back.
    error.code = "VALIDATION_ERROR";
    error.message = "Some values were not accepted.";
    error.problems = body.detail.filter(record).flatMap((issue) => {
      const parts = Array.isArray(issue.loc) ? issue.loc.filter((p) => typeof p === "string" || typeof p === "number") : [];
      return typeof issue.msg === "string" ? [`${parts.slice(1).join(".") || "request"}: ${issue.msg}`] : [];
    });
  }
  if (Array.isArray(body.problems)) error.problems.push(...body.problems.filter((p): p is string => typeof p === "string"));
  if (record(body.details)) {
    const { violations, reasons } = body.details;
    if (Array.isArray(violations)) {
      error.violations = violations.filter(record).flatMap((v) =>
        typeof v.constraint === "string" && typeof v.message === "string" ? [{ constraint: v.constraint, message: v.message }] : []);
    }
    if (Array.isArray(reasons)) error.reasons = reasons.filter((r): r is string => typeof r === "string");
  }
  return error;
}

/** One authenticated, uncached request to the duty-allocation API. `path` is relative to /api/v1/duty-allocation. */
export async function dutyRequest<T>(
  path: string,
  { method = "GET", query, body: requestBody, timeoutMs = DEFAULT_TIMEOUT_MS, sessionExpiredPath = SESSION_EXPIRED }: {
    method?: "GET" | "POST";
    query?: Record<string, string | number | boolean | undefined>;
    body?: unknown;
    timeoutMs?: number;
    sessionExpiredPath?: string;
  } = {},
): Promise<DutyResult<T>> {
  await requireExaminationAdmin();
  const session = await auth();
  if (!session?.accessToken || session.error) redirect(sessionExpiredPath);

  let url: URL;
  try {
    const base = (process.env.EXAMINATION_SERVICE_URL ?? "http://localhost:8003").replace(/\/+$/, "");
    url = new URL(`${base}${BASE}${path}`);
    if (!path.startsWith("/") || path.includes("..") || !["http:", "https:"].includes(url.protocol) || url.username || url.password) {
      throw new Error("Invalid Examination Service URL");
    }
    for (const [key, value] of Object.entries(query ?? {})) {
      if (value !== undefined && value !== "") url.searchParams.set(key, String(value));
    }
  } catch {
    return { ok: false, error: { status: 0, code: "CONFIGURATION_ERROR", message: "The Examination Service connection is not configured correctly.", problems: [], violations: [], reasons: [] } };
  }

  const signal = AbortSignal.timeout(timeoutMs);
  let response: Response;
  let body: unknown;
  try {
    response = await fetch(url, {
      method,
      headers: {
        Authorization: `Bearer ${session.accessToken}`,
        Accept: "application/json",
        ...(requestBody === undefined ? {} : { "Content-Type": "application/json" }),
      },
      body: requestBody === undefined ? undefined : JSON.stringify(requestBody),
      cache: "no-store",
      redirect: "error",
      signal,
    });
    const content = await response.text();
    try {
      body = content ? JSON.parse(content) : undefined;
    } catch {
      body = undefined;
    }
  } catch {
    const message = signal.aborted
      ? "The Examination Service took too long to respond. Please try again."
      : "The Examination Service could not be reached. Check that it is running, then try again.";
    return { ok: false, error: { status: 0, code: signal.aborted ? "TIMEOUT" : "UNREACHABLE", message, problems: [], violations: [], reasons: [] } };
  }

  if (response.status === 401) redirect(sessionExpiredPath);
  if (!response.ok) return { ok: false, error: readError(response.status, body) };
  if (body === undefined) {
    return { ok: false, error: { status: response.status, code: "INVALID_RESPONSE", message: "The Examination Service returned an empty or invalid response.", problems: [], violations: [], reasons: [] } };
  }
  return { ok: true, data: body as T };
}

// --------------------------------------------------------------------------------------------- reads

// React cache deduplicates these within one render only, never between users or requests.
export const getDutyStatus = cache(() => dutyRequest<DutyStatus>("/status"));
export const listDutyPeriods = cache(() => dutyRequest<{ items: DutyPeriod[]; meta: Meta }>("/exam-periods"));
export const listDutySessions = cache((period: string) =>
  dutyRequest<{ items: DutySession[]; total: number; meta: Meta }>(`/exam-periods/${encodeURIComponent(period)}/sessions`));
export const getRequirements = cache((period: string) =>
  dutyRequest<Requirements>(`/exam-periods/${encodeURIComponent(period)}/requirements`));
export const listDutyStaff = cache((period: string, includeExcluded: boolean) =>
  dutyRequest<DemoPage<DutyStaff>>("/staff", { query: { period_code: period, include_excluded: includeExcluded, limit: 500 } }));
export const listWorkloads = cache((period: string) =>
  dutyRequest<WorkloadPage>("/staff/workload", { query: { period_code: period, limit: 500 } }));
export const getDutyStaff = cache((staffId: string, period: string) =>
  dutyRequest<DutyStaffDetail>(`/staff/${encodeURIComponent(staffId)}`, { query: { period_code: period } }));

export const getAllocationRun = cache((runId: string) => dutyRequest<AllocationRun>(`/allocations/${runId}`));
export const getRunFairness = cache((runId: string) => dutyRequest<RunFairness>(`/allocations/${runId}/fairness`));

export const getSeatingRun = cache((seatingId: string) => dutyRequest<SeatingRun>(`/seating/${seatingId}`));
export const getRoomSeats = cache((seatingId: string, sessionId: string, venueId: string) =>
  dutyRequest<RoomSeats>(`/seating/${seatingId}/rooms/${encodeURIComponent(sessionId)}/${encodeURIComponent(venueId)}`));

export const listRosters = cache((period: string | undefined, status: RosterStatus | undefined, limit = 50, offset = 0) =>
  dutyRequest<RosterPage<Roster>>("/rosters", { query: { period_code: period, status, limit, offset } }));
const rosterLogin = (id: string) => `/login?error=SessionExpired&callbackUrl=${encodeURIComponent(`/exams/duties/rosters/${id}`)}`;
export const getRoster = cache((rosterId: string) =>
  dutyRequest<RosterDetail>(`/rosters/${rosterId}`, { sessionExpiredPath: rosterLogin(rosterId) }));
export const getRosterHistory = cache((rosterId: string) =>
  dutyRequest<RosterHistory>(`/rosters/${rosterId}/history`, { sessionExpiredPath: rosterLogin(rosterId) }));
export const listChangeRequests = cache((rosterId: string) => dutyRequest<ChangeRequest[]>(`/rosters/${rosterId}/change-requests`));

export const listSeatingDrafts = cache((period: string | undefined) =>
  dutyRequest<RosterPage<SeatingDraft>>("/seating-drafts", { query: { period_code: period, limit: 20 } }));
export const getSeatingDraftRoom = cache((draftId: string, sessionId: string, venueId: string) =>
  dutyRequest<SeatingDraftRoom>(`/seating-drafts/${draftId}/rooms/${encodeURIComponent(sessionId)}/${encodeURIComponent(venueId)}`));

/**
 * The period a page shows: the one in the URL when it exists, else the service's default, else the first one.
 * Returns the periods too, for the picker.
 */
export async function resolvePeriod(requested: string | undefined) {
  const result = await listDutyPeriods();
  if (!result.ok) return result;
  const periods = result.data.items;
  const codes = new Set(periods.map((p) => p.period_code));
  let period = requested && codes.has(requested) ? requested : undefined;
  if (!period) {
    const status = await getDutyStatus();
    period = status.ok && codes.has(status.data.default_period_code) ? status.data.default_period_code : periods[0]?.period_code;
  }
  return { ok: true as const, data: { periods, period, meta: result.data.meta } };
}
