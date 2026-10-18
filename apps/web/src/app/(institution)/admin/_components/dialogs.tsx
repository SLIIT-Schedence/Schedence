import { ActionDialog, type DialogField } from "@/components/console/action-dialog";
import type { IconName } from "@/components/icon";
import type { InstitutionSummary } from "@/lib/identity";
import type { Provisioning, UserRecord } from "@/lib/identity-records";
import { cancelInvitation, inviteUser, resendInvitation, retryInvitation } from "@/lib/institution-actions";
import { INVITABLE_ROLE_CODES, type InvitableRole, roleInfo } from "@/lib/roles";

// The Institution Admin's changes, as dialogs bound to their server actions (src/lib/institution-actions.ts).

/**
 * Invites a person into one of the roles the console can grant. `role` preselects it (the quick-invite
 * tiles); with more than one institution the dialog also asks where.
 */
export function InviteDialog({
  institutions,
  role,
  label = "Invite people",
  icon = "person_add",
  variant = "primary",
}: {
  institutions: InstitutionSummary[];
  role?: InvitableRole;
  label?: string;
  icon?: IconName;
  variant?: "primary" | "secondary";
}) {
  const active = institutions.filter((institution) => institution.status === "ACTIVE");
  const target = active[0] ?? institutions[0];
  const fields: DialogField[] = [
    {
      name: "role_code",
      label: "Role",
      type: "radio",
      required: true,
      defaultValue: role,
      options: INVITABLE_ROLE_CODES.map((code) => {
        const info = roleInfo(code);
        return { value: code, label: info.name, description: info.description, icon: info.icon };
      }),
    },
    { name: "full_name", label: "Full name", required: true, maxLength: 200, placeholder: "Nimal Perera" },
    {
      name: "email",
      label: "Email address",
      type: "email",
      required: true,
      maxLength: 320,
      placeholder: "name@institution.edu",
      hint: "Their sign-in email. A temporary password is sent here and works for 7 days.",
    },
    ...(active.length > 1
      ? [
          {
            name: "institution_id",
            label: "Institution",
            type: "select" as const,
            required: true,
            defaultValue: target.id,
            options: active.map((institution) => ({ value: institution.id, label: `${institution.name} (${institution.code})` })),
          },
        ]
      : []),
  ];

  return (
    <ActionDialog
      label={label}
      icon={icon}
      variant={variant}
      title={role ? `Invite a${/^[AEIOU]/.test(roleInfo(role).name) ? "n" : ""} ${roleInfo(role).name}` : "Invite people"}
      description={`They'll get an email from Schedence with a temporary password${active.length > 1 ? "" : ` for ${target.name}`}. On first sign-in they choose their own password and set up an authenticator app.`}
      fields={fields}
      submitLabel="Send invitation"
      action={inviteUser.bind(null, target.id)}
      disabledReason={active.length ? undefined : "Your institution is disabled."}
    />
  );
}

export function ResendInvitationDialog({ user }: { user: UserRecord }) {
  return (
    <ActionDialog
      label={`Resend invitation to ${user.fullName}`}
      icon="forward_to_inbox"
      variant="icon"
      title="Resend the invitation?"
      description={`${user.email} gets a new temporary password and the old one stops working. This only works if they haven't signed in yet.`}
      submitLabel="Resend invitation"
      action={resendInvitation.bind(null, user.id)}
    />
  );
}

export function RetryInvitationDialog({ record }: { record: Provisioning }) {
  return (
    <ActionDialog
      label={`Retry the invitation to ${record.fullName}`}
      icon="replay"
      variant="icon"
      title="Retry this invitation?"
      description={`Schedence asks Cognito again to create ${record.email}'s sign-in and email the invitation. Retrying is safe; it never creates a second account.`}
      submitLabel="Retry invitation"
      action={retryInvitation.bind(null, record.id)}
    />
  );
}

export function CancelInvitationDialog({ record }: { record: Provisioning }) {
  return (
    <ActionDialog
      label={`Cancel the invitation to ${record.fullName}`}
      icon="cancel"
      variant="icon-danger"
      tone="danger"
      title="Cancel this invitation?"
      description={`The invitation to ${record.email} stops here. You can invite them again later.`}
      submitLabel="Cancel invitation"
      action={cancelInvitation.bind(null, record.id)}
    />
  );
}
