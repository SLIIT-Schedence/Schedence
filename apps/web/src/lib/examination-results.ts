import type { Tone } from "./audit-events";
import type { GeneratedTimetable, GenerationProblem, WorkloadScore } from "./examination-generation-types";
import { comparableAnalysis, type DifficultPair, type GapStatistics } from "./examination-evaluation";

/**
 * Pure view logic for the Results dashboard. Everything here is derived from the data the Examination Service
 * returned. A value the service did not return is "Not available": nothing is estimated, and no student clash is
 * ever inferred from specialization, because only real registrations produce the pair counts used here.
 */

export const NOT_AVAILABLE = "Not available";
type Unit = "count" | "points" | "hours";
const fraction = (value: number, digits = 2) => value.toLocaleString("en-GB", { maximumFractionDigits: digits });
export const count = (value: number | null | undefined) => value == null ? NOT_AVAILABLE : fraction(value, 0);
export const points = (value: number | null | undefined) => value == null ? NOT_AVAILABLE : fraction(value, 3);
export const hours = (minutes: number | null | undefined) => minutes == null ? NOT_AVAILABLE : `${fraction(minutes / 60, 2)} hours`;
export const display = (value: number | null | undefined, unit: Unit) => unit === "hours" ? hours(value) : unit === "count" ? count(value) : points(value);

export type Direction = "improved" | "worse" | "same" | "unknown";
export type Change = { direction: Direction; text: string };
const DIGITS: Record<Unit, number> = { count: 0, points: 3, hours: 2 };

/** Baseline → selected change. Gap values are given in minutes and shown in hours. */
export function changeOf(before: number | null | undefined, after: number | null | undefined, lowerIsBetter: boolean, unit: Unit = "points"): Change {
  if (before == null || after == null) return { direction: "unknown", text: NOT_AVAILABLE };
  const digits = DIGITS[unit];
  const delta = Number(((after - before) / (unit === "hours" ? 60 : 1)).toFixed(digits));
  if (delta === 0) return { direction: "same", text: "Unchanged" };
  const better = lowerIsBetter ? delta < 0 : delta > 0;
  return { direction: better ? "improved" : "worse", text: `${better ? "Improved" : "Worse"} (${delta > 0 ? "+" : "-"}${fraction(Math.abs(delta), digits)}${unit === "hours" ? " hours" : ""})` };
}

const SOLVER_TEXT: Record<GeneratedTimetable["status"], string> = {
  OPTIMAL: "The solver reported an optimal solution for the requested mode.",
  FEASIBLE: "A feasible timetable was found; optimality is not proven.",
  INFEASIBLE: "The solver proved no timetable satisfies the current constraints.",
  UNKNOWN: "The solver did not establish a solution within this run.",
  MODEL_INVALID: "The model or returned timetable failed validation.",
};
export const solverHint = (status: GeneratedTimetable["status"]) => SOLVER_TEXT[status];
const SOLVER_TONE: Record<GeneratedTimetable["status"], Tone> = { OPTIMAL: "accent", FEASIBLE: "primary", INFEASIBLE: "danger", UNKNOWN: "neutral", MODEL_INVALID: "danger" };

/** Baseline exists only in Optimized mode. Selected falls back to the single workload score of the run. */
function scoreSources(result: GeneratedTimetable): { baseline: WorkloadScore | null; selected: WorkloadScore | null } {
  const optimized = result.solver.mode === "optimized";
  const baseline = optimized ? result.optimization?.baseline ?? result.analysis?.baseline?.workload ?? null : null;
  const selected = (optimized ? result.optimization?.selected : null) ?? result.workload ?? result.analysis?.selected?.workload ?? null;
  return { baseline, selected };
}

type MetricDefinition = { id: string; label: string; unit: Unit; lowerBetter: boolean };
const METRICS: MetricDefinition[] = [
  { id: "mean_gap", label: "Average consecutive gap", unit: "hours", lowerBetter: false },
  { id: "median_gap", label: "Median consecutive gap", unit: "hours", lowerBetter: false },
  { id: "min_gap", label: "Minimum consecutive gap", unit: "hours", lowerBetter: false },
  { id: "decile_gap", label: "Lower-decile consecutive gap", unit: "hours", lowerBetter: false },
  { id: "short_consecutive", label: "Short consecutive gaps", unit: "count", lowerBetter: true },
  { id: "short_pairs", label: "Short gaps across all exam pairs", unit: "count", lowerBetter: true },
  { id: "difficult_below", label: "Difficult pairs below preparation target", unit: "count", lowerBetter: true },
  { id: "same_day", label: "Same-day exam pairs", unit: "count", lowerBetter: true },
  { id: "mean_workload", label: "Average weighted workload", unit: "points", lowerBetter: true },
  { id: "worst_student", label: "Highest single-student workload score", unit: "points", lowerBetter: true },
];

/** The detailed analysis is preferred; the workload score carries the same headline numbers when it is missing. */
function metricValues(stats: GapStatistics | null, score: WorkloadScore | null): Record<string, number | null> {
  return {
    mean_gap: stats?.mean_gap_minutes ?? null,
    median_gap: stats?.median_gap_minutes ?? score?.median_gap_minutes ?? null,
    min_gap: stats?.minimum_gap_minutes ?? score?.minimum_gap_minutes ?? null,
    decile_gap: stats?.lower_decile_gap_minutes ?? score?.lower_decile_gap_minutes ?? null,
    short_consecutive: stats?.short_consecutive_gaps ?? null,
    short_pairs: stats?.short_gap_student_pairs ?? score?.short_gap_student_pairs ?? null,
    difficult_below: stats?.difficult_pairs_below_target ?? score?.difficult_pairs_below_target ?? null,
    same_day: stats?.same_day_student_pairs ?? score?.same_day_student_pairs ?? null,
    mean_workload: stats?.mean_weighted_workload ?? score?.total_weighted_penalty ?? null,
    worst_student: score?.worst_student_weighted_penalty ?? null,
  };
}

export type Metric = MetricDefinition & { before: number | null; after: number | null; change: Change };
/** Baseline versus selected on one consistent source; null when there is no baseline to compare with. */
export function compareMetrics(result: GeneratedTimetable): Metric[] | null {
  const { baseline, selected } = scoreSources(result);
  if (!baseline || !selected) return null;
  const analysis = comparableAnalysis(result);
  const before = metricValues(analysis ? result.analysis?.baseline?.statistics ?? null : null, baseline);
  const after = metricValues(analysis ? result.analysis?.selected?.statistics ?? null : null, selected);
  return METRICS.map((metric) => ({ ...metric, before: before[metric.id], after: after[metric.id], change: changeOf(before[metric.id], after[metric.id], metric.lowerBetter, metric.unit) }));
}

export type SummaryItem = {
  id: "validation" | "solver" | "workload" | "gaps" | "difficult" | "students";
  label: string; value: string; detail: string; tone: Tone; badge: { text: string; tone: Tone } | null;
};

const percentChange = (before: number, after: number) => before === 0 ? null : fraction(Math.abs(after - before) / before * 100, 1);
const tone = (change: Change): Tone => change.direction === "improved" ? "accent" : "neutral";
const badgeOf = (change: Change): SummaryItem["badge"] => change.direction === "unknown" ? null : { text: change.direction === "improved" ? "Improved" : change.direction === "worse" ? "Worse" : "Unchanged", tone: tone(change) };

/** The six headline facts shown above the tabs. */
export function summarize(result: GeneratedTimetable): SummaryItem[] {
  const { baseline, selected } = scoreSources(result);
  const optimized = result.solver.mode === "optimized";
  const passed = result.validation.passed;
  const violations = result.validation.violations.length;
  const stats = result.analysis?.selected?.statistics ?? null;
  const metrics = compareMetrics(result);
  const metric = (id: string) => metrics?.find((item) => item.id === id) ?? null;
  const now = metricValues(stats, selected);

  const validation: SummaryItem = {
    id: "validation", label: "Hard rules H1–H11", tone: passed === true ? "accent" : passed === false ? "danger" : "neutral", badge: null,
    value: passed === true ? "Passed" : passed === false ? "Failed" : "Not evaluated",
    detail: passed === null ? "No timetable was available to validate."
      : `${result.validation.rules_checked.length ? `${result.validation.rules_checked.length} mandatory rules` : "Mandatory rules"} checked by the independent validator${passed === false && violations ? `; ${violations} violation${violations === 1 ? "" : "s"} reported` : ""}.`,
  };
  const solver: SummaryItem = {
    id: "solver", label: "Solver status", value: result.status, detail: solverHint(result.status), tone: SOLVER_TONE[result.status],
    badge: result.status === "FEASIBLE" ? { text: "Not proven optimal", tone: "neutral" } : result.status === "OPTIMAL" && optimized ? { text: "Proven optimal", tone: "accent" } : null,
  };

  const before = baseline?.total_weighted_penalty, after = selected?.total_weighted_penalty;
  const total = optimized && before != null && after != null ? changeOf(before, after, true, "points") : null;
  const percent = total && before != null && after != null ? percentChange(before, after) : null;
  const workload: SummaryItem = total && before != null && after != null ? {
    id: "workload", label: "Workload: baseline → selected", value: `${points(before)} → ${points(after)}`, tone: tone(total), badge: badgeOf(total),
    detail: `${total.direction === "improved" ? "Improved" : total.direction === "worse" ? "Worse" : "Unchanged"}${percent && total.direction !== "same" ? ` by ${percent}%` : ""}. Lower is better.${total.direction === "same" && result.optimization && !result.optimization.improved ? " No improvement was found; the baseline was kept." : ""}`,
  } : {
    id: "workload", label: "Workload: baseline → selected", value: NOT_AVAILABLE, tone: "neutral", badge: null,
    detail: optimized ? "The service did not return both scores." : `Feasibility mode has no baseline comparison.${after != null ? ` Score: ${points(after)}.` : ""}`,
  };

  const gap = metric("mean_gap"), minimum = metric("min_gap");
  const gaps: SummaryItem = now.mean_gap == null && now.min_gap == null ? {
    id: "gaps", label: "Preparation gaps", value: NOT_AVAILABLE, tone: "neutral", badge: null, detail: "Gap statistics were not returned for this result.",
  } : {
    id: "gaps", label: "Preparation gaps", tone: gap ? tone(gap.change) : "primary", badge: gap ? badgeOf(gap.change) : null,
    value: now.mean_gap != null ? `${hours(now.mean_gap)} average` : `${hours(now.median_gap)} median`,
    detail: `Minimum ${hours(now.min_gap)}${minimum && minimum.before != null ? ` (baseline ${hours(minimum.before)}${minimum.change.direction === "worse" ? ", shorter now" : ""})` : ""}.${gap && gap.before != null ? ` Baseline average ${hours(gap.before)}.` : ""}`,
  };

  const below = metric("difficult_below");
  const difficult: SummaryItem = now.difficult_below == null ? {
    id: "difficult", label: "Difficult pairs below target", value: NOT_AVAILABLE, tone: "neutral", badge: null, detail: "Difficult-pair counts were not returned.",
  } : {
    id: "difficult", label: "Difficult pairs below target", value: count(now.difficult_below), tone: below ? tone(below.change) : "neutral", badge: below ? badgeOf(below.change) : null,
    detail: `${stats ? `of ${count(stats.difficult_exam_student_pairs)} difficult student pair${stats.difficult_exam_student_pairs === 1 ? "" : "s"}` : "student pairs (total not available)"}${below && below.before != null ? `; baseline ${count(below.before)}` : ""}. Soft target.`,
  };

  const fairness = result.optimization?.fairness ?? null;
  const students: SummaryItem = fairness ? {
    id: "students", label: "Students improved / worsened / unchanged", tone: fairness.students_worsened === 0 ? "accent" : "neutral", badge: null,
    value: `${count(fairness.students_improved)} / ${count(fairness.students_worsened)} / ${count(fairness.students_unchanged)}`,
    detail: `${count(fairness.students)} students compared with the baseline workload score.`,
  } : {
    id: "students", label: "Students improved / worsened / unchanged", value: NOT_AVAILABLE, tone: "neutral", badge: null,
    detail: optimized ? "The service did not return a per-student comparison." : "Feasibility mode has no baseline to compare with.",
  };
  return [validation, solver, workload, gaps, difficult, students];
}

export const SCORE_INFO = [
  { code: "S1", name: "Preparation for difficult exams", simple: "Difficult exams get enough study time between them." },
  { code: "S2", name: "General preparation gaps", simple: "Any two exams get enough study time between them." },
  { code: "S3", name: "Same-day and short-gap exams", simple: "Avoids two exams on one day and very short breaks." },
  { code: "S4", name: "Demanding-exam concentration", simple: "Avoids packing the heaviest exams into the same few days." },
  { code: "S5", name: "Exam-count concentration", simple: "Avoids too many exams in a few days." },
] as const;

export type ScoreRow = { code: string; name: string; simple: string; weight: number | null; baseline: number | null; selected: number | null; weighted: number | null; change: Change };
export type ScoreTable = { rows: ScoreRow[]; hasScores: boolean; hasBaseline: boolean; total: { baseline: number | null; selected: number | null; change: Change } };

/** S1–S5 with applied weights, baseline and selected penalties (0–100, lower is better) and the direction of change. */
export function scoreTable(result: GeneratedTimetable): ScoreTable {
  const { baseline, selected } = scoreSources(result);
  const find = (score: WorkloadScore | null, code: string) => score?.penalties.find((penalty) => penalty.constraint === code) ?? null;
  const rows: ScoreRow[] = SCORE_INFO.map(({ code, name, simple }) => {
    const before = find(baseline, code), after = find(selected, code);
    return { code, name, simple, weight: after?.weight ?? before?.weight ?? null, baseline: before?.penalty_points ?? null, selected: after?.penalty_points ?? null,
      weighted: after?.weighted_points ?? null, change: changeOf(before?.penalty_points, after?.penalty_points, true, "points") };
  });
  const totalBefore = baseline?.total_weighted_penalty ?? null, totalAfter = selected?.total_weighted_penalty ?? null;
  return { rows, hasScores: baseline != null || selected != null, hasBaseline: baseline != null,
    total: { baseline: totalBefore, selected: totalAfter, change: changeOf(totalBefore, totalAfter, true, "points") } };
}

export type Concern = {
  id: "difficult" | "short" | "sameday"; title: string; explain: string;
  selected: number | null; baseline: number | null; of: number | null; change: Change; review: boolean | null;
};

/** Soft-target concerns. Totals are student-pair counts from real registrations; they are never a hard-rule result. */
export function concernsOf(result: GeneratedTimetable): Concern[] {
  const policy = result.analysis?.policy ?? null;
  const { baseline, selected } = scoreSources(result);
  const stats = result.analysis?.selected?.statistics ?? null;
  const before = comparableAnalysis(result) ? result.analysis?.baseline?.statistics ?? null : null;
  const make = (id: Concern["id"], title: string, explain: string, pick: (s: GapStatistics | null, w: WorkloadScore | null) => number | null, of: number | null): Concern => {
    const now = pick(stats, selected), was = result.solver.mode === "optimized" ? pick(before, baseline) : null;
    return { id, title, explain, selected: now, baseline: was, of, change: changeOf(was, now, true, "count"), review: now == null ? null : now > 0 };
  };
  return [
    make("difficult", "Difficult exams close together",
      policy ? `Student pairs sitting two difficult exams (difficulty ${policy.high_difficulty_minimum}–5) less than ${hours(policy.difficult_target_minutes)} apart.` : "Student pairs sitting two difficult exams closer than the difficult-exam preparation target.",
      (s, w) => s?.difficult_pairs_below_target ?? w?.difficult_pairs_below_target ?? null, stats?.difficult_exam_student_pairs ?? null),
    make("short", "Short gaps between exams",
      policy ? `Student exam pairs with less than ${hours(policy.short_gap_minutes)} between the end of one exam and the start of the next.` : "Student exam pairs with a very short gap between exams.",
      (s, w) => s?.short_gap_student_pairs ?? w?.short_gap_student_pairs ?? null, stats?.all_exam_student_pairs ?? null),
    make("sameday", "Same-day exams", "Student exam pairs with both exams on the same calendar day.",
      (s, w) => s?.same_day_student_pairs ?? w?.same_day_student_pairs ?? null, stats?.all_exam_student_pairs ?? null),
  ];
}

export type Regression = { id: string; label: string; detail: string };
export type RegressionReport = { available: boolean; reason: string; items: Regression[] };

/** Everything that got worse from the baseline to the selected timetable. Individual metrics can regress while the total improves. */
export function regressionsOf(result: GeneratedTimetable): RegressionReport {
  if (result.solver.mode !== "optimized") return { available: false, reason: "Feasibility mode has no baseline, so regressions cannot be measured.", items: [] };
  const metrics = compareMetrics(result);
  if (!metrics) return { available: false, reason: "The service did not return both the baseline and the selected scores.", items: [] };
  const items: Regression[] = metrics.filter((metric) => metric.change.direction === "worse").map((metric) => ({
    id: metric.id, label: metric.label, detail: `${display(metric.before, metric.unit)} → ${display(metric.after, metric.unit)} · ${metric.change.text}`,
  }));
  for (const row of scoreTable(result).rows) {
    if (row.change.direction === "worse") items.push({ id: row.code, label: `${row.code} · ${row.name} (penalty)`, detail: `${points(row.baseline)} → ${points(row.selected)} · ${row.change.text}` });
  }
  const fairness = result.optimization?.fairness ?? null;
  if (fairness && fairness.students_worsened > 0) {
    items.push({ id: "students", label: "Students with a higher workload score", detail: `${count(fairness.students_worsened)} of ${count(fairness.students)} students are worse off than in the baseline. Largest rise: ${points(fairness.worst_student_increase_points)} points.` });
  }
  return { available: true, reason: "", items };
}

export type PairFlag = "below" | "same_day" | "short";
/** Flags for one shared-candidate pair. Dates and gaps come from the returned sessions; nothing is inferred. */
export function pairFlags(pair: DifficultPair, shortGapMinutes: number | null | undefined): PairFlag[] {
  const flags: PairFlag[] = [];
  if (pair.below_target) flags.push("below");
  if (pair.first.date === pair.second.date) flags.push("same_day");
  if (shortGapMinutes != null && pair.gap_minutes < shortGapMinutes) flags.push("short");
  return flags;
}

/** Hard-rule problems from the independent validator, grouped by the rule they name. Other codes stay separate. */
export function violationsByRule(problems: GenerationProblem[]): { byRule: Record<string, GenerationProblem[]>; other: GenerationProblem[] } {
  const byRule: Record<string, GenerationProblem[]> = {};
  const other: GenerationProblem[] = [];
  for (const problem of problems) {
    if (/^H(?:[1-9]|1[01])$/.test(problem.rule)) (byRule[problem.rule] ??= []).push(problem);
    else other.push(problem);
  }
  return { byRule, other };
}

/** The number shown on the Issues tab: hard failure (1), each soft concern that needs review, and regressions (1). */
export function reviewCount(result: GeneratedTimetable): number {
  const hard = result.validation.passed === false || result.violations.length > 0 ? 1 : 0;
  return hard + concernsOf(result).filter((concern) => concern.review === true).length + (regressionsOf(result).items.length > 0 ? 1 : 0);
}
