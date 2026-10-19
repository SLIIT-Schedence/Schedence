import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";
import {
  Badge, Card, EmptyState, formatDate, formatDateTime, Kpi, PageHeader, Skeleton, TableSkeleton, ToneIcon,
} from "@/components/console/ui";
import { Icon } from "@/components/icon";
import { requireExaminationAdmin } from "@/lib/examination-access";
import {
  getExamRegistrationTotal, listExamModules, listExamPeriods, listExamVenues, listRecentExamTimetables,
} from "@/lib/examination";
import { ExaminationReadError } from "./_components/read-error";
import { RefreshButton } from "./_components/refresh-button";

export const metadata: Metadata = { title: "Examination overview" };

const number = new Intl.NumberFormat("en-GB");
const todayFormat = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Colombo", year: "numeric", month: "2-digit", day: "2-digit" });

export default function ExaminationOverviewPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Examination overview"
        description="Your examination periods, module catalog and saved timetables in one place."
        actions={<RefreshButton />}
      />
      <div className="grid gap-3 sm:grid-cols-2 sm:gap-4 xl:grid-cols-4">
        <Suspense fallback={<MetricSkeleton />}>
          <ModuleMetric />
        </Suspense>
        <Suspense fallback={<MetricSkeleton />}>
          <VenueMetric />
        </Suspense>
        <Suspense fallback={<MetricSkeleton />}>
          <RegistrationMetric />
        </Suspense>
        <Suspense fallback={<MetricSkeleton />}>
          <TimetableMetric />
        </Suspense>
      </div>
      <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
        <Card title="Exam periods" description="Upcoming and ongoing periods, followed by past periods.">
          <Suspense fallback={<TableSkeleton rows={4} />}>
            <Periods />
          </Suspense>
        </Card>
        <div className="space-y-6">
          <Card title="Examination seating" description="Capacity from the current venue seating rules.">
            <Suspense fallback={<div className="p-5"><Skeleton className="h-28 w-full" /></div>}>
              <Seating />
            </Suspense>
          </Card>
          <Card>
            <div className="p-5">
              <ToneIcon icon="edit_note" tone="primary" />
              <h2 className="mt-4 text-[15px] font-semibold text-ink">Generate a FINAL timetable</h2>
              <p className="mt-2 text-sm leading-6 text-muted">
                Run feasibility or workload optimization for a FINAL examination cycle. Review the generated sessions and validation results.
              </p>
              <Link href="/exams/generate" className="mt-4 inline-flex items-center gap-2 rounded-lg text-sm font-semibold text-primary-strong">
                Generate Timetable <Icon name="arrow_forward" size={18} />
              </Link>
            </div>
          </Card>
        </div>
      </div>
      <Card
        title="Recent timetables"
        description="The five most recently saved timetables."
        actions={
          <Link href="/exams/timetables" className="inline-flex items-center rounded-lg text-sm font-semibold text-primary-strong">
            Timetables <Icon name="chevron_right" size={16} />
          </Link>
        }
      >
        <Suspense fallback={<TableSkeleton rows={5} />}>
          <RecentTimetables />
        </Suspense>
      </Card>
    </div>
  );
}

function MetricSkeleton() {
  return <div aria-busy="true" aria-label="Loading overview count"><Skeleton className="h-36 rounded-2xl" /></div>;
}

async function ModuleMetric() {
  const result = await listExamModules();
  if (!result.ok) return <Card><ExaminationReadError error={result.error} what="modules" /></Card>;
  return (
    <Kpi icon="menu_book" tone="primary" label="Modules" value={number.format(result.data.length)}
      hint="Modules in the examination catalog" href="/exams/modules" />
  );
}

async function VenueMetric() {
  const result = await listExamVenues();
  if (!result.ok) return <Card><ExaminationReadError error={result.error} what="venues" /></Card>;
  return (
    <Kpi icon="meeting_room" tone="accent" label="Venues" value={number.format(result.data.length)}
      hint={`${number.format(result.data.filter((venue) => venue.available_for_exams).length)} with examination seating`}
      href="/exams/venues" />
  );
}

async function RegistrationMetric() {
  const result = await getExamRegistrationTotal();
  if (!result.ok) return <Card><ExaminationReadError error={result.error} what="registrations" /></Card>;
  return (
    <Kpi icon="how_to_reg" tone="primary" label="Registrations" value={number.format(result.data)}
      hint="Student-module registrations across the catalog" href="/exams/registrations" />
  );
}

async function TimetableMetric() {
  const result = await listRecentExamTimetables();
  if (!result.ok) return <Card><ExaminationReadError error={result.error} what="saved timetables" /></Card>;
  return (
    <Kpi icon="fact_check" tone="neutral" label="Saved timetables" value={number.format(result.data.total)}
      hint="Across all periods and statuses" href="/exams/timetables" />
  );
}

async function Periods() {
  await requireExaminationAdmin();
  const result = await listExamPeriods();
  if (!result.ok) return <ExaminationReadError error={result.error} what="exam periods" />;
  if (!result.data.length) return <EmptyState icon="history" title="No exam periods yet">Examination periods will appear here once they are configured.</EmptyState>;
  // Calendar dates are compared in the same timezone used throughout the existing consoles.
  const parts = todayFormat.formatToParts(new Date());
  const part = (type: Intl.DateTimeFormatPartTypes) => parts.find((item) => item.type === type)?.value;
  const today = `${part("year")}-${part("month")}-${part("day")}`;
  const periods = [...result.data].sort((a, b) => {
    const aPast = a.end_date < today;
    const bPast = b.end_date < today;
    if (aPast !== bPast) return aPast ? 1 : -1;
    return aPast ? b.end_date.localeCompare(a.end_date) : a.start_date.localeCompare(b.start_date);
  }).slice(0, 6);
  return (
    <>
      <ul className="divide-y divide-line">
        {periods.map((period) => (
          <li key={period.period_code} className="flex flex-wrap items-start justify-between gap-3 px-5 py-4">
            <div className="min-w-0 flex-1 basis-48">
              <p className="text-sm font-semibold text-ink">{period.period_name}</p>
              <p className="mt-1 break-all font-mono text-[11px] text-secondary-ink">{period.period_code}</p>
              <p className="mt-2 text-[13px] text-muted">{formatDate(period.start_date)} – {formatDate(period.end_date)}</p>
              <p className="mt-1 text-xs text-muted">{period.exam_kind === "MID" ? "Mid-semester" : "Final"} · Semester {period.semester} · {period.exam_days.length} exam days</p>
            </div>
            <Badge tone={period.end_date < today ? "neutral" : period.start_date > today ? "primary" : "accent"}>
              {period.end_date < today ? "Past" : period.start_date > today ? "Upcoming" : "Ongoing"}
            </Badge>
          </li>
        ))}
      </ul>
      <p className="border-t border-line px-5 py-3 text-xs text-muted">Showing {periods.length} of {number.format(result.data.length)} periods. Dates use Asia/Colombo time.</p>
    </>
  );
}

async function Seating() {
  const result = await listExamVenues();
  if (!result.ok) return <ExaminationReadError error={result.error} what="examination seating" />;
  if (!result.data.length) return <EmptyState icon="meeting_room" title="No venues yet">Seating capacity will appear when venues are available.</EmptyState>;
  const capacity = result.data.reduce((total, venue) => total + venue.exam_capacity, 0);
  return (
    <div className="p-5">
      <p className="text-3xl font-semibold tracking-tight text-ink">{number.format(capacity)} <span className="text-sm font-medium text-muted">exam seats</span></p>
      <p className="mt-2 text-sm text-muted">Across {number.format(result.data.filter((venue) => venue.available_for_exams).length)} venues with examination seating.</p>
      <p className="mt-4 rounded-xl bg-secondary-soft p-3 text-xs leading-5 text-secondary-ink">
        Configured capacity before date-specific venue availability checks.
      </p>
    </div>
  );
}

async function RecentTimetables() {
  const result = await listRecentExamTimetables();
  if (!result.ok) return <ExaminationReadError error={result.error} what="recent timetables" />;
  if (!result.data.items.length) return <EmptyState icon="fact_check" title="No saved timetables yet">Generation previews are temporary. Use Generate & Save Draft to keep a timetable for approval.</EmptyState>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[40rem] text-left text-sm">
        <caption className="sr-only">Most recently saved examination timetables</caption>
        <thead className="border-b border-line bg-background text-xs text-muted">
          <tr>
            <th scope="col" className="px-5 py-3 font-semibold">Exam period</th>
            <th scope="col" className="px-5 py-3 font-semibold">Draft</th>
            <th scope="col" className="px-5 py-3 font-semibold">Status</th>
            <th scope="col" className="px-5 py-3 font-semibold">Sessions</th>
            <th scope="col" className="px-5 py-3 font-semibold">Saved</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {result.data.items.map((timetable) => (
            <tr key={timetable.timetable_id}>
              <th scope="row" className="px-5 py-4 font-medium text-ink">
                <Link href={`/exams/timetables/${timetable.timetable_id}`} className="text-primary-strong underline-offset-2 hover:underline">{timetable.period_name}</Link>
                <span className="mt-1 block font-mono text-[11px] text-secondary-ink">{timetable.period_code}</span>
              </th>
              <td className="px-5 py-4 text-muted">#{timetable.draft_number}</td>
              <td className="px-5 py-4">
                <Badge tone={timetable.status === "PUBLISHED" ? "accent" : timetable.status === "APPROVED" ? "primary" : "neutral"}>
                  {timetable.status.charAt(0) + timetable.status.slice(1).toLowerCase()}
                </Badge>
              </td>
              <td className="px-5 py-4 text-muted">{number.format(timetable.summary.sessions)}</td>
              <td className="px-5 py-4 text-muted">{formatDateTime(timetable.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
