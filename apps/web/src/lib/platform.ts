import "server-only";
import { type ApiResult, identityRequest } from "@/lib/identity-api";
import {
  mapPage,
  type RawAudit,
  type RawPage,
  type RawUser,
  toAudit,
  toUser,
  type UserRecord,
} from "@/lib/identity-records";

// Read side of the System Admin console: /api/v1/identity/platform/*. Writes live in platform-actions.ts.

export { daysAgo, isUuid } from "@/lib/identity-records";
export type { AuditEntry, Page } from "@/lib/identity-records";
export type PlatformUser = UserRecord;

export type InstitutionStatus = "ACTIVE" | "DISABLED";

export type Institution = {
  id: string;
  code: string;
  name: string;
  status: InstitutionStatus;
  createdAt: string;
  updatedAt: string;
};

export const INSTITUTION_ADMIN = "INSTITUTION_ADMIN";

const BASE = "/api/v1/identity/platform";

type RawInstitution = { id: string; code: string; name: string; status: InstitutionStatus; created_at: string; updated_at: string };

export const toInstitution = (raw: RawInstitution): Institution => ({
  id: raw.id,
  code: raw.code,
  name: raw.name,
  status: raw.status,
  createdAt: raw.created_at,
  updatedAt: raw.updated_at,
});

export async function listInstitutions({
  search,
  status,
  page = 1,
  pageSize = 20,
}: { search?: string; status?: InstitutionStatus; page?: number; pageSize?: number } = {}) {
  return mapPage(
    await identityRequest<RawPage<RawInstitution>>(`${BASE}/institutions`, {
      query: { search, status, page, page_size: pageSize },
    }),
    toInstitution,
  );
}

export async function getInstitution(institutionId: string): Promise<ApiResult<Institution>> {
  const result = await identityRequest<RawInstitution>(`${BASE}/institutions/${institutionId}`);
  return result.ok ? { ok: true, data: toInstitution(result.data) } : result;
}

export async function listInstitutionAdmins(institutionId: string, { page = 1, pageSize = 100 } = {}) {
  return mapPage(
    await identityRequest<RawPage<RawUser>>(`${BASE}/institutions/${institutionId}/admins`, {
      query: { page, page_size: pageSize },
    }),
    toUser,
  );
}

export type AuditFilters = {
  institutionId?: string;
  action?: string;
  /** ISO 8601 instants; `occurredTo` is exclusive. */
  occurredFrom?: string;
  occurredTo?: string;
  page?: number;
  pageSize?: number;
};

export async function listPlatformAudit({ institutionId, action, occurredFrom, occurredTo, page = 1, pageSize = 20 }: AuditFilters = {}) {
  return mapPage(
    await identityRequest<RawPage<RawAudit>>(`${BASE}/audit-logs`, {
      query: {
        institution_id: institutionId,
        action,
        occurred_from: occurredFrom,
        occurred_to: occurredTo,
        page,
        page_size: pageSize,
      },
    }),
    toAudit,
  );
}

/** When this user became an Institution Admin of the institution, if they are one. */
export function adminSince(user: UserRecord, institutionId: string): string | undefined {
  return user.roles.find((role) => role.roleCode === INSTITUTION_ADMIN && role.institutionId === institutionId)?.assignedAt;
}
