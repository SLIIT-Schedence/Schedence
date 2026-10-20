"use client";

import { type ReactNode, useRef, useState, useTransition } from "react";
import { Badge, Card } from "@/components/console/ui";
import type { ExaminationResult } from "@/lib/examination-api";
import { HARD_RULES } from "@/lib/examination-presentation";
import {
  API_TIME_LIMIT_SECONDS, changedSettings, DEFAULT_START_TIMES, NUMBER_FIELDS, type NumberField, reasonError, settingLabel, settingsDocument,
  settingsErrors, settingsForm, type SettingsForm, type SettingsPreview, type SettingsSaved,
} from "@/lib/examination-settings";
import { previewSettingsChange, saveSettingsChange } from "@/lib/examination-settings-actions";
import type { CycleReview, SchedulingReview, SeatingReview } from "@/lib/examination-workspace-types";
import { fieldClass, primaryButton, secondaryButton } from "../../generate/_components/period-calendar";

export function HardRuleDescriptions() {
  return <div className="space-y-2">{HARD_RULES.map(([code, title, description]) => <details key={code} id={`rule-${code}`} className="scroll-mt-24 rounded-lg border border-line p-3"><summary className="cursor-pointer text-sm font-semibold text-ink">{code} · {title}</summary><p className="mt-2 text-sm leading-6 text-muted">{description}</p></details>)}</div>;
}

const S_ITEMS: [NumberField, string, string][] = [
  ["s1_weight", "S1 · Difficult exams close together", "Two difficult exams with less than the difficult-exam target between them."],
  ["s2_weight", "S2 · Little preparation time", "Any two exams with less than the preparation target between them."],
  ["s3_weight", "S3 · Same day or very short gap", "Two exams on the same day, or with less than the short gap between them."],
  ["s4_weight", "S4 · Heavy exams bunched together", "Demanding exams concentrated inside one busy-period window."],
  ["s5_weight", "S5 · Too many exams in a few days", "More exams than preferred inside one busy-period window."],
];
const hours = (value: string) => /^\d+$/.test(value.trim()) ? ` = ${Math.round(Number(value) / 6) / 10} hours` : "";
const shown = (value: number | null) => value === null ? "No limit" : String(value);

type Props = {
  review: Pick<CycleReview, "scheduling" | "seating">; disabled?: boolean;
  /** Called when the form gets or loses unsaved edits. */
  onDirty?: (dirty: boolean) => void;
  /** Called with the reloaded settings after a save. */
  onSaved?: (scheduling: SchedulingReview, seating: SeatingReview) => void;
};

export function InstitutionSettingsEditor({ review, disabled = false, onDirty, onSaved }: Props) {
  const { scheduling, seating } = review;
  const base = settingsForm(scheduling, seating);
  const [form, setForm] = useState<SettingsForm>(base);
  const [reason, setReason] = useState("");
  const [reasonTouched, setReasonTouched] = useState(false);
  const [newStart, setNewStart] = useState("");
  const [preview, setPreview] = useState<{ key: string; result: ExaminationResult<SettingsPreview> } | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [notice, setNotice] = useState<{ tone: "success" | "warning"; text: string } | null>(null);
  const [transportError, setTransportError] = useState("");
  const [operation, setOperation] = useState<"preview" | "save">("preview");
  const [pending, startTransition] = useTransition();
  const revision = useRef(0);

  const baseKey = JSON.stringify(base);
  const dirty = JSON.stringify(form) !== baseKey;
  const key = JSON.stringify([form, reason.trim()]);
  const result = preview?.key === key ? preview.result : null;
  const validated = result?.ok ? result.data : null;
  const errors: Record<string, string> = { ...(result && !result.ok ? result.error.fieldErrors : {}), ...settingsErrors(form, { min: seating.min_percentage, max: seating.max_percentage }) };
  const reasonProblem = reasonError(reason);
  const formErrors = Object.keys(errors).filter((item) => item !== "reason").length > 0;
  const changes = formErrors ? [] : [...changedSettings(scheduling.settings, settingsDocument(scheduling.settings, form)),
    ...(Number(form.seating_percentage) !== seating.default.percentage ? ["seating"] : [])];
  const locked = disabled || pending;

  const invalidate = () => { revision.current++; setPreview(null); setConfirmed(false); setTransportError(""); setNotice(null); };
  const update = (patch: Partial<SettingsForm>) => {
    const next = { ...form, ...patch };
    setForm(next); invalidate(); onDirty?.(JSON.stringify(next) !== baseKey);
  };
  const reset = () => { setForm(base); setReason(""); setReasonTouched(false); setNewStart(""); invalidate(); onDirty?.(false); };
  const expected = { scheduling_version: scheduling.version, seating_version: seating.default.version };

  const request = (save: boolean) => {
    setReasonTouched(true);
    if (locked || formErrors || reasonProblem || !changes.length || (save && !(validated && confirmed))) return;
    const submitted = { form, reason, key, consent: confirmed };
    setOperation(save ? "save" : "preview");
    invalidate();
    const started = revision.current;
    startTransition(async () => {
      try {
        if (!save) {
          const response = await previewSettingsChange(submitted.form, expected, submitted.reason);
          if (revision.current === started) setPreview({ key: submitted.key, result: response });
          return;
        }
        const response: ExaminationResult<SettingsSaved> = await saveSettingsChange(submitted.form, expected, submitted.reason, submitted.consent);
        if (!response.ok) { setPreview({ key: submitted.key, result: response }); return; }
        const saved = response.data;
        if (saved.scheduling && saved.seating) {
          setForm(settingsForm(saved.scheduling, saved.seating)); setReason(""); setReasonTouched(false); onDirty?.(false);
          onSaved?.(saved.scheduling, saved.seating);
        }
        setNotice(saved.problem ? { tone: "warning", text: saved.problem }
          : !saved.scheduling || !saved.seating ? { tone: "warning", text: "Your changes were saved, but the new values could not be reloaded. Use “Reload examination” before generating." }
            : { tone: "success", text: `Saved. The next generation uses settings version ${saved.scheduling.version} and seating ruleset ${saved.seating.ruleset_version}. Earlier temporary results were cleared.` });
      } catch { setTransportError("The request was interrupted. Reload the examination to check which settings are saved before trying again."); }
    });
  };

  const field = (name: NumberField, help: ReactNode, options: { placeholder?: string; disabled?: boolean } = {}) => {
    const rule = NUMBER_FIELDS[name];
    const id = `setting-${name}`;
    return <div key={name}>
      <label htmlFor={id} className="block text-sm font-medium text-ink">{rule.label}</label>
      <input id={id} inputMode={rule.integer ? "numeric" : "decimal"} value={form[name]} placeholder={options.placeholder} disabled={locked || options.disabled}
        onChange={(event) => update({ [name]: event.target.value } as Partial<SettingsForm>)} aria-invalid={Boolean(errors[name])}
        aria-describedby={`${id}-help ${id}-error`} className={`${fieldClass} max-w-xs`} />
      <p id={`${id}-help`} className="mt-1 text-xs leading-5 text-muted">{help} <span className="text-secondary-ink">Default: {shown(rule.default)}.</span></p>
      <p id={`${id}-error`} className="mt-1 text-xs text-danger">{errors[name]}</p>
    </div>;
  };
  const addStart = () => {
    if (!/^\d{2}:\d{2}$/.test(newStart) || form.permitted_start_times.includes(newStart)) return;
    update({ permitted_start_times: [...form.permitted_start_times, newStart].sort() }); setNewStart("");
  };
  const fairnessOff = !form.fairness_enabled;

  return <div className="space-y-5">
    <Card title="Scheduling preferences" description={scheduling.source === "defaults" ? "Using the built-in defaults: nothing has been saved yet." : `Saved settings, version ${scheduling.version}. Seating ruleset ${seating.ruleset_version}.`}
      actions={<Badge tone="neutral">Institution-wide</Badge>}>
      <div className="grid gap-3 p-5 text-sm leading-6 md:grid-cols-2">
        <div className="rounded-xl bg-secondary-soft p-4 text-secondary-ink"><h3 className="font-semibold">Saved for every examination cycle</h3>
          <p>These are institution defaults for future timetables. Customize one timetable in the Generate wizard. Scheduling defaults do not replace a timetable&apos;s stored preferences; changes to source seating rules are still checked.</p></div>
        <div className="rounded-xl bg-background p-4 text-muted"><h3 className="font-semibold text-ink">For this generation only</h3>
          <p>The exam dates chosen in the wizard, the mode (Feasibility or Optimized), the recommended solver profile (4 workers, 120 seconds) and any Advanced Settings changes are used only for that timetable. They are not saved here.</p></div>
        {scheduling.status === "invalid" && <div role="alert" className="rounded-xl bg-danger-soft p-4 text-danger md:col-span-2">
          <p className="font-semibold">The saved settings are not valid, so generation is blocked.</p>
          <ul className="mt-1 list-disc pl-5">{scheduling.problems.map((item) => <li key={`${item.rule}-${item.message}`}>{item.rule}: {item.message}</li>)}</ul>
          <p className="mt-1">Correct the fields below and save them to repair the settings.</p>
        </div>}
      </div>
    </Card>

    <fieldset disabled={locked} className="space-y-5" aria-busy={pending}>
      <legend className="sr-only">Editable scheduling preferences</legend>
      <Card title="Exam scheduling" description="When exams may start, and how exams are split across rooms and sittings.">
        <div className="grid gap-5 p-5 sm:grid-cols-2">
          <div className="sm:col-span-2">
            <p className="text-sm font-medium text-ink">Permitted exam start times</p>
            <ul aria-label="Permitted exam start times" className="mt-2 flex flex-wrap gap-2">{form.permitted_start_times.map((time) => <li key={time} className="inline-flex items-center gap-2 rounded-lg border border-line bg-background px-3 py-1 font-mono text-sm">
              {time}<button type="button" aria-label={`Remove start time ${time}`} className="text-xs font-semibold text-danger" onClick={() => update({ permitted_start_times: form.permitted_start_times.filter((item) => item !== time) })}>Remove</button>
            </li>)}{!form.permitted_start_times.length && <li className="text-sm text-muted">No start times.</li>}</ul>
            <div className="mt-2 flex flex-wrap items-end gap-2">
              <label className="text-xs text-muted">Add a start time<input type="time" step={60} value={newStart} onChange={(event) => setNewStart(event.target.value)} className={fieldClass} /></label>
              <button type="button" className={secondaryButton} disabled={!newStart || form.permitted_start_times.includes(newStart)} onClick={addStart}>Add time</button>
            </div>
            <p className="mt-1 text-xs leading-5 text-muted">Exams can only start at these times (rule H7), and must still finish within the day&apos;s exam hours. <span className="text-secondary-ink">Default: {DEFAULT_START_TIMES.join(", ")}.</span></p>
            <p className="mt-1 text-xs text-danger">{errors.permitted_start_times}</p>
          </div>
          {field("minimum_break_minutes", "The shortest free time a student gets between two of their exams (rule H1).")}
          {field("max_rooms_per_exam", "Leave empty for no limit. A low limit can force large exams into more sittings.", { placeholder: "No limit" })}
          {field("max_sessions_per_exam", "If a module's students do not fit in the rooms at one time, it may be split into up to this many sittings. 1 = never split.")}
        </div>
      </Card>

      <Card title="Venue and seating" description="How many of each venue's seats are used for exams.">
        <div className="space-y-4 p-5 text-sm text-muted">
          <div>
            <label htmlFor="setting-seating" className="block text-sm font-medium text-ink">Default exam seating (% of normal capacity)</label>
            <input id="setting-seating" inputMode="numeric" value={form.seating_percentage} onChange={(event) => update({ seating_percentage: event.target.value })}
              aria-invalid={Boolean(errors.seating_percentage)} aria-describedby="setting-seating-help setting-seating-error" className={`${fieldClass} max-w-xs`} />
            <p id="setting-seating-help" className="mt-1 text-xs leading-5">For example, 50% lets a 100-seat hall hold 50 candidates. Used by every venue without its own value. Current saved value: <strong className="text-ink">{seating.default.percentage}%</strong>.</p>
            <p id="setting-seating-error" className="mt-1 text-xs text-danger">{errors.seating_percentage}</p>
          </div>
          <p>Currently {seating.venue_count} venues · {seating.total_exam_capacity} exam seats out of {seating.total_normal_capacity} normal seats.</p>
          <div><h3 className="font-semibold text-ink">Venues with their own percentage</h3>
            {seating.venue_overrides.length ? <ul className="mt-1 grid gap-1 sm:grid-cols-2">{seating.venue_overrides.map((item) => <li key={item.venue_id ?? "venue"}>{item.venue_id}: {item.percentage}%</li>)}</ul> : <p className="mt-1">None.</p>}
            <p className="mt-1 text-xs">These are kept unchanged when you change the default.</p></div>
          <p>Venues with no exam seats: {seating.unavailable_venues.join(", ") || "None"}</p>
        </div>
      </Card>

      <Card title="Cognitive load optimization (S1–S5)" description="Used only in Optimized mode, after every mandatory rule is met.">
        <div className="space-y-5 p-5">
          <p className="rounded-xl bg-background p-4 text-sm leading-6 text-muted">The solver first finds a timetable that meets all mandatory rules. In Optimized mode it then tries to lower a total &quot;workload penalty&quot; for all students. Each item below adds to that penalty. A higher weight makes the item matter more compared with the others; 0 ignores it.</p>
          <div className="grid gap-5 sm:grid-cols-2">{S_ITEMS.map(([name, title, help]) => <div key={name}><p className="text-sm font-semibold text-ink">{title}</p>{field(name, help)}</div>)}</div>
          <h3 className="text-sm font-semibold text-ink">Thresholds</h3>
          <div className="grid gap-5 sm:grid-cols-2">
            {field("short_gap_minutes", `A gap shorter than this counts as very short (S3).${hours(form.short_gap_minutes)}`)}
            {field("preparation_target_minutes", `The preparation time each student should get between exams (S2).${hours(form.preparation_target_minutes)}`)}
            {field("difficult_target_minutes", `The preparation time wanted between two difficult exams (S1).${hours(form.difficult_target_minutes)}`)}
            {field("high_difficulty_minimum", "Modules with this difficulty level or higher count as difficult (S1).")}
            {field("window_days", "The number of days looked at together for bunching (S4, S5).")}
            {field("preferred_window_exams", "More exams than this inside one window is penalised (S5).")}
          </div>
        </div>
      </Card>

      <Card title="Fairness protection" description="Optional limits so no single student is made much worse by optimization.">
        <div className="space-y-4 p-5 text-sm leading-6">
          <div className="grid gap-3 md:grid-cols-2">
            <p className="rounded-xl bg-background p-4 text-muted"><strong className="block text-ink">Optimization</strong>Lowers the total workload penalty of all students together. The total can improve while a few students get a worse timetable.</p>
            <p className="rounded-xl bg-background p-4 text-muted"><strong className="block text-ink">Fairness protection</strong>Compares each student with the first valid timetable found (the baseline) and refuses changes that make any one student worse by more than the limits below.</p>
          </div>
          <label className="flex items-start gap-2 font-medium text-ink"><input type="checkbox" checked={form.fairness_enabled} onChange={(event) => update({ fairness_enabled: event.target.checked })} className="mt-1 accent-primary" />
            <span>Turn on fairness protection (Optimized mode only) <span className="block text-xs font-normal text-muted">Default: off.</span></span></label>
          {fairnessOff && <p className="rounded-xl bg-secondary-soft p-4 text-secondary-ink">Fairness protection is disabled. Workload optimization may improve the total score while worsening some candidates&apos; scores; individual fairness limits are not enforced.</p>}
          <div className="grid gap-5 sm:grid-cols-2">
            {field("max_individual_increase_points", "How much any one student's workload score (0–100) may rise above the baseline.", { disabled: fairnessOff })}
            <div>
              <label className="flex items-start gap-2 font-medium text-ink"><input type="checkbox" checked={form.protect_worst_student} disabled={fairnessOff} onChange={(event) => update({ protect_worst_student: event.target.checked })} className="mt-1 accent-primary" />
                <span>Also protect the worst-off student <span className="block text-xs font-normal text-muted">The highest score among all students may not rise by more than the limit below. Default: on.</span></span></label>
              {field("max_worst_increase_points", "0 = the worst score may not get worse at all.", { disabled: fairnessOff || !form.protect_worst_student })}
            </div>
          </div>
          <p className="text-xs text-muted">Automatic loosening of these limits is not supported: if they cannot be met, the baseline timetable is kept.</p>
        </div>
      </Card>

      <Card title="Solver configuration" description="How long the timetable search may run.">
        <div className="space-y-4 p-5">
          {field("time_limit_seconds", `The longest one generation may search. Up to ${API_TIME_LIMIT_SECONDS} seconds from this console. When generating, Optimized mode uses a recommended 120-second profile for that timetable only, and Advanced Settings can change it.`)}
          <details className="rounded-xl border border-line p-4">
            <summary className="cursor-pointer text-sm font-semibold text-ink">Advanced settings</summary>
            <p className="mt-2 text-xs leading-5 text-muted">Technical values. Most institutions keep the defaults.</p>
            <div className="mt-4 grid gap-5 sm:grid-cols-2">
              {field("random_seed", "Fixes the search order so the same inputs give the same result.")}
              {field("num_search_workers", "Parallel searches. Use 1 for repeatable results.")}
              {field("baseline_budget_percent", "Share of the time limit used to find the first valid timetable in Optimized mode.")}
              {field("difficulty_weight", "How much module difficulty adds to an exam's workload (S4).")}
              {field("credits_weight", "How much module credits add to an exam's workload (S4).")}
              {field("duration_weight", "How much exam length adds to an exam's workload (S4).")}
              {field("reference_difficulty", "The difficulty level treated as a normal exam.")}
              {field("reference_credits", "The credit value treated as a normal exam.")}
              {field("reference_duration_minutes", "The exam length (with reading time) treated as a normal exam.")}
            </div>
            <dl className="mt-5 grid gap-3 rounded-xl bg-background p-4 text-xs text-muted sm:grid-cols-2">
              <div><dt className="font-semibold text-ink">Score and load scale (read-only)</dt><dd>{String(scheduling.settings.optimization?.score_scale ?? "—")} / {String(scheduling.settings.optimization?.load_scale ?? "—")}. Internal number precision.</dd></div>
              <div><dt className="font-semibold text-ink">Venue types per exam type (read-only)</dt><dd>{Object.entries((scheduling.settings.scheduling?.venue_compatibility ?? {}) as Record<string, string[]>).map(([type, venues]) => `${type}: ${venues.join(", ")}`).join(" · ") || "—"}. Used by rule H3; not edited on this page.</dd></div>
              <div><dt className="font-semibold text-ink">Module venue exceptions (read-only)</dt><dd>{Object.keys((scheduling.settings.scheduling?.module_venue_overrides ?? {}) as Record<string, unknown>).length} module(s). Not edited on this page.</dd></div>
            </dl>
          </details>
        </div>
      </Card>
    </fieldset>

    <Card title="Mandatory rules H1–H11" description="Always checked for every timetable." actions={<Badge tone="danger">Cannot be disabled</Badge>}>
      <div className="space-y-4 p-5">
        <p className="text-sm leading-6 text-muted">These rules have no on/off settings. Some use values from this page (H1 uses the minimum break, H7 the start times, H2 the seating capacity). Changing a value changes what the rule requires; the rule is still always checked. {scheduling.mandatory_rules.length} mandatory rules confirmed by the Examination Service.</p>
        <HardRuleDescriptions />
      </div>
    </Card>

    <Card title="Save changes" description="Review the impact first. Saving needs a reason, which is kept in the settings history.">
      <div className="space-y-4 p-5 text-sm" aria-busy={pending}>
        <p role="status" className="text-muted">{!dirty ? "No unsaved changes." : formErrors ? "Some values need correcting before you can save." : `Unsaved changes: ${changes.map(settingLabel).join(", ") || "none"}.`}</p>
        <label htmlFor="settings-reason" className="block font-medium text-ink">Reason for the change
          <input id="settings-reason" maxLength={500} value={reason} disabled={locked} onChange={(event) => { setReason(event.target.value); invalidate(); }} onBlur={() => setReasonTouched(true)}
            aria-invalid={reasonTouched && Boolean(reasonProblem || errors.reason)} aria-describedby="settings-reason-error" placeholder="For example: Faculty board decision, 12 Oct" className={fieldClass} />
        </label>
        <p id="settings-reason-error" className="text-xs text-danger">{reasonTouched ? reasonProblem ?? errors.reason : ""}</p>
        {validated && <div role="alert" className="space-y-2 rounded-xl bg-secondary-soft p-4 leading-6 text-secondary-ink">
          <p className="font-semibold">Check before saving — this affects every examination cycle</p>
          {validated.scheduling_fields.length > 0 && <p>Settings to change: {validated.scheduling_fields.map(settingLabel).join(", ")}.</p>}
          {validated.seating && <p>Default seating: {validated.seating.old_percentage}% → {validated.seating.new_percentage}% · {validated.seating.affected_venues} venue(s) affected{validated.seating.newly_unavailable_venues.length ? ` · would have no exam seats: ${validated.seating.newly_unavailable_venues.join(", ")}` : ""}.</p>}
          <p>{validated.drafts === null || validated.approved === null ? "Saved timetable counts are unavailable." : `${validated.drafts} draft and ${validated.approved} approved timetable(s) exist across all cycles.`} Legacy timetables that depend on active settings may become stale. Stored timetable preferences keep their scheduling values. Source seating changes can still make timetables stale. Published timetables are not changed.</p>
          {validated.replace_invalid && <p>This replaces the invalid saved settings.</p>}
          <label className="flex items-start gap-2 font-medium"><input type="checkbox" checked={confirmed} disabled={locked} onChange={(event) => setConfirmed(event.target.checked)} className="mt-1" />I understand these settings apply to all examination cycles.</label>
        </div>}
        {result && !result.ok && <p role="alert" className="rounded-lg bg-danger-soft p-3 text-danger">{result.error.status ? `HTTP ${result.error.status}: ` : ""}{result.error.message}</p>}
        {transportError && <p role="alert" className="text-danger">{transportError}</p>}
        {notice && <p role="status" className={`rounded-lg p-3 ${notice.tone === "success" ? "bg-accent text-accent-ink" : "bg-secondary-soft text-secondary-ink"}`}>{notice.text}</p>}
        <div className="flex flex-wrap gap-2">
          <button type="button" disabled={locked || (!dirty && !reason)} className={secondaryButton} onClick={reset}>Reset changes</button>
          {!validated ? <button type="button" disabled={locked || !dirty || formErrors} className={primaryButton} onClick={() => request(false)}>{pending && operation === "preview" ? "Checking…" : "Review changes"}</button>
            : <>
              <button type="button" disabled={locked} className={secondaryButton} onClick={invalidate}>Cancel</button>
              <button type="button" disabled={locked || !confirmed} className={primaryButton} onClick={() => request(true)}>{pending && operation === "save" ? "Saving…" : "Save changes"}</button>
            </>}
        </div>
      </div>
    </Card>
  </div>;
}
