// Response shapes of the Examination Service's duty-allocation API (/api/v1/duty-allocation), the
// "Fairness-Aware Staff Allocation for Examination Duties" component. Field names match
// services/examination-service/app/api/duty_schemas.py and duty_roster_schemas.py. No server imports, so client
// components can use these types and helpers too.

export const COMPONENT_NAME = "Fairness-Aware Staff Allocation for Examination Duties";

export type DataSource = "LOCAL_DEMO" | "NEON_READ_ONLY" | "NEON_SHARED" | "DISABLED";
export type PersistenceMode = "DISABLED" | "MEMORY" | "DATABASE";
export type DutyRole = "SUPERVISOR" | "INVIGILATOR";
export type RosterLabel = "BASELINE" | "FAIR";
export type AllocationMode = "baseline" | "fair" | "compare";
export type RosterStatus = "DRAFT" | "VALIDATED" | "APPROVED" | "PUBLISHED" | "REJECTED" | "SUPERSEDED";
export type ChangeKind = "SWAP" | "REPLACEMENT";
export type ChangeStatus = "REQUESTED" | "APPROVED" | "REJECTED" | "APPLIED" | "CANCELLED" | "STALE";

export type Violation = { constraint: string; message: string };

export type Meta = {
  data_source: DataSource;
  source_description: string;
  synthetic: boolean;
  institution_id: string;
  period_code: string | null;
  snapshot_loaded_at: string;
  preview_only: boolean;
  component: string;
  notice: string;
};

export type DutyStatus = {
  source: DataSource;
  enabled: boolean;
  problem_code: string | null;
  problem: string | null;
  missing_settings: string[];
  institution_id: string;
  default_period_code: string;
  app_env: string;
  snapshot_loaded: boolean;
  safeguards: string[];
  component: string;
  notice: string;
  persistence_mode: PersistenceMode;
  persistence_problem: string | null;
  persistence: string;
};

export type DemoPage<T> = { items: T[]; total: number; limit: number; offset: number; meta: Meta };

export type DutyPeriod = {
  period_code: string;
  period_name: string | null;
  academic_year_label: string | null;
  semester: number | null;
  start_date: string;
  end_date: string;
  day_start_time: string;
  day_end_time: string;
  session_count: number;
};

export type DutyRoom = {
  venue_id: string;
  venue_name: string | null;
  venue_type: string | null;
  building: string | null;
  students: number;
  full_capacity: number | null;
  capacity_percentage: number | null;
  exam_capacity: number | null;
};

export type DutySession = {
  session_id: string;
  module_code: string;
  module_name: string | null;
  period_code: string;
  date: string;
  start: string;
  end: string;
  duration_minutes: number;
  students: number;
  rooms: DutyRoom[];
  publication_status: string | null;
};

export type Requirement = { session_id: string; venue_id: string; module_code: string; students: number; supervisors: number; invigilators: number };

export type Requirements = {
  period_code: string;
  policy_source: string;
  complete: boolean;
  items: Requirement[];
  total_supervisors: number;
  total_invigilators: number;
  violations: Violation[];
  stored_example_mismatches: string[];
  meta: Meta;
};

export type DutyStaff = {
  staff_id: string;
  staff_number: string;
  full_name: string | null;
  academic_rank: string | null;
  eligible_roles: DutyRole[];
  eligible: boolean;
  exclusion_reason: string | null;
};

export type WorkloadStatus = "COMPUTED" | "NOT_COMPUTED" | "MISSING_RESPONSE" | "NOT_LOADED";
export type Workload = { staff_id: string; staff_number: string | null; status: WorkloadStatus; hours_per_week: number | null; note: string };
export type WorkloadPage = DemoPage<Workload> & { counts: Partial<Record<WorkloadStatus, number>> };

export type TimeWindow = { start: string; end: string; reason: string };
export type StaffConstraints = {
  staff_id: string;
  unavailability: TimeWindow[];
  availability: TimeWindow[];
  preferences: { day: string; kind: string }[];
  restrictions: { module_code: string | null; session_id: string | null; role: string | null; reason: string }[];
  history: { prior_duties: number; prior_minutes: number } | null;
  limits: { max_duties_per_day: number | null; max_duties_per_period: number | null; minimum_gap_minutes: number; source: string };
  notes: string[];
};
export type DutyStaffDetail = DutyStaff & { workload: Workload | null; constraints: StaffConstraints | null; meta: Meta };

export type RunAssignment = {
  session_id: string;
  venue_id: string;
  venue_name: string | null;
  module_code: string | null;
  date: string | null;
  start: string | null;
  end: string | null;
  staff_id: string;
  staff_number: string | null;
  staff_name: string | null;
  role: DutyRole;
};

export type StaffLoad = { staff_id: string; staff_number: string | null; staff_name: string | null; duties: number; total_load: number };

export type FairnessMetrics = {
  min_load: number;
  max_load: number;
  mean_load: number;
  std_load: number;
  gini: number;
  max_duties: number;
  min_duties: number;
  staff_without_duties: number;
  supervisor_duties_range: number;
  back_to_back_pairs: number;
  preference_breaches: number;
  staff_missing_admin_hours: string[];
  per_staff: StaffLoad[];
};

export type RunRoster = {
  label: RosterLabel;
  solver_status: string;
  assignments: RunAssignment[];
  metrics: FairnessMetrics | null;
  revalidation_violations: Violation[];
  independently_valid: boolean | null;
  diagnostics: string[];
  wall_time_seconds: number;
};

export type Comparison = {
  fairer_by_gini: "FAIR" | "BASELINE" | "TIE";
  gini_baseline: number;
  gini_fair: number;
  std_load_baseline: number;
  std_load_fair: number;
  load_range_baseline: number;
  load_range_fair: number;
  back_to_back_baseline: number;
  back_to_back_fair: number;
  preference_breaches_baseline: number;
  preference_breaches_fair: number;
  assignments_different: number;
};

export type AllocationRun = {
  run_id: string;
  mode: "BASELINE" | "FAIR" | "COMPARE";
  status: "COMPLETED" | "INFEASIBLE" | "UNKNOWN" | "INVALID_INPUT" | "VALIDATION_FAILED";
  created_at: string;
  saved: boolean;
  approved: boolean;
  published: boolean;
  rosters: RunRoster[];
  comparison: Comparison | null;
  fairness_complete: boolean;
  fairness_scope: string[];
  data_availability: { name: string; status: string; source: string; records: number; origin: string }[];
  violations: Violation[];
  diagnostics: string[];
  warnings: string[];
  requirement_mismatches: string[];
  meta: Meta;
};

export type RunFairness = {
  run_id: string;
  status: string;
  baseline: FairnessMetrics | null;
  fair: FairnessMetrics | null;
  comparison: Comparison | null;
  fairness_complete: boolean;
  fairness_scope: string[];
  notes: string[];
  meta: Meta;
};

export type PersistenceInfo = { mode: PersistenceMode; durable: boolean; component: string; notice: string };
export type RosterPage<T> = { items: T[]; total: number; limit: number; offset: number; persistence: PersistenceInfo };

export type RosterAssignment = {
  session_id: string;
  venue_id: string;
  staff_id: string;
  staff_number: string | null;
  role: DutyRole;
  module_code: string | null;
  starts_at: string;
  ends_at: string;
};

export type Roster = {
  roster_id: string;
  period_code: string;
  version: number;
  status: RosterStatus;
  row_version: number;
  algorithm_mode: string;
  roster_label: RosterLabel;
  timetable_reference: string;
  timetable_verified: boolean;
  published_timetable_reference: string | null;
  data_source: string;
  synthetic: boolean;
  parent_roster_id: string | null;
  fairness_metrics: Partial<FairnessMetrics>;
  validation: { valid: boolean | null; violations: Violation[]; checked_at: string | null; assignments_checked: number };
  created_by: string;
  created_at: string;
  updated_at: string;
  validated_at: string | null;
  approved_at: string | null;
  published_at: string | null;
  rejected_at: string | null;
  rejection_reason: string | null;
  assignment_count: number;
  next_actions: string[];
  can_be_published: boolean;
  notice: string;
  persistence: PersistenceInfo;
};
export type RosterDetail = Roster & { assignments: RosterAssignment[] };

export type PublicationCheck = {
  roster_id: string;
  row_version: number;
  can_publish: boolean;
  published_timetable_reference: string | null;
  blockers: { code: string; message: string }[];
};
export type RosterExport = {
  roster: Roster;
  period_name: string;
  warning: string | null;
  notes: string[];
  assignments: (RosterAssignment & { staff_name: string | null; module_name: string | null })[];
};

export type RosterEvent = {
  event_id: number;
  action: string;
  from_status: string | null;
  to_status: string | null;
  actor_id: string;
  at: string;
  reason: string | null;
  details: Record<string, unknown>;
};
export type RosterHistory = { roster_id: string; events: RosterEvent[]; persistence: PersistenceInfo };

export type ChangeCandidate = {
  staff_id: string;
  staff_number: string;
  staff_name: string | null;
  duties_after: number;
  objective: number | null;
  metrics: Partial<FairnessMetrics>;
};

export type ChangePreview = {
  roster_id: string;
  roster_version: number;
  kind: ChangeKind;
  status: "PROPOSED" | "INFEASIBLE" | "INVALID_REQUEST";
  applied: boolean;
  removed: RosterAssignment[];
  added: RosterAssignment[];
  candidates: ChangeCandidate[];
  rejected: { staff_id: string; staff_number: string; reasons: string[] }[];
  violations: Violation[];
  independent_check_violations: Violation[] | null;
  reasons: string[];
  original_metrics: Partial<FairnessMetrics>;
  proposed_metrics: Partial<FairnessMetrics>;
  notice: string;
};

export type ChangeRequest = {
  request_id: string;
  roster_id: string;
  kind: ChangeKind;
  status: ChangeStatus;
  base_row_version: number;
  removed: RosterAssignment[];
  added: RosterAssignment[];
  reason: string;
  summary: Record<string, unknown>;
  requested_by: string;
  requested_at: string;
  decided_by: string | null;
  decided_at: string | null;
  decision_reason: string | null;
  applied_at: string | null;
  next_actions: string[];
  notice: string;
};

export type SeatingRoom = {
  session_id: string;
  venue_id: string;
  venue_type: string;
  full_capacity: number;
  capacity_percentage: number | null;
  exam_capacity: number | null;
  usable_seats: number;
  students: number;
  seat_limit: number | null;
};

export type SeatingRun = {
  seating_id: string;
  status: string;
  created_at: string;
  seat_state: "SYNTHETIC" | "PROVISIONAL" | "VERIFIED" | "NONE";
  all_seats_verified: boolean;
  synthetic_halls: string[];
  provisional_halls: string[];
  capacity_rules_source: string | null;
  assignment_count: number;
  rooms: SeatingRoom[];
  violations: Violation[];
  diagnostics: string[];
  example_problems: string[];
  meta: Meta;
};

export type Seat = { student_id: string; module_code: string; seat_number: number; seat_label: string; seat_verified: boolean };
export type RoomSeats = { seating_id: string; session_id: string; venue_id: string; seat_state: string; students: number; seats: Seat[] };

export type SeatingDraft = {
  draft_id: string;
  period_code: string;
  version: number;
  status: "DRAFT" | "SUPERSEDED";
  timetable_reference: string;
  data_source: string;
  seat_state: "SYNTHETIC" | "PROVISIONAL" | "VERIFIED";
  synthetic: boolean;
  created_at: string;
  summary: Record<string, unknown>;
  notice: string;
  persistence: PersistenceInfo;
};
export type SeatingDraftRoom = { draft_id: string; session_id: string; venue_id: string; seat_state: string; students: number; seats: Seat[] };

// ------------------------------------------------------------------------------------------- presentation

export const ROSTER_STATUSES: RosterStatus[] = ["DRAFT", "VALIDATED", "APPROVED", "PUBLISHED", "REJECTED", "SUPERSEDED"];
export const WORKING_STATUSES: RosterStatus[] = ["DRAFT", "VALIDATED", "APPROVED"];

export const titleCase = (value: string) => value.charAt(0) + value.slice(1).toLowerCase().replaceAll("_", " ");

export const ROSTER_TONES: Record<RosterStatus, "primary" | "accent" | "danger" | "neutral"> = {
  DRAFT: "neutral",
  VALIDATED: "primary",
  APPROVED: "primary",
  PUBLISHED: "accent",
  REJECTED: "danger",
  SUPERSEDED: "neutral",
};

export const CHANGE_TONES: Record<ChangeStatus, "primary" | "accent" | "danger" | "neutral"> = {
  REQUESTED: "primary",
  APPROVED: "primary",
  APPLIED: "accent",
  REJECTED: "danger",
  CANCELLED: "neutral",
  STALE: "danger",
};

/** "08:30" from "2026-11-16T08:30:00". Session times are local wall-clock times without an offset. */
export const clock = (iso: string | null | undefined) => (iso ? iso.slice(11, 16) : "–");
export const fixed = (value: number | null | undefined, digits = 3) => (typeof value === "number" ? value.toFixed(digits) : "–");
export const roomKey = (sessionId: string, venueId: string) => `${sessionId}|${venueId}`;

/** One duty in a form value: the four fields the backend uses to identify an assignment. */
export function assignmentValue(a: { session_id: string; venue_id: string; staff_id: string; role: string }): string {
  return [a.session_id, a.venue_id, a.staff_id, a.role].map(encodeURIComponent).join("~");
}
