import type { ApiResult } from "@/lib/identity-api";
import type { RoleAssignment } from "@/lib/identity";

// identity-service response shapes shared by the consoles, mapped from snake_case once here.

export type Page<T> = { items: T[]; total: number; page: number; pageSize: number };

export type UserRecord = {
  id: string;
  email: string;
  fullName: string;
  accountStatus: "ACTIVE" | "DISABLED";
  createdAt: string;
  updatedAt: string;
  roles: RoleAssignment[];
};

export type AuditEntry = {
  id: string;
  actorUserId: string | null;
  actorType: string;
  action: string;
  targetUserId: string | null;
  institutionId: string | null;
  metadata: Record<string, unknown>;
  occurredAt: string;
};

export type ProvisioningStatus = "PENDING" | "COGNITO_CREATED" | "FAILED" | "COMPLETED" | "CANCELLED";

/** One onboarding attempt: the record identity-service keeps while it creates a Cognito invitation. */
export type Provisioning = {
  id: string;
  email: string;
  fullName: string;
  institutionId: string;
  status: ProvisioningStatus;
  errorCategory: string | null;
  attemptCount: number;
  userId: string | null;
  createdAt: string;
  updatedAt: string;
  completedAt: string | null;
};

export type RawPage<T> = { items: T[]; total: number; page: number; page_size: number };

export type RawUser = {
  id: string;
  email: string;
  full_name: string;
  account_status: "ACTIVE" | "DISABLED";
  created_at: string;
  updated_at: string;
  roles: { role_code: string; role_name: string; institution_id: string; assigned_at: string }[];
};

export type RawAudit = {
  id: string;
  actor_user_id: string | null;
  actor_type: string;
  action: string;
  target_user_id: string | null;
  institution_id: string | null;
  metadata: Record<string, unknown> | null;
  occurred_at: string;
};

export type RawProvisioning = {
  id: string;
  email: string;
  full_name: string;
  institution_id: string;
  status: ProvisioningStatus;
  error_category: string | null;
  attempt_count: number;
  user_id: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
};

export const toUser = (raw: RawUser): UserRecord => ({
  id: raw.id,
  email: raw.email,
  fullName: raw.full_name,
  accountStatus: raw.account_status,
  createdAt: raw.created_at,
  updatedAt: raw.updated_at,
  roles: raw.roles.map((role) => ({
    roleCode: role.role_code,
    roleName: role.role_name,
    institutionId: role.institution_id,
    assignedAt: role.assigned_at,
  })),
});

export const toAudit = (raw: RawAudit): AuditEntry => ({
  id: raw.id,
  actorUserId: raw.actor_user_id,
  actorType: raw.actor_type,
  action: raw.action,
  targetUserId: raw.target_user_id,
  institutionId: raw.institution_id,
  metadata: raw.metadata ?? {},
  occurredAt: raw.occurred_at,
});

export const toProvisioning = (raw: RawProvisioning): Provisioning => ({
  id: raw.id,
  email: raw.email,
  fullName: raw.full_name,
  institutionId: raw.institution_id,
  status: raw.status,
  errorCategory: raw.error_category,
  attemptCount: raw.attempt_count,
  userId: raw.user_id,
  createdAt: raw.created_at,
  updatedAt: raw.updated_at,
  completedAt: raw.completed_at,
});

export function mapPage<R, T>(result: ApiResult<RawPage<R>>, map: (raw: R) => T): ApiResult<Page<T>> {
  if (!result.ok) return result;
  const { items, total, page, page_size } = result.data;
  return { ok: true, data: { items: items.map(map), total, page, pageSize: page_size } };
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export const isUuid = (value: unknown): value is string => typeof value === "string" && UUID.test(value);

/** The instant `days` days before now, as ISO 8601. Request-time only: call it after `connection()`. */
export function daysAgo(days: number): string {
  return new Date(Date.now() - days * 24 * 60 * 60 * 1000).toISOString();
}
