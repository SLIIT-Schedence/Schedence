import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { type ReactNode, Suspense } from "react";
import { ActionDialog, type DialogField } from "@/components/console/action-dialog";
import { Badge, Card, EmptyState, formatDate, formatDateTime, Kpi, PageSkeleton } from "@/components/console/ui";
import { Icon } from "@/components/icon";
import { requireExaminationAdmin } from "@/lib/examination-access";
import {
  getSavedTimetable, getTimetableHistory, listExamModules, listSavedTimetables,
  type SavedTimetable, type SavedTimetableDetail, type TimetableHistoryEntry,
} from "@/lib/examination";
import { approveSavedTimetable, publishSavedTimetable } from "@/lib/examination-timetable-actions";
import {
  actorLabel, REASON_MAX, REASON_MIN, redactStudentIds, savedSessions, STATUS_LABELS, STATUS_TONES,
} from "@/lib/examination-timetables";
import { ExaminationReadError } from "../../_components/read-error";
import { RefreshButton } from "../../_components/refresh-button";
import { TimetableBrowser } from "../../generate/_components/timetable-browser";

export const metadata: Metadata = { title: "Saved timetable" };

type Params = Promise<{ id: string }>;
const number = new Intl.NumberFormat("en-GB");
const seconds = new Intl.NumberFormat("en-GB", { maximumFractionDigits: 1 });

export default function SavedTimetablePage({ params }: { params: Params }) {
  return (
    <div className="space-y-6">
      <Link href="/exams/timetables" className="inline-flex items-center gap-1 text-sm font-semibold text-primary-strong">
        <Icon name="arrow_back" size={18} />Saved timetables
      </Link>
      <Suspense fallback={<PageSkeleton />}>
        <TimetableDetail params={params} />
      </Suspense>
    </div>
  );
}

async function TimetableDetail({ params }: { params: Params }) {
  const account = await requireExaminationAdmin();
  const { id: raw } = await params;
  if (!/^[1-9][0-9]{0,9}$/.test(raw) || Number(raw) > 2_147_483_647) notFound();
  const id = Number(raw);
  const [detail, history, modules] = await Promise.all([getSavedTimetable(id), getTimetableHistory(id), listExamModules()]);
  if (!detail.ok) {
    if (detail.error.status === 404) notFound();
    return <Card><ExaminationReadError error={detail.error} what="this saved timetable" /></Card>;
  }
  const timetable = detail.data;
  // Only an APPROVED timetable can be published; find out whether the cycle already has a published one.
  const published = timetable.status === "APPROVED"
    ? await listSavedTimetables({ period_code: timetable.period_code, status: "PUBLISHED", limit: 1, offset: 0 })
    : null;
  const current = published?.ok ? published.data.items[0] ?? null : undefined;
  const sessions = savedSessions(timetable, modules.ok ? modules.data : []);
  const me = account.id;

  return (
    <>
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div className="min-w-0">
          <p className="font-mono text-xs text-secondary-ink">{timetable.period_code} · FINAL</p>
          <h1 className="mt-1 text-[1.75rem] font-semibold leading-tight tracking-[-0.02em] text-ink font-stretch-semi-expanded sm:text-[2rem]">
            {timetable.period_name} · Draft #{timetable.draft_number}
          </h1>
          <p className="mt-1.5 text-[15px] text-muted">
            Saved {formatDateTime(timetable.created_at)} by {actorLabel(timetable.created_by, me)} · Revision {timetable.revision}
            {timetable.source_timetable_id ? ` · Regenerated from #${timetable.source_timetable_id}` : ""}
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          <Badge tone={STATUS_TONES[timetable.status]}>{STATUS_LABELS[timetable.status]}</Badge>
          <RefreshButton label="Refresh" />
        </div>
      </div>

      <Workflow timetable={timetable} current={current} me={me} />

      <div className="grid grid-cols-2 gap-3 sm:gap-4 xl:grid-cols-4">
        <Kpi icon="menu_book" tone="primary" label="Sessions" value={number.format(timetable.summary.sessions)}
          hint={`${number.format(timetable.summary.multi_session_modules)} modules split into several sittings`} />
        <Kpi icon="history" tone="primary" label="Exam days used" value={number.format(timetable.summary.exam_days_used)}
          hint={`${formatDate(timetable.inputs.start_date)} – ${formatDate(timetable.inputs.end_date)}`} />
        <Kpi icon="meeting_room" tone="primary" label="Seats allocated" value={number.format(timetable.summary.seat_allocations)}
          hint={`${number.format(timetable.summary.room_allocations)} room allocations`} />
        <Kpi icon="verified_user" tone={timetable.validation.passed ? "accent" : "danger"} label="Validation"
          value={timetable.validation.passed ? "Passed" : "Failed"} hint="Independent H1–H11 check when saved" />
      </div>

      <TimetableBrowser sessions={sessions} valid statusMessage="" title="Saved timetable sessions"
        description="Sessions, venues and candidate counts as saved. Individual student seat allocations are not shown here." />
      {!modules.ok && <p className="rounded-xl bg-secondary-soft p-4 text-sm text-secondary-ink">Module specializations could not be loaded, so the specialization filter is unavailable.</p>}

      <Summary timetable={timetable} me={me} />
      <History history={history} me={me} />
    </>
  );
}

function Workflow({ timetable, current, me }: { timetable: SavedTimetableDetail; current: SavedTimetable | null | undefined; me: string }) {
  const steps = [
    { status: "DRAFT", label: "Saved as draft", at: timetable.created_at, by: timetable.created_by },
    { status: "APPROVED", label: "Approved", at: timetable.approved_at, by: timetable.approved_by },
    { status: "PUBLISHED", label: "Published", at: timetable.published_at, by: timetable.published_by },
  ] as const;
  const reason: DialogField = {
    name: "reason", label: "Reason", type: "textarea", required: true, minLength: REASON_MIN, maxLength: REASON_MAX,
    hint: `${REASON_MIN}–${REASON_MAX} characters, kept in the timetable history.`,
  };
  const publishFields: DialogField[] = [
    reason,
    ...(current !== null ? [{
      name: "replace_published", label: "Currently published timetable", type: "radio" as const, required: true, defaultValue: "keep",
      hint: current ? `Draft #${current.draft_number} is published for this cycle now.`
        : "The published timetable of this cycle could not be checked. The service refuses to publish if one exists and you keep it.",
      options: [
        { value: "keep", label: "Keep it", description: "Do not publish if another timetable of this cycle is published." },
        { value: "archive", label: "Archive it and publish this one",
          description: "The currently published timetable becomes ARCHIVED and is no longer the published timetable. This cannot be undone." },
      ],
    }] : []),
    { name: "confirm", label: "Type PUBLISH to confirm", required: true, pattern: "PUBLISH", placeholder: "PUBLISH", code: true },
  ];

  return (
    <Card title="Approval and publication" description="Each change is checked by the Examination Service against the current inputs, the stored content and this revision.">
      <div className="space-y-5 p-5">
        <ol className="grid gap-3 sm:grid-cols-3">
          {steps.map((step) => {
            const done = Boolean(step.at);
            return (
              <li key={step.status} className={`rounded-xl border p-4 text-sm ${done ? "border-accent bg-accent text-accent-ink" : "border-line text-muted"}`}>
                <p className="flex items-center gap-2 font-semibold"><Icon name={done ? "check_circle" : "info"} size={18} filled={done} />{step.label}</p>
                <p className="mt-1 text-xs">{step.at ? `${formatDateTime(step.at)} · ${actorLabel(step.by, me)}` : "Not yet"}</p>
              </li>
            );
          })}
        </ol>
        {timetable.status === "ARCHIVED" && <Note icon="archive">This timetable was archived when another timetable of the cycle was published. It can no longer be approved or published.</Note>}

        {timetable.status === "DRAFT" && <div className="flex flex-wrap items-center gap-3">
          <ActionDialog
            label="Approve draft" icon="task_alt" variant="primary" title={`Approve draft #${timetable.draft_number}`}
            description={<>The service reloads the current inputs, settings and seating capacities and validates this timetable against H1–H11 again. A stale draft is refused. Approving does <strong>not</strong> publish it.</>}
            fields={[reason]} submitLabel="Approve draft"
            action={approveSavedTimetable.bind(null, timetable.timetable_id, timetable.revision)}
          />
          <p className="text-sm text-muted">Needs the examinations:approve permission. Revision {timetable.revision} is checked to prevent concurrent changes.</p>
        </div>}

        {timetable.status === "APPROVED" && <div className="space-y-3">
          {current && <Note icon="info">Draft #{current.draft_number} is currently published for this cycle. Publishing this one requires archiving it.</Note>}
          <div className="flex flex-wrap items-center gap-3">
            <ActionDialog
              label="Publish timetable" icon="publish" variant="primary" title={`Publish draft #${timetable.draft_number}`}
              description="Publishing makes this the official timetable of the cycle. The same checks as approval run again. Students are not notified automatically."
              fields={publishFields} submitLabel="Publish timetable"
              action={publishSavedTimetable.bind(null, timetable.timetable_id, timetable.revision)}
            />
            <p className="text-sm text-muted">Needs the examinations:approve permission. Approval never publishes automatically.</p>
          </div>
        </div>}

        {timetable.status === "PUBLISHED" && <Note icon="check_circle">This is the published timetable of its cycle. Publishing another approved timetable of the cycle, with the archive option, replaces it.</Note>}
      </div>
    </Card>
  );
}

function Summary({ timetable, me }: { timetable: SavedTimetableDetail; me: string }) {
  const facts: [string, ReactNode][] = [
    ["Independent H1–H11 validation", timetable.validation.passed ? "Passed when saved" : "Not passed"],
    ["Rules checked", timetable.validation.rules_checked.join(", ") || "—"],
    ["Solver", `${timetable.solver_status} · ${timetable.mode === "optimized" ? "Optimized" : "Feasibility"} · ${seconds.format(timetable.solver.wall_time_seconds)} s of ${seconds.format(timetable.solver.time_limit_seconds)} s`],
    ["Cycle inputs", `${number.format(timetable.inputs.modules)} modules (${number.format(timetable.inputs.repeat_only_modules)} repeat-only) · ${number.format(timetable.inputs.students)} students · ${number.format(timetable.inputs.registrations)} registrations`],
    ["Exam calendar", `${formatDate(timetable.inputs.start_date)} – ${formatDate(timetable.inputs.end_date)} · `
      + `${number.format(timetable.inputs.exam_days.length)} exam days`
      + (timetable.inputs.day_start_time && timetable.inputs.day_end_time
        ? ` · ${timetable.inputs.day_start_time.slice(0, 5)}–${timetable.inputs.day_end_time.slice(0, 5)}` : "")
      + (timetable.inputs.calendar_source === "selected" ? " · Selected for this timetable" : " · Configured period")],
    ["Excluded dates", timetable.inputs.exclusions?.length
      ? timetable.inputs.exclusions.map((item) => `${formatDate(item.excluded_date)} (${redactStudentIds(item.reason)})`).join(", ")
      : timetable.inputs.exclusions ? "None" : "Not recorded"],
    ["Settings used", `Scheduling version ${timetable.settings_version} · Seating ruleset ${timetable.seating_ruleset_version}`],
    ["Workload penalty (S1–S5)", timetable.workload ? seconds.format(timetable.workload.total_weighted_penalty) : "Not returned"],
    ["Stored content check", timetable.content_fingerprint ? "Fingerprint recorded; checked before approval and publication" : "No fingerprint (saved before content checks)"],
    ["Approved", timetable.approved_at ? `${formatDateTime(timetable.approved_at)} · ${actorLabel(timetable.approved_by, me)}` : "Not approved"],
    ["Published", timetable.published_at ? `${formatDateTime(timetable.published_at)} · ${actorLabel(timetable.published_by, me)}` : "Not published"],
  ];
  const violations = timetable.validation.violations;
  return (
    <Card title="Validation summary" description="As recorded when this timetable was saved. Approval and publication check it again against current inputs.">
      <dl className="grid gap-5 p-5 sm:grid-cols-2">
        {facts.map(([label, value]) => <div key={label}><dt className="text-xs font-medium text-muted">{label}</dt><dd className="mt-1 text-sm font-semibold text-ink">{value}</dd></div>)}
      </dl>
      {(violations.length > 0 || timetable.notices.length > 0 || timetable.excluded_registrations.total > 0) && (
        <div className="space-y-3 border-t border-line p-5 text-sm leading-6 text-muted">
          {violations.length > 0 && <ul className="list-disc pl-5 text-danger">{violations.map((item, index) => <li key={index}>{item.rule}: {redactStudentIds(item.message)}</li>)}</ul>}
          {timetable.notices.length > 0 && <ul className="list-disc pl-5">{timetable.notices.map((item, index) => <li key={index}>{redactStudentIds(item)}</li>)}</ul>}
          {timetable.excluded_registrations.total > 0 && <p className="rounded-xl bg-secondary-soft p-4 text-secondary-ink">
            {number.format(timetable.excluded_registrations.total)} registrations were outside this cycle ({number.format(timetable.excluded_registrations.normal)} normal · {number.format(timetable.excluded_registrations.repeat)} repeat). {redactStudentIds(timetable.excluded_registrations.note)}
          </p>}
        </div>
      )}
    </Card>
  );
}

const ACTIONS: Record<TimetableHistoryEntry["action"], string> = {
  create: "Saved as draft", approve: "Approved", publish: "Published", archive: "Archived",
};

function History({ history, me }: { history: Awaited<ReturnType<typeof getTimetableHistory>>; me: string }) {
  return (
    <Card title="History" description="Every status change, oldest first, as recorded by the Examination Service.">
      {!history.ok ? <ExaminationReadError error={history.error} what="the timetable history" />
        : !history.data.length ? <EmptyState icon="history" title="No history recorded">This timetable was saved before history was kept.</EmptyState>
          : <ol className="divide-y divide-line">
            {history.data.map((entry) => (
              <li key={entry.history_id} className="flex flex-col gap-1 px-5 py-4 sm:flex-row sm:items-start sm:justify-between">
                <div className="min-w-0">
                  <p className="text-sm font-semibold text-ink">
                    {ACTIONS[entry.action]}
                    <span className="ml-2 font-normal text-muted">{entry.from_status ? `${STATUS_LABELS[entry.from_status]} → ` : ""}{STATUS_LABELS[entry.to_status]} · Revision {entry.revision}</span>
                  </p>
                  <p className="mt-1 text-sm text-muted">“{redactStudentIds(entry.reason)}”</p>
                </div>
                <p className="shrink-0 text-xs text-muted sm:text-right">{formatDateTime(entry.changed_at)}<span className="block">{actorLabel(entry.changed_by, me)}</span></p>
              </li>
            ))}
          </ol>}
    </Card>
  );
}

function Note({ icon, children }: { icon: "archive" | "info" | "check_circle"; children: ReactNode }) {
  return <p className="flex gap-2.5 rounded-xl bg-secondary-soft p-4 text-sm leading-6 text-secondary-ink"><Icon name={icon} size={18} className="mt-0.5" />{children}</p>;
}
