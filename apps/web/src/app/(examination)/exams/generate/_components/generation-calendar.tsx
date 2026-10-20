"use client";

import { useState } from "react";
import { Card, formatDate } from "@/components/console/ui";
import {
  calendarErrors, calendarExamDays, calendarFromPeriod, calendarPeriod, exclusionDateError, type ExamCalendarInput, isCalendarDate,
  startsWithinHours,
} from "@/lib/examination-calendar";
import type { PeriodDefinition } from "@/lib/examination-workspace-types";
import { CalendarGrid, fieldClass, secondaryButton } from "./period-calendar";

function FieldError({ id, message }: { id: string; message?: string }) {
  return <p id={id} className="mt-1 text-xs text-danger">{message}</p>;
}

/**
 * Step 2: the exam dates, daily hours, weekends and excluded dates for THIS generation. It starts from the
 * configured period and never changes it; the selected calendar is sent with the generation request.
 */
export function GenerationCalendar({ period, calendar, onChange, permittedStarts, disabled }: {
  period: PeriodDefinition;
  calendar: ExamCalendarInput;
  onChange: (calendar: ExamCalendarInput) => void;
  permittedStarts: unknown;
  disabled: boolean;
}) {
  const [newDate, setNewDate] = useState("");
  const update = (patch: Partial<ExamCalendarInput>) => onChange({ ...calendar, ...patch });
  const errors = calendarErrors(calendar, period.academic_year_label);
  const days = calendarExamDays(calendar);
  const preview = calendarPeriod(period, calendar);
  const starts = startsWithinHours(permittedStarts, calendar);
  const candidateError = newDate ? exclusionDateError(newDate, { ...calendar, expected_fingerprint: "" }, calendar.exclusions.map((item) => item.excluded_date)) : undefined;
  const bounds = { min: isCalendarDate(calendar.start_date) ? calendar.start_date : undefined, max: isCalendarDate(calendar.end_date) ? calendar.end_date : undefined };
  const configured = calendarFromPeriod(period);
  const unchanged = JSON.stringify(configured) === JSON.stringify(calendar);
  const toggle = (day: string) => update({ exclusions: calendar.exclusions.some((row) => row.excluded_date === day)
    ? calendar.exclusions.filter((row) => row.excluded_date !== day)
    : [...calendar.exclusions, { excluded_date: day, reason: "" }].sort((a, b) => a.excluded_date.localeCompare(b.excluded_date)) });

  return (
    <Card title="2. Exam calendar" description="Choose the dates and hours for this timetable. The timetable is generated on exactly these days; the configured period is not changed.">
      <fieldset disabled={disabled} className="space-y-6 p-5">
        <legend className="sr-only">Exam calendar for this generation</legend>
        <div className="flex flex-wrap items-start justify-between gap-3 rounded-xl bg-background p-4 text-sm text-muted">
          <p>
            Configured period: <span className="font-semibold text-ink">{formatDate(period.start_date)} – {formatDate(period.end_date)}</span>
            {" · "}{period.day_start_time.slice(0, 5)}–{period.day_end_time.slice(0, 5)} · {period.exam_days.length} exam days.
            {unchanged ? " This generation uses the same calendar." : " This generation uses your selected calendar below."}
          </p>
          {!unchanged && <button type="button" className={secondaryButton} onClick={() => { onChange(configured); setNewDate(""); }}>Reset to configured period</button>}
        </div>

        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <label className="text-sm font-medium">Exam start date
            <input type="date" value={calendar.start_date} onChange={(event) => update({ start_date: event.target.value })}
              aria-invalid={Boolean(errors.start_date)} aria-describedby="calendar-start-error" className={fieldClass} />
            <FieldError id="calendar-start-error" message={errors.start_date} />
          </label>
          <label className="text-sm font-medium">Exam end date
            <input type="date" value={calendar.end_date} min={bounds.min} onChange={(event) => update({ end_date: event.target.value })}
              aria-invalid={Boolean(errors.end_date)} aria-describedby="calendar-end-error" className={fieldClass} />
            <FieldError id="calendar-end-error" message={errors.end_date} />
          </label>
          <label className="text-sm font-medium">Daily start time
            <input type="time" step={60} value={calendar.day_start_time} onChange={(event) => update({ day_start_time: event.target.value })}
              aria-invalid={Boolean(errors.day_start_time)} aria-describedby="calendar-day-start-error" className={fieldClass} />
            <FieldError id="calendar-day-start-error" message={errors.day_start_time} />
          </label>
          <label className="text-sm font-medium">Daily end time
            <input type="time" step={60} value={calendar.day_end_time} onChange={(event) => update({ day_end_time: event.target.value })}
              aria-invalid={Boolean(errors.day_end_time)} aria-describedby="calendar-day-end-error" className={fieldClass} />
            <FieldError id="calendar-day-end-error" message={errors.day_end_time} />
          </label>
        </div>

        <div className="flex flex-wrap gap-5 text-sm">
          <label className="flex items-center gap-2"><input type="checkbox" checked={calendar.include_saturdays} className="accent-primary"
            onChange={(event) => update({ include_saturdays: event.target.checked })} />Allow exams on Saturdays</label>
          <label className="flex items-center gap-2"><input type="checkbox" checked={calendar.include_sundays} className="accent-primary"
            onChange={(event) => update({ include_sundays: event.target.checked })} />Allow exams on Sundays</label>
        </div>

        {starts.inside.length === 0 ? (
          <p role="alert" className="rounded-xl bg-danger-soft p-3 text-sm text-danger">
            None of the permitted start times ({starts.outside.join(", ") || "not returned"}) is inside these daily hours, so no exam can be scheduled. Widen the hours or change the scheduling settings.
          </p>
        ) : starts.outside.length > 0 && (
          <p className="rounded-xl bg-secondary-soft p-3 text-sm text-secondary-ink">
            Start times {starts.outside.join(", ")} are outside these hours and will not be used. Exams can start at {starts.inside.join(", ")}.
          </p>
        )}

        <section aria-label="Excluded dates" className="space-y-2">
          <h3 className="text-sm font-semibold text-ink">Excluded dates</h3>
          <p className="text-xs text-muted">No exam is scheduled on these days. Select a day in the calendar below, or add it here, and give a short reason such as “Poya day”.</p>
          {calendar.exclusions.length === 0 && <p className="text-sm text-muted">No excluded dates.</p>}
          {calendar.exclusions.map((item, index) => (
            <div key={`${item.excluded_date}-${index}`} className="flex flex-wrap items-start gap-2">
              <label className="text-xs text-muted">Date
                <input type="date" {...bounds} value={item.excluded_date} className={fieldClass}
                  onChange={(event) => update({ exclusions: calendar.exclusions.map((row, i) => i === index ? { ...row, excluded_date: event.target.value } : row) })}
                  aria-invalid={Boolean(errors[`exclusions.${index}.excluded_date`])} aria-describedby={`calendar-exclusion-${index}-date`} />
                <FieldError id={`calendar-exclusion-${index}-date`} message={errors[`exclusions.${index}.excluded_date`]} />
              </label>
              <label className="min-w-40 flex-1 text-xs text-muted">Reason (3–120 characters)
                <input maxLength={120} value={item.reason} className={fieldClass}
                  onChange={(event) => update({ exclusions: calendar.exclusions.map((row, i) => i === index ? { ...row, reason: event.target.value } : row) })}
                  aria-invalid={Boolean(errors[`exclusions.${index}.reason`])} aria-describedby={`calendar-exclusion-${index}-reason`} />
                <FieldError id={`calendar-exclusion-${index}-reason`} message={errors[`exclusions.${index}.reason`]} />
              </label>
              <button type="button" className={`${secondaryButton} mt-5`} onClick={() => update({ exclusions: calendar.exclusions.filter((_, i) => i !== index) })}>Remove</button>
            </div>
          ))}
          <div className="flex flex-wrap items-start gap-2">
            <label className="text-xs text-muted">Add an excluded date
              <input type="date" {...bounds} value={newDate} onChange={(event) => setNewDate(event.target.value)} className={fieldClass}
                aria-invalid={Boolean(candidateError)} aria-describedby="calendar-new-exclusion" />
              <FieldError id="calendar-new-exclusion" message={candidateError} />
            </label>
            <button type="button" className={`${secondaryButton} mt-5`} disabled={!newDate || Boolean(candidateError) || calendar.exclusions.length >= 120}
              onClick={() => { toggle(newDate); setNewDate(""); }}>Add excluded date</button>
          </div>
        </section>

        {errors.calendar && <p role="alert" className="text-sm text-danger">{errors.calendar}</p>}
        {preview ? (
          <div className="space-y-3">
            <p className="text-sm font-semibold text-primary-strong">
              {formatDate(calendar.start_date)} – {formatDate(calendar.end_date)} · {days.length} exam days · {calendar.day_start_time}–{calendar.day_end_time}
            </p>
            <CalendarGrid period={preview} proposed onDate={disabled ? undefined : toggle} />
          </div>
        ) : <p className="rounded-xl bg-danger-soft p-3 text-sm text-danger">Choose a valid date range of at most 120 days to see its calendar.</p>}
      </fieldset>
    </Card>
  );
}
