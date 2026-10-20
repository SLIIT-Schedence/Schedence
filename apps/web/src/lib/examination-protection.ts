import type { SavedTimetableDetail } from "@/lib/examination";
import { redactStudentIds } from "@/lib/examination-timetables";

export type ProtectedTimetable = {
  timetable_id: number; period_code: string; period_name: string; status: "APPROVED" | "PUBLISHED";
  draft_number: number; start_date: string; end_date: string; sessions_in_range: number; occupied_venue_slots: number;
};
export type OccupiedExamSlot = {
  timetable_id: number; period_code: string; module_code: string; venue_id: string;
  date: string; start_time: string; end_time: string;
};
/** Existing CrossTimetableOut: aggregate counts and public room slots, never seating records. */
export type CrossTimetableSummary = {
  protected_timetables: ProtectedTimetable[]; occupied_slots: OccupiedExamSlot[]; occupied_slot_total: number;
  shared_students: number; student_conflict_status: "none" | "clear" | "blocked";
  venue_conflicts: number; student_conflicts: number; overlapping_drafts: number;
  protected_fingerprint: string; blocking_issues: string[];
};
export type ProtectionPreview = Pick<CrossTimetableSummary, "protected_timetables" | "occupied_slots" | "occupied_slot_total"> & {
  incomplete: boolean; notices: string[];
};

/** Pick every public field explicitly before crossing the server/browser boundary. */
export function publicProtection(raw: CrossTimetableSummary | null | undefined): CrossTimetableSummary | null {
  if (!raw) return null;
  const text = redactStudentIds;
  return {
    protected_timetables: raw.protected_timetables.map((t) => ({
      timetable_id: t.timetable_id, period_code: text(t.period_code), period_name: text(t.period_name), status: t.status,
      draft_number: t.draft_number, start_date: t.start_date, end_date: t.end_date,
      sessions_in_range: t.sessions_in_range, occupied_venue_slots: t.occupied_venue_slots,
    })),
    occupied_slots: raw.occupied_slots.map((s) => ({
      timetable_id: s.timetable_id, period_code: text(s.period_code), module_code: text(s.module_code),
      venue_id: text(s.venue_id), date: s.date, start_time: s.start_time, end_time: s.end_time,
    })),
    occupied_slot_total: raw.occupied_slot_total, shared_students: raw.shared_students,
    student_conflict_status: raw.student_conflict_status, venue_conflicts: raw.venue_conflicts,
    student_conflicts: raw.student_conflicts, overlapping_drafts: raw.overlapping_drafts,
    protected_fingerprint: raw.protected_fingerprint, blocking_issues: raw.blocking_issues.map(text),
  };
}

/** Read-only preview from saved detail APIs. No student assignments are requested or inferred. */
export function protectionPreview(details: SavedTimetableDetail[], periodCode: string, start: string, end: string,
  minimumBreak: number, incomplete: boolean): ProtectionPreview {
  const first = Date.parse(`${start}T00:00:00Z`) - minimumBreak * 60_000;
  const last = Date.parse(`${end}T00:00:00Z`) + 86_400_000 + minimumBreak * 60_000;
  const timetables: ProtectedTimetable[] = [];
  const slots: OccupiedExamSlot[] = [];
  for (const t of details) {
    if (t.period_code === periodCode || (t.status !== "APPROVED" && t.status !== "PUBLISHED")) continue;
    const sessions = t.sessions.filter((s) => Date.parse(`${s.date}T${s.start_time}Z`) < last
      && Date.parse(`${s.date}T${s.end_time}Z`) > first);
    if (!sessions.length && !(t.inputs.start_date <= end && start <= t.inputs.end_date)) continue;
    timetables.push({ timetable_id: t.timetable_id, period_code: redactStudentIds(t.period_code),
      period_name: redactStudentIds(t.period_name), status: t.status, draft_number: t.draft_number,
      start_date: t.inputs.start_date, end_date: t.inputs.end_date, sessions_in_range: sessions.length,
      occupied_venue_slots: sessions.reduce((sum, s) => sum + s.rooms.length, 0) });
    for (const s of sessions) for (const room of s.rooms) slots.push({
      timetable_id: t.timetable_id, period_code: redactStudentIds(t.period_code), module_code: redactStudentIds(s.module_code),
      venue_id: redactStudentIds(room.venue_id), date: s.date, start_time: s.start_time, end_time: s.end_time,
    });
  }
  slots.sort((a, b) => a.date.localeCompare(b.date) || a.start_time.localeCompare(b.start_time)
    || a.venue_id.localeCompare(b.venue_id) || a.timetable_id - b.timetable_id);
  return { protected_timetables: timetables, occupied_slots: slots.slice(0, 100), occupied_slot_total: slots.length,
    incomplete, notices: incomplete ? ["This preview is incomplete. Some protected timetables could not be reviewed; generation checks all protected timetables again."] : [] };
}
