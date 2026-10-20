"use client";

import { useState } from "react";
import { Card, EmptyState, formatDate } from "@/components/console/ui";
import type { GenerationSession } from "@/lib/examination-generation-types";
import type { TimetableExportMetadata } from "@/lib/examination-export";
import { type AcademicFilters, calendarMonths, EMPTY_FILTERS, filterSessions, monthCells } from "@/lib/examination-presentation";
import { AcademicFilterBar } from "./academic-groups";
import { fieldClass, secondaryButton } from "./period-calendar";

const heading = "px-4 py-3 text-xs font-semibold text-muted";
const cell = "px-4 py-3 text-sm text-muted";
const number = (value: number | undefined) => value === undefined ? "—" : value.toLocaleString("en-GB");

function CandidateCounts({ session }: { session: GenerationSession }) {
  return <span>{number(session.candidates)} total · {number(session.normal_candidates)} Normal · {number(session.repeat_candidates)} Repeat</span>;
}

export function TimetableBrowser({ sessions, valid, statusMessage, exportMetadata, title = "Generated timetable", description = "Filter the full-cycle result for display. Counts remain the full sitting's counts, including shared specializations.", filters: controlledFilters, onFiltersChange }: {
  sessions: GenerationSession[]; valid: boolean; statusMessage: string; exportMetadata?: TimetableExportMetadata; title?: string; description?: string;
  /** Optional controlled filters, so other parts of the page can download exactly what is shown here. */
  filters?: AcademicFilters; onFiltersChange?: (filters: AcademicFilters) => void;
}) {
  const [view, setView] = useState<"calendar" | "table">("calendar");
  const [localFilters, setLocalFilters] = useState(EMPTY_FILTERS);
  const filters = controlledFilters ?? localFilters;
  const setFilters = onFiltersChange ?? setLocalFilters;
  const [month, setMonth] = useState("");
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState("");
  const [downloaded, setDownloaded] = useState(false);
  const rows = filterSessions(sessions, filters);
  const months = rows.length ? calendarMonths(rows[0].date, rows[rows.length - 1].date) : [];
  const currentMonth = months.includes(month) ? month : months[0];
  const download = async () => {
    if (!exportMetadata || !valid || !rows.length || downloading) return;
    setDownloading(true); setDownloadError(""); setDownloaded(false);
    try {
      const { downloadTimetablePdf } = await import("@/lib/examination-pdf");
      await downloadTimetablePdf(rows, exportMetadata, filters);
      setDownloaded(true);
    } catch (error) {
      setDownloadError(error instanceof Error && error.message.startsWith("This timetable contains characters")
        ? error.message : "The PDF could not be downloaded. Retry, or check whether your browser blocked the download.");
    } finally { setDownloading(false); }
  };
  return <Card title={title} description={description}>
    <div className="space-y-4 p-5">
      <AcademicFilterBar items={sessions} filters={filters} onChange={(next) => { setFilters(next); setDownloadError(""); setDownloaded(false); }} includeModule />
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-xs text-muted">{rows.length} of {sessions.length} sessions shown. Examination period local times.</p>
        <div className="flex flex-wrap gap-2" role="group" aria-label="Timetable view and download">
          <button type="button" className={secondaryButton} aria-pressed={view === "calendar"} onClick={() => setView("calendar")}>Calendar View</button>
          <button type="button" className={secondaryButton} aria-pressed={view === "table"} onClick={() => setView("table")}>Table View</button>
          {exportMetadata && <button type="button" className={secondaryButton} disabled={downloading || !valid || !rows.length} aria-busy={downloading} onClick={download}>{downloading ? "Preparing PDF…" : "Download PDF"}</button>}
        </div>
      </div>
      {exportMetadata && <p className="text-xs leading-5 text-muted"><strong className="text-ink">{exportMetadata.status}</strong> · PDF includes all sessions matching the filters above, across all calendar months. Normal/repeat counts are session registrations, not unique students. Shared sessions appear once; specialization counts are unavailable. Specialization filters use current module mappings.</p>}
      {downloadError && <p role="alert" className="text-sm text-danger">{downloadError}</p>}
      {downloaded && <p role="status" className="text-sm text-accent-ink">PDF download requested. Check your browser downloads.</p>}
      {!valid ? <EmptyState icon="fact_check" title="No timetable to display">{statusMessage} Review the validation results and diagnostics before trying again.</EmptyState>
        : !rows.length ? <EmptyState title="No sessions in this academic group">No sessions match the selected year, semester, specialization or module. The generated full-cycle timetable has not changed.</EmptyState>
          : view === "calendar" ? <>
            <label className="block max-w-xs text-xs font-medium text-muted">Calendar month<select value={currentMonth} onChange={(event) => setMonth(event.target.value)} className={fieldClass}>{months.map((item) => <option key={item}>{item}</option>)}</select></label>
            <div className="overflow-x-auto rounded-xl border border-line">
              <div className="grid min-w-[60rem] grid-cols-7 gap-px bg-line">
                {["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"].map((day) => <div key={day} className="bg-background p-3 text-center text-xs font-semibold text-muted">{day}</div>)}
                {monthCells(currentMonth).map((day, index) => <section key={day ?? `blank-${index}`} aria-label={day ? formatDate(day) : undefined} className="min-h-32 bg-surface p-2">
                  {day && <>
                    <h3 className="mb-2 text-xs font-semibold text-muted">{Number(day.slice(-2))}</h3>
                    <div className="space-y-2">{rows.filter((session) => session.date === day).map((session) => <article key={`${session.module_code}-${session.session_number}`} className="space-y-1 rounded-lg border border-primary/15 bg-primary-soft p-2 text-[11px] leading-4 text-ink">
                      <p className="font-mono font-semibold text-primary-strong">{session.start_time.slice(0, 5)}–{session.end_time.slice(0, 5)}</p>
                      <p className="font-semibold">{session.module_code} · {session.module_name}</p>
                      <p>Year {session.academic_year ?? "—"} · Semester {session.semester ?? "—"}</p>
                      <p>Session {session.session_number} of {session.session_count} · {session.rooms.length} room(s)</p>
                      <ul>{session.rooms.map((room) => <li key={room.venue_id}>{room.venue_name} · {number(room.seats)} candidates</li>)}</ul>
                      <p><CandidateCounts session={session} /></p>
                    </article>)}</div>
                  </>}
                </section>)}
              </div>
            </div>
          </> : <div className="overflow-x-auto">
            <table className="w-full min-w-[66rem] text-left">
              <caption className="sr-only">Generated FINAL examination sessions by academic group, date and time</caption>
              <thead className="border-b border-line bg-background"><tr>{["Exam date", "Time", "Module and academic group", "Session / rooms", "Allocated venues", "Candidates"].map((label) => <th key={label} scope="col" className={heading}>{label}</th>)}</tr></thead>
              <tbody className="divide-y divide-line">{rows.map((session) => <tr key={`${session.module_code}-${session.session_number}-${session.date}-${session.start_time}`}>
                <td className={`${cell} whitespace-nowrap`}>{formatDate(session.date)}</td>
                <td className={`${cell} whitespace-nowrap font-mono text-xs`}>{session.start_time.slice(0, 5)}–{session.end_time.slice(0, 5)}</td>
                <th scope="row" className="max-w-80 px-4 py-3 text-sm font-medium text-ink"><span className="font-mono text-primary-strong">{session.module_code}</span> · {session.module_name}<span className="mt-1 block text-xs font-normal text-muted">Year {session.academic_year ?? "—"} · Semester {session.semester ?? "—"} · {session.specializations?.join(", ") || "Specialization unavailable"} · {session.delivery_mode ?? "Delivery mode unavailable"}</span></th>
                <td className={cell}>Session {session.session_number} of {session.session_count}<span className="block text-xs">{session.rooms.length} room(s){session.rooms.length > 1 ? " · Multiple rooms" : ""}</span></td>
                <td className={cell}><ul>{session.rooms.map((room) => <li key={room.venue_id}>{room.venue_name} · {number(room.seats)} candidates</li>)}</ul></td>
                <td className={cell}><CandidateCounts session={session} /></td>
              </tr>)}</tbody>
            </table>
          </div>}
    </div>
  </Card>;
}
