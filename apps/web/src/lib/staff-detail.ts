import type { TimetableSession, TimetableSummary, WorkloadStaff } from "./teaching";
import type { StaffModule } from "./teaching-preview";

export const STAFF_TABS = [
  { id: "overview", label: "Overview" }, { id: "sessions", label: "Assigned Sessions" },
  { id: "workload", label: "Workload" }, { id: "availability", label: "Availability & Preferences" },
  { id: "history", label: "History" },
] as const;
export type StaffTab = typeof STAFF_TABS[number]["id"];
export type StaffTeachingVersion = { versionId: string; label: string; summary: TimetableSummary;
  workload: WorkloadStaff | null; modules: StaffModule[]; sessions: TimetableSession[] };
export function staffTab(value?: string, history?: string): StaffTab {
  if (STAFF_TABS.some((tab) => tab.id === value)) return value as StaffTab;
  return history === "1" ? "history" : "overview";
}
export function staffHref(staffId: string, tab: StaffTab = "overview", version?: string) {
  const query = new URLSearchParams({ tab });
  if (version) query.set("version", version);
  return `/academic/staff/${encodeURIComponent(staffId)}?${query}`;
}
export const DAY_ORDER = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"];
export const dayLabel = (day: string) => day.charAt(0).toUpperCase() + day.slice(1).toLowerCase();
export const staffHours = (value: number | null | undefined) => value === null || value === undefined ? "Not available" : `${Number.isInteger(value) ? value : value.toFixed(1)} h`;
export function orderedSessions(sessions: TimetableSession[]) {
  return [...sessions].sort((a, b) => a.teaching_date.localeCompare(b.teaching_date) ||
    DAY_ORDER.indexOf(a.day) - DAY_ORDER.indexOf(b.day) || a.start_minute - b.start_minute || a.delivery_reference.localeCompare(b.delivery_reference));
}
export function sessionFacts(session: TimetableSession) {
  return {
    modules: [...new Map(session.coverage.map((row) => [row.module_code, { code: row.module_code, name: row.module_name }])).values()],
    types: [...new Set(session.coverage.map((row) => row.session_type))],
    groups: [...new Set(session.coverage.map((row) => row.group_id))].sort(),
    semesters: [...new Set(session.coverage.map((row) => row.semester))].sort((a, b) => a - b),
    venue: session.venue?.venue_name ?? (session.delivery_mode === "ONLINE" ? "Online" : "Not recorded"),
  };
}
export function teachingLoadByDay(sessions: TimetableSession[]) {
  return DAY_ORDER.map((day) => ({ day, minutes: sessions.filter((row) => row.day === day)
    .reduce((sum, row) => sum + Math.max(0, row.end_minute - row.start_minute), 0) }));
}
export function workloadScale(assigned: number, target: number | null) {
  const maximum = Math.max(assigned, target ?? 0, 1);
  return { assigned: assigned / maximum * 100, target: target === null ? null : target / maximum * 100 };
}
