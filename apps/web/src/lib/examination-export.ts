import type { GenerationSession } from "./examination-generation-types";
import type { AcademicFilters } from "./examination-presentation";

/** Public timetable metadata only. Never pass an API snapshot or individual seating records. */
export type TimetableExportMetadata = {
  periodCode: string; periodName: string;
  academicYearLabel?: string; semester?: number; examKind?: string;
  status: "UNSAVED" | "DRAFT" | "APPROVED" | "PUBLISHED" | "ARCHIVED";
  recordedAt?: string; recordedAtLabel: "Generated" | "Saved";
  calendarStart?: string; calendarEnd?: string;
  draftNumber?: number; revision?: number;
  periodMetadataSource?: "current";
};

// Matches the general timetable's identifier redaction; no raw records enter the export.
export const exportText = (value: string) => value.replace(/[A-Z]{2}[0-9]{8}/gi, "[student identifier omitted]")
  .replace(/[\u0000-\u001f\u007f]/g, " ").replace(/\s+/g, " ").trim();

export function exportFilterLabel(filters: AcademicFilters): string {
  return [filters.year ? `Year ${filters.year}` : "All years", filters.semester ? `Module semester ${filters.semester}` : "All module semesters",
    filters.specialization || "All specializations", filters.module || "All modules"].map(exportText).join(" / ");
}

export function sessionGroup(session: GenerationSession): string {
  const specializations = [...new Set(session.specializations ?? [])].sort().map(exportText);
  return `${session.academic_year == null ? "Study year unavailable" : `Year ${session.academic_year}`} / ${specializations.join(" / ") || "Specialization unavailable"}${specializations.length > 1 ? " (shared module)" : ""}`;
}

/** Each session belongs to one group, even when a module is shared by several specializations. */
export function exportGroups(sessions: GenerationSession[]) {
  const groups = new Map<string, GenerationSession[]>();
  for (const session of sessions) {
    const label = sessionGroup(session);
    groups.set(label, [...(groups.get(label) ?? []), session]);
  }
  return [...groups].sort(([a], [b]) => a.localeCompare(b, "en", { numeric: true })).map(([label, rows]) => ({
    label, rows: [...rows].sort((a, b) => a.date.localeCompare(b.date) || a.start_time.localeCompare(b.start_time)
      || a.module_code.localeCompare(b.module_code) || a.session_number - b.session_number),
  }));
}

export function candidateText(session: GenerationSession): string {
  const value = (count: number | undefined) => count == null ? "not provided" : count.toLocaleString("en-GB");
  return `${value(session.candidates)} total\n${value(session.normal_candidates)} normal\n${value(session.repeat_candidates)} repeat`;
}

export const exportFilename = (metadata: TimetableExportMetadata, filters: AcademicFilters) =>
  `examination-${metadata.periodCode}-${metadata.status}-${filters.year ? `year-${filters.year}` : "all-years"}.pdf`.replace(/[^a-zA-Z0-9._-]/g, "-");
