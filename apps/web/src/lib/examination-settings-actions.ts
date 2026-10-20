"use server";

import { requireExaminationAdmin } from "@/lib/examination-access";
import { examinationRequest, type ExaminationResult } from "@/lib/examination-api";
import {
  changedSettings, NUMBER_FIELDS, reasonError, schedulingReview, seatingPercentage, seatingReview, settingsDocument, settingsErrors,
  type SettingsDocument, type SettingsExpected, type SettingsForm, type SettingsPreview, type SettingsSaved,
} from "@/lib/examination-settings";
import type { SchedulingReview, SeatingReview } from "@/lib/examination-workspace-types";

// Existing endpoints only: PUT /settings/scheduling (complete document) and PUT /settings/exam-seating/default.
// Both need examinations:manage and the version last read; venue overrides are never changed here.
const SCHEDULING = "/api/v1/settings/scheduling";
const SEATING = "/api/v1/settings/exam-seating";
type SchedulingChange = { dry_run: boolean; changed: boolean; changed_fields: string[]; version: number };
type SeatingChange = { dry_run: boolean; changed: boolean; old_percentage: number | null; new_percentage: number | null;
  affected_venues: unknown[]; newly_unavailable_venues: string[] };
type Prepared = { scheduling: SchedulingReview; seating: SeatingReview; document: SettingsDocument; fields: string[]; percentage: number | null; reason: string };

const fail = (status: number, code: string, message: string, fieldErrors?: Record<string, string>) =>
  ({ ok: false as const, error: { status, code, message, ...(fieldErrors ? { fieldErrors } : {}) } });

function isForm(value: unknown): value is SettingsForm {
  if (value === null || typeof value !== "object") return false;
  const form = value as Record<string, unknown>;
  return Object.keys(NUMBER_FIELDS).every((key) => typeof form[key] === "string") && typeof form.seating_percentage === "string"
    && Array.isArray(form.permitted_start_times) && form.permitted_start_times.length <= 48
    && typeof form.fairness_enabled === "boolean" && typeof form.protect_worst_student === "boolean";
}
const isVersion = (value: unknown) => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;

/** Re-read the saved settings, check the loaded versions, and build the complete document from the saved one. */
async function prepare(form: unknown, expected: SettingsExpected, reason: unknown): Promise<ExaminationResult<Prepared>> {
  if (!isForm(form) || !expected || !isVersion(expected.scheduling_version) || !isVersion(expected.seating_version)) {
    return fail(422, "INVALID_INPUT", "The settings form could not be read. Reload the examination and try again.");
  }
  const [scheduling, seating] = await Promise.all([
    examinationRequest<SchedulingReview>(SCHEDULING, { query: { history_limit: 0 } }),
    examinationRequest<SeatingReview>(SEATING, { query: { history_limit: 0 } }),
  ]);
  if (!scheduling.ok) return scheduling;
  if (!seating.ok) return seating;
  const current = { scheduling: schedulingReview(scheduling.data), seating: seatingReview(seating.data) };
  const errors = settingsErrors(form, { min: current.seating.min_percentage, max: current.seating.max_percentage });
  const reasonProblem = reasonError(reason);
  if (reasonProblem) errors.reason = reasonProblem;
  if (Object.keys(errors).length) return fail(422, "INVALID_INPUT", "Correct the highlighted settings and try again.", errors);
  if (current.scheduling.version !== expected.scheduling_version || current.seating.default.version !== expected.seating_version) {
    return fail(409, "STALE_SETTINGS", "The settings were changed by someone else after you loaded them. Nothing was saved. Reload the examination to see the current values.");
  }
  const document = settingsDocument(current.scheduling.settings, form);
  const fields = changedSettings(current.scheduling.settings, document);
  const percentage = seatingPercentage(form) === current.seating.default.percentage ? null : seatingPercentage(form);
  if (!fields.length && percentage === null) return fail(422, "NO_CHANGES", "Nothing has changed. There is nothing to save.");
  return { ok: true, data: { ...current, document, fields, percentage, reason: (reason as string).trim() } };
}

const schedulingPut = (p: Prepared, dryRun: boolean) => examinationRequest<SchedulingChange>(SCHEDULING, {
  method: "PUT", query: { dry_run: dryRun, ...(p.scheduling.status === "invalid" ? { replace_invalid: true } : {}) },
  body: { settings: p.document, reason: p.reason, expected_version: p.scheduling.version },
});
const seatingPut = (p: Prepared, dryRun: boolean) => examinationRequest<SeatingChange>(`${SEATING}/default`, {
  method: "PUT", query: { dry_run: dryRun }, body: { percentage: p.percentage, reason: p.reason, expected_version: p.seating.default.version },
});
async function count(status: "DRAFT" | "APPROVED"): Promise<number | null> {
  const result = await examinationRequest<{ total: number }>("/api/v1/timetables", { query: { status, limit: 1 } });
  return result.ok && Number.isSafeInteger(result.data.total) ? result.data.total : null;
}

/** Validate the change with the backend (dry run). Nothing is saved. */
export async function previewSettingsChange(form: SettingsForm, expected: SettingsExpected, reason: string): Promise<ExaminationResult<SettingsPreview>> {
  await requireExaminationAdmin();
  const prepared = await prepare(form, expected, reason);
  if (!prepared.ok) return prepared;
  const p = prepared.data;
  const [scheduling, seating, drafts, approved] = await Promise.all([
    p.fields.length ? schedulingPut(p, true) : null, p.percentage !== null ? seatingPut(p, true) : null, count("DRAFT"), count("APPROVED"),
  ]);
  if (scheduling && !scheduling.ok) return scheduling;
  if (seating && !seating.ok) return seating;
  if ((scheduling && !scheduling.data.dry_run) || (seating && !seating.data.dry_run)) return fail(502, "INVALID_RESPONSE", "The service did not return a dry-run preview. Reload the examination to check the saved settings.");
  return { ok: true, data: {
    scheduling_fields: scheduling ? scheduling.data.changed_fields : [],
    seating: seating ? { old_percentage: p.seating.default.percentage, new_percentage: p.percentage ?? p.seating.default.percentage,
      affected_venues: seating.data.affected_venues.length, newly_unavailable_venues: seating.data.newly_unavailable_venues } : null,
    drafts, approved, replace_invalid: p.scheduling.status === "invalid",
  } };
}

/** Save a previewed change after explicit confirmation, then return the reloaded settings. */
export async function saveSettingsChange(form: SettingsForm, expected: SettingsExpected, reason: string, confirmed: boolean): Promise<ExaminationResult<SettingsSaved>> {
  await requireExaminationAdmin();
  if (confirmed !== true) return fail(422, "CONFIRMATION_REQUIRED", "Confirm that these institution-wide changes may be saved.");
  const prepared = await prepare(form, expected, reason);
  if (!prepared.ok) return prepared;
  const p = prepared.data;
  const saved: SettingsSaved["saved"] = [];
  let problem: string | null = null;
  if (p.fields.length) {
    const result = await schedulingPut(p, false);
    if (!result.ok) return result;
    if (result.data.dry_run) return fail(502, "INVALID_RESPONSE", "The service returned a preview instead of saving. Reload the examination to check the saved settings.");
    saved.push("scheduling");
  }
  if (p.percentage !== null) {
    const result = await seatingPut(p, false);
    if (!result.ok && !saved.length) return result;
    if (!result.ok || result.data.dry_run) {
      problem = `The scheduling settings were saved, but the default seating change was not${result.ok ? "" : ` (HTTP ${result.error.status}: ${result.error.message})`}. Review the seating value and save it again.`;
    } else saved.push("seating");
  }
  const [scheduling, seating] = await Promise.all([
    examinationRequest<SchedulingReview>(SCHEDULING, { query: { history_limit: 0 } }),
    examinationRequest<SeatingReview>(SEATING, { query: { history_limit: 0 } }),
  ]);
  return { ok: true, data: { saved, problem,
    scheduling: scheduling.ok ? schedulingReview(scheduling.data) : null, seating: seating.ok ? seatingReview(seating.data) : null } };
}
