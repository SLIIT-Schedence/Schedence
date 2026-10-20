import type { Metadata } from "next";
import Form from "next/form";
import Link from "next/link";
import { redirect } from "next/navigation";
import { Suspense } from "react";
import { Badge, Card, EmptyState, first, LoadError, PageHeader, query, Skeleton, TableSkeleton } from "@/components/console/ui";
import { Icon, type IconName } from "@/components/icon";
import { getAcademicContext } from "@/lib/academic";
import { loadTimetableView, STAGES, TEACHING_SERVICE, type TimetableView, type ViewMode, type ViewParams } from "@/lib/teaching";
import { HISTORICAL_PREVIEW, isHistoricalPreview } from "@/lib/teaching-preview";
import { PreviewLink, PreviewNotice, TeachingUnavailable } from "../_teaching/teaching-ui";
import { SessionText, WeeklyGrid } from "./_components/weekly-grid";

export const metadata: Metadata = { title: "Teaching timetables" };

type SearchParams = PageProps<"/academic/timetables">["searchParams"];

const KEYS = ["institution", "semester", "version", "year", "stream", "specialization", "group", "view", "lecturer", "venue", "source"] as const;

const VIEWS: { value: ViewMode; label: string; icon: IconName }[] = [
  { value: "group", label: "Student group", icon: "group" },
  { value: "lecturer", label: "Lecturer", icon: "badge" },
  { value: "venue", label: "Venue", icon: "meeting_room" },
];

const SELECT =
  "h-10 w-full rounded-xl border border-line bg-surface px-3 text-sm text-ink transition-colors hover:border-secondary/70 focus:border-primary";
const LABEL = "block min-w-0 text-[13px] font-semibold text-muted";
const plural = (count: number, word: string) => `${count} ${word}${count === 1 ? "" : "s"}`;

export default function TeachingTimetablesPage({ searchParams }: PageProps<"/academic/timetables">) {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Teaching timetables"
        description="Validated teaching timetable versions for your institution: weekly views by student group, lecturer and venue."
        actions={
          <Link
            href="/academic/timetables/generate"
            className="inline-flex h-10 items-center gap-2 rounded-xl bg-primary px-4 text-sm font-semibold text-white transition-colors hover:bg-primary-strong"
          >
            <Icon name="event_repeat" size={18} />
            Generate timetable
          </Link>
        }
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
  const params = Object.fromEntries(KEYS.map((key) => [key, first(raw[key])?.slice(0, 300)])) as ViewParams & { institution?: string; source?: string };
  const institution = context.institutions.find((row) => row.id === params.institution) ?? context.primary;
  // The Historical Experimental Preview is read from its saved export, never as this institution's data.
  const source = isHistoricalPreview(params.source) ? HISTORICAL_PREVIEW : undefined;

  const result = await loadTimetableView(source ?? institution.id, params);
  if (source && result.status !== "ok") {
    return <TeachingUnavailable status={result.status} error={result.status === "error" ? result.error : undefined} institution={institution.name} retryHref={`/academic/timetables?source=${source}`} />;
  }
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
        <div className="flex justify-center pb-8">
          <PreviewLink href={`/academic/timetables?source=${HISTORICAL_PREVIEW}`} />
        </div>
      </Card>
    );
  }
  if (result.status === "empty") {
    return (
      <Card>
        <EmptyState icon="calendar_month" title="No validated timetables yet" action={<PreviewLink href={`/academic/timetables?source=${HISTORICAL_PREVIEW}`} />}>
          {institution.name} has no teaching timetable version that has passed independent validation. Versions appear
          here once one is generated and validated for this institution.
        </EmptyState>
      </Card>
    );
  }

  const view = result.view;
  const institutionId = context.institutions.length > 1 ? institution.id : undefined;
  const hrefWith = (changes: Partial<Record<(typeof KEYS)[number], string | number>>) =>
    `/academic/timetables${query({
      institution: institutionId,
      semester: view.semester,
      version: view.version.version_id,
      year: view.year,
      stream: view.stream,
      specialization: view.specialization,
      group: view.group,
      view: view.view,
      lecturer: view.lecturer,
      venue: view.venue,
      source,
      ...changes,
    })}`;
  const exportable = view.validation.ok && view.validation.data.status === "PASSED" && view.sessions.length > 0;

  return (
    <>
      {source ? (
        <PreviewNotice
          institution={institution.name}
          backHref="/academic/timetables"
          versionId={view.version.version_id}
          semester={view.semester}
          year={view.view === "group" ? view.year : undefined}
        />
      ) : (
        view.version.preview_label && (
          <p className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-2xl border border-danger/20 bg-danger-soft px-4 py-2.5 text-sm text-danger">
            <Icon name="info" size={18} filled />
            <strong>{view.version.preview_label}</strong>
            <span className="text-[13px]">· provisional settings · not an approved or published timetable</span>
          </p>
        )
      )}

      <Card>
        <ViewTabs view={view} hrefWith={hrefWith} />
        <Filters view={view} institutionId={institutionId} source={source} />
      </Card>

      <StatusSummary view={view} />

      <Card
        title={heading(view)}
        description={countLine(view)}
        actions={
          exportable ? (
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
        {view.sessions.length === 0 && view.view === "group" && blockedScope(view) ? (
          <EmptyState icon="block" title="Not scheduled in this version">
            Year {view.year} {view.specialization.replaceAll("_", " ")} ({view.stream === "WEEKEND" ? "weekend" : "weekday"}) has no
            session mapping in its academic inputs ({blockedScope(view)?.replaceAll("_", " ").toLowerCase()}), so it was left out
            of scheduling rather than guessed. Its students are still counted.
          </EmptyState>
        ) : view.sessions.length === 0 ? (
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
        <Card className="hidden md:block">
          <details className="group">
            <summary className="flex cursor-pointer list-none items-center gap-2 px-5 py-4 text-sm font-semibold text-ink [&::-webkit-details-marker]:hidden">
              <Icon name="chevron_right" size={18} className="transition-transform group-open:rotate-90" />
              Session details
              <span className="font-normal text-muted">· {plural(view.sessions.length, "session")}</span>
            </summary>
            <ul className="divide-y divide-line border-t border-line">
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
          </details>
        </Card>
      )}
    </>
  );
}

/** The selected year/stream/specialization's scope status when it was not scheduled (for example a missing mapping). */
function blockedScope(view: TimetableView) {
  const scope = view.version.scopes.find(
    (row) => row.semester === view.semester && row.academic_year === view.year && row.stream === view.stream && row.specialization === view.specialization,
  );
  return scope && scope.scope_status !== "SUPPORTED" ? scope.scope_status : undefined;
}

function heading(view: TimetableView) {
  if (view.view === "lecturer") return `Lecturer: ${view.lecturers.find((row) => row.value === view.lecturer)?.label ?? "—"}`;
  if (view.view === "venue") return `Venue: ${view.venues.find((row) => row.value === view.venue)?.label ?? "—"}`;
  return `Year ${view.year} · ${view.stream === "WEEKEND" ? "Weekend" : "Weekday"} · ${view.groups.find((row) => row.value === view.group)?.label ?? "No group"}`;
}

/** What the shown count is, against the selected year and the whole semester, so the three are never confused. */
function countLine(view: TimetableView) {
  const shown = plural(view.sessions.length, "session");
  const semester = `${view.semesterSessionCount} in Semester ${view.semester}`;
  if (view.view === "group") {
    return `${shown} for this group (including its combined and parent-group sessions) · Year ${view.year}: ${view.yearSessionCounts[view.year] ?? 0} · ${semester}`;
  }
  return `${shown} for this ${view.view} across Semester ${view.semester} · ${semester} (all years)`;
}

function ViewTabs({ view, hrefWith }: { view: TimetableView; hrefWith: (changes: { view: ViewMode }) => string }) {
  return (
    <nav aria-label="Timetable view" className="border-b border-line px-4 pt-4 sm:px-5">
      <ul className="-mb-px flex gap-1 overflow-x-auto">
        {VIEWS.map((tab) => {
          const active = tab.value === view.view;
          return (
            <li key={tab.value} className="shrink-0">
              <Link
                href={hrefWith({ view: tab.value })}
                prefetch={false}
                aria-current={active ? "page" : undefined}
                className={`inline-flex h-10 items-center gap-2 border-b-2 px-3 text-sm font-semibold transition-colors ${
                  active ? "border-primary text-primary-strong" : "border-transparent text-muted hover:text-ink"
                }`}
              >
                <Icon name={tab.icon} size={18} />
                {tab.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

/** Only the selects the chosen view uses; the other views' selections ride along hidden so switching keeps them. */
function Filters({ view, institutionId, source }: { view: TimetableView; institutionId?: string; source?: string }) {
  const hidden: Record<string, string | number | undefined> = {
    institution: institutionId,
    source,
    view: view.view,
    ...(view.view === "group" ? {} : { year: view.year, stream: view.stream, specialization: view.specialization, group: view.group }),
    ...(view.view === "lecturer" ? {} : { lecturer: view.lecturer }),
    ...(view.view === "venue" ? {} : { venue: view.venue }),
  };
  const versions = view.semesterVersions.length > 1;
  return (
    <Form action="/academic/timetables" className="space-y-3 p-4 sm:px-5">
      {Object.entries(hidden).map(([name, value]) =>
        value === undefined || value === "" ? null : <input key={name} type="hidden" name={name} value={value} />,
      )}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-[repeat(auto-fit,minmax(10rem,1fr))]">
        <label className={LABEL}>
          Semester
          <select name="semester" defaultValue={view.semester} className={`mt-1 ${SELECT}`}>
            {view.semesters.map((semester) => (
              <option key={semester} value={semester}>
                Semester {semester}
              </option>
            ))}
          </select>
        </label>
        {view.view === "group" && (
          <>
            <label className={LABEL}>
              Academic year
              <select name="year" defaultValue={view.year} className={`mt-1 ${SELECT}`}>
                {view.years.map((year) => (
                  <option key={year} value={year}>
                    Year {year}
                  </option>
                ))}
              </select>
            </label>
            <label className={LABEL}>
              Stream
              <select name="stream" defaultValue={view.stream} className={`mt-1 ${SELECT}`}>
                {view.streams.map((stream) => (
                  <option key={stream} value={stream}>
                    {stream === "WEEKEND" ? "Weekend" : "Weekday"}
                  </option>
                ))}
              </select>
            </label>
            {view.specializations.length > 1 && (
              <label className={LABEL}>
                Specialization or track
                <select name="specialization" defaultValue={view.specialization} className={`mt-1 ${SELECT}`}>
                  {view.specializations.map((name) => (
                    <option key={name} value={name}>
                      {name.replaceAll("_", " ")}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <label className={LABEL}>
              Student group
              <select name="group" defaultValue={view.group} className={`mt-1 ${SELECT}`}>
                {view.groups.map((group) => (
                  <option key={group.value} value={group.value}>
                    {group.label}
                  </option>
                ))}
              </select>
            </label>
          </>
        )}
        {view.view === "lecturer" && (
          <label className={`${LABEL} sm:col-span-1 lg:col-span-2`}>
            Lecturer
            <select name="lecturer" defaultValue={view.lecturer} className={`mt-1 ${SELECT}`}>
              {view.lecturers.map((row) => (
                <option key={row.value} value={row.value}>
                  {row.label}
                </option>
              ))}
            </select>
          </label>
        )}
        {view.view === "venue" && (
          <label className={`${LABEL} sm:col-span-1 lg:col-span-2`}>
            Venue
            <select name="venue" defaultValue={view.venue} className={`mt-1 ${SELECT}`}>
              {view.venues.map((row) => (
                <option key={row.value} value={row.value}>
                  {row.label}
                </option>
              ))}
            </select>
          </label>
        )}
        <div className="flex items-end">
          <button
            type="submit"
            className="inline-flex h-10 w-full items-center justify-center gap-2 rounded-xl bg-primary px-4 text-sm font-semibold text-white transition-colors hover:bg-primary-strong"
          >
            <Icon name="calendar_month" size={18} />
            Show timetable
          </button>
        </div>
      </div>

      <details className="group" open={versions && view.semesterVersions[0].version_id !== view.version.version_id}>
        <summary className="inline-flex cursor-pointer list-none items-center gap-1 text-[13px] font-semibold text-muted hover:text-ink [&::-webkit-details-marker]:hidden">
          <Icon name="chevron_right" size={16} className="transition-transform group-open:rotate-90" />
          Advanced options
          <span className="font-normal">· {versionLabel(view, view.version.version_id)}</span>
        </summary>
        <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {versions ? (
            <label className={LABEL}>
              Timetable version
              <select name="version" defaultValue={view.version.version_id} className={`mt-1 ${SELECT}`}>
                {view.semesterVersions.map((version) => (
                  <option key={version.version_id} value={version.version_id}>
                    {versionLabel(view, version.version_id)}
                  </option>
                ))}
              </select>
            </label>
          ) : (
            <>
              <input type="hidden" name="version" value={view.version.version_id} />
              <p className="text-[13px] leading-5 text-muted sm:col-span-2">This semester has one validated timetable version.</p>
            </>
          )}
          <p className="text-[13px] leading-5 text-muted sm:col-span-2 lg:col-span-2">
            The student-group view shows one group&apos;s week, including sessions shared with its parent group and lab subgroups.
            The lecturer and venue views show that person or room across the whole semester.
          </p>
        </div>
      </details>
    </Form>
  );
}

function versionLabel(view: TimetableView, versionId: string) {
  const version = view.semesterVersions.find((row) => row.version_id === versionId) ?? view.version;
  const years = [...new Set(version.scopes.filter((scope) => scope.semester === view.semester).map((scope) => scope.academic_year))].sort();
  const range = years.length === 1 ? `Year ${years[0]} only` : `Years ${years[0]}–${years[years.length - 1]}`;
  return `${range} · ${version.solver_status.toLowerCase()} · version ${version.version_number}`;
}

/** One line of status; calendar notes, unscheduled scopes and the objective stages expand on demand. */
function StatusSummary({ view }: { view: TimetableView }) {
  const { version, validation } = view;
  const report = validation.ok ? validation.data : undefined;
  const optimization = report?.optimization;
  const ran = new Map(optimization?.stages.map((row) => [row.criterion, row]) ?? []);
  const calendar = version.calendar;
  const notes = [
    calendar.synthetic_data && `Dated on a synthetic reference week (${calendar.week_start} to ${calendar.week_end}), not the actual academic term.`,
    report?.research_pending && "Compulsory Year 4 research has no session mapping yet and is not scheduled.",
    report && report.blocked_scopes.length > 0 &&
      `Not scheduled (missing academic mapping): ${report.blocked_scopes.map((row) => `Year ${row.academic_year} ${row.specialization} ${row.stream.toLowerCase()}`).join(", ")}.`,
  ].filter((note): note is string => Boolean(note));

  return (
    <Card>
      <details className="group">
        <summary className="flex cursor-pointer list-none flex-wrap items-center gap-2 px-5 py-4 [&::-webkit-details-marker]:hidden">
          <span className="mr-1 inline-flex items-center gap-1.5 text-sm font-semibold text-ink">
            <Icon name="chevron_right" size={18} className="transition-transform group-open:rotate-90" />
            Generation and validation
          </span>
          {report ? (
            report.status === "PASSED" ? <Badge tone="accent">Validation passed</Badge> : <Badge tone="danger">Validation failed</Badge>
          ) : (
            <Badge tone="neutral">Validation report unavailable</Badge>
          )}
          {version.solver_status === "OPTIMAL" ? (
            <Badge tone="accent">Solver: optimal</Badge>
          ) : (
            <Badge tone="primary">Solver: {version.solver_status.toLowerCase()}</Badge>
          )}
          {report && (
            <Badge tone="neutral">
              Whole version: {report.covered_occurrences} of {report.expected_occurrences} sessions · {plural(report.hard_violations, "hard violation")}
            </Badge>
          )}
          {!version.academic_complete && <Badge tone="neutral">Partial academic coverage</Badge>}
          <Badge tone="neutral">Not published</Badge>
        </summary>

        <div className="grid gap-5 border-t border-line px-5 py-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
          <div className="space-y-3">
            <dl className="grid grid-cols-2 gap-3 text-sm">
              <Fact label="Version" value={`${version.version_number} · ${version.institution_policy_revision ?? "policy not recorded"}`} />
              <Fact label="Calendar" value={calendar.synthetic_data ? "Reference week" : calendar.label} />
              {report && <Fact label="Sessions scheduled (whole version)" value={`${report.covered_occurrences} of ${report.expected_occurrences}`} />}
              {report && <Fact label="Hard-rule violations" value={String(report.hard_violations)} />}
              <Fact label={`Sessions in Semester ${view.semester}`} value={String(view.semesterSessionCount)} />
              <Fact
                label="By academic year"
                value={view.years.map((year) => `Y${year}: ${view.yearSessionCounts[year] ?? 0}`).join(" · ")}
              />
              {optimization && (
                <Fact label="Optimization stages proven" value={`${optimization.proven_stage_count} of ${optimization.priority_order.length}`} />
              )}
            </dl>
            {notes.length > 0 && (
              <ul className="space-y-1.5 text-[13px] leading-5 text-muted">
                {notes.map((note) => (
                  <li key={note} className="flex gap-2">
                    <Icon name="info" size={16} className="mt-0.5" />
                    {note}
                  </li>
                ))}
              </ul>
            )}
            <p className="text-[13px] leading-5 text-muted">
              A session shared by several years counts once in the semester and once in each year it serves.
            </p>
          </div>
          {optimization ? (
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
          ) : (
            <p className="text-[13px] leading-5 text-muted">No objective stages are stored for this version; it was solved for feasibility.</p>
          )}
        </div>
      </details>
    </Card>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-[13px] text-muted">{label}</dt>
      <dd className="break-words font-semibold text-ink">{value}</dd>
    </div>
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
      <Skeleton className="h-10 rounded-2xl" />
      <Skeleton className="h-36 rounded-2xl" />
      <Skeleton className="h-14 rounded-2xl" />
      <Card title="Weekly timetable">
        <TableSkeleton rows={6} />
      </Card>
    </div>
  );
}
