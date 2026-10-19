import { Badge, Card, EmptyState, formatDate, Kpi } from "@/components/console/ui";
import type { GeneratedTimetable, GenerationCalendar, GenerationFairness, GenerationProblem, WorkloadScore } from "@/lib/examination-generation-types";
import { HARD_RULES } from "@/lib/examination-presentation";
import { HardRuleDescriptions } from "./settings-review";
import { TimetableBrowser } from "./timetable-browser";

const count = new Intl.NumberFormat("en-GB");
const score = new Intl.NumberFormat("en-GB", { maximumFractionDigits: 3 });
const penaltyLabels = [
  ["S1", "Preparation for difficult exams"],
  ["S2", "General preparation gaps"],
  ["S3", "Same-day and short-gap exams"],
  ["S4", "Demanding-exam concentration"],
  ["S5", "Exam-count concentration"],
] as const;
const cell = "px-5 py-3 text-sm text-muted";
const heading = "px-5 py-3 text-xs font-semibold text-muted";
const value = (number: number | undefined | null) => number == null ? "—" : score.format(number);

export function GenerationResults({ result }: { result: GeneratedTimetable }) {
  const valid = result.generated && result.validation.passed === true;
  const optimization = result.optimization;
  const sessions = [...result.sessions].sort((a, b) =>
    a.date.localeCompare(b.date) || a.start_time.localeCompare(b.start_time) || a.module_code.localeCompare(b.module_code) || a.session_number - b.session_number,
  );
  return (
    <section className="space-y-6" aria-label="Timetable generation result">
      <div role="status" className="flex flex-wrap items-start justify-between gap-3 rounded-2xl border border-line bg-surface p-5">
        <div>
          <h2 className="text-lg font-semibold text-ink">{valid ? "Timetable generated" : "No validated timetable generated"}</h2>
          <p className="mt-1 text-sm text-muted">{result.inputs.period_name} · {result.inputs.period_code}</p>
          <p className="mt-2 text-xs text-muted">{result.solver.mode === "optimized" ? "Optimized" : "Feasibility"} mode · {count.format(result.inputs.modules)} modules · {count.format(result.inputs.students)} candidates</p>
          {result.inputs.calendar && <CalendarUsed calendar={result.inputs.calendar} />}
        </div>
        <Badge tone="neutral">Unsaved result</Badge>
      </div>

      <TimetableBrowser sessions={sessions} valid={valid} statusMessage={solverHint(result.status)} />
      <details className="rounded-2xl border border-line bg-surface p-4" open>
        <summary className="cursor-pointer text-base font-semibold text-ink">Validation and optimization summary</summary>
        <div className="mt-5 space-y-6">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi icon="fact_check" tone={valid ? "accent" : "neutral"} label="Solver status" value={result.status} hint={solverHint(result.status)} />
        <Kpi icon="history" tone="primary" label="Runtime" value={`${score.format(result.solver.wall_time_seconds)} s`} hint={`Solver limit: ${score.format(result.solver.time_limit_seconds)} seconds`} />
        <Kpi icon="menu_book" tone="primary" label="Sessions" value={count.format(result.summary.sessions)} hint={`${count.format(result.summary.exam_days_used)} exam days used`} />
        <Kpi icon="verified_user" tone={result.validation.passed === true ? "accent" : result.validation.passed === false ? "danger" : "neutral"}
          label="Validation" value={result.validation.passed === true ? "Passed" : result.validation.passed === false ? "Failed" : "Not evaluated"}
          hint={result.validation.passed === null ? "No timetable was available to validate" : `${result.validation.rules_checked.length} rules checked`} />
      </div>

      <Card title="Validation and diagnostics" description="Independent checks returned by the Examination Service.">
        <div className="space-y-4 p-5 text-sm text-muted">
          <p><span className="font-semibold text-ink">Overall independent hard validation:</span> {result.validation.passed === true ? "Passed" : result.validation.passed === false ? "Failed" : "Not evaluated"}.</p>
          <p>The API reports an overall result and violations, not individual success flags. A listed rule is not presented as individually passed.</p>
          <ul className="grid gap-2 sm:grid-cols-2">{HARD_RULES.map(([code, title]) => <li key={code} className="rounded-lg border border-line p-3 text-xs">
            <a href={`#rule-${code}`} className="font-semibold text-primary-strong underline underline-offset-2">{code} · {title}</a>
            <span className="mt-1 block">{result.validation.violations.some((problem) => problem.rule === code) ? "Failed — violation reported" : result.validation.rules_checked.includes(code) ? "Listed in overall validation; no individual status" : "No evidence returned for this rule"}</span>
          </li>)}</ul>
          <HardRuleDescriptions />
          <Problems title="Validation violations" items={result.validation.violations} />
          <Problems title="Generation violations" items={result.violations} />
          {result.diagnostics.length > 0 && <Messages title="Diagnostics" items={result.diagnostics} />}
          {result.notices.length > 0 && <Messages title="Service notices" items={result.notices} />}
          {result.excluded_registrations.total > 0 && (
            <div className="rounded-xl bg-secondary-soft p-4 text-secondary-ink">
              <p className="font-semibold">{count.format(result.excluded_registrations.total)} registrations excluded from this cycle</p>
              <p className="mt-1">{count.format(result.excluded_registrations.normal)} normal · {count.format(result.excluded_registrations.repeat)} repeat</p>
              <p className="mt-2 leading-6">{result.excluded_registrations.note}</p>
            </div>
          )}
        </div>
      </Card>

      <Card title="S1–S5 workload scores" description="Prototype workload penalties. Lower values are better; these are not validated stress measurements.">
        {result.solver.mode === "optimized" && optimization ? (
          <div className="space-y-1 border-b border-line px-5 py-4 text-sm text-muted">
            <p className="font-semibold text-ink">{optimization.improved ? "An improved timetable was selected" : "No improvement reported; the selected result may retain the baseline"}</p>
            <p>Optimization status: {optimization.status ?? "Not returned"}</p>
            <p>Objective bound: {value(optimization.objective_bound_units)} · Relative gap: {optimization.relative_gap === null ? "Not returned" : `${score.format(optimization.relative_gap * 100)}%`}</p>
          </div>
        ) : null}
        <WorkloadTable workload={result.workload} baseline={optimization?.baseline ?? null} selected={optimization?.selected ?? null} compare={result.solver.mode === "optimized"} />
      </Card>

      {result.solver.mode === "optimized" && (
        <Card title="Fairness comparison" description="Aggregate comparison across candidates; individual student records are excluded.">
          {optimization?.fairness ? <Fairness fairness={optimization.fairness} /> : <EmptyState title="No fairness details returned">The service did not return a fairness comparison for this run.</EmptyState>}
        </Card>
      )}
        </div>
      </details>
    </section>
  );
}

/** The exam calendar the service reports it used: the selected dates, hours, weekends and excluded dates. */
export function CalendarUsed({ calendar }: { calendar: GenerationCalendar }) {
  const weekends = [calendar.include_saturdays && "Saturdays", calendar.include_sundays && "Sundays"].filter(Boolean).join(" and ");
  return (
    <div className="mt-3 rounded-xl bg-primary-soft px-3 py-2 text-sm text-primary-strong">
      <p><span className="font-semibold">Exam calendar:</span> {formatDate(calendar.start_date)} – {formatDate(calendar.end_date)} · {count.format(calendar.exam_days)} exam days
        {calendar.day_start_time && calendar.day_end_time ? ` · ${calendar.day_start_time.slice(0, 5)}–${calendar.day_end_time.slice(0, 5)}` : ""}
        {calendar.include_saturdays === null ? "" : weekends ? ` · Weekends: ${weekends}` : " · No weekends"}
        {calendar.source === "selected" ? " · Selected for this generation" : " · Configured period"}</p>
      {calendar.exclusions.length > 0 && <p className="mt-1 text-xs">Excluded: {calendar.exclusions.map((item) => `${formatDate(item.excluded_date)} (${item.reason})`).join(", ")}</p>}
    </div>
  );
}

function solverHint(status: GeneratedTimetable["status"]): string {
  const messages = {
    OPTIMAL: "The solver reported an optimal solution for the requested mode.",
    FEASIBLE: "A feasible timetable was found; optimality is not proven.",
    INFEASIBLE: "The solver proved no timetable satisfies the current constraints.",
    UNKNOWN: "The solver did not establish a solution within this run.",
    MODEL_INVALID: "The model or returned timetable failed validation.",
  };
  return messages[status];
}

function Messages({ title, items }: { title: string; items: string[] }) {
  return <div><h3 className="font-semibold text-ink">{title}</h3><ul className="mt-2 list-disc space-y-1 pl-5 leading-6">{items.map((message, index) => <li key={index}>{message}</li>)}</ul></div>;
}

function Problems({ title, items }: { title: string; items: GenerationProblem[] }) {
  if (!items.length) return null;
  return <Messages title={title} items={items.map((problem) => `${problem.rule}: ${problem.message}`)} />;
}

function WorkloadTable({ workload, baseline, selected, compare }: { workload: WorkloadScore | null; baseline: WorkloadScore | null; selected: WorkloadScore | null; compare: boolean }) {
  const current = selected ?? workload;
  if (!current && !baseline) return <EmptyState title="No workload scores returned">The service could not score a timetable for this run.</EmptyState>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[38rem] text-left">
        <caption className="sr-only">S1 to S5 workload penalty scores and weighted contributions</caption>
        <thead className="border-b border-line bg-background"><tr>
          <th scope="col" className={heading}>Workload penalty</th>
          {compare && <th scope="col" className={heading}>Baseline penalty</th>}
          <th scope="col" className={heading}>{compare ? "Selected penalty" : "Penalty"}</th>
          <th scope="col" className={heading}>Weight</th>
          <th scope="col" className={heading}>Weighted contribution</th>
        </tr></thead>
        <tbody className="divide-y divide-line">
          {penaltyLabels.map(([constraint, label]) => {
            const before = baseline?.penalties.find((penalty) => penalty.constraint === constraint);
            const after = current?.penalties.find((penalty) => penalty.constraint === constraint);
            return (
              <tr key={constraint}>
                <th scope="row" className="px-5 py-3 text-sm font-medium text-ink"><span className="font-mono text-primary-strong">{constraint}</span> · {label}</th>
                {compare && <td className={cell}>{value(before?.penalty_points)}</td>}
                <td className={cell}>{value(after?.penalty_points)}</td>
                <td className={cell}>{value(after?.weight)}</td>
                <td className={cell}>{value(after?.weighted_points)}</td>
              </tr>
            );
          })}
          <tr className="bg-background">
            <th scope="row" className="px-5 py-4 text-sm font-semibold text-ink">Total weighted penalty</th>
            {compare && <td className={cell}>{value(baseline?.total_weighted_penalty)}</td>}
            <td className={cell} colSpan={3}>{value(current?.total_weighted_penalty)}</td>
          </tr>
        </tbody>
      </table>
      <p className="border-t border-line px-5 py-3 text-xs text-muted">A dash means the service did not return that score. Penalties and weighted contributions use different scales.</p>
    </div>
  );
}

function Fairness({ fairness }: { fairness: GenerationFairness }) {
  const facts = [
    ["Fairness protection", fairness.protection_applied ? "Applied" : "Not applied"],
    ["Fairness limits", fairness.limits_satisfied === null ? "Not evaluated" : fairness.limits_satisfied ? "Satisfied" : "Not satisfied"],
    ["Candidates compared", count.format(fairness.students)],
    ["Improved / worsened / unchanged", `${count.format(fairness.students_improved)} / ${count.format(fairness.students_worsened)} / ${count.format(fairness.students_unchanged)}`],
    ["Worst normalized penalty: baseline → selected", `${value(fairness.baseline_worst_normalized_penalty)} → ${value(fairness.selected_worst_normalized_penalty)}`],
    ["Allowed individual increase (points)", value(fairness.maximum_individual_increase_points)],
    ["Worst individual increase (points)", value(fairness.worst_student_increase_points)],
  ];
  return <div>{!fairness.protection_applied && <p className="m-5 rounded-xl bg-secondary-soft p-3 text-sm leading-6 text-secondary-ink">Fairness protection was not applied. The comparison is descriptive; individual fairness limits were not enforced for the selected timetable.</p>}<dl className="grid gap-5 p-5 sm:grid-cols-2">{facts.map(([label, text]) => <div key={label}><dt className="text-xs font-medium text-muted">{label}</dt><dd className="mt-1 text-sm font-semibold text-ink">{text}</dd></div>)}</dl></div>;
}
