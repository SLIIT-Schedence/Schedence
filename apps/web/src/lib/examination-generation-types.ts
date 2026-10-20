// Browser-safe view contracts. Only aggregate counts and timetable rows are returned by the action.
import type { PreferencesSnapshot } from "@/lib/examination-settings";
import type { EvaluationAnalysis } from "./examination-evaluation";
import type { CrossTimetableSummary } from "./examination-protection";
export type GenerationMode = "feasibility" | "optimized";
export type GenerationProblem = { rule: string; message: string };
export type WorkloadScore = {
  objective_units?: number;
  same_day_student_pairs?: number; student_days_with_multiple_exams?: number; short_gap_student_pairs?: number;
  difficult_pairs_below_target?: number; minimum_gap_minutes?: number | null; median_gap_minutes?: number | null;
  lower_decile_gap_minutes?: number | null; maximum_window_exams?: number; maximum_window_load_points?: number;
  worst_student_weighted_penalty?: number; repeat_student_mean_weighted_penalty?: number | null;
  total_weighted_penalty: number;
  penalties: { constraint: string; weight: number; penalty_points: number; weighted_points: number }[];
};
export type GenerationFairness = {
  protection_applied: boolean;
  limits_satisfied: boolean | null;
  students: number;
  students_improved: number;
  students_worsened: number;
  students_unchanged: number;
  baseline_worst_normalized_penalty: number;
  selected_worst_normalized_penalty: number;
  maximum_individual_increase_points: number;
  worst_student_increase_points: number;
};
export type GenerationSession = {
  academic_year?: number;
  semester?: number;
  specializations?: string[];
  normal_candidates?: number;
  repeat_candidates?: number;
  delivery_mode?: "regular" | "block";
  date: string;
  start_time: string;
  end_time: string;
  module_code: string;
  module_name: string;
  session_number: number;
  session_count: number;
  candidates: number;
  rooms: { venue_id: string; venue_name: string; seats: number }[];
};
export type GeneratedTimetable = {
  analysis?: EvaluationAnalysis | null;
  /** Server response completion time, retained across display filters and downloads. */
  generated_at?: string;
  status: "OPTIMAL" | "FEASIBLE" | "INFEASIBLE" | "UNKNOWN" | "MODEL_INVALID";
  generated: boolean;
  solver: { mode: GenerationMode; time_limit_seconds: number; wall_time_seconds: number };
  inputs: {
    batch?: { batch_id: string; peer_period_code: string; strategy: string };
    cross_timetable?: CrossTimetableSummary | null;
    scheduling_preferences?: PreferencesSnapshot | null;
    period_code: string; period_name: string; modules: number; students: number;
    academic_year_label?: string; semester?: number; exam_kind?: string;
    /** The exam calendar the service actually used (null when an older service did not report it). */
    calendar?: GenerationCalendar | null;
    // Raw service fields, read by the server action only.
    calendar_source?: "period" | "selected"; start_date?: string; end_date?: string; exam_days?: string[];
    day_start_time?: string | null; day_end_time?: string | null; include_saturdays?: boolean | null; include_sundays?: boolean | null;
    exclusions?: { excluded_date: string; reason: string }[];
  };
  validation: { passed: boolean | null; rules_checked: string[]; violations: GenerationProblem[] };
  summary: { sessions: number; exam_days_used: number; seat_allocations: number };
  sessions: GenerationSession[];
  workload: WorkloadScore | null;
  optimization: {
    status: string | null;
    improved: boolean;
    baseline: WorkloadScore | null;
    selected: WorkloadScore | null;
    objective_bound_units: number | null;
    relative_gap: number | null;
    fairness: GenerationFairness | null;
  } | null;
  violations: GenerationProblem[];
  diagnostics: string[];
  notices: string[];
  excluded_registrations: { note: string; total: number; normal: number; repeat: number };
};
export type GenerationCalendar = {
  source: "period" | "selected";
  start_date: string;
  end_date: string;
  exam_days: number;
  day_start_time: string | null;
  day_end_time: string | null;
  include_saturdays: boolean | null;
  include_sundays: boolean | null;
  exclusions: { excluded_date: string; reason: string }[];
};
export type GenerationState =
  | { status: "idle" }
  | { status: "error"; title: string; message: string; httpStatus?: number; fieldErrors?: Record<string, string> }
  | { status: "complete"; result: GeneratedTimetable };
export type SaveDraftState =
  | { status: "idle" }
  | { status: "error"; title: string; message: string; httpStatus?: number; fieldErrors?: Record<string, string> }
  | { status: "saved"; timetableId: number; draftNumber: number; periodCode: string };
