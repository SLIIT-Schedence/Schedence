import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";
import { Badge, Card, EmptyState, formatDate, formatDateTime, Kpi, PageHeader, TableSkeleton } from "@/components/console/ui";
import { saveRosterDraft, runAllocation } from "@/lib/duty-allocation-actions";
import {
  getAllocationRun, getDutyStatus, getRequirements, listDutySessions, listRosters, resolvePeriod, RUN_PATTERN, type DutyResult,
} from "@/lib/duty-allocation";
import {
  clock, fixed, roomKey, WORKING_STATUSES, type AllocationRun, type RosterLabel, type RunAssignment, type RunRoster,
} from "@/lib/duty-allocation-types";
import { RefreshButton } from "../../_components/refresh-button";
import { ActionForm } from "../_components/action-form";
import {
  cell, DataNotice, DutyReadError, Field, first, headCell, Notice, NoPeriods, number, PeriodBar, RosterStatusBadge,
  selectClass, TextLink, ViolationList, type SearchParams,
} from "../_components/duty-ui";

export const metadata: Metadata = { title: "Duty allocation" };

export default function AllocationPage({ searchParams }: { searchParams: SearchParams }) {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Duty allocation"
        description="Assign supervisors and invigilators to every exam room with the CP-SAT solver. BASELINE only meets the rules; FAIR also balances workload. Results are temporary until you save one."
        actions={<RefreshButton label="Refresh" />}
      />
      <Suspense fallback={<Card><TableSkeleton rows={8} /></Card>}>
        <Allocation searchParams={searchParams} />
      </Suspense>
    </div>
  );
}

async function Allocation({ searchParams }: { searchParams: SearchParams }) {
  const params = await searchParams;
  const runParam = first(params.run);
  const runResult: DutyResult<AllocationRun> | undefined = runParam && RUN_PATTERN.test(runParam) ? await getAllocationRun(runParam) : undefined;
  const run = runResult?.ok ? runResult.data : undefined;
  const resolved = await resolvePeriod(run?.meta.period_code ?? first(params.period));
  if (!resolved.ok) return <Card><DutyReadError error={resolved.error} what="exam periods" /></Card>;
  const { period, periods, meta } = resolved.data;
  if (!period) return <Card><NoPeriods /></Card>;

  const [sessions, requirements] = await Promise.all([listDutySessions(period), getRequirements(period)]);
  if (!sessions.ok) return <Card><DutyReadError error={sessions.error} what="exam sessions" /></Card>;
  if (!requirements.ok) return <Card><DutyReadError error={requirements.error} what="duty requirements" /></Card>;

  const labelParam = first(params.roster) as RosterLabel | undefined;
  const roster: RunRoster | undefined = run?.rosters.find((r) => r.label === labelParam) ?? run?.rosters.find((r) => r.label === "FAIR") ?? run?.rosters[0];
  const assigned = new Map<string, RunAssignment[]>();
  for (const a of roster?.assignments ?? []) {
    const key = roomKey(a.session_id, a.venue_id);
    assigned.set(key, [...(assigned.get(key) ?? []), a]);
  }
  const required = requirements.data.total_supervisors + requirements.data.total_invigilators;
  const gaps = roster ? requirements.data.items.flatMap((need) => {
    const here = assigned.get(roomKey(need.session_id, need.venue_id)) ?? [];
    const sup = need.supervisors - here.filter((a) => a.role === "SUPERVISOR").length;
    const inv = need.invigilators - here.filter((a) => a.role === "INVIGILATOR").length;
    return [
      ...(sup > 0 ? [`${need.session_id} · ${need.venue_id}: ${sup} supervisor${sup > 1 ? "s" : ""} missing`] : []),
      ...(inv > 0 ? [`${need.session_id} · ${need.venue_id}: ${inv} invigilator${inv > 1 ? "s" : ""} missing`] : []),
    ];
  }) : [];
  const needOf = new Map(requirements.data.items.map((r) => [roomKey(r.session_id, r.venue_id), r]));
  const ordered = [...sessions.data.items].sort((a, b) => a.start.localeCompare(b.start) || a.module_code.localeCompare(b.module_code));
  const runHref = (label: RosterLabel) => `/exams/duties/allocation?period=${encodeURIComponent(period)}&run=${run?.run_id}&roster=${label}`;

  return (
    <>
      <DataNotice meta={meta} />
      <Card title="Run an allocation" description="Choose what to calculate. Compare runs BASELINE and FAIR on the same data.">
        <PeriodBar action="/exams/duties/allocation" periods={periods} period={period} />
        <ActionForm action={runAllocation} submitLabel="Run allocation" pendingLabel="Solving… this can take up to the time limit" icon="work" className="space-y-4 p-5">
          <input type="hidden" name="period" value={period} />
          <div className="grid gap-3 sm:grid-cols-2 lg:max-w-2xl">
            <Field label="Mode">
              <select name="mode" defaultValue="compare" className={selectClass}>
                <option value="compare">Compare BASELINE and FAIR</option>
                <option value="fair">FAIR only (balance workload)</option>
                <option value="baseline">BASELINE only (rules only)</option>
              </select>
            </Field>
            <Field label="Solver time limit">
              <select name="time_limit" defaultValue="20" className={selectClass}>
                <option value="10">10 seconds</option>
                <option value="20">20 seconds</option>
                <option value="60">60 seconds</option>
              </select>
            </Field>
          </div>
        </ActionForm>
      </Card>

      {runParam && runResult && !runResult.ok && (
        runResult.error.status === 404
          ? <Notice tone="warn" title="This allocation is no longer available">Allocations are temporary previews kept in memory. Run it again.</Notice>
          : <Card><DutyReadError error={runResult.error} what="the allocation" /></Card>
      )}

      <div className="grid gap-3 sm:grid-cols-2 sm:gap-4 xl:grid-cols-4">
        <Kpi icon="calendar_month" tone="primary" label="Exam sessions" value={number.format(sessions.data.total)} hint={`${number.format(requirements.data.items.length)} rooms need staff`} />
        <Kpi icon="shield_person" tone="primary" label="Supervisors required" value={number.format(requirements.data.total_supervisors)} hint="From the staffing rules" />
        <Kpi icon="how_to_reg" tone="primary" label="Invigilators required" value={number.format(requirements.data.total_invigilators)} hint="From the staffing rules" />
        <Kpi icon="task_alt" tone={roster ? (gaps.length ? "danger" : "accent") : "neutral"} label="Allocated"
          value={roster ? `${number.format(roster.assignments.length)} / ${number.format(required)}` : "—"}
          hint={roster ? (gaps.length ? `${gaps.length} positions unfilled` : "Every position is filled") : "Run an allocation"} />
      </div>

      {!requirements.data.complete && (
        <Notice tone="danger" title="The staffing rules do not cover every exam room">
          <ViolationList violations={requirements.data.violations} limit={6} />
        </Notice>
      )}
      <p className="text-xs text-muted">Staffing rules: {requirements.data.policy_source}</p>

      {run && <RunResult run={run} period={period} />}

      {run && run.rosters.length > 1 && (
        <nav aria-label="Roster to show" className="flex flex-wrap gap-2">
          {run.rosters.map((r) => (
            <Link key={r.label} href={runHref(r.label)} aria-current={roster?.label === r.label ? "page" : undefined}
              className={`inline-flex h-10 items-center rounded-xl px-4 text-sm font-semibold ${roster?.label === r.label ? "bg-primary text-white" : "border border-line bg-surface text-ink hover:bg-background"}`}>
              {r.label === "FAIR" ? "Fair roster" : "Baseline roster"}
            </Link>
          ))}
        </nav>
      )}

      <Card
        title="Sessions, rooms and duties"
        description={roster ? `The ${roster.label.toLowerCase()} roster (temporary preview). Required counts come from the staffing rules.` : "Required staff for each room. Run an allocation to fill them."}
      >
        {!ordered.length ? <EmptyState icon="calendar_month" title="No exam sessions in this period" /> : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[64rem] text-left">
              <caption className="sr-only">Exam sessions, rooms and assigned duty staff</caption>
              <thead className="border-b border-line bg-background text-xs text-muted">
                <tr>{["Date and time", "Module", "Room", "Students", "Exam capacity", "Required", "Assigned staff"].map((label) => <th key={label} scope="col" className={headCell}>{label}</th>)}</tr>
              </thead>
              <tbody className="divide-y divide-line">
                {ordered.flatMap((s) => s.rooms.map((room, index) => {
                  const need = needOf.get(roomKey(s.session_id, room.venue_id));
                  const here = assigned.get(roomKey(s.session_id, room.venue_id)) ?? [];
                  const over = room.exam_capacity !== null && room.students > room.exam_capacity;
                  return (
                    <tr key={`${s.session_id}-${room.venue_id}`} className="align-top">
                      <td className={`${cell} whitespace-nowrap`}>
                        {index === 0 ? <><span className="font-medium text-ink">{formatDate(s.date)}</span><span className="block text-xs">{clock(s.start)}–{clock(s.end)}</span></> : <span className="text-xs">Same session</span>}
                      </td>
                      <td className={cell}><span className="font-mono text-[13px] text-ink">{s.module_code}</span><span className="block text-xs">{s.module_name}</span></td>
                      <td className={cell}><span className="text-ink">{room.venue_name ?? room.venue_id}</span><span className="block text-xs">{room.venue_type?.replaceAll("_", " ")}</span></td>
                      <td className={`${cell} tabular-nums`}>{number.format(room.students)}</td>
                      <td className={`${cell} whitespace-nowrap`}>
                        {room.exam_capacity !== null
                          ? <span className={over ? "font-semibold text-danger" : ""}>{room.exam_capacity} seats<span className="block text-xs font-normal text-muted">{room.capacity_percentage}% of {room.full_capacity}</span></span>
                          : <span className="text-xs">No capacity rule</span>}
                        {over && <span className="mt-1 block"><Badge tone="danger">Over capacity</Badge></span>}
                      </td>
                      <td className={`${cell} whitespace-nowrap`}>{need ? <>{need.supervisors} supervisor{need.supervisors === 1 ? "" : "s"}<span className="block">{need.invigilators} invigilator{need.invigilators === 1 ? "" : "s"}</span></> : "—"}</td>
                      <td className={cell}>
                        {!roster ? <span className="text-xs">Not allocated yet</span> : (
                          <div className="space-y-1.5">
                            <Duties label="Supervisors" staff={here.filter((a) => a.role === "SUPERVISOR")} missing={need ? need.supervisors - here.filter((a) => a.role === "SUPERVISOR").length : 0} />
                            <Duties label="Invigilators" staff={here.filter((a) => a.role === "INVIGILATOR")} missing={need ? need.invigilators - here.filter((a) => a.role === "INVIGILATOR").length : 0} />
                          </div>
                        )}
                      </td>
                    </tr>
                  );
                }))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {gaps.length > 0 && <Notice tone="danger" title={`Unfilled positions (${gaps.length})`}><ul className="list-disc space-y-1 pl-5">{gaps.slice(0, 10).map((g) => <li key={g}>{g}</li>)}</ul></Notice>}

      <Suspense fallback={null}>
        <SaveCard run={run} roster={roster} period={period} />
      </Suspense>
    </>
  );
}

function Duties({ label, staff, missing }: { label: string; staff: RunAssignment[]; missing: number }) {
  return (
    <div className="flex flex-wrap items-center gap-1">
      <span className="w-24 text-xs">{label}</span>
      {staff.map((a) => (
        <span key={a.staff_id} title={a.staff_number ?? undefined} className="inline-flex rounded-md bg-secondary-soft px-2 py-0.5 text-xs font-medium text-secondary-ink">
          {a.staff_name ?? a.staff_number}
        </span>
      ))}
      {missing > 0 && <Badge tone="danger">{missing} missing</Badge>}
    </div>
  );
}

function RunResult({ run, period }: { run: AllocationRun; period: string }) {
  const revalidation = run.rosters.flatMap((r) => r.revalidation_violations);
  return (
    <Card title="Result" description={`${run.mode} run · ${formatDateTime(run.created_at)} · temporary preview, not saved`}>
      <div className="space-y-4 p-5">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={run.status === "COMPLETED" ? "accent" : "danger"}>{run.status === "COMPLETED" ? "Completed" : run.status.replaceAll("_", " ").toLowerCase()}</Badge>
          {run.rosters.map((r) => (
            <Badge key={r.label} tone={r.independently_valid ? "accent" : r.independently_valid === false ? "danger" : "neutral"}>
              {r.label}: {r.solver_status.toLowerCase()}{r.independently_valid ? ", checked independently" : r.independently_valid === false ? ", check failed" : ""} · {r.wall_time_seconds.toFixed(1)} s
            </Badge>
          ))}
          {!run.fairness_complete && run.fairness_scope.length > 0 && <Badge tone="neutral">Fairness covers: {run.fairness_scope.join(", ")}</Badge>}
        </div>
        {run.status !== "COMPLETED" && (
          <Notice tone="danger" title={run.status === "INFEASIBLE" ? "No roster meets every rule with the current staff." : "The allocation did not finish."}>
            <ViolationList violations={run.violations} />
          </Notice>
        )}
        {revalidation.length > 0 && <Notice tone="danger" title="The independent check found problems"><ViolationList violations={revalidation} /></Notice>}
        {run.warnings.length > 0 && <Notice tone="warn"><ul className="list-disc space-y-1 pl-5">{run.warnings.slice(0, 6).map((w) => <li key={w}>{w}</li>)}</ul></Notice>}
        {run.comparison && (
          <div className="overflow-x-auto rounded-xl border border-line">
            <table className="w-full min-w-[30rem] text-left text-sm">
              <caption className="sr-only">BASELINE and FAIR rosters compared</caption>
              <thead className="bg-background text-xs text-muted">
                <tr><th scope="col" className="px-4 py-2.5 font-semibold">Measure</th><th scope="col" className="px-4 py-2.5 text-right font-semibold">Baseline</th><th scope="col" className="px-4 py-2.5 text-right font-semibold">Fair</th></tr>
              </thead>
              <tbody className="divide-y divide-line tabular-nums">
                <Row label="Gini coefficient (lower is fairer)" a={fixed(run.comparison.gini_baseline)} b={fixed(run.comparison.gini_fair)} />
                <Row label="Workload standard deviation" a={fixed(run.comparison.std_load_baseline)} b={fixed(run.comparison.std_load_fair)} />
                <Row label="Workload range (most − least)" a={run.comparison.load_range_baseline} b={run.comparison.load_range_fair} />
                <Row label="Back-to-back duty pairs" a={run.comparison.back_to_back_baseline} b={run.comparison.back_to_back_fair} />
                <Row label="Preference breaches" a={run.comparison.preference_breaches_baseline} b={run.comparison.preference_breaches_fair} />
              </tbody>
            </table>
            <p className="border-t border-line px-4 py-2.5 text-[13px] text-muted">
              Fairer by Gini: <span className="font-semibold text-ink">{run.comparison.fairer_by_gini}</span> · {run.comparison.assignments_different} duties differ.{" "}
              <TextLink href={`/exams/duties/fairness?period=${encodeURIComponent(period)}&run=${run.run_id}`}>Open fairness analysis</TextLink>
            </p>
          </div>
        )}
        {!run.comparison && run.status === "COMPLETED" && (
          <p className="text-[13px] text-muted"><TextLink href={`/exams/duties/fairness?period=${encodeURIComponent(period)}&run=${run.run_id}`}>Open fairness analysis</TextLink></p>
        )}
        {run.diagnostics.length > 0 && (
          <details className="text-[13px]">
            <summary className="cursor-pointer font-semibold text-primary-strong">Solver notes ({run.diagnostics.length})</summary>
            <ul className="mt-2 list-disc space-y-1 pl-5 text-muted">{run.diagnostics.map((d, i) => <li key={i}>{d}</li>)}</ul>
          </details>
        )}
      </div>
    </Card>
  );
}

function Row({ label, a, b }: { label: string; a: string | number; b: string | number }) {
  return <tr><th scope="row" className="px-4 py-2.5 font-normal text-ink">{label}</th><td className="px-4 py-2.5 text-right text-ink">{a}</td><td className="px-4 py-2.5 text-right text-ink">{b}</td></tr>;
}

async function SaveCard({ run, roster, period }: { run?: AllocationRun; roster?: RunRoster; period: string }) {
  const [status, rosters] = await Promise.all([getDutyStatus(), listRosters(period, undefined, 20)]);
  const persistence = status.ok ? status.data.persistence_mode : "DISABLED";
  const working = rosters.ok ? rosters.data.items.find((r) => WORKING_STATUSES.includes(r.status)) : undefined;
  return (
    <Card title="Save as a draft roster" description="An allocation is only a preview. Saving it creates a DRAFT roster that can be validated, approved and published.">
      <div className="space-y-4 p-5">
        {persistence === "DISABLED" ? (
          <Notice>Saving rosters is turned off in the Examination Service (M3_DEMO_PERSISTENCE). You can still run and compare allocations.</Notice>
        ) : !rosters.ok ? (
          <DutyReadError error={rosters.error} what="saved rosters" />
        ) : working ? (
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <RosterStatusBadge status={working.status} />
            <span className="text-ink">Version {working.version} · {working.roster_label.toLowerCase()} roster · {number.format(working.assignment_count)} duties</span>
            <TextLink href={`/exams/duties/rosters/${working.roster_id}`}>Open roster</TextLink>
            <p className="basis-full text-[13px] text-muted">This period already has a working roster. Reject it or publish it before saving another, or make changes through a revision.</p>
          </div>
        ) : (
          <>
            {persistence === "MEMORY" && <Notice>Drafts are kept in memory only and are lost when the Examination Service restarts.</Notice>}
            <ActionForm
              action={saveRosterDraft.bind(null, run?.run_id ?? "", roster?.label ?? "FAIR")}
              submitLabel={roster ? `Save the ${roster.label.toLowerCase()} roster as a draft` : "Save as a draft"}
              pendingLabel="Checking and saving…"
              icon="save"
              disabledReason={!run || !roster ? "Run an allocation first." : run.status !== "COMPLETED" ? "Only a completed allocation can be saved." : undefined}
            />
          </>
        )}
      </div>
    </Card>
  );
}
