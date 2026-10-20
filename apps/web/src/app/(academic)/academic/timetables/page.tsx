import type { Metadata } from "next";
import Form from "next/form";
import Link from "next/link";
import { redirect } from "next/navigation";
import { Suspense } from "react";
import { Badge, Card, EmptyState, first, LoadError, PageHeader, query, Skeleton, TableSkeleton } from "@/components/console/ui";
import { Icon } from "@/components/icon";
import { getAcademicContext } from "@/lib/academic";
import { loadTimetableView, STAGES, TEACHING_SERVICE, type TimetableView, type ViewParams } from "@/lib/teaching";
import { SessionText, WeeklyGrid } from "./_components/weekly-grid";

export const metadata: Metadata = { title: "Teaching timetables" };

type SearchParams = PageProps<"/academic/timetables">["searchParams"];

const KEYS = ["institution", "semester", "version", "year", "stream", "specialization", "group", "view", "lecturer", "venue"] as const;

export default function TeachingTimetablesPage({ searchParams }: PageProps<"/academic/timetables">) {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Teaching timetables"
        description="Validated teaching timetable versions for your institution: weekly views by student group, lecturer and venue."
      />
      <Suspense fallback={<ViewerSkeleton />}>
        <Viewer searchParams={searchParams} />
      </Suspense>
    </div>
  );
}

async function Viewer({ searchParams }: { searchParams: SearchParams }) {
  const context = await getAcademicContext();
  if (!context) redirect("/");
  const raw = await searchParams;
  const params = Object.fromEntries(KEYS.map((key) => [key, first(raw[key])?.slice(0, 300)])) as ViewParams & { institution?: string };
  const institution = context.institutions.find((row) => row.id === params.institution) ?? context.primary;

  const result = await loadTimetableView(institution.id, params);
  if (result.status === "error") {
    if (result.error.code === "PREVIEW_LOADING") {
      return (
        <Card>
          <EmptyState icon="schedule" title="Validating the saved timetables" action={<Retry href="/academic/timetables" />}>
            The teaching timetable service re-checks every saved timetable before showing it. This takes a minute or two
            after the service starts.
          </EmptyState>
        </Card>
      );
    }
    return (
      <Card>
        <LoadError error={result.error} what="teaching timetables" retryHref="/academic/timetables" service={TEACHING_SERVICE} />
      </Card>
    );
  }
  if (result.status === "empty") {
    return (
      <Card>
        <EmptyState icon="calendar_month" title="No validated timetables yet">
          {institution.name} has no teaching timetable version that has passed independent validation. Versions appear
          here once one is generated and validated for this institution.
        </EmptyState>
      </Card>
    );
  }

  const view = result.view;
  const hrefWith = (changes: Partial<Record<(typeof KEYS)[number], string | number>>) =>
    `/academic/timetables${query({
      institution: context.institutions.length > 1 ? institution.id : undefined,
      semester: view.semester,
      version: view.version.version_id,
      year: view.year,
      stream: view.stream,
      specialization: view.specialization,
      group: view.group,
      view: view.view,
      lecturer: view.lecturer,
      venue: view.venue,
      ...changes,
    })}`;

  return (
    <>
      {view.version.preview_label && (
        <p className="flex gap-2 rounded-2xl border border-danger/20 bg-danger-soft px-4 py-3 text-sm leading-6 text-danger">
          <Icon name="info" size={20} filled className="mt-0.5" />
          <span>
            <strong>{view.version.preview_label}.</strong> A local development preview generated with provisional
            scheduling settings. It is not an approved or published university timetable.
          </span>
        </p>
      )}

      <Card>
        <Filters view={view} institutionId={context.institutions.length > 1 ? institution.id : undefined} />
      </Card>

      <StatusCard view={view} />

      <Card
        title={heading(view)}
        description={`${view.sessions.length} session${view.sessions.length === 1 ? "" : "s"} in the week`}
        actions={
          view.validation.ok && view.validation.data.status === "PASSED" && view.sessions.length > 0 ? (
            <a
              href={`/academic/timetables/export${hrefWith({}).slice("/academic/timetables".length)}`}
              className="inline-flex h-9 items-center gap-2 rounded-xl border border-line bg-surface px-3 text-sm font-semibold text-ink transition-colors hover:bg-background"
            >
              <Icon name="download" size={18} />
              CSV
            </a>
          ) : undefined
        }
      >
        {view.sessions.length === 0 ? (
          <EmptyState icon="calendar_month" title="No sessions for this selection">
            {view.view === "group" && !view.group
              ? "This year and stream have no student groups in the selected timetable version."
              : "Nothing is scheduled for this selection in the selected timetable version."}
          </EmptyState>
        ) : (
          <WeeklyGrid version={view.version} sessions={view.sessions} />
        )}
      </Card>

      {view.sessions.length > 0 && (
        <Card title="Session details" className="hidden md:block">
          <ul className="divide-y divide-line">
            {view.sessions.map((session) => (
              <li key={session.delivery_reference} className="flex gap-4 px-5 py-3">
                <span className="w-24 shrink-0 text-[13px] font-semibold text-ink">
                  {session.day.charAt(0) + session.day.slice(1, 3).toLowerCase()} {session.start_time}
                </span>
                <SessionText session={session} />
                <span className="ml-auto shrink-0">
                  {session.delivery_mode === "ONLINE" ? <Badge tone="accent">Online</Badge> : <Badge tone="primary">In person</Badge>}
                </span>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </>
  );
}

function heading(view: TimetableView) {
  if (view.view === "lecturer") return `Lecturer: ${view.lecturers.find((row) => row.value === view.lecturer)?.label ?? "—"}`;
  if (view.view === "venue") return `Venue: ${view.venues.find((row) => row.value === view.venue)?.label ?? "—"}`;
  return `Year ${view.year} · ${view.stream === "WEEKEND" ? "Weekend" : "Weekday"} · ${view.groups.find((row) => row.value === view.group)?.label ?? "No group"}`;
}

function Filters({ view, institutionId }: { view: TimetableView; institutionId?: string }) {
  const select =
    "h-10 w-full rounded-xl border border-line bg-surface px-3 text-sm text-ink transition-colors hover:border-secondary/70 focus:border-primary";
  const label = "block text-[13px] font-semibold text-muted";
  return (
    <Form action="/academic/timetables" className="grid gap-3 p-4 sm:grid-cols-2 sm:px-5 lg:grid-cols-4 xl:grid-cols-5">
      {institutionId && <input type="hidden" name="institution" value={institutionId} />}
      <label className={label}>
        Semester
        <select name="semester" defaultValue={view.semester} className={`mt-1 ${select}`}>
          {view.semesters.map((semester) => (
            <option key={semester} value={semester}>
              Semester {semester}
            </option>
          ))}
        </select>
      </label>
      {view.semesterVersions.length > 1 && (
        <label className={label}>
          Timetable version
          <select name="version" defaultValue={view.version.version_id} className={`mt-1 ${select}`}>
            {view.semesterVersions.map((version) => (
              <option key={version.version_id} value={version.version_id}>
                {versionLabel(version.scopes.filter((scope) => scope.semester === view.semester).map((scope) => scope.academic_year))} ·{" "}
                {version.solver_status.toLowerCase()}
              </option>
            ))}
          </select>
        </label>
      )}
      <label className={label}>
        View
        <select name="view" defaultValue={view.view} className={`mt-1 ${select}`}>
          <option value="group">Student group</option>
          <option value="lecturer">Lecturer</option>
          <option value="venue">Venue</option>
        </select>
      </label>
      <label className={label}>
        Academic year
        <select name="year" defaultValue={view.year} className={`mt-1 ${select}`}>
          {view.years.map((year) => (
            <option key={year} value={year}>
              Year {year}
            </option>
          ))}
        </select>
      </label>
      <label className={label}>
        Stream
        <select name="stream" defaultValue={view.stream} className={`mt-1 ${select}`}>
          {view.streams.map((stream) => (
            <option key={stream} value={stream}>
              {stream === "WEEKEND" ? "Weekend" : "Weekday"}
            </option>
          ))}
        </select>
      </label>
      {view.specializations.length > 1 && (
        <label className={label}>
          Specialization or track
          <select name="specialization" defaultValue={view.specialization} className={`mt-1 ${select}`}>
            {view.specializations.map((name) => (
              <option key={name} value={name}>
                {name.replaceAll("_", " ")}
              </option>
            ))}
          </select>
        </label>
      )}
      <label className={label}>
        Student group
        <select name="group" defaultValue={view.group} className={`mt-1 ${select}`}>
          {view.groups.map((group) => (
            <option key={group.value} value={group.value}>
              {group.label}
            </option>
          ))}
        </select>
      </label>
      <label className={label}>
        Lecturer
        <select name="lecturer" defaultValue={view.lecturer} className={`mt-1 ${select}`}>
          {view.lecturers.map((row) => (
            <option key={row.value} value={row.value}>
              {row.label}
            </option>
          ))}
        </select>
      </label>
      <label className={label}>
        Venue
        <select name="venue" defaultValue={view.venue} className={`mt-1 ${select}`}>
          {view.venues.map((row) => (
            <option key={row.value} value={row.value}>
              {row.label}
            </option>
          ))}
        </select>
      </label>
      <div className="flex items-end">
        <button
          type="submit"
          className="inline-flex h-10 w-full items-center justify-center gap-2 rounded-xl bg-primary px-4 text-sm font-semibold text-white transition-colors hover:bg-primary-strong"
        >
          <Icon name="calendar_month" size={18} />
          Show timetable
        </button>
      </div>
      <p className="text-[13px] leading-5 text-muted sm:col-span-2 lg:col-span-4 xl:col-span-5">
        Academic year, stream, specialization and student group choose the student-group view. The lecturer and venue
        views show that person or room across the whole semester.
      </p>
    </Form>
  );
}

function versionLabel(years: number[]) {
  const sorted = [...new Set(years)].sort();
  return sorted.length === 1 ? `Year ${sorted[0]} only` : `Years ${sorted[0]}–${sorted[sorted.length - 1]}`;
}

function StatusCard({ view }: { view: TimetableView }) {
  const { version, validation } = view;
  const optimization = validation.ok ? validation.data.optimization : undefined;
  const planned = optimization?.priority_order.length ?? 0;
  const ran = new Map(optimization?.stages.map((row) => [row.criterion, row]) ?? []);
  const calendar = version.calendar;
  return (
    <Card title="Generation and validation" description={`Version ${version.version_number} · ${version.institution_policy_revision ?? "policy not recorded"}`}>
      <div className="grid gap-4 px-5 py-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
        <div className="space-y-3">
          <div className="flex flex-wrap gap-2">
            {version.solver_status === "OPTIMAL" ? (
              <Badge tone="accent">Solver: optimal</Badge>
            ) : (
              <Badge tone="primary">Solver: {version.solver_status.toLowerCase()} — not proven optimal</Badge>
            )}
            {validation.ok ? (
              validation.data.status === "PASSED" ? (
                <Badge tone="accent">Independent validation passed</Badge>
              ) : (
                <Badge tone="danger">Independent validation failed</Badge>
              )
            ) : (
              <Badge tone="neutral">Validation report unavailable</Badge>
            )}
            {!version.academic_complete && <Badge tone="neutral">Partial academic coverage</Badge>}
            <Badge tone="neutral">Not published</Badge>
          </div>
          {validation.ok && (
            <dl className="grid grid-cols-2 gap-3 text-sm">
              <div>
                <dt className="text-[13px] text-muted">Sessions scheduled</dt>
                <dd className="font-semibold text-ink">
                  {validation.data.covered_occurrences} of {validation.data.expected_occurrences}
                </dd>
              </div>
              <div>
                <dt className="text-[13px] text-muted">Hard-rule violations</dt>
                <dd className="font-semibold text-ink">{validation.data.hard_violations}</dd>
              </div>
              {optimization && (
                <div>
                  <dt className="text-[13px] text-muted">Optimization stages proven</dt>
                  <dd className="font-semibold text-ink">
                    {optimization.proven_stage_count} of {planned}
                  </dd>
                </div>
              )}
              <div>
                <dt className="text-[13px] text-muted">Calendar</dt>
                <dd className="font-semibold text-ink">{calendar.synthetic_data ? "Reference week" : calendar.label}</dd>
              </div>
            </dl>
          )}
          <ul className="space-y-1.5 text-[13px] leading-5 text-muted">
            {calendar.synthetic_data && (
              <li className="flex gap-2">
                <Icon name="info" size={16} className="mt-0.5" />
                Dated on a synthetic reference week ({calendar.week_start} to {calendar.week_end}), not the actual academic term.
              </li>
            )}
            {validation.ok && validation.data.research_pending && (
              <li className="flex gap-2">
                <Icon name="info" size={16} className="mt-0.5" />
                Compulsory Year 4 research has no session mapping yet and is not scheduled.
              </li>
            )}
            {validation.ok && validation.data.blocked_scopes.length > 0 && (
              <li className="flex gap-2">
                <Icon name="info" size={16} className="mt-0.5" />
                Not scheduled (missing academic mapping):{" "}
                {validation.data.blocked_scopes.map((row) => `Year ${row.academic_year} ${row.specialization} ${row.stream.toLowerCase()}`).join(", ")}.
              </li>
            )}
          </ul>
        </div>
        {optimization && (
          <div className="min-w-0">
            <p className="text-[13px] font-semibold text-muted">Objective stages, in priority order</p>
            <ol className="mt-2 space-y-1">
              {optimization.priority_order.map((criterion) => {
                const stage = ran.get(criterion);
                return (
                  <li key={criterion} className="flex items-center justify-between gap-3 text-[13px]">
                    <span className="truncate text-ink">{STAGES[criterion] ?? criterion}</span>
                    {stage?.status === "OPTIMAL" ? (
                      <Badge tone="accent">Proven</Badge>
                    ) : stage ? (
                      <Badge tone="primary">{stage.status === "FEASIBLE" ? "Feasible, not proven" : stage.status.toLowerCase()}</Badge>
                    ) : (
                      <Badge tone="neutral">Not reached</Badge>
                    )}
                  </li>
                );
              })}
            </ol>
          </div>
        )}
      </div>
    </Card>
  );
}

function Retry({ href }: { href: string }) {
  return (
    <Link
      href={href}
      prefetch={false}
      className="inline-flex h-10 items-center gap-2 rounded-xl border border-line bg-surface px-4 text-sm font-semibold text-ink transition-colors hover:bg-background"
    >
      <Icon name="refresh" size={18} />
      Try again
    </Link>
  );
}

function ViewerSkeleton() {
  return (
    <div className="space-y-6" aria-busy="true" aria-label="Loading teaching timetables">
      <Skeleton className="h-28 rounded-2xl" />
      <Skeleton className="h-40 rounded-2xl" />
      <Card title="Weekly timetable">
        <TableSkeleton rows={6} />
      </Card>
    </div>
  );
}
