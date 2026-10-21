import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";
import { Badge, Card, EmptyState, formatDate, PageHeader, PageSkeleton } from "@/components/console/ui";
import { Icon } from "@/components/icon";
import { getDutyStaff, PERIOD_PATTERN, UUID_PATTERN } from "@/lib/duty-allocation";
import { clock, titleCase, type TimeWindow } from "@/lib/duty-allocation-types";
import { DataNotice, DutyReadError, first, type SearchParams } from "../../_components/duty-ui";

export const metadata: Metadata = { title: "Staff member" };

type Params = Promise<{ staffId: string }>;

export default function StaffDetailPage({ params, searchParams }: { params: Params; searchParams: SearchParams }) {
  return <Suspense fallback={<PageSkeleton />}><StaffDetail params={params} searchParams={searchParams} /></Suspense>;
}

async function StaffDetail({ params, searchParams }: { params: Params; searchParams: SearchParams }) {
  const [{ staffId }, query] = await Promise.all([params, searchParams]);
  if (!UUID_PATTERN.test(staffId)) notFound();
  const periodParam = first(query.period);
  const period = periodParam && PERIOD_PATTERN.test(periodParam) ? periodParam : undefined;
  const result = await getDutyStaff(staffId, period ?? "");
  const back = (
    <Link href={`/exams/duties/staff${period ? `?period=${encodeURIComponent(period)}` : ""}`} className="inline-flex items-center gap-1 text-sm font-semibold text-primary-strong">
      <Icon name="arrow_back" size={18} />All staff
    </Link>
  );
  if (!result.ok) {
    if (result.error.status === 404) notFound();
    return <div className="space-y-4">{back}<Card><DutyReadError error={result.error} what="this staff member" /></Card></div>;
  }
  const s = result.data;
  const c = s.constraints;

  return (
    <div className="space-y-6">
      {back}
      <PageHeader
        title={s.full_name ?? s.staff_number}
        description={<><span className="font-mono">{s.staff_number}</span>{s.academic_rank ? ` · ${s.academic_rank}` : ""}</>}
        actions={s.eligible ? <Badge tone="accent">Available for duties</Badge> : <Badge tone="danger">Left out of duties</Badge>}
      />
      <DataNotice meta={s.meta} />
      {!s.eligible && <Card><EmptyState icon="person_off" title="Not available for duties">{s.exclusion_reason ?? "No reason was given."}</EmptyState></Card>}

      <div className="grid items-start gap-6 lg:grid-cols-2">
        <Card title="Duty roles and limits">
          <dl className="grid grid-cols-[11rem_minmax(0,1fr)] gap-x-3 gap-y-2.5 p-5 text-sm">
            <dt className="text-muted">Roles</dt>
            <dd className="flex flex-wrap gap-1.5">{s.eligible_roles.length ? s.eligible_roles.map((r) => <Badge key={r} tone="primary">{titleCase(r)}</Badge>) : "—"}</dd>
            <dt className="text-muted">Administrative workload</dt>
            <dd className="text-ink">
              {s.workload?.hours_per_week != null ? `${s.workload.hours_per_week} hours per week` : "—"}
              {s.workload && <span className="block text-xs text-muted">{titleCase(s.workload.status)}{s.workload.note ? ` · ${s.workload.note}` : ""}</span>}
            </dd>
            {c && (
              <>
                <dt className="text-muted">Most duties per day</dt>
                <dd className="text-ink">{c.limits.max_duties_per_day ?? "No limit"}</dd>
                <dt className="text-muted">Most duties per period</dt>
                <dd className="text-ink">{c.limits.max_duties_per_period ?? "No limit"}</dd>
                <dt className="text-muted">Minimum gap</dt>
                <dd className="text-ink">{c.limits.minimum_gap_minutes} minutes</dd>
                <dt className="text-muted">Earlier duties</dt>
                <dd className="text-ink">{c.history ? `${c.history.prior_duties} duties · ${c.history.prior_minutes} minutes` : "None recorded"}</dd>
              </>
            )}
          </dl>
          {c && <p className="border-t border-line px-5 py-3 text-xs text-muted">Limits: {c.limits.source}</p>}
        </Card>

        {c && (
          <Card title="Day preferences" description="Preferences are respected where possible; breaches are counted in fairness.">
            {c.preferences.length ? (
              <ul className="divide-y divide-line">
                {c.preferences.map((p, i) => (
                  <li key={i} className="flex items-center justify-between gap-3 px-5 py-3 text-sm">
                    <span className="text-ink">{p.day}</span><Badge tone="neutral">{titleCase(p.kind)}</Badge>
                  </li>
                ))}
              </ul>
            ) : <p className="px-5 py-4 text-sm text-muted">No preferences recorded.</p>}
          </Card>
        )}
      </div>

      {c && (
        <div className="grid items-start gap-6 lg:grid-cols-2">
          <WindowCard title="Unavailable" description="No duties are assigned in these windows." windows={c.unavailability} />
          <WindowCard title="Available" description="Declared availability, when it was given." windows={c.availability} />
        </div>
      )}

      {c && c.restrictions.length > 0 && (
        <Card title="Restrictions">
          <ul className="divide-y divide-line">
            {c.restrictions.map((r, i) => (
              <li key={i} className="px-5 py-3 text-sm text-muted">
                <span className="font-medium text-ink">{[r.module_code, r.session_id, r.role && titleCase(r.role)].filter(Boolean).join(" · ") || "General"}</span>
                <span className="block text-xs">{r.reason}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}
      {c && c.notes.length > 0 && <Card title="Notes"><ul className="list-disc space-y-1 py-4 pl-10 pr-5 text-sm text-muted">{c.notes.map((n) => <li key={n}>{n}</li>)}</ul></Card>}
    </div>
  );
}

function WindowCard({ title, description, windows }: { title: string; description: string; windows: TimeWindow[] }) {
  return (
    <Card title={title} description={description}>
      {windows.length ? (
        <ul className="divide-y divide-line">
          {windows.map((w, i) => (
            <li key={i} className="px-5 py-3 text-sm">
              <span className="font-medium text-ink">{formatDate(w.start.slice(0, 10))} {clock(w.start)} – {w.end.slice(0, 10) === w.start.slice(0, 10) ? "" : `${formatDate(w.end.slice(0, 10))} `}{clock(w.end)}</span>
              {w.reason && <span className="block text-xs text-muted">{w.reason}</span>}
            </li>
          ))}
        </ul>
      ) : <p className="px-5 py-4 text-sm text-muted">None recorded.</p>}
    </Card>
  );
}
