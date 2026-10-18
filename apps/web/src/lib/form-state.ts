import type { ApiError } from "@/lib/identity-api";

/** What a console dialog's server action returns. */
export type FormState = {
  status: "idle" | "success" | "error";
  /** On success: a short confirmation for the toast. On error: what went wrong. */
  message?: string;
  fieldErrors?: Record<string, string>;
};

const FIELD_HINTS: Record<string, string> = {
  code: "Use 2–30 letters, numbers, hyphens or underscores, starting with a letter.",
  name: "Enter a name of up to 200 characters.",
  email: "Enter a valid email address.",
  full_name: "Enter the person's full name (up to 200 characters).",
  reason: "Keep the reason under 500 characters.",
  role_code: "Choose a role.",
  institution_id: "Choose an institution.",
};

export const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function text(formData: FormData, name: string): string {
  const value = formData.get(name);
  return typeof value === "string" ? value.trim() : "";
}

export function invalid(fields: string[]): FormState {
  return {
    status: "error",
    message: "Check the highlighted fields.",
    fieldErrors: Object.fromEntries(fields.map((field) => [field, FIELD_HINTS[field] ?? "This value isn't valid."])),
  };
}

export function staleRecord(): FormState {
  return { status: "error", message: "This record no longer exists. Reload the page." };
}

/**
 * Turns identity-service's error envelope into a message for the dialog. `fieldForCode` pins an error code
 * to a field; `messages` replaces the backend's text for codes that need a friendlier explanation.
 */
export function formFailure(
  error: ApiError,
  { fieldForCode = {}, messages = {} }: { fieldForCode?: Record<string, string>; messages?: Record<string, string> } = {},
): FormState {
  if (error.code === "VALIDATION_ERROR") {
    const fields = ((error.details as { errors?: { loc?: unknown[] }[] } | undefined)?.errors ?? [])
      .map((entry) => entry.loc?.at(-1))
      .filter((field): field is string => typeof field === "string");
    if (fields.length) return invalid(fields);
  }
  const message = messages[error.code] ?? error.message;
  const field = fieldForCode[error.code];
  if (field) return { status: "error", message, fieldErrors: { [field]: message } };
  if (messages[error.code]) return { status: "error", message };

  if (error.status === 0 || error.status >= 500) {
    return { status: "error", message: "Schedence couldn't reach the identity service. Try again in a moment." };
  }
  if (error.status === 429) return { status: "error", message: "Too many changes in a short time. Wait a minute, then try again." };
  if (error.status === 403 && error.code === "FORBIDDEN") {
    return { status: "error", message: "Your account doesn't have permission to do this." };
  }
  if (error.status === 404) return staleRecord();
  // 409 and the remaining 403 codes carry messages written for people, e.g. "Another institution already uses this code."
  return { status: "error", message: error.message };
}
