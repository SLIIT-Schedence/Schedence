import type { SchedulingReview, SeatingReview } from "@/lib/examination-workspace-types";

/**
 * Scheduling preferences (Advanced Settings of the generation wizard): the editable subset of the institution's scheduling settings and default
 * exam seating. Rules mirror the Examination Service validation (app/scheduling/*policy.py, seating rules),
 * which stays authoritative. Shared by the browser form and the server actions.
 */

const INT32 = 2_147_483_647;
/** A generation from this console can run at most 300 s (MAX_API_TIME_LIMIT_SECONDS), so a saved limit above it would block generation. */
export const API_TIME_LIMIT_SECONDS = 300;

type NumberRule = { section: "scheduling" | "solver" | "optimization" | "fairness"; default: number | null; label: string; min: number; max: number; integer: boolean; above?: boolean; optional?: boolean };

/** Every editable number, with the built-in Examination Service default. Keys are the backend setting names. */
export const NUMBER_FIELDS = {
  minimum_break_minutes: { section: "scheduling", default: 60, label: "Minimum break between exams (minutes)", min: 0, max: 1440, integer: true },
  max_rooms_per_exam: { section: "scheduling", default: null, label: "Maximum rooms per examination", min: 1, max: INT32, integer: true, optional: true },
  max_sessions_per_exam: { section: "scheduling", default: 3, label: "Maximum sessions per examination", min: 1, max: 10, integer: true },
  time_limit_seconds: { section: "solver", default: 60, label: "Solver time limit (seconds)", min: 0, max: API_TIME_LIMIT_SECONDS, integer: false, above: true },
  random_seed: { section: "solver", default: 20261019, label: "Random seed", min: 0, max: INT32, integer: true },
  num_search_workers: { section: "solver", default: 1, label: "Search workers", min: 1, max: 16, integer: true },
  s1_weight: { section: "optimization", default: 25, label: "S1 weight", min: 0, max: INT32, integer: true },
  s2_weight: { section: "optimization", default: 20, label: "S2 weight", min: 0, max: INT32, integer: true },
  s3_weight: { section: "optimization", default: 30, label: "S3 weight", min: 0, max: INT32, integer: true },
  s4_weight: { section: "optimization", default: 15, label: "S4 weight", min: 0, max: INT32, integer: true },
  s5_weight: { section: "optimization", default: 10, label: "S5 weight", min: 0, max: INT32, integer: true },
  short_gap_minutes: { section: "optimization", default: 720, label: "Short gap (minutes)", min: 1, max: INT32, integer: true },
  preparation_target_minutes: { section: "optimization", default: 2880, label: "Preparation target (minutes)", min: 1, max: INT32, integer: true },
  difficult_target_minutes: { section: "optimization", default: 4320, label: "Difficult-exam target (minutes)", min: 1, max: INT32, integer: true },
  high_difficulty_minimum: { section: "optimization", default: 4, label: "Difficult exam level (1–5)", min: 1, max: 5, integer: true },
  window_days: { section: "optimization", default: 3, label: "Busy-period window (days)", min: 1, max: INT32, integer: true },
  preferred_window_exams: { section: "optimization", default: 2, label: "Preferred exams per window", min: 0, max: INT32, integer: true },
  difficulty_weight: { section: "optimization", default: 50, label: "Workload share: difficulty", min: 0, max: INT32, integer: true },
  credits_weight: { section: "optimization", default: 25, label: "Workload share: credits", min: 0, max: INT32, integer: true },
  duration_weight: { section: "optimization", default: 25, label: "Workload share: duration", min: 0, max: INT32, integer: true },
  reference_difficulty: { section: "optimization", default: 3, label: "Reference difficulty (1–5)", min: 1, max: 5, integer: true },
  reference_credits: { section: "optimization", default: 4, label: "Reference credits", min: 1, max: INT32, integer: true },
  reference_duration_minutes: { section: "optimization", default: 120, label: "Reference duration (minutes)", min: 1, max: INT32, integer: true },
  baseline_budget_percent: { section: "optimization", default: 10, label: "Baseline search share (%)", min: 1, max: 99, integer: true },
  max_individual_increase_points: { section: "fairness", default: 5, label: "Largest allowed increase per student (points)", min: 0, max: 100, integer: false },
  max_worst_increase_points: { section: "fairness", default: 0, label: "Largest allowed increase for the worst-off student (points)", min: 0, max: 100, integer: false },
} as const satisfies Record<string, NumberRule>;
export type NumberField = keyof typeof NUMBER_FIELDS;
export const DEFAULT_START_TIMES = ["08:30", "13:30"];
/** Read-only technical settings; they are copied unchanged from the saved document. */
export const READ_ONLY_KEYS = ["scheduling.venue_compatibility", "scheduling.module_venue_overrides", "optimization.score_scale",
  "optimization.load_scale", "fairness.automatic_relaxation"] as const;

export type SettingsForm = Record<NumberField, string> & {
  permitted_start_times: string[];
  fairness_enabled: boolean;
  protect_worst_student: boolean;
  seating_percentage: string;
};
export type SettingsDocument = Record<string, Record<string, unknown>>;
export type SchedulingPreferences = Partial<Record<"scheduling" | "solver" | "optimization" | "fairness" | "seating", Record<string, unknown>>>;
export type PreferencesSnapshot = {
  format: string; fingerprint: string; settings: SettingsDocument;
  seating: { default_percentage: number; venue_overrides: Record<string, number> };
};

/**
 * Recommended per-timetable solver profile for Optimized generation: the search needs parallel workers and time to
 * leave its first valid timetable. It only travels in this timetable's scheduling_preferences.solver; institution
 * settings, S1-S5 weights and the hard rules are untouched.
 */
export const RECOMMENDED_PROFILE = { num_search_workers: "4", time_limit_seconds: "120" } as const;
export type SolverSource = "recommended" | "institution" | "custom";
const sameNumber = (a: string, b: string) => a.trim() !== "" && Number(a) === Number(b);
type SolverFields = Pick<SettingsForm, "num_search_workers" | "time_limit_seconds">;
const sameSolver = (a: SolverFields, b: SolverFields) => sameNumber(a.num_search_workers, b.num_search_workers) && sameNumber(a.time_limit_seconds, b.time_limit_seconds);

/** Starting values for a new timetable: the institution's settings plus the recommended solver profile. */
export const withRecommendedProfile = (form: SettingsForm): SettingsForm => ({ ...form, ...RECOMMENDED_PROFILE });

/**
 * The values a generation really uses. The profile belongs to Optimized mode: in Feasibility mode an untouched
 * profile falls back to the institution's solver values. Values the admin changed are always kept.
 */
export function effectiveForm(applied: SettingsForm, institution: SettingsForm, mode: "optimized" | "feasibility"): SettingsForm {
  return mode === "feasibility" && sameSolver(applied, RECOMMENDED_PROFILE)
    ? { ...applied, num_search_workers: institution.num_search_workers, time_limit_seconds: institution.time_limit_seconds } : applied;
}

/** Where the solver values in use come from, for the summary shown before generating. */
export const solverSource = (effective: SettingsForm, institution: SettingsForm): SolverSource =>
  sameSolver(effective, RECOMMENDED_PROFILE) ? "recommended" : sameSolver(effective, institution) ? "institution" : "custom";

/** Explicit writable subset; technical safety fields never come from the browser. */
export function schedulingPreferences(form: SettingsForm): SchedulingPreferences {
  const result: SchedulingPreferences = { scheduling: { permitted_start_times: [...form.permitted_start_times] },
    solver: {}, optimization: {}, fairness: { enabled: form.fairness_enabled, protect_worst_student: form.protect_worst_student },
    seating: { default_percentage: Number(form.seating_percentage) } };
  for (const [key, rule] of Object.entries(NUMBER_FIELDS)) result[rule.section]![key] = form[key as NumberField].trim() === "" ? null : Number(form[key as NumberField]);
  return result;
}

/** Validate the supported request shape again inside authenticated server actions. */
export function parseSchedulingPreferences(value: unknown): SchedulingPreferences | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const allowed: Record<string, string[]> = { scheduling: ["permitted_start_times"], solver: [], optimization: [],
    fairness: ["enabled", "protect_worst_student"], seating: ["default_percentage"] };
  for (const [key, rule] of Object.entries(NUMBER_FIELDS)) allowed[rule.section].push(key);
  for (const [name, raw] of Object.entries(value)) {
    if (!Object.hasOwn(allowed, name) || !raw || typeof raw !== "object" || Array.isArray(raw)) return null;
    for (const [key, item] of Object.entries(raw)) {
      if (!allowed[name].includes(key)) return null;
      if (key === "permitted_start_times") {
        if (!Array.isArray(item) || !item.length || item.length > 48 || item.some((time) => typeof time !== "string" || !TIME.test(time)) || new Set(item).size !== item.length) return null;
      } else if (key === "enabled" || key === "protect_worst_student") {
        if (typeof item !== "boolean") return null;
      } else if (key === "default_percentage") {
        if (typeof item !== "number" || !Number.isInteger(item) || item < 1 || item > 100) return null;
      } else {
        const rule: NumberRule = NUMBER_FIELDS[key as NumberField];
        if (item === null && rule.optional) continue;
        if (typeof item !== "number" || !Number.isFinite(item) || (rule.integer && !Number.isInteger(item)) || item < rule.min || item > rule.max || (rule.above && item === rule.min)) return null;
      }
    }
  }
  return value as SchedulingPreferences;
}

/** Pick displayable preference fields only; never forward arbitrary backend snapshot metadata. */
export function publicPreferences(snapshot: PreferencesSnapshot | null | undefined): PreferencesSnapshot | null {
  if (!snapshot || snapshot.format !== "timetable-preferences-v1") return null;
  const settings: SettingsDocument = {};
  for (const [name, values] of Object.entries(snapshot.settings ?? {})) {
    const picked: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(values)) {
      if (key in NUMBER_FIELDS && NUMBER_FIELDS[key as NumberField].section === name && (typeof value === "number" || value === null)) picked[key] = value;
      if (name === "fairness" && ["enabled", "protect_worst_student"].includes(key) && typeof value === "boolean") picked[key] = value;
      if (name === "scheduling" && key === "permitted_start_times" && Array.isArray(value)) picked[key] = value.filter((item) => typeof item === "string" && TIME.test(item));
    }
    if (Object.keys(picked).length) settings[name] = picked;
  }
  return { format: snapshot.format, fingerprint: /^[a-f0-9]{64}$/.test(snapshot.fingerprint) ? snapshot.fingerprint : "",
    settings, seating: { default_percentage: snapshot.seating.default_percentage,
      venue_overrides: Object.fromEntries(Object.entries(snapshot.seating.venue_overrides).filter(([id, percentage]) => /^[A-Za-z0-9+][A-Za-z0-9+ -]{0,15}$/.test(id) && !/^[A-Z]{2}\d{8}$/.test(id) && typeof percentage === "number")) } };
}
export type SeatingLimits = { min: number; max: number };
/** The versions the admin loaded: the settings version (0 = defaults) and the default seating rule's version. */
export type SettingsExpected = { scheduling_version: number; seating_version: number };
export type SettingsPreview = {
  scheduling_fields: string[];
  seating: { old_percentage: number; new_percentage: number; affected_venues: number; newly_unavailable_venues: string[] } | null;
  /** Saved timetables in every examination cycle that the change makes stale (null = count unavailable). */
  drafts: number | null; approved: number | null;
  replace_invalid: boolean;
};
export type SettingsSaved = {
  saved: ("scheduling" | "seating")[];
  /** Set when only part of the change was saved. */
  problem: string | null;
  /** The reloaded settings (null when they could not be reloaded). */
  scheduling: SchedulingReview | null; seating: SeatingReview | null;
};

const TIME = /^(?:[01][0-9]|2[0-3]):[0-5][0-9]$/;
const REASON = /^[^\s\x00-\x1f\x7f](?:[^\x00-\x1f\x7f]*[^\s\x00-\x1f\x7f])?$/;
const text = (value: unknown) => typeof value === "number" && Number.isFinite(value) ? String(value) : "";
const section = (document: SettingsDocument, name: string): Record<string, unknown> => {
  const value = document[name];
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value : {};
};

/** The form for the current saved settings (an invalid saved document can leave fields blank). */
export function settingsForm(scheduling: SchedulingReview, seating: SeatingReview): SettingsForm {
  const document = scheduling.settings;
  const form = Object.fromEntries(Object.entries(NUMBER_FIELDS).map(([key, rule]) => [key, text(section(document, rule.section)[key])])) as Record<NumberField, string>;
  const starts = section(document, "scheduling").permitted_start_times;
  const fairness = section(document, "fairness");
  return {
    ...form,
    permitted_start_times: Array.isArray(starts) ? starts.filter((item): item is string => typeof item === "string") : [],
    fairness_enabled: fairness.enabled === true,
    protect_worst_student: fairness.protect_worst_student !== false,
    seating_percentage: text(seating.default.percentage),
  };
}

function numberValue(value: string, rule: NumberRule): number | null | undefined {
  const trimmed = value.trim();
  if (!trimmed) return rule.optional ? null : undefined;
  if (!(rule.integer ? /^\d+$/ : /^\d+(?:\.\d+)?$/).test(trimmed)) return undefined;
  const number = Number(trimmed);
  return Number.isFinite(number) && (rule.integer ? Number.isSafeInteger(number) : true) ? number : undefined;
}

function rangeMessage(rule: NumberRule): string {
  const kind = rule.integer ? "a whole number" : "a number";
  const low = rule.above ? `greater than ${rule.min}` : `from ${rule.min}`;
  return rule.max === INT32 ? `Enter ${kind} of ${rule.min} or more.` : rule.above ? `Enter ${kind} ${low} and no more than ${rule.max}.` : `Enter ${kind} ${low} to ${rule.max}.`;
}

/** Field errors for a form, keyed by field name; empty = valid. */
export function settingsErrors(form: SettingsForm, seatingLimits: SeatingLimits = { min: 1, max: 100 }): Record<string, string> {
  const errors: Record<string, string> = {};
  const values: Partial<Record<NumberField, number | null>> = {};
  for (const [key, rule] of Object.entries(NUMBER_FIELDS) as [NumberField, NumberRule][]) {
    const value = numberValue(typeof form[key] === "string" ? form[key] : "", rule);
    if (value === undefined || (value !== null && (rule.above ? value <= rule.min : value < rule.min) || (value !== null && value > rule.max))) {
      errors[key] = rule.optional ? `Leave empty for no limit, or ${rangeMessage(rule).replace("Enter", "enter")}` : rangeMessage(rule);
    } else values[key] = value;
  }
  const starts = Array.isArray(form.permitted_start_times) ? form.permitted_start_times : [];
  if (!starts.length) errors.permitted_start_times = "Add at least one permitted start time.";
  else if (starts.some((item) => typeof item !== "string" || !TIME.test(item))) errors.permitted_start_times = "Use 24-hour times such as 08:30.";
  else if (new Set(starts).size !== starts.length) errors.permitted_start_times = "Each start time can be listed only once.";
  const { short_gap_minutes: short, preparation_target_minutes: preparation, difficult_target_minutes: difficult } = values;
  if (typeof short === "number" && typeof preparation === "number" && typeof difficult === "number" && !(short < preparation && preparation < difficult)) {
    errors.preparation_target_minutes = "The short gap must be less than the preparation target, and the preparation target less than the difficult-exam target.";
  }
  if (values.difficulty_weight === 0 && values.credits_weight === 0 && values.duration_weight === 0) {
    errors.difficulty_weight = "At least one workload share (difficulty, credits or duration) must be above 0.";
  }
  if (typeof form.fairness_enabled !== "boolean" || typeof form.protect_worst_student !== "boolean") errors.fairness_enabled = "Choose the fairness options again.";
  const seating = numberValue(typeof form.seating_percentage === "string" ? form.seating_percentage : "", { section: "scheduling", default: null, label: "", min: seatingLimits.min, max: seatingLimits.max, integer: true });
  if (typeof seating !== "number" || seating < seatingLimits.min || seating > seatingLimits.max) {
    errors.seating_percentage = `Enter a whole percentage from ${seatingLimits.min} to ${seatingLimits.max}.`;
  }
  return errors;
}

/** The complete document to save: the saved document with only the editable keys replaced. Call with a valid form. */
export function settingsDocument(saved: SettingsDocument, form: SettingsForm): SettingsDocument {
  const document: SettingsDocument = Object.fromEntries(Object.entries(saved).map(([name, value]) =>
    [name, value !== null && typeof value === "object" && !Array.isArray(value) ? { ...value } : value as Record<string, unknown>]));
  for (const name of ["scheduling", "solver", "optimization", "fairness"]) document[name] = { ...section(document, name) };
  for (const [key, rule] of Object.entries(NUMBER_FIELDS) as [NumberField, NumberRule][]) {
    document[rule.section][key] = numberValue(form[key], rule) ?? null;
  }
  document.scheduling.permitted_start_times = [...form.permitted_start_times];
  document.fairness.enabled = form.fairness_enabled;
  document.fairness.protect_worst_student = form.protect_worst_student;
  return document;
}

export const seatingPercentage = (form: SettingsForm) => Number(form.seating_percentage.trim());

/** Dotted setting paths whose values differ (e.g. "scheduling.minimum_break_minutes"). */
export function changedSettings(saved: SettingsDocument, next: SettingsDocument): string[] {
  const paths: string[] = [];
  for (const name of new Set([...Object.keys(saved), ...Object.keys(next)])) {
    const before = section(saved, name), after = section(next, name);
    for (const key of new Set([...Object.keys(before), ...Object.keys(after)])) {
      if (JSON.stringify(before[key]) !== JSON.stringify(after[key])) paths.push(`${name}.${key}`);
    }
  }
  return paths.sort();
}

const OTHER_LABELS: Record<string, string> = {
  "scheduling.permitted_start_times": "Permitted exam start times", "fairness.enabled": "Fairness protection",
  "fairness.protect_worst_student": "Protect the worst-off student", seating: "Default exam seating percentage",
};
export function settingLabel(path: string): string {
  const key = path.split(".").pop() ?? path;
  return OTHER_LABELS[path] ?? (key in NUMBER_FIELDS ? NUMBER_FIELDS[key as NumberField].label : path);
}

/** The change reason kept in the settings history: 5–500 characters on one line. */
export function reasonError(value: unknown): string | undefined {
  if (typeof value !== "string") return "Enter a reason of 5–500 characters.";
  const trimmed = value.trim();
  const length = Array.from(trimmed).length;
  if (length < 5 || length > 500) return "Enter a reason of 5–500 characters, for example “Faculty board decision”.";
  if (!REASON.test(trimmed)) return "Use a single line of text without tabs or control characters.";
  return undefined;
}

/** Browser-safe views of the two settings responses (no history, no user IDs). */
export function schedulingReview(data: SchedulingReview): SchedulingReview {
  return { source: data.source, status: data.status, version: data.version, fingerprint: data.fingerprint,
    mandatory_rules: data.mandatory_rules, problems: data.problems, settings: data.settings };
}
export function seatingReview(data: SeatingReview): SeatingReview {
  return { ruleset_version: data.ruleset_version, min_percentage: data.min_percentage ?? 1, max_percentage: data.max_percentage ?? 100,
    default: { percentage: data.default.percentage, version: data.default.version },
    venue_count: data.venue_count, total_normal_capacity: data.total_normal_capacity,
    total_exam_capacity: data.total_exam_capacity, unavailable_venues: data.unavailable_venues,
    venue_overrides: data.venue_overrides.map((item) => ({ venue_id: item.venue_id, percentage: item.percentage })) };
}
