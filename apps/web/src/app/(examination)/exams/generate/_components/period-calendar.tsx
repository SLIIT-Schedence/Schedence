"use client";

import { useRef, useState, useTransition } from "react";
import { Card, formatDate } from "@/components/console/ui";
import { previewExamPeriod, saveExamPeriod } from "@/lib/examination-workspace-actions";
import type { PeriodChangeResult, PeriodDefinition, PeriodEdit } from "@/lib/examination-workspace-types";
import { calendarMonths, monthCells } from "@/lib/examination-presentation";
import { exclusionDateError, isCalendarDate, periodEditErrors, periodEditKey, proposedCalendar, sameProposedCalendar } from "@/lib/examination-calendar";

export const fieldClass = "mt-1 block min-h-10 w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink aria-invalid:border-danger";
export const secondaryButton = "inline-flex min-h-10 items-center justify-center rounded-xl border border-line bg-surface px-4 text-sm font-semibold text-ink hover:bg-background disabled:cursor-not-allowed disabled:opacity-50";
export const primaryButton = "inline-flex min-h-10 items-center justify-center rounded-xl bg-primary px-4 text-sm font-semibold text-white hover:bg-primary-strong disabled:cursor-not-allowed disabled:opacity-50";

export function CalendarGrid({ period, onDate, proposed = false }: { period: PeriodDefinition; onDate?: (day: string) => void; proposed?: boolean }) {
  const allowed = new Set(period.exam_days);
  const exclusions = new Map(period.exclusions.map((item) => [item.excluded_date, item.reason]));
  return (
    <div className="space-y-4">
      <p className="text-xs leading-5 text-muted">Blue: allowed exam day · Red: excluded date · Grey: weekend or other non-exam day. {proposed ? "Local proposed preview; it does not change saved dates." : "Saved dates returned by the backend."} {onDate && "Select an enabled date to add or remove a proposed exclusion."}</p>
      <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">
        {calendarMonths(period.start_date, period.end_date).map((month) => (
          <section key={month} aria-label={`${proposed ? "Proposed" : "Saved"} examination calendar ${month}`} className="rounded-xl border border-line p-3">
            <h3 className="mb-3 text-center text-sm font-semibold text-ink">{new Intl.DateTimeFormat("en-GB", { month: "long", year: "numeric", timeZone: "UTC" }).format(new Date(`${month}-01T00:00:00Z`))}</h3>
            <div className="grid grid-cols-7 gap-1 text-center text-xs">
              {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((day) => <span key={day} className="py-1 font-medium text-muted">{day}</span>)}
              {monthCells(month).map((day, index) => {
                if (!day) return <span key={`blank-${index}`} />;
                const inside = day >= period.start_date && day <= period.end_date;
                const excluded = exclusions.has(day);
                const weekend = [0, 6].includes(new Date(`${day}T00:00:00Z`).getUTCDay());
                const status = excluded ? `Excluded: ${exclusions.get(day) || "reason required"}` : allowed.has(day) ? "Allowed exam day" : weekend ? "Weekend not allowed" : "Not an exam day";
                return <button key={day} type="button" disabled={!onDate || !inside || (!allowed.has(day) && !excluded)} onClick={() => onDate?.(day)}
                  aria-label={`${formatDate(day)}: ${inside ? status : "Outside period"}`} title={inside ? status : "Outside period"}
                  className={`min-h-9 rounded-lg border text-xs disabled:cursor-default ${!inside ? "border-transparent text-secondary/45" : excluded ? "border-danger/20 bg-danger-soft font-semibold text-danger" : allowed.has(day) ? "border-primary/15 bg-primary-soft font-semibold text-primary-strong" : "border-line bg-secondary-soft text-secondary-ink"}`}>
                  {Number(day.slice(-2))}{inside && weekend && <span className="sr-only"> weekend</span>}
                </button>;
              })}
            </div>
          </section>
        ))}
      </div>
    </div>
  );
}

function periodEdit(period: PeriodDefinition): PeriodEdit {
  return { expected_fingerprint: period.fingerprint ?? "", start_date: period.start_date, end_date: period.end_date,
    include_saturdays: period.include_saturdays, include_sundays: period.include_sundays, exclusions: period.exclusions.map((item) => ({ ...item })) };
}

function FieldError({ id, message }: { id: string; message?: string }) {
  return <p id={id} className="mt-1 text-xs text-danger">{message}</p>;
}

export function PeriodCalendar({ period, locked, drafts, onEditing, onSaved }: {
  period: PeriodDefinition; locked: boolean | null; drafts: number | null;
  onEditing: (value: boolean) => void; onSaved: (period: PeriodDefinition, notice: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [edit, setEdit] = useState(() => periodEdit(period));
  const [preview, setPreview] = useState<{ key: string; response: PeriodChangeResult } | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [pending, startTransition] = useTransition();
  const [transportError, setTransportError] = useState("");
  const [selectedDate, setSelectedDate] = useState("");
  const [operation, setOperation] = useState<"preview" | "save">("preview");
  const editRevision = useRef(0);
  const invalidate = () => { editRevision.current++; setPreview(null); setConfirmed(false); setTransportError(""); };
  const update = (patch: Partial<PeriodEdit>) => { setEdit((previous) => ({ ...previous, ...patch })); invalidate(); };
  const editKey = periodEditKey(edit);
  const response = preview?.key === editKey ? preview.response : null;
  const errors = { ...(response && !response.ok ? response.error.fieldErrors : {}), ...periodEditErrors(edit, period.academic_year_label) };
  const candidateError = selectedDate ? exclusionDateError(selectedDate, edit, edit.exclusions.map((item) => item.excluded_date)) : undefined;
  const hasErrors = Object.keys(errors).length > 0 || Boolean(selectedDate);
  const proposed = proposedCalendar(period, edit);
  const validated = response?.ok && response.data.change.dry_run && response.data.change.period.period_code === period.period_code
    && sameProposedCalendar(edit, response.data.change.period) ? response.data : null;
  const canSave = !pending && !hasErrors && locked !== true && Boolean(validated?.change.changed && validated.preview_token && confirmed);
  const saveHelp = pending ? operation === "save" ? "Saving the confirmed period changes…" : "Validating the proposed values with the backend…"
    : hasErrors ? "Resolve the highlighted errors and add or clear any pending excluded date before validation."
      : !validated ? "Validate the current proposed values, then confirm the successful dry-run to enable saving."
        : !validated.change.changed ? "No changes from the Saved Calendar. There is nothing to save."
          : !confirmed ? "Dry-run succeeded. Tick the confirmation below before saving."
            : "The current proposal is validated and confirmed. Saving requires the button below.";

  const request = (save: boolean) => {
    if (hasErrors || locked === true || pending || (save && !canSave)) return;
    const submittedEdit = edit;
    const key = editKey;
    const token = validated?.preview_token ?? "";
    const consent = confirmed;
    setOperation(save ? "save" : "preview");
    invalidate();
    const submittedRevision = editRevision.current;
    startTransition(async () => {
      try {
        const result = save ? await saveExamPeriod(period.period_code, submittedEdit, token, consent) : await previewExamPeriod(period.period_code, submittedEdit);
        if (!save && editRevision.current !== submittedRevision) return;
        setPreview({ key, response: result });
        setConfirmed(false);
        if (save && result.ok) {
          setEditing(false); onEditing(false);
          onSaved(result.data.change.period, `Period saved. ${result.data.change.notices.join(" ")} Previous temporary results have been cleared; generate again using the saved calendar.`);
        }
      } catch { setTransportError("The request was interrupted. Reload the cycle to verify its stored dates before trying again."); }
    });
  };

  return (
    <Card title="Examination calendar" description="Generation uses the Saved Calendar. Proposed changes take effect only after an explicit, successful save.">
      <div className="space-y-5 p-5">
        <section aria-label="Saved Calendar" className="space-y-4">
          <div className="flex flex-wrap justify-between gap-3">
            <div><h3 className="text-sm font-semibold text-ink">Saved Calendar</h3><p className="mt-1 font-semibold text-ink">{formatDate(period.start_date)} – {formatDate(period.end_date)}</p>
              <p className="mt-1 text-sm text-muted">{period.exam_days.length} allowed days · {period.day_start_time.slice(0, 5)}–{period.day_end_time.slice(0, 5)} · Semester {period.semester}</p></div>
            {!editing && <button type="button" disabled={locked === true || !period.fingerprint} className={secondaryButton} onClick={() => { setEdit(periodEdit(period)); invalidate(); setSelectedDate(""); setEditing(true); onEditing(true); }}>Edit period dates</button>}
          </div>
          {editing ? <details><summary className="cursor-pointer text-xs font-semibold text-muted">View saved calendar for comparison</summary><div className="mt-3"><CalendarGrid period={period} /></div></details> : <CalendarGrid period={period} />}
          <div className="text-sm text-muted"><h4 className="font-semibold text-ink">Saved exclusions</h4>
            {period.exclusions.length ? <ul className="mt-2 space-y-1">{period.exclusions.map((item) => <li key={item.excluded_date}>{formatDate(item.excluded_date)} · {item.reason}</li>)}</ul> : <p className="mt-1">No explicitly excluded dates.</p>}
          </div>
        </section>
        <p className={`rounded-xl p-3 text-sm leading-6 ${locked ? "bg-danger-soft text-danger" : "bg-secondary-soft text-secondary-ink"}`}>
          {locked ? "This cycle has an approved, published or archived timetable. Date changes are locked." : "The backend refuses date changes for periods with any approved, published or archived timetable, including in another institution."}
          {drafts === null ? " Draft count unavailable." : ` ${drafts} saved draft(s) in this institution.`} Changing period definitions makes existing drafts stale; they must be regenerated before approval.
        </p>
        {editing && (
          <section aria-label="Proposed Calendar" className="space-y-5 rounded-xl border border-primary/30 p-4" aria-busy={pending}>
            <div><h3 className="text-sm font-semibold text-primary-strong">Proposed Calendar — not saved</h3><p className="mt-1 text-xs leading-5 text-muted">The local preview updates immediately. Backend validation is a separate step. Save or cancel editing before generating.</p></div>
            <fieldset disabled={pending} className="grid gap-4 sm:grid-cols-2">
              <legend className="sr-only">Proposed examination dates</legend>
              <label className="text-sm font-medium">Start date<input type="date" value={edit.start_date} onChange={(event) => update({ start_date: event.target.value })} aria-invalid={Boolean(errors.start_date)} aria-describedby="period-start-error" className={fieldClass} /><FieldError id="period-start-error" message={errors.start_date} /></label>
              <label className="text-sm font-medium">End date<input type="date" value={edit.end_date} onChange={(event) => update({ end_date: event.target.value })} aria-invalid={Boolean(errors.end_date)} aria-describedby="period-end-error" className={fieldClass} /><FieldError id="period-end-error" message={errors.end_date} /></label>
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={edit.include_saturdays} onChange={(event) => update({ include_saturdays: event.target.checked })} />Allow Saturdays</label>
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={edit.include_sundays} onChange={(event) => update({ include_sundays: event.target.checked })} />Allow Sundays</label>
              <div className="sm:col-span-2">
                <p className="text-sm font-medium">Proposed excluded dates and reasons</p>
                <p className="mt-1 text-xs text-muted">Use a single-line reason such as “Poya day” or “University holiday”. Surrounding spaces are removed before validation; spaces between words are kept.</p>
                {edit.exclusions.map((item, index) => <div key={index} className="mt-2 flex flex-wrap items-start gap-2">
                  <label className="text-xs text-muted">Excluded date<input type="date" min={isCalendarDate(edit.start_date) ? edit.start_date : undefined} max={isCalendarDate(edit.end_date) ? edit.end_date : undefined} value={item.excluded_date}
                    onChange={(event) => update({ exclusions: edit.exclusions.map((row, i) => i === index ? { ...row, excluded_date: event.target.value } : row) })}
                    aria-invalid={Boolean(errors[`exclusions.${index}.excluded_date`])} aria-describedby={`exclusion-${index}-date-error`} className={fieldClass} /><FieldError id={`exclusion-${index}-date-error`} message={errors[`exclusions.${index}.excluded_date`]} /></label>
                  <label className="min-w-40 flex-1 text-xs text-muted">Reason (3–120 characters)<input maxLength={120} value={item.reason}
                    onChange={(event) => update({ exclusions: edit.exclusions.map((row, i) => i === index ? { ...row, reason: event.target.value } : row) })}
                    aria-invalid={Boolean(errors[`exclusions.${index}.reason`])} aria-describedby={`exclusion-${index}-reason-error`} className={fieldClass} /><FieldError id={`exclusion-${index}-reason-error`} message={errors[`exclusions.${index}.reason`]} /></label>
                  <button type="button" className={`${secondaryButton} mt-5`} onClick={() => update({ exclusions: edit.exclusions.filter((_, i) => i !== index) })}>Remove</button>
                </div>)}
                <div className="mt-3 flex flex-wrap items-start gap-2">
                  <label className="text-xs text-muted">Add excluded date<input type="date" value={selectedDate} min={isCalendarDate(edit.start_date) ? edit.start_date : undefined} max={isCalendarDate(edit.end_date) ? edit.end_date : undefined}
                    onChange={(event) => { setSelectedDate(event.target.value); invalidate(); }} aria-invalid={Boolean(candidateError)} aria-describedby="new-exclusion-error" className={fieldClass} /><FieldError id="new-exclusion-error" message={candidateError ?? (selectedDate ? "Click Add exclusion to include this date, or clear the field." : undefined)} /></label>
                  <button type="button" disabled={!selectedDate || Boolean(candidateError) || edit.exclusions.length >= 120} className={`${secondaryButton} mt-5`} onClick={() => { update({ exclusions: [...edit.exclusions, { excluded_date: selectedDate, reason: "" }] }); setSelectedDate(""); }}>Add exclusion</button>
                </div>
              </div>
            </fieldset>
            {errors.calendar && <p role="alert" className="text-sm text-danger">{errors.calendar}</p>}
            {proposed ? <div className="space-y-3"><p className="text-sm font-medium text-primary-strong">{formatDate(proposed.start_date)} – {formatDate(proposed.end_date)} · {proposed.exam_days.length} proposed allowed days</p>
              <CalendarGrid period={proposed} proposed onDate={pending ? undefined : (day) => update({ exclusions: edit.exclusions.some((row) => row.excluded_date === day) ? edit.exclusions.filter((row) => row.excluded_date !== day) : [...edit.exclusions, { excluded_date: day, reason: "" }] })} />
            </div> : <p className="rounded-xl bg-danger-soft p-3 text-sm text-danger">Set a valid proposed date range of at most 120 days to display its calendar.</p>}
            {validated && <div className="space-y-3 rounded-xl bg-accent p-4 text-sm text-accent-ink">
              <p className="font-semibold">Backend dry-run validated these exact proposed values — not saved</p>
              <p>Changed fields: {validated.change.changed_fields.join(", ") || "None"}. Backend allowed exam days: {validated.change.period.exam_days.length}.</p>
              <p>{validated.change.draft_timetables} draft timetable(s) may become stale. The preview expires after ten minutes.</p>
              {validated.change.notices.map((notice) => <p key={notice}>{notice}</p>)}
              {validated.change.changed && <label className="flex items-start gap-2"><input type="checkbox" disabled={pending} checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} className="mt-1" />I confirm saving these period changes and understand the impact on existing drafts.</label>}
            </div>}
            {response?.ok && !validated && <p role="alert" className="text-sm text-danger">The returned dry-run does not match the current proposed values. Validate the proposal again.</p>}
            {response && !response.ok && <p role="alert" className="rounded-lg bg-danger-soft p-3 text-sm text-danger">HTTP {response.error.status}: {response.error.message}</p>}
            {transportError && <p role="alert" className="text-sm text-danger">{transportError}</p>}
            <p id="period-save-help" role="status" className="text-xs leading-5 text-muted">{saveHelp}</p>
            <div className="flex flex-wrap gap-2">
              <button type="button" disabled={pending} className={secondaryButton} onClick={() => { setEditing(false); invalidate(); setSelectedDate(""); onEditing(false); }}>Cancel editing</button>
              <button type="button" disabled={pending || hasErrors || locked === true} className={secondaryButton} onClick={() => request(false)}>{pending && operation === "preview" ? "Validating…" : "Validate dry-run preview"}</button>
              <button type="button" disabled={!canSave} aria-describedby="period-save-help" className={primaryButton} onClick={() => request(true)}>{pending && operation === "save" ? "Saving…" : "Confirm and save period dates"}</button>
            </div>
          </section>
        )}
      </div>
    </Card>
  );
}
