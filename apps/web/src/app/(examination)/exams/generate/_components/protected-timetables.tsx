import Link from "next/link";
import { Badge, formatDate } from "@/components/console/ui";
import type { CrossTimetableSummary, ProtectionPreview } from "@/lib/examination-protection";

/** Public timetable/room information only. Shared-student counts come solely from generation. */
export function ProtectedTimetables({ data, resultGenerated }: {
  data: CrossTimetableSummary | ProtectionPreview | null | undefined; resultGenerated?: boolean;
}) {
  if (!data) return <section aria-label="Cross-semester clash prevention" className="rounded-xl border border-line bg-surface p-4">
    <h3 className="font-semibold text-ink">Cross-semester clash prevention</h3>
    <p className="mt-2 text-sm text-muted">The service did not return a cross-cycle check summary. Protection status is unavailable for this result.</p>
  </section>;
  const actual = "student_conflict_status" in data ? data : null;
  const incomplete = "incomplete" in data && data.incomplete;
  return <section aria-label="Cross-semester clash prevention" className="space-y-3 rounded-xl border border-line bg-surface p-4 sm:p-5">
    <div className="flex flex-wrap items-center gap-2">
      <h3 className="font-semibold text-ink">Cross-semester clash prevention</h3>
      <Badge tone="primary">{actual ? "Generation input snapshot" : "Read-only preview"}</Badge>
    </div>
    <p className="text-sm leading-6 text-muted">Approved and published timetables of other examination cycles are protected. Generation must work around their occupied rooms and students, including repeat students and the minimum break. Drafts do not reserve slots.</p>
    {actual ? <div className={`rounded-xl p-3 text-sm ${resultGenerated ? "bg-accent text-accent-ink" : "bg-secondary-soft text-secondary-ink"}`}>
      <p>{resultGenerated && actual.student_conflict_status !== "blocked" ? "The returned timetable passed cross-cycle validation."
        : "No clash-free timetable was established. Review the solver outcome and any blocking issues below."}</p>
      <p className="mt-1">{actual.shared_students} candidates also sit protected examinations · {actual.overlapping_drafts} overlapping drafts (not blocking).</p>
      {resultGenerated && <p className="mt-1">Reported room conflicts: {actual.venue_conflicts} · Reported student conflicts: {actual.student_conflicts}</p>}
    </div> : <p className="text-xs leading-5 text-muted">This preview uses the selected calendar and minimum-break boundary buffer. It is not a room reservation or a student-clash check. Generation, Save Draft, approval and publication check current protection again.</p>}
    {"notices" in data && data.notices.map((notice, index) => <p key={index} role="status" className="rounded-xl bg-secondary-soft p-3 text-sm text-secondary-ink">{notice}</p>)}
    {actual?.blocking_issues.length ? <div role="alert" className="rounded-xl bg-danger-soft p-3 text-sm leading-6 text-danger">
      <p className="font-semibold">Scheduling issues</p><ul className="list-disc pl-5">{actual.blocking_issues.map((issue, index) => <li key={index}>{issue}</li>)}</ul>
    </div> : null}
    {!data.protected_timetables.length ? <p className="text-sm text-muted">{incomplete ? "No overlapping protected timetable was found in the portion reviewed. The preview is incomplete."
      : "No protected timetable overlaps this calendar or its minimum-break boundary buffer."}</p>
      : <details open><summary className="cursor-pointer text-sm font-semibold text-primary-strong">Protected timetables ({data.protected_timetables.length})</summary>
        <ul className="mt-2 grid gap-2 sm:grid-cols-2">{data.protected_timetables.map((t) => <li key={t.timetable_id} className="rounded-xl bg-background p-3 text-sm">
          <div className="flex flex-wrap items-center gap-2"><Link className="font-semibold text-primary-strong underline" href={`/exams/timetables/${t.timetable_id}`}>{t.period_name} · Draft #{t.draft_number}</Link><Badge tone="primary">{t.status}</Badge></div>
          <p className="mt-1 text-xs text-muted">{t.period_code} · {formatDate(t.start_date)} – {formatDate(t.end_date)}</p>
          <p className="mt-1 text-xs text-muted">{t.sessions_in_range} sessions in range · {t.occupied_venue_slots} occupied room slots</p>
        </li>)}</ul>
      </details>}
    {data.occupied_slots.length > 0 && <details open><summary className="cursor-pointer text-sm font-semibold text-primary-strong">Occupied exam slots ({data.occupied_slot_total})</summary>
      <p className="my-2 text-xs text-muted">Showing {data.occupied_slots.length} of {data.occupied_slot_total} slots{incomplete ? " in the portion reviewed" : ""}. Multiple rooms appear separately. Boundary-buffer sessions may fall just outside your chosen dates.</p>
      <div className="max-h-80 overflow-auto rounded-xl border border-line"><table className="w-full min-w-[38rem] text-left text-sm">
        <caption className="sr-only">Protected examination dates, times and rooms of other cycles</caption>
        <thead className="sticky top-0 bg-background text-xs text-muted"><tr>{["Date", "Time", "Module", "Room", "Cycle / timetable"].map((label) => <th key={label} scope="col" className="px-3 py-2">{label}</th>)}</tr></thead>
        <tbody className="divide-y divide-line text-muted">{data.occupied_slots.map((s, index) => <tr key={`${s.timetable_id}-${s.date}-${s.start_time}-${s.module_code}-${s.venue_id}-${index}`}>
          <td className="px-3 py-2">{formatDate(s.date)}</td><td className="whitespace-nowrap px-3 py-2">{s.start_time.slice(0, 5)}–{s.end_time.slice(0, 5)}</td>
          <td className="px-3 py-2 font-mono text-ink">{s.module_code}</td><td className="px-3 py-2">{s.venue_id}</td>
          <td className="px-3 py-2"><Link href={`/exams/timetables/${s.timetable_id}`} className="text-primary-strong underline">{s.period_code} · #{s.timetable_id}</Link></td>
        </tr>)}</tbody>
      </table></div>
    </details>}
  </section>;
}
