import type { Metadata } from "next";
import Form from "next/form";
import Link from "next/link";
import { redirect } from "next/navigation";
import { Suspense } from "react";
import { Badge, Card, EmptyState, first, LoadError, PageHeader, query, TableSkeleton } from "@/components/console/ui";
import { Icon } from "@/components/icon";
import { getAcademicContext } from "@/lib/academic";
import { allSessions, chooseVersion, groupLabel, SESSION_TYPES, TEACHING_SERVICE, type TimetableSession } from "@/lib/teaching";
import { DraftNotice, LABEL, SELECT, SUBMIT, TeachingUnavailable, VersionBadges, VersionFields, hoursText } from "../_teaching/teaching-ui";

export const metadata: Metadata = { title: "Assigned sessions" };

type SearchParams = PageProps<"/academic/assigned-sessions">["searchParams"];

const KEYS = ["semester", "version", "year", "type", "lecturer"] as const;
const DAY_ORDER = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"];
const dayName = (day: string) => day.charAt(0) + day.slice(1).toLowerCase();

export default function AssignedSessionsPage({ searchParams }: PageProps<"/academic/assigned-sessions">) {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Assigned sessions"
        description="Every session each lecturer teaches in a validated timetable version: module, type, day and time, venue, delivery mode and student groups."
      />
      <Suspense fallback={<Card><TableSkeleton rows={8} /></Card>}>
        <Sessions searchParams={searchParams} />
      </Suspense>
    </div>
  );
}

async function Sessions({ searchParams }: { searchParams: SearchParams }) {
  const context = await getAcademicContext();
  if (!context) redirect("/");
  const raw = await searchParams;
  const params = Object.fromEntries(KEYS.map((key) => [key, first(raw[key])?.slice(0, 300)])) as Partial<Record<(typeof KEYS)[number], string>>;
  const institution = context.primary;

  const chosen = await chooseVersion(institution.id, params);
  if (chosen.status !== "ok") {
    return <TeachingUnavailable status={chosen.status} error={chosen.status === "error" ? chosen.error : undefined} institution={institution.name} retryHref="/academic/assigned-sessions" />;
  }
  const { choice } = chosen;
  const { version, semester } = choice;
  const years = [...new Set(version.scopes.filter((scope) => scope.semester === semester).map((scope) => scope.academic_year))].sort();
  const year = years.find((value) => String(value) === params.year);
  const type = params.type && params.type in SESSION_TYPES ? params.type : undefined;

  const result = await allSessions(institution.id, version.version_id, { semester, academic_year: year });
  if (!result.ok) {
    return (
      <Card>
        <LoadError error={result.error} what="assigned sessions" retryHref="/academic/assigned-sessions" service={TEACHING_SERVICE} />
      </Card>
    );
  }
  const typed = result.data.filter((session) => !type || session.coverage[0]?.session_type === type);
  const lecturers = new Map<string, { name: string; sessions: TimetableSession[] }>();
  for (const session of typed) {
    const entry = lecturers.get(session.lecturer.staff_id) ?? { name: session.lecturer.staff_name, sessions: [] };
    entry.sessions.push(session);
    lecturers.set(session.lecturer.staff_id, entry);
  }
  const options = [...new Map(result.data.map((session) => [session.lecturer.staff_id, session.lecturer.staff_name]))].sort((a, b) => a[1].localeCompare(b[1]));
  const lecturer = options.some(([id]) => id === params.lecturer) ? params.lecturer : undefined;
  const weekly = (staffId: string) =>
    `/academic/timetables${query({ semester, version: version.version_id, view: "lecturer", lecturer: staffId })}`;

  return (
    <>
      <DraftNotice version={version} />
      <Card>
        <Form action="/academic/assigned-sessions" className="grid gap-3 p-4 sm:grid-cols-2 sm:px-5 lg:grid-cols-6">
          <VersionFields choice={choice} />
          <label className={LABEL}>
            Academic year
            <select name="year" defaultValue={year ?? ""} className={`mt-1 ${SELECT}`}>
              <option value="">All years</option>
              {years.map((value) => (
                <option key={value} value={value}>
                  Year {value}
                </option>
              ))}
            </select>
          </label>
          <label className={LABEL}>
            Session type
            <select name="type" defaultValue={type ?? ""} className={`mt-1 ${SELECT}`}>
              <option value="">All types</option>
              {Object.entries(SESSION_TYPES).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label className={LABEL}>
            Lecturer
            <select name="lecturer" defaultValue={lecturer ?? ""} className={`mt-1 ${SELECT}`}>
              <option value="">All lecturers</option>
              {options.map(([id, name]) => (
                <option key={id} value={id}>
                  {name}
                </option>
              ))}
            </select>
          </label>
          <div className="flex items-end">
            <button type="submit" className={SUBMIT}>
              <Icon name="filter_alt_off" size={18} />
              Apply filters
            </button>
          </div>
        </Form>
        <div className="border-t border-line px-5 py-3">
          <VersionBadges version={version} />
        </div>
      </Card>

      {lecturer ? (
        <LecturerSessions name={lecturers.get(lecturer)?.name ?? options.find(([id]) => id === lecturer)?.[1] ?? lecturer}
          sessions={lecturers.get(lecturer)?.sessions ?? []} weeklyHref={weekly(lecturer)} />
      ) : (
        <Card title="Lecturers" description={`${lecturers.size} lecturer${lecturers.size === 1 ? "" : "s"} with ${typed.length} assigned session${typed.length === 1 ? "" : "s"} for this selection`}>
          {lecturers.size === 0 ? (
            <EmptyState icon="filter_alt_off" title="No sessions match">
              Nothing is scheduled for this year and session type in the selected timetable version.
            </EmptyState>
          ) : (
            <ul className="divide-y divide-line">
              {[...lecturers].sort((a, b) => a[1].name.localeCompare(b[1].name)).map(([id, entry]) => {
                const minutes = entry.sessions.reduce((sum, row) => sum + row.end_minute - row.start_minute, 0);
                const types = [...new Set(entry.sessions.map((row) => SESSION_TYPES[row.coverage[0]?.session_type ?? ""] ?? row.coverage[0]?.session_type))];
                const days = new Set(entry.sessions.map((row) => row.day)).size;
                return (
                  <li key={id} className="flex flex-col gap-2 px-5 py-3.5 sm:flex-row sm:items-center">
                    <div className="min-w-0 flex-1">
                      <Link href={`/academic/assigned-sessions${query({ semester, version: version.version_id, year, type, lecturer: id })}`}
                        className="font-semibold text-ink hover:text-primary-strong hover:underline">
                        {entry.name}
                      </Link>
                      <p className="text-[13px] text-muted">{types.join(", ")}</p>
                    </div>
                    <div className="flex flex-wrap items-center gap-2 text-[13px] text-muted">
                      <Badge tone="neutral">{entry.sessions.length} session{entry.sessions.length === 1 ? "" : "s"}</Badge>
                      <Badge tone="neutral">{hoursText(minutes / 60)}</Badge>
                      <Badge tone="neutral">{days} day{days === 1 ? "" : "s"}</Badge>
                      <Link href={weekly(id)} className="inline-flex items-center gap-1 font-semibold text-primary-strong hover:underline">
                        Weekly view
                        <Icon name="arrow_forward" size={16} />
                      </Link>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </Card>
      )}
    </>
  );
}

function LecturerSessions({ name, sessions, weeklyHref }: { name: string; sessions: TimetableSession[]; weeklyHref: string }) {
  const ordered = sessions.toSorted((a, b) => DAY_ORDER.indexOf(a.day) - DAY_ORDER.indexOf(b.day) || a.start_minute - b.start_minute);
  const minutes = ordered.reduce((sum, row) => sum + row.end_minute - row.start_minute, 0);
  return (
    <Card
      title={name}
      description={`${ordered.length} session${ordered.length === 1 ? "" : "s"} · ${hoursText(minutes / 60)} of teaching for this selection`}
      actions={
        <Link href={weeklyHref} className="inline-flex items-center gap-1 rounded-lg text-sm font-semibold text-primary-strong underline-offset-2 hover:underline">
          Open weekly timetable
          <Icon name="arrow_forward" size={16} />
        </Link>
      }
    >
      {ordered.length === 0 ? (
        <EmptyState icon="filter_alt_off" title="No sessions for this selection">
          This lecturer has no sessions for the chosen year and session type.
        </EmptyState>
      ) : (
        <>
          {/* Phones: one card per session. */}
          <ul className="divide-y divide-line md:hidden">
            {ordered.map((session) => (
              <li key={session.delivery_reference} className="px-4 py-3 text-[13px] leading-5">
                <p className="font-semibold text-ink">
                  {dayName(session.day)} {session.start_time}–{session.end_time}
                </p>
                <SessionCells session={session} stacked />
              </li>
            ))}
          </ul>
          {/* Tablets and up: a table. */}
          <table className="hidden w-full text-left text-sm md:table">
            <thead className="bg-background/70 text-xs font-semibold uppercase tracking-wider text-muted">
              <tr>
                {["Day and time", "Module", "Type", "Venue", "Mode", "Student groups"].map((heading) => (
                  <th key={heading} scope="col" className="px-5 py-3">
                    {heading}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {ordered.map((session) => (
                <tr key={session.delivery_reference}>
                  <td className="whitespace-nowrap px-5 py-3 font-semibold text-ink">
                    {dayName(session.day)} {session.start_time}–{session.end_time}
                  </td>
                  <SessionCells session={session} />
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </Card>
  );
}

function SessionCells({ session, stacked = false }: { session: TimetableSession; stacked?: boolean }) {
  const subject = session.coverage[0];
  const online = session.delivery_mode === "ONLINE";
  const groups = [...new Set(session.coverage.map((row) => groupLabel(row.group_id)))].join(", ");
  const cells = [
    <span key="module">
      <span className="font-semibold text-ink">{subject?.module_code}</span> {subject?.module_name}
    </span>,
    <span key="type">{SESSION_TYPES[subject?.session_type ?? ""] ?? subject?.session_type}</span>,
    <span key="venue">{online ? "Online" : (session.venue?.venue_name ?? "No venue")}</span>,
    online ? <Badge key="mode" tone="accent">Online</Badge> : <Badge key="mode" tone="primary">In person</Badge>,
    <span key="groups">{groups}</span>,
  ];
  if (stacked) {
    return (
      <div className="mt-1 space-y-1 text-muted">
        {cells.map((cell, index) => (
          <p key={index}>{cell}</p>
        ))}
      </div>
    );
  }
  return (
    <>
      {cells.map((cell, index) => (
        <td key={index} className="px-5 py-3 align-top text-ink">
          {cell}
        </td>
      ))}
    </>
  );
}
