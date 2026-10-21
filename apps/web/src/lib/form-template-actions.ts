"use server";

import { revalidatePath } from "next/cache";
import { getAcademicContext } from "@/lib/academic";
import { isUuid } from "@/lib/identity-records";
import type { ApiResult } from "@/lib/service-api";
import type { CreateDraft, DraftFormTemplate, SaveDraft } from "@/lib/form-template-contracts";
import { createFormTemplate, duplicateFormTemplate, getFormTemplate, saveFormTemplate } from "@/lib/form-templates";

const forbidden = (): ApiResult<DraftFormTemplate> => ({ ok: false, error: { status: 403, code: "FORM_BUILDER_FORBIDDEN", message: "Form access is not permitted for this institution." } });
async function allowed(institution: string) {
  if (!isUuid(institution)) return false;
  const context = await getAcademicContext();
  return !!context?.institutions.some((row) => row.id === institution && row.status === "ACTIVE");
}
function refreshed(result: ApiResult<DraftFormTemplate>) {
  if (result.ok) revalidatePath("/academic/forms", "layout");
  return result;
}

export async function createDraftForm(body: CreateDraft): Promise<ApiResult<DraftFormTemplate>> {
  if (!body || !await allowed(body.institution_id)) return forbidden();
  return refreshed(await createFormTemplate(body));
}
export async function saveDraftForm(id: string, body: SaveDraft): Promise<ApiResult<DraftFormTemplate>> {
  if (!body || !isUuid(id) || !await allowed(body.institution_id)) return forbidden();
  return refreshed(await saveFormTemplate(id, body));
}
export async function duplicateDraftForm(institution: string, id: string): Promise<ApiResult<DraftFormTemplate>> {
  if (!isUuid(id) || !await allowed(institution)) return forbidden();
  return refreshed(await duplicateFormTemplate(institution, id));
}
export async function reloadDraftForm(institution: string, id: string): Promise<ApiResult<DraftFormTemplate>> {
  if (!isUuid(id) || !await allowed(institution)) return forbidden();
  return getFormTemplate(institution, id);
}
