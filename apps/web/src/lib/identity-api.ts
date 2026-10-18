import "server-only";
import { redirect } from "next/navigation";
import { auth } from "@/auth";

/** identity-service's error envelope: {"error": {"code", "message", "details", "request_id"}}. */
export type ApiError = { status: number; code: string; message: string; details?: unknown };
export type ApiResult<T> = { ok: true; data: T } | { ok: false; error: ApiError };

type Query = Record<string, string | number | readonly string[] | undefined | null>;

const SESSION_EXPIRED = "/login?error=SessionExpired";
const UNREACHABLE: ApiError = { status: 0, code: "UNREACHABLE", message: "The identity service didn't respond." };

function baseUrl(): string {
  return (process.env.IDENTITY_SERVICE_URL ?? "http://localhost:8001").replace(/\/+$/, "");
}

/**
 * Calls identity-service as the signed-in user, from the server only. A missing or rejected access token
 * sends the user to /login; every other failure comes back as `{ ok: false }` for the caller to show.
 */
export async function identityRequest<T>(
  path: string,
  { method = "GET", query, body }: { method?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE"; query?: Query; body?: unknown } = {},
): Promise<ApiResult<T>> {
  const session = await auth();
  if (!session?.accessToken || session.error) redirect(SESSION_EXPIRED);

  const url = new URL(`${baseUrl()}${path}`);
  for (const [key, value] of Object.entries(query ?? {})) {
    if (Array.isArray(value)) {
      for (const item of value) url.searchParams.append(key, item);
    } else if (value !== undefined && value !== null && value !== "") {
      url.searchParams.set(key, String(value));
    }
  }

  let response: Response;
  try {
    response = await fetch(url, {
      method,
      headers: {
        Authorization: `Bearer ${session.accessToken}`,
        Accept: "application/json",
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
      signal: AbortSignal.timeout(15_000),
    });
  } catch {
    return { ok: false, error: UNREACHABLE };
  }

  if (response.status === 401) redirect(SESSION_EXPIRED);
  if (response.status === 204) return { ok: true, data: undefined as T };
  if (response.ok) return { ok: true, data: (await response.json()) as T };
  return { ok: false, error: await readError(response) };
}

async function readError(response: Response): Promise<ApiError> {
  try {
    const body = (await response.json()) as { error?: { code?: unknown; message?: unknown; details?: unknown } };
    return {
      status: response.status,
      code: typeof body.error?.code === "string" ? body.error.code : `HTTP_${response.status}`,
      message: typeof body.error?.message === "string" ? body.error.message : `Request failed (HTTP ${response.status}).`,
      details: body.error?.details,
    };
  } catch {
    return { status: response.status, code: `HTTP_${response.status}`, message: `Request failed (HTTP ${response.status}).` };
  }
}
