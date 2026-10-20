"use client";

import { useState } from "react";
import { Badge, Card, EmptyState } from "@/components/console/ui";
import type { GeneratedTimetable } from "@/lib/examination-generation-types";
import { ALL_COHORTS, cohortLabel, cohortStatistics, comparableAnalysis, EVALUATION_METRICS, gapValue,
  metricChange, metricValue, type AnalysisExam, type AnalysisRun, type CohortFilter } from "@/lib/examination-evaluation";
import { fieldClass, secondaryButton } from "./period-calendar";

const cell = "px-4 py-3 text-left text-sm text-muted align-top";
const heading = "px-4 py-3 text-left text-xs font-semibold text-muted";
const examLabel = (exam: AnalysisExam) => `${exam.module_code} · Session ${exam.session_number} · ${exam.date} · ${exam.start_time.slice(0, 5)}–${exam.end_time.slice(0, 5)}`;

/** Blocks the Results dashboard places in different tabs. Standalone use renders all of them. */
export type EvaluationSection = "evaluation" | "pairs" | "inputs";
const ALL_SECTIONS: EvaluationSection[] = ["evaluation", "pairs", "inputs"];

export function EvaluationDashboard({ result, sections = ALL_SECTIONS, filter: controlledFilter, onFilterChange, onOpenValidation }: {
  result: GeneratedTimetable; sections?: EvaluationSection[];
  /** Optional controlled cohort filter, so the result actions and this dashboard share one selection. */
  filter?: CohortFilter; onFilterChange?: (filter: CohortFilter) => void; onOpenValidation?: () => void;
}) {
  const [localFilter, setLocalFilter] = useState<CohortFilter>(ALL_COHORTS);
  const filter = controlledFilter ?? localFilter;
  const setFilter = onFilterChange ?? setLocalFilter;
  const show = (section: EvaluationSection) => sections.includes(section);
  const [pairSource, setPairSource] = useState<"selected" | "baseline">("selected");
  const [downloading, setDownloading] = useState(false);
  const [downloadMessage, setDownloadMessage] = useState("");
  const a = result.analysis;
  if (!a) return <Card title="Student Workload Evaluation"><EmptyState title="Detailed evaluation unavailable">This result did not include the optional analysis. Existing workload and hard-validation results remain available below. Generate again after the Examination Service supports analysis.</EmptyState></Card>;
  const cohorts = a.selected?.cohorts ?? a.baseline?.cohorts ?? [];
  const years = [...new Set(cohorts.flatMap((c) => c.academic_year == null ? [] : [c.academic_year]))].sort();
  const specializations = [...new Set(cohorts.flatMap((c) => c.specialization ? [c.specialization] : []))].sort();
  const before = cohortStatistics(a.baseline, filter);
  const after = cohortStatistics(a.selected, filter);
  const comparable = comparableAnalysis(result);
  const pairs = a[pairSource];
  const download = async () => {
    setDownloading(true); setDownloadMessage("");
    try {
      const { downloadEvaluationPdf } = await import("@/lib/examination-evaluation-pdf");
      await downloadEvaluationPdf(result, filter);
      setDownloadMessage("Evaluation report download requested. Check your browser downloads.");
    } catch { setDownloadMessage("The evaluation PDF could not be downloaded. Check that names use supported Latin characters and retry."); }
    finally { setDownloading(false); }
  };
  return <section className="space-y-4" aria-label="Student Workload Evaluation">
    {show("evaluation") && <Card title="Evaluation Dashboard" description="Student preparation gaps and estimated cognitive workload from actual registrations and assigned examination sittings.">
      <div className="space-y-4 p-5">
        <div className="flex flex-wrap items-center justify-between gap-3"><Badge tone="neutral">UNSAVED · {result.status}</Badge>
          <button type="button" className={secondaryButton} disabled={downloading} onClick={download} aria-busy={downloading}>{downloading ? "Preparing report…" : "Download Evaluation Report PDF"}</button></div>
        {downloadMessage && <p role="status" className="text-sm text-muted">{downloadMessage}</p>}
        <p className="text-sm leading-6 text-muted">{result.status === "OPTIMAL" ? "The solver reported OPTIMAL for its modeled objective." : "Optimality is not proven for this result."} Soft workload targets are preferences, not guarantees or hard-rule violations. These scores are a research workload proxy, not a validated measurement of student stress.</p>
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">{[
          ["Difficult exam", `Difficulty ${a.policy.high_difficulty_minimum}–5`],
          ["Short gap", `Less than ${gapValue(a.policy.short_gap_minutes)}`],
          ["General preparation target", gapValue(a.policy.preparation_target_minutes)],
          ["Difficult-exam target", gapValue(a.policy.difficult_target_minutes)],
        ].map(([label, value]) => <div key={label} className="rounded-xl border border-line bg-background p-3"><p className="text-xs text-muted">{label}</p><p className="mt-1 text-sm font-semibold text-ink">{value}</p></div>)}</div>
        <p className="text-xs leading-5 text-muted">S1 penalizes close pairs when both exams meet the difficulty threshold. S2 penalizes insufficient general preparation. S3 penalizes same-day or very short-gap pairs. S4 considers difficulty, credits and duration concentration; S5 considers exam-count concentration. The applied S1–S5 weights are shown separately below. Zero weights do not influence the objective.</p>
        {onOpenValidation
          ? <button type="button" onClick={onOpenValidation} className="text-left text-sm font-semibold text-primary-strong underline">View independent H1–H11 validation separately</button>
          : <a href="#hard-validation-summary" className="text-sm font-semibold text-primary-strong underline">View independent H1–H11 validation separately</a>}
        <div className="grid gap-3 sm:grid-cols-3" role="group" aria-label="Evaluation cohort filters">
          <label className="text-xs font-medium text-muted">Student academic year<select className={fieldClass} value={filter.year} onChange={(e) => setFilter({ ...filter, year: e.target.value })}><option value="">All years</option>{years.map((y) => <option key={y} value={y}>Year {y}</option>)}</select></label>
          <label className="text-xs font-medium text-muted">Student specialization<select className={fieldClass} value={filter.specialization} onChange={(e) => setFilter({ ...filter, specialization: e.target.value })}><option value="">All specializations</option>{specializations.map((s) => <option key={s}>{s}</option>)}</select></label>
          <label className="text-xs font-medium text-muted">Registration group<select className={fieldClass} value={filter.registration} onChange={(e) => setFilter({ ...filter, registration: e.target.value as CohortFilter["registration"] })}><option value="all">All registered students</option><option value="normal_only">Normal-only students</option><option value="includes_repeat">Students with repeat registrations</option></select></label>
        </div>
        <p className="text-xs leading-5 text-muted">Filters use actual student membership, not module specialization labels. “Students with repeat registrations” includes anyone with at least one repeat registration in this cycle, and considers all their scheduled exams. It is disjoint from “Normal-only”. Filters change this comparison only; the full timetable is unchanged. {a.cohort_membership_available} registered students have both a supported year and specialization.</p>
        <p className="text-sm font-medium text-ink">{cohortLabel(filter)}</p>
        {!before && !after ? <EmptyState title="No reliable evaluation for this cohort">This cohort is empty, unavailable or outside the returned cohort limit. No metric is estimated from module membership.</EmptyState> : <>
          <div className="grid gap-3 sm:grid-cols-3">{([
            ["Average preparation gap", gapValue(after?.mean_gap_minutes)], ["Minimum preparation gap", gapValue(after?.minimum_gap_minutes)],
            ["Difficult pairs below target", metricValue(after?.difficult_pairs_below_target)],
          ] as const).map(([label, value]) => <div key={label} className="rounded-xl border border-line p-3"><p className="text-xs text-muted">Selected · {label}</p><p className="mt-1 text-lg font-semibold text-ink">{value}</p></div>)}</div>
          <details className="rounded-xl border border-line" open><summary className="cursor-pointer p-4 text-sm font-semibold">Baseline versus selected: gaps, denominators and workload</summary>
            <div className="overflow-x-auto"><table className="w-full min-w-[42rem]"><thead className="bg-background"><tr>{["Metric", "Basic feasibility baseline", result.solver.mode === "optimized" ? "Selected by optimization" : "Basic result", "Change (gap differences in minutes)"].map((h) => <th key={h} className={heading}>{h}</th>)}</tr></thead><tbody className="divide-y divide-line">{EVALUATION_METRICS.map((m) => <tr key={m.key}><th scope="row" className={cell}>{m.label}</th><td className={cell}>{m.gap ? gapValue(before?.[m.key]) : metricValue(before?.[m.key])}</td><td className={cell}>{m.gap ? gapValue(after?.[m.key]) : metricValue(after?.[m.key])}</td><td className={`${cell} ${comparable && m.compare && before?.[m.key] != null && after?.[m.key] != null && metricChange(before[m.key], after[m.key], m.lowerBetter).startsWith("Regressed") ? "text-danger" : ""}`}>{comparable && m.compare ? metricChange(before?.[m.key], after?.[m.key], m.lowerBetter) : "—"}</td></tr>)}</tbody></table></div>
          </details>
        </>}
        <p className="text-xs leading-5 text-muted">Average = sum of end-to-next-start gaps ÷ consecutive-gap count, pooled across registered students. Students with one exam contribute no gaps. End time includes reserved reading time. Median and lower decile use the same consecutive gaps; the lower decile uses index floor((count − 1) / 10). Pair counts include every exam pair for each student and can count a module pair for several students.</p>
        {!comparable && <p className="rounded-xl bg-secondary-soft p-3 text-sm text-secondary-ink">No comparable baseline/selected analysis is available. {a.baseline_unavailable_reason} {a.selected_unavailable_reason}</p>}
        {comparable && <p className="text-xs text-muted">Both runs use the same frozen input and effective configuration fingerprints. The baseline is this optimization run’s feasibility solution, not a separately generated timetable. Individual metrics can regress while the weighted total improves.</p>}
        {(a.selected?.cohorts_truncated || a.baseline?.cohorts_truncated) && <p className="text-sm text-secondary-ink">Cohort summaries are limited to {a.limits.max_cohorts}; some intersections are unavailable.</p>}
        <details className="rounded-xl border border-line"><summary className="cursor-pointer p-4 text-sm font-semibold">S1–S5 workload comparison · full cycle</summary>
          <p className="px-4 text-xs text-muted">These are the full-cycle independent scores, unaffected by the cohort filters. Each cell shows normalized penalty / weighted contribution. Lower scores are better.</p>
          <div className="overflow-x-auto"><table className="w-full min-w-[40rem]"><thead><tr>{["Score", "Weight baseline / selected", "Baseline penalty / weighted", "Selected penalty / weighted", "Weighted change"].map((h) => <th key={h} className={heading}>{h}</th>)}</tr></thead><tbody className="divide-y divide-line">{["S1", "S2", "S3", "S4", "S5"].map((code) => {
            const b = a.baseline?.workload?.penalties.find((p) => p.constraint === code);
            const s = a.selected?.workload?.penalties.find((p) => p.constraint === code);
            return <tr key={code}><th scope="row" className={cell}>{code}</th><td className={cell}>{metricValue(b?.weight)} / {metricValue(s?.weight)}</td><td className={cell}>{metricValue(b?.penalty_points)} / {metricValue(b?.weighted_points)}</td><td className={cell}>{metricValue(s?.penalty_points)} / {metricValue(s?.weighted_points)}</td><td className={cell}>{comparable ? metricChange(b?.weighted_points, s?.weighted_points, true) : "No comparison"}</td></tr>;
          })}<tr><th scope="row" className={cell}>Weighted total</th><td className={cell}>—</td><td className={cell}>{metricValue(a.baseline?.workload?.total_weighted_penalty)}</td><td className={cell}>{metricValue(a.selected?.workload?.total_weighted_penalty)}</td><td className={cell}>{comparable ? metricChange(a.baseline?.workload?.total_weighted_penalty, a.selected?.workload?.total_weighted_penalty, true) : "No comparison"}</td></tr></tbody></table></div>
        </details>
      </div>
    </Card>}
    {show("pairs") && <Card title="Difficult-exam pairs" description="Full-cycle pair details. Cohort filters above apply to the summary statistics, not this table.">
      <div className="space-y-3 p-5"><label className="block max-w-xs text-xs font-medium text-muted">Timetable<select className={fieldClass} value={pairSource} onChange={(e) => setPairSource(e.target.value as "selected" | "baseline")}><option value="selected">Selected timetable</option><option value="baseline">Basic feasibility baseline</option></select></label>
        <PairTable run={pairs} reason={a[`${pairSource}_unavailable_reason`]} />
      </div>
    </Card>}
    {show("inputs") && <details className="rounded-2xl border border-line bg-surface p-4"><summary className="cursor-pointer font-semibold text-ink">Module difficulty · frozen generation inputs</summary>
      <div className="mt-3 overflow-x-auto"><table className="w-full min-w-[32rem]"><thead><tr>{["Module", "Module academic group", "Difficulty (1–5)"].map((h) => <th key={h} className={heading}>{h}</th>)}</tr></thead><tbody className="divide-y divide-line">{a.modules.map((m) => <tr key={m.module_code}><th scope="row" className={cell}>{m.module_code} · {m.module_name}</th><td className={cell}>Year {m.academic_year} · Semester {m.semester}<span className="block text-xs">{m.specializations.join(", ") || "Specialization unavailable"}</span></td><td className={cell}><Badge tone={m.difficult ? "primary" : "neutral"}>{m.difficulty ?? "Not available"}{m.difficult ? " · Difficult" : ""}</Badge></td></tr>)}</tbody></table></div>
    </details>}
    {show("inputs") && <details className="rounded-2xl border border-line bg-surface p-4 text-xs text-muted"><summary className="cursor-pointer font-semibold">Evaluation provenance and limits</summary><dl className="mt-3 space-y-2 break-all">{[["Input fingerprint", a.input_fingerprint], ["Effective configuration", a.configuration_fingerprint], ["Scoring version", a.policy.scoring_version], ["Scoring fingerprint", a.policy.scoring_fingerprint], ["Analysis limit", `${a.limits.max_student_pairs} student-exam pairs per run; ${a.limits.max_pair_rows} pair rows; ${a.limits.max_cohorts} cohorts`]].map(([k, v]) => <div key={k}><dt>{k}</dt><dd className="font-mono text-ink">{v || "Not available"}</dd></div>)}</dl></details>}
  </section>;
}

function PairTable({ run, reason }: { run: AnalysisRun | null; reason: string | null }) {
  if (!run) return <EmptyState title="Pair details unavailable">{reason}</EmptyState>;
  if (!run.difficult_pairs.length) return <EmptyState title="No shared difficult-exam pairs">No two difficult examinations were taken by the same registered student in this timetable.</EmptyState>;
  return <details open><summary className="cursor-pointer text-sm font-semibold text-ink">{run.difficult_pairs.length} of {run.difficult_pair_rows_total} session-pair groups{run.difficult_pair_rows_truncated ? " · Limited to closest pairs, below-target pairs first" : ""}</summary>
    <div className="mt-3 overflow-x-auto"><table className="w-full min-w-[54rem]"><thead className="bg-background"><tr>{["First examination", "Second examination", "End-to-start gap", "Shared candidates"].map((h) => <th key={h} className={heading}>{h}</th>)}</tr></thead><tbody className="divide-y divide-line">{run.difficult_pairs.map((p) => <tr key={`${p.first.module_code}-${p.first.session_number}-${p.second.module_code}-${p.second.session_number}`}><td className={cell}>{examLabel(p.first)}</td><td className={cell}>{examLabel(p.second)}</td><td className={`${cell} ${p.below_target ? "text-secondary-ink" : ""}`}>{gapValue(p.gap_minutes)}<span className="block text-xs">{p.gap_minutes} minutes · {p.below_target ? "Below soft target" : "Meets soft target"}</span></td><td className={cell}>{p.shared_candidates}<span className="block text-xs">{p.normal_only_candidates} normal-only · {p.repeat_candidates} with repeat registrations</span></td></tr>)}</tbody></table></div>
  </details>;
}
