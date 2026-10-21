import { Card, EmptyState, formatDate } from "@/components/console/ui";
import { dayLabel, orderedSessions, sessionFacts, type StaffTeachingVersion } from "@/lib/staff-detail";
import { SESSION_TYPES } from "@/lib/teaching";

export function AssignedSessions({ version }: { version: StaffTeachingVersion }) {
  const sessions = orderedSessions(version.sessions);
  return <Card title="Assigned sessions" description={`${sessions.length} scheduled sessions · ${version.label}. Times use the saved timetable’s timezone (${version.summary.calendar.timezone}).`}>
    {!sessions.length ? <EmptyState icon="calendar_month" title="No assigned sessions">This staff member has no sessions in the selected timetable version. Try another available version.</EmptyState> : <>
      <ul className="divide-y divide-line md:hidden">{sessions.map((session) => {
        const facts = sessionFacts(session);
        return <li key={session.delivery_reference} className="space-y-3 p-4 text-sm"><div><p className="font-semibold text-ink">{dayLabel(session.day)} · {session.start_time}–{session.end_time}</p><p className="mt-1 text-xs text-muted">{formatDate(session.teaching_date)}</p></div>
          <div>{facts.modules.map((module) => <p key={module.code} className="text-ink"><span className="font-semibold">{module.code}</span> · {module.name}</p>)}</div>
          <dl className="grid grid-cols-2 gap-3"><div><dt className="text-xs text-muted">Session type</dt><dd className="mt-1 text-ink">{facts.types.map((type) => SESSION_TYPES[type] ?? type).join(", ") || "Not recorded"}</dd></div><div><dt className="text-xs text-muted">Venue</dt><dd className="mt-1 text-ink">{facts.venue}</dd></div><div className="col-span-2"><dt className="text-xs text-muted">Student groups</dt><dd className="mt-1 break-words text-ink">{facts.groups.join(", ") || "Not recorded"}</dd></div><div><dt className="text-xs text-muted">Semester</dt><dd className="mt-1 text-ink">{facts.semesters.join(", ") || "Not recorded"}</dd></div><div><dt className="text-xs text-muted">Version</dt><dd className="mt-1 text-ink">v{version.summary.version_number}</dd></div></dl>
        </li>;
      })}</ul>
      <div className="hidden overflow-x-auto md:block"><table className="w-full min-w-[64rem] text-left text-sm"><caption className="sr-only">Assigned teaching sessions for {version.label}</caption><thead className="border-b border-line bg-background/60 text-xs font-semibold text-muted"><tr>{["Module", "Session type", "Student group", "Day", "Time", "Venue", "Semester", "Version"].map((label) => <th key={label} scope="col" className="px-4 py-3">{label}</th>)}</tr></thead>
        <tbody className="divide-y divide-line">{sessions.map((session) => {
          const facts = sessionFacts(session);
          return <tr key={session.delivery_reference} className="align-top hover:bg-background/50"><td className="px-4 py-4">{facts.modules.map((module) => <div key={module.code}><p className="font-semibold text-ink">{module.code}</p><p className="mt-1 max-w-56 text-xs text-muted">{module.name}</p></div>)}</td><td className="px-4 py-4 text-ink">{facts.types.map((type) => SESSION_TYPES[type] ?? type).join(", ") || "Not recorded"}</td><td className="max-w-52 px-4 py-4 text-ink"><p className="break-words">{facts.groups.join(", ") || "Not recorded"}</p></td><td className="whitespace-nowrap px-4 py-4 text-ink">{dayLabel(session.day)}<p className="mt-1 text-xs text-muted">{formatDate(session.teaching_date)}</p></td><td className="whitespace-nowrap px-4 py-4 tabular-nums text-ink">{session.start_time}–{session.end_time}</td><td className="px-4 py-4 text-ink">{facts.venue}</td><td className="px-4 py-4 text-ink">{facts.semesters.join(", ") || "Not recorded"}</td><td className="whitespace-nowrap px-4 py-4 text-ink">v{version.summary.version_number}</td></tr>;
        })}</tbody></table></div>
    </>}
  </Card>;
}
