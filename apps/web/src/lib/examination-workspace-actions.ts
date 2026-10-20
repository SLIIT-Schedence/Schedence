"use server";

import { createHmac, timingSafeEqual } from "node:crypto";
import { requireExaminationAdmin } from "@/lib/examination-access";
import { examinationRequest, type ExaminationResult } from "@/lib/examination-api";
import { listExamModules } from "@/lib/examination";
import { normalizeExclusionReason, periodEditErrors } from "@/lib/examination-calendar";
import { schedulingReview, seatingReview } from "@/lib/examination-settings";
import type { CycleModule, PeriodChange, PeriodChangeResult, PeriodDefinition, PeriodEdit, ReviewResult, SchedulingReview, SeatingReview } from "@/lib/examination-workspace-types";

const validCode = (value: unknown): value is string => typeof value === "string" && /^[A-Z0-9-]{1,32}$/.test(value);
const error = (message: string, status = 422, code = "INVALID_INPUT", fieldErrors?: Record<string, string>) => ({ ok: false as const, error: { status, code, message, ...(fieldErrors ? { fieldErrors } : {}) } });

const REGISTRATION_PAGE = 500;
const MAX_REPEAT_PAGES = 20;

/**
 * Repeat registrations per module, counted on the server from every page of
 * GET /registrations?registration_type=repeat. Only the counts leave the server (never student IDs).
 * null when any page fails or the list is unexpectedly long: the split is then shown as unavailable.
 */
async function repeatCounts(): Promise<Map<string, number> | null> {
  const counts = new Map<string, number>();
  for (let page = 0; page < MAX_REPEAT_PAGES; page++) {
    const result = await examinationRequest<{ items: { module_code: string; registration_type: string }[]; total: number }>(
      "/api/v1/registrations", { query: { registration_type: "repeat", limit: REGISTRATION_PAGE, offset: page * REGISTRATION_PAGE } });
    if (!result.ok) return null;
    for (const item of result.data.items) {
      if (item.registration_type !== "repeat") return null;
      counts.set(item.module_code, (counts.get(item.module_code) ?? 0) + 1);
    }
    if ((page + 1) * REGISTRATION_PAGE >= result.data.total) return counts;
  }
  return null;
}

export async function loadCycleReview(periodCode: string): Promise<ReviewResult> {
  await requireExaminationAdmin();
  if (!validCode(periodCode)) return error("Select a valid FINAL examination cycle.");
  const path = `/api/v1/exam-periods/${encodeURIComponent(periodCode)}`;
  const [period, mapping, modules, scheduling, seating, repeats, ...counts] = await Promise.all([
    examinationRequest<PeriodDefinition>(path),
    examinationRequest<CycleModule[]>(`${path}/modules`),
    listExamModules(),
    examinationRequest<SchedulingReview>("/api/v1/settings/scheduling", { query: { history_limit: 0 } }),
    examinationRequest<SeatingReview>("/api/v1/settings/exam-seating", { query: { history_limit: 0 } }),
    repeatCounts(),
    ...["DRAFT", "APPROVED", "PUBLISHED", "ARCHIVED"].map((status) =>
      examinationRequest<{ total: number }>("/api/v1/timetables", { query: { period_code: periodCode, status, limit: 1 } })),
  ]);
  if (!period.ok) return period;
  if (period.data.exam_kind !== "FINAL") return error("Only FINAL examination cycles are supported.");
  if (!mapping.ok) return mapping;
  if (!modules.ok) return modules;
  if (!scheduling.ok) return scheduling;
  if (!seating.ok) return seating;
  const ids = new Set(mapping.data.map((item) => item.module_code));
  const warnings: string[] = [];
  if (!ids.size) warnings.push("No modules are mapped to this cycle. Full-cycle generation needs an existing backend cycle mapping; this wizard does not create a partial selection.");
  if (mapping.data.some((item) => !modules.data.some((module) => module.module_code === item.module_code))) warnings.push("Some mapped modules have no catalog metadata. Those modules will be shown as unclassified.");
  const locked = counts.slice(1).some((item) => item.ok && item.data.total > 0) ? true
    : counts.slice(1).every((item) => item.ok) ? false : null;
  if (counts.some((item) => !item.ok)) warnings.push("Saved timetable counts could not all be read. Period locking is checked again by the backend during preview and save.");
  return { ok: true, data: {
    period: period.data, mapping: mapping.data,
    modules: modules.data.filter((module) => ids.has(module.module_code)).map((module) => ({
      module_code: module.module_code, module_name: module.module_name, academic_year: module.academic_year,
      semester: module.semester, specializations: module.specializations, candidate_count: module.candidate_count, delivery_mode: module.delivery_mode,
      // candidate_count counts every registration of the module, so normal = total - repeat is exact.
      repeat_candidates: repeats ? repeats.get(module.module_code) ?? 0 : null,
      normal_candidates: repeats ? module.candidate_count - (repeats.get(module.module_code) ?? 0) : null,
    })),
    scheduling: schedulingReview(scheduling.data), seating: seatingReview(seating.data),
    draft_count: counts[0].ok ? counts[0].data.total : null, locked, warnings,
  } };
}

function validateEdit(edit: PeriodEdit): boolean {
  const date = (value: unknown) => typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value) && Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0, 10) === value;
  return Boolean(edit && /^[a-f0-9]{64}$/.test(edit.expected_fingerprint) && date(edit.start_date) && date(edit.end_date)
    && edit.start_date <= edit.end_date && typeof edit.include_saturdays === "boolean" && typeof edit.include_sundays === "boolean"
    && Array.isArray(edit.exclusions) && edit.exclusions.length <= 120 && edit.exclusions.every((item) => item && date(item.excluded_date)
      && typeof item.reason === "string"));
}

async function updateBody(periodCode: string, edit: PeriodEdit): Promise<ExaminationResult<Record<string, unknown>>> {
  if (!validCode(periodCode) || !validateEdit(edit)) return error("Check the dates, excluded-date reasons and period version. Exclusion reasons need 3–120 characters.");
  const inputErrors = periodEditErrors(edit);
  if (Object.keys(inputErrors).length) return error(Object.values(inputErrors).join(" "), 422, "INVALID_INPUT", inputErrors);
  const current = await examinationRequest<PeriodDefinition>(`/api/v1/exam-periods/${encodeURIComponent(periodCode)}`);
  if (!current.ok) return current;
  const period = current.data;
  if (period.exam_kind !== "FINAL") return error("Only FINAL periods can be edited in this wizard.");
  if (period.fingerprint !== edit.expected_fingerprint) return error("The period changed since you loaded it. Reload the cycle before previewing again.", 409, "STALE_PERIOD");
  const calendarErrors = periodEditErrors(edit, period.academic_year_label);
  if (Object.keys(calendarErrors).length) return error(Object.values(calendarErrors).join(" "), 422, "INVALID_INPUT", calendarErrors);
  return { ok: true, data: {
    period_name: period.period_name, academic_year_label: period.academic_year_label, semester: period.semester, exam_kind: period.exam_kind,
    day_start_time: period.day_start_time, day_end_time: period.day_end_time,
    start_date: edit.start_date, end_date: edit.end_date, include_saturdays: edit.include_saturdays, include_sundays: edit.include_sundays,
    exclusions: edit.exclusions.map((item) => ({ excluded_date: item.excluded_date, reason: normalizeExclusionReason(item.reason) })), expected_fingerprint: edit.expected_fingerprint,
  } };
}

function signature(userId: string, code: string, body: unknown, timestamp: string): string {
  const secret = process.env.AUTH_SECRET;
  if (!secret) throw new Error("Missing server signing configuration");
  return createHmac("sha256", secret).update(JSON.stringify([userId, code, body, timestamp])).digest("hex");
}

export async function previewExamPeriod(periodCode: string, edit: PeriodEdit): Promise<PeriodChangeResult> {
  const account = await requireExaminationAdmin();
  const body = await updateBody(periodCode, edit);
  if (!body.ok) return body;
  const preview = await examinationRequest<PeriodChange>(`/api/v1/exam-periods/${encodeURIComponent(periodCode)}`, { method: "PUT", query: { dry_run: true }, body: body.data });
  if (!preview.ok) return preview;
  if (!preview.data.dry_run) return error("The service did not return a dry-run preview.", 502);
  try {
    const timestamp = String(Date.now());
    return { ok: true, data: { change: preview.data, preview_token: `${timestamp}.${signature(account.id, periodCode, body.data, timestamp)}` } };
  } catch { return error("A save confirmation could not be prepared. No date changes were requested to be saved.", 503); }
}

export async function saveExamPeriod(periodCode: string, edit: PeriodEdit, token: string, confirmed: boolean): Promise<PeriodChangeResult> {
  const account = await requireExaminationAdmin();
  if (confirmed !== true || typeof token !== "string") return error("Explicit confirmation of a validated preview is required.");
  const body = await updateBody(periodCode, edit);
  if (!body.ok) return body;
  const [timestamp, supplied, extra] = token.split(".");
  try {
    if (extra || !/^\d{13}$/.test(timestamp) || !/^[a-f0-9]{64}$/.test(supplied)
      || Date.now() - Number(timestamp) > 600_000 || Number(timestamp) > Date.now()
      || !timingSafeEqual(Buffer.from(supplied, "hex"), Buffer.from(signature(account.id, periodCode, body.data, timestamp), "hex"))) {
      return error("The preview expired or the proposed change differs. Validate a new preview before saving.");
    }
  } catch { return error("The preview could not be verified. Validate again before saving."); }
  // Recheck locks and validation immediately before the real update. The backend checks the fingerprint atomically.
  const path = `/api/v1/exam-periods/${encodeURIComponent(periodCode)}`;
  const preview = await examinationRequest<PeriodChange>(path, { method: "PUT", query: { dry_run: true }, body: body.data });
  if (!preview.ok) return preview;
  if (!preview.data.dry_run) return error("The service did not return a dry-run validation. Reload the cycle before continuing.", 502);
  const saved = await examinationRequest<PeriodChange>(path, { method: "PUT", query: { dry_run: false }, body: body.data });
  if (!saved.ok) return saved;
  if (saved.data.dry_run) return error("The service returned a preview instead of a saved period. Reload the cycle to verify its dates.", 502);
  return { ok: true, data: { change: saved.data } };
}
