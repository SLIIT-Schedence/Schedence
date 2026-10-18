import "server-only";
import { cache } from "react";
import { type Account, getAccount, type InstitutionSummary } from "@/lib/identity";
import { type ApiResult, identityRequest } from "@/lib/identity-api";
import {
  type AuditEntry,
  mapPage,
  type ProvisioningStatus,
  type RawAudit,
  type RawPage,
  type RawProvisioning,
  type RawUser,
  toAudit,
  toProvisioning,
  toUser,
} from "@/lib/identity-records";

// Read side of the Institution Admin console. identity-service scopes every list to the institutions in
// which the caller holds the needed permission, so these calls never pass an institution for users.

export const INSTITUTION_ADMIN = "INSTITUTION_ADMIN";

/** Onboarding records that still need an outcome; FAILED ones can be retried. */
export const OPEN_PROVISIONING: ProvisioningStatus[] = ["PENDING", "COGNITO_CREATED", "FAILED"];

/** The institutions the account administers (holds INSTITUTION_ADMIN in), sorted as /me returns them. */
export function adminInstitutions(account: Account): InstitutionSummary[] {
  const ids = new Set(account.roles.filter((role) => role.roleCode === INSTITUTION_ADMIN).map((role) => role.institutionId));
  const known = account.institutions.filter((institution) => ids.has(institution.id));
  const missing = [...ids].filter((id) => !known.some((institution) => institution.id === id));
  return [...known, ...missing.map((id) => ({ id, code: "—", name: "Your institution", status: "ACTIVE" as const }))];
}

export const isInstitutionAdmin = (account: Account) => adminInstitutions(account).length > 0;

export type AdminContext = {
  account: Account;
  institutions: InstitutionSummary[];
  /** Where new people are invited by default: the first active institution the account administers. */
  primary: InstitutionSummary;
};

/** The signed-in Institution Admin and their institutions, or null for anyone else. Read once per request. */
export const getAdminContext = cache(async (): Promise<AdminContext | null> => {
  const result = await getAccount();
  if (result.status !== "ok") return null;
  const institutions = adminInstitutions(result.account);
  if (!institutions.length) return null;
  return {
    account: result.account,
    institutions,
    primary: institutions.find((institution) => institution.status === "ACTIVE") ?? institutions[0],
  };
});

export type UserFilters = {
  search?: string;
  status?: "ACTIVE" | "DISABLED";
  roleCode?: string;
  page?: number;
  pageSize?: number;
};

export async function listUsers({ search, status, roleCode, page = 1, pageSize = 20 }: UserFilters = {}) {
  return mapPage(
    await identityRequest<RawPage<RawUser>>("/api/v1/identity/users", {
      query: { search, status, role_code: roleCode, page, page_size: pageSize },
    }),
    toUser,
  );
}

/** How many users match, from the list's total; undefined when the call fails. */
export async function countUsers(filters: Omit<UserFilters, "page" | "pageSize"> = {}): Promise<number | undefined> {
  const result = await listUsers({ ...filters, pageSize: 1 });
  return result.ok ? result.data.total : undefined;
}

export async function listProvisioning({
  statuses = [],
  page = 1,
  pageSize = 20,
}: { statuses?: ProvisioningStatus[]; page?: number; pageSize?: number } = {}) {
  return mapPage(
    await identityRequest<RawPage<RawProvisioning>>("/api/v1/identity/provisioning", {
      query: { status: statuses, page, page_size: pageSize },
    }),
    toProvisioning,
  );
}

export async function listInstitutionAudit({
  institutionId,
  action,
  occurredFrom,
  page = 1,
  pageSize = 20,
}: { institutionId?: string; action?: string; occurredFrom?: string; page?: number; pageSize?: number }) {
  return mapPage(
    await identityRequest<RawPage<RawAudit>>("/api/v1/identity/audit-logs", {
      query: { institution_id: institutionId, action, occurred_from: occurredFrom, page, page_size: pageSize },
    }),
    toAudit,
  );
}

/** Every audit entry of one action since `occurredFrom`, newest first; stops after `maxPages` pages of 100. */
export async function auditSince(
  action: string,
  occurredFrom: string,
  { institutionId, maxPages = 5 }: { institutionId?: string; maxPages?: number } = {},
): Promise<ApiResult<AuditEntry[]>> {
  const entries: AuditEntry[] = [];
  for (let page = 1; page <= maxPages; page++) {
    const result = await listInstitutionAudit({ institutionId, action, occurredFrom, page, pageSize: 100 });
    if (!result.ok) return result;
    entries.push(...result.data.items);
    if (entries.length >= result.data.total) break;
  }
  return { ok: true, data: entries };
}
