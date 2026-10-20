"use client";

import Link from "next/link";
import { useEffect, useState, useTransition } from "react";
import { Badge, Card, formatDate } from "@/components/console/ui";
import { calendarErrors, calendarFromPeriod, type ExamCalendarInput } from "@/lib/examination-calendar";
import { loadBothCycleReviews } from "@/lib/examination-both-actions";
import { generateBothFinalTimetables, saveBothFinalTimetableDrafts } from "@/lib/examination-generation-actions";
import type { BothGenerationState, BothSaveState } from "@/lib/examination-both-types";
import type { GenerationMode } from "@/lib/examination-generation-types";
import { academicYears, type ExamOption } from "@/lib/examination-selection";
import { effectiveForm, schedulingPreferences, settingsErrors, settingsForm, withRecommendedProfile, type SettingsForm } from "@/lib/examination-settings";
import type { CycleReview } from "@/lib/examination-workspace-types";
import { AcademicGroups } from "./academic-groups";
import { GenerationCalendar } from "./generation-calendar";
import { GenerationResults } from "./generation-results";
import { fieldClass, primaryButton, secondaryButton } from "./period-calendar";
import { SettingsReview } from "./settings-review";
import { ProtectionPreview } from "./protection-preview";

const STEPS = ["Select cycles", "Calendars & modules", "Shared preferences", "Generate both", "Separate results"];

export function BothSemestersForm({ cycles }: { cycles: ExamOption[] }) {
  const finals = cycles.filter((c) => c.exam_kind === "FINAL");
  const years = academicYears(finals);
  const codesFor = (year: string): [string, string] => [1, 2].map((semester) => {
    const matches = finals.filter((c) => c.academic_year_label === year && c.semester === semester);
    return matches.length === 1 ? matches[0].period_code : "";
  }) as [string, string];
  const [year, setYear] = useState(years[0] ?? "");
  const [codes, setCodes] = useState<[string, string]>(() => codesFor(years[0] ?? ""));
  const [reviews, setReviews] = useState<[CycleReview, CycleReview] | null>(null);
  const [calendars, setCalendars] = useState<ExamCalendarInput[]>([]);
  const [form, setForm] = useState<SettingsForm | null>(null);
  const [applied, setApplied] = useState<SettingsForm | null>(null);
  const [mode, setMode] = useState<GenerationMode>("optimized");
  const [limit, setLimit] = useState("");
  const [step, setStep] = useState(0);
  const [active, setActive] = useState(0);
  const [state, setState] = useState<BothGenerationState>({ status: "idle" });
  const [saved, setSaved] = useState<BothSaveState>({ status: "idle" });
  const [confirmed, setConfirmed] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [pending, startTransition] = useTransition();
  const [task, setTask] = useState("Loading both cycle reviews");
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    if (!pending) return;
    const started = Date.now();
    const timer = setInterval(() => setSeconds(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => clearInterval(timer);
  }, [pending]);
  const clear = () => { setState({ status: "idle" }); setSaved({ status: "idle" }); setConfirmed(false); };
  const defaults = reviews ? settingsForm(reviews[0].scheduling, reviews[0].seating) : null;
  const used = applied && defaults ? effectiveForm(applied, defaults, mode) : null;
  const dirty = JSON.stringify(form) !== JSON.stringify(applied);
  const bounds = reviews ? { min: reviews[0].seating.min_percentage, max: reviews[0].seating.max_percentage } : undefined;
  const errors = form ? settingsErrors(form, bounds) : {};
  const calendarsValid = reviews !== null && calendars.length === 2 && calendars.every((c, i) =>
    Object.keys(calendarErrors(c, reviews[i].period.academic_year_label)).length === 0);
  const maxLimit = Math.min(300, Number(used?.time_limit_seconds) || 300);
  const limitInvalid = limit.trim() !== "" && !(Number(limit) > 0 && Number(limit) <= maxLimit);
  const ready = reviews?.every((r) => r.mapping.length > 0 && r.scheduling.status === "valid") && calendarsValid
    && used !== null && !dirty && !limitInvalid && Object.keys(settingsErrors(used, bounds)).length === 0;
  const load = () => {
    setTask("Loading both cycle reviews"); setSeconds(0); setLoadError("");
    startTransition(async () => {
      try {
        const response = await loadBothCycleReviews(codes);
        if (!response.ok) { setLoadError(response.error.message); return; }
        setReviews(response.data); setCalendars(response.data.map((r) => calendarFromPeriod(r.period)));
        const initial = withRecommendedProfile(settingsForm(response.data[0].scheduling, response.data[0].seating));
        setForm(initial); setApplied(initial); setActive(0); clear(); setStep(1);
      } catch { setLoadError("Both cycles could not be loaded. Retry after checking the connection."); }
    });
  };
  const payload = () => {
    const data = new FormData();
    data.set("cycles", JSON.stringify(reviews?.map((r, i) => ({ period_code: r.period.period_code,
      calendar: calendars[i], expected_period_fingerprint: r.period.fingerprint }))));
    data.set("mode", mode);
    data.set("scheduling_preferences", JSON.stringify(used ? schedulingPreferences(used) : {}));
    data.set("expected_settings_fingerprint", reviews?.[0].scheduling.fingerprint ?? "");
    data.set("expected_seating_version", String(reviews?.[0].seating.ruleset_version ?? ""));
    if (limit.trim()) data.set("time_limit_seconds", limit.trim());
    return data;
  };
  const generate = () => {
    if (!ready || pending) return;
    const data = payload(); clear(); setTask("Generating Semester 1, then Semester 2"); setSeconds(0);
    startTransition(async () => {
      try {
        const response = await generateBothFinalTimetables({ status: "idle" }, data);
        setState(response);
        if (response.status === "complete") { setActive(0); setStep(4); }
      } catch { setState({ status: "error", title: "Generation connection interrupted", message: "The solver may still be running. Wait before retrying. Nothing was requested to be saved." }); }
    });
  };
  const save = () => {
    if (!ready || pending || !confirmed || state.status !== "complete" || !state.result.generated) return;
    const data = payload(); data.set("confirm_save", "yes"); setTask("Generating and saving both drafts atomically"); setSeconds(0);
    startTransition(async () => {
      try { setSaved(await saveBothFinalTimetableDrafts({ status: "idle" }, data)); setConfirmed(false); }
      catch { setSaved({ status: "error", title: "Save connection interrupted", message: "Both drafts may have been saved. Check Saved timetables before retrying." }); }
    });
  };
  const result = state.status === "complete" ? state.result : null;
  return <div className="space-y-5">
    <div className="rounded-xl bg-primary-soft p-4 text-sm leading-6 text-primary-strong">Generate two full FINAL cycles together. Semester 1 is scheduled first; Semester 2 respects its temporary room and student assignments. Each cycle keeps its own calendar. This is sequential scheduling, not a guarantee of joint optimality.</div>
    <ol aria-label="Both semesters workflow steps" className="grid gap-2 rounded-xl border border-line bg-surface p-3 sm:grid-cols-5">{STEPS.map((name, i) =>
      <li key={name} aria-current={step === i ? "step" : undefined} className={`rounded-lg p-2 text-xs font-semibold ${step === i ? "bg-primary text-white" : "text-muted"}`}>{i + 1}. {name}</li>)}</ol>
    {step === 0 && <Card title="Select both FINAL cycles"><fieldset disabled={pending} className="grid gap-4 p-5 sm:grid-cols-3">
      <label className="text-sm font-medium">Academic year<select className={fieldClass} value={year} onChange={(e) => { setYear(e.target.value); setCodes(codesFor(e.target.value)); setReviews(null); clear(); }}>
        {years.map((y) => <option key={y}>{y}</option>)}</select></label>
      {[1, 2].map((semester, i) => <label key={semester} className="text-sm font-medium">Semester {semester} FINAL<select className={fieldClass} value={codes[i]}
        onChange={(e) => { const next: [string, string] = [...codes]; next[i] = e.target.value; setCodes(next); setReviews(null); clear(); }}>
        <option value="">Select a configured cycle</option>{finals.filter((c) => c.academic_year_label === year && c.semester === semester).map((c) => <option key={c.period_code} value={c.period_code}>{c.period_name}</option>)}
      </select>{!finals.some((c) => c.academic_year_label === year && c.semester === semester) && <span className="block text-xs text-danger">No FINAL cycle configured for this semester.</span>}</label>)}
    </fieldset></Card>}
    {loadError && <p role="alert" className="text-sm text-danger">{loadError}</p>}
    {reviews?.flatMap((r) => r.warnings.map((warning, i) => <p key={`${r.period.period_code}-${i}`} className="rounded-xl bg-secondary-soft p-3 text-sm text-secondary-ink">{r.period.period_name}: {warning}</p>))}
    {step === 1 && reviews && <>
      <div className="flex gap-2" aria-label="Calendar to review">{reviews.map((r, i) => <button key={r.period.period_code} type="button" className={active === i ? primaryButton : secondaryButton} disabled={pending} aria-pressed={active === i} onClick={() => setActive(i)}>Semester {r.period.semester}</button>)}</div>
      <p className="font-semibold text-ink">{reviews[active].period.period_name}</p>
      <GenerationCalendar key={reviews[active].period.period_code} period={reviews[active].period} calendar={calendars[active]} disabled={pending}
        permittedStarts={applied?.permitted_start_times} onChange={(calendar) => { setCalendars((previous) => previous.map((c, i) => i === active ? calendar : c)); clear(); }} />
      <AcademicGroups modules={reviews[active].modules} mapping={reviews[active].mapping} />
      <p className="text-sm text-muted">Review both calendars. No period dates or institution settings are saved by this workflow.</p>
    </>}
    {step === 2 && reviews && form && <>
      <SettingsReview review={reviews[0]} form={form} disabled={pending} onChange={setForm} />
      <div className="sticky bottom-2 z-10 space-y-2 rounded-xl border border-line bg-surface p-3 shadow-sm">
        <p role="status" className="text-sm text-muted">{Object.keys(errors).length ? `Correct ${Object.keys(errors).length} preference field(s).` : dirty ? "Unapplied changes. Apply before generating." : "Shared preferences applied to both timetables."}</p>
        <div className="flex flex-wrap gap-2"><button type="button" className={secondaryButton} disabled={pending} onClick={() => { const f = withRecommendedProfile(settingsForm(reviews[0].scheduling, reviews[0].seating)); setForm(f); setApplied(f); setLimit(""); clear(); }}>Reset to Defaults</button>
          <button type="button" className={primaryButton} disabled={pending || !dirty || Object.keys(errors).length > 0} onClick={() => { setApplied(form); setLimit(""); clear(); }}>Apply Preferences</button></div>
      </div>
    </>}
    {step === 3 && reviews && used && <Card title="Generate both timetables"><div className="space-y-4 p-5">
      <dl className="grid gap-3 sm:grid-cols-2">{reviews.map((r, i) => <div key={r.period.period_code} className="rounded-xl bg-background p-3 text-sm"><dt className="font-semibold">Semester {r.period.semester} · {r.period.period_name}</dt>
        <dd className="mt-1 text-muted">{formatDate(calendars[i].start_date)} – {formatDate(calendars[i].end_date)} · {r.mapping.length} mapped modules · {r.seating.venue_count} venues</dd></div>)}</dl>
      <label className="block text-sm font-medium">Generation mode<select className={fieldClass} value={mode} disabled={pending} onChange={(e) => { setMode(e.target.value as GenerationMode); clear(); }}>
        <option value="optimized">Optimized (recommended)</option><option value="feasibility">Basic / Feasibility</option></select></label>
      <label className="block text-sm font-medium">Time limit for each semester (seconds, optional)<input type="number" className={fieldClass} min="0.001" max={maxLimit} step="any" value={limit} disabled={pending}
        aria-invalid={limitInvalid} onChange={(e) => { setLimit(e.target.value); clear(); }} placeholder={`Use ${used.time_limit_seconds} seconds per semester`} /></label>
      <p className="text-xs text-muted">Leave blank to use {used.time_limit_seconds} seconds per cycle. Two solves run sequentially, plus input processing. Shared start times: {used.permitted_start_times.join(", ")} · minimum break: {used.minimum_break_minutes} minutes · default seating: {used.seating_percentage}%.</p>
      {limitInvalid && <p role="alert" className="text-sm text-danger">Enter a time above 0 and no more than {maxLimit} seconds.</p>}
      {reviews.map((r, i) => <details key={r.period.period_code}><summary className="cursor-pointer text-sm font-semibold text-primary-strong">Protected timetables around Semester {r.period.semester}</summary>
        <ProtectionPreview key={`${r.period.period_code}:${calendars[i].start_date}:${calendars[i].end_date}:${used.minimum_break_minutes}`} periodCode={r.period.period_code} start={calendars[i].start_date} end={calendars[i].end_date} minimumBreak={Number(used.minimum_break_minutes)} disabled={pending} /></details>)}
      <button type="button" className={primaryButton} disabled={pending || !ready} onClick={generate}>Generate Both Timetables</button>
      <p className="text-xs text-muted">Previews are UNSAVED. Existing approved and published timetables remain protected.</p>
    </div></Card>}
    {state.status === "error" && <div role="alert" className="rounded-xl bg-danger-soft p-4 text-sm text-danger"><p className="font-semibold">{state.title}</p><p>{state.message}</p></div>}
    {step === 4 && result && <>
      <Card title="Both-semester results" actions={<Badge tone={result.generated ? "accent" : "neutral"}>{result.generated ? "Pair validated" : "Pair incomplete"}</Badge>}><div className="space-y-3 p-5 text-sm">
        <p>{result.pair_validation.passed === true ? "No shared-room or shared-student clashes were found between the two generated results." : "A validated pair was not generated. No drafts were saved. Review each available result before retrying."}</p>
        {result.shared_candidates !== null && <p>{result.shared_candidates} registered candidates take exams in both cycles, including repeat registrations.</p>}
        {result.notices.map((notice, i) => <p key={i} className="text-muted">{notice}</p>)}
        {result.skipped_period_codes.length > 0 && <p role="status">Not attempted because Semester 1 did not produce a timetable: {result.skipped_period_codes.join(", ")}</p>}
        {result.pair_validation.violations.map((p, i) => <p key={i} role="alert" className="text-danger">{p.rule}: {p.message}</p>)}
        <div className="flex flex-wrap gap-2">{result.results.map((r, i) => <button key={r.inputs.period_code} type="button" aria-pressed={active === i} className={active === i ? primaryButton : secondaryButton} onClick={() => setActive(i)}>Semester {r.inputs.semester} results & PDFs</button>)}</div>
      </div></Card>
      {result.results[active] && <GenerationResults key={result.results[active].inputs.period_code} result={result.results[active]} />}
      {result.generated && saved.status !== "saved" && <Card title="Save both as separate drafts"><div className="space-y-3 p-5 text-sm">
        <p>Saving runs a fresh coordinated generation and saves both drafts in one transaction. They may differ from these UNSAVED previews. Neither is approved or published.</p>
        <label className="flex items-start gap-2"><input type="checkbox" className="mt-1 accent-primary" disabled={pending} checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />I understand that both timetables will be generated again before saving.</label>
        <button type="button" className={primaryButton} disabled={pending || !confirmed || !ready} onClick={save}>Generate again and Save Both Drafts</button>
      </div></Card>}
      {saved.status === "saved" && <Card title="Both drafts saved"><div className="space-y-3 p-5 text-sm"><p>Approve and publish each timetable separately from its saved detail page. PDFs above still describe the UNSAVED previews; open a saved draft for its actual DRAFT PDF.</p>
        {saved.timetables.map((t) => <Link key={t.timetableId} className="block font-semibold text-primary-strong underline" href={`/exams/timetables/${t.timetableId}`}>{t.periodCode} · Draft #{t.draftNumber}</Link>)}
      </div></Card>}
      {saved.status === "error" && <div role="alert" className="rounded-xl bg-danger-soft p-4 text-sm text-danger"><strong>{saved.title}</strong><p>{saved.message}</p></div>}
    </>}
    {pending && <div role="status" aria-busy="true" className="rounded-xl bg-primary-soft p-4 text-sm text-primary-strong"><p className="font-semibold">{task}</p><p>Keep this page open. Elapsed: {seconds} seconds.</p></div>}
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-line bg-surface p-3">
      <button type="button" className={secondaryButton} disabled={pending || step === 0} onClick={() => setStep(step - 1)}>Back</button>
      {step < 3 && <button type="button" className={primaryButton} disabled={pending || (step === 0 ? !codes.every(Boolean) : step === 1 ? !calendarsValid : dirty || Object.keys(errors).length > 0)}
        onClick={() => step === 0 ? load() : setStep(step + 1)}>{step === 0 ? "Load Both Cycles" : "Next"}</button>}
      {step === 4 && <button type="button" className={secondaryButton} disabled={pending} onClick={() => setStep(3)}>Review inputs / generate again</button>}
    </div>
  </div>;
}
