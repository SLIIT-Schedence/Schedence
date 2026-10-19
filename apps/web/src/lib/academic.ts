import "server-only";
import { cache } from "react";
import { type Account, getAccount, type InstitutionSummary } from "@/lib/identity";
import { mapPage, type Page, type RawPage } from "@/lib/identity-records";
import { type ApiResult, type RequestOptions, type Service, serviceRequest, serviceUrl } from "@/lib/service-api";

// Read side of the Academic Admin console. academic-data-service checks every permission itself and scopes
// each list to the institutions in which the caller holds it, so these calls never pass an institution.

export const ACADEMIC_ADMIN = "ACADEMIC_ADMIN";

const ACADEMIC: Service = {
  baseUrl: () => serviceUrl("ACADEMIC_SERVICE_URL", "http://localhost:8002"),
  unreachable: "The academic data service didn't respond.",
};

function academicRequest<T>(path: string, options?: RequestOptions) {
  return serviceRequest<T>(ACADEMIC, `/api/v1/academic${path}`, options);
}

// ------------------------------------------------------------------------------------------- access

/** The institutions in which the account holds ACADEMIC_ADMIN, sorted as /me returns them. */
export function academicInstitutions(account: Account): InstitutionSummary[] {
  const ids = new Set(account.roles.filter((role) => role.roleCode === ACADEMIC_ADMIN).map((role) => role.institutionId));
  const known = account.institutions.filter((institution) => ids.has(institution.id));
  const missing = [...ids].filter((id) => !known.some((institution) => institution.id === id));
  return [...known, ...missing.map((id) => ({ id, code: "—", name: "Your institution", status: "ACTIVE" as const }))];
}

export const isAcademicAdmin = (account: Account) => academicInstitutions(account).length > 0;

export type AcademicContext = { account: Account; institutions: InstitutionSummary[]; primary: InstitutionSummary };

/** The signed-in Academic Admin and their institutions, or null for anyone else. Read once per request. */
export const getAcademicContext = cache(async (): Promise<AcademicContext | null> => {
  const result = await getAccount();
  if (result.status !== "ok") return null;
  const institutions = academicInstitutions(result.account);
  if (!institutions.length) return null;
  return {
    account: result.account,
    institutions,
    primary: institutions.find((institution) => institution.status === "ACTIVE") ?? institutions[0],
  };
});

// ------------------------------------------------------------------------------------------ records

export type UnitType = "INSTITUTION" | "FACULTY" | "DEPARTMENT" | "PROGRAM" | "OTHER";
export type CombinationRule = "UNSPECIFIED" | "ADDITIVE" | "CAPPED" | "REPLACEMENT";
export type AppointmentStatus = "SCHEDULED" | "ACTIVE" | "ENDED";

export type StaffRecord = {
  id: string;
  institutionId: string;
  staffNumber: string;
  fullName: string;
  email: string | null;
  academicRank: string | null;
  employmentStatus: "ACTIVE" | "INACTIVE";
  /** The linked Schedence login, if any; many staff have none. */
  identityUserId: string | null;
  createdAt: string;
};

export type HoursPolicy = {
  id: string;
  version: number;
  hoursPerWeek: number;
  combinationRule: CombinationRule;
  capHoursPerWeek: number | null;
  reference: string | null;
  notes: string | null;
  /** Calendar dates (YYYY-MM-DD); `effectiveUntil` is inclusive, null when open-ended. */
  effectiveFrom: string;
  effectiveUntil: string | null;
  createdAt: string;
};

export type PositionRecord = {
  id: string;
  institutionId: string;
  code: string;
  name: string;
  description: string;
  unitType: UnitType | null;
  exclusivePerUnit: boolean;
  active: boolean;
  /** Hours under the policy in force today; null when no policy is in force. */
  hoursPerWeek: number | null;
  policy: HoursPolicy | null;
  createdAt: string;
  updatedAt: string;
};

export type PositionDetail = PositionRecord & { history: HoursPolicy[] };

export type Appointment = {
  id: string;
  positionId: string;
  positionCode: string;
  positionName: string;
  unitType: UnitType | null;
  unitCode: string | null;
  unitName: string | null;
  effectiveFrom: string;
  effectiveUntil: string | null;
  status: AppointmentStatus;
  hoursOverride: number | null;
  overrideReason: string | null;
  notes: string | null;
  endReason: string | null;
};

export type Workload = {
  asOf: string;
  appointments: {
    appointmentId: string;
    positionName: string;
    unitCode: string | null;
    unitName: string | null;
    hoursPerWeek: number;
    hoursSource: "POLICY" | "OVERRIDE" | "NO_POLICY";
    combinationRule: CombinationRule | null;
  }[];
  aggregate: { computed: boolean; rule: string | null; totalHoursPerWeek: number | null; reason: string | null };
};

type RawStaff = {
  id: string;
  institution_id: string;
  staff_number: string;
  full_name: string;
  email: string | null;
  academic_rank: string | null;
  employment_status: "ACTIVE" | "INACTIVE";
  identity_user_id: string | null;
  created_at: string;
};

type RawPolicy = {
  id: string;
  version: number;
  admin_hours_per_week: number;
  combination_rule: CombinationRule;
  cap_hours_per_week: number | null;
  policy_reference: string | null;
  notes: string | null;
  effective_from: string;
  effective_until: string | null;
  created_at: string;
};

type RawPosition = {
  id: string;
  institution_id: string;
  code: string;
  name: string;
  description: string;
  unit_type: UnitType | null;
  is_exclusive_per_unit: boolean;
  is_active: boolean;
  default_admin_hours_per_week: number | null;
  effective_policy: RawPolicy | null;
  created_at: string;
  updated_at: string;
};

type RawAppointment = {
  id: string;
  position_type_id: string;
  position_code: string;
  position_name: string;
  unit_type: UnitType | null;
  unit_code: string | null;
  unit_name: string | null;
  effective_from: string;
  effective_until: string | null;
  status: AppointmentStatus;
  admin_hours_override: number | null;
  override_reason: string | null;
  notes: string | null;
  end_reason: string | null;
};

type RawWorkload = {
  as_of: string;
  appointments: {
    appointment_id: string;
    position_name: string;
    unit_code: string | null;
    unit_name: string | null;
    required_admin_hours_per_week: number;
    hours_source: "POLICY" | "OVERRIDE" | "NO_POLICY";
    policy: { combination_rule: CombinationRule } | null;
  }[];
  aggregate: { status: "COMPUTED" | "NOT_COMPUTED"; rule: string | null; total_admin_hours_per_week: number | null; reason: string | null };
};

const toStaff = (raw: RawStaff): StaffRecord => ({
  id: raw.id,
  institutionId: raw.institution_id,
  staffNumber: raw.staff_number,
  fullName: raw.full_name,
  email: raw.email,
  academicRank: raw.academic_rank,
  employmentStatus: raw.employment_status,
  identityUserId: raw.identity_user_id,
  createdAt: raw.created_at,
});

const toPolicy = (raw: RawPolicy): HoursPolicy => ({
  id: raw.id,
  version: raw.version,
  hoursPerWeek: raw.admin_hours_per_week,
  combinationRule: raw.combination_rule,
  capHoursPerWeek: raw.cap_hours_per_week,
  reference: raw.policy_reference,
  notes: raw.notes,
  effectiveFrom: raw.effective_from,
  effectiveUntil: raw.effective_until,
  createdAt: raw.created_at,
});

const toPosition = (raw: RawPosition): PositionRecord => ({
  id: raw.id,
  institutionId: raw.institution_id,
  code: raw.code,
  name: raw.name,
  description: raw.description,
  unitType: raw.unit_type,
  exclusivePerUnit: raw.is_exclusive_per_unit,
  active: raw.is_active,
  hoursPerWeek: raw.default_admin_hours_per_week,
  policy: raw.effective_policy ? toPolicy(raw.effective_policy) : null,
  createdAt: raw.created_at,
  updatedAt: raw.updated_at,
});

const toAppointment = (raw: RawAppointment): Appointment => ({
  id: raw.id,
  positionId: raw.position_type_id,
  positionCode: raw.position_code,
  positionName: raw.position_name,
  unitType: raw.unit_type,
  unitCode: raw.unit_code,
  unitName: raw.unit_name,
  effectiveFrom: raw.effective_from,
  effectiveUntil: raw.effective_until,
  status: raw.status,
  hoursOverride: raw.admin_hours_override,
  overrideReason: raw.override_reason,
  notes: raw.notes,
  endReason: raw.end_reason,
});

const toWorkload = (raw: RawWorkload): Workload => ({
  asOf: raw.as_of,
  appointments: raw.appointments.map((item) => ({
    appointmentId: item.appointment_id,
    positionName: item.position_name,
    unitCode: item.unit_code,
    unitName: item.unit_name,
    hoursPerWeek: item.required_admin_hours_per_week,
    hoursSource: item.hours_source,
    combinationRule: item.policy?.combination_rule ?? null,
  })),
  aggregate: {
    computed: raw.aggregate.status === "COMPUTED",
    rule: raw.aggregate.rule,
    totalHoursPerWeek: raw.aggregate.total_admin_hours_per_week,
    reason: raw.aggregate.reason,
  },
});

function mapResult<R, T>(result: ApiResult<R>, map: (raw: R) => T): ApiResult<T> {
  return result.ok ? { ok: true, data: map(result.data) } : result;
}

// -------------------------------------------------------------------------------------------- reads

export async function listStaff({ search, page = 1, pageSize = 20 }: { search?: string; page?: number; pageSize?: number } = {}) {
  return mapPage(await academicRequest<RawPage<RawStaff>>("/staff", { query: { search, page, page_size: pageSize } }), toStaff);
}

/** Every staff record, A–Z by name; stops after `maxPages` pages of 100. */
export async function allStaff(maxPages = 10): Promise<ApiResult<{ items: StaffRecord[]; total: number }>> {
  const items: StaffRecord[] = [];
  let total = 0;
  for (let page = 1; page <= maxPages; page++) {
    const result = await listStaff({ page, pageSize: 100 });
    if (!result.ok) return result;
    items.push(...result.data.items);
    total = result.data.total;
    if (items.length >= total || !result.data.items.length) break;
  }
  return { ok: true, data: { items, total } };
}

export async function getStaff(staffId: string) {
  return mapResult(await academicRequest<RawStaff>(`/staff/${staffId}`), toStaff);
}

/** Current and scheduled appointments, or the full history with `includeEnded`; newest start first. */
export async function listAppointments(staffId: string, { includeEnded = false } = {}) {
  return mapResult(
    await academicRequest<RawAppointment[]>(`/staff/${staffId}/appointments`, { query: { include_ended: includeEnded } }),
    (items) => items.map(toAppointment),
  );
}

/** The administrative workload contract the timetable services read, as of today in the institution's time zone. */
export async function getWorkload(staffId: string) {
  return mapResult(await academicRequest<RawWorkload>(`/staff/${staffId}/administrative-workload`), toWorkload);
}

export async function listPositions({
  includeInactive = false,
  page = 1,
  pageSize = 20,
}: { includeInactive?: boolean; page?: number; pageSize?: number } = {}): Promise<ApiResult<Page<PositionRecord>>> {
  return mapPage(
    await academicRequest<RawPage<RawPosition>>("/positions", {
      query: { include_inactive: includeInactive, page, page_size: pageSize },
    }),
    toPosition,
  );
}

export async function getPosition(positionId: string): Promise<ApiResult<PositionDetail>> {
  return mapResult(
    await academicRequest<RawPosition & { policy_history: RawPolicy[] }>(`/positions/${positionId}`),
    (raw) => ({ ...toPosition(raw), history: raw.policy_history.map(toPolicy) }),
  );
}
