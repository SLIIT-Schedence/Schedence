import type { Institution, PlatformUser } from "@/lib/platform";
import {
  createInstitution,
  inviteInstitutionAdmin,
  resendAdminInvitation,
  revokeInstitutionAdmin,
  setAdminStatus,
  setInstitutionStatus,
  updateInstitution,
} from "@/lib/platform-actions";
import { ActionDialog, type DialogField } from "@/components/console/action-dialog";

// Every System Admin change, as a dialog bound to its server action (src/lib/platform-actions.ts).

const codeField = (defaultValue?: string): DialogField => ({
  name: "code",
  label: "Institution code",
  required: true,
  minLength: 2,
  maxLength: 30,
  pattern: "[A-Za-z][A-Za-z0-9_\\-]*",
  placeholder: "SLIIT",
  hint: "Short and unique. Letters, numbers, - and _, starting with a letter. Saved in capitals.",
  code: true,
  defaultValue,
});

const nameField = (defaultValue?: string): DialogField => ({
  name: "name",
  label: "Institution name",
  required: true,
  maxLength: 200,
  placeholder: "Sri Lanka Institute of Information Technology",
  defaultValue,
});

const reasonField: DialogField = {
  name: "reason",
  label: "Reason",
  type: "textarea",
  maxLength: 500,
  hint: "Kept in the audit log.",
};

export function CreateInstitutionDialog({ variant = "primary" }: { variant?: "primary" | "secondary" }) {
  return (
    <ActionDialog
      label="Register institution"
      icon="domain_add"
      variant={variant}
      title="Register an institution"
      description="The institution starts active. Next, invite its first Institution Admin from the institution's page."
      fields={[codeField(), nameField()]}
      submitLabel="Register institution"
      action={createInstitution}
    />
  );
}

export function EditInstitutionDialog({ institution }: { institution: Institution }) {
  return (
    <ActionDialog
      label="Edit details"
      icon="edit"
      title="Edit institution details"
      description="Changing the code doesn't affect accounts or roles; they're linked by the institution's ID."
      fields={[codeField(institution.code), nameField(institution.name)]}
      submitLabel="Save changes"
      action={updateInstitution.bind(null, institution.id)}
    />
  );
}

export function InstitutionStatusDialog({ institution }: { institution: Institution }) {
  return institution.status === "ACTIVE" ? (
    <ActionDialog
      label="Disable"
      icon="block"
      variant="danger"
      tone="danger"
      title={`Disable ${institution.name}?`}
      description="Roles held in this institution stop granting permissions right away, so its staff lose access. Accounts aren't deleted, and you can re-enable the institution later."
      fields={[reasonField]}
      submitLabel="Disable institution"
      action={setInstitutionStatus.bind(null, institution.id, false)}
    />
  ) : (
    <ActionDialog
      label="Re-enable"
      icon="check_circle"
      variant="primary"
      title={`Re-enable ${institution.name}?`}
      description="Roles held in this institution grant their permissions again."
      submitLabel="Re-enable institution"
      action={setInstitutionStatus.bind(null, institution.id, true)}
    />
  );
}

export function InviteAdminDialog({ institution }: { institution: Institution }) {
  return (
    <ActionDialog
      label="Invite admin"
      icon="person_add"
      variant="primary"
      title="Invite an Institution Admin"
      description={`They'll get an email with a temporary password for ${institution.name}. It works for 7 days.`}
      fields={[
        { name: "full_name", label: "Full name", required: true, maxLength: 200, autoComplete: "off", placeholder: "Nimal Perera" },
        { name: "email", label: "Email address", type: "email", required: true, maxLength: 320, placeholder: "name@institution.edu" },
      ]}
      submitLabel="Send invitation"
      action={inviteInstitutionAdmin.bind(null, institution.id)}
      disabledReason={institution.status === "DISABLED" ? "Re-enable the institution before inviting admins." : undefined}
    />
  );
}

export function ResendInvitationDialog({ institutionId, admin }: { institutionId: string; admin: PlatformUser }) {
  return (
    <ActionDialog
      label={`Resend invitation to ${admin.fullName}`}
      icon="forward_to_inbox"
      variant="icon"
      title="Resend the invitation?"
      description={`${admin.email} gets a new temporary password, and the old one stops working. Only works if they haven't signed in yet.`}
      submitLabel="Resend invitation"
      action={resendAdminInvitation.bind(null, institutionId, admin.id)}
    />
  );
}

export function AdminStatusDialog({ institutionId, admin }: { institutionId: string; admin: PlatformUser }) {
  return admin.accountStatus === "ACTIVE" ? (
    <ActionDialog
      label={`Disable ${admin.fullName}`}
      icon="person_off"
      variant="icon-danger"
      tone="danger"
      title={`Disable ${admin.fullName}?`}
      description="They're signed out of Schedence right away and can't sign in until you re-enable the account."
      fields={[reasonField]}
      submitLabel="Disable account"
      action={setAdminStatus.bind(null, institutionId, admin.id, false)}
    />
  ) : (
    <ActionDialog
      label={`Re-enable ${admin.fullName}`}
      icon="how_to_reg"
      variant="icon"
      title={`Re-enable ${admin.fullName}?`}
      description="They can sign in again with their existing password."
      submitLabel="Re-enable account"
      action={setAdminStatus.bind(null, institutionId, admin.id, true)}
    />
  );
}

export function RevokeAdminDialog({ institutionId, admin }: { institutionId: string; admin: PlatformUser }) {
  return (
    <ActionDialog
      label={`Remove ${admin.fullName} as admin`}
      icon="person_remove"
      variant="icon-danger"
      tone="danger"
      title={`Remove ${admin.fullName} as Institution Admin?`}
      description="Only the Institution Admin role is removed; their account and any other roles stay. If they're the only admin, invite another so someone can still manage the institution."
      submitLabel="Remove admin role"
      action={revokeInstitutionAdmin.bind(null, institutionId, admin.id)}
    />
  );
}
