"use client";

import { useActionState, useState } from "react";
import { Icon } from "@/components/icon";
import { reviewSchedulingRules, type RulesState } from "@/lib/teaching-generation-actions";
import { DAY_PARTS, PREFERENCE_WEIGHTS, minuteLabel, rulesForm, rulesFromForm, serializeRules, wizardHref,
  type RulesForm, type SchedulingRules, type SchedulingRulesReport } from "@/lib/teaching-scheduling-rules";

const CONTROL = "mt-1 h-10 w-full rounded-xl border border-line bg-surface px-3 text-sm text-ink disabled:opacity-50";
const SECONDARY = "inline-flex h-10 items-center justify-center gap-2 rounded-xl border border-line px-4 text-sm font-semibold text-ink hover:bg-background disabled:opacity-50";
const PRIMARY = "inline-flex h-10 items-center justify-center gap-2 rounded-xl bg-primary px-4 text-sm font-semibold text-white hover:bg-primary-strong disabled:cursor-not-allowed disabled:opacity-50";
const INITIAL: RulesState = { status: "idle" };
const WEIGHT_LABELS = ["Preferred day part", "Not preferred day part", "Preferred free day", "Weekend work pattern"];

export function SchedulingRulesForm({ defaults, selected, report, scope, validated = false, initialError }: {
  defaults: SchedulingRules; selected: SchedulingRules; report: SchedulingRulesReport;
  scope: { semester: 1 | 2; year: number | null; stream: string | null };
  /** The Teaching Service validated exactly `selected` while rendering this step. */
  validated?: boolean; initialError?: string;
}) {
  const [form, setForm] = useState(() => rulesForm(selected, defaults));
  const [state, action, pending] = useActionState(reviewSchedulingRules, INITIAL);
  const [edited, setEdited] = useState(false);
  const parsed = rulesFromForm(form);
  const rulesKey = parsed.ok ? serializeRules(parsed.rules) : "";
  const currentResult = !state.rulesKey || state.rulesKey === rulesKey;
  function update(next: RulesForm) {
    setForm(next);
    setEdited(true);
    const result = rulesFromForm(next);
    if (result.ok) window.history.replaceState(null, "", wizardHref("rules", scope, serializeRules(result.rules)));
  }
  const patch = (change: Partial<RulesForm>) => update({ ...form, ...change });
  const checkbox = (key: keyof RulesForm, label: string, detail?: string) => (
    <label className="flex items-start gap-3 rounded-xl border border-line p-3.5">
      <input type="checkbox" checked={form[key] === true} onChange={(event) => patch({ [key]: event.target.checked })}
        className="mt-0.5 size-4 shrink-0 accent-primary" />
      <span><span className="block text-sm font-semibold text-ink">{label}</span>{detail && <span className="mt-0.5 block text-[13px] leading-5 text-muted">{detail}</span>}</span>
    </label>
  );
  const number = (key: keyof RulesForm, label: string, min: number, max: number, step = 1) => (
    <label className="block text-[13px] font-semibold text-muted">{label}
      <input type="number" min={min} max={max} step={step} value={String(form[key])} onChange={(event) => patch({ [key]: event.target.value })}
        className={CONTROL} />
    </label>
  );
  return (
    <form action={action}>
      <input type="hidden" name="semester" value={scope.semester} />
      <input type="hidden" name="year" value={scope.year ?? ""} />
      <input type="hidden" name="stream" value={scope.stream ?? ""} />
      <input type="hidden" name="rules" value={rulesKey} />
      <div className="space-y-5 px-5 py-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div><h3 className="text-sm font-semibold text-ink">Choose what to optimise</h3>
            <p className="mt-1 max-w-2xl text-[13px] leading-5 text-muted">Defaults come from the Teaching Service and are experimental. These preferences guide scheduling; they do not relax clashes, availability or approved workload limits.</p></div>
          <button type="button" disabled={pending} onClick={() => update(rulesForm(defaults, defaults))} className={SECONDARY}>
            <Icon name="replay" size={18} />Reset to defaults
          </button>
        </div>
        <fieldset disabled={pending} className="space-y-5">
          <legend className="sr-only">Supported scheduling rules</legend>
          <div className="grid gap-3 sm:grid-cols-3">
            {checkbox("workload_fairness", "Balance staff workload", "Compare each staff member with their own approved teaching target.")}
            {checkbox("balance_student_daily_load", "Balance student days", "Reduce the highest daily teaching load across student groups.")}
            {checkbox("balance_staff_daily_load", "Balance staff days", "Reduce the highest daily teaching load across staff.")}
          </div>
          <section className="space-y-3">
            {checkbox("consecutive_enabled", "Consecutive teaching and idle gaps", "Prefer shorter continuous runs or reduce gaps between teaching sessions.")}
            <fieldset disabled={!form.consecutive_enabled} className="space-y-3 rounded-xl bg-background p-4 disabled:opacity-50">
              <legend className="sr-only">Consecutive teaching settings</legend>
              <div className="grid gap-3 sm:grid-cols-2">
                {number("maximum_preferred_continuous_minutes", "Preferred maximum continuous teaching (minutes)", 60, 600, 30)}
                {number("penalty_weight", "Penalty weight (1–10)", 1, 10)}
              </div>
              <p className="text-[13px] text-muted">A soft preference, not a hard cap. Leave the maximum blank to score only idle gaps.</p>
              <div className="grid gap-3 sm:grid-cols-3">
                {checkbox("minimize_idle_gaps", "Reduce idle gaps")}
                {checkbox("apply_to_lecturers", "Apply to staff")}
                {checkbox("apply_to_student_groups", "Apply to student groups")}
              </div>
            </fieldset>
          </section>
          <section className="space-y-3">
            {checkbox("preferences_enabled", "Score stored lecturer preferences", "Use the existing stored records. Not Available periods remain hard blocks, even when soft scoring is off.")}
            <details className="rounded-xl border border-line p-4">
              <summary className="cursor-pointer text-sm font-semibold text-ink">Day-part boundaries and scoring weights</summary>
              <fieldset disabled={!form.preferences_enabled} className="mt-4 space-y-4 disabled:opacity-50">
                <legend className="sr-only">Lecturer preference scoring settings</legend>
                <div className="grid gap-3 sm:grid-cols-3">
                  {DAY_PARTS.map((key, index) => <label key={key} className="text-[13px] font-semibold text-muted">
                    {["Morning starts", "Afternoon starts / morning ends", "Afternoon ends"][index]}
                    <select value={form[key]} onChange={(event) => patch({ [key]: event.target.value })} className={CONTROL}>
                      {Array.from({ length: 49 }, (_, n) => n * 30).map((minute) => <option key={minute} value={minute}>{minuteLabel(minute)}</option>)}
                    </select>
                  </label>)}
                </div>
                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                  {PREFERENCE_WEIGHTS.map((key, index) => <div key={key}>{number(key, `${WEIGHT_LABELS[index]} (0–10)`, 0, 10)}</div>)}
                </div>
                <p className="text-[13px] leading-5 text-muted">Zero disables a scoring cost. Existing hard unavailability is preserved; supplied boundaries may add blocks. Scoring can be inactive when no applicable stored preferences exist.</p>
              </fieldset>
            </details>
          </section>
        </fieldset>
        <section className="rounded-xl border border-line p-4">
          <h3 className="text-sm font-semibold text-ink">Protected rules</h3>
          <p className="mt-1 text-[13px] leading-5 text-muted">Clash prevention, staff eligibility, venue suitability and capacity, availability and approved workload targets stay fixed.</p>
          <p className="mt-2 text-[13px] text-muted">{report.protected.lunch_enforced ? `Student lunch periods: ${report.protected.lunch_periods.map((row) => `Year ${row.academic_year} ${minuteLabel(row.start_minute)}–${minuteLabel(row.end_minute)}`).join(" · ")}.` : "No enforced student lunch policy is recorded in this snapshot."}</p>
          <fieldset disabled className="mt-3 grid gap-3 sm:grid-cols-2">
            <legend className="sr-only">Unsupported settings</legend>
            <label className="flex gap-2 text-[13px] text-muted"><input type="checkbox" disabled />Custom objective order — not supported in this wizard</label>
            <label className="flex gap-2 text-[13px] text-muted"><input type="checkbox" disabled />Automatic rule relaxation — not supported</label>
          </fieldset>
          <p className="mt-3 text-[13px] text-muted">Teaching windows, online-day policy, hard breaks and workload caps cannot be edited here.</p>
        </section>
        {!edited && validated && (
          <p role="status" className="flex items-center gap-2 rounded-xl bg-accent p-3 text-sm font-semibold text-accent-ink">
            <Icon name="check_circle" size={18} filled className="shrink-0" />
            Rules validated for this scope by the Teaching Service. Nothing has been solved or stored.
          </p>
        )}
        {!edited && initialError && <p role="alert" className="rounded-xl bg-danger-soft p-3 text-sm text-danger">{initialError}</p>}
        {!parsed.ok && <p role="alert" className="rounded-xl bg-danger-soft p-3 text-sm text-danger">{parsed.error}</p>}
        {currentResult && state.message && <p role={state.status === "error" ? "alert" : "status"} className={`rounded-xl p-3 text-sm ${state.status === "error" ? "bg-danger-soft text-danger" : "bg-background text-ink"}`}>{state.message}</p>}
        {pending && <p role="status" className="text-[13px] text-muted">Validating rules against this institution&apos;s inputs. Nothing is being solved.</p>}
      </div>
      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line px-5 py-4">
        <a href={wizardHref("inputs", scope, rulesKey || serializeRules(selected))} className={SECONDARY}><Icon name="arrow_back" size={18} />Back</a>
        <div className="flex flex-wrap gap-2">
          <button type="submit" name="intent" value="validate" disabled={pending || !parsed.ok} className={SECONDARY}>Validate rules</button>
          <button type="submit" name="intent" value="continue" disabled={pending || !parsed.ok} className={PRIMARY}>Next<Icon name="arrow_forward" size={18} /></button>
        </div>
      </div>
    </form>
  );
}
