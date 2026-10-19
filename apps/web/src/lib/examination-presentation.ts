import type { GenerationSession } from "@/lib/examination-generation-types";

export const HARD_RULES = [
  ["H1", "Student overlap and breaks", "Student intervals, including repeat candidates, cannot overlap and include the configured break."],
  ["H2", "Effective seating capacity", "Room candidate counts and logical seats must fit the effective frozen capacity; zero-seat venues cannot be used."],
  ["H3", "Compatible venues", "Rooms must satisfy examination-type rules and module venue overrides."],
  ["H4", "Source availability blocks", "Teaching, maintenance and other source blocks must be respected."],
  ["H5", "Room overlap", "A room cannot host overlapping examinations or conflict with a fixed block."],
  ["H6", "Period boundaries", "Every examination must be inside the selected examination period."],
  ["H7", "Daily hours and starts", "Starts must follow policy and the complete sitting must fit within daily examination hours."],
  ["H8", "Exam calendar", "Weekends and excluded dates must follow the selected period calendar."],
  ["H9", "Reading time", "Room and student intervals must reserve writing duration plus reading time."],
  ["H10", "Sessions and candidate seating", "Use the minimum feasible number of non-overlapping sittings. Every registered candidate gets one room and logical seat in exactly one sitting."],
  ["H11", "Consistency", "Input/output references, counts, session identifiers, room allocations and logical seats must be consistent."],
] as const;

export type AcademicFilters = { year: string; semester: string; specialization: string; module: string };
export const EMPTY_FILTERS: AcademicFilters = { year: "", semester: "", specialization: "", module: "" };

export function matchesAcademic(item: { academic_year?: number; semester?: number; specializations?: string[]; module_code: string }, filters: AcademicFilters): boolean {
  return (!filters.year || String(item.academic_year) === filters.year)
    && (!filters.semester || String(item.semester) === filters.semester)
    && (!filters.specialization || Boolean(item.specializations?.includes(filters.specialization)))
    && (!filters.module || item.module_code === filters.module);
}

export function filterSessions(sessions: GenerationSession[], filters: AcademicFilters) {
  return sessions.filter((session) => matchesAcademic(session, filters)).sort((a, b) =>
    a.date.localeCompare(b.date) || a.start_time.localeCompare(b.start_time) || a.module_code.localeCompare(b.module_code) || a.session_number - b.session_number);
}

export function calendarMonths(start: string, end: string): string[] {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(start) || !/^\d{4}-\d{2}-\d{2}$/.test(end) || start > end) return [];
  const months: string[] = [];
  const cursor = new Date(`${start.slice(0, 7)}-01T00:00:00Z`);
  if (!Number.isFinite(cursor.getTime())) return [];
  for (let i = 0; i < 6 && cursor.toISOString().slice(0, 7) <= end.slice(0, 7); i++) {
    months.push(cursor.toISOString().slice(0, 7));
    cursor.setUTCMonth(cursor.getUTCMonth() + 1);
  }
  return months;
}

export function monthCells(month: string): (string | null)[] {
  const first = new Date(`${month}-01T00:00:00Z`);
  if (!Number.isFinite(first.getTime())) return [];
  const days = new Date(Date.UTC(first.getUTCFullYear(), first.getUTCMonth() + 1, 0)).getUTCDate();
  const offset = (first.getUTCDay() + 6) % 7;
  const cells: (string | null)[] = Array.from({ length: offset }, () => null);
  for (let day = 1; day <= days; day++) cells.push(`${month}-${String(day).padStart(2, "0")}`);
  while (cells.length % 7) cells.push(null);
  return cells;
}
