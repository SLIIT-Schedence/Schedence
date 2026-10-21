"use client";

import { type KeyboardEvent, type ReactNode, useState } from "react";
import { Badge } from "@/components/console/ui";
import { API_TIME_LIMIT_SECONDS, NUMBER_FIELDS, type NumberField, RECOMMENDED_PROFILE, settingsErrors, settingsForm, type SettingsForm } from "@/lib/examination-settings";
import type { CycleReview } from "@/lib/examination-workspace-types";
import { fieldClass, secondaryButton } from "./period-calendar";
import { HARD_RULES } from "@/lib/examination-presentation";
import { PreferenceOverview } from "./preferences-summary";

export const PREFERENCE_TABS = [
  { id: "basic", label: "Exam Rules" }, { id: "workload", label: "Student Workload" },
  { id: "fairness", label: "Fairness" }, { id: "advanced", label: "Advanced Settings (Optional)" },
] as const;
export type PreferenceTab = typeof PREFERENCE_TABS[number]["id"];
const WORKLOAD_FIELDS: NumberField[] = ["short_gap_minutes", "preparation_target_minutes", "difficult_target_minutes", "high_difficulty_minimum", "window_days", "preferred_window_exams"];
const BASIC_FIELDS = ["permitted_start_times", "minimum_break_minutes", "max_rooms_per_exam", "max_sessions_per_exam", "seating_percentage"];

/** Presentation grouping only; settingsErrors remains the source of validation. */
export function preferenceErrorTabs(errors: Record<string, string>): Record<PreferenceTab, number> {
  const counts = { basic: 0, workload: 0, fairness: 0, advanced: 0 };
  for (const key of Object.keys(errors)) {
    const tab = BASIC_FIELDS.includes(key) ? "basic" : WORKLOAD_FIELDS.includes(key as NumberField) ? "workload"
      : ["fairness_enabled", "protect_worst_student"].includes(key) ? "fairness" : "advanced";
    counts[tab]++;
  }
  return counts;
}

export function HardRuleDescriptions({ idPrefix = "rule" }: { idPrefix?: string }) {
  return <div className="space-y-2">{HARD_RULES.map(([code, title, description]) => <details key={code} id={`${idPrefix}-${code}`} className="scroll-mt-24 rounded-lg border border-line p-3"><summary className="cursor-pointer text-sm font-semibold text-ink">{code} · {title}</summary><p className="mt-2 text-sm leading-6 text-muted">{description}</p></details>)}</div>;
}

export function SettingsReview({ review, form, onChange, disabled = false, activeTab = "basic", onTabChange, showOverview = true }: {
  review: CycleReview; form: SettingsForm; onChange: (form: SettingsForm) => void; disabled?: boolean;
  activeTab?: PreferenceTab; onTabChange?: (tab: PreferenceTab) => void;
  showOverview?: boolean;
}) {
  const { scheduling, seating } = review;
  const [localTab, setLocalTab] = useState<PreferenceTab>(activeTab);
  const [newStart, setNewStart] = useState("");
  const [customizeOpen, setCustomizeOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [referenceOpen, setReferenceOpen] = useState(false);
  const [fairnessOpen, setFairnessOpen] = useState(false);
  const tab = onTabChange ? activeTab : localTab;
  const selectTab = onTabChange ?? setLocalTab;
  const defaults = settingsForm(scheduling, seating);
  const errors = settingsErrors(form, { min: seating.min_percentage, max: seating.max_percentage });
  const errorTabs = preferenceErrorTabs(errors);
  const update = (patch: Partial<SettingsForm>) => onChange({ ...form, ...patch });
  const workloadKeys = (Object.keys(NUMBER_FIELDS) as NumberField[]).filter((key) => NUMBER_FIELDS[key].section === "optimization");
  const balanced = workloadKeys.every((key) => form[key].trim() !== "" && defaults[key].trim() !== "" && Number(form[key]) === Number(defaults[key]));
  const useBalanced = () => update(Object.fromEntries(workloadKeys.map((key) => [key, defaults[key]])) as Partial<SettingsForm>);
  const timesValid = /^(?:[01]\d|2[0-3]):[0-5]\d$/.test(newStart);
  const field = (name: NumberField, help: ReactNode, label: string = NUMBER_FIELDS[name].label, inactive = false) => {
    const rule = NUMBER_FIELDS[name];
    const id = `setting-${name}`;
    return <div key={name} className="min-w-0">
      <label htmlFor={id} className="block text-sm font-medium text-ink">{label}</label>
      <input id={id} inputMode={rule.integer ? "numeric" : "decimal"} value={form[name]} disabled={disabled || inactive}
        placeholder={name === "max_rooms_per_exam" ? "No limit" : undefined}
        onChange={(event) => update({ [name]: event.target.value })} aria-invalid={Boolean(errors[name])}
        aria-describedby={`${id}-help ${id}-error`} className={`${fieldClass} max-w-xs scroll-mb-48`} />
      <p id={`${id}-help`} className="mt-1 text-xs leading-5 text-muted">{help}</p>
      <p id={`${id}-error`} className="mt-1 text-xs text-danger">{errors[name]}</p>
    </div>;
  };
  const keyboardTab = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const next = event.key === "ArrowRight" ? (index + 1) % PREFERENCE_TABS.length
      : event.key === "ArrowLeft" ? (index + PREFERENCE_TABS.length - 1) % PREFERENCE_TABS.length
        : event.key === "Home" ? 0 : event.key === "End" ? PREFERENCE_TABS.length - 1 : null;
    if (next === null) return;
    event.preventDefault(); selectTab(PREFERENCE_TABS[next].id);
    event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next]?.focus();
  };

  return <div className="space-y-4 pb-2">
    {showOverview && <PreferenceOverview form={form} status="Selected preferences" description="Start with your institution's exam rules and workload preferences. Adjust only what this timetable needs; use Apply Preferences to keep your edits." />}
    <div className="flex flex-wrap items-start justify-between gap-2">
      <div><h2 className="text-base font-semibold text-ink">Adjust preferences</h2><p className="mt-1 text-sm text-muted">Choose a group below. Technical options are optional.</p></div>
      <Badge tone="primary">This timetable only</Badge>
    </div>
    <div role="tablist" aria-label="Scheduling preference sections" className="grid grid-cols-2 gap-1 border-b border-line pb-2 sm:grid-cols-4">
      {PREFERENCE_TABS.map((item, index) => <button key={item.id} id={`preference-tab-${item.id}`} role="tab" type="button"
        aria-selected={tab === item.id} aria-controls={`preference-panel-${item.id}`} tabIndex={tab === item.id ? 0 : -1}
        disabled={disabled} onClick={() => selectTab(item.id)} onKeyDown={(event) => keyboardTab(event, index)}
        className={`min-h-11 rounded-lg px-3 py-2 text-sm font-semibold focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary disabled:opacity-50 ${tab === item.id ? "bg-primary-soft text-primary-strong" : "text-muted hover:bg-background"}`}>
        {item.label}{errorTabs[item.id] > 0 && <span className="ml-1 rounded bg-danger-soft px-1.5 py-0.5 text-xs text-danger">{errorTabs[item.id]}<span className="sr-only"> errors</span></span>}
      </button>)}
    </div>
    <section id="preference-panel-basic" role="tabpanel" aria-labelledby="preference-tab-basic" hidden={tab !== "basic"} tabIndex={0}>
      <div>
        <fieldset disabled={disabled} className="grid gap-5 p-4 sm:grid-cols-2 sm:p-5"><legend className="sr-only">Basic exam rules</legend>
          <div className="sm:col-span-2">
            <h3 className="text-sm font-medium text-ink">Permitted exam start times</h3>
            <ul aria-label="Permitted exam start times" className="mt-2 flex flex-wrap gap-2">{form.permitted_start_times.map((time) => <li key={time} className="inline-flex items-center gap-2 rounded-lg border border-line bg-background px-3 py-1 font-mono text-sm">{time}<button type="button" aria-label={`Remove start time ${time}`} className="min-h-8 text-xs font-semibold text-danger" onClick={() => update({ permitted_start_times: form.permitted_start_times.filter((item) => item !== time) })}>Remove</button></li>)}{!form.permitted_start_times.length && <li key="empty" className="text-sm text-muted">No start times.</li>}</ul>
            <div className="mt-2 flex flex-wrap items-end gap-2"><label className="text-xs text-muted">Add a start time<input type="time" step={60} value={newStart} onChange={(event) => setNewStart(event.target.value)} className={`${fieldClass} scroll-mb-48`} /></label>
              <button type="button" className={secondaryButton} disabled={!timesValid || form.permitted_start_times.includes(newStart)} onClick={() => { update({ permitted_start_times: [...form.permitted_start_times, newStart].sort() }); setNewStart(""); }}>Add time</button></div>
            <p className="mt-1 text-xs text-muted">For example, 08:30 and 13:30. Exams must still finish within your exam calendar&apos;s daily hours.</p>
            <p role={errors.permitted_start_times ? "alert" : undefined} className="mt-1 text-xs text-danger">{errors.permitted_start_times}</p>
          </div>
          {field("minimum_break_minutes", "Free time between a student’s exams. For example, 60 means a one-hour break.", "Minimum break between exams (minutes)")}
          {field("max_rooms_per_exam", "Leave blank for no limit. For example, 2 lets one exam use up to two rooms.", "Maximum rooms per exam")}
          {field("max_sessions_per_exam", "For large groups that need separate sittings. 1 means every student sits the exam together.", "Maximum sessions per exam")}
          <div><label htmlFor="setting-seating" className="block text-sm font-medium text-ink">Default seating capacity (%)</label>
            <input id="setting-seating" inputMode="numeric" value={form.seating_percentage} onChange={(event) => update({ seating_percentage: event.target.value })} aria-invalid={Boolean(errors.seating_percentage)} aria-describedby="setting-seating-help setting-seating-error" className={`${fieldClass} max-w-xs scroll-mb-48`} />
            <p id="setting-seating-help" className="mt-1 text-xs leading-5 text-muted">50% allows 50 candidates in a 100-seat room. Venue-specific percentages stay unchanged.</p><p id="setting-seating-error" className="mt-1 text-xs text-danger">{errors.seating_percentage}</p></div>
          <details className="rounded-lg border border-line p-3 text-xs text-muted sm:col-span-2"><summary className="cursor-pointer font-semibold text-ink">View institution seating rules</summary><p className="mt-2">Loaded default: {seating.default.percentage}%. Actual timetable capacities are calculated when generating.</p>
            {seating.venue_overrides.length ? <ul className="mt-2 grid gap-1 sm:grid-cols-2">{seating.venue_overrides.map((item, index) => <li key={`${item.venue_id ?? "venue"}-${index}`}>{item.venue_id}: {item.percentage}%</li>)}</ul> : <p className="mt-2">No venue-specific percentages.</p>}
            <p className="mt-2">Venues with no exam seats under institution defaults: {seating.unavailable_venues.join(", ") || "None"}.</p></details>
        </fieldset>
      </div>
    </section>
    <section id="preference-panel-workload" role="tabpanel" aria-labelledby="preference-tab-workload" hidden={tab !== "workload"} tabIndex={0}>
      <div className="space-y-4 p-4 sm:p-5">
        <p className="text-sm leading-6 text-muted">Give students time to prepare and spread demanding exams across the calendar. These are goals, while mandatory exam rules always apply.</p>
        <div className="flex flex-wrap items-center justify-between gap-3"><div><Badge tone={balanced ? "accent" : "primary"}>{balanced ? "Balanced" : "Custom"}</Badge><p className="mt-1 text-xs leading-5 text-muted">Balanced restores your institution&apos;s workload values, including advanced priorities.</p></div><button type="button" disabled={disabled || balanced} className={secondaryButton} onClick={useBalanced}>Use Balanced Values</button></div>
        <details open={customizeOpen || errorTabs.workload > 0} onToggle={(event) => { if (event.target === event.currentTarget) setCustomizeOpen(event.currentTarget.open); }} className="border-t border-line pt-4">
          <summary className="cursor-pointer text-sm font-semibold text-primary-strong">Adjust preparation time{errorTabs.workload > 0 ? ` · ${errorTabs.workload} fields to check` : ""}</summary>
          <fieldset disabled={disabled} className="mt-4 space-y-5"><legend className="sr-only">Preparation and exam-spacing targets</legend>
            <h3 className="text-sm font-semibold text-ink">Preparation and exam-spacing targets</h3><div className="grid gap-4 sm:grid-cols-2">
              {field("short_gap_minutes", "Gaps below this count as very short. 720 minutes is 12 hours.", "Short exam gap (minutes)")}
              {field("preparation_target_minutes", "Preferred study time between any two exams. 2880 minutes is two days.", "Preparation time target (minutes)")}
              {field("difficult_target_minutes", "Preferred study time between two difficult exams.", "Preparation for difficult exams (minutes)")}
              {field("high_difficulty_minimum", "Modules at or above this level count as difficult.", "Difficult exam level (1–5)")}
              {field("window_days", "How many consecutive days to review together.", "Busy-period length (days)")}
              {field("preferred_window_exams", "Exams above this count increase the workload score.", "Preferred exams in that period")}
            </div><p className="text-xs text-muted">Priority weights and calculation options are in Advanced Settings.</p>
          </fieldset>
        </details>
      </div>
    </section>
    <section id="preference-panel-fairness" role="tabpanel" aria-labelledby="preference-tab-fairness" hidden={tab !== "fairness"} tabIndex={0}>
      <fieldset disabled={disabled} className="space-y-4 p-4 sm:p-5"><legend className="sr-only">Fairness protection</legend>
        <p className="text-sm leading-6 text-muted">Help protect individual students when improving the overall timetable. Compare each student&apos;s workload with the first valid timetable found.</p>
        <label className="flex items-center gap-3 rounded-xl border border-line p-3 text-sm font-semibold text-ink"><input id="setting-fairness-enabled" type="checkbox" checked={form.fairness_enabled} onChange={(event) => update({ fairness_enabled: event.target.checked })} className="size-4 accent-primary" aria-describedby="fairness-help fairness-error" />Fairness protection {form.fairness_enabled ? "ON" : "OFF"}</label>
        <p id="fairness-help" className="text-xs leading-5 text-muted">Works in Optimized mode. Your fairness limit values are kept when protection is off.</p><p id="fairness-error" className="text-xs text-danger">{errors.fairness_enabled}</p>
        {!form.fairness_enabled && <p className="rounded-xl bg-secondary-soft p-3 text-sm leading-6 text-secondary-ink">Fairness protection is disabled. The total workload score may improve while some students receive a harder schedule; individual fairness limits are not enforced.</p>}
        <label className="flex items-start gap-2 text-sm font-medium text-ink"><input type="checkbox" disabled={disabled || !form.fairness_enabled} checked={form.protect_worst_student} onChange={(event) => update({ protect_worst_student: event.target.checked })} className="mt-1 size-4 accent-primary" />Also protect the student with the highest workload score</label>
        <button type="button" disabled={disabled} onClick={() => selectTab("advanced")} className="min-h-10 text-sm font-semibold text-primary-strong underline-offset-4 hover:underline">Review fairness limits in Advanced Settings</button>
        {(!form.fairness_enabled && (errors.max_individual_increase_points || errors.max_worst_increase_points)) && <p role="alert" className="text-sm text-danger">A retained fairness limit is invalid. Turn protection on to correct it; your values are still kept.</p>}
        <p className="text-xs leading-5 text-muted">Limits are never loosened automatically. If no improvement meets them, the first valid timetable is kept.</p>
      </fieldset>
    </section>
    <section id="preference-panel-advanced" role="tabpanel" aria-labelledby="preference-tab-advanced" hidden={tab !== "advanced"} tabIndex={0}>
      <fieldset disabled={disabled} className="space-y-5 p-4 sm:p-5"><legend className="sr-only">Advanced scheduling preferences</legend>
        <p className="text-sm text-muted">Optional fine-tuning. Keep the loaded values unless you need a specific change.</p>
        {field("time_limit_seconds", `Maximum search time, up to ${API_TIME_LIMIT_SECONDS} seconds. Recommended profile for Optimized mode: ${RECOMMENDED_PROFILE.time_limit_seconds} seconds.`, "Solver time limit (seconds)")}
        <details open={searchOpen || Boolean(errors.random_seed || errors.num_search_workers || errors.baseline_budget_percent)} onToggle={(event) => { if (event.target === event.currentTarget) setSearchOpen(event.currentTarget.open); }} className="rounded-xl border border-line p-3"><summary className="cursor-pointer text-sm font-semibold text-ink">Search controls</summary><div className="mt-4 grid gap-4 sm:grid-cols-2">
          {field("random_seed", "Controls the search order. Keep the same value for repeatable searches.", "Search seed")}
          {field("num_search_workers", `Parallel searches. Recommended profile for Optimized mode: ${RECOMMENDED_PROFILE.num_search_workers}. Use 1 for repeatable results.`, "Search workers")}
          {field("baseline_budget_percent", "Share of search time used to find the first valid timetable in Optimized mode.", "Initial search budget (%)")}
        </div></details>
        <details open={referenceOpen || Boolean(errors.difficulty_weight || errors.credits_weight || errors.duration_weight || errors.reference_difficulty || errors.reference_credits || errors.reference_duration_minutes || errors.s1_weight || errors.s2_weight || errors.s3_weight || errors.s4_weight || errors.s5_weight)} onToggle={(event) => { if (event.target === event.currentTarget) setReferenceOpen(event.currentTarget.open); }} className="border-t border-line pt-4"><summary className="cursor-pointer text-sm font-semibold text-ink">Workload priorities and reference values</summary><p className="mt-2 text-xs leading-5 text-muted">Higher weights give a goal more importance; 0 ignores it. Reference values define how exam workload is compared.</p><div className="mt-4 grid gap-4 sm:grid-cols-2">
          {field("s1_weight", "Avoid difficult exams close together.", "Difficult exams close together (S1 weight)")}
          {field("s2_weight", "Allow more preparation time between exams.", "Preparation time (S2 weight)")}
          {field("s3_weight", "Avoid same-day exams and very short gaps.", "Same-day exams and short gaps (S3 weight)")}
          {field("s4_weight", "Spread demanding exams across the calendar.", "Demanding exams together (S4 weight)")}
          {field("s5_weight", "Avoid too many exams in a few days.", "Too many exams in a few days (S5 weight)")}
          {field("difficulty_weight", "How much module difficulty contributes to exam workload.")}
          {field("credits_weight", "How much module credits contribute to exam workload.")}
          {field("duration_weight", "How much exam length contributes to exam workload.")}
          {field("reference_difficulty", "Difficulty level used as the normal exam reference.")}
          {field("reference_credits", "Credit value used as the normal exam reference.")}
          {field("reference_duration_minutes", "Exam length, including reading time, used as the reference.")}
        </div></details>
        <details open={fairnessOpen || Boolean(errors.max_individual_increase_points || errors.max_worst_increase_points)} onToggle={(event) => { if (event.target === event.currentTarget) setFairnessOpen(event.currentTarget.open); }} className="border-t border-line pt-4">
          <summary className="cursor-pointer text-sm font-semibold text-ink">Fairness score limits</summary>
          <p className="mt-2 text-xs leading-5 text-muted">Only active when fairness protection is on. Retained values are still checked when it is off.</p>
          <div hidden={!form.fairness_enabled} className="space-y-4"><fieldset disabled={!form.fairness_enabled} className="mt-4 grid gap-4 sm:grid-cols-2"><legend className="sr-only">Fairness limits</legend>
            {field("max_individual_increase_points", "Maximum workload score increase for any student, from 0 to 100 points.", "Allowed increase per student (points)")}
            {field("max_worst_increase_points", "0 means the highest workload score cannot increase.", "Allowed increase in the highest score (points)", !form.protect_worst_student)}
          </fieldset></div>
          {!form.fairness_enabled && <p className="mt-2 text-xs text-muted">Turn on protection in Fairness to edit these limits.</p>}
        </details>
        <details className="rounded-xl border border-line p-3"><summary className="cursor-pointer text-sm font-semibold text-ink">Technical configuration (read-only)</summary><dl className="mt-3 space-y-3 text-xs leading-5 text-muted">
          <div><dt className="font-semibold text-ink">Score and load scale</dt><dd>{String(scheduling.settings.optimization?.score_scale ?? "Not returned")} / {String(scheduling.settings.optimization?.load_scale ?? "Not returned")}</dd></div>
          <div><dt className="font-semibold text-ink">Venue types per exam type</dt><dd>{Object.entries((scheduling.settings.scheduling?.venue_compatibility ?? {}) as Record<string, string[]>).map(([type, venues]) => `${type}: ${venues.join(", ")}`).join(" · ") || "None returned"}</dd></div>
          <div><dt className="font-semibold text-ink">Module venue exceptions</dt><dd>{Object.entries((scheduling.settings.scheduling?.module_venue_overrides ?? {}) as Record<string, string[]>).map(([code, venues]) => `${code}: ${venues.join(", ")}`).join(" · ") || "None returned"}</dd></div>
          <div><dt className="font-semibold text-ink">Automatic fairness relaxation</dt><dd>{String(scheduling.settings.fairness?.automatic_relaxation ?? "Not returned")}</dd></div>
        </dl></details>
        <details className="rounded-xl border border-line p-3"><summary className="cursor-pointer text-sm font-semibold text-ink">Mandatory rules H1–H11 (read-only)</summary><p className="my-3 text-xs leading-5 text-muted">Always enforced; no switch can disable these rules. {scheduling.mandatory_rules.length} mandatory rules confirmed by the service.</p><HardRuleDescriptions idPrefix="preference-rule" /></details>
      </fieldset>
    </section>
  </div>;
}
