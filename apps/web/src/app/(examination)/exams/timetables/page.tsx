import type { Metadata } from "next";
import Form from "next/form";
import Link from "next/link";
import { type ReactNode, Suspense } from "react";
import { Badge, Card, EmptyState, formatDateTime, PageHeader, Pagination, query, TableSkeleton } from "@/components/console/ui";
import { Icon } from "@/components/icon";
import { requireExaminationAdmin } from "@/lib/examination-access";
import { listExamPeriods, listSavedTimetables } from "@/lib/examination";
import {
  mergeTimetablePages, readTimetableFilters, STATUS_LABELS, STATUS_TONES, TIMETABLE_PAGE_SIZE, TIMETABLE_STATUSES,
  timetableQueries, type FinalPeriodOption, type TimetableFilters,
} from "@/lib/examination-timetables";
import { ExaminationReadError } from "../_components/read-error";
import { RefreshButton } from "../_components/refresh-button";

export const metadata: Metadata = { title: "Saved timetables" };

type SearchParams = Promise<Record<string, string | string[] | undefined>>;
const number = new Intl.NumberFormat("en-GB");
const select = "h-10 w-full appearance-none rounded-xl border border-line bg-surface pl-3 pr-9 text-sm text-ink transition-colors hover:border-secondary/70 focus:border-primary";
const cell = "px-5 py-4 text-sm text-muted";

export default function SavedTimetablesPage({ searchParams }: { searchParams: SearchParams }) {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Saved timetables"
        description="FINAL timetables saved as drafts, and their approval and publication status. Temporary previews are never listed here."
        actions={<>
          <RefreshButton label="Refresh" />
          <Link href="/exams/generate" className="inline-flex min-h-10 items-center gap-2 rounded-xl bg-primary px-4 text-sm font-semibold text-white transition-colors hover:bg-primary-strong">
            <Icon name="save" size={18} />Generate &amp; save a draft
          </Link>
        </>}
      />
      <Card>
        <Suspense fallback={<TableSkeleton rows={6} />}>
          <TimetableList searchParams={searchParams} />
        </Suspense>
      </Card>
    </div>
  );
}

async function TimetableList({ searchParams }: { searchParams: SearchParams }) {
  await requireExaminationAdmin();
  const [params, periodResult] = await Promise.all([searchParams, listExamPeriods()]);
  if (!periodResult.ok) return <ExaminationReadError error={periodResult.error} what="examination cycles" />;
  const periods: FinalPeriodOption[] = periodResult.data.filter((period) => period.exam_kind === "FINAL")
    .sort((a, b) => a.semester - b.semester || a.start_date.localeCompare(b.start_date));
  const filters = readTimetableFilters(params, periods);
  const filtered = Boolean(filters.semester || filters.period || filters.status);
  const queries = timetableQueries(filters, periods);

  const bar = <FilterBar periods={periods} filters={filters} />;
  if (queries === null) {
    return <>{bar}<EmptyState icon="history" title={`No Semester ${filters.semester} FINAL cycle`} action={<ClearLink />}>
      No FINAL examination cycle is configured for this semester, so it has no saved timetables.
    </EmptyState></>;
  }
  const results = await Promise.all(queries.map((item) => listSavedTimetables(item)));
  const failed = results.find((result) => !result.ok);
  if (failed && !failed.ok) return <>{bar}<ExaminationReadError error={failed.error} what="saved timetables" /></>;
  const pages = results.flatMap((result) => (result.ok ? [result.data] : []));
  const { items, total } = mergeTimetablePages(pages, filters, queries.length > 1);
  const semesterOf = new Map(periods.map((period) => [period.period_code, period.semester]));
  const href = (page: number) => `/exams/timetables${query({
    semester: filters.semester, period: filters.period, status: filters.status, page,
  })}`;

  if (!total) {
    return <>{bar}{filtered
      ? <EmptyState icon="filter_alt_off" title="No timetables match these filters" action={<ClearLink />}>Try another semester, cycle or status.</EmptyState>
      : <EmptyState icon="fact_check" title="No saved timetables yet" action={
        <Link href="/exams/generate" className="inline-flex rounded-xl bg-primary px-4 py-2.5 text-sm font-semibold text-white hover:bg-primary-strong">Go to Generate Timetable</Link>}>
        Generate a FINAL timetable and choose Generate &amp; Save Draft. Saved drafts appear here for approval.
      </EmptyState>}</>;
  }
  if (!items.length) {
    return <>{bar}<EmptyState title="This page is empty" action={<Link href={href(1)} className="text-sm font-semibold text-primary-strong">Go to the first page</Link>}>
      There are fewer saved timetables than this page number.
    </EmptyState></>;
  }

  return (
    <>
      {bar}
      <div className="overflow-x-auto">
        <table className="w-full min-w-[64rem] text-left">
          <caption className="sr-only">Saved FINAL timetables, newest first</caption>
          <thead className="border-b border-line bg-background text-xs text-muted">
            <tr>{["Timetable", "Examination cycle", "Status", "Revision", "Created", "Summary", "Validation"].map((label) =>
              <th key={label} scope="col" className="px-5 py-3 font-semibold">{label}</th>)}</tr>
          </thead>
          <tbody className="divide-y divide-line">
            {items.map((timetable) => (
              <tr key={timetable.timetable_id}>
                <th scope="row" className="px-5 py-4 text-sm font-semibold text-ink">
                  <Link href={`/exams/timetables/${timetable.timetable_id}`} className="text-primary-strong underline-offset-2 hover:underline">
                    Draft #{timetable.draft_number}
                  </Link>
                  <span className="mt-1 block text-xs font-normal text-muted">{timetable.mode === "optimized" ? "Optimized" : "Feasibility"} · {timetable.solver_status}</span>
                </th>
                <td className={cell}>
                  <span className="font-medium text-ink">{timetable.period_name}</span>
                  <span className="mt-1 block font-mono text-[11px] text-secondary-ink">{timetable.period_code}</span>
                  <span className="block text-xs">Semester {semesterOf.get(timetable.period_code) ?? "—"} · FINAL</span>
                </td>
                <td className={cell}><Badge tone={STATUS_TONES[timetable.status]}>{STATUS_LABELS[timetable.status]}</Badge></td>
                <td className={`${cell} font-mono`}>{timetable.revision}</td>
                <td className={`${cell} whitespace-nowrap`}>{formatDateTime(timetable.created_at)}</td>
                <td className={cell}>
                  {number.format(timetable.summary.sessions)} sessions
                  <span className="block text-xs">{number.format(timetable.summary.exam_days_used)} exam days · {number.format(timetable.summary.seat_allocations)} seats</span>
                </td>
                <td className={cell}>{timetable.validation_passed ? "H1–H11 passed" : "Not passed"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Pagination page={filters.page} pageSize={TIMETABLE_PAGE_SIZE} total={total} hrefFor={href} />
    </>
  );
}

function FilterBar({ periods, filters }: { periods: FinalPeriodOption[]; filters: TimetableFilters }) {
  return (
    <Form action="/exams/timetables" className="grid gap-3 border-b border-line p-4 sm:grid-cols-2 sm:px-5 lg:grid-cols-[10rem_minmax(0,1fr)_10rem_auto_auto] lg:items-end">
      <Field label="Semester">
        <select name="semester" defaultValue={filters.semester ?? ""} className={select}>
          <option value="">All semesters</option>
          <option value="1">Semester 1 FINAL</option>
          <option value="2">Semester 2 FINAL</option>
        </select>
      </Field>
      <Field label="Examination cycle">
        <select name="period" defaultValue={filters.period ?? ""} className={select}>
          <option value="">All FINAL cycles</option>
          {periods.map((period) => <option key={period.period_code} value={period.period_code}>
            Semester {period.semester} · {period.period_name} ({period.period_code})
          </option>)}
        </select>
      </Field>
      <Field label="Status">
        <select name="status" defaultValue={filters.status ?? ""} className={select}>
          <option value="">All statuses</option>
          {TIMETABLE_STATUSES.map((status) => <option key={status} value={status}>{STATUS_LABELS[status]}</option>)}
        </select>
      </Field>
      <button type="submit" className="inline-flex h-10 items-center justify-center rounded-xl bg-primary px-4 text-sm font-semibold text-white transition-colors hover:bg-primary-strong">Apply</button>
      <ClearLink />
    </Form>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block text-xs font-semibold text-muted">
      {label}
      <span className="relative mt-1 block">
        {children}
        <Icon name="chevron_right" size={18} className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 rotate-90 text-secondary" />
      </span>
    </label>
  );
}

function ClearLink() {
  return <Link href="/exams/timetables" className="inline-flex h-10 items-center justify-center rounded-xl border border-line px-4 text-sm font-semibold text-ink hover:bg-background">Clear filters</Link>;
}
