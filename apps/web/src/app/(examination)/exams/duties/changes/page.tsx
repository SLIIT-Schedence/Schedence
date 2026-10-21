import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";
import { ActionDialog } from "@/components/console/action-dialog";
import { Card, EmptyState, formatDateTime, PageHeader, TableSkeleton } from "@/components/console/ui";
import { decideChange } from "@/lib/duty-allocation-actions";
import { getRoster, listChangeRequests, listDutyStaff, listRosters, UUID_PATTERN } from "@/lib/duty-allocation";
import {
  assignmentValue, clock, WORKING_STATUSES, type ChangeRequest, type RosterAssignment, type RosterDetail,
} from "@/lib/duty-allocation-types";
import { RefreshButton } from "../../_components/refresh-button";
import { ChangeStatusBadge, DutyReadError, first, Notice, primaryButton, RosterStatusBadge, TextLink, type SearchParams } from "../_components/duty-ui";
import { ChangeForm } from "./_components/change-forms";

export const metadata: Metadata = { title: "Swaps and replacements" };

export default function ChangesPage({ searchParams }: { searchParams: SearchParams }) {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Swaps and replacements"
        description="Swap two duties, or find an emergency replacement. The solver checks every rule and the effect on fairness; changes are applied only after approval."
        actions={<RefreshButton label="Refresh" />}
      />
      <Suspense fallback={<Card><TableSkeleton rows={6} /></Card>}>
        <Changes searchParams={searchParams} />
      </Suspense>
    </div>
  );
}

async function Changes({ searchParams }: { searchParams: SearchParams }) {
  const params = await searchParams;
  const rosterId = first(params.roster);
  if (!rosterId || !UUID_PATTERN.test(rosterId)) return <ChooseRoster />;
  const result = await getRoster(rosterId);
  if (!result.ok) return <Card><DutyReadError error={result.error} what="the roster" /></Card>;
  const r = result.data;
  const [staff, requests] = await Promise.all([listDutyStaff(r.period_code, true), listChangeRequests(r.roster_id)]);
  const names: Record<string, string> = Object.fromEntries(staff.ok ? staff.data.items.map((s) => [s.staff_id, s.full_name ?? s.staff_number]) : []);
  const working = WORKING_STATUSES.includes(r.status);

  return (
    <>
      <Card>
        <div className="flex flex-wrap items-center justify-between gap-3 p-5 text-sm">
          <p className="flex flex-wrap items-center gap-2">
            <span className="font-semibold text-ink">Roster version {r.version}</span>
            <span className="font-mono text-xs text-secondary-ink">{r.period_code}</span>
            <RosterStatusBadge status={r.status} />
          </p>
          <span className="flex flex-wrap gap-4"><TextLink href={`/exams/duties/rosters/${r.roster_id}`}>Open roster</TextLink><TextLink href="/exams/duties/changes">Choose another roster</TextLink></span>
        </div>
      </Card>
      {!working ? (
        <Notice tone="warn" title="This roster cannot be changed here">
          {r.status === "PUBLISHED" ? "A published roster is never changed directly. Create a revision from the roster page, then request changes on the new draft." : "Only DRAFT, VALIDATED and APPROVED rosters accept changes."}
        </Notice>
      ) : (
        <>
          {r.status === "APPROVED" && <Notice tone="warn">Applying a change to an approved roster withdraws its approval; it must be approved again.</Notice>}
          <div className="grid items-start gap-6 xl:grid-cols-2">
            <Card title="Swap two duties" description="Both staff members exchange duties. Roles and staffing rules must still hold.">
              <ChangeForm kind="swap" rosterId={r.roster_id} rowVersion={r.row_version} duties={dutyOptions(r, names)} staff={[]} names={names} />
            </Card>
            <Card title="Emergency replacement" description="Someone cannot take a duty. The solver finds the fairest staff member who can.">
              <ChangeForm kind="replacement" rosterId={r.roster_id} rowVersion={r.row_version} duties={dutyOptions(r, names)} names={names}
                staff={staff.ok ? staff.data.items.filter((s) => s.eligible).map((s) => ({ value: s.staff_id, label: `${s.full_name ?? s.staff_number} (${s.staff_number})` })) : []} />
            </Card>
          </div>
        </>
      )}
      <Card title="Change requests" description="Requested changes and their decisions, newest first.">
        {!requests.ok ? <DutyReadError error={requests.error} what="change requests" /> : !requests.data.length ? (
          <EmptyState icon="event_repeat" title="No change requests yet">Preview a swap or replacement, then submit it for approval.</EmptyState>
        ) : (
          <ul className="divide-y divide-line">
            {[...requests.data].sort((a, b) => b.requested_at.localeCompare(a.requested_at)).map((c) => <RequestRow key={c.request_id} request={c} names={names} />)}
          </ul>
        )}
      </Card>
    </>
  );
}

function describe(a: RosterAssignment, names: Record<string, string>) {
  return `${a.starts_at.slice(0, 10)} ${clock(a.starts_at)} · ${a.module_code ?? a.session_id} · ${a.venue_id} · ${a.role === "SUPERVISOR" ? "Supervisor" : "Invigilator"} · ${names[a.staff_id] ?? a.staff_number ?? a.staff_id.slice(0, 8)}`;
}

function dutyOptions(r: RosterDetail, names: Record<string, string>) {
  return [...r.assignments]
    .sort((a, b) => a.starts_at.localeCompare(b.starts_at) || a.venue_id.localeCompare(b.venue_id) || a.role.localeCompare(b.role))
    .map((a) => ({ value: assignmentValue(a), label: describe(a, names) }));
}

function RequestRow({ request: c, names }: { request: ChangeRequest; names: Record<string, string> }) {
  const decide = (decision: "approve" | "reject" | "cancel" | "apply") => decideChange.bind(null, c.request_id, decision);
  const note = { name: "reason", label: "Note", type: "textarea" as const, maxLength: 500 };
  return (
    <li className="space-y-3 px-5 py-4 text-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="flex flex-wrap items-center gap-2"><span className="font-semibold text-ink">{c.kind === "SWAP" ? "Swap" : "Replacement"}</span><ChangeStatusBadge status={c.status} /></p>
          <p className="mt-1 text-xs text-muted">Requested {formatDateTime(c.requested_at)} · {c.reason}</p>
          {c.decision_reason && <p className="mt-1 text-xs text-muted">Decision: {c.decision_reason}</p>}
        </div>
        <div className="flex flex-wrap gap-2">
          {c.next_actions.includes("APPROVE") && <ActionDialog label="Approve" icon="task_alt" variant="primary" title="Approve this change" description="Approval needs examinations:approve. The roster changes only when the request is applied." fields={[note]} submitLabel="Approve" action={decide("approve")} />}
          {c.next_actions.includes("APPLY") && <ActionDialog label="Apply" icon="check_circle" variant="primary" title="Apply this change" description="The roster, its version and every rule are checked again before the change is written. An approved roster goes back for approval." submitLabel="Apply" action={decide("apply")} />}
          {c.next_actions.includes("REJECT") && <ActionDialog label="Reject" icon="cancel" variant="danger" tone="danger" title="Reject this change" fields={[{ ...note, label: "Reason", required: true }]} submitLabel="Reject" action={decide("reject")} />}
          {c.next_actions.includes("CANCEL") && <ActionDialog label="Cancel request" icon="close" title="Cancel this request" description="Only the requester, or someone with examinations:manage, can cancel." fields={[note]} submitLabel="Cancel request" action={decide("cancel")} />}
        </div>
      </div>
      <div className="grid gap-3 text-[13px] lg:grid-cols-2">
        <div><p className="text-xs font-semibold text-muted">Removed</p><ul className="mt-1 space-y-0.5 text-ink">{c.removed.map((a, i) => <li key={i} className="line-through decoration-danger/60">{describe(a, names)}</li>)}</ul></div>
        <div><p className="text-xs font-semibold text-muted">Added</p><ul className="mt-1 space-y-0.5 text-ink">{c.added.map((a, i) => <li key={i}>{describe(a, names)}</li>)}</ul></div>
      </div>
    </li>
  );
}

async function ChooseRoster() {
  const result = await listRosters(undefined, undefined, 50);
  if (!result.ok) return <Card><DutyReadError error={result.error} what="saved rosters" /></Card>;
  const rosters = result.data.items;
  return (
    <Card title="Choose a roster" description="Changes are made on a saved roster. Working rosters are listed first.">
      {!rosters.length ? (
        <EmptyState icon="fact_check" title="No saved rosters yet" action={<Link href="/exams/duties/allocation" className={primaryButton}>Allocate duties</Link>}>
          Save an allocation as a draft roster first.
        </EmptyState>
      ) : (
        <ul className="divide-y divide-line">
          {[...rosters].sort((a, b) => Number(WORKING_STATUSES.includes(b.status)) - Number(WORKING_STATUSES.includes(a.status))).map((r) => (
            <li key={r.roster_id}>
              <Link href={`/exams/duties/changes?roster=${r.roster_id}`} className="flex flex-wrap items-center justify-between gap-3 px-5 py-3.5 text-sm hover:bg-background">
                <span><span className="font-semibold text-ink">Version {r.version} · {r.roster_label === "FAIR" ? "Fair" : "Baseline"}</span><span className="ml-2 font-mono text-[11px] text-secondary-ink">{r.period_code}</span></span>
                <RosterStatusBadge status={r.status} />
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
