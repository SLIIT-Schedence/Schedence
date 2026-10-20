import type { GeneratedTimetable, WorkloadScore } from "./examination-generation-types";

export type GapStatistics = {
  students: number; registrations: number; normal_registrations: number; repeat_registrations: number;
  students_with_consecutive_exams: number; consecutive_gap_count: number; total_consecutive_gap_minutes: number;
  mean_gap_minutes: number | null; median_gap_minutes: number | null; minimum_gap_minutes: number | null;
  lower_decile_gap_minutes: number | null; short_consecutive_gaps: number; all_exam_student_pairs: number;
  short_gap_student_pairs: number; difficult_exam_student_pairs: number; difficult_pairs_below_target: number;
  same_day_student_pairs: number; mean_weighted_workload: number | null;
};
export type AnalysisCohort = { academic_year: number | null; specialization: string | null;
  registration_group: "all" | "normal_only" | "includes_repeat"; statistics: GapStatistics };
export type AnalysisExam = { module_code: string; session_number: number; date: string; start_time: string; end_time: string };
export type DifficultPair = { first: AnalysisExam; second: AnalysisExam; gap_minutes: number; below_target: boolean;
  shared_candidates: number; normal_only_candidates: number; repeat_candidates: number };
export type AnalysisRun = { input_fingerprint: string; configuration_fingerprint: string;
  workload: WorkloadScore | null;
  validation: { passed: boolean; rules_checked: string[] }; statistics: GapStatistics;
  cohorts: AnalysisCohort[]; cohorts_total: number; cohorts_truncated: boolean;
  difficult_pairs: DifficultPair[]; difficult_pair_rows_total: number; difficult_pair_rows_truncated: boolean };
export type EvaluationAnalysis = {
  version: "preparation-gap-v1"; input_fingerprint: string; configuration_fingerprint: string;
  policy: { high_difficulty_minimum: number; short_gap_minutes: number; preparation_target_minutes: number;
    difficult_target_minutes: number; scoring_version: string; scoring_fingerprint: string };
  modules: { module_code: string; module_name: string; academic_year: number; semester: number;
    specializations: string[]; difficulty: number | null; difficult: boolean | null }[];
  limits: { max_student_pairs: number; max_pair_rows: number; max_cohorts: number };
  cohort_membership_available: number;
  baseline: AnalysisRun | null; selected: AnalysisRun | null;
  baseline_unavailable_reason: string | null; selected_unavailable_reason: string | null;
};
export type CohortFilter = { year: string; specialization: string; registration: AnalysisCohort["registration_group"] };
export const ALL_COHORTS: CohortFilter = { year: "", specialization: "", registration: "all" };
export const analysisText = (value: string) => value.replace(/[A-Z]{2}[0-9]{8}/gi, "[student identifier omitted]");
const fingerprint = (value: string) => /^[a-f0-9]{64}$/.test(value) ? value : "";

/** Explicit allowlist at the browser boundary, including nested objects. Never serialize raw analysis. */
export function publicAnalysis(raw: EvaluationAnalysis | null | undefined): EvaluationAnalysis | null {
  if (!raw || raw.version !== "preparation-gap-v1") return null;
  const stats = (s: GapStatistics): GapStatistics => Object.fromEntries(([
    "students", "registrations", "normal_registrations", "repeat_registrations", "students_with_consecutive_exams",
    "consecutive_gap_count", "total_consecutive_gap_minutes", "mean_gap_minutes", "median_gap_minutes", "minimum_gap_minutes",
    "lower_decile_gap_minutes", "short_consecutive_gaps", "all_exam_student_pairs", "short_gap_student_pairs", "difficult_exam_student_pairs",
    "difficult_pairs_below_target", "same_day_student_pairs", "mean_weighted_workload",
  ] as const).map((key) => [key, s[key]])) as GapStatistics;
  const exam = (e: AnalysisExam): AnalysisExam => ({ module_code: analysisText(e.module_code), session_number: e.session_number,
    date: e.date, start_time: e.start_time, end_time: e.end_time });
  const run = (r: AnalysisRun | null): AnalysisRun | null => r === null ? null : ({
    input_fingerprint: fingerprint(r.input_fingerprint), configuration_fingerprint: fingerprint(r.configuration_fingerprint),
    validation: { passed: r.validation.passed, rules_checked: r.validation.rules_checked.filter((rule) => /^H(?:[1-9]|1[01])$/.test(rule)) },
    workload: r.workload ? { objective_units: r.workload.objective_units, total_weighted_penalty: r.workload.total_weighted_penalty,
      penalties: r.workload.penalties.map((p) => ({ constraint: analysisText(p.constraint), weight: p.weight, penalty_points: p.penalty_points, weighted_points: p.weighted_points })) } : null,
    statistics: stats(r.statistics), cohorts_total: Math.max(r.cohorts_total, r.cohorts.length), cohorts_truncated: r.cohorts_truncated || r.cohorts.length > 128,
    cohorts: r.cohorts.slice(0, 128).map((c) => ({ academic_year: c.academic_year, specialization: c.specialization ? analysisText(c.specialization) : null,
      registration_group: c.registration_group, statistics: stats(c.statistics) })),
    difficult_pair_rows_total: Math.max(r.difficult_pair_rows_total, r.difficult_pairs.length), difficult_pair_rows_truncated: r.difficult_pair_rows_truncated || r.difficult_pairs.length > 100,
    difficult_pairs: r.difficult_pairs.slice(0, 100).map((p) => ({ first: exam(p.first), second: exam(p.second), gap_minutes: p.gap_minutes,
      below_target: p.below_target, shared_candidates: p.shared_candidates, normal_only_candidates: p.normal_only_candidates, repeat_candidates: p.repeat_candidates })),
  });
  return { version: raw.version, input_fingerprint: fingerprint(raw.input_fingerprint), configuration_fingerprint: fingerprint(raw.configuration_fingerprint),
    policy: { high_difficulty_minimum: raw.policy.high_difficulty_minimum, short_gap_minutes: raw.policy.short_gap_minutes,
      preparation_target_minutes: raw.policy.preparation_target_minutes, difficult_target_minutes: raw.policy.difficult_target_minutes,
      scoring_version: analysisText(raw.policy.scoring_version), scoring_fingerprint: fingerprint(raw.policy.scoring_fingerprint) },
    modules: raw.modules.slice(0, 200).map((m) => ({ module_code: analysisText(m.module_code), module_name: analysisText(m.module_name),
      academic_year: m.academic_year, semester: m.semester, specializations: m.specializations.map(analysisText), difficulty: m.difficulty, difficult: m.difficult })),
    limits: { max_student_pairs: raw.limits.max_student_pairs, max_pair_rows: raw.limits.max_pair_rows, max_cohorts: raw.limits.max_cohorts },
    cohort_membership_available: raw.cohort_membership_available, baseline: run(raw.baseline), selected: run(raw.selected),
    baseline_unavailable_reason: raw.baseline_unavailable_reason ? analysisText(raw.baseline_unavailable_reason) : null,
    selected_unavailable_reason: raw.selected_unavailable_reason ? analysisText(raw.selected_unavailable_reason) : null };
}

export function cohortStatistics(run: AnalysisRun | null, filter: CohortFilter): GapStatistics | null {
  if (!run) return null;
  if (!filter.year && !filter.specialization && filter.registration === "all") return run.statistics;
  return run.cohorts.find((c) => String(c.academic_year ?? "") === filter.year && (c.specialization ?? "") === filter.specialization
    && c.registration_group === filter.registration)?.statistics ?? null;
}
export const cohortLabel = (f: CohortFilter) => `${f.year ? `Year ${f.year}` : "All years"} / ${f.specialization || "All specializations"} / ${f.registration === "all" ? "All registered students" : f.registration === "normal_only" ? "Normal-only students" : "Students with repeat registrations"}`;
export const metricValue = (value: number | null | undefined) => value == null ? "Not available" : value.toLocaleString("en-GB", { maximumFractionDigits: 2 });
export const gapValue = (value: number | null | undefined) => value == null ? "Not available" : `${metricValue(value / 60)} hours`;

export const EVALUATION_METRICS: { key: keyof GapStatistics; label: string; gap?: boolean; lowerBetter?: boolean; compare?: boolean }[] = [
  { key: "mean_gap_minutes", label: "Average consecutive gap", gap: true, compare: true },
  { key: "median_gap_minutes", label: "Median consecutive gap", gap: true, compare: true },
  { key: "minimum_gap_minutes", label: "Minimum consecutive gap", gap: true, compare: true },
  { key: "lower_decile_gap_minutes", label: "Lower-decile consecutive gap", gap: true, compare: true },
  { key: "consecutive_gap_count", label: "Consecutive gaps (mean denominator)" },
  { key: "total_consecutive_gap_minutes", label: "Sum of consecutive gaps (minutes)" },
  { key: "students_with_consecutive_exams", label: "Students with at least two exams" },
  { key: "students", label: "Registered students" }, { key: "registrations", label: "Exam registrations" },
  { key: "normal_registrations", label: "Normal registrations" }, { key: "repeat_registrations", label: "Repeat registrations" },
  { key: "short_consecutive_gaps", label: "Short consecutive gaps", lowerBetter: true, compare: true },
  { key: "all_exam_student_pairs", label: "All exam pairs across students (pair denominator)" },
  { key: "short_gap_student_pairs", label: "Short gaps across all exam pairs", lowerBetter: true, compare: true },
  { key: "difficult_exam_student_pairs", label: "Difficult exam pairs across students" },
  { key: "difficult_pairs_below_target", label: "Difficult pairs below preparation target", lowerBetter: true, compare: true },
  { key: "same_day_student_pairs", label: "Same-day exam pairs", lowerBetter: true, compare: true },
  { key: "mean_weighted_workload", label: "Average weighted workload", lowerBetter: true, compare: true },
];
export function metricChange(before: number | null | undefined, after: number | null | undefined, lowerBetter = false): string {
  if (before == null || after == null) return "No comparison";
  const delta = after - before;
  return delta === 0 ? "Unchanged" : `${(lowerBetter ? delta < 0 : delta > 0) ? "Improved" : "Regressed"} (${delta > 0 ? "+" : ""}${metricValue(delta)})`;
}
export function comparableAnalysis(result: GeneratedTimetable): boolean {
  const a = result.analysis;
  return Boolean(a?.baseline && a.selected && a.input_fingerprint && a.configuration_fingerprint &&
    a.baseline.input_fingerprint === a.input_fingerprint && a.selected.input_fingerprint === a.input_fingerprint &&
    a.baseline.configuration_fingerprint === a.configuration_fingerprint && a.selected.configuration_fingerprint === a.configuration_fingerprint);
}
