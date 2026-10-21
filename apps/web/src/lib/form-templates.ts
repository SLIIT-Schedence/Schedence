import "server-only";
import { type ApiResult, type RequestOptions, serviceRequest, serviceUrl } from "@/lib/service-api";
import type { CreateDraft, DraftFormPage, DraftFormTemplate, FormPreset, SaveDraft } from "@/lib/form-template-contracts";

const SERVICE = { baseUrl: () => serviceUrl("TEACHING_SERVICE_URL", "http://localhost:8004"), unreachable: "The Teaching Service didn't respond." };
const ROOT = "/api/v1/teaching/form-templates";
function request<T>(path: string, options?: RequestOptions): Promise<ApiResult<T>> {
  return serviceRequest<T>(SERVICE, ROOT + path, options);
}
export const getFormPresets = (institution: string) => request<FormPreset[]>("/presets", { query: { institution_id: institution } });
export const listFormTemplates = (institution: string, page = 1) => request<DraftFormPage>("", { query: { institution_id: institution, page, page_size: 20 } });
export const getFormTemplate = (institution: string, id: string) => request<DraftFormTemplate>(`/${encodeURIComponent(id)}`, { query: { institution_id: institution } });
export const createFormTemplate = (body: CreateDraft) => request<DraftFormTemplate>("", { method: "POST", body });
export const saveFormTemplate = (id: string, body: SaveDraft) => request<DraftFormTemplate>(`/${encodeURIComponent(id)}`, { method: "PUT", body });
export const duplicateFormTemplate = (institution: string, id: string) => request<DraftFormTemplate>(`/${encodeURIComponent(id)}/duplicate`, { method: "POST", body: { institution_id: institution } });
