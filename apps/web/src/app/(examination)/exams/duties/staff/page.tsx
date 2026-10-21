import type { Metadata } from "next";
import Form from "next/form";
import Link from "next/link";
import { Suspense } from "react";
import { Badge, Card, EmptyState, PageHeader, TableSkeleton } from "@/components/console/ui";
import { listDutyStaff, listWorkloads, resolvePeriod } from "@/lib/duty-allocation";
import { titleCase, type DutyRole, type WorkloadStatus } from "@/lib/duty-allocation-types";
import { RefreshButton } from "../../_components/refresh-button";
import {
  cell, DataNotice, DutyReadError, Field, first, headCell, NoPeriods, number, periodLabel, primaryButton, secondaryButton,
  selectClass, type SearchParams,
} from "../_components/duty-ui";

export const metadata: Metadata = { title: "Duty staff" };

const WORKLOAD_LABEL: Record<WorkloadStatus, string> = {
  COMPUTED: "Calculated",
  NOT_COMPUTED: "Not calculated",
  MISSING_RESPONSE: "No response",
  NOT_LOADED: "Not loaded",
};

export default function DutyStaffPage({ searchParams }: { searchParams: SearchParams }) {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Staff"
        description="Academic staff who can take examination duties, with their duty roles and administrative workload. Seniority only affects who may supervise."
        actions={<RefreshButton label="Refresh" />}
      />
      <Suspense fallback={<Card><TableSkeleton rows={8} /></Card>}>
        <StaffList searchParams={searchParams} />
      </Suspense>
    </div>
  );
}

async function StaffList({ searchParams }: { searchParams: SearchParams }) {
  const params = await searchParams;
  const resolved = await resolvePeriod(first(params.period));
  if (!resolved.ok) return <Card><DutyReadError error={resolved.error} what="exam periods" /></Card>;
  const { period, periods, meta } = resolved.data;
  if (!period) return <Card><NoPeriods /></Card>;
  const role = (["SUPERVISOR", "INVIGILATOR"] as const).find((r) => r === first(params.role));
  const showExcluded = first(params.show) === "all";
  const [staff, workloads] = await Promise.all([listDutyStaff(period, true), listWorkloads(period)]);
  if (!staff.ok) return <Card><DutyReadError error={staff.error} what="staff" /></Card>;
  const workloadOf = new Map(workloads.ok ? workloads.data.items.map((w) => [w.staff_id, w]) : []);
  const eligible = staff.data.items.filter((s) => s.eligible);
  const rows = staff.data.items.filter((s) => (showExcluded || s.eligible) && (!role || s.eligible_roles.includes(role as DutyRole)));
  const counts = workloads.ok ? workloads.data.counts : {};

  return (
    <>
      <DataNotice meta={meta} />
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Count label="Available for duties" value={eligible.length} hint={`${staff.data.total - eligible.length} left out, with a reason`} />
        <Count label="Can supervise" value={eligible.filter((s) => s.eligible_roles.includes("SUPERVISOR")).length} hint="Seniority rule" />
        <Count label="Can invigilate" value={eligible.filter((s) => s.eligible_roles.includes("INVIGILATOR")).length} />
        <Count label="Workload calculated" value={counts.COMPUTED ?? 0}
          hint={`${counts.NOT_COMPUTED ?? 0} not calculated · ${counts.MISSING_RESPONSE ?? 0} no response`} />
      </div>
      <Card>
        <Form action="/exams/duties/staff" className="grid gap-3 border-b border-line p-4 sm:grid-cols-2 sm:px-5 lg:grid-cols-[minmax(0,1fr)_12rem_12rem_auto] lg:items-end">
          <Field label="Exam period">
            <select name="period" defaultValue={period} className={selectClass}>
              {periods.map((p) => <option key={p.period_code} value={p.period_code}>{periodLabel(p)}</option>)}
            </select>
          </Field>
          <Field label="Duty role">
            <select name="role" defaultValue={role ?? ""} className={selectClass}>
              <option value="">Any role</option>
              <option value="SUPERVISOR">Supervisor</option>
              <option value="INVIGILATOR">Invigilator</option>
            </select>
          </Field>
          <Field label="Show">
            <select name="show" defaultValue={showExcluded ? "all" : ""} className={selectClass}>
              <option value="">Available staff</option>
              <option value="all">Everyone, with reasons</option>
            </select>
          </Field>
          <div className="flex gap-2">
            <button type="submit" className={primaryButton}>Apply</button>
            <Link href="/exams/duties/staff" className={secondaryButton}>Clear</Link>
          </div>
        </Form>
        {!workloads.ok && <p className="border-b border-line px-5 py-3 text-sm text-danger">Workload could not be loaded: {workloads.error.message}</p>}
        {!rows.length ? (
          <EmptyState icon="filter_alt_off" title="No staff match these filters" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[52rem] text-left">
              <caption className="sr-only">Staff available for examination duties</caption>
              <thead className="border-b border-line bg-background text-xs text-muted">
                <tr>{["Staff member", "Rank", "Duty roles", "Administrative workload", "Status"].map((label) => <th key={label} scope="col" className={headCell}>{label}</th>)}</tr>
              </thead>
              <tbody className="divide-y divide-line">
                {rows.map((s) => {
                  const workload = workloadOf.get(s.staff_id);
                  return (
                    <tr key={s.staff_id}>
                      <th scope="row" className="px-5 py-4 text-sm font-semibold text-ink">
                        <Link href={`/exams/duties/staff/${s.staff_id}?period=${encodeURIComponent(period)}`} className="text-primary-strong underline-offset-2 hover:underline">
                          {s.full_name ?? s.staff_number}
                        </Link>
                        <span className="mt-1 block font-mono text-[11px] font-normal text-secondary-ink">{s.staff_number}</span>
                      </th>
                      <td className={cell}>{s.academic_rank ?? "—"}</td>
                      <td className={cell}>
                        <span className="flex flex-wrap gap-1.5">
                          {s.eligible_roles.length ? s.eligible_roles.map((r) => <Badge key={r} tone={r === "SUPERVISOR" ? "primary" : "neutral"}>{titleCase(r)}</Badge>) : "—"}
                        </span>
                      </td>
                      <td className={cell}>
                        {workload ? (
                          <>
                            {workload.hours_per_week !== null ? `${workload.hours_per_week} h/week` : "—"}
                            <span className="block text-xs">{WORKLOAD_LABEL[workload.status]}</span>
                          </>
                        ) : "—"}
                      </td>
                      <td className={cell}>
                        {s.eligible ? <Badge tone="accent">Available</Badge> : (
                          <>
                            <Badge tone="danger">Left out</Badge>
                            {s.exclusion_reason && <span className="mt-1 block text-xs">{s.exclusion_reason}</span>}
                          </>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <p className="border-t border-line px-5 py-3 text-xs text-muted">Showing {number.format(rows.length)} of {number.format(staff.data.total)} staff.</p>
          </div>
        )}
      </Card>
    </>
  );
}

function Count({ label, value, hint }: { label: string; value: number; hint?: string }) {
  return (
    <div className="rounded-2xl border border-line bg-surface p-4">
      <p className="text-[13px] font-semibold text-muted">{label}</p>
      <p className="mt-1.5 text-2xl font-semibold tracking-tight text-ink">{number.format(value)}</p>
      {hint && <p className="mt-1 text-xs text-muted">{hint}</p>}
    </div>
  );
}
