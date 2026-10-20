import "server-only";
import { redirect } from "next/navigation";
import { auth } from "@/auth";
import { requireExaminationAdmin } from "@/lib/examination-access";

export type ExaminationProblem = { rule: string; message: string };
export type ExaminationApiError = {
  status: number; code: string; message: string; details?: unknown; fieldErrors?: Record<string, string>;
  /** Domain problems such as [{ rule: "STALE", message }]: the workflow's 409/422 answers. */
  problems?: ExaminationProblem[];
};
export type ExaminationResult<T> = { ok: true; data: T } | { ok: false; error: ExaminationApiError };
type Query = Record<string, string | number | boolean | undefined>;

const DEFAULT_TIMEOUT_MS = 15_000;
const SESSION_EXPIRED = "/login?error=SessionExpired&callbackUrl=%2Fexams";

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

/** FastAPI uses { detail: string | object | validationIssue[] }, unlike identity-service. */
function readError(status: number, body: unknown): ExaminationApiError {
  const fallback = { status, code: `HTTP_${status}`, message: `Examination Service request failed (HTTP ${status}).` };
  if (!record(body)) return fallback;
  const detail = body.detail;
  if (typeof detail === "string") return { ...fallback, message: detail };
  if (Array.isArray(detail)) {
    const fieldErrors: Record<string, string> = {};
    const messages = detail.filter(record).flatMap((issue) => {
      const rawMessage = typeof issue.msg === "string" ? issue.msg : issue.message;
      if (typeof rawMessage !== "string") return [];
      let message: string = rawMessage;
      const parts = Array.isArray(issue.loc) ? issue.loc.filter((part) => typeof part === "string" || typeof part === "number") : [];
      const reason = parts[0] === "body" && parts[1] === "exclusions" && typeof parts[2] === "number" && parts[3] === "reason";
      if (reason) {
        message = issue.type === "string_pattern_mismatch"
          ? "Use a single line of text without surrounding whitespace, tabs or control characters. Spaces between words are allowed."
          : "Enter an exclusion reason of 3–120 characters.";
      } else if (issue.type === "string_pattern_mismatch") {
        message = "Use the required format for this field.";
      }
      if (parts[0] === "body" && parts.length > 1) fieldErrors[parts.slice(1).join(".")] = message;
      const location = reason ? `Excluded date ${Number(parts[2]) + 1} reason` : parts.join(".");
      return [`${location ? `${location}: ` : ""}${message}`];
    });
    // Domain answers ({ rule, message }) keep their rule codes: the first one names the problem.
    const problems = detail.filter(record).flatMap((issue) =>
      typeof issue.rule === "string" && typeof issue.message === "string" ? [{ rule: issue.rule, message: issue.message }] : []);
    // Deliberately exclude rejected inputs, regex patterns and validation context.
    return { ...fallback, code: problems.length ? problems[0].rule : status === 422 ? "VALIDATION_ERROR" : fallback.code,
      message: messages.join("; ") || fallback.message,
      ...(Object.keys(fieldErrors).length ? { fieldErrors } : {}), ...(problems.length ? { problems } : {}) };
  }
  const envelope = record(detail) ? detail : record(body.error) ? body.error : body;
  return {
    ...fallback,
    code: typeof envelope.code === "string" ? envelope.code : fallback.code,
    message: typeof envelope.message === "string" ? envelope.message : fallback.message,
    details: envelope.details,
  };
}

/**
 * Authenticated, uncached server requests. No browser proxy or caller-supplied auth headers.
 * Long-running generation explicitly opts into a larger timeout; reads keep their 15-second default.
 */
export async function examinationRequest<T>(
  path: string,
  {
    method = "GET", query, body: requestBody, timeoutMs = DEFAULT_TIMEOUT_MS, sessionExpiredPath = SESSION_EXPIRED,
  }: {
    method?: "GET" | "POST" | "PUT";
    query?: Query;
    body?: unknown;
    timeoutMs?: number;
    sessionExpiredPath?: string;
  } = {},
): Promise<ExaminationResult<T>> {
  await requireExaminationAdmin();
  const session = await auth();
  if (!session?.accessToken || session.error) redirect(sessionExpiredPath);

  let url: URL;
  try {
    const base = (process.env.EXAMINATION_SERVICE_URL ?? "http://localhost:8003").replace(/\/+$/, "");
    url = new URL(`${base}${path}`);
    if (!path.startsWith("/api/v1/") || path.includes("..") || !["http:", "https:"].includes(url.protocol) || url.username || url.password) {
      throw new Error("Invalid Examination Service URL");
    }
    for (const [key, value] of Object.entries(query ?? {})) {
      if (value !== undefined) url.searchParams.set(key, String(value));
    }
    if (!Number.isSafeInteger(timeoutMs) || timeoutMs <= 0 || timeoutMs > 4_294_967_295) throw new Error("Invalid timeout");
  } catch {
    return { ok: false, error: { status: 0, code: "CONFIGURATION_ERROR", message: "The Examination Service connection is not configured correctly." } };
  }

  const signal = AbortSignal.timeout(timeoutMs);
  let response: Response;
  let body: unknown;
  try {
    response = await fetch(url, {
      method,
      headers: {
        Authorization: `Bearer ${session.accessToken}`,
        Accept: "application/json",
        ...(requestBody === undefined ? {} : { "Content-Type": "application/json" }),
      },
      body: requestBody === undefined ? undefined : JSON.stringify(requestBody),
      cache: "no-store",
      redirect: "error",
      signal,
    });
    // Read the body inside the timeout/network boundary as well.
    const content = await response.text();
    try {
      body = content ? JSON.parse(content) : undefined;
    } catch {
      if (response.ok) {
        return { ok: false, error: { status: response.status, code: "INVALID_RESPONSE", message: "The Examination Service returned an invalid response. Please try again." } };
      }
    }
  } catch {
    return {
      ok: false,
      error: signal.aborted
        ? { status: 0, code: "TIMEOUT", message: "The Examination Service took too long to respond. Please try again." }
        : { status: 0, code: "UNREACHABLE", message: "The Examination Service could not be reached. Check that it is running, then try again." },
    };
  }

  // Keep framework redirects outside catch blocks.
  if (response.status === 401) redirect(sessionExpiredPath);
  if (!response.ok) return { ok: false, error: readError(response.status, body) };
  if (body === undefined && response.status !== 204) {
    return { ok: false, error: { status: response.status, code: "INVALID_RESPONSE", message: "The Examination Service returned an empty response. Please try again." } };
  }
  return { ok: true, data: body as T };
}
