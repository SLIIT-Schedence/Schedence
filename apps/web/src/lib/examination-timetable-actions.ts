"use server";

import { refresh } from "next/cache";
import { requireExaminationAdmin } from "@/lib/examination-access";
import { examinationRequest, type ExaminationApiError } from "@/lib/examination-api";
import { normalizeReason, reasonError, redactStudentIds } from "@/lib/examination-timetables";
import type { FormState } from "@/lib/form-state";

// Approve and publish a saved FINAL timetable. The backend is the authority: it checks the permission
// (examinations:approve), the status, expected_revision, stored content and stale inputs in one transaction.
// These actions only validate the form, send exactly what the admin confirmed and phrase the answer.

const WORKFLOW_TIMEOUT_MS = 120_000;
const MAX_ID = 2_147_483_647;
type Action = "approve" | "publish";
type StatusChange = {
  timetable: { revision: number; draft_number: number; status: string };
  archived_timetable: { draft_number: number } | null;
};

const validId = (value: unknown): value is number => Number.isSafeInteger(value) && (value as number) >= 1 && (value as number) <= MAX_ID;
const field = (formData: FormData, name: string) => {
  const value = formData.get(name);
  return typeof value === "string" ? value : "";
};
const login = (id: number) => `/login?error=SessionExpired&callbackUrl=${encodeURIComponent(`/exams/timetables/${id}`)}`;

function workflowFailure(error: ExaminationApiError, action: Action): FormState {
  const verb = action === "approve" ? "approve" : "publish";
  const done = action === "approve" ? "approved" : "published";
  const details = (error.problems ?? []).slice(1, 5).map((problem) => `${problem.rule}: ${redactStudentIds(problem.message)}`);
  if (error.status === 0) {
    return { status: "error", message: "The Examination Service did not answer. Reload the page to see whether the change was made before trying again." };
  }
  if (error.status === 403) return { status: "error", message: `Your account needs the examinations:approve permission to ${verb} timetables.` };
  if (error.status === 404) return { status: "error", message: "This timetable no longer exists in your institution. Reload the timetable list." };
  if (error.status === 429) return { status: "error", message: "The Examination Service is busy. Wait a moment, then try again." };
  if (error.status >= 500) return { status: "error", message: "The Examination Service is temporarily unavailable. Nothing was changed. Try again later." };
  if (error.status === 422) {
    const reason = error.fieldErrors?.reason;
    return reason ? { status: "error", message: "Check the reason.", fieldErrors: { reason: redactStudentIds(reason) } }
      : { status: "error", message: redactStudentIds(error.message) };
  }
  if (error.status === 409) {
    if (error.problems?.some((problem) => problem.rule === "X1" || problem.rule === "X2")) {
      return { status: "error", message: `This timetable conflicts with an approved or published timetable of another examination cycle, so it was not ${done}. `
        + `Generate and save a new draft to work around the protected exams. ${details.join(" · ")}` };
    }
    switch (error.code) {
      case "STALE":
        return { status: "error", message: `This timetable no longer matches the current inputs or settings, so it was not ${done}. `
          + `Generate and save a new draft, then approve that one.${details.length ? ` Changes found: ${details.join(" · ")}` : ""}` };
      case "REVISION":
        return { status: "error", message: "This timetable changed since you opened it. Close this dialog and reload the page." };
      case "ALREADY_PUBLISHED":
        return { status: "error", message: "Another timetable of this cycle is already published. Reload the page, then choose whether to archive it when you publish." };
      default:
        return { status: "error", message: redactStudentIds(error.message) };
    }
  }
  return { status: "error", message: redactStudentIds(error.message) };
}

function checkedReason(formData: FormData): { reason: string } | { state: FormState } {
  const reason = normalizeReason(field(formData, "reason"));
  const problem = reasonError(reason);
  return problem ? { state: { status: "error", message: "Check the reason.", fieldErrors: { reason: problem } } } : { reason };
}

/** DRAFT -> APPROVED. Never publishes. */
export async function approveSavedTimetable(timetableId: number, expectedRevision: number, _previous: FormState, formData: FormData): Promise<FormState> {
  await requireExaminationAdmin();
  if (!validId(timetableId) || !validId(expectedRevision)) return { status: "error", message: "This timetable could not be identified. Reload the page." };
  const checked = checkedReason(formData);
  if ("state" in checked) return checked.state;
  const result = await examinationRequest<StatusChange>(`/api/v1/timetables/${timetableId}/approve`, {
    method: "POST", timeoutMs: WORKFLOW_TIMEOUT_MS, sessionExpiredPath: login(timetableId),
    body: { expected_revision: expectedRevision, reason: checked.reason },
  });
  if (!result.ok) return workflowFailure(result.error, "approve");
  refresh();
  return { status: "success", message: `Draft #${result.data.timetable.draft_number} approved. It is not published yet.` };
}

/** APPROVED -> PUBLISHED, archiving the cycle's published timetable only when the admin chose to. */
export async function publishSavedTimetable(timetableId: number, expectedRevision: number, _previous: FormState, formData: FormData): Promise<FormState> {
  await requireExaminationAdmin();
  if (!validId(timetableId) || !validId(expectedRevision)) return { status: "error", message: "This timetable could not be identified. Reload the page." };
  const checked = checkedReason(formData);
  const confirmed = field(formData, "confirm").trim() === "PUBLISH";
  if ("state" in checked || !confirmed) {
    return {
      status: "error", message: "Check the highlighted fields.",
      fieldErrors: {
        ...("state" in checked ? checked.state.fieldErrors : {}),
        ...(confirmed ? {} : { confirm: "Type PUBLISH in capital letters to confirm." }),
      },
    };
  }
  const replacePublished = field(formData, "replace_published") === "archive";
  const result = await examinationRequest<StatusChange>(`/api/v1/timetables/${timetableId}/publish`, {
    method: "POST", timeoutMs: WORKFLOW_TIMEOUT_MS, sessionExpiredPath: login(timetableId),
    body: { expected_revision: expectedRevision, reason: checked.reason, replace_published: replacePublished },
  });
  if (!result.ok) return workflowFailure(result.error, "publish");
  refresh();
  const archived = result.data.archived_timetable;
  return {
    status: "success",
    message: `Draft #${result.data.timetable.draft_number} published.${archived ? ` Draft #${archived.draft_number} was archived.` : ""}`,
  };
}
