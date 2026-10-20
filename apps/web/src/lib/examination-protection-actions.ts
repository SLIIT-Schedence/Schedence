"use server";

import { requireExaminationAdmin } from "@/lib/examination-access";
import { examinationRequest, type ExaminationResult } from "@/lib/examination-api";
import type { Page, SavedTimetable, SavedTimetableDetail } from "@/lib/examination";
import { protectionPreview, type ProtectionPreview } from "@/lib/examination-protection";

/** Existing read endpoints only, with bounded requests. The generation service remains authoritative. */
export async function loadProtectionPreview(periodCode: string, start: string, end: string,
  minimumBreak: number): Promise<ExaminationResult<ProtectionPreview>> {
  await requireExaminationAdmin();
  const date = (value: string) => /^\d{4}-\d{2}-\d{2}$/.test(value)
    && Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0, 10) === value;
  if (!/^[A-Z0-9-]{1,32}$/.test(periodCode) || !date(start) || !date(end) || end < start
    || !Number.isInteger(minimumBreak) || minimumBreak < 0 || minimumBreak > 1440) {
    return { ok: false, error: { status: 422, code: "VALIDATION_ERROR", message: "Review the selected dates and minimum break before loading protected timetables." } };
  }
  // Include MID cycles too: the backend protects all other approved/published examination cycles.
  const pages = await Promise.all((["APPROVED", "PUBLISHED"] as const).map((status) =>
    examinationRequest<Page<SavedTimetable>>("/api/v1/timetables", { query: { status, limit: 50, offset: 0 } })));
  for (const page of pages) if (!page.ok) return page;
  const headers = pages.flatMap((page) => page.ok ? page.data.items : [])
    .filter((t) => t.period_code !== periodCode)
    .sort((a, b) => b.created_at.localeCompare(a.created_at) || b.timetable_id - a.timetable_id);
  let incomplete = pages.some((page) => page.ok && page.data.total > page.data.items.length) || headers.length > 25;
  const details: SavedTimetableDetail[] = [];
  // At most 25 detail reads, five at a time. Never request /seats or registration records.
  const selected = headers.slice(0, 25);
  for (let offset = 0; offset < selected.length; offset += 5) {
    const batch = await Promise.all(selected.slice(offset, offset + 5).map((t) =>
      examinationRequest<SavedTimetableDetail>(`/api/v1/timetables/${t.timetable_id}`)));
    for (const item of batch) {
      if (item.ok) details.push(item.data);
      else incomplete = true;
    }
  }
  return { ok: true, data: protectionPreview(details, periodCode, start, end, minimumBreak, incomplete) };
}
