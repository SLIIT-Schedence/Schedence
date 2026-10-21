import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";
import { Badge, Card, EmptyState, formatDateTime, Kpi, PageHeader, Skeleton, TableSkeleton } from "@/components/console/ui";
import { Icon, type IconName } from "@/components/icon";
import { getDutyStatus, getRequirements, listDutySessions, listDutyStaff, listRosters, resolvePeriod } from "@/lib/duty-allocation";
import { COMPONENT_NAME, WORKING_STATUSES } from "@/lib/duty-allocation-types";
import { RefreshButton } from "../../_components/refresh-button";
import { cell, DataNotice, DutyReadError, headCell, number, periodLabel, RosterStatusBadge } from "../_components/duty-ui";

export const metadata: Metadata = { title: "Staff duty allocation" };

export default function DutyOverviewPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Staff duty allocation"
        description={`${COMPONENT_NAME}: assign supervisors and invigilators to every exam room, check fairness, then save, approve and publish the roster.`}
        actions={<RefreshButton label="Refresh" />}
      />
      <Suspense fallback={<MetricsSkeleton />}>
        <PeriodMetrics />
      </Suspense>
      <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
        <Card
          title="Recent rosters"
          description="Saved duty rosters, newest first."
          actions={<Link href="/exams/duties/rosters" className="inline-flex items-center rounded-lg text-sm font-semibold text-primary-strong">Rosters <Icon name="chevron_right" size={16} /></Link>}
        >
          <Suspense fallback={<TableSkeleton rows={4} />}>
            <RecentRosters />
          </Suspense>
        </Card>
        <div className="space-y-6">
          <Card title="Data and storage" description="Where duties are calculated from and where drafts are kept.">
            <Suspense fallback={<div className="p-5"><Skeleton className="h-28 w-full" /></div>}>
              <SourceStatus />
            </Suspense>
          </Card>
          <Card title="Workflow">
            <ol className="divide-y divide-line">
              <Step n={1} icon="group" href="/exams/duties/staff" title="Check staff">Eligibility, workload and availability.</Step>
              <Step n={2} icon="work" href="/exams/duties/allocation" title="Allocate duties">Run BASELINE or FAIR and compare them.</Step>
              <Step n={3} icon="verified_user" href="/exams/duties/fairness" title="Review fairness">Workload spread, Gini and back-to-back duties.</Step>
              <Step n={4} icon="fact_check" href="/exams/duties/rosters" title="Validate, approve, publish">Each step is checked again by the service.</Step>
              <Step n={5} icon="event_repeat" href="/exams/duties/changes" title="Handle swaps">Swaps and emergency replacements with approval.</Step>
            </ol>
          </Card>
        </div>
      </div>
    </div>
  );
}

function Step({ n, icon, href, title, children }: { n: number; icon: IconName; href: string; title: string; children: string }) {
  return (
    <li>
      <Link href={href} className="flex items-start gap-3 px-5 py-3.5 transition-colors hover:bg-background">
        <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-primary-soft text-primary-strong"><Icon name={icon} size={18} filled /></span>
        <span className="min-w-0 flex-1">
          <span className="block text-sm font-semibold text-ink">{n}. {title}</span>
          <span className="block text-[13px] text-muted">{children}</span>
        </span>
        <Icon name="chevron_right" size={18} className="mt-1.5 text-secondary" />
      </Link>
    </li>
  );
}

function MetricsSkeleton() {
  return (
    <div aria-busy="true" aria-label="Loading duty counts" className="grid gap-3 sm:grid-cols-2 sm:gap-4 xl:grid-cols-4">
      {[0, 1, 2, 3].map((key) => <Skeleton key={key} className="h-36 rounded-2xl" />)}
    </div>
  );
}

async function PeriodMetrics() {
  const resolved = await resolvePeriod(undefined);
  if (!resolved.ok) return <Card><DutyReadError error={resolved.error} what="exam periods" /></Card>;
  const { period, periods, meta } = resolved.data;
  if (!period) return <Card><EmptyState icon="calendar_month" title="No exam periods">The duty-allocation data source has no exam periods yet.</EmptyState></Card>;
  const [sessions, requirements, staff, rosters] = await Promise.all([
    listDutySessions(period), getRequirements(period), listDutyStaff(period, false), listRosters(period, undefined, 20),
  ]);
  const failed = [sessions, requirements, staff].find((result) => !result.ok);
  if (failed && !failed.ok) return <Card><DutyReadError error={failed.error} what="duty data" /></Card>;
  if (!sessions.ok || !requirements.ok || !staff.ok) return null;
  const selected = periods.find((p) => p.period_code === period)!;
  const working = rosters.ok ? rosters.data.items.find((r) => WORKING_STATUSES.includes(r.status)) : undefined;
  const published = rosters.ok ? rosters.data.items.find((r) => r.status === "PUBLISHED") : undefined;
  const supervisors = staff.data.items.filter((s) => s.eligible_roles.includes("SUPERVISOR")).length;
  return (
    <div className="space-y-4">
      <DataNotice meta={meta} />
      <p className="text-sm text-muted">Showing <span className="font-semibold text-ink">{periodLabel(selected)}</span>.</p>
      <div className="grid gap-3 sm:grid-cols-2 sm:gap-4 xl:grid-cols-4">
        <Kpi icon="calendar_month" tone="primary" label="Exam sessions" value={number.format(sessions.data.total)}
          hint={`${number.format(requirements.data.items.length)} exam rooms need staff`} href="/exams/duties/allocation" />
        <Kpi icon="badge" tone="primary" label="Duties required" value={number.format(requirements.data.total_supervisors + requirements.data.total_invigilators)}
          hint={`${number.format(requirements.data.total_supervisors)} supervisors · ${number.format(requirements.data.total_invigilators)} invigilators`} />
        <Kpi icon="group" tone="accent" label="Staff available" value={number.format(staff.data.total)}
          hint={`${number.format(supervisors)} can supervise`} href="/exams/duties/staff" />
        <Kpi icon="fact_check" tone={published ? "accent" : "neutral"} label="Roster"
          value={published ? "Published" : working ? working.status.charAt(0) + working.status.slice(1).toLowerCase() : "None"}
          hint={working ? `Version ${working.version} · ${working.roster_label.toLowerCase()} roster` : rosters.ok ? "No working roster for this period" : "Saved rosters are unavailable"}
          href={working ? `/exams/duties/rosters/${working.roster_id}` : "/exams/duties/rosters"} />
      </div>
      {!requirements.data.complete && (
        <p className="rounded-xl bg-danger-soft px-3.5 py-3 text-sm text-danger">
          The staffing rules do not cover every exam room, so the required duties are incomplete. See Allocation for details.
        </p>
      )}
    </div>
  );
}

async function SourceStatus() {
  const result = await getDutyStatus();
  if (!result.ok) return <DutyReadError error={result.error} what="the data source" />;
  const status = result.data;
  return (
    <div className="space-y-4 p-5 text-sm">
      <dl className="grid grid-cols-[8rem_minmax(0,1fr)] gap-x-3 gap-y-2">
        <dt className="text-muted">Data source</dt>
        <dd className="font-medium text-ink">{status.source === "LOCAL_DEMO" ? "Local demo data" : status.source === "NEON_READ_ONLY" ? "Database (read only)" : status.source === "NEON_SHARED" ? "Neon database" : status.source}</dd>
        <dt className="text-muted">Saved drafts</dt>
        <dd className="font-medium text-ink">
          {status.persistence_mode === "DATABASE" ? "Database" : status.persistence_mode === "MEMORY" ? "Memory only" : "Turned off"}
        </dd>
        <dt className="text-muted">Default period</dt>
        <dd className="break-all font-mono text-xs text-secondary-ink">{status.default_period_code}</dd>
      </dl>
      <p className="rounded-xl bg-secondary-soft p-3 text-xs leading-5 text-secondary-ink">{status.persistence_problem ?? status.persistence}</p>
      {status.safeguards.length > 0 && (
        <details className="text-[13px]">
          <summary className="cursor-pointer font-semibold text-primary-strong">Safeguards ({status.safeguards.length})</summary>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-muted">{status.safeguards.map((s) => <li key={s}>{s}</li>)}</ul>
        </details>
      )}
    </div>
  );
}

async function RecentRosters() {
  const result = await listRosters(undefined, undefined, 5);
  if (!result.ok) return <DutyReadError error={result.error} what="saved rosters" />;
  if (!result.data.items.length) {
    return (
      <EmptyState icon="fact_check" title="No saved rosters yet" action={<Link href="/exams/duties/allocation" className="inline-flex rounded-xl bg-primary px-4 py-2.5 text-sm font-semibold text-white hover:bg-primary-strong">Allocate duties</Link>}>
        Allocations are temporary previews until you save one as a draft roster.
      </EmptyState>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[36rem] text-left text-sm">
        <caption className="sr-only">Most recently saved duty rosters</caption>
        <thead className="border-b border-line bg-background text-xs text-muted">
          <tr>{["Roster", "Status", "Duties", "Saved"].map((label) => <th key={label} scope="col" className={headCell}>{label}</th>)}</tr>
        </thead>
        <tbody className="divide-y divide-line">
          {result.data.items.map((roster) => (
            <tr key={roster.roster_id}>
              <th scope="row" className="px-5 py-4 font-medium text-ink">
                <Link href={`/exams/duties/rosters/${roster.roster_id}`} className="text-primary-strong underline-offset-2 hover:underline">
                  Version {roster.version} · {roster.roster_label === "FAIR" ? "Fair" : "Baseline"}
                </Link>
                <span className="mt-1 block font-mono text-[11px] text-secondary-ink">{roster.period_code}</span>
              </th>
              <td className={cell}><RosterStatusBadge status={roster.status} />{roster.synthetic && <span className="ml-1.5"><Badge tone="neutral">Synthetic</Badge></span>}</td>
              <td className={cell}>{number.format(roster.assignment_count)}</td>
              <td className={`${cell} whitespace-nowrap`}>{formatDateTime(roster.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
