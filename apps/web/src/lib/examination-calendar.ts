import type { PeriodDefinition, PeriodEdit } from "@/lib/examination-workspace-types";

export function isCalendarDate(value: unknown): value is string {
  return typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value)
    && Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0, 10) === value;
}

function validRange(edit: PeriodEdit): boolean {
  return isCalendarDate(edit.start_date) && isCalendarDate(edit.end_date) && edit.start_date <= edit.end_date
    && (Date.parse(edit.end_date) - Date.parse(edit.start_date)) / 86_400_000 < 120;
}

/** Display calculation only. The authenticated backend dry-run remains authoritative. */
export function proposedExamDays(edit: PeriodEdit): string[] {
  if (!validRange(edit)) return [];
  const excluded = new Set(edit.exclusions.map((item) => item.excluded_date));
  const cursor = new Date(`${edit.start_date}T00:00:00Z`);
  const days: string[] = [];
  while (cursor.toISOString().slice(0, 10) <= edit.end_date) {
    const day = cursor.toISOString().slice(0, 10);
    const weekday = cursor.getUTCDay();
    if ((weekday !== 6 || edit.include_saturdays) && (weekday !== 0 || edit.include_sundays) && !excluded.has(day)) days.push(day);
    cursor.setUTCDate(cursor.getUTCDate() + 1);
  }
  return days;
}

export function proposedCalendar(saved: PeriodDefinition, edit: PeriodEdit): PeriodDefinition | null {
  if (!validRange(edit)) return null;
  return { ...saved, ...edit, exam_days: proposedExamDays(edit) };
}

export function exclusionDateError(value: string, edit: PeriodEdit, existing: string[] = []): string | undefined {
  if (!isCalendarDate(value)) return "Choose a valid excluded date.";
  if (!isCalendarDate(edit.start_date) || !isCalendarDate(edit.end_date) || edit.start_date > edit.end_date) return "Set a valid proposed date range first.";
  if (value < edit.start_date || value > edit.end_date) return `Excluded dates must be between ${edit.start_date} and ${edit.end_date}.`;
  const weekday = new Date(`${value}T00:00:00Z`).getUTCDay();
  if ((weekday === 6 && !edit.include_saturdays) || (weekday === 0 && !edit.include_sundays)) return "This weekend day is already disabled. Remove this exclusion or allow that weekend day.";
  if (existing.includes(value)) return "This date is already excluded.";
}

/** Preserve ordinary spaces within a reason; the API disallows surrounding whitespace. */
export function normalizeExclusionReason(value: string): string {
  return value.replace(/^\p{White_Space}+|\p{White_Space}+$/gu, "");
}

export function exclusionReasonError(value: unknown): string | undefined {
  if (typeof value !== "string") return "Enter an exclusion reason of 3–120 characters.";
  // Match the API's ASCII control-character restriction, including pasted line breaks.
  if (Array.from(value).some((char) => char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127)) {
    return "Use a single line of text without tabs or control characters.";
  }
  const length = Array.from(normalizeExclusionReason(value)).length;
  if (length < 3 || length > 120) return "Enter an exclusion reason of 3–120 characters, excluding surrounding spaces.";
}

export function periodEditErrors(edit: PeriodEdit, academicYearLabel?: string): Record<string, string> {
  const errors: Record<string, string> = {};
  if (!isCalendarDate(edit.start_date)) errors.start_date = "Choose a valid proposed start date.";
  if (!isCalendarDate(edit.end_date)) errors.end_date = "Choose a valid proposed end date.";
  if (!errors.start_date && !errors.end_date) {
    if (edit.start_date > edit.end_date) errors.end_date = "End date must be on or after start date.";
    else if (!validRange(edit)) errors.end_date = "An examination period can span at most 120 days.";
    if (academicYearLabel && /^\d{4}\/\d{4}$/.test(academicYearLabel)) {
      const years = academicYearLabel.split("/");
      if (!years.includes(edit.start_date.slice(0, 4))) errors.start_date = `The proposed date must be within academic year ${academicYearLabel}.`;
      if (!years.includes(edit.end_date.slice(0, 4))) errors.end_date = `The proposed date must be within academic year ${academicYearLabel}.`;
    }
  }
  const seen: string[] = [];
  edit.exclusions.forEach((item, index) => {
    const error = exclusionDateError(item.excluded_date, edit, seen);
    if (error) errors[`exclusions.${index}.excluded_date`] = error;
    const reasonError = exclusionReasonError(item.reason);
    if (reasonError) errors[`exclusions.${index}.reason`] = reasonError;
    seen.push(item.excluded_date);
  });
  if (edit.exclusions.length > 120) errors.calendar = "At most 120 excluded dates are allowed.";
  else if (validRange(edit) && !proposedExamDays(edit).length) errors.calendar = "No allowed exam days remain. Adjust the dates, weekends or exclusions.";
  return errors;
}

/** Every edited value and the optimistic-concurrency version participate in preview matching. */
export function periodEditKey(edit: PeriodEdit): string {
  return JSON.stringify([edit.expected_fingerprint, edit.start_date, edit.end_date, edit.include_saturdays, edit.include_sundays,
    edit.exclusions.map((item) => [item.excluded_date, item.reason])]);
}

export function sameProposedCalendar(edit: PeriodEdit, returned: PeriodDefinition): boolean {
  const exclusions = (items: PeriodEdit["exclusions"]) => JSON.stringify(items.map((item) => [item.excluded_date, normalizeExclusionReason(item.reason)]).sort((a, b) => a[0].localeCompare(b[0])));
  return edit.start_date === returned.start_date && edit.end_date === returned.end_date
    && edit.include_saturdays === returned.include_saturdays && edit.include_sundays === returned.include_sundays
    && exclusions(edit.exclusions) === exclusions(returned.exclusions);
}

// --- Calendar selected for one generation (the saved period is not changed) ---------------------

export type ExamCalendarInput = {
  start_date: string;
  end_date: string;
  day_start_time: string; // HH:MM
  day_end_time: string;
  include_saturdays: boolean;
  include_sundays: boolean;
  exclusions: { excluded_date: string; reason: string }[];
};

const TIME = /^([01]\d|2[0-3]):[0-5]\d$/;
const asEdit = (calendar: ExamCalendarInput): PeriodEdit => ({
  expected_fingerprint: "", start_date: calendar.start_date, end_date: calendar.end_date,
  include_saturdays: calendar.include_saturdays, include_sundays: calendar.include_sundays, exclusions: calendar.exclusions,
});

/** The configured period's calendar: the starting point of the Exam calendar step. */
export function calendarFromPeriod(period: PeriodDefinition): ExamCalendarInput {
  return {
    start_date: period.start_date, end_date: period.end_date,
    day_start_time: period.day_start_time.slice(0, 5), day_end_time: period.day_end_time.slice(0, 5),
    include_saturdays: period.include_saturdays, include_sundays: period.include_sundays,
    exclusions: period.exclusions.map((item) => ({ excluded_date: item.excluded_date, reason: item.reason })),
  };
}

/** Every problem with a selected calendar, with the same rules as a saved period (the backend checks again). */
export function calendarErrors(calendar: ExamCalendarInput, academicYearLabel?: string): Record<string, string> {
  const errors = periodEditErrors(asEdit(calendar), academicYearLabel);
  if (!TIME.test(calendar.day_start_time)) errors.day_start_time = "Enter the daily start time as HH:MM.";
  if (!TIME.test(calendar.day_end_time)) errors.day_end_time = "Enter the daily end time as HH:MM.";
  else if (!errors.day_start_time && calendar.day_end_time <= calendar.day_start_time) errors.day_end_time = "The daily end time must be after the start time.";
  return errors;
}

export function calendarExamDays(calendar: ExamCalendarInput): string[] {
  return proposedExamDays(asEdit(calendar));
}

/** The selected calendar shown as a period for the month grid (display only). */
export function calendarPeriod(period: PeriodDefinition, calendar: ExamCalendarInput): PeriodDefinition | null {
  const proposed = proposedCalendar(period, asEdit(calendar));
  return proposed && { ...proposed, day_start_time: calendar.day_start_time, day_end_time: calendar.day_end_time };
}

/** Which permitted start times (scheduling settings) fall inside the selected daily hours. */
export function startsWithinHours(permitted: unknown, calendar: ExamCalendarInput): { inside: string[]; outside: string[] } {
  const starts = Array.isArray(permitted) ? permitted.filter((value): value is string => typeof value === "string") : [];
  const inside = starts.filter((start) => start >= calendar.day_start_time && start < calendar.day_end_time);
  return { inside, outside: starts.filter((start) => !inside.includes(start)) };
}

/** A strictly shaped calendar from untrusted form data, or null. Reasons lose surrounding whitespace. */
export function parseCalendar(raw: unknown): ExamCalendarInput | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const value = raw as Record<string, unknown>;
  const keys = ["start_date", "end_date", "day_start_time", "day_end_time", "include_saturdays", "include_sundays", "exclusions"];
  if (Object.keys(value).length !== keys.length || !keys.every((key) => Object.hasOwn(value, key))) return null;
  const { start_date, end_date, day_start_time, day_end_time, include_saturdays, include_sundays, exclusions } = value;
  if (typeof start_date !== "string" || typeof end_date !== "string" || typeof day_start_time !== "string" || typeof day_end_time !== "string"
    || typeof include_saturdays !== "boolean" || typeof include_sundays !== "boolean" || !Array.isArray(exclusions) || exclusions.length > 120) return null;
  const rows: ExamCalendarInput["exclusions"] = [];
  for (const item of exclusions) {
    if (!item || typeof item !== "object" || Array.isArray(item)) return null;
    const { excluded_date, reason } = item as Record<string, unknown>;
    if (typeof excluded_date !== "string" || typeof reason !== "string" || Object.keys(item).length !== 2) return null;
    rows.push({ excluded_date, reason: normalizeExclusionReason(reason) });
  }
  return { start_date, end_date, day_start_time, day_end_time, include_saturdays, include_sundays, exclusions: rows };
}
