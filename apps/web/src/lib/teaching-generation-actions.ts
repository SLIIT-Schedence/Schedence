"use server";

import { redirect } from "next/navigation";
import { getAcademicContext } from "@/lib/academic";
import { checkReadiness, getGenerationInputs, parseScope, submitGeneration } from "@/lib/teaching-generation";
import { parseRulesSelection, serializeRules, wizardHref } from "@/lib/teaching-scheduling-rules";

export type GenerateState = { status: "idle" | "error"; message?: string };
export type RulesState = { status: "idle" | "error" | "valid"; message?: string; rulesKey?: string };

const field = (formData: FormData, name: string) => {
  const value = formData.get(name);
  return typeof value === "string" ? value : undefined;
};

/** Read-only backend validation. This action never submits a generation job or stores a snapshot. */
export async function reviewSchedulingRules(_previous: RulesState, formData: FormData): Promise<RulesState> {
  const context = await getAcademicContext();
  if (!context) return { status: "error", message: "Only Academic Admins can validate scheduling rules." };
  const parsed = parseRulesSelection(field(formData, "rules"));
  if (!parsed.ok) return { status: "error", message: parsed.error };
  const rulesKey = serializeRules(parsed.rules);
  const scope = parseScope({ semester: field(formData, "semester"), year: field(formData, "year"), stream: field(formData, "stream") });
  // The rules step reports the outcome itself, validating these exact rules while it renders. A result returned
  // as state would be lost: every Server Action here re-renders the page, because the auth proxy re-sets the
  // session cookie on each request and Next.js treats that as a cookie revalidation.
  const review = wizardHref("rules", scope, rulesKey, { validated: "1" });
  if (field(formData, "intent") !== "continue") redirect(review);
  const result = await getGenerationInputs(context.primary.id, scope.semester, scope.year, scope.stream, rulesKey);
  redirect(result.ok && result.data.ready ? wizardHref("readiness", scope, rulesKey) : review);
}

/**
 * The only place the wizard starts a solve, and only from an explicit Generate click. The institution comes
 * from the signed-in Academic Admin context and readiness is re-checked here, so a stale or edited form can
 * never submit a request the server would not build itself. The Teaching Service authorizes again.
 */
export async function startGeneration(_previous: GenerateState, formData: FormData): Promise<GenerateState> {
  const context = await getAcademicContext();
  if (!context) return { status: "error", message: "Only Academic Admins can generate teaching timetables." };
  const parsed = parseRulesSelection(field(formData, "rules"));
  if (!parsed.ok) return { status: "error", message: parsed.error };
  const rulesKey = serializeRules(parsed.rules);
  const scope = parseScope({ semester: field(formData, "semester"), year: field(formData, "year"), stream: field(formData, "stream") });
  const readiness = await checkReadiness(context.primary.id, scope.semester, scope.year, scope.stream, rulesKey);
  if (!readiness.ready || !readiness.body) {
    return { status: "error", message: "Readiness checks no longer pass. Go back to Check readiness to see why." };
  }
  const result = await submitGeneration(readiness.body);
  if (!result.ok) {
    const reasons: Record<string, string> = {
      GENERATION_DISABLED: "This deployment has no generation worker enabled, so the Teaching Service refused the request. Nothing was solved.",
      GENERATION_FORBIDDEN: "The Teaching Service did not accept your teaching:generate permission for this institution.",
      UNREACHABLE: "The teaching timetable service didn't respond. Nothing was submitted.",
    };
    return { status: "error", message: reasons[result.error.code] ?? `${result.error.message} (${result.error.code}).` };
  }
  redirect(wizardHref("generate", scope, rulesKey, { job: result.data.job_id }));
}
