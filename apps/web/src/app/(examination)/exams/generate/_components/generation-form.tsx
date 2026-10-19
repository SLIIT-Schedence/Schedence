"use client";

import { useEffect, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { Badge, Card, formatDate } from "@/components/console/ui";
import { Icon } from "@/components/icon";
import { calendarErrors, calendarExamDays, calendarFromPeriod, type ExamCalendarInput } from "@/lib/examination-calendar";
import { generateFinalTimetable, saveFinalTimetableDraft } from "@/lib/examination-generation-actions";
import { loadCycleReview } from "@/lib/examination-workspace-actions";
import type { GenerationMode, GenerationState, SaveDraftState } from "@/lib/examination-generation-types";
import { type ExamChoice, type ExamOption, initialChoice, isGeneratable } from "@/lib/examination-selection";
import type { CycleReview } from "@/lib/examination-workspace-types";
import { GenerationResults } from "./generation-results";
import { fieldClass, primaryButton, secondaryButton } from "./period-calendar";
import { AcademicGroups } from "./academic-groups";
import { ExamSelection } from "./exam-selection";
import { GenerationCalendar } from "./generation-calendar";
import { SaveDraftCard } from "./save-draft";
import { SettingsReview } from "./settings-review";

const STEPS = ["Select Examination", "Exam calendar", "Academic groups", "Scheduling rules", "Generate", "Results"];

export function GenerationForm({ cycles }: { cycles: ExamOption[] }) {
  const [step, setStep] = useState(0);
  const [furthest, setFurthest] = useState(0);
  const [choice, setChoice] = useState<ExamChoice>(() => initialChoice(cycles));
  const periodCode = choice.periodCode;
  const [review, setReview] = useState<CycleReview | null>(null);
  // The exam calendar of THIS generation. It starts as the configured period's calendar and is never saved to it.
  const [calendar, setCalendar] = useState<ExamCalendarInput | null>(null);
  const [loadError, setLoadError] = useState("");
  const [state, setState] = useState<GenerationState>({ status: "idle" });
  const [mode, setMode] = useState<GenerationMode>("feasibility");
  const [timeLimit, setTimeLimit] = useState("");
  const [seconds, setSeconds] = useState(0);
  const [task, setTask] = useState<"review" | "generation" | "save">("review");
  const [saveState, setSaveState] = useState<SaveDraftState>({ status: "idle" });
  const [confirmSave, setConfirmSave] = useState(false);
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const fields = state.status === "error" ? state.fieldErrors : undefined;
  const selected = cycles.find((cycle) => cycle.period_code === periodCode && isGeneratable(cycle));
  const calendarProblems = calendar && review ? calendarErrors(calendar, review.period.academic_year_label) : {};
  const calendarValid = Boolean(calendar) && Object.keys(calendarProblems).length === 0;
  const calendarJson = calendar ? JSON.stringify(calendar) : "";

  useEffect(() => {
    if (!pending) return;
    const started = Date.now();
    const timer = setInterval(() => setSeconds(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => clearInterval(timer);
  }, [pending]);

  const advance = (next: number) => { setStep(next); setFurthest((previous) => Math.max(previous, next)); };
  const clearResults = () => { setState({ status: "idle" }); setSaveState({ status: "idle" }); setConfirmSave(false); };
  const load = () => { setTask("review"); setSeconds(0); startTransition(async () => {
    setLoadError("");
    try {
      const response = await loadCycleReview(periodCode);
      if (!response.ok) { setLoadError(`HTTP ${response.error.status}: ${response.error.message}`); return; }
      setReview(response.data); setCalendar(calendarFromPeriod(response.data.period)); clearResults(); setFurthest(1); advance(1);
    } catch { setLoadError("The cycle could not be loaded. Retry after checking the connection."); }
  }); };
  const changeCalendar = (next: ExamCalendarInput) => {
    // Results and save confirmations belong to the previous calendar.
    setCalendar(next); clearResults(); setFurthest((previous) => Math.min(previous, 4));
  };
  const generate = (formData: FormData) => { setTask("generation"); startTransition(async () => {
    setSeconds(0);
    try {
      const response = await generateFinalTimetable({ status: "idle" }, formData);
      setState(response);
      if (response.status === "complete") advance(5);
    } catch { setState({ status: "error", title: "The generation connection was interrupted", message: "The solver may still be running. Wait before trying another generation request." }); }
  }); };
  const saveDraft = () => {
    if (!review || !calendarValid || pending) return;
    const data = new FormData();
    data.set("period_code", review.period.period_code);
    data.set("expected_period_fingerprint", review.period.fingerprint ?? "");
    data.set("expected_settings_fingerprint", review.scheduling.fingerprint ?? "");
    data.set("expected_seating_version", String(review.seating.ruleset_version));
    data.set("calendar", calendarJson);
    data.set("mode", mode);
    if (timeLimit.trim()) data.set("time_limit_seconds", timeLimit.trim());
    data.set("confirm_save", confirmSave ? "yes" : "");
    setTask("save"); setSeconds(0); setSaveState({ status: "idle" });
    startTransition(async () => {
      try {
        const response = await saveFinalTimetableDraft({ status: "idle" }, data);
        setSaveState(response);
        if (response.status === "saved") { setConfirmSave(false); router.push(`/exams/timetables/${response.timetableId}`); }
      } catch {
        setSaveState({ status: "error", title: "The save connection was interrupted", message: "A draft may still have been saved. Check Saved timetables before trying again." });
      }
    });
  };
  const disabled = pending;
  const ready = Boolean(review?.mapping.length) && review?.scheduling.status === "valid" && calendarValid;
  const saveCard = review && <SaveDraftCard periodName={review.period.period_name} mode={mode} timeLimit={timeLimit.trim()} confirmed={confirmSave}
    onConfirm={setConfirmSave} onSave={saveDraft} saving={pending && task === "save"} state={saveState}
    disabled={disabled || !ready || saveState.status === "saved"} />;
  const canNext = step === 0 ? Boolean(selected)
    : Boolean(review) && (step !== 1 || calendarValid) && (step !== 2 || Boolean(review?.mapping.length)) && (step !== 3 || review?.scheduling.status === "valid");

  return <div className="space-y-6">
    <nav aria-label="Timetable generation steps" className="rounded-2xl border border-line bg-surface p-4">
      <ol className="grid gap-2 sm:grid-cols-3 xl:grid-cols-6">{STEPS.map((label, index) => <li key={label}>
        <button type="button" disabled={disabled || index > furthest || (index === 5 && state.status !== "complete") || (index > 1 && !calendarValid)} onClick={() => setStep(index)} aria-current={step === index ? "step" : undefined}
          className={`flex min-h-12 w-full items-center gap-2 rounded-xl px-3 text-left text-xs font-semibold disabled:cursor-not-allowed ${step === index ? "bg-primary text-white" : index < step ? "bg-accent text-accent-ink" : "bg-background text-muted"}`}>
          <span className="grid size-6 shrink-0 place-items-center rounded-full border border-current/30">{index + 1}</span>{label}
        </button>
      </li>)}</ol>
      <p className="mt-3 text-xs text-muted">Step {step + 1} of 6 · {STEPS[step]}</p>
    </nav>
    {review?.warnings.map((warning) => <p key={warning} className="rounded-xl bg-secondary-soft p-4 text-sm text-secondary-ink">{warning}</p>)}

    {step === 0 && <>
      <ExamSelection options={cycles} choice={choice} disabled={pending} onChange={(next) => {
        setChoice(next); setReview(null); setCalendar(null); clearResults(); setFurthest(0); setLoadError("");
      }} />
      {selected && <p className="text-sm text-muted">Continue loads the current period, module mapping, settings and seating rules.</p>}
      {loadError && <p role="alert" className="text-sm text-danger">{loadError}</p>}
    </>}
    {step === 1 && review && calendar && <GenerationCalendar period={review.period} calendar={calendar} onChange={changeCalendar}
      permittedStarts={review.scheduling.settings.scheduling?.permitted_start_times} disabled={pending} />}
    {step === 2 && review && <AcademicGroups modules={review.modules} mapping={review.mapping} />}
    {step === 3 && review && <SettingsReview review={review} />}
    {step === 4 && review && calendar && <Card title="5. Review inputs and generate" description="Only a temporary, unsaved full-cycle timetable is generated, on exactly the selected exam calendar." actions={<Badge tone="primary">FINAL · Full cycle</Badge>}>
      <form action={generate} className="space-y-5 p-5" aria-busy={pending}>
        <input type="hidden" name="period_code" value={review.period.period_code} />
        <input type="hidden" name="expected_period_fingerprint" value={review.period.fingerprint ?? ""} />
        <input type="hidden" name="expected_settings_fingerprint" value={review.scheduling.fingerprint ?? ""} />
        <input type="hidden" name="expected_seating_version" value={review.seating.ruleset_version} />
        <input type="hidden" name="calendar" value={calendarJson} />
        <div className="rounded-xl bg-background p-4 text-sm leading-6 text-muted">
          <p className="font-semibold text-ink">{review.period.period_name} · <span className="font-mono text-xs font-normal">{review.period.period_code}</span></p>
          <p><span className="font-semibold text-ink">Exam calendar:</span> {formatDate(calendar.start_date)} – {formatDate(calendar.end_date)} · {calendarExamDays(calendar).length} exam days · {calendar.day_start_time}–{calendar.day_end_time}
            {calendar.include_saturdays || calendar.include_sundays ? ` · Weekends: ${[calendar.include_saturdays && "Saturdays", calendar.include_sundays && "Sundays"].filter(Boolean).join(", ")}` : " · No weekends"}
            {calendar.exclusions.length ? ` · ${calendar.exclusions.length} excluded date(s)` : ""}</p>
          <p>{review.mapping.length} mapped modules · {review.seating.venue_count} venues · {review.seating.total_exam_capacity} configured exam seats</p>
          <p>Scheduling version {review.scheduling.version} · Seating ruleset {review.seating.ruleset_version}</p>
        </div>
        <fieldset disabled={pending} className="space-y-5">
          <legend className="sr-only">Generation mode and solver limit</legend>
          <div className="grid gap-3 sm:grid-cols-2">{([
            ["feasibility", "Feasibility", "Find a timetable satisfying hard rules H1–H11."],
            ["optimized", "Optimized", "Keep hard rules and improve the S1–S5 workload objective."],
          ] as const).map(([value, label, description]) => <label key={value} className={`flex cursor-pointer items-start gap-3 rounded-xl border p-4 ${mode === value ? "border-primary bg-primary-soft" : "border-line"}`}>
            <input type="radio" name="mode" value={value} checked={mode === value} onChange={() => setMode(value)} className="mt-1 accent-primary" />
            <span><strong className="block text-sm">{label}</strong><span className="mt-1 block text-sm text-muted">{description}</span></span>
          </label>)}</div>
          <label htmlFor="time-limit" className="block text-sm font-medium">Solver limit in seconds (optional)
            <input id="time-limit" name="time_limit_seconds" type="number" min="0.001" max="300" step="any" value={timeLimit} onChange={(event) => setTimeLimit(event.target.value)} className={`${fieldClass} max-w-xs`} placeholder="Use saved limit" aria-describedby="limit-help" />
          </label>
          <p id="limit-help" className="text-xs text-muted">Up to 300 seconds; an override can only lower the saved limit ({String(review.scheduling.settings.solver?.time_limit_seconds ?? "not returned")} seconds).</p>
          {fields && <ul className="text-sm text-danger">{Object.entries(fields).map(([key, message]) => <li key={key}>{message}</li>)}</ul>}
        </fieldset>
        <button type="submit" disabled={pending || !ready} className={primaryButton}><Icon name="edit_note" size={18} />{pending ? "Generating…" : "Generate full-cycle timetable"}</button>
        {state.status === "error" && <div role="alert" className="rounded-xl bg-danger-soft p-4 text-sm leading-6 text-danger"><h3 className="font-semibold">{state.title}</h3><p>{state.httpStatus ? `HTTP ${state.httpStatus}: ` : ""}{state.message}</p></div>}
      </form>
    </Card>}
    {step === 4 && saveCard}
    {step === 5 && state.status === "complete" && <GenerationResults result={state.result} />}
    {step === 5 && state.status === "complete" && saveCard}

    {pending && <Card><div role="status" aria-busy="true" className="flex flex-col items-center p-6 text-center">
      <span aria-hidden="true" className="size-7 animate-spin rounded-full border-4 border-primary-soft border-t-primary" />
      <p className="mt-3 font-semibold text-ink">{task === "review" ? "Loading cycle review" : task === "save" ? "Generating and saving a new draft" : "Generating your FINAL timetable"}</p>
      <p className="mt-2 text-sm text-muted">{task === "review" ? "Reading the configured period, module mapping and settings."
        : task === "save" ? "A fresh generation runs on the selected exam calendar, is validated again and is saved as a DRAFT. This can take up to five minutes. Keep this page open."
          : "The solver can take up to five minutes plus input processing. Keep this page open."}</p>
      <p aria-live="off" className="mt-2 font-mono text-xs text-secondary-ink">Elapsed: {Math.floor(seconds / 60)}m {seconds % 60}s</p>
    </div></Card>}
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-line bg-surface p-4">
      <button type="button" disabled={disabled || step === 0} className={secondaryButton} onClick={() => setStep((previous) => previous - 1)}>Back</button>
      {review && step > 0 && <button type="button" disabled={disabled} className={secondaryButton} onClick={load}>Reload examination (resets the calendar and clears results)</button>}
      {step < 4 && <button type="button" disabled={disabled || !canNext} className={primaryButton} onClick={() => step === 0 ? load() : advance(step + 1)}>{step === 0 ? "Continue" : "Next"}</button>}
      {step === 5 && <button type="button" className={secondaryButton} onClick={() => setStep(4)}>Review inputs / generate again</button>}
    </div>
  </div>;
}
