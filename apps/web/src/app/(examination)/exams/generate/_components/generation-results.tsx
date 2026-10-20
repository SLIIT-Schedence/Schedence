"use client";

import { type KeyboardEvent, type ReactNode, useState } from "react";
import { Badge, formatDate } from "@/components/console/ui";
import { ALL_COHORTS, type CohortFilter } from "@/lib/examination-evaluation";
import type { TimetableExportMetadata } from "@/lib/examination-export";
import type { GeneratedTimetable, GenerationCalendar } from "@/lib/examination-generation-types";
import { type AcademicFilters, EMPTY_FILTERS, filterSessions } from "@/lib/examination-presentation";
import { reviewCount, solverHint, summarize, type SummaryItem } from "@/lib/examination-results";
import { secondaryButton } from "./period-calendar";
import { DetailsPanel, HardPanel, IssuesPanel, LoadPanel } from "./result-panels";
import { TimetableBrowser } from "./timetable-browser";

const count = new Intl.NumberFormat("en-GB");
const seconds = new Intl.NumberFormat("en-GB", { maximumFractionDigits: 1 });

export const RESULT_TABS = [
  { id: "timetable", label: "Timetable" }, { id: "issues", label: "Issues to Review" }, { id: "hard", label: "Hard Constraints" },
  { id: "load", label: "Cognitive Load (S1–S5)" }, { id: "details", label: "Fairness & Details" },
] as const;
export type ResultTab = typeof RESULT_TABS[number]["id"];

const TILE_EDGE = { primary: "border-l-primary", accent: "border-l-accent-ink", danger: "border-l-danger", neutral: "border-l-secondary-ink" } as const;

/** One headline fact. The value and detail are text, so colour is never the only cue. */
function SummaryTile({ item }: { item: SummaryItem }) {
  return <div className={`min-w-0 rounded-xl border border-l-4 border-line bg-surface p-3 ${TILE_EDGE[item.tone]}`}>
    <dt className="text-xs font-semibold text-muted">{item.label}</dt>
    <dd className="mt-1 flex flex-wrap items-center gap-2 text-lg font-semibold leading-6 text-ink">{item.value}{item.badge && <Badge tone={item.badge.tone}>{item.badge.text}</Badge>}</dd>
    <dd className="mt-1 text-xs leading-5 text-muted">{item.detail}</dd>
  </div>;
}

export function DownloadButton({ label, busyLabel, errorText, disabled, title, run }: {
  label: string; busyLabel: string; errorText: string; disabled: boolean; title?: string; run: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ error: boolean; text: string } | null>(null);
  const click = async () => {
    if (busy || disabled) return;
    setBusy(true); setMessage(null);
    try { await run(); setMessage({ error: false, text: `${label}: download requested. Check your browser downloads.` }); }
    catch (error) { setMessage({ error: true, text: error instanceof Error && error.message.startsWith("This timetable contains characters") ? error.message : errorText }); }
    finally { setBusy(false); }
  };
  return <div className="min-w-0">
    <button type="button" className={secondaryButton} disabled={disabled || busy} aria-busy={busy} title={title} onClick={click}>{busy ? busyLabel : label}</button>
    {message && <p role={message.error ? "alert" : "status"} className={`mt-1 max-w-xs text-xs ${message.error ? "text-danger" : "text-accent-ink"}`}>{message.text}</p>}
  </div>;
}

/**
 * Results dashboard: the headline facts first, the actions within reach, then five accessible tabs.
 * `children` is the Save Draft card, which opens right under the actions instead of at the end of the page.
 */
export function GenerationResults({ result, children }: { result: GeneratedTimetable; children?: ReactNode }) {
  const valid = result.generated && result.validation.passed === true;
  const sessions = [...result.sessions].sort((a, b) =>
    a.date.localeCompare(b.date) || a.start_time.localeCompare(b.start_time) || a.module_code.localeCompare(b.module_code) || a.session_number - b.session_number,
  );
  const [tab, setTab] = useState<ResultTab>(valid ? "timetable" : "hard");
  // Shared with the action area, so a PDF always matches what the tabs show.
  const [filters, setFilters] = useState<AcademicFilters>(EMPTY_FILTERS);
  const [cohort, setCohort] = useState<CohortFilter>(ALL_COHORTS);
  const [saveOpen, setSaveOpen] = useState(false);
  const rows = filterSessions(sessions, filters);
  const review = reviewCount(result);
  const exportMetadata: TimetableExportMetadata = {
    periodCode: result.inputs.period_code, periodName: result.inputs.period_name,
    academicYearLabel: result.inputs.academic_year_label, semester: result.inputs.semester, examKind: result.inputs.exam_kind,
    status: "UNSAVED", recordedAt: result.generated_at, recordedAtLabel: "Generated",
    calendarStart: result.inputs.calendar?.start_date, calendarEnd: result.inputs.calendar?.end_date,
  };
  const openHard = () => {
    setTab("hard");
    if (typeof window !== "undefined") window.requestAnimationFrame(() => document.getElementById("result-tab-hard")?.focus());
  };
  const keyboardTab = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const next = event.key === "ArrowRight" ? (index + 1) % RESULT_TABS.length
      : event.key === "ArrowLeft" ? (index + RESULT_TABS.length - 1) % RESULT_TABS.length
        : event.key === "Home" ? 0 : event.key === "End" ? RESULT_TABS.length - 1 : null;
    if (next === null) return;
    event.preventDefault(); setTab(RESULT_TABS[next].id);
    event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next]?.focus();
  };
  const tabNote = (id: ResultTab) => id === "issues" && review > 0
    ? <span className="ml-1 rounded bg-secondary-soft px-1.5 py-0.5 text-xs text-secondary-ink">{review}<span className="sr-only"> items to review</span></span>
    : id === "hard" && result.validation.passed !== null
      ? <span className={`ml-1 rounded px-1.5 py-0.5 text-xs ${result.validation.passed ? "bg-accent text-accent-ink" : "bg-danger-soft text-danger"}`}>{result.validation.passed ? "Passed" : "Failed"}</span> : null;

  return (
    <section className="space-y-4" aria-label="Timetable generation result">
      <div role="status" className="flex flex-wrap items-start justify-between gap-3 rounded-2xl border border-line bg-surface p-4 sm:p-5">
        <div>
          <h2 className="text-lg font-semibold text-ink">{valid ? "Timetable generated" : "No validated timetable generated"}</h2>
          <p className="mt-1 text-sm text-muted">{result.inputs.period_name} · {result.inputs.period_code}</p>
          <p className="mt-2 text-xs text-muted">{result.solver.mode === "optimized" ? "Optimized" : "Feasibility"} mode · {count.format(result.inputs.modules)} modules · {count.format(result.inputs.students)} candidates · {count.format(result.summary.sessions)} sessions on {count.format(result.summary.exam_days_used)} exam days · solver ran {seconds.format(result.solver.wall_time_seconds)} s of a {seconds.format(result.solver.time_limit_seconds)} s limit</p>
          {result.inputs.calendar && <CalendarUsed calendar={result.inputs.calendar} />}
        </div>
        <Badge tone="neutral">UNSAVED</Badge>
      </div>

      <dl aria-label="Result summary" className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-6">{summarize(result).map((item) => <SummaryTile key={item.id} item={item} />)}</dl>

      <div role="region" aria-label="Result actions" className="space-y-2 rounded-2xl border border-line bg-surface p-3 sm:p-4">
        <div className="flex flex-wrap items-start gap-2">
          <DownloadButton label="Download Timetable PDF" busyLabel="Preparing PDF…" disabled={!valid || !rows.length}
            title={valid ? undefined : "No validated timetable to export"} errorText="The PDF could not be downloaded. Retry, or check whether your browser blocked the download."
            run={async () => { const { downloadTimetablePdf } = await import("@/lib/examination-pdf"); await downloadTimetablePdf(rows, exportMetadata, filters); }} />
          <DownloadButton label="Download Evaluation PDF" busyLabel="Preparing report…" disabled={!result.analysis}
            title={result.analysis ? undefined : "The optional evaluation was not included in this result"} errorText="The evaluation PDF could not be downloaded. Check that names use supported Latin characters and retry."
            run={async () => { const { downloadEvaluationPdf } = await import("@/lib/examination-evaluation-pdf"); await downloadEvaluationPdf(result, cohort); }} />
          {children && <button type="button" className={secondaryButton} aria-expanded={saveOpen} aria-controls="save-draft-panel" onClick={() => setSaveOpen(!saveOpen)}>{saveOpen ? "Hide Save Draft" : "Save Draft"}</button>}
        </div>
        <p className="text-xs leading-5 text-muted">Timetable PDF: {count.format(rows.length)} of {count.format(sessions.length)} sessions, the ones shown in the Timetable tab. {result.analysis ? "Evaluation PDF: uses the student group chosen under Cognitive Load." : "Evaluation PDF: not available for this result."}</p>
        <p className="text-xs leading-5 text-muted"><strong className="text-ink">Unsaved preview.</strong> Save Draft runs a fresh generation, so the saved draft can differ from what you see here. Nothing is approved or published.</p>
      </div>
      {children && <div id="save-draft-panel" hidden={!saveOpen}>{children}</div>}

      <div role="tablist" aria-label="Result sections" className="grid grid-cols-2 gap-1 rounded-xl border border-line bg-surface p-1 sm:grid-cols-3 lg:grid-cols-5">
        {RESULT_TABS.map((item, index) => <button key={item.id} id={`result-tab-${item.id}`} role="tab" type="button"
          aria-selected={tab === item.id} aria-controls={`result-panel-${item.id}`} tabIndex={tab === item.id ? 0 : -1}
          onClick={() => setTab(item.id)} onKeyDown={(event) => keyboardTab(event, index)}
          className={`min-h-11 rounded-lg px-2 py-2 text-sm font-semibold focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary ${tab === item.id ? "bg-primary text-white" : "text-muted hover:bg-background"}`}>
          {item.label}{tabNote(item.id)}
        </button>)}
      </div>
      {/* Every panel stays mounted, so filters and open sections survive switching tabs. */}
      {RESULT_TABS.map((item) => <section key={item.id} id={`result-panel-${item.id}`} role="tabpanel" aria-labelledby={`result-tab-${item.id}`} hidden={tab !== item.id} tabIndex={0}>
        {item.id === "timetable" ? <TimetableBrowser sessions={sessions} valid={valid} statusMessage={solverHint(result.status)} exportMetadata={exportMetadata} filters={filters} onFiltersChange={setFilters} />
          : <div className="rounded-2xl border border-line bg-surface p-4 sm:p-5">
            {item.id === "issues" && <IssuesPanel result={result} onOpenHard={openHard} />}
            {item.id === "hard" && <HardPanel result={result} />}
            {item.id === "load" && <LoadPanel result={result} cohort={cohort} onCohortChange={setCohort} onOpenHard={openHard} />}
            {item.id === "details" && <DetailsPanel result={result} />}
          </div>}
      </section>)}
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
