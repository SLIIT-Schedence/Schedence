import type { IconName } from "@/components/icon";
import type { AuditEntry } from "@/lib/identity-records";
import { roleInfo } from "@/lib/roles";

export type Tone = "primary" | "accent" | "danger" | "neutral";

type EventKind = { label: string; icon: IconName; tone: Tone };

/** Every platform.* action identity-service records, in the order the audit filter lists them. */
export const PLATFORM_EVENTS: Record<string, EventKind> = {
  "platform.institution_created": { label: "Registered an institution", icon: "domain_add", tone: "primary" },
  "platform.institution_updated": { label: "Updated institution details", icon: "edit_note", tone: "neutral" },
  "platform.institution_disabled": { label: "Disabled an institution", icon: "domain_disabled", tone: "danger" },
  "platform.institution_enabled": { label: "Re-enabled an institution", icon: "check_circle", tone: "accent" },
  "platform.institution_admin_created": { label: "Invited an institution admin", icon: "person_add", tone: "primary" },
  "platform.institution_admin_invitation_resent": { label: "Resent an admin invitation", icon: "forward_to_inbox", tone: "neutral" },
  "platform.institution_admin_disabled": { label: "Disabled an institution admin", icon: "person_off", tone: "danger" },
  "platform.institution_admin_enabled": { label: "Re-enabled an institution admin", icon: "how_to_reg", tone: "accent" },
  "platform.institution_admin_revoked": { label: "Revoked an institution admin", icon: "person_remove", tone: "danger" },
  "platform.system_admin_bootstrapped": { label: "Created the first system admin", icon: "shield_person", tone: "primary" },
  "platform.system_admin_invited": { label: "Invited a system admin", icon: "shield_person", tone: "primary" },
};

/** Account events inside an institution (the identity audit log). */
export const USER_EVENTS: Record<string, EventKind> = {
  "user.created": { label: "Created an account", icon: "person_add", tone: "primary" },
  "user.role_assigned": { label: "Assigned a role", icon: "verified_user", tone: "primary" },
  "user.role_revoked": { label: "Removed a role", icon: "person_remove", tone: "danger" },
  "user.invitation_resent": { label: "Resent an invitation", icon: "forward_to_inbox", tone: "neutral" },
  "user.updated": { label: "Updated a profile", icon: "edit_note", tone: "neutral" },
  "user.disabled": { label: "Disabled an account", icon: "person_off", tone: "danger" },
  "user.enabled": { label: "Re-enabled an account", icon: "how_to_reg", tone: "accent" },
  "user.provisioning_started": { label: "Started an invitation", icon: "mail", tone: "neutral" },
  "user.provisioning_failed": { label: "An invitation failed", icon: "error", tone: "danger" },
  "user.provisioning_cancelled": { label: "Cancelled an invitation", icon: "cancel", tone: "neutral" },
  "user.cognito_sync_failed": { label: "Sign-in sync failed", icon: "error", tone: "danger" },
};

/** Steps the feed hides because a clearer event always follows them. */
export const QUIET_EVENTS = new Set(["user.provisioning_started"]);

const asText = (value: unknown) => (typeof value === "string" && value.trim() ? value.trim() : undefined);

/** A one-line, human description of an audit entry, plus any detail worth showing under it. */
export function describeEvent(entry: AuditEntry): EventKind & { detail?: string } {
  const kind = PLATFORM_EVENTS[entry.action] ?? USER_EVENTS[entry.action] ?? {
    label: entry.action.replace(/^(platform|user)\./, "").replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase()),
    icon: "history" as const,
    tone: "neutral" as const,
  };
  const meta = entry.metadata;

  switch (entry.action) {
    case "platform.institution_created": {
      const name = asText(meta.name);
      const code = asText(meta.code);
      return { ...kind, detail: name && code ? `${name} (${code})` : (name ?? code) };
    }
    case "platform.institution_updated": {
      const changes = [
        asText(meta.name) && `renamed to “${asText(meta.name)}”`,
        asText(meta.code) && `code changed from ${asText(meta.previous_code) ?? "?"} to ${asText(meta.code)}`,
      ].filter(Boolean);
      return { ...kind, detail: changes.length ? changes.join("; ").replace(/^./, (c) => c.toUpperCase()) : undefined };
    }
    case "user.role_assigned":
    case "user.role_revoked":
    case "user.created": {
      const role = asText(meta.role_code);
      return { ...kind, detail: role ? roleInfo(role).name : undefined };
    }
    case "user.provisioning_failed": {
      const category = asText(meta.error_category);
      return { ...kind, detail: category ? describeFailure(category) : undefined };
    }
    case "user.disabled":
    case "platform.institution_disabled":
    case "platform.institution_admin_disabled": {
      const reason = asText(meta.reason);
      return { ...kind, detail: reason ? `Reason: ${reason}` : undefined };
    }
    default:
      return kind;
  }
}

const FAILURES: Record<string, string> = {
  COGNITO_USER_EXISTS: "A sign-in with this email already exists in Cognito.",
  COGNITO_USER_NOT_FOUND: "The Cognito sign-in no longer exists.",
  COGNITO_INVALID_REQUEST: "Cognito rejected the details.",
  COGNITO_INVITATION_DELIVERY_FAILED: "The invitation email couldn't be delivered.",
  COGNITO_THROTTLED: "Cognito was busy. Retrying usually works.",
  COGNITO_UNAVAILABLE: "Cognito was unavailable. Retrying usually works.",
  COGNITO_ACCESS_DENIED: "The identity service isn't allowed to create sign-ins.",
  COGNITO_MISCONFIGURED: "Cognito isn't configured correctly for the identity service.",
};

/** Why an invitation failed, from identity-service's error category. */
export function describeFailure(category: string): string {
  return FAILURES[category] ?? "Cognito returned an unexpected error.";
}
