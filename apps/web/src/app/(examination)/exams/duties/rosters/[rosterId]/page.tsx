import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";
import { ActionDialog } from "@/components/console/action-dialog";
import { Badge, Card, formatDate, formatDateTime, PageHeader, PageSkeleton, TableSkeleton } from "@/components/console/ui";
import { Icon } from "@/components/icon";
import { changeRosterStatus } from "@/lib/duty-allocation-actions";
import { getRoster, getRosterHistory, listChangeRequests, listDutyStaff, UUID_PATTERN } from "@/lib/duty-allocation";
import { clock, fixed, roomKey, titleCase, type RosterAssignment, type RosterDetail } from "@/lib/duty-allocation-types";
import {
  cell, ChangeStatusBadge, DutyReadError, first, headCell, Notice, number, RosterStatusBadge, TextLink, ViolationList, type SearchParams,
} from "../../_components/duty-ui";

export const metadata: Metadata = { title: "Duty roster" };

type Params = Promise<{ rosterId: string }>;

export default function RosterPage({ params, searchParams }: { params: Params; searchParams: SearchParams }) {
  return <Suspense fallback={<PageSkeleton />}><Roster params={params} searchParams={searchParams} /></Suspense>;
}

async function Roster({ params, searchParams }: { params: Params; searchParams: SearchParams }) {
  const [{ rosterId }, query] = await Promise.all([params, searchParams]);
  if (!UUID_PATTERN.test(rosterId)) notFound();
  const result = await getRoster(rosterId);
  const back = <Link href="/exams/duties/rosters" className="inline-flex items-center gap-1 text-sm font-semibold text-primary-strong"><Icon name="arrow_back" size={18} />All rosters</Link>;
  if (!result.ok) {
    if (result.error.status === 404) notFound();
    return <div className="space-y-4">{back}<Card><DutyReadError error={result.error} what="this roster" /></Card></div>;
  }
  const r = result.data;
  const justSaved = first(query.saved) === "1" && r.status === "DRAFT";

  return (
    <div className="space-y-6">
      {back}
      <PageHeader
        title={`Duty roster · version ${r.version}`}
        description={<><span className="font-mono">{r.period_code}</span> · {r.roster_label === "FAIR" ? "Fair" : "Baseline"} roster · {number.format(r.assignment_count)} duties</>}
        actions={<WorkflowActions roster={r} />}
      />
      {justSaved && <Notice tone="success" title="Saved as a draft">The roster was checked and stored. Validate it, then send it for approval.</Notice>}
      <Notice tone={r.synthetic ? "warn" : "info"}>{r.notice}{!r.persistence.durable && ` ${r.persistence.notice}`}</Notice>

      <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <Card title="Status">
          <div className="space-y-4 p-5 text-sm">
            <div className="flex flex-wrap items-center gap-2"><RosterStatusBadge status={r.status} />{r.synthetic && <Badge tone="neutral">Synthetic data</Badge>}</div>
            <dl className="grid grid-cols-[9rem_minmax(0,1fr)] gap-x-3 gap-y-2">
              <dt className="text-muted">Saved</dt><dd className="text-ink">{formatDateTime(r.created_at)}</dd>
              {r.validated_at && <><dt className="text-muted">Validated</dt><dd className="text-ink">{formatDateTime(r.validated_at)}</dd></>}
              {r.approved_at && <><dt className="text-muted">Approved</dt><dd className="text-ink">{formatDateTime(r.approved_at)}</dd></>}
              {r.published_at && <><dt className="text-muted">Published</dt><dd className="text-ink">{formatDateTime(r.published_at)}</dd></>}
              {r.rejected_at && <><dt className="text-muted">Rejected</dt><dd className="text-ink">{formatDateTime(r.rejected_at)}{r.rejection_reason && <span className="block text-xs text-muted">{r.rejection_reason}</span>}</dd></>}
              <dt className="text-muted">Timetable</dt>
              <dd className="text-ink"><span className="break-all font-mono text-xs">{r.timetable_reference}</span><span className="block text-xs text-muted">{r.timetable_verified ? "Matches a published timetable" : "Not checked against a published timetable"}</span></dd>
              <dt className="text-muted">Data source</dt><dd className="text-ink">{r.data_source}</dd>
              {r.parent_roster_id && <><dt className="text-muted">Revision of</dt><dd><TextLink href={`/exams/duties/rosters/${r.parent_roster_id}`}>Previous version</TextLink></dd></>}
            </dl>
          </div>
        </Card>
        <Card title="Validation and fairness">
          <div className="space-y-4 p-5 text-sm">
            {r.validation.valid === null ? (
              <Notice>Not validated yet. Validation re-checks every staffing rule against the current data.</Notice>
            ) : r.validation.valid ? (
              <Notice tone="success" title="Every rule passed">{number.format(r.validation.assignments_checked)} duties checked{r.validation.checked_at ? ` on ${formatDateTime(r.validation.checked_at)}` : ""}.</Notice>
            ) : (
              <Notice tone="danger" title={`${r.validation.violations.length} problems found`}><ViolationList violations={r.validation.violations} /></Notice>
            )}
            <dl className="grid grid-cols-[9rem_minmax(0,1fr)] gap-x-3 gap-y-2">
              <dt className="text-muted">Gini coefficient</dt><dd className="tabular-nums text-ink">{fixed(r.fairness_metrics.gini)}</dd>
              <dt className="text-muted">Duties per person</dt><dd className="text-ink">{r.fairness_metrics.min_duties ?? "—"}–{r.fairness_metrics.max_duties ?? "—"}</dd>
              <dt className="text-muted">Back-to-back pairs</dt><dd className="text-ink">{r.fairness_metrics.back_to_back_pairs ?? "—"}</dd>
            </dl>
            <p className="flex flex-wrap gap-4">
              <TextLink href={`/exams/duties/fairness?roster=${r.roster_id}`}>Fairness details</TextLink>
              <TextLink href={`/exams/duties/changes?roster=${r.roster_id}`}>Swaps and replacements</TextLink>
            </p>
          </div>
        </Card>
      </div>

      <Card title="Duties" description="Supervisors and invigilators per exam room.">
        <Suspense fallback={<TableSkeleton rows={8} />}>
          <Assignments roster={r} />
        </Suspense>
      </Card>

      <div className="grid items-start gap-6 xl:grid-cols-2">
        <Card title="History" description="Every change to this roster, newest first.">
          <Suspense fallback={<TableSkeleton rows={3} />}><History rosterId={r.roster_id} /></Suspense>
        </Card>
        <Card title="Change requests" actions={<TextLink href={`/exams/duties/changes?roster=${r.roster_id}`}>Manage</TextLink>}>
          <Suspense fallback={<TableSkeleton rows={3} />}><Requests rosterId={r.roster_id} /></Suspense>
        </Card>
      </div>
    </div>
  );
}

function WorkflowActions({ roster: r }: { roster: RosterDetail }) {
  const act = (action: Parameters<typeof changeRosterStatus>[2]) => changeRosterStatus.bind(null, r.roster_id, r.row_version, action);
  const note = { name: "reason", label: "Note", type: "textarea" as const, maxLength: 500, hint: "Kept in the roster history." };
  const reason = { name: "reason", label: "Reason", type: "textarea" as const, required: true, maxLength: 500 };
  const can = (action: string) => r.next_actions.includes(action);
  return (
    <>
      {can("VALIDATE") && (
        <ActionDialog label={r.status === "VALIDATED" ? "Validate again" : "Validate"} icon="task_alt" variant={r.status === "DRAFT" ? "primary" : "secondary"}
          title="Validate this roster" description="Re-checks every staffing rule against the current data. Problems are listed and nothing is changed."
          fields={[note]} submitLabel="Validate" action={act("validate")} />
      )}
      {can("APPROVE") && (
        <ActionDialog label="Approve" icon="verified_user" variant="primary" title="Approve this roster"
          description="Approval needs the examinations:approve permission. The roster is not published yet."
          fields={[note]} submitLabel="Approve" action={act("approve")} />
      )}
      {r.status === "APPROVED" && (
        <ActionDialog label="Publish" icon="publish" variant="primary" title="Publish this roster"
          description="Publishing makes the roster final for this exam period. Later changes need a new revision."
          fields={[note, { name: "confirm", label: "Type PUBLISH to confirm", required: true, code: true, autoComplete: "off" }]}
          submitLabel="Publish" action={act("publish")}
          disabledReason={!r.can_be_published ? "Rosters built on synthetic demo data can never be published." : !can("PUBLISH") ? "This roster cannot be published now." : undefined} />
      )}
      {can("REJECT") && (
        <ActionDialog label="Reject" icon="cancel" variant="danger" tone="danger" title="Reject this roster"
          description="A rejected roster cannot be approved. Create a revision to continue from it." fields={[reason]} submitLabel="Reject" action={act("reject")} />
      )}
      {can("REVISE") && (
        <ActionDialog label="Create revision" icon="edit_note" title="Create a revision"
          description="Copies this roster into a new DRAFT version. The current version stays unchanged until the revision is published."
          fields={[reason]} submitLabel="Create revision" action={act("revise")} />
      )}
    </>
  );
}

async function Assignments({ roster }: { roster: RosterDetail }) {
  const staff = await listDutyStaff(roster.period_code, true);
  const nameOf = new Map(staff.ok ? staff.data.items.map((s) => [s.staff_id, s.full_name ?? s.staff_number]) : []);
  const rooms = new Map<string, RosterAssignment[]>();
  for (const a of [...roster.assignments].sort((x, y) => x.starts_at.localeCompare(y.starts_at) || x.venue_id.localeCompare(y.venue_id))) {
    const key = roomKey(a.session_id, a.venue_id);
    rooms.set(key, [...(rooms.get(key) ?? []), a]);
  }
  if (!rooms.size) return <p className="px-5 py-4 text-sm text-muted">This roster has no duties.</p>;
  const chip = (a: RosterAssignment) => (
    <span key={`${a.staff_id}-${a.role}`} title={a.staff_number ?? undefined} className="inline-flex rounded-md bg-secondary-soft px-2 py-0.5 text-xs font-medium text-secondary-ink">
      {nameOf.get(a.staff_id) ?? a.staff_number ?? a.staff_id.slice(0, 8)}
    </span>
  );
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[52rem] text-left">
        <caption className="sr-only">Duties per exam room</caption>
        <thead className="border-b border-line bg-background text-xs text-muted">
          <tr>{["Date and time", "Module", "Room", "Supervisors", "Invigilators"].map((label) => <th key={label} scope="col" className={headCell}>{label}</th>)}</tr>
        </thead>
        <tbody className="divide-y divide-line">
          {[...rooms.values()].map((duties) => {
            const a = duties[0];
            return (
              <tr key={roomKey(a.session_id, a.venue_id)} className="align-top">
                <td className={`${cell} whitespace-nowrap`}><span className="font-medium text-ink">{formatDate(a.starts_at.slice(0, 10))}</span><span className="block text-xs">{clock(a.starts_at)}–{clock(a.ends_at)}</span></td>
                <td className={cell}><span className="font-mono text-[13px] text-ink">{a.module_code ?? "—"}</span><span className="block break-all text-xs">{a.session_id}</span></td>
                <td className={cell}>{a.venue_id}</td>
                <td className={cell}><span className="flex flex-wrap gap-1">{duties.filter((d) => d.role === "SUPERVISOR").map(chip)}</span></td>
                <td className={cell}><span className="flex flex-wrap gap-1">{duties.filter((d) => d.role === "INVIGILATOR").map(chip)}</span></td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

async function History({ rosterId }: { rosterId: string }) {
  const result = await getRosterHistory(rosterId);
  if (!result.ok) return <DutyReadError error={result.error} what="the history" />;
  const events = [...result.data.events].reverse();
  if (!events.length) return <p className="px-5 py-4 text-sm text-muted">No history yet.</p>;
  return (
    <ol className="divide-y divide-line">
      {events.map((e) => (
        <li key={e.event_id} className="px-5 py-3 text-sm">
          <p className="flex flex-wrap items-center gap-2">
            <span className="font-semibold text-ink">{titleCase(e.action)}</span>
            {e.to_status && <span className="text-xs text-muted">{e.from_status ? `${titleCase(e.from_status)} → ` : ""}{titleCase(e.to_status)}</span>}
          </p>
          <p className="mt-0.5 text-xs text-muted">{formatDateTime(e.at)}</p>
          {e.reason && <p className="mt-1 text-[13px] text-ink">{e.reason}</p>}
        </li>
      ))}
    </ol>
  );
}

async function Requests({ rosterId }: { rosterId: string }) {
  const result = await listChangeRequests(rosterId);
  if (!result.ok) return <DutyReadError error={result.error} what="change requests" />;
  if (!result.data.length) return <p className="px-5 py-4 text-sm text-muted">No swaps or replacements requested.</p>;
  return (
    <ul className="divide-y divide-line">
      {result.data.slice(0, 6).map((c) => (
        <li key={c.request_id} className="flex flex-wrap items-center justify-between gap-2 px-5 py-3 text-sm">
          <span><span className="font-semibold text-ink">{c.kind === "SWAP" ? "Swap" : "Replacement"}</span><span className="block text-xs text-muted">{formatDateTime(c.requested_at)} · {c.reason}</span></span>
          <ChangeStatusBadge status={c.status} />
        </li>
      ))}
    </ul>
  );
}
