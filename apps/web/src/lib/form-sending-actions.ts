"use server";

import { revalidatePath } from "next/cache";
import { getAcademicContext } from "@/lib/academic";
import { isUuid } from "@/lib/identity-records";
import type { ApiResult } from "@/lib/service-api";
import { validSendBody, type DistributionResult, type EligibleLecturer, type SendFormBody } from "@/lib/form-sending-contracts";
import { getFormRecipients, sendSavedForm } from "@/lib/form-sending";

const denied = <T>(): ApiResult<T> => ({ ok: false, error: { status: 403, code: "FORM_BUILDER_FORBIDDEN", message: "Form access is not permitted for this institution." } });
async function allowed(institution: string) {
  if (!isUuid(institution)) return false;
  const context = await getAcademicContext();
  return !!context?.institutions.some((row) => row.id === institution && row.status === "ACTIVE");
}
export async function refreshEligibleLecturers(institution: string): Promise<ApiResult<EligibleLecturer[]>> {
  if (!await allowed(institution)) return denied();
  return getFormRecipients(institution);
}
export async function sendFormDraft(id: string, body: SendFormBody, key: string): Promise<ApiResult<DistributionResult>> {
  if (!validSendBody(body) || !isUuid(id) || typeof key !== "string" || !/^[A-Za-z0-9._:-]{1,128}$/.test(key)) {
    return { ok: false, error: { status: 422, code: "VALIDATION_ERROR", message: "Review the audience and deadline before sending." } };
  }
  if (!await allowed(body.institution_id)) return denied();
  const result = await sendSavedForm(id, body, key);
  if (result.ok) revalidatePath("/academic/forms", "layout");
  return result;
}
