/** Shared wizard contract. Numeric limits mirror generation/scheduling_rules.py; the backend validates policy. */
export type LecturerPreferenceRules = {
  morning_start_minute: number; afternoon_start_minute: number; afternoon_end_minute: number;
  preferred_day_part_weight: number; not_preferred_day_part_weight: number;
  preferred_free_day_weight: number; weekend_pattern_weight: number;
};
export type ConsecutiveRules = {
  maximum_preferred_continuous_minutes: number | null; minimize_idle_gaps: boolean;
  apply_to_lecturers: boolean; apply_to_student_groups: boolean; penalty_weight: number;
};
export type SchedulingRules = {
  lecturer_preferences: LecturerPreferenceRules | null; workload_fairness: boolean;
  balance_student_daily_load: boolean; balance_staff_daily_load: boolean; consecutive: ConsecutiveRules | null;
};
export type SchedulingRulesReport = {
  selected: SchedulingRules | null; experimental_defaults: SchedulingRules; reference: string | null;
  connected: { lecturer_preferences: boolean; day_part_settings: boolean; workload_fairness: boolean;
    daily_load: string[]; consecutive: boolean; idle_gaps: boolean; objective_order: string[] };
  protected: { lunch_enforced: boolean; lunch_periods: { academic_year: number; start_minute: number; end_minute: number }[];
    workload_policy_reference: string | null; staff_with_weekly_cap: number; staff_with_daily_cap: number;
    not_available_handling: string };
  objective_order: string[];
};
type Parsed = { ok: true; rules: SchedulingRules } | { ok: false; error: string };
const object = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value);
const keysOnly = (value: Record<string, unknown>, keys: string[]) => Object.keys(value).every((key) => keys.includes(key));
const integer = (value: unknown, min: number, max: number, step = 1): value is number =>
  typeof value === "number" && Number.isSafeInteger(value) && value >= min && value <= max && value % step === 0;
const invalid = (error: string): Parsed => ({ ok: false, error });
export const PREFERENCE_WEIGHTS = ["preferred_day_part_weight", "not_preferred_day_part_weight", "preferred_free_day_weight", "weekend_pattern_weight"] as const;
export const DAY_PARTS = ["morning_start_minute", "afternoon_start_minute", "afternoon_end_minute"] as const;

export function parseSchedulingRules(value: unknown): Parsed {
  if (!object(value) || !keysOnly(value, ["lecturer_preferences", "workload_fairness", "balance_student_daily_load", "balance_staff_daily_load", "consecutive"])) {
    return invalid("The rules contain an unsupported setting. Reset or review Scheduling Rules.");
  }
  for (const key of ["workload_fairness", "balance_student_daily_load", "balance_staff_daily_load"]) {
    if (value[key] !== undefined && typeof value[key] !== "boolean") return invalid("Rule switches must be true or false.");
  }
  let preferences: LecturerPreferenceRules | null = null;
  if (value.lecturer_preferences !== undefined && value.lecturer_preferences !== null) {
    const p = value.lecturer_preferences;
    if (!object(p) || !keysOnly(p, [...DAY_PARTS, ...PREFERENCE_WEIGHTS])) return invalid("Lecturer preference settings are invalid.");
    if (!DAY_PARTS.every((key) => integer(p[key], 0, 1440, 30))) return invalid("Day-part boundaries must be whole half hours between 00:00 and 24:00.");
    if (!(Number(p.morning_start_minute) < Number(p.afternoon_start_minute) && Number(p.afternoon_start_minute) < Number(p.afternoon_end_minute))) {
      return invalid("Morning start must precede afternoon start, which must precede afternoon end.");
    }
    if (!PREFERENCE_WEIGHTS.every((key) => integer(p[key], 0, 10))) return invalid("Preference weights must be whole numbers from 0 to 10.");
    preferences = Object.fromEntries([...DAY_PARTS, ...PREFERENCE_WEIGHTS].map((key) => [key, p[key]])) as LecturerPreferenceRules;
  }
  let consecutive: ConsecutiveRules | null = null;
  if (value.consecutive !== undefined && value.consecutive !== null) {
    const c = value.consecutive;
    if (!object(c) || !keysOnly(c, ["maximum_preferred_continuous_minutes", "minimize_idle_gaps", "apply_to_lecturers", "apply_to_student_groups", "penalty_weight"])) {
      return invalid("Consecutive teaching settings contain an unsupported setting.");
    }
    const maximum = c.maximum_preferred_continuous_minutes ?? null;
    const idle = c.minimize_idle_gaps === undefined ? false : c.minimize_idle_gaps;
    const lecturers = c.apply_to_lecturers === undefined ? true : c.apply_to_lecturers;
    const students = c.apply_to_student_groups === undefined ? false : c.apply_to_student_groups;
    const weight = c.penalty_weight === undefined ? 1 : c.penalty_weight;
    if ([idle, lecturers, students].some((v) => typeof v !== "boolean")) return invalid("Consecutive teaching switches must be true or false.");
    if (maximum !== null && !integer(maximum, 60, 600, 30)) return invalid("The preferred continuous run must be 60–600 minutes, in half-hour steps.");
    if (!integer(weight, 1, 10)) return invalid("The consecutive teaching weight must be a whole number from 1 to 10.");
    if (maximum === null && !idle) return invalid("Choose a preferred continuous run, reduce idle gaps, or both.");
    if (!lecturers && !students) return invalid("Apply consecutive teaching rules to staff, student groups, or both.");
    consecutive = { maximum_preferred_continuous_minutes: maximum as number | null, minimize_idle_gaps: idle as boolean,
      apply_to_lecturers: lecturers as boolean, apply_to_student_groups: students as boolean, penalty_weight: weight };
  }
  return { ok: true, rules: { lecturer_preferences: preferences, workload_fairness: value.workload_fairness === true,
    balance_student_daily_load: value.balance_student_daily_load === true, balance_staff_daily_load: value.balance_staff_daily_load === true, consecutive } };
}

export function parseRulesSelection(encoded: string | undefined): Parsed {
  if (!encoded) return invalid("Choose and validate your Scheduling Rules before continuing.");
  if (encoded.length > 2400) return invalid("The Scheduling Rules selection is too large. Reset the rules.");
  try { return parseSchedulingRules(JSON.parse(encoded)); } catch { return invalid("The Scheduling Rules selection could not be read. Review or reset the rules."); }
}
export const serializeRules = (rules: SchedulingRules) => JSON.stringify(rules);
export function rulesMatchReport(rules: SchedulingRules, report: SchedulingRulesReport | null | undefined) {
  if (!report?.selected || !report.reference) return false;
  const selected = parseSchedulingRules(report.selected);
  return selected.ok && serializeRules(selected.rules) === serializeRules(rules);
}

type Scope = { semester: number; year: number | null; stream: string | null };
export function wizardHref(step: string, scope: Scope, rules?: string, extra: Record<string, string | undefined> = {}) {
  const params = new URLSearchParams({ step, semester: String(scope.semester) });
  if (scope.year) params.set("year", String(scope.year));
  if (scope.stream) params.set("stream", scope.stream);
  if (rules) params.set("rules", rules);
  for (const [key, value] of Object.entries(extra)) if (value !== undefined) params.set(key, value);
  return `/academic/timetables/generate?${params}`;
}

export type RulesForm = {
  preferences_enabled: boolean; workload_fairness: boolean; balance_student_daily_load: boolean; balance_staff_daily_load: boolean;
  consecutive_enabled: boolean; maximum_preferred_continuous_minutes: string; minimize_idle_gaps: boolean;
  apply_to_lecturers: boolean; apply_to_student_groups: boolean; penalty_weight: string;
} & Record<keyof LecturerPreferenceRules, string>;
export function rulesForm(rules: SchedulingRules, defaults: SchedulingRules): RulesForm {
  const p = rules.lecturer_preferences ?? defaults.lecturer_preferences;
  const c = rules.consecutive ?? defaults.consecutive;
  return { ...Object.fromEntries([...DAY_PARTS, ...PREFERENCE_WEIGHTS].map((key) => [key, p ? String(p[key]) : ""])),
    preferences_enabled: rules.lecturer_preferences !== null, workload_fairness: rules.workload_fairness,
    balance_student_daily_load: rules.balance_student_daily_load, balance_staff_daily_load: rules.balance_staff_daily_load,
    consecutive_enabled: rules.consecutive !== null, maximum_preferred_continuous_minutes: c?.maximum_preferred_continuous_minutes == null ? "" : String(c.maximum_preferred_continuous_minutes),
    minimize_idle_gaps: c?.minimize_idle_gaps ?? false, apply_to_lecturers: c?.apply_to_lecturers ?? true,
    apply_to_student_groups: c?.apply_to_student_groups ?? false, penalty_weight: String(c?.penalty_weight ?? 1) } as RulesForm;
}
const formNumber = (value: string) => /^\d+$/.test(value) ? Number(value) : Number.NaN;
export function rulesFromForm(form: RulesForm): Parsed {
  return parseSchedulingRules({ lecturer_preferences: form.preferences_enabled
    ? Object.fromEntries([...DAY_PARTS, ...PREFERENCE_WEIGHTS].map((key) => [key, formNumber(form[key])])) : null,
    workload_fairness: form.workload_fairness, balance_student_daily_load: form.balance_student_daily_load,
    balance_staff_daily_load: form.balance_staff_daily_load, consecutive: form.consecutive_enabled ? {
      maximum_preferred_continuous_minutes: form.maximum_preferred_continuous_minutes === "" ? null : formNumber(form.maximum_preferred_continuous_minutes),
      minimize_idle_gaps: form.minimize_idle_gaps, apply_to_lecturers: form.apply_to_lecturers,
      apply_to_student_groups: form.apply_to_student_groups, penalty_weight: formNumber(form.penalty_weight) } : null });
}
export const minuteLabel = (minutes: number) => `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
