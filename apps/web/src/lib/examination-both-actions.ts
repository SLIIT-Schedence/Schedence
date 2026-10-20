"use server";

import { requireExaminationAdmin } from "@/lib/examination-access";
import { loadCycleReview } from "@/lib/examination-workspace-actions";
import type { ExaminationResult } from "@/lib/examination-api";
import type { CycleReview } from "@/lib/examination-workspace-types";

/** One action loads both reviews; the browser never receives registration IDs or access tokens. */
export async function loadBothCycleReviews(codes: [string, string]): Promise<ExaminationResult<[CycleReview, CycleReview]>> {
  await requireExaminationAdmin();
  if (!Array.isArray(codes) || codes.length !== 2 || new Set(codes).size !== 2
    || codes.some((code) => typeof code !== "string" || !/^[A-Z0-9-]{1,32}$/.test(code))) {
    return { ok: false, error: { status: 422, code: "INVALID_INPUT", message: "Select two distinct FINAL examination cycles." } };
  }
  const responses = await Promise.all(codes.map(loadCycleReview));
  for (const response of responses) if (!response.ok) return response;
  const reviews = responses.flatMap((response) => response.ok ? [response.data] : []).sort((a, b) => a.period.semester - b.period.semester);
  if (reviews[0].period.semester !== 1 || reviews[1].period.semester !== 2
    || reviews[0].period.academic_year_label !== reviews[1].period.academic_year_label
    || reviews[0].scheduling.fingerprint !== reviews[1].scheduling.fingerprint
    || reviews[0].seating.ruleset_version !== reviews[1].seating.ruleset_version) {
    return { ok: false, error: { status: 409, code: "STALE", message: "Choose one FINAL cycle per semester in the same academic year. Reload if shared settings changed." } };
  }
  return { ok: true, data: [reviews[0], reviews[1]] };
}
