"use client";

import { useState } from "react";
import { Badge, EmptyState, formatDate } from "@/components/console/ui";
import type { AnalysisExam, CohortFilter } from "@/lib/examination-evaluation";
import type { GeneratedTimetable, GenerationFairness, GenerationProblem } from "@/lib/examination-generation-types";
import { HARD_RULES } from "@/lib/examination-presentation";
import {
  type Change, concernsOf, count, hours, NOT_AVAILABLE, type PairFlag, pairFlags, points, regressionsOf, scoreTable, violationsByRule,
} from "@/lib/examination-results";
import { EvaluationDashboard } from "./evaluation-dashboard";
import { fieldClass } from "./period-calendar";
import { PreferencesSummary } from "./preferences-summary";

const cell = "px-4 py-3 text-left text-sm text-muted align-top";
const heading = "px-4 py-3 text-left text-xs font-semibold text-muted";
const FLAGS: Record<PairFlag, string> = { below: "Below target", same_day: "Same day", short: "Short gap" };
const problemLines = (items: GenerationProblem[]) => items.map((problem) => `${problem.rule}: ${problem.message}`);

/** The words always carry the meaning; the symbol and colour only support them. */
export function ChangeText({ change }: { change: Change }) {
  if (change.direction === "unknown") return <span className="text-muted">{NOT_AVAILABLE}</span>;
  const style = change.direction === "improved" ? "text-accent-ink" : change.direction === "worse" ? "font-semibold text-secondary-ink" : "text-muted";
  const glyph = change.direction === "improved" ? "✓" : change.direction === "worse" ? "!" : "=";
  return <span className={`inline-flex items-baseline gap-1 ${style}`}><span aria-hidden="true">{glyph}</span>{change.text}</span>;
}

function Messages({ title, items }: { title: string; items: string[] }) {
  if (!items.length) return null;
  return <div><h4 className="text-sm font-semibold text-ink">{title}</h4><ul className="mt-2 list-disc space-y-1 pl-5 text-sm leading-6 text-muted">{items.map((message, index) => <li key={index}>{message}</li>)}</ul></div>;
}

function PairTable({ result, source }: { result: GeneratedTimetable; source: "selected" | "baseline" }) {
  const a = result.analysis;
  const run = a?.[source] ?? null;
  if (!a || !run) return <EmptyState title="Pair details not available">{a ? a[`${source}_unavailable_reason`] ?? "No pair details were returned." : "The optional evaluation was not included in this result."}</EmptyState>;
  if (!run.difficult_pairs.length) return <EmptyState title="No shared difficult-exam pairs">No two difficult examinations were taken by the same registered student in this timetable.</EmptyState>;
  const names = new Map(a.modules.map((module) => [module.module_code, module.module_name]));
  const exam = (e: AnalysisExam) => <>
    <span className="font-mono font-semibold text-primary-strong">{e.module_code}</span>{names.get(e.module_code) ? ` · ${names.get(e.module_code)}` : ""}
    <span className="block text-xs">{formatDate(e.date)} · {e.start_time.slice(0, 5)}–{e.end_time.slice(0, 5)} · Session {e.session_number}</span>
  </>;
  return <div className="space-y-2">
    <p className="text-sm font-medium text-ink">{run.difficult_pairs.length} of {run.difficult_pair_rows_total} session-pair groups{run.difficult_pair_rows_truncated ? " · limited to the closest pairs, below-target pairs first" : ""}</p>
    <div className="overflow-x-auto rounded-xl border border-line"><table className="w-full min-w-[46rem] text-left">
      <caption className="sr-only">Pairs of difficult exams sat by the same registered students, with the gap between them</caption>
      <thead className="border-b border-line bg-background"><tr>{["First exam", "Second exam", "Gap (end to next start)", "Students who sit both"].map((label) => <th key={label} scope="col" className={heading}>{label}</th>)}</tr></thead>
      <tbody className="divide-y divide-line">{run.difficult_pairs.map((pair) => {
        const flags = pairFlags(pair, a.policy.short_gap_minutes);
        return <tr key={`${pair.first.module_code}-${pair.first.session_number}-${pair.second.module_code}-${pair.second.session_number}`} className={pair.below_target ? "bg-secondary-soft" : undefined}>
          <th scope="row" className={`${cell} font-normal`}>{exam(pair.first)}</th>
          <td className={cell}>{exam(pair.second)}</td>
          <td className={cell}><span className="font-semibold text-ink">{hours(pair.gap_minutes)}</span><span className="block text-xs">{count(pair.gap_minutes)} minutes</span>
            <span className="mt-1 flex flex-wrap gap-1">{flags.length ? flags.map((flag) => <Badge key={flag} tone="neutral">{FLAGS[flag]}</Badge>) : <Badge tone="accent">Meets target</Badge>}</span></td>
          <td className={cell}><span className="font-semibold text-ink">{count(pair.shared_candidates)}</span><span className="block text-xs">{count(pair.normal_only_candidates)} normal-only · {count(pair.repeat_candidates)} with repeat registrations</span></td>
        </tr>;
      })}</tbody>
    </table></div>
  </div>;
}

/** Hard-rule failures and soft-target concerns, kept apart: only the first group can make a timetable unusable. */
export function IssuesPanel({ result, onOpenHard }: { result: GeneratedTimetable; onOpenHard: () => void }) {
  const [source, setSource] = useState<"selected" | "baseline">("selected");
  const hardProblems = [...result.validation.violations, ...result.violations];
  const failed = result.validation.passed === false || hardProblems.length > 0;
  const concerns = concernsOf(result);
  const report = regressionsOf(result);
  const policy = result.analysis?.policy ?? null;
  return <div className="space-y-6">
    <section aria-labelledby="issues-hard" className="space-y-2">
      <h3 id="issues-hard" className="flex flex-wrap items-center gap-2 text-sm font-semibold text-ink">Hard-rule failures <Badge tone="danger">Mandatory rules</Badge></h3>
      {failed ? <div role="alert" className="rounded-xl border border-danger bg-danger-soft p-4 text-sm leading-6 text-danger">
        <p className="font-semibold">The independent validator reported hard-rule problems. Do not use this timetable.</p>
        <ul className="mt-2 list-disc pl-5">{problemLines(hardProblems).slice(0, 5).map((line, index) => <li key={index}>{line}</li>)}</ul>
        {hardProblems.length > 5 && <p className="mt-1">…and {count(hardProblems.length - 5)} more.</p>}
        <button type="button" onClick={onOpenHard} className="mt-2 min-h-11 font-semibold underline underline-offset-2">Open Hard Constraints</button>
      </div> : result.validation.passed === true ? <p className="rounded-xl bg-accent p-3 text-sm text-accent-ink">No hard-rule failures were reported by the independent validator.</p>
        : <p className="rounded-xl bg-secondary-soft p-3 text-sm text-secondary-ink">Hard rules were not evaluated because no timetable was available.</p>}
    </section>

    <section aria-labelledby="issues-soft" className="space-y-3">
      <h3 id="issues-soft" className="flex flex-wrap items-center gap-2 text-sm font-semibold text-ink">Soft-target concerns <Badge tone="neutral">Preferences, not rules</Badge></h3>
      <p className="text-sm leading-6 text-muted">These are goals the optimizer tries to improve. A concern here does not make the timetable invalid. Counts are student exam pairs from real registrations, so one pair of modules can count once for every student who sits both.</p>
      <ul className="grid gap-3 lg:grid-cols-3">{concerns.map((concern) => <li key={concern.id} className="rounded-xl border border-line p-4">
        <div className="flex flex-wrap items-start justify-between gap-2"><h4 className="text-sm font-semibold text-ink">{concern.title}</h4>
          {concern.review === null ? <Badge tone="neutral">Not available</Badge> : concern.review ? <Badge tone="neutral">Needs review</Badge> : <Badge tone="accent">No concern</Badge>}</div>
        <p className="mt-1 text-xs leading-5 text-muted">{concern.explain}</p>
        <dl className="mt-3 grid grid-cols-2 gap-2">
          <div><dt className="text-xs text-muted">Selected timetable</dt><dd className="text-sm font-semibold text-ink">{count(concern.selected)}{concern.of != null && <span className="text-xs font-normal text-muted"> of {count(concern.of)}</span>}</dd></div>
          <div><dt className="text-xs text-muted">Baseline</dt><dd className="text-sm font-semibold text-ink">{count(concern.baseline)}</dd></div>
        </dl>
        <p className="mt-2 text-xs"><ChangeText change={concern.change} /></p>
      </li>)}</ul>
    </section>

    <section aria-labelledby="issues-regressions" className="space-y-2">
      <h3 id="issues-regressions" className="text-sm font-semibold text-ink">Workload regressions compared with the baseline</h3>
      {!report.available ? <p className="rounded-xl bg-background p-3 text-sm text-muted">{NOT_AVAILABLE}. {report.reason}</p>
        : report.items.length === 0 ? <p className="rounded-xl bg-accent p-3 text-sm text-accent-ink">No measured value is worse than in the baseline.</p>
          : <>
            <p className="text-sm leading-6 text-muted">The total score can improve while single measures get worse. These measures are worse than in the baseline.</p>
            <ul className="space-y-2">{report.items.map((item) => <li key={item.id} className="rounded-xl border border-line bg-secondary-soft p-3 text-sm text-secondary-ink"><span className="font-semibold">{item.label}</span><span className="block">{item.detail}</span></li>)}</ul>
          </>}
    </section>

    <section aria-labelledby="issues-pairs" className="space-y-3">
      <h3 id="issues-pairs" className="text-sm font-semibold text-ink">Difficult exam pairs sat by the same students</h3>
      <p className="text-sm leading-6 text-muted">{policy ? `Target: at least ${hours(policy.difficult_target_minutes)} between two difficult exams. ` : ""}Each row is two difficult exams that the same registered students sit. Specialization is never used to guess a clash. The service lists these pairs only for difficult exams; short gaps and same-day pairs between other exams are reported as the totals above.</p>
      {result.solver.mode === "optimized" && <label className="block max-w-xs text-xs font-medium text-muted">Show pairs from
        <select className={fieldClass} value={source} onChange={(event) => setSource(event.target.value as "selected" | "baseline")}><option value="selected">Selected timetable</option><option value="baseline">Basic feasibility baseline</option></select></label>}
      <PairTable result={result} source={source} />
    </section>
  </div>;
}

/** Overall result of the independent validator. Per-rule PASS flags are never invented; only reported violations are shown. */
export function HardPanel({ result }: { result: GeneratedTimetable }) {
  const passed = result.validation.passed;
  const { byRule, other } = violationsByRule(result.validation.violations);
  return <div className="space-y-5">
    <div className={`rounded-2xl border p-4 ${passed === true ? "border-line bg-accent text-accent-ink" : passed === false ? "border-danger bg-danger-soft text-danger" : "border-line bg-secondary-soft text-secondary-ink"}`}>
      <h3 id="hard-validation-summary" className="text-base font-semibold">Independent hard-rule validation: {passed === true ? "Passed" : passed === false ? "Failed" : "Not evaluated"}</h3>
      <p className="mt-1 text-sm leading-6">{passed === true ? "The Examination Service re-checked the final timetable with a separate validator. It reported no violations of H1–H11."
        : passed === false ? "The independent validator reported violations. Do not use this timetable."
          : "No timetable was available to validate."}</p>
    </div>
    <p className="text-sm leading-6 text-muted">H1–H11 are mandatory. No setting and no optimization weight can switch them off. The service returns one overall result and a list of violations; a rule with no reported violation has no individual status, so none is shown as individually passed.</p>
    <ul className="space-y-2">{HARD_RULES.map(([code, title, description]) => {
      const found = byRule[code] ?? [];
      return <li key={code}><details id={`rule-${code}`} open={found.length > 0} className="scroll-mt-24 rounded-lg border border-line p-3">
        <summary className="flex cursor-pointer flex-wrap items-center justify-between gap-2 text-sm font-semibold text-ink"><span>{code} · {title}</span>
          {found.length > 0 && <Badge tone="danger">{count(found.length)} violation{found.length === 1 ? "" : "s"} reported</Badge>}</summary>
        <p className="mt-2 text-sm leading-6 text-muted">{description}</p>
        {found.length > 0 && <ul className="mt-2 list-disc pl-5 text-sm leading-6 text-danger">{found.map((problem, index) => <li key={index}>{problem.message}</li>)}</ul>}
      </details></li>;
    })}</ul>
    <Messages title="Other validation messages" items={problemLines(other)} />
    <Messages title="Generation violations" items={problemLines(result.violations)} />
    <Messages title="Solver diagnostics" items={result.diagnostics} />
  </div>;
}

/** S1–S5 as goals, not rules: penalties, applied weights, baseline versus selected, in plain English. */
export function LoadPanel({ result, cohort, onCohortChange, onOpenHard }: { result: GeneratedTimetable; cohort: CohortFilter; onCohortChange: (filter: CohortFilter) => void; onOpenHard: () => void }) {
  const table = scoreTable(result);
  const optimization = result.optimization;
  const compare = table.hasBaseline;
  return <div className="space-y-5">
    <div className="rounded-xl bg-background p-4 text-sm leading-6 text-muted">
      <p><strong className="text-ink">What these scores mean.</strong> Each score is a penalty from 0 to 100 that estimates how heavy the exam schedule is for students on one goal. Lower is better, and 0 means no concern for that goal.</p>
      <p className="mt-2">They are goals, not rules. A high score never makes a timetable invalid; the mandatory rules are checked separately under Hard Constraints. The weight shows how much a goal counts when the solver compares timetables. A weight of 0 means the goal is ignored.</p>
      <p className="mt-2 text-xs">Research estimates only. They are not a validated measure of student stress.</p>
    </div>
    {result.solver.mode === "optimized" && optimization && <div className="space-y-1 text-sm text-muted">
      <p className="font-semibold text-ink">{optimization.improved ? "An improved timetable was selected" : "No improvement reported; the selected result may retain the baseline"}</p>
      <p>Optimization status: {optimization.status ?? "Not returned"}</p>
    </div>}
    {!table.hasScores ? <EmptyState title="No workload scores returned">The service could not score a timetable for this run.</EmptyState> : <div className="overflow-x-auto rounded-xl border border-line"><table className="w-full min-w-[46rem] text-left">
      <caption className="sr-only">S1 to S5 workload goals with applied weight, baseline and selected penalty, and the change between them</caption>
      <thead className="border-b border-line bg-background"><tr>
        <th scope="col" className={heading}>Goal</th><th scope="col" className={heading}>Weight</th>
        {compare && <th scope="col" className={heading}>Baseline penalty</th>}<th scope="col" className={heading}>{compare ? "Selected penalty" : "Penalty"}</th>
        {compare && <th scope="col" className={heading}>Change</th>}<th scope="col" className={heading}>Weighted contribution</th>
      </tr></thead>
      <tbody className="divide-y divide-line">
        {table.rows.map((row) => <tr key={row.code}>
          <th scope="row" className="px-4 py-3 text-left text-sm font-medium text-ink"><span className="font-mono text-primary-strong">{row.code}</span> · {row.name}<span className="mt-1 block text-xs font-normal text-muted">{row.simple}</span></th>
          <td className={cell}>{count(row.weight)}</td>
          {compare && <td className={cell}>{points(row.baseline)}</td>}<td className={cell}>{points(row.selected)}</td>
          {compare && <td className={cell}><ChangeText change={row.change} /></td>}<td className={cell}>{points(row.weighted)}</td>
        </tr>)}
        <tr className="bg-background">
          <th scope="row" className="px-4 py-3 text-left text-sm font-semibold text-ink">Total weighted penalty<span className="mt-1 block text-xs font-normal text-muted">Lower is better.</span></th>
          <td className={cell}>—</td>{compare && <td className={cell}>{points(table.total.baseline)}</td>}<td className={cell}>{points(table.total.selected)}</td>
          {compare && <td className={cell}><ChangeText change={table.total.change} /></td>}<td className={cell}>{points(table.total.selected)}</td>
        </tr>
      </tbody>
    </table></div>}
    <p className="text-xs leading-5 text-muted">Penalty = average over students on a 0–100 scale. Weighted contribution = weight × penalty ÷ 100. {compare ? "The baseline is the first valid timetable found in this run, before optimization." : "Feasibility mode has no baseline, so there is no comparison."} {NOT_AVAILABLE} means the service did not return that value.</p>
    {result.analysis ? <details className="rounded-xl border border-line"><summary className="cursor-pointer p-4 text-sm font-semibold text-ink">Preparation-gap statistics and student-group filters</summary>
      <div className="p-4 pt-0"><EvaluationDashboard result={result} sections={["evaluation"]} filter={cohort} onFilterChange={onCohortChange} onOpenValidation={onOpenHard} /></div>
    </details> : <p className="rounded-xl bg-background p-3 text-sm text-muted">{NOT_AVAILABLE}: this result did not include the optional preparation-gap evaluation.</p>}
  </div>;
}

function Fairness({ fairness }: { fairness: GenerationFairness }) {
  const facts = [
    ["Fairness protection", fairness.protection_applied ? "Applied" : "Not applied"],
    ["Fairness limits", fairness.limits_satisfied === null ? "Not evaluated" : fairness.limits_satisfied ? "Satisfied" : "Not satisfied"],
    ["Candidates compared", count(fairness.students)],
    ["Improved / worsened / unchanged", `${count(fairness.students_improved)} / ${count(fairness.students_worsened)} / ${count(fairness.students_unchanged)}`],
    ["Worst normalized penalty: baseline → selected", `${points(fairness.baseline_worst_normalized_penalty)} → ${points(fairness.selected_worst_normalized_penalty)}`],
    ["Allowed individual increase (points)", points(fairness.maximum_individual_increase_points)],
    ["Worst individual increase (points)", points(fairness.worst_student_increase_points)],
  ];
  return <div>{!fairness.protection_applied && <p className="mb-3 rounded-xl bg-secondary-soft p-3 text-sm leading-6 text-secondary-ink">Fairness protection was not applied. The comparison is descriptive; individual fairness limits were not enforced for the selected timetable.</p>}
    <dl className="grid gap-4 sm:grid-cols-2">{facts.map(([label, text]) => <div key={label}><dt className="text-xs font-medium text-muted">{label}</dt><dd className="mt-1 text-sm font-semibold text-ink">{text}</dd></div>)}</dl></div>;
}

/** Fairness, the solver run, effective preferences, notices and the evaluation's inputs and provenance. */
export function DetailsPanel({ result }: { result: GeneratedTimetable }) {
  const optimization = result.optimization;
  const optimized = result.solver.mode === "optimized";
  const run: [string, string][] = [
    ["Mode", optimized ? "Optimized" : "Feasibility"], ["Solver status", result.status],
    ["Runtime", `${points(result.solver.wall_time_seconds)} s of a ${points(result.solver.time_limit_seconds)} s limit`],
    ["Optimization status", optimization?.status ?? NOT_AVAILABLE],
    ["Objective bound", optimization ? points(optimization.objective_bound_units) : NOT_AVAILABLE],
    ["Relative gap", optimization?.relative_gap == null ? NOT_AVAILABLE : `${points(optimization.relative_gap * 100)}%`],
  ];
  return <div className="space-y-6">
    <section aria-labelledby="details-run" className="space-y-2">
      <h3 id="details-run" className="text-sm font-semibold text-ink">Solver run</h3>
      <dl className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">{run.map(([label, text]) => <div key={label}><dt className="text-xs font-medium text-muted">{label}</dt><dd className="mt-1 text-sm font-semibold text-ink">{text}</dd></div>)}</dl>
      {optimized && <p className="text-xs leading-5 text-muted">A large relative gap means the solver could not prove how close this result is to the best possible one. It is not a quality score.</p>}
    </section>
    {optimized && <section aria-labelledby="details-fairness" className="space-y-2">
      <h3 id="details-fairness" className="text-sm font-semibold text-ink">Fairness comparison</h3>
      <p className="text-xs leading-5 text-muted">Optimization lowers the average workload score of all students. Fairness protection is an optional limit on how much any one student may be made worse. Aggregate counts only; individual student records are excluded.</p>
      {optimization?.fairness ? <Fairness fairness={optimization.fairness} /> : <EmptyState title="No fairness details returned">The service did not return a fairness comparison for this run.</EmptyState>}
    </section>}
    <PreferencesSummary snapshot={result.inputs.scheduling_preferences} />
    {result.notices.length > 0 && <Messages title="Service notices" items={result.notices} />}
    {result.excluded_registrations.total > 0 && <div className="rounded-xl bg-secondary-soft p-4 text-sm text-secondary-ink">
      <p className="font-semibold">{count(result.excluded_registrations.total)} registrations excluded from this cycle</p>
      <p className="mt-1">{count(result.excluded_registrations.normal)} normal · {count(result.excluded_registrations.repeat)} repeat</p>
      <p className="mt-2 leading-6">{result.excluded_registrations.note}</p>
    </div>}
    {result.analysis ? <EvaluationDashboard result={result} sections={["inputs"]} /> : <p className="rounded-xl bg-background p-3 text-sm text-muted">{NOT_AVAILABLE}: module difficulty and provenance come from the optional evaluation, which this result did not include.</p>}
  </div>;
}
