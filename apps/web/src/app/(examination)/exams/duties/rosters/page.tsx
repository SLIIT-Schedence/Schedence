import type { Metadata } from "next";
import Form from "next/form";
import Link from "next/link";
import { Suspense } from "react";
import { Badge, Card, EmptyState, formatDateTime, PageHeader, Pagination, pageNumber, query, TableSkeleton } from "@/components/console/ui";
import { Icon } from "@/components/icon";
import { listDutyPeriods, listRosters, PERIOD_PATTERN } from "@/lib/duty-allocation";
import { fixed, ROSTER_STATUSES, titleCase, type RosterStatus } from "@/lib/duty-allocation-types";
import { RefreshButton } from "../../_components/refresh-button";
import {
  cell, DutyReadError, Field, first, headCell, Notice, number, primaryButton, RosterStatusBadge, secondaryButton, selectClass,
  type SearchParams,
} from "../_components/duty-ui";

export const metadata: Metadata = { title: "Duty rosters" };

const PAGE_SIZE = 20;

export default function RostersPage({ searchParams }: { searchParams: SearchParams }) {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Duty rosters"
        description="Saved rosters and their workflow: DRAFT, VALIDATED, APPROVED, PUBLISHED. Each step is checked again by the Examination Service."
        actions={<>
          <RefreshButton label="Refresh" />
          <Link href="/exams/duties/allocation" className={primaryButton}><Icon name="work" size={18} />Allocate duties</Link>
        </>}
      />
      <Card>
        <Suspense fallback={<TableSkeleton rows={6} />}>
          <RosterList searchParams={searchParams} />
        </Suspense>
      </Card>
    </div>
  );
}

async function RosterList({ searchParams }: { searchParams: SearchParams }) {
  const params = await searchParams;
  const periodParam = first(params.period);
  const period = periodParam && PERIOD_PATTERN.test(periodParam) ? periodParam : undefined;
  const status = ROSTER_STATUSES.find((s) => s === first(params.status)) as RosterStatus | undefined;
  const page = pageNumber(params.page);
  const [periods, result] = await Promise.all([listDutyPeriods(), listRosters(period, status, PAGE_SIZE, (page - 1) * PAGE_SIZE)]);

  const bar = (
    <Form action="/exams/duties/rosters" className="grid gap-3 border-b border-line p-4 sm:grid-cols-2 sm:px-5 lg:grid-cols-[minmax(0,1fr)_12rem_auto] lg:items-end">
      <Field label="Exam period">
        <select name="period" defaultValue={period ?? ""} className={selectClass}>
          <option value="">All periods</option>
          {periods.ok && periods.data.items.map((p) => <option key={p.period_code} value={p.period_code}>{p.period_name ?? p.period_code} ({p.period_code})</option>)}
        </select>
      </Field>
      <Field label="Status">
        <select name="status" defaultValue={status ?? ""} className={selectClass}>
          <option value="">All statuses</option>
          {ROSTER_STATUSES.map((s) => <option key={s} value={s}>{titleCase(s)}</option>)}
        </select>
      </Field>
      <div className="flex gap-2">
        <button type="submit" className={primaryButton}>Apply</button>
        <Link href="/exams/duties/rosters" className={secondaryButton}>Clear</Link>
      </div>
    </Form>
  );

  if (!result.ok) return <>{bar}<DutyReadError error={result.error} what="saved rosters" /></>;
  const { items, total, persistence } = result.data;
  const href = (target: number) => `/exams/duties/rosters${query({ period, status, page: target })}`;
  return (
    <>
      {bar}
      {!persistence.durable && <div className="border-b border-line p-4 sm:px-5"><Notice>{persistence.notice}</Notice></div>}
      {!items.length ? (
        period || status
          ? <EmptyState icon="filter_alt_off" title="No rosters match these filters">Try another period or status.</EmptyState>
          : <EmptyState icon="fact_check" title="No saved rosters yet" action={<Link href="/exams/duties/allocation" className={primaryButton}>Allocate duties</Link>}>
              Run an allocation and save it as a draft. Saved rosters appear here for validation and approval.
            </EmptyState>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[60rem] text-left">
            <caption className="sr-only">Saved duty rosters</caption>
            <thead className="border-b border-line bg-background text-xs text-muted">
              <tr>{["Roster", "Exam period", "Status", "Duties", "Fairness", "Validation", "Saved"].map((label) => <th key={label} scope="col" className={headCell}>{label}</th>)}</tr>
            </thead>
            <tbody className="divide-y divide-line">
              {items.map((r) => (
                <tr key={r.roster_id}>
                  <th scope="row" className="px-5 py-4 text-sm font-semibold text-ink">
                    <Link href={`/exams/duties/rosters/${r.roster_id}`} className="text-primary-strong underline-offset-2 hover:underline">Version {r.version}</Link>
                    <span className="mt-1 block text-xs font-normal text-muted">{r.roster_label === "FAIR" ? "Fair" : "Baseline"} · {r.algorithm_mode.toLowerCase()}</span>
                  </th>
                  <td className={cell}><span className="font-mono text-[12px] text-secondary-ink">{r.period_code}</span></td>
                  <td className={cell}>
                    <span className="flex flex-wrap gap-1.5"><RosterStatusBadge status={r.status} />{r.synthetic && <Badge tone="neutral">Synthetic</Badge>}</span>
                  </td>
                  <td className={`${cell} tabular-nums`}>{number.format(r.assignment_count)}</td>
                  <td className={`${cell} tabular-nums`}>Gini {fixed(r.fairness_metrics.gini)}</td>
                  <td className={cell}>{r.validation.valid === true ? "Passed" : r.validation.valid === false ? <span className="text-danger">{r.validation.violations.length} problems</span> : "Not checked"}</td>
                  <td className={`${cell} whitespace-nowrap`}>{formatDateTime(r.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <Pagination page={page} pageSize={PAGE_SIZE} total={total} hrefFor={href} />
    </>
  );
}
