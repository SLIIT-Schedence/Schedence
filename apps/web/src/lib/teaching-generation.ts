import "server-only";
import { redirect } from "next/navigation";
import { cache } from "react";
import { auth } from "@/auth";
import { allStaff } from "@/lib/academic";
import { identityRequest } from "@/lib/identity-api";
import { type ApiError, type ApiResult, serviceRequest, serviceUrl, type Service } from "@/lib/service-api";
import { getWorkloadReport, listTimetables, type Stream, type TimetableSummary } from "@/lib/teaching";
import { parseRulesSelection, rulesMatchReport, type SchedulingRules, type SchedulingRulesReport } from "@/lib/teaching-scheduling-rules";

// The Generate Timetable wizard's server side. Every fact comes from an existing API for the signed-in user's
// institution: Identity (/me/permissions), the Academic Data Service (staff register) and the Teaching Service
// (validated versions, their stored input snapshot and source checksums, the workload report's academic term,
// and /generations). Nothing is inferred, and the Historical Experimental Preview is never an input.

const TEACHING: Service = {
  baseUrl: () => serviceUrl("TEACHING_SERVICE_URL", "http://localhost:8004"),
  unreachable: "The teaching timetable service didn't respond.",
};

export const STEPS = ["scope", "inputs", "rules", "readiness", "generate"] as const;
export type Step = (typeof STEPS)[number];

/** All years / both streams are null: the request then omits them and the service selects every applicable one. */
export type Scope = { semester: 1 | 2; year: 1 | 2 | 3 | 4 | null; stream: Stream | null };

export function parseScope(values: Record<string, string | undefined>): Scope {
  const semester = values.semester === "2" ? 2 : 1;
  const year = ["1", "2", "3", "4"].includes(values.year ?? "") ? (Number(values.year) as 1 | 2 | 3 | 4) : null;
  const stream = values.stream === "WEEKDAY" || values.stream === "WEEKEND" ? values.stream : null;
  return { semester, year, stream };
}

export const scopeQuery = (scope: Scope) => ({ semester: scope.semester, year: scope.year ?? undefined, stream: scope.stream ?? undefined });

export function scopeLabel(scope: Scope) {
  const stream = scope.stream === "WEEKDAY" ? "Weekday" : scope.stream === "WEEKEND" ? "Weekend" : "Weekday and weekend";
  return `Semester ${scope.semester} · ${scope.year ? `Year ${scope.year}` : "Years 1–4"} · ${stream}`;
}

export type CheckStatus = "READY" | "WARNING" | "MISSING";
export type Check = { key: string; title: string; status: CheckStatus; detail: string };

/** The exact generation request body, built only from the stored snapshot the service already validated. */
export type GenerationBody = {
  institution_id: string;
  snapshot_id: string;
  academic_term_reference: string;
  semester: 1 | 2;
  academic_years?: number[];
  streams?: Stream[];
  calendar_label: "PP1_TEST_CALENDAR" | "PP1_EXPERIMENTAL_CALENDAR";
  calendar_reference: string;
  scheduling_policy_reference: string;
  policy_version: "HISTORICAL_SNAPSHOT";
  strategy: "MONOLITHIC";
  max_time_seconds: number;
  job_timeout_seconds: number;
  scheduling_rules: SchedulingRules;
};

/** Native solver limit and whole-job limit sent with every request (the service may cap the job limit lower). */
export const TIME_LIMITS = { max_time_seconds: 300, job_timeout_seconds: 600 };

export type ScopeSummary = { groups: number; specializations: string[]; blocked: { year: number; stream: string; specialization: string }[] };

/** The Teaching Service's read-only input readiness for one scope (GET /generation-inputs). */
export type GenerationInputs = {
  institution_id: string;
  semester: number;
  academic_years: number[];
  streams: Stream[];
  ready: boolean;
  checks: Check[];
  inputs: {
    staff: number;
    modules: number;
    module_offerings: number;
    student_groups: number;
    venues: number;
    teaching_team_entries: number;
    lecturer_preferences: number;
    session_requirements: number;
    expected_sessions: number;
    specializations: string[];
    blocked_scopes: { academic_year: number; stream: Stream; specialization: string; student_count: number | null; scope_status: string }[];
    unresolved_issue_codes: string[];
    calendar_label: string;
    academic_term_reference: string;
    configuration_label: string;
    prepared_snapshot_sha256: string;
  } | null;
  /** Institution-wide owner totals, present even when `inputs` could not be validated (neither scoped nor validated). */
  owner_records?: {
    staff: number;
    modules: number;
    student_groups: number;
    teaching_team_entries: number;
    venues: number;
    lecturer_preferences: number;
  } | null;
  /** The stored Teaching input snapshot readiness assessed; a generation request quotes these exact references. */
  prepared_snapshot?: {
    snapshot_id: string;
    academic_term_reference: string;
    calendar_label: GenerationBody["calendar_label"];
    calendar_reference: string;
    scheduling_policy_reference: string;
  } | null;
  generation_enabled: boolean;
  snapshot_stored: boolean;
  university_approved: false;
  scheduling_rules?: SchedulingRulesReport | null;
  stored_input?: StoredInput | null;
};

export type StoredInput = {
  institution_id: string; snapshot_id: string; revision: number; semantic_sha256: string;
  validated_snapshot_id: string | null; validated_rules_reference: string;
  semester: number; academic_years: number[]; streams: Stream[];
  calendar_label: GenerationBody["calendar_label"]; academic_term_reference: string;
  calendar_reference: string; scheduling_policy_reference: string; persistence: "ISOLATED_POSTGRESQL";
};

export type Readiness = {
  checks: Check[];
  ready: boolean;
  body: GenerationBody | null;
  snapshot: TimetableSummary | null;
  inputSnapshot?: StoredInput | null;
  scopeSummary: ScopeSummary | null;
  staff: { total: number; ranked: number; byRank: [string, number][] } | null;
  staffError: ApiError | null;
  /** Owner-validated inputs for the selected scope, or why they could not be assessed. */
  inputs: ApiResult<GenerationInputs>;
};

/**
 * An authorized Teaching Service call with its own time limit and headers, which the shared helper cannot set
 * (owner-record validation takes tens of seconds; generation needs an Idempotency-Key). Same token and error
 * handling as `serviceRequest`; a rejected token goes to sign-in.
 */
async function teachingFetch<T>(path: string, { method = "GET", body, headers = {}, timeoutMs }: {
  method?: "GET" | "POST";
  body?: unknown;
  headers?: Record<string, string>;
  timeoutMs: number;
}): Promise<ApiResult<T>> {
  const session = await auth();
  if (!session?.accessToken || session.error) redirect("/login?error=SessionExpired");
  let response: Response;
  try {
    response = await fetch(`${TEACHING.baseUrl()}${path}`, {
      method,
      headers: {
        Authorization: `Bearer ${session.accessToken}`,
        Accept: "application/json",
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
        ...headers,
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
      signal: AbortSignal.timeout(timeoutMs),
    });
  } catch {
    return { ok: false, error: { status: 0, code: "UNREACHABLE", message: TEACHING.unreachable } };
  }
  if (response.status === 401) redirect("/login?error=SessionExpired");
  if (response.ok) return { ok: true, data: (await response.json()) as T };
  try {
    const payload = (await response.json()) as { error?: { code?: string; message?: string } };
    return { ok: false, error: { status: response.status, code: payload.error?.code ?? `HTTP_${response.status}`, message: payload.error?.message ?? `Request failed (HTTP ${response.status}).` } };
  } catch {
    return { ok: false, error: { status: response.status, code: `HTTP_${response.status}`, message: `Request failed (HTTP ${response.status}).` } };
  }
}

/** Prepare and validate the institution's inputs for the scope, read-only (no snapshot stored, nothing solved). */
export async function getGenerationInputs(institutionId: string, semester: 1 | 2, year: number | null, stream: Stream | null, rulesSelection?: string): Promise<ApiResult<GenerationInputs>> {
  const params = new URLSearchParams({ institution_id: institutionId, semester: String(semester) });
  if (year) params.append("academic_year", String(year));
  if (stream) params.append("stream", stream);
  const parsed = rulesSelection === undefined ? undefined : parseRulesSelection(rulesSelection);
  if (parsed && !parsed.ok) return { ok: false, error: { status: 422, code: "INVALID_SCHEDULING_RULES", message: parsed.error } };
  const result = await teachingFetch<GenerationInputs>(`/api/v1/teaching/generation-inputs?${params}`, {
    timeoutMs: 120_000, ...(parsed?.ok ? { method: "POST", body: parsed.rules } : {}),
  });
  if (result.ok && result.data.ready && parsed?.ok && !rulesMatchReport(parsed.rules, result.data.scheduling_rules)) {
    return { ok: false, error: { status: 502, code: "RULES_NOT_VALIDATED", message: "The Teaching Service did not confirm the selected rules. Review Scheduling Rules and try again." } };
  }
  return result;
}

type MePermissions = { institutions: { institution_id: string; roles: string[]; permissions: string[] }[] };

const CALENDAR_SOURCE = "PP1:calendar-configuration";
const POLICY_SOURCE = "PP1:synthetic-elective-registration-evidence";

/** Prefer an unsealed snapshot that can accept experimental rules, then the widest semester coverage. */
function snapshotFor(versions: TimetableSummary[], semester: number) {
  const years = (row: TimetableSummary) => new Set(row.scopes.filter((scope) => scope.semester === semester).map((scope) => scope.academic_year)).size;
  return versions
    .filter((row) => row.validation_status === "PASSED" && row.input_snapshot_id && years(row) > 0)
    .sort((a, b) => Number(Boolean(a.institution_policy_revision)) - Number(Boolean(b.institution_policy_revision)) || years(b) - years(a))[0];
}

export const checkReadiness = cache(async (institutionId: string, semester: 1 | 2, year: number | null, stream: Stream | null, rulesSelection?: string): Promise<Readiness> => {
  const checks: Check[] = [];
  const [permissions, register, listing, inputs] = await Promise.all([
    identityRequest<MePermissions>("/api/v1/identity/me/permissions"),
    allStaff(),
    listTimetables(institutionId),
    getGenerationInputs(institutionId, semester, year, stream, rulesSelection),
  ]);

  const granted = permissions.ok && permissions.data.institutions.some((row) => row.institution_id === institutionId && row.permissions.includes("teaching:generate"));
  checks.push({
    key: "authorization",
    title: "Permission to generate",
    status: granted ? "READY" : "MISSING",
    detail: !permissions.ok
      ? `Your permissions could not be read (${permissions.error.message}).`
      : granted
        ? "You hold teaching:generate for this institution."
        : "Your account has no teaching:generate permission for this institution. Ask your Institution Admin.",
  });

  // The Teaching Service's own owner-record validation of the prepared inputs for exactly this scope.
  if (inputs.ok) {
    checks.push(...inputs.data.checks.map((row) => ({ ...row, key: `inputs:${row.key}` })));
  } else {
    checks.push({
      key: "inputs",
      title: "Institution inputs",
      status: "MISSING",
      detail:
        inputs.error.code === "READINESS_DISABLED"
          ? "Input readiness is not enabled in this Teaching Service deployment, so the institution's inputs cannot be validated."
          : inputs.error.code === "GENERATION_FORBIDDEN"
            ? "The Teaching Service did not accept your teaching:generate permission for this institution."
            : `The institution's inputs could not be validated (${inputs.error.message}).`,
    });
  }

  const staff = register.ok
    ? {
        total: register.data.total,
        ranked: register.data.items.filter((row) => row.academicRank).length,
        byRank: [...register.data.items.reduce((map, row) => map.set(row.academicRank ?? "Rank not recorded", (map.get(row.academicRank ?? "Rank not recorded") ?? 0) + 1), new Map<string, number>())].sort((a, b) => b[1] - a[1]),
      }
    : null;
  const selected = parseRulesSelection(rulesSelection);
  checks.push({ key: "selected-rules", title: "Scheduling Rules", status: selected.ok && inputs.ok && inputs.data.ready ? "READY" : "MISSING",
    detail: !selected.ok ? selected.error : inputs.ok && inputs.data.ready ? "Your selected rules passed backend validation for this scope." : "Review the rule and input checks before continuing." });
  checks.push({
    key: "staff",
    title: "Staff register",
    status: staff && staff.total > 0 ? "READY" : "MISSING",
    detail: !register.ok ? `The staff register could not be read (${register.error.message}).` : staff && staff.total > 0 ? `${staff.total} staff in the Academic Data Service register.` : "The staff register is empty.",
  });

  let snapshot: TimetableSummary | null = null;
  let scopeSummary: ScopeSummary | null = null;
  let body: GenerationBody | null = null;
  const inputSnapshot = inputs.ok ? inputs.data.stored_input ?? null : null;
  if (inputSnapshot) {
    const compatible = inputSnapshot.institution_id === institutionId && inputSnapshot.semester === semester
      && inputSnapshot.academic_years.length === (year ? 1 : 4)
      && (year ? inputSnapshot.academic_years[0] === year : [1, 2, 3, 4].every((value) => inputSnapshot.academic_years.includes(value)))
      && inputSnapshot.streams.length === (stream ? 1 : 2)
      && (stream ? inputSnapshot.streams[0] === stream : ["WEEKDAY", "WEEKEND"].every((value) => inputSnapshot.streams.includes(value as Stream)))
      && inputSnapshot.persistence === "ISOLATED_POSTGRESQL" && inputs.ok && inputs.data.snapshot_stored;
    checks.push({ key: "snapshot", title: "Stored input snapshot", status: compatible ? "READY" : "MISSING",
      detail: compatible ? `Input version ${inputSnapshot.revision} is stored in isolated development PostgreSQL. No timetable has been generated from it.`
        : "The stored input version does not match this institution and scope. Select the prepared year, semester and stream." });
    const data = inputs.ok ? inputs.data.inputs : null;
    scopeSummary = data ? { groups: data.student_groups, specializations: data.specializations,
      blocked: data.blocked_scopes.map((row) => ({ year: row.academic_year, stream: row.stream, specialization: row.specialization })) } : null;
    if (compatible && granted && selected.ok && inputs.ok && inputs.data.ready) {
      body = { institution_id: institutionId, snapshot_id: inputSnapshot.snapshot_id,
        academic_term_reference: inputSnapshot.academic_term_reference, semester,
        ...(year ? { academic_years: [year] } : {}), ...(stream ? { streams: [stream] } : {}),
        calendar_label: inputSnapshot.calendar_label, calendar_reference: inputSnapshot.calendar_reference,
        scheduling_policy_reference: inputSnapshot.scheduling_policy_reference, policy_version: "HISTORICAL_SNAPSHOT",
        strategy: "MONOLITHIC", ...TIME_LIMITS, scheduling_rules: selected.rules };
    }
  } else if (!listing.ok) {
    checks.push({ key: "snapshot", title: "Stored input snapshot", status: "MISSING", detail: `The Teaching Service could not be read (${listing.error.message}).` });
  } else {
    snapshot = snapshotFor(listing.data.items, semester) ?? null;
    const prepared = inputs.ok ? inputs.data.prepared_snapshot ?? null : null;
    if (!snapshot && prepared && inputs.ok) {
      // No generated version yet: solve the stored input snapshot readiness just validated. The Teaching Service
      // re-binds it to the owners' records and re-checks every reference before it accepts the job.
      checks.push({ key: "snapshot", title: "Stored input snapshot", status: "READY",
        detail: `Prepared input snapshot for Semester ${semester} is stored in the Teaching Service and re-checked against the owners' records before solving.` });
      const data = inputs.data.inputs;
      scopeSummary = data ? { groups: data.student_groups, specializations: data.specializations,
        blocked: data.blocked_scopes.map((row) => ({ year: row.academic_year, stream: row.stream, specialization: row.specialization })) } : null;
      if (granted && selected.ok && inputs.data.ready) {
        body = { institution_id: institutionId, snapshot_id: prepared.snapshot_id,
          academic_term_reference: prepared.academic_term_reference, semester,
          ...(year ? { academic_years: [year] } : {}), ...(stream ? { streams: [stream] } : {}),
          calendar_label: prepared.calendar_label, calendar_reference: prepared.calendar_reference,
          scheduling_policy_reference: prepared.scheduling_policy_reference, policy_version: "HISTORICAL_SNAPSHOT",
          strategy: "MONOLITHIC", ...TIME_LIMITS, scheduling_rules: selected.rules };
      }
    } else if (!snapshot) {
      checks.push({
        key: "snapshot",
        title: "Stored input snapshot",
        status: "MISSING",
        detail: inputs.ok && inputs.data.ready
          ? `The inputs above are prepared and validated, but not stored: storing the snapshot is a production write, which is disabled for now. Generation solves a stored snapshot.`
          : `This institution has no stored input snapshot for Semester ${semester}, and generation solves a stored snapshot.`,
      });
    } else {
      checks.push({ key: "snapshot", title: "Stored input snapshot", status: "READY", detail: `Snapshot of validated version ${snapshot.version_number}, re-checked by the Teaching Service before solving.` });
      const editablePolicy = !snapshot.institution_policy_revision;
      checks.push({ key: "snapshot-rules", title: "Stored snapshot policy", status: editablePolicy ? "READY" : "MISSING",
        detail: editablePolicy ? "The stored snapshot can accept experimental Scheduling Rules; the backend checks it again before accepting a job."
          : "This snapshot has a sealed institution policy. Experimental Scheduling Rules cannot replace it; select an unsealed snapshot through the Teaching Service." });
      const scopes = snapshot.scopes.filter((row) => row.semester === semester && (!year || row.academic_year === year) && (!stream || row.stream === stream));
      const supported = scopes.filter((row) => row.scope_status === "SUPPORTED");
      scopeSummary = {
        groups: new Set(supported.flatMap((row) => row.student_groups)).size,
        specializations: [...new Set(supported.map((row) => row.specialization))].sort(),
        blocked: scopes.filter((row) => row.scope_status !== "SUPPORTED").map((row) => ({ year: row.academic_year, stream: row.stream, specialization: row.specialization })),
      };
      checks.push({
        key: "scope",
        title: "Selected scope",
        status: supported.length === 0 ? "MISSING" : scopeSummary.blocked.length ? "WARNING" : "READY",
        detail:
          supported.length === 0
            ? "The snapshot has no schedulable student groups for this year and stream."
            : scopeSummary.blocked.length
              ? `${scopeSummary.groups} student groups can be scheduled. Not scheduled (missing academic mapping): ${scopeSummary.blocked.map((row) => `Year ${row.year} ${row.specialization} ${row.stream.toLowerCase()}`).join(", ")}.`
              : `${scopeSummary.groups} student groups across ${scopeSummary.specializations.length} specializations or tracks.`,
      });

      const sources = new Map((snapshot.policy_sources ?? []).map((row) => [row.label, row.sha256]));
      const calendarReference = sources.get(CALENDAR_SOURCE);
      const policyReference = sources.get(POLICY_SOURCE);
      const calendarLabel = snapshot.calendar.label;
      const calendarOk = Boolean(calendarReference && policyReference) && (calendarLabel === "PP1_TEST_CALENDAR" || calendarLabel === "PP1_EXPERIMENTAL_CALENDAR");
      checks.push({
        key: "calendar",
        title: "Calendar and scheduling policy",
        status: calendarOk ? "READY" : "MISSING",
        detail: calendarOk
          ? `${calendarLabel.replaceAll("_", " ").toLowerCase()} and its policy evidence, matched by checksum.${snapshot.calendar.synthetic_data ? " Dated on a synthetic reference week." : ""}`
          : "The snapshot's calendar or scheduling-policy evidence is missing, so the request cannot reference it.",
      });

      const report = await getWorkloadReport(institutionId, snapshot.version_id);
      const term = report.ok ? report.data.academic_term_reference : undefined;
      checks.push({
        key: "term",
        title: "Academic term",
        status: term ? "READY" : "MISSING",
        detail: term ? term : `The snapshot's academic term could not be read${report.ok ? "" : ` (${report.error.message})`}.`,
      });

      if (granted && calendarOk && term && supported.length > 0 && editablePolicy && selected.ok && inputs.ok && inputs.data.ready) {
        body = {
          institution_id: institutionId,
          snapshot_id: snapshot.input_snapshot_id!,
          academic_term_reference: term,
          semester,
          ...(year ? { academic_years: [year] } : {}),
          ...(stream ? { streams: [stream] } : {}),
          calendar_label: calendarLabel as GenerationBody["calendar_label"],
          calendar_reference: calendarReference!,
          scheduling_policy_reference: policyReference!,
          policy_version: "HISTORICAL_SNAPSHOT",
          strategy: "MONOLITHIC",
          ...TIME_LIMITS,
          scheduling_rules: selected.rules,
        };
      }
    }
  }

  const workerEnabled = inputs.ok && inputs.data.generation_enabled;
  checks.push({
    key: "worker",
    title: "Generation worker",
    status: workerEnabled ? "READY" : inputs.ok ? "MISSING" : "WARNING",
    detail: workerEnabled
      ? "A generation worker is enabled in this deployment."
      : inputs.ok
        ? "Generation is disabled in this Teaching Service deployment, so the solver cannot be started."
        : "Could not be confirmed; a deployment without a generation worker refuses the request instead of pretending to accept it.",
  });

  const ready = checks.every((row) => row.status !== "MISSING") && body !== null;
  return { checks, ready, body, snapshot, inputSnapshot, scopeSummary, staff, staffError: register.ok ? null : register.error, inputs };
});

// -------------------------------------------------------------------------------------------- jobs

export type GenerationJob = {
  job_id: string;
  institution_id: string;
  status: "PENDING" | "RUNNING" | "COMPLETED" | "FAILED";
  created_at: string;
  started_at?: string;
  finished_at?: string;
  elapsed_seconds: number;
  solver_status?: string;
  solver_runtime_seconds?: number;
  validation_status: "NOT_ASSESSED" | "PASSED" | "FAILED";
  expected_occurrences: number;
  scheduled_occurrences: number;
  timetable_version_id?: string;
  persistence: string;
  unresolved_requirement_codes: string[];
  blocked_student_count: number;
  error?: { category: string; message: string; retry_with_new_key: boolean };
  request: { semester: number; academic_years: number[]; streams: string[] };
};

export function getGenerationJob(institutionId: string, jobId: string) {
  return serviceRequest<GenerationJob>(TEACHING, `/api/v1/teaching/generations/${encodeURIComponent(jobId)}`, { query: { institution_id: institutionId } });
}

/** POST /generations with a fresh Idempotency-Key; preflight re-validates the snapshot before accepting (up to 90 s). */
export function submitGeneration(body: GenerationBody): Promise<ApiResult<GenerationJob>> {
  return teachingFetch<GenerationJob>("/api/v1/teaching/generations", {
    method: "POST",
    body,
    headers: { "Idempotency-Key": `wizard-${crypto.randomUUID()}` },
    timeoutMs: 120_000,
  });
}
