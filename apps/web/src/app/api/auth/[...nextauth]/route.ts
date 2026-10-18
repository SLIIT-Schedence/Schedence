import type { NextRequest } from "next/server";
import { handlers } from "@/auth";

export const { POST } = handlers;

/** Same as Auth.js, except the session endpoint never sends the Cognito access token to the browser. */
export async function GET(request: NextRequest) {
  const response = await handlers.GET(request);
  if (!request.nextUrl.pathname.endsWith("/session") || !response.ok) {
    return response;
  }

  const session: unknown = await response.json();
  if (session && typeof session === "object") {
    delete (session as { accessToken?: unknown }).accessToken;
  }
  const headers = new Headers(response.headers);
  headers.delete("content-length");
  return new Response(JSON.stringify(session), { status: response.status, headers });
}
