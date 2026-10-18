import "server-only";
import { cache } from "react";
import { auth } from "@/auth";
import { identityRequest } from "@/lib/identity-api";

export type RoleAssignment = {
  roleCode: string;
  roleName: string;
  institutionId: string;
  assignedAt: string;
};

export type InstitutionSummary = { id: string; code: string; name: string; status: "ACTIVE" | "DISABLED" };

export type Account = {
  id: string;
  email: string;
  fullName: string;
  accountStatus: "ACTIVE" | "DISABLED";
  roles: RoleAssignment[];
  /** Roles held outside any institution, such as SYSTEM_ADMIN. */
  platformRoles: string[];
  institutions: InstitutionSummary[];
};

export type AccountResult =
  | { status: "ok"; account: Account }
  | { status: "not-provisioned"; email: string | null }
  | { status: "disabled"; email: string | null }
  | { status: "unavailable" };

type MeResponse = {
  id: string;
  email: string;
  full_name: string;
  account_status: "ACTIVE" | "DISABLED";
  roles: { role_code: string; role_name: string; institution_id: string; assigned_at: string }[];
  platform_roles?: string[];
  institutions?: InstitutionSummary[];
};

export const SYSTEM_ADMIN = "SYSTEM_ADMIN";

export function isSystemAdmin(account: Account): boolean {
  return account.platformRoles.includes(SYSTEM_ADMIN);
}

/** The signed-in user's Schedence account (GET /api/v1/identity/me), read once per request. */
export const getAccount = cache(async (): Promise<AccountResult> => {
  const result = await identityRequest<MeResponse>("/api/v1/identity/me");

  if (!result.ok) {
    const email = (await auth())?.user?.email ?? null;
    if (result.error.code === "ACCOUNT_NOT_PROVISIONED") return { status: "not-provisioned", email };
    if (result.error.code === "ACCOUNT_DISABLED") return { status: "disabled", email };
    return { status: "unavailable" };
  }

  const me = result.data;
  return {
    status: "ok",
    account: {
      id: me.id,
      email: me.email,
      fullName: me.full_name,
      accountStatus: me.account_status,
      roles: me.roles.map((role) => ({
        roleCode: role.role_code,
        roleName: role.role_name,
        institutionId: role.institution_id,
        assignedAt: role.assigned_at,
      })),
      platformRoles: me.platform_roles ?? [],
      institutions: me.institutions ?? [],
    },
  };
});
