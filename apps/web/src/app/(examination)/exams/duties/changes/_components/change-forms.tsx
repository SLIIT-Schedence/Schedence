"use client";

import { type SyntheticEvent, startTransition, useActionState, useState } from "react";
import { useToast } from "@/components/console/shell";
import { Icon } from "@/components/icon";
import { previewChange, requestChange, type PreviewState } from "@/lib/duty-allocation-actions";
import { clock, fixed, type ChangePreview, type FairnessMetrics, type RosterAssignment } from "@/lib/duty-allocation-types";

export type DutyOption = { value: string; label: string };
export type StaffOption = { value: string; label: string };

const IDLE: PreviewState = { status: "idle" };
const FIELD = "block w-full rounded-xl border border-line bg-surface px-3.5 text-sm text-ink transition-colors hover:border-secondary/70 focus:border-primary";

/**
 * Swap two duties, or find an emergency replacement for one. Preview asks the solver and changes nothing; only a
 * PROPOSED preview that still matches the form can be submitted as a request for approval.
 */
export function ChangeForm({
  kind, rosterId, rowVersion, duties, staff, names,
}: {
  kind: "swap" | "replacement";
  rosterId: string;
  rowVersion: number;
  duties: DutyOption[];
  staff: StaffOption[];
  names: Record<string, string>;
}) {
  const toast = useToast();
  // The form is submitted from onSubmit, not through the action prop: React resets a form after an action prop runs,
  // which would clear the duties that the preview below describes.
  const [values, setValues] = useState<Record<string, string>>({});
  const [changed, setChanged] = useState(false);
  const set = (name: string) => (event: { target: { value: string } }) => {
    setValues((current) => ({ ...current, [name]: event.target.value }));
    setChanged(true);
  };
  const [previewState, previewAction, previewing] = useActionState(async (previous: PreviewState, formData: FormData) => {
    const result = await previewChange(rosterId, kind, previous, formData);
    setChanged(false);
    return result;
  }, IDLE);
  const [requestState, requestAction, requesting] = useActionState(async (previous: PreviewState, formData: FormData) => {
    const result = await requestChange(rosterId, rowVersion, kind, previous, formData);
    if (result.status === "success" && result.message) toast(result.message);
    return result;
  }, IDLE);

  const preview = changed ? undefined : previewState.preview;
  const error = requestState.status === "error" ? requestState.message : previewState.status === "error" ? previewState.message : undefined;
  const busy = previewing || requesting;
  const submitTo = (dispatch: (formData: FormData) => void) => (event: SyntheticEvent<HTMLElement>) => {
    event.preventDefault();
    const form = event.currentTarget.closest("form");
    if (!form?.reportValidity()) return;
    const formData = new FormData(form);
    startTransition(() => dispatch(formData));
  };
  const select = (name: string, label: string, options: DutyOption[], required = true, empty?: string) => (
    <label className="block text-sm font-semibold text-ink">
      {label}
      <select name={name} required={required} value={values[name] ?? ""} onChange={set(name)} className={`${FIELD} mt-1.5 h-11`}>
        <option value="" disabled={required}>{empty ?? "Choose a duty"}</option>
        {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    </label>
  );

  return (
    <form onSubmit={submitTo(previewAction)} className="space-y-4 p-5" aria-busy={busy}>
      {kind === "swap" ? (
        <div className="grid gap-4 lg:grid-cols-2">
          {select("first", "First duty", duties)}
          {select("second", "Second duty", duties)}
        </div>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          {select("duty", "Duty that needs a replacement", duties)}
          {select("preferred_staff_id", "Preferred replacement", staff, false, "Let the solver choose")}
        </div>
      )}
      <label className="block text-sm font-semibold text-ink">
        Reason
        <textarea name="reason" required maxLength={500} rows={2} value={values.reason ?? ""} onChange={set("reason")} className={`${FIELD} mt-1.5 resize-y py-2.5 leading-6`}
          placeholder={kind === "swap" ? "For example: both staff members asked to swap" : "For example: illness on the exam day"} />
      </label>

      <div className="flex flex-wrap gap-2">
        <button type="submit" disabled={busy} className="inline-flex min-h-10 items-center gap-2 rounded-xl border border-line bg-surface px-4 text-sm font-semibold text-ink transition-colors hover:bg-primary-soft disabled:cursor-progress disabled:opacity-60">
          {previewing ? <span aria-hidden="true" className="size-4 animate-spin rounded-full border-2 border-current border-r-transparent" /> : <Icon name="visibility" size={18} />}
          {previewing ? "Checking…" : "Preview"}
        </button>
        <button type="button" onClick={submitTo(requestAction)} disabled={busy || preview?.status !== "PROPOSED"}
          title={preview?.status === "PROPOSED" ? undefined : "Preview a feasible change first."}
          className="inline-flex min-h-10 items-center gap-2 rounded-xl bg-primary px-4 text-sm font-semibold text-white transition-colors hover:bg-primary-strong disabled:cursor-not-allowed disabled:opacity-50">
          {requesting ? <span aria-hidden="true" className="size-4 animate-spin rounded-full border-2 border-current border-r-transparent" /> : <Icon name="forward_to_inbox" size={18} />}
          Submit for approval
        </button>
      </div>
      {changed && previewState.preview && <p className="text-[13px] text-muted">The form changed since the last preview. Preview again before submitting.</p>}
      {error && (
        <p role="alert" className="flex gap-2.5 rounded-xl bg-danger-soft px-3.5 py-3 text-sm leading-5 text-danger">
          <Icon name="error" size={18} filled className="mt-px shrink-0" />{error}
        </p>
      )}
      {preview && <PreviewResult preview={preview} names={names} />}
    </form>
  );
}

function PreviewResult({ preview: p, names }: { preview: ChangePreview; names: Record<string, string> }) {
  const proposed = p.status === "PROPOSED";
  const duty = (a: RosterAssignment) =>
    `${a.starts_at.slice(0, 10)} ${clock(a.starts_at)} · ${a.module_code ?? a.session_id} · ${a.venue_id} · ${a.role === "SUPERVISOR" ? "Supervisor" : "Invigilator"} · ${names[a.staff_id] ?? a.staff_number ?? a.staff_id.slice(0, 8)}`;
  const metric = (label: string, key: keyof FairnessMetrics, digits = 3) => {
    const before = p.original_metrics[key];
    const after = p.proposed_metrics[key];
    return (
      <tr key={key}>
        <th scope="row" className="px-4 py-2 font-normal text-ink">{label}</th>
        <td className="px-4 py-2 text-right">{typeof before === "number" ? fixed(before, digits) : "—"}</td>
        <td className="px-4 py-2 text-right">{typeof after === "number" ? fixed(after, digits) : "—"}</td>
      </tr>
    );
  };
  return (
    <div className="space-y-4 rounded-2xl border border-line bg-background/60 p-4" aria-live="polite">
      <p className={`flex items-center gap-2 text-sm font-semibold ${proposed ? "text-accent-ink" : "text-danger"}`}>
        <Icon name={proposed ? "check_circle" : "error"} size={18} filled />
        {proposed ? "This change meets every rule. Nothing has been changed yet." : p.status === "INFEASIBLE" ? "No change meets every rule." : "The request was not accepted."}
      </p>
      {p.reasons.length > 0 && <ul className="list-disc space-y-1 pl-5 text-[13px] text-muted">{p.reasons.map((r) => <li key={r}>{r}</li>)}</ul>}
      {[...p.violations, ...(p.independent_check_violations ?? [])].length > 0 && (
        <ul className="list-disc space-y-1 pl-5 text-[13px] text-danger">
          {[...p.violations, ...(p.independent_check_violations ?? [])].slice(0, 8).map((v, i) => <li key={i}><span className="font-mono text-xs">{v.constraint}</span> {v.message}</li>)}
        </ul>
      )}
      {proposed && (
        <div className="grid gap-4 lg:grid-cols-2">
          <div>
            <p className="text-xs font-semibold text-muted">Removed</p>
            <ul className="mt-1 space-y-1 text-[13px] text-ink">{p.removed.map((a) => <li key={`${a.session_id}${a.venue_id}${a.staff_id}`} className="line-through decoration-danger/60">{duty(a)}</li>)}</ul>
            <p className="mt-3 text-xs font-semibold text-muted">Added</p>
            <ul className="mt-1 space-y-1 text-[13px] text-ink">{p.added.map((a) => <li key={`${a.session_id}${a.venue_id}${a.staff_id}`}>{duty(a)}</li>)}</ul>
          </div>
          <table className="w-full text-left text-[13px] tabular-nums">
            <caption className="sr-only">Fairness before and after the change</caption>
            <thead className="text-xs text-muted"><tr><th scope="col" className="px-4 py-2 font-semibold">Fairness</th><th scope="col" className="px-4 py-2 text-right font-semibold">Now</th><th scope="col" className="px-4 py-2 text-right font-semibold">After</th></tr></thead>
            <tbody className="divide-y divide-line">
              {metric("Gini coefficient", "gini")}
              {metric("Workload spread", "std_load", 1)}
              {metric("Back-to-back pairs", "back_to_back_pairs", 0)}
            </tbody>
          </table>
        </div>
      )}
      {p.candidates.length > 0 && (
        <div>
          <p className="text-xs font-semibold text-muted">Replacement candidates, best first</p>
          <ul className="mt-1 space-y-1 text-[13px] text-ink">
            {p.candidates.slice(0, 5).map((c) => <li key={c.staff_id}>{c.staff_name ?? c.staff_number} · {c.duties_after} duties after the change</li>)}
          </ul>
        </div>
      )}
      {p.rejected.length > 0 && (
        <details className="text-[13px]">
          <summary className="cursor-pointer font-semibold text-primary-strong">Staff who could not take it ({p.rejected.length})</summary>
          <ul className="mt-2 space-y-1 text-muted">{p.rejected.slice(0, 20).map((r) => <li key={r.staff_id}>{names[r.staff_id] ?? r.staff_number}: {r.reasons.join("; ")}</li>)}</ul>
        </details>
      )}
      <p className="text-xs text-muted">{p.notice}</p>
    </div>
  );
}
