import type { DraftFormTemplate } from "./form-template-contracts";

export type AudienceKind = "SELECTED" | "DEPARTMENT" | "ALL_ELIGIBLE";
export type SendChannel = "DIRECT" | "SHARE_LINK";
export type FormAudience = { kind: "SELECTED"; staff_ids: string[] } | { kind: "DEPARTMENT"; department_id: string } | { kind: "ALL_ELIGIBLE" };
export type EligibleLecturer = { staff_id: string; staff_number: string; full_name: string; department_ids: string[];
  departments?: { id: string; name: string }[]; identity_user_id: string | null; link_status: "READY" | "NO_LINK" | "AMBIGUOUS_LINK" };
export type SendFormBody = { institution_id: string; expected_revision: number; channel: SendChannel; audience: FormAudience; deadline: string };
export type FormDistribution = { id: string; institution_id: string; channel: SendChannel; audience: FormAudience; deadline: string;
  created_at: string; created_by: string; frozen_template: DraftFormTemplate; template_sha256: string;
  recipient_count: number; unlinked_count: number; ambiguous_count: number; schema_version: 1 };
export type DistributionResult = { distribution: FormDistribution; status: "OPEN" | "CLOSED"; share_url: string | null; submissions_enabled: false };
export type DistributionPage = { items: DistributionResult[]; total: number; page: number; page_size: number };
export type FormRecipient = EligibleLecturer & { first_opened_at: string | null };
export type RecipientPage = { items: FormRecipient[]; total: number; page: number; page_size: number };
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const validId = (value: unknown): value is string => typeof value === "string" && UUID.test(value) && value !== "00000000-0000-0000-0000-000000000000";

export const sendingHref = (institution: string, form: string) => `/academic/forms/${encodeURIComponent(form)}/send?institution=${encodeURIComponent(institution)}`;
export const sentFormsHref = (institution: string, issue?: string, page?: number) => `/academic/forms/sent${issue ? `/${encodeURIComponent(issue)}` : ""}?institution=${encodeURIComponent(institution)}${page && page > 1 ? `&page=${page}` : ""}`;
export const localDeadline = (date: Date) => new Date(date.getTime() + 330 * 60000).toISOString().slice(0, 16);
export function deadlineIso(value: string): string | null {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value)) return null;
  const date = new Date(`${value}:00+05:30`);
  if (!Number.isFinite(date.getTime()) || localDeadline(date) !== value) return null;
  return date.toISOString();
}
export function departmentOptions(lecturers: EligibleLecturer[]) {
  const names = new Map<string, string>();
  for (const lecturer of lecturers) for (const department of lecturer.departments ?? []) names.set(department.id, department.name);
  return [...new Set(lecturers.flatMap((row) => row.department_ids))].map((id) => ({ id, name: names.get(id) ?? `Name unavailable (${id.slice(0, 8)})` }))
    .sort((a, b) => a.name.localeCompare(b.name) || a.id.localeCompare(b.id));
}
export function audienceRecipients(lecturers: EligibleLecturer[], kind: AudienceKind, selected: string[], department: string) {
  if (kind === "SELECTED") { const ids = new Set(selected); return lecturers.filter((row) => ids.has(row.staff_id)); }
  if (kind === "DEPARTMENT") return lecturers.filter((row) => row.department_ids.includes(department));
  return lecturers;
}
export function recipientStatus(row: FormRecipient) {
  if (row.link_status === "NO_LINK") return "Account required";
  if (row.link_status === "AMBIGUOUS_LINK") return "Account link needs review";
  return row.first_opened_at ? "Opened" : "Pending access";
}
export function prepareSend(template: DraftFormTemplate, lecturers: EligibleLecturer[], kind: AudienceKind,
  selected: string[], department: string, channel: SendChannel, deadline: string, now = Date.now()) {
  const errors: Record<string, string> = {};
  const iso = deadlineIso(deadline);
  if (!iso || Date.parse(iso) <= now) errors.deadline = "Choose a future deadline in Sri Lanka time.";
  const recipients = audienceRecipients(lecturers, kind, selected, department);
  if (kind === "SELECTED" && (!selected.length || new Set(selected).size !== selected.length || selected.some((id) => !lecturers.some((row) => row.staff_id === id)))) {
    errors.audience = "Select eligible lecturers. Clear any selections that are no longer available.";
  }
  if (kind === "DEPARTMENT" && !departmentOptions(lecturers).some((row) => row.id === department)) errors.audience = "Select an available department.";
  if (!recipients.length) errors.audience ??= "No eligible lecturers match this audience.";
  if (recipients.length > 10000) errors.audience = "Choose at most 10,000 recipients.";
  if (!template.questions.length) errors.form = "Add and save at least one question before sending.";
  const audience: FormAudience = kind === "SELECTED" ? { kind, staff_ids: [...selected].sort() } : kind === "DEPARTMENT" ? { kind, department_id: department } : { kind: "ALL_ELIGIBLE" };
  const body: SendFormBody = { institution_id: template.institution_id, expected_revision: template.revision, audience, channel, deadline: iso ?? "" };
  return { body, recipients, errors, valid: !Object.keys(errors).length };
}
// Server Actions are public entry points; check runtime input as well as TypeScript callers.
export function validSendBody(body: unknown): body is SendFormBody {
  if (!body || typeof body !== "object") return false;
  const b = body as SendFormBody;
  if (!validId(b.institution_id) || !Number.isSafeInteger(b.expected_revision) || b.expected_revision < 1 || !["DIRECT", "SHARE_LINK"].includes(b.channel)) return false;
  if (typeof b.deadline !== "string" || !/(Z|[+-]\d{2}:\d{2})$/.test(b.deadline) || !Number.isFinite(Date.parse(b.deadline))) return false;
  if (!b.audience || typeof b.audience !== "object") return false;
  const a = b.audience;
  if (a.kind === "SELECTED") return Array.isArray(a.staff_ids) && a.staff_ids.length > 0 && a.staff_ids.length <= 10000 && a.staff_ids.every(validId) && new Set(a.staff_ids).size === a.staff_ids.length;
  if (a.kind === "DEPARTMENT") return validId(a.department_id);
  return a.kind === "ALL_ELIGIBLE";
}
export function sendingError(error: { status: number; code: string; message: string }) {
  if (error.code === "REVISION_CONFLICT") return "The saved form changed. Load the latest draft, review it, then send again. Your recipient choices and deadline are kept.";
  if (error.code === "IDEMPOTENCY_CONFLICT") return "This sending request was already used with different settings. Review sent forms before starting another request.";
  if (error.status === 403) return "You do not have permission to send forms for this institution.";
  if (error.status === 0 || error.status >= 500) return "Sending could not be confirmed. Keep these selections and retry to safely check the same request, or review sent forms.";
  return error.message;
}
