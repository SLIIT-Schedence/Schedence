"use server";

import { refresh } from "next/cache";
import { EMAIL_PATTERN, type FormState, formFailure, invalid, staleRecord, text } from "@/lib/form-state";
import { identityRequest } from "@/lib/identity-api";
import { isUuid } from "@/lib/identity-records";
import { isInvitableRole, roleInfo } from "@/lib/roles";

// Write side of the Institution Admin console. identity-service checks that the caller administers the
// institution and may grant the role; these actions only validate input and phrase the outcome.

const PROVIDER_FAILED =
  "Cognito couldn't send the invitation, so it's saved under Invitations. Retry it from there in a moment.";

const INVITE_ERRORS = {
  fieldForCode: { EMAIL_ALREADY_REGISTERED: "email", PROVISIONING_IN_PROGRESS: "email", UNKNOWN_ROLE: "role_code" },
  messages: {
    IDENTITY_PROVIDER_UNAVAILABLE: PROVIDER_FAILED,
    IDENTITY_PROVIDER_ERROR: PROVIDER_FAILED,
    PROVISIONING_INCOMPLETE: PROVIDER_FAILED,
    IDENTITY_PROVIDER_MISCONFIGURED:
      "The identity service can't create sign-ins with its current AWS setup. Ask your Schedence administrator to check it.",
  },
};

/** Codes after which identity-service has kept an onboarding record that the Invitations page should show. */
const KEPT_FOR_RETRY = new Set(Object.keys(INVITE_ERRORS.messages));

type CreateUserOut = { user: { email: string }; linked_existing_identity: boolean };

export async function inviteUser(defaultInstitutionId: string, _previous: FormState, formData: FormData): Promise<FormState> {
  const fullName = text(formData, "full_name");
  const email = text(formData, "email").toLowerCase();
  const roleCode = text(formData, "role_code");
  const institutionId = text(formData, "institution_id") || defaultInstitutionId;
  const bad = [
    ...(isInvitableRole(roleCode) ? [] : ["role_code"]),
    ...(!fullName || fullName.length > 200 ? ["full_name"] : []),
    ...(!EMAIL_PATTERN.test(email) || email.length > 320 ? ["email"] : []),
    ...(isUuid(institutionId) ? [] : ["institution_id"]),
  ];
  if (bad.length) return invalid(bad);

  const result = await identityRequest<CreateUserOut>("/api/v1/identity/users", {
    method: "POST",
    body: { email, full_name: fullName, role_code: roleCode, institution_id: institutionId },
  });
  if (!result.ok) {
    if (KEPT_FOR_RETRY.has(result.error.code)) refresh();
    return formFailure(result.error, INVITE_ERRORS);
  }
  refresh();
  const role = roleInfo(roleCode).name;
  return {
    status: "success",
    message: result.data.linked_existing_identity
      ? `${email} already had a Schedence sign-in, so they're now a ${role} without a new email.`
      : `Invitation sent to ${email} as ${role}.`,
  };
}

export async function resendInvitation(userId: string): Promise<FormState> {
  if (!isUuid(userId)) return staleRecord();
  const result = await identityRequest(`/api/v1/identity/users/${userId}/resend-invitation`, { method: "POST" });
  if (!result.ok) return formFailure(result.error, { messages: { IDENTITY_PROVIDER_UNAVAILABLE: PROVIDER_FAILED } });
  return { status: "success", message: "A new invitation email is on its way." };
}

export async function retryInvitation(provisioningId: string): Promise<FormState> {
  if (!isUuid(provisioningId)) return staleRecord();
  const result = await identityRequest<CreateUserOut>(`/api/v1/identity/provisioning/${provisioningId}/retry`, {
    method: "POST",
  });
  refresh();
  if (!result.ok) return formFailure(result.error, INVITE_ERRORS);
  return { status: "success", message: `Invitation sent to ${result.data.user.email}.` };
}

export async function cancelInvitation(provisioningId: string): Promise<FormState> {
  if (!isUuid(provisioningId)) return staleRecord();
  const result = await identityRequest(`/api/v1/identity/provisioning/${provisioningId}/cancel`, { method: "POST" });
  if (!result.ok) return formFailure(result.error);
  refresh();
  return { status: "success", message: "Invitation cancelled." };
}
