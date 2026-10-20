"use client";

import { type KeyboardEvent, useEffect, useState, useTransition } from "react";
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
import { PREFERENCE_TABS, preferenceErrorTabs, SettingsReview, type PreferenceTab } from "./settings-review";
import {
  effectiveForm, RECOMMENDED_PROFILE, schedulingPreferences, settingsErrors, settingsForm, solverSource, withRecommendedProfile, type SettingsForm,
} from "@/lib/examination-settings";

const STEPS = ["Select Exam", "Choose Dates", "Review Modules", "Generate", "Results"];
const GENERATE = 3;
const RESULTS = 4;
const SOLVER_SOURCE = { recommended: "recommended profile", institution: "institution settings", custom: "custom" } as const;
const workersLabel = (value: string) => `${value} search worker${Number(value) === 1 ? "" : "s"}`;

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
  const [mode, setMode] = useState<GenerationMode>("optimized");
  const [timeLimit, setTimeLimit] = useState("");
  const [seconds, setSeconds] = useState(0);
  const [task, setTask] = useState<"review" | "generation" | "save">("review");
  const [saveState, setSaveState] = useState<SaveDraftState>({ status: "idle" });
  const [confirmSave, setConfirmSave] = useState(false);
  // Advanced Settings edits are parent-owned so they survive leaving the Generate step; only applied values may generate.
  // Both forms start as the institution settings plus the recommended solver profile (this timetable only).
  const [preferenceForm, setPreferenceForm] = useState<SettingsForm | null>(null);
  const [appliedForm, setAppliedForm] = useState<SettingsForm | null>(null);
  const [preferenceTab, setPreferenceTab] = useState<PreferenceTab>("basic");
  const [preferenceSession, setPreferenceSession] = useState(0);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const fields = state.status === "error" ? state.fieldErrors : undefined;
  const selected = cycles.find((cycle) => cycle.period_code === periodCode && isGeneratable(cycle));
  const calendarProblems = calendar && review ? calendarErrors(calendar, review.period.academic_year_label) : {};
  const calendarValid = Boolean(calendar) && Object.keys(calendarProblems).length === 0;
  const calendarJson = calendar ? JSON.stringify(calendar) : "";
  const seatingLimits = review ? { min: review.seating.min_percentage, max: review.seating.max_percentage } : undefined;
  // What this generation really uses: the applied values, with the profile belonging to Optimized mode only.
  const institution = review ? settingsForm(review.scheduling, review.seating) : null;
  const used = appliedForm && institution ? effectiveForm(appliedForm, institution, mode) : appliedForm;
  const settingsDirty = JSON.stringify(preferenceForm) !== JSON.stringify(appliedForm);
  const preferencesJson = used ? JSON.stringify(schedulingPreferences(used)) : "";
  const preferencesValid = used !== null && Object.keys(settingsErrors(used, seatingLimits)).length === 0;
  const preferenceErrors = preferenceForm ? settingsErrors(preferenceForm, seatingLimits) : {};
  const preferenceErrorCount = Object.keys(preferenceErrors).length;
  const errorTabs = preferenceErrorTabs(preferenceErrors);
  const shortened = timeLimit.trim();
  const limitMax = Math.min(300, Number(used?.time_limit_seconds) || 300);
  const timeLimitError = shortened !== "" && !(Number(shortened) > 0 && Number(shortened) <= limitMax);
  const solverLimit = shortened !== "" && !timeLimitError ? shortened : used?.time_limit_seconds ?? "";

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
      const defaults = withRecommendedProfile(settingsForm(response.data.scheduling, response.data.seating));
      setPreferenceForm(defaults); setAppliedForm(defaults); setTimeLimit("");
      setPreferenceTab("basic"); setAdvancedOpen(false); setPreferenceSession((previous) => previous + 1);
    } catch { setLoadError("The cycle could not be loaded. Retry after checking the connection."); }
  }); };
  const changeCalendar = (next: ExamCalendarInput) => {
    // Results and save confirmations belong to the previous calendar.
    setCalendar(next); clearResults(); setFurthest((previous) => Math.min(previous, GENERATE));
  };
  const generate = (formData: FormData) => { setTask("generation"); startTransition(async () => {
    clearResults();
    setSeconds(0);
    try {
      const response = await generateFinalTimetable({ status: "idle" }, formData);
      setState(response);
      if (response.status === "complete") advance(RESULTS);
    } catch { setState({ status: "error", title: "The generation connection was interrupted", message: "The solver may still be running. Wait before trying another generation request." }); }
  }); };
  const saveDraft = () => {
    if (!review || !calendarValid || pending || settingsDirty || !appliedForm || timeLimitError) return;
    const data = new FormData();
    data.set("period_code", review.period.period_code);
    data.set("expected_period_fingerprint", review.period.fingerprint ?? "");
    data.set("expected_settings_fingerprint", review.scheduling.fingerprint ?? "");
    data.set("expected_seating_version", String(review.seating.ruleset_version));
    data.set("calendar", calendarJson);
    data.set("scheduling_preferences", preferencesJson);
    data.set("mode", mode);
    if (shortened) data.set("time_limit_seconds", shortened);
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
  const applyPreferences = () => {
    setAppliedForm(preferenceForm); setTimeLimit(""); clearResults();
  };
  const resetPreferences = () => {
    if (!review) return;
    const defaults = withRecommendedProfile(settingsForm(review.scheduling, review.seating));
    setPreferenceForm(defaults); setAppliedForm(defaults); setTimeLimit(""); clearResults();
  };
  // Pressing Enter in an Advanced Settings field must never start a generation; only the Generate button does.
  const blockEnterSubmit = (event: KeyboardEvent<HTMLFormElement>) => {
    if (event.key === "Enter" && (event.target as HTMLElement).tagName === "INPUT") event.preventDefault();
  };
  const disabled = pending;
  const ready = Boolean(review?.mapping.length) && review?.scheduling.status === "valid" && calendarValid && !settingsDirty && preferencesValid && !timeLimitError;
  const saveCard = review && <SaveDraftCard periodName={review.period.period_name} mode={mode} timeLimit={solverLimit} confirmed={confirmSave}
    onConfirm={setConfirmSave} onSave={saveDraft} saving={pending && task === "save"} state={saveState}
    disabled={disabled || !ready || saveState.status === "saved"} />;
  const canNext = step === 0 ? Boolean(selected)
    : Boolean(review) && (step !== 1 || calendarValid) && (step !== 2 || Boolean(review?.mapping.length));
  const source = used && institution ? solverSource(used, institution) : "custom";

  return <div className="space-y-6">
    <nav aria-label="Timetable generation steps" className="rounded-2xl border border-line bg-surface p-4">
      <ol className="grid gap-2 sm:grid-cols-3 xl:grid-cols-5">{STEPS.map((label, index) => <li key={label}>
        <button type="button" disabled={disabled || index > furthest || (index === RESULTS && state.status !== "complete") || (index > 1 && !calendarValid)} onClick={() => setStep(index)} aria-current={step === index ? "step" : undefined}
          className={`flex min-h-12 w-full items-center gap-2 rounded-xl px-3 text-left text-xs font-semibold disabled:cursor-not-allowed ${step === index ? "bg-primary text-white" : index < step ? "bg-accent text-accent-ink" : "bg-background text-muted"}`}>
          <span className="grid size-6 shrink-0 place-items-center rounded-full border border-current/30">{index + 1}</span>{label}
        </button>
      </li>)}</ol>
      <p className="mt-3 text-xs text-muted">Step {step + 1} of {STEPS.length} · {STEPS[step]}</p>
    </nav>
    {review?.warnings.map((warning) => <p key={warning} className="rounded-xl bg-secondary-soft p-4 text-sm text-secondary-ink">{warning}</p>)}

    {step === 0 && <>
      <ExamSelection options={cycles} choice={choice} disabled={pending} onChange={(next) => {
        setChoice(next); setReview(null); setCalendar(null); setPreferenceForm(null); setAppliedForm(null); clearResults(); setFurthest(0); setLoadError("");
      }} />
      {selected && <p className="text-sm text-muted">Continue loads the current period, module mapping, settings and seating rules.</p>}
      {loadError && <p role="alert" className="text-sm text-danger">{loadError}</p>}
    </>}
    {step === 1 && review && calendar && <GenerationCalendar period={review.period} calendar={calendar} onChange={changeCalendar}
      permittedStarts={appliedForm?.permitted_start_times ?? review.scheduling.settings.scheduling?.permitted_start_times} disabled={pending} />}
    {step === 2 && review && <AcademicGroups modules={review.modules} mapping={review.mapping} />}
    {settingsDirty && step !== GENERATE && <p role="status" className="rounded-xl bg-secondary-soft p-4 text-sm text-secondary-ink">You have unapplied Advanced Settings. Your edits are kept. Open Advanced Settings in the Generate step, then Apply Preferences or Reset to Defaults before generating.</p>}
    {step === GENERATE && review && calendar && used && institution && <Card title="4. Generate your timetable" description="Check the summary, then generate the full examination timetable. You can download the result before deciding whether to save a draft." actions={<Badge tone="primary">FINAL · Full cycle</Badge>}>
      <form action={generate} onKeyDown={blockEnterSubmit} className="space-y-5 p-5" aria-busy={pending}>
        <input type="hidden" name="period_code" value={review.period.period_code} />
        <input type="hidden" name="expected_period_fingerprint" value={review.period.fingerprint ?? ""} />
        <input type="hidden" name="expected_settings_fingerprint" value={review.scheduling.fingerprint ?? ""} />
        <input type="hidden" name="expected_seating_version" value={review.seating.ruleset_version} />
        <input type="hidden" name="calendar" value={calendarJson} />
        <input type="hidden" name="scheduling_preferences" value={preferencesJson} />
        {review.scheduling.status === "invalid" && <div role="alert" className="rounded-xl bg-danger-soft p-4 text-sm leading-6 text-danger">
          <p className="font-semibold">Generation is blocked because the saved institution settings are not valid.</p>
          <ul className="mt-1 list-disc pl-5">{review.scheduling.problems.map((item) => <li key={`${item.rule}-${item.message}`}>{item.rule}: {item.message}</li>)}</ul>
        </div>}
        <section aria-label="Generation summary" className="rounded-xl bg-background p-4 text-sm text-muted">
          <p className="font-semibold text-ink">{review.period.period_name} · <span className="font-mono text-xs font-normal">{review.period.period_code}</span></p>
          <dl className="mt-3 grid gap-x-5 gap-y-3 sm:grid-cols-2 xl:grid-cols-3">
            {([
              ["Exam", `${review.period.exam_kind} · ${review.period.academic_year_label} · Semester ${review.period.semester}`],
              ["Dates", `${formatDate(calendar.start_date)} – ${formatDate(calendar.end_date)} · ${calendarExamDays(calendar).length} exam days · ${calendar.day_start_time}–${calendar.day_end_time}`],
              ["Modules", `${review.mapping.length} mapped modules · ${review.seating.venue_count} venues`],
              ["Mode", mode === "optimized" ? "Optimized" : "Feasibility"],
              ["Solver", `${workersLabel(used.num_search_workers)} · ${used.time_limit_seconds}-second limit (${SOLVER_SOURCE[source]})`],
              ["Seating and breaks", `${used.seating_percentage}% default seating · ${used.minimum_break_minutes}-minute break`],
            ] as const).map(([label, value]) => <div key={label}><dt className="text-xs">{label}</dt><dd className="mt-1 font-medium text-ink">{value}</dd></div>)}
          </dl>
          <p className="mt-3 text-xs">{calendar.include_saturdays || calendar.include_sundays ? `Weekends: ${[calendar.include_saturdays && "Saturdays", calendar.include_sundays && "Sundays"].filter(Boolean).join(", ")}` : "Weekends excluded"} · {calendar.exclusions.length} excluded date(s). Venue-specific seating rules still apply.</p>
          <details className="mt-3 text-xs"><summary className="cursor-pointer font-semibold text-primary-strong">Review all settings in use</summary><p className="mt-2 leading-5">Start times: {used.permitted_start_times.join(", ")} · Minimum break: {used.minimum_break_minutes} minutes · Rooms per exam: {used.max_rooms_per_exam || "No fixed limit"} · Maximum sessions per exam: {used.max_sessions_per_exam}.</p><p className="mt-1 leading-5">Workload weights (S1–S5): {[used.s1_weight, used.s2_weight, used.s3_weight, used.s4_weight, used.s5_weight].join(" / ")} · Fairness protection: {used.fairness_enabled ? "On (Optimized mode)" : "Off"}. Mandatory rules H1–H11 always apply.</p></details>
        </section>
        <fieldset disabled={pending} className="space-y-5">
          <legend className="sr-only">Generation mode</legend>
          <div className="grid gap-3 sm:grid-cols-2">{([
            ["optimized", "Optimized (recommended)", "Find a valid timetable and improve students’ exam workload. Uses the recommended solver profile unless you change it in Advanced Settings."],
            ["feasibility", "Basic / Feasibility", "Find a valid timetable without workload optimization. All mandatory examination rules still apply."],
          ] as const).map(([value, label, description]) => <label key={value} className={`flex cursor-pointer items-start gap-3 rounded-xl border p-4 ${mode === value ? "border-primary bg-primary-soft" : "border-line"}`}>
            <input type="radio" name="mode" value={value} checked={mode === value} onChange={() => { setMode(value); setTimeLimit(""); clearResults(); }} className="mt-1 accent-primary" />
            <span><strong className="block text-sm">{label}</strong><span className="mt-1 block text-sm text-muted">{description}</span></span>
          </label>)}</div>
          {fields && <ul className="text-sm text-danger">{Object.entries(fields).map(([key, message]) => <li key={key}>{message}</li>)}</ul>}
        </fieldset>
        <details id="advanced-settings" open={advancedOpen || preferenceErrorCount > 0 || timeLimitError}
          onToggle={(event) => { if (event.target === event.currentTarget) setAdvancedOpen(event.currentTarget.open); }} className="rounded-xl border border-line">
          <summary className="cursor-pointer px-4 py-3 text-sm font-semibold text-primary-strong">Advanced Settings (Optional)</summary>
          <div className="space-y-4 border-t border-line p-4">
            <p className="text-sm leading-6 text-muted">You can skip this. Optimized mode already uses the recommended profile: {RECOMMENDED_PROFILE.num_search_workers} search workers and a {RECOMMENDED_PROFILE.time_limit_seconds}-second solver limit. Changes here apply to this timetable only. Institution settings, S1–S5 weights and mandatory rules H1–H11 are not changed.</p>
            {preferenceForm && <SettingsReview key={preferenceSession} review={review} form={preferenceForm} disabled={pending}
              onChange={setPreferenceForm} activeTab={preferenceTab} onTabChange={setPreferenceTab} />}
            <fieldset disabled={pending}>
              <legend className="sr-only">Shorten this run</legend>
              <label htmlFor="time-limit" className="block text-sm font-medium text-ink">Shorten this run (seconds, optional)
                <input id="time-limit" name="time_limit_seconds" type="number" min="0.001" max={limitMax} step="any" value={timeLimit}
                  onChange={(event) => { setTimeLimit(event.target.value); clearResults(); }} aria-invalid={timeLimitError}
                  aria-describedby="limit-help limit-error" className={`${fieldClass} max-w-xs`} placeholder={`Use ${used.time_limit_seconds}s`} />
              </label>
              <p id="limit-help" className="mt-1 text-xs text-muted">Leave blank to use the solver limit in use ({used.time_limit_seconds} seconds). A shorter time finishes sooner but may give a less balanced timetable. It cannot be longer than that limit.</p>
              <p id="limit-error" role={timeLimitError ? "alert" : undefined} className="mt-1 text-xs text-danger">{timeLimitError ? `Enter a time above 0 and no more than ${limitMax} seconds.` : ""}</p>
            </fieldset>
            <div role="region" aria-label="Scheduling preference actions" className="space-y-2 rounded-xl border border-line bg-surface p-3">
              <p role="status" aria-live="polite" className={`text-sm font-medium ${preferenceErrorCount ? "text-danger" : settingsDirty ? "text-secondary-ink" : "text-accent-ink"}`}>
                {preferenceErrorCount ? `Check ${preferenceErrorCount} preference field${preferenceErrorCount === 1 ? "" : "s"} before applying.` : settingsDirty ? "Unapplied changes. Apply Preferences before generating." : "Preferences applied to this timetable."}
              </p>
              {preferenceErrorCount > 0 && <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs">{PREFERENCE_TABS.filter((tab) => errorTabs[tab.id] > 0).map((tab) => <a key={tab.id} href={`#preference-tab-${tab.id}`} onClick={() => setPreferenceTab(tab.id)} className="font-semibold text-danger underline underline-offset-2">{tab.label} ({errorTabs[tab.id]})</a>)}</div>}
              <div className="grid grid-cols-2 gap-2 sm:flex sm:flex-wrap">
                <button type="button" disabled={disabled} className={secondaryButton} onClick={resetPreferences}>Reset to Defaults</button>
                <button type="button" disabled={disabled || !settingsDirty || preferenceErrorCount > 0} className={primaryButton} onClick={applyPreferences}>Apply Preferences</button>
              </div>
              <p className="text-xs text-muted">Defaults are the institution settings plus the recommended solver profile.</p>
            </div>
          </div>
        </details>
        <button type="submit" disabled={pending || !ready} className={primaryButton}><Icon name="edit_note" size={18} />{pending ? "Generating…" : "Generate Timetable"}</button>
        <p className="text-xs text-muted">Generation uses the selected calendar and the settings above for this timetable. It does not save a timetable or change institution defaults.</p>
        {state.status === "error" && <div role="alert" className="rounded-xl bg-danger-soft p-4 text-sm leading-6 text-danger"><h3 className="font-semibold">{state.title}</h3><p>{state.httpStatus ? `HTTP ${state.httpStatus}: ` : ""}{state.message}</p></div>}
      </form>
    </Card>}
    {/* Save Draft opens inside the results' action area, so it is reachable without scrolling to the bottom. */}
    {step === RESULTS && state.status === "complete" && <GenerationResults result={state.result}>
      {state.result.generated && state.result.validation.passed === true && saveCard}
    </GenerationResults>}

    {pending && <Card><div role="status" aria-busy="true" className="flex flex-col items-center p-6 text-center">
      <span aria-hidden="true" className="size-7 animate-spin rounded-full border-4 border-primary-soft border-t-primary" />
      <p className="mt-3 font-semibold text-ink">{task === "review" ? "Loading cycle review" : task === "save" ? "Generating and saving a new draft" : "Generating your FINAL timetable"}</p>
      <p className="mt-2 text-sm text-muted">{task === "review" ? "Reading the configured period, module mapping and settings."
        : task === "save" ? `A fresh generation runs on the selected exam calendar, is validated again and is saved as a DRAFT. The solver may use up to ${solverLimit} seconds, plus input processing. Keep this page open.`
          : `The solver may use up to ${solverLimit} seconds, plus input processing. Keep this page open.`}</p>
      <p aria-live="off" className="mt-2 font-mono text-xs text-secondary-ink">Elapsed: {Math.floor(seconds / 60)}m {seconds % 60}s</p>
    </div></Card>}
    <div role="region" aria-label="Wizard navigation" className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-line bg-surface p-3 sm:p-4">
      <button type="button" disabled={disabled || step === 0} className={secondaryButton} onClick={() => setStep((previous) => previous - 1)}>Back</button>
      {review && step > 0 && <button type="button" disabled={disabled} className={secondaryButton} onClick={load}>Reload examination (resets the calendar, settings and results)</button>}
      {step < GENERATE && <button type="button" disabled={disabled || !canNext} className={primaryButton} onClick={() => step === 0 ? load() : advance(step + 1)}>{step === 0 ? "Continue" : "Next"}</button>}
      {step === RESULTS && <button type="button" className={secondaryButton} onClick={() => setStep(GENERATE)}>Review inputs / generate again</button>}
    </div>
  </div>;
}
