import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";
import { BarChart } from "@/components/console/charts";
import { Badge, Card, EmptyState, Kpi, PageHeader, TableSkeleton } from "@/components/console/ui";
import { getRoster, getRunFairness, listRosters, RUN_PATTERN, UUID_PATTERN } from "@/lib/duty-allocation";
import { fixed, type Comparison, type FairnessMetrics, type Meta } from "@/lib/duty-allocation-types";
import { RefreshButton } from "../../_components/refresh-button";
import {
  DataNotice, DutyReadError, first, Notice, number, primaryButton, RosterStatusBadge, TextLink, type SearchParams,
} from "../_components/duty-ui";

export const metadata: Metadata = { title: "Duty fairness" };

export default function FairnessPage({ searchParams }: { searchParams: SearchParams }) {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Fairness"
        description="How evenly duties are spread. Workload is weighted by role and adds earlier duties and administrative workload to this period's duties, as set in the fairness policy. A lower Gini coefficient is fairer."
        actions={<RefreshButton label="Refresh" />}
      />
      <Suspense fallback={<Card><TableSkeleton rows={6} /></Card>}>
        <Fairness searchParams={searchParams} />
      </Suspense>
    </div>
  );
}

async function Fairness({ searchParams }: { searchParams: SearchParams }) {
  const params = await searchParams;
  const runId = first(params.run);
  const rosterId = first(params.roster);

  if (runId && RUN_PATTERN.test(runId)) {
    const result = await getRunFairness(runId);
    if (!result.ok) {
      return result.error.status === 404
        ? <><Notice tone="warn" title="This allocation is no longer available">Allocations are temporary previews kept in memory. Run it again, or choose a saved roster.</Notice><Choose /></>
        : <Card><DutyReadError error={result.error} what="fairness" /></Card>;
    }
    const f = result.data;
    const main = f.fair ?? f.baseline;
    return (
      <>
        <DataNotice meta={f.meta} />
        <p className="text-sm text-muted">
          Temporary allocation preview · <TextLink href={`/exams/duties/allocation?run=${runId}`}>Back to the allocation</TextLink>
        </p>
        {!f.fairness_complete && f.fairness_scope.length > 0 && (
          <Notice tone="warn" title="Fairness is partial">Calculated from: {f.fairness_scope.join(", ")}. Some workload data is missing.</Notice>
        )}
        {main ? <Metrics metrics={main} label={f.fair ? "Fair roster" : "Baseline roster"} /> : <Card><EmptyState icon="verified_user" title="No fairness figures">The allocation finished with status {f.status}, so there is no roster to measure.</EmptyState></Card>}
        {f.comparison && <ComparisonCard comparison={f.comparison} />}
        {main && <LoadChart metrics={main} title={f.fair ? "Workload per staff member — fair roster" : "Workload per staff member — baseline roster"} />}
        {f.baseline && f.fair && <LoadChart metrics={f.baseline} title="Workload per staff member — baseline roster" />}
        {f.notes.length > 0 && <Card title="Notes"><ul className="list-disc space-y-1 py-4 pl-10 pr-5 text-sm text-muted">{f.notes.map((n) => <li key={n}>{n}</li>)}</ul></Card>}
      </>
    );
  }

  if (rosterId && UUID_PATTERN.test(rosterId)) {
    const result = await getRoster(rosterId);
    if (!result.ok) return <Card><DutyReadError error={result.error} what="the roster" /></Card>;
    const r = result.data;
    const meta: Pick<Meta, "synthetic" | "notice" | "source_description"> = { synthetic: r.synthetic, notice: r.notice, source_description: r.data_source };
    return (
      <>
        <DataNotice meta={meta} />
        <p className="flex flex-wrap items-center gap-2 text-sm text-muted">
          Saved roster version {r.version} · <RosterStatusBadge status={r.status} /> · <TextLink href={`/exams/duties/rosters/${r.roster_id}`}>Open roster</TextLink>
        </p>
        <Metrics metrics={r.fairness_metrics} label={`${r.roster_label === "FAIR" ? "Fair" : "Baseline"} roster`} />
        <LoadChart metrics={r.fairness_metrics} title="Workload per staff member" />
      </>
    );
  }
  return <Choose />;
}

async function Choose() {
  const rosters = await listRosters(undefined, undefined, 10);
  return (
    <Card title="Choose what to measure" description="Run an allocation, or pick a saved roster.">
      {rosters.ok && rosters.data.items.length > 0 ? (
        <ul className="divide-y divide-line">
          {rosters.data.items.map((r) => (
            <li key={r.roster_id}>
              <Link href={`/exams/duties/fairness?roster=${r.roster_id}`} className="flex flex-wrap items-center justify-between gap-3 px-5 py-3.5 text-sm hover:bg-background">
                <span><span className="font-semibold text-ink">Version {r.version} · {r.roster_label === "FAIR" ? "Fair" : "Baseline"}</span><span className="ml-2 font-mono text-[11px] text-secondary-ink">{r.period_code}</span></span>
                <span className="flex items-center gap-2"><RosterStatusBadge status={r.status} /><span className="text-muted">Gini {fixed(r.fairness_metrics.gini)}</span></span>
              </Link>
            </li>
          ))}
        </ul>
      ) : (
        <EmptyState icon="verified_user" title="Nothing to measure yet" action={<Link href="/exams/duties/allocation" className={primaryButton}>Allocate duties</Link>}>
          {rosters.ok || rosters.error.status === 503 ? "Run an allocation first. Its fairness appears here." : rosters.error.message}
        </EmptyState>
      )}
    </Card>
  );
}

function Metrics({ metrics: m, label }: { metrics: Partial<FairnessMetrics>; label: string }) {
  return (
    <>
      <div className="grid gap-3 sm:grid-cols-2 sm:gap-4 xl:grid-cols-4">
        <Kpi icon="verified_user" tone="primary" label="Gini coefficient" value={fixed(m.gini)} hint={`${label} · 0 is perfectly even`} />
        <Kpi icon="schedule" tone="primary" label="Workload spread" value={fixed(m.std_load, 1)} hint={`Standard deviation · mean ${fixed(m.mean_load, 1)}`} />
        <Kpi icon="badge" tone="neutral" label="Duties per person" value={m.min_duties !== undefined ? `${m.min_duties}–${m.max_duties}` : "—"}
          hint={`${m.staff_without_duties ?? 0} staff without duties`} />
        <Kpi icon="event_repeat" tone={(m.back_to_back_pairs ?? 0) + (m.preference_breaches ?? 0) ? "danger" : "accent"} label="Back-to-back duties"
          value={m.back_to_back_pairs ?? "—"} hint={`${m.preference_breaches ?? 0} preference breaches`} />
      </div>
      {m.staff_missing_admin_hours && m.staff_missing_admin_hours.length > 0 && (
        <Notice tone="warn" title={`Administrative workload missing for ${m.staff_missing_admin_hours.length} staff`}>
          Their workload counts examination duties only: {m.staff_missing_admin_hours.slice(0, 12).join(", ")}{m.staff_missing_admin_hours.length > 12 ? "…" : ""}
        </Notice>
      )}
    </>
  );
}

function ComparisonCard({ comparison: c }: { comparison: Comparison }) {
  const rows: [string, string | number, string | number][] = [
    ["Gini coefficient", fixed(c.gini_baseline), fixed(c.gini_fair)],
    ["Workload standard deviation", fixed(c.std_load_baseline), fixed(c.std_load_fair)],
    ["Workload range (most − least)", c.load_range_baseline, c.load_range_fair],
    ["Back-to-back duty pairs", c.back_to_back_baseline, c.back_to_back_fair],
    ["Preference breaches", c.preference_breaches_baseline, c.preference_breaches_fair],
  ];
  return (
    <Card title="Baseline compared with fair" description={`${c.assignments_different} duties differ between the two rosters.`}
      actions={<Badge tone={c.fairer_by_gini === "FAIR" ? "accent" : "neutral"}>Fairer by Gini: {c.fairer_by_gini === "TIE" ? "tie" : c.fairer_by_gini.toLowerCase()}</Badge>}>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[30rem] text-left text-sm">
          <caption className="sr-only">Fairness measures of the baseline and fair rosters</caption>
          <thead className="border-b border-line bg-background text-xs text-muted">
            <tr><th scope="col" className="px-5 py-3 font-semibold">Measure</th><th scope="col" className="px-5 py-3 text-right font-semibold">Baseline</th><th scope="col" className="px-5 py-3 text-right font-semibold">Fair</th></tr>
          </thead>
          <tbody className="divide-y divide-line tabular-nums">
            {rows.map(([label, a, b]) => (
              <tr key={label}><th scope="row" className="px-5 py-3 font-normal text-ink">{label}</th><td className="px-5 py-3 text-right text-ink">{a}</td><td className="px-5 py-3 text-right text-ink">{b}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function LoadChart({ metrics, title }: { metrics: Partial<FairnessMetrics>; title: string }) {
  const staff = [...(metrics.per_staff ?? [])].sort((a, b) => b.total_load - a.total_load);
  if (!staff.length) return null;
  return (
    <Card title={title} description="Weighted workload (earlier duties, administrative workload and this period's duties, in the fairness policy's unit), highest first.">
      <div className="p-5">
        <BarChart
          caption={title}
          shares={false}
          head={["Staff member", "Workload"]}
          unit={{ one: "unit", other: "units" }}
          bars={staff.map((s) => ({ key: s.staff_id, label: s.staff_name ?? s.staff_number ?? s.staff_id.slice(0, 8), value: Math.round(s.total_load) }))}
        />
        <p className="mt-3 text-xs text-muted">{number.format(staff.length)} staff · duties per person from {Math.min(...staff.map((s) => s.duties))} to {Math.max(...staff.map((s) => s.duties))}.</p>
      </div>
    </Card>
  );
}
