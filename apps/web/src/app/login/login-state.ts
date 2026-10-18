import { safeRedirectPath } from "@/lib/safe-redirect";

export type LoginSearchParams = Promise<Record<string, string | string[] | undefined>>;

export type LoginState = {
  /** Shown above the sign-in button; undefined when there is nothing to report. */
  errorMessage?: string;
  callbackUrl: string;
};

// Keys are Auth.js error types plus SessionExpired, which src/auth.ts and src/lib/identity.ts send.
const ERROR_MESSAGES = new Map<string, string>([
  ["SessionExpired", "Your session has ended. Sign in again to continue."],
  ["AccessDenied", "Sign-in was cancelled or not allowed. Try again, or ask your Schedence administrator for help."],
  ["Configuration", "Sign-in isn't set up correctly on this server. Contact your Schedence administrator."],
]);
const FALLBACK_ERROR = "Sign-in didn't finish. Try again.";

const first = (value: string | string[] | undefined) => (Array.isArray(value) ? value[0] : value);

export async function readLoginState(searchParams: LoginSearchParams): Promise<LoginState> {
  const params = await searchParams;
  const error = first(params.error);
  return {
    errorMessage: error ? (ERROR_MESSAGES.get(error) ?? FALLBACK_ERROR) : undefined,
    callbackUrl: safeRedirectPath(first(params.callbackUrl)),
  };
}
