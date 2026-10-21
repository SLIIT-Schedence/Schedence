import "server-only";
import { auth } from "@/auth";
import { redirect } from "next/navigation";
import { serviceRequest, serviceUrl, type ApiResult } from "@/lib/service-api";
import type { DistributionPage, DistributionResult, EligibleLecturer, RecipientPage, SendFormBody } from "@/lib/form-sending-contracts";

const SERVICE = { baseUrl: () => serviceUrl("TEACHING_SERVICE_URL", "http://localhost:8004"), unreachable: "The Teaching Service didn't respond." };
const ROOT = "/api/v1/teaching";
export const getFormRecipients = (institution: string) => serviceRequest<EligibleLecturer[]>(SERVICE, ROOT + "/form-recipients", { query: { institution_id: institution } });
export const getDistributions = (institution: string, page = 1) => serviceRequest<DistributionPage>(SERVICE, ROOT + "/form-distributions", { query: { institution_id: institution, page, page_size: 20 } });
export const getDistribution = (institution: string, id: string) => serviceRequest<DistributionResult>(SERVICE, `${ROOT}/form-distributions/${encodeURIComponent(id)}`, { query: { institution_id: institution } });
export const getDistributionRecipients = (institution: string, id: string, page = 1) => serviceRequest<RecipientPage>(SERVICE, `${ROOT}/form-distributions/${encodeURIComponent(id)}/recipients`, { query: { institution_id: institution, page, page_size: 20 } });

export async function sendSavedForm(id: string, body: SendFormBody, key: string): Promise<ApiResult<DistributionResult>> {
  // The existing generic client has no custom-header hook. Keep this keyed POST server-only
  // and use the same session, error envelope and service origin as that client.
  const session = await auth();
  if (!session?.accessToken || session.error) redirect("/login?error=SessionExpired");
  let response: Response;
  try {
    response = await fetch(`${SERVICE.baseUrl()}${ROOT}/form-templates/${encodeURIComponent(id)}/send`, {
      method: "POST", headers: { Authorization: `Bearer ${session.accessToken}`, Accept: "application/json", "Content-Type": "application/json", "Idempotency-Key": key },
      body: JSON.stringify(body), cache: "no-store", signal: AbortSignal.timeout(15000), redirect: "error",
    });
  } catch { return { ok: false, error: { status: 0, code: "UNREACHABLE", message: SERVICE.unreachable } }; }
  if (response.status === 401) redirect("/login?error=SessionExpired");
  if (response.ok) return { ok: true, data: await response.json() as DistributionResult };
  try {
    const result = await response.json() as { error?: { code?: unknown; message?: unknown } };
    return { ok: false, error: { status: response.status, code: typeof result.error?.code === "string" ? result.error.code : `HTTP_${response.status}`, message: typeof result.error?.message === "string" ? result.error.message : "Sending failed." } };
  } catch { return { ok: false, error: { status: response.status, code: `HTTP_${response.status}`, message: "Sending failed." } }; }
}
