"use server";

import { refresh } from "next/cache";
import { redirect } from "next/navigation";
import { requireExaminationAdmin } from "@/lib/examination-access";
import {
  dutyRequest, ID_TEXT_PATTERN, PERIOD_PATTERN, RUN_PATTERN, UUID_PATTERN, type DutyApiError,
} from "@/lib/duty-allocation";
import type {
  AllocationMode, ChangePreview, ChangeRequest, DutyRole, Roster, RosterDetail, RosterLabel, SeatingDraft, SeatingRun,
} from "@/lib/duty-allocation-types";
import type { FormState } from "@/lib/form-state";

// Changes to duty rosters, change requests and seating drafts. The Examination Service is the authority: it
// checks every permission, the roster's row_version, the workflow status and every staffing rule. These actions
// only validate the form, send exactly what the admin confirmed, and phrase the answer.

export type PreviewState = FormState & { preview?: ChangePreview };

const SOLVE_TIMEOUT_MS = 300_000;
const WORKFLOW_TIMEOUT_MS = 60_000;
const MODES: AllocationMode[] = ["baseline", "fair", "compare"];
const LABELS: RosterLabel[] = ["BASELINE", "FAIR"];
const ROLES: DutyRole[] = ["SUPERVISOR", "INVIGILATOR"];
const PERMISSION: Record<string, string> = {
  generate: "examinations:generate",
  approve: "examinations:approve",
  manage: "examinations:manage",
};

const field = (formData: FormData, name: string) => {
  const value = formData.get(name);
  return typeof value === "string" ? value.trim() : "";
};
const validVersion = (value: unknown): value is number => Number.isSafeInteger(value) && (value as number) >= 1;

function failure(error: DutyApiError, permission: keyof typeof PERMISSION, what: string): FormState {
  const details = [...error.violations.map((v) => `${v.constraint}: ${v.message}`), ...error.reasons, ...error.problems].slice(0, 6);
  const withDetails = (message: string) => (details.length ? `${message} ${details.join(" · ")}` : message);
  if (error.status === 0) return { status: "error", message: `${error.message} Reload the page to see whether the change was made.` };
  if (error.status === 403) return { status: "error", message: `Your account needs the ${PERMISSION[permission]} permission to ${what}.` };
  if (error.status === 404) return { status: "error", message: withDetails(`${error.message} Previews are kept in memory only, so they disappear when the service restarts. Reload the page.`) };
  if (error.status === 409) return { status: "error", message: withDetails(`${error.message} Reload the page to see the latest version.`) };
  if (error.status === 429) return { status: "error", message: "The Examination Service is busy. Wait a moment, then try again." };
  if (error.status >= 500 && error.status !== 503) return { status: "error", message: "The Examination Service is temporarily unavailable. Nothing was changed. Try again later." };
  return { status: "error", message: withDetails(error.message) };
}

/** Reads "session~venue~staff~role" from a form field (see assignmentValue). */
function assignmentRef(formData: FormData, name: string) {
  const parts = field(formData, name).split("~").map((part) => {
    try {
      return decodeURIComponent(part);
    } catch {
      return "";
    }
  });
  if (parts.length !== 4) return null;
  const [session_id, venue_id, staff_id, role] = parts;
  if (!ID_TEXT_PATTERN.test(session_id) || !ID_TEXT_PATTERN.test(venue_id) || !UUID_PATTERN.test(staff_id) || !ROLES.includes(role as DutyRole)) return null;
  return { session_id, venue_id, staff_id, role };
}

function reasonOf(formData: FormData, required: boolean): { reason?: string; error?: FormState } {
  const reason = field(formData, "reason");
  if (!reason && required) return { error: { status: "error", message: "Check the reason.", fieldErrors: { reason: "Enter a reason (up to 500 characters)." } } };
  if (reason.length > 500) return { error: { status: "error", message: "Check the reason.", fieldErrors: { reason: "Keep the reason under 500 characters." } } };
  return { reason: reason || undefined };
}

// ------------------------------------------------------------------------------------------- allocation

/** Runs the CP-SAT allocation (a temporary preview), then opens its result. */
export async function runAllocation(_previous: FormState, formData: FormData): Promise<FormState> {
  await requireExaminationAdmin();
  const period = field(formData, "period");
  const mode = field(formData, "mode") as AllocationMode;
  const limit = Number(field(formData, "time_limit"));
  if (!PERIOD_PATTERN.test(period) || !MODES.includes(mode) || ![10, 20, 60].includes(limit)) {
    return { status: "error", message: "Choose an exam period, a mode and a time limit." };
  }
  const result = await dutyRequest<{ run_id: string }>(`/allocations/${mode}`, {
    method: "POST", timeoutMs: SOLVE_TIMEOUT_MS, body: { period_code: period, time_limit_seconds: limit },
  });
  if (!result.ok) return failure(result.error, "generate", "run allocations");
  redirect(`/exams/duties/allocation?period=${encodeURIComponent(period)}&run=${result.data.run_id}`);
}

/** Saves one roster of a preview run as a DRAFT record. The backend re-validates it before storing. */
export async function saveRosterDraft(runId: string, label: RosterLabel): Promise<FormState> {
  await requireExaminationAdmin();
  if (!RUN_PATTERN.test(runId) || !LABELS.includes(label)) return { status: "error", message: "This allocation could not be identified. Run it again." };
  const result = await dutyRequest<RosterDetail>("/rosters", { method: "POST", timeoutMs: WORKFLOW_TIMEOUT_MS, body: { run_id: runId, roster: label } });
  if (!result.ok) return failure(result.error, "generate", "save rosters");
  redirect(`/exams/duties/rosters/${result.data.roster_id}?saved=1`);
}

// ------------------------------------------------------------------------------------------- roster workflow

type Transition = "validate" | "approve" | "publish" | "reject" | "revise";
const TRANSITION_PERMISSION: Record<Transition, keyof typeof PERMISSION> = {
  validate: "generate", approve: "approve", publish: "approve", reject: "approve", revise: "manage",
};
const DONE: Record<Exclude<Transition, "revise">, string> = {
  validate: "validated", approve: "approved", publish: "published", reject: "rejected",
};

/** DRAFT -> VALIDATED -> APPROVED -> PUBLISHED, REJECTED, or a new DRAFT revision. */
export async function changeRosterStatus(
  rosterId: string, rowVersion: number, action: Transition, _previous: FormState, formData: FormData,
): Promise<FormState> {
  await requireExaminationAdmin();
  if (!UUID_PATTERN.test(rosterId) || !validVersion(rowVersion) || !(action in TRANSITION_PERMISSION)) {
    return { status: "error", message: "This roster could not be identified. Reload the page." };
  }
  const needsReason = action === "reject" || action === "revise";
  const { reason, error } = reasonOf(formData, needsReason);
  if (error) return error;
  if (action === "publish" && field(formData, "confirm") !== "PUBLISH") {
    return { status: "error", message: "Check the highlighted fields.", fieldErrors: { confirm: "Type PUBLISH in capital letters to confirm." } };
  }
  const body = needsReason ? { expected_version: rowVersion, reason } : { expected_version: rowVersion, note: reason };
  const path = action === "revise" ? `/rosters/${rosterId}/revisions` : `/rosters/${rosterId}/${action}`;
  const result = await dutyRequest<Roster>(path, { method: "POST", timeoutMs: WORKFLOW_TIMEOUT_MS, body });
  if (!result.ok) return failure(result.error, TRANSITION_PERMISSION[action], `${action} rosters`);
  if (action === "revise") redirect(`/exams/duties/rosters/${result.data.roster_id}?saved=1`);
  refresh();
  return { status: "success", message: `Roster version ${result.data.version} ${DONE[action]}.` };
}

// ------------------------------------------------------------------------------------------- swaps & replacements

function changeBody(kind: "swap" | "replacement", formData: FormData): { body?: Record<string, unknown>; error?: FormState } {
  if (kind === "swap") {
    const first = assignmentRef(formData, "first");
    const second = assignmentRef(formData, "second");
    if (!first || !second) return { error: { status: "error", message: "Choose the two duties to swap." } };
    return { body: { first, second } };
  }
  const duty = assignmentRef(formData, "duty");
  if (!duty) return { error: { status: "error", message: "Choose the duty that needs a replacement." } };
  const preferred = field(formData, "preferred_staff_id");
  if (preferred && !UUID_PATTERN.test(preferred)) return { error: { status: "error", message: "Choose a valid preferred replacement." } };
  return { body: { duty, preferred_staff_id: preferred || undefined } };
}

/** Asks the solver for the change and its effect on fairness. Changes nothing. */
export async function previewChange(rosterId: string, kind: "swap" | "replacement", _previous: PreviewState, formData: FormData): Promise<PreviewState> {
  await requireExaminationAdmin();
  if (!UUID_PATTERN.test(rosterId)) return { status: "error", message: "This roster could not be identified. Reload the page." };
  const { body, error } = changeBody(kind, formData);
  if (error) return error;
  const { reason, error: reasonError } = reasonOf(formData, true);
  if (reasonError) return reasonError;
  const result = await dutyRequest<ChangePreview>(`/rosters/${rosterId}/change-previews/${kind}`, {
    method: "POST", timeoutMs: WORKFLOW_TIMEOUT_MS, body: { ...body, reason },
  });
  if (!result.ok) return failure(result.error, "generate", `preview ${kind}s`);
  return { status: "idle", preview: result.data };
}

/** Records a change request for approval. The roster itself is not changed until the request is applied. */
export async function requestChange(
  rosterId: string, rowVersion: number, kind: "swap" | "replacement", _previous: PreviewState, formData: FormData,
): Promise<PreviewState> {
  await requireExaminationAdmin();
  if (!UUID_PATTERN.test(rosterId) || !validVersion(rowVersion)) return { status: "error", message: "This roster could not be identified. Reload the page." };
  const { body, error } = changeBody(kind, formData);
  if (error) return error;
  const { reason, error: reasonError } = reasonOf(formData, true);
  if (reasonError) return reasonError;
  const result = await dutyRequest<ChangeRequest>(`/rosters/${rosterId}/change-requests/${kind}`, {
    method: "POST", timeoutMs: WORKFLOW_TIMEOUT_MS, body: { ...body, reason, expected_version: rowVersion },
  });
  if (!result.ok) return failure(result.error, "generate", `request ${kind}s`);
  refresh();
  return { status: "success", message: `${kind === "swap" ? "Swap" : "Replacement"} request recorded. It needs approval before it is applied.` };
}

type Decision = "approve" | "reject" | "cancel" | "apply";

export async function decideChange(requestId: string, decision: Decision, _previous: FormState, formData: FormData): Promise<FormState> {
  await requireExaminationAdmin();
  if (!UUID_PATTERN.test(requestId) || !["approve", "reject", "cancel", "apply"].includes(decision)) {
    return { status: "error", message: "This request could not be identified. Reload the page." };
  }
  const { reason, error } = reasonOf(formData, decision === "reject");
  if (error) return error;
  const result = await dutyRequest<unknown>(`/change-requests/${requestId}/${decision}`, {
    method: "POST", timeoutMs: WORKFLOW_TIMEOUT_MS, body: decision === "apply" ? undefined : { reason },
  });
  if (!result.ok) return failure(result.error, decision === "cancel" ? "generate" : "approve", `${decision} change requests`);
  refresh();
  const done = { approve: "approved", reject: "rejected", cancel: "cancelled", apply: "applied to the roster" }[decision];
  return { status: "success", message: `Change request ${done}.` };
}

// ------------------------------------------------------------------------------------------- seating

export async function generateSeating(_previous: FormState, formData: FormData): Promise<FormState> {
  await requireExaminationAdmin();
  const period = field(formData, "period");
  if (!PERIOD_PATTERN.test(period)) return { status: "error", message: "Choose an exam period." };
  const result = await dutyRequest<SeatingRun>("/seating", { method: "POST", timeoutMs: SOLVE_TIMEOUT_MS, body: { period_code: period } });
  if (!result.ok) return failure(result.error, "generate", "generate seating plans");
  redirect(`/exams/duties/seating?period=${encodeURIComponent(period)}&seating=${result.data.seating_id}`);
}

export async function saveSeatingDraft(seatingId: string): Promise<FormState> {
  await requireExaminationAdmin();
  if (!RUN_PATTERN.test(seatingId)) return { status: "error", message: "This seating plan could not be identified. Generate it again." };
  const result = await dutyRequest<SeatingDraft>(`/seating/${seatingId}/save`, { method: "POST", timeoutMs: WORKFLOW_TIMEOUT_MS });
  if (!result.ok) return failure(result.error, "generate", "save seating plans");
  refresh();
  return { status: "success", message: `Seating plan saved as draft version ${result.data.version}.` };
}
