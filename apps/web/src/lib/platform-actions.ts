"use server";

import { refresh } from "next/cache";
import { redirect } from "next/navigation";
import { EMAIL_PATTERN, type FormState, formFailure, invalid, staleRecord, text } from "@/lib/form-state";
import { identityRequest } from "@/lib/identity-api";
import { isUuid } from "@/lib/platform";

// Write side of the System Admin console. Every action re-checks its input; identity-service still
// authorizes each call with the System Admin's own access token.

const BASE = "/api/v1/identity/platform/institutions";
const CODE_PATTERN = /^[A-Za-z][A-Za-z0-9_-]*$/;

function institutionFields(formData: FormData) {
  const code = text(formData, "code");
  const name = text(formData, "name");
  const bad = [
    ...(code.length < 2 || code.length > 30 || !CODE_PATTERN.test(code) ? ["code"] : []),
    ...(!name || name.length > 200 ? ["name"] : []),
  ];
  return { code, name, bad };
}

// ---------------------------------------------------------------------------------------------- institutions

export async function createInstitution(_previous: FormState, formData: FormData): Promise<FormState> {
  const { code, name, bad } = institutionFields(formData);
  if (bad.length) return invalid(bad);

  const result = await identityRequest<{ id: string }>(BASE, { method: "POST", body: { code, name } });
  if (!result.ok) return formFailure(result.error, { fieldForCode: { INSTITUTION_CODE_TAKEN: "code" } });
  redirect(`/platform/institutions/${result.data.id}`);
}

export async function updateInstitution(institutionId: string, _previous: FormState, formData: FormData): Promise<FormState> {
  if (!isUuid(institutionId)) return staleRecord();
  const { code, name, bad } = institutionFields(formData);
  if (bad.length) return invalid(bad);

  const result = await identityRequest(`${BASE}/${institutionId}`, { method: "PATCH", body: { code, name } });
  if (!result.ok) return formFailure(result.error, { fieldForCode: { INSTITUTION_CODE_TAKEN: "code" } });
  refresh();
  return { status: "success", message: "Institution details saved." };
}

export async function setInstitutionStatus(
  institutionId: string,
  active: boolean,
  _previous: FormState,
  formData: FormData,
): Promise<FormState> {
  if (!isUuid(institutionId)) return staleRecord();
  const reason = text(formData, "reason");
  if (reason.length > 500) return invalid(["reason"]);

  const result = await identityRequest(`${BASE}/${institutionId}/${active ? "enable" : "disable"}`, {
    method: "POST",
    body: active ? undefined : { reason: reason || null },
  });
  if (!result.ok) return formFailure(result.error);
  refresh();
  return { status: "success", message: active ? "Institution re-enabled." : "Institution disabled." };
}

// ---------------------------------------------------------------------------------------------- institution admins

export async function inviteInstitutionAdmin(institutionId: string, _previous: FormState, formData: FormData): Promise<FormState> {
  if (!isUuid(institutionId)) return staleRecord();
  const fullName = text(formData, "full_name");
  const email = text(formData, "email").toLowerCase();
  const bad = [
    ...(!fullName || fullName.length > 200 ? ["full_name"] : []),
    ...(!EMAIL_PATTERN.test(email) || email.length > 320 ? ["email"] : []),
  ];
  if (bad.length) return invalid(bad);

  const result = await identityRequest(`${BASE}/${institutionId}/admins`, {
    method: "POST",
    body: { email, full_name: fullName },
  });
  if (!result.ok) {
    return formFailure(result.error, { fieldForCode: { EMAIL_ALREADY_REGISTERED: "email", PROVISIONING_IN_PROGRESS: "email" } });
  }
  refresh();
  return { status: "success", message: `Invitation sent to ${email}.` };
}

export async function resendAdminInvitation(institutionId: string, userId: string): Promise<FormState> {
  if (!isUuid(institutionId) || !isUuid(userId)) return staleRecord();
  const result = await identityRequest(`${BASE}/${institutionId}/admins/${userId}/resend-invitation`, { method: "POST" });
  if (!result.ok) return formFailure(result.error);
  return { status: "success", message: "A new invitation email is on its way." };
}

export async function setAdminStatus(
  institutionId: string,
  userId: string,
  active: boolean,
  _previous: FormState,
  formData: FormData,
): Promise<FormState> {
  if (!isUuid(institutionId) || !isUuid(userId)) return staleRecord();
  const reason = text(formData, "reason");
  if (reason.length > 500) return invalid(["reason"]);

  const result = await identityRequest<{ cognito_sync: string }>(
    `${BASE}/${institutionId}/admins/${userId}/${active ? "enable" : "disable"}`,
    { method: "POST", body: active ? undefined : { reason: reason || null } },
  );
  if (!result.ok) return formFailure(result.error);
  refresh();
  if (!active && result.data.cognito_sync === "failed") {
    return {
      status: "success",
      message: "Admin disabled in Schedence. Cognito sign-in couldn't be blocked yet; the service will retry.",
    };
  }
  return { status: "success", message: active ? "Admin account re-enabled." : "Admin account disabled." };
}

export async function revokeInstitutionAdmin(institutionId: string, userId: string): Promise<FormState> {
  if (!isUuid(institutionId) || !isUuid(userId)) return staleRecord();
  const result = await identityRequest(`${BASE}/${institutionId}/admins/${userId}`, { method: "DELETE" });
  if (!result.ok) return formFailure(result.error);
  refresh();
  return { status: "success", message: "Institution admin role revoked." };
}
