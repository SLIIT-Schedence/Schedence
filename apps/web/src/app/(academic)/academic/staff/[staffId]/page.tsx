import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";
import { Badge, Card, EmptyState, first, formatDate, Kpi, LoadError, PageSkeleton, TableSkeleton } from "@/components/console/ui";
import { Icon } from "@/components/icon";
import { type Appointment, getAcademicContext, getStaff, getWorkload, listAppointments, type Workload } from "@/lib/academic";
import { isUuid } from "@/lib/identity-records";
import { STAFF_TABS, staffHref, staffHours, staffTab, type StaffTab } from "@/lib/staff-detail";
import { ACADEMIC_SERVICE, AppointmentBadge, CodeTag, combinationRule, EmploymentBadge, formatDay, hours, period, StaffAvatar, UNIT_TYPES } from "../../_components/academic-ui";
import { AssignedModulesCard, AvailabilityCard, getStaffTeaching, HistoricalTeaching, liveReason, TeachingWorkloadCard, type Live } from "./_components/teaching-sections";
import { AssignedSessions } from "./_components/assigned-sessions";
import { WorkloadCharts } from "./_components/workload-charts";

export const metadata: Metadata = { title: "Staff member" };
type Params = PageProps<"/academic/staff/[staffId]">["params"];
type SearchParams = PageProps<"/academic/staff/[staffId]">["searchParams"];

export default function StaffMemberPage({ params, searchParams }: PageProps<"/academic/staff/[staffId]">) {
  return <div className="space-y-6">
    <Link href="/academic/staff" className="inline-flex items-center gap-1.5 rounded-lg text-sm font-semibold text-muted hover:text-primary-strong"><Icon name="arrow_back" size={18} />All staff</Link>
    <Suspense fallback={<PageSkeleton />}><StaffDetail params={params} searchParams={searchParams} /></Suspense>
  </div>;
}

async function StaffDetail({ params, searchParams }: { params: Params; searchParams: SearchParams }) {
  const { staffId } = await params;
  if (!isUuid(staffId)) notFound();
  const result = await getStaff(staffId);
  if (!result.ok) {
    if (result.error.status === 404) notFound();
    return <Card><LoadError error={result.error} what="this staff member" retryHref={`/academic/staff/${staffId}`} service={ACADEMIC_SERVICE} /></Card>;
  }
  const member = result.data;
  const [context, teaching, search] = await Promise.all([getAcademicContext(), getStaffTeaching(member.institutionId, member.staffNumber), searchParams]);
  const institution = context?.institutions.find((row) => row.id === member.institutionId)?.name ?? "This institution";
  const tab = staffTab(first(search.tab), first(search.history));
  const requested = first(search.version);
  const selected = teaching.status === "ok" ? teaching.versions.find((version) => version.versionId === requested) ?? teaching.versions[0] : undefined;
  const selectedId = selected?.versionId;
  const row = selected?.workload;
  const selectedTab = STAFF_TABS.find((item) => item.id === tab)!;

  return <div className="space-y-6">
    <section aria-label="Staff summary" className="rounded-2xl border border-line bg-surface p-5 sm:p-6">
      <div className="flex flex-col gap-5 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex min-w-0 items-start gap-4"><StaffAvatar staff={member} size="lg" /><div className="min-w-0">
          <h1 className="break-words text-[1.75rem] font-semibold leading-tight tracking-[-0.02em] text-ink sm:text-[2rem]">{member.fullName}</h1>
          <p className="mt-1 text-sm text-muted">{member.academicRank ?? "Academic rank not recorded"} · {institution}</p>
          <div className="mt-3 flex flex-wrap items-center gap-2"><CodeTag>{member.staffNumber}</CodeTag><EmploymentBadge status={member.employmentStatus} /><Badge tone="neutral">{member.identityUserId ? "Login linked" : "No linked login"}</Badge></div>
        </div></div>
        <Link href={staffHref(member.id, "sessions", selectedId)} className="inline-flex shrink-0 items-center justify-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-semibold text-white hover:bg-primary-strong"><Icon name="calendar_month" size={18} />View sessions</Link>
      </div>
    </section>

    <section aria-label="Quick teaching statistics" className="space-y-3">
      <p className="text-[13px] text-muted">{selected ? `Teaching figures for ${selected.label}.` : "Teaching figures become available when a validated timetable can be read."}</p>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi icon="calendar_month" tone="primary" label="Assigned sessions" value={selected ? selected.sessions.length : "Not available"} hint="Combined groups count as one delivery" href={staffHref(member.id, "sessions", selectedId)} />
        <Kpi icon="schedule" tone="accent" label="Assigned teaching" value={staffHours(row?.assigned_teaching_hours)} hint="Hours in the selected version" href={staffHref(member.id, "workload", selectedId)} />
        <Kpi icon="fact_check" tone="neutral" label="Weekly target" value={staffHours(row?.weekly_teaching_target_hours)} hint="Stored target for this version" href={staffHref(member.id, "workload", selectedId)} />
        <Kpi icon="event_repeat" tone="neutral" label="Teaching days" value={row?.teaching_day_count ?? "Not available"} hint={row?.weekly_teaching_day_limit ? `Limit: ${row.weekly_teaching_day_limit} days` : "Selected timetable version"} />
      </div>
    </section>

    {teaching.status === "ok" && selected && <Card><form action={`/academic/staff/${member.id}`} className="flex flex-col gap-3 p-4 sm:flex-row sm:items-end sm:px-5">
      <input type="hidden" name="tab" value={tab} />
      <label className="min-w-0 flex-1 text-[13px] font-semibold text-muted">Timetable version<select name="version" defaultValue={selectedId} className="mt-1 block w-full rounded-xl border border-line bg-surface px-3 py-2.5 text-sm text-ink">{teaching.versions.map((version) => <option key={version.versionId} value={version.versionId}>{version.label}</option>)}</select></label>
      <button type="submit" className="rounded-xl border border-line bg-surface px-4 py-2.5 text-sm font-semibold text-ink hover:bg-background">View version</button>
    </form><div className="flex flex-wrap items-center gap-2 border-t border-line px-5 py-3 text-xs text-muted"><Badge tone="accent">Validated</Badge><Badge tone="neutral">{selected.summary.lifecycle_status.replaceAll("_", " ").toLowerCase()}</Badge><span>{formatDate(selected.summary.calendar.week_start)}–{formatDate(selected.summary.calendar.week_end)}</span>{selected.summary.preview_label && <Badge tone="primary">{selected.summary.preview_label}</Badge>}</div></Card>}
    {requested && selected && requested !== selectedId && <p role="status" className="text-sm text-muted">The requested version is unavailable. Showing the selected version above.</p>}

    <nav aria-label="Staff sections" className="overflow-x-auto border-b border-line"><div className="flex min-w-max gap-1">{STAFF_TABS.map((item) => <Link key={item.id} href={staffHref(member.id, item.id, selectedId)} scroll={false} aria-current={tab === item.id ? "page" : undefined} className={`border-b-2 px-4 py-3 text-sm font-semibold transition-colors ${tab === item.id ? "border-primary text-primary-strong" : "border-transparent text-muted hover:text-ink"}`}>{item.label}</Link>)}</div></nav>
    <section aria-label={selectedTab.label} className="min-w-0 space-y-6">
      {tab === "overview" && <>
        <div className="grid items-start gap-6 xl:grid-cols-2"><Card title="Staff details"><dl className="grid gap-5 p-5 sm:grid-cols-2">{[
          ["Staff number", member.staffNumber], ["Academic rank", member.academicRank ?? "Not recorded"], ["Email", member.email ?? "Not recorded"],
          ["Schedence login", member.identityUserId ? "Linked" : "No linked account"], ["Registered", formatDate(member.createdAt)], ["Institution", institution],
        ].map(([label, value]) => <div key={label} className="min-w-0"><dt className="text-xs font-semibold text-muted">{label}</dt><dd className="mt-1 break-words text-sm text-ink">{value}</dd></div>)}</dl></Card>
          <Suspense fallback={<Card title="Current appointments"><TableSkeleton rows={3} /></Card>}><AppointmentsCard staffId={member.id} version={selectedId} /></Suspense>
        </div>
        {selected ? <AssignedModulesCard version={selected} /> : <TeachingUnavailable teaching={teaching} institution={institution} staffId={member.id} tab={tab} version={requested} />}
      </>}
      {tab === "sessions" && (selected ? <AssignedSessions version={selected} /> : <TeachingUnavailable teaching={teaching} institution={institution} staffId={member.id} tab={tab} version={requested} />)}
      {tab === "workload" && <>
        {selected ? <><TeachingWorkloadCard version={selected} /><WorkloadCharts version={selected} /></> : <TeachingUnavailable teaching={teaching} institution={institution} staffId={member.id} tab={tab} version={requested} />}
        <Suspense fallback={<Card title="Administrative workload"><TableSkeleton rows={3} /></Card>}><WorkloadCard staffId={member.id} version={selectedId} /></Suspense>
      </>}
      {tab === "availability" && <AvailabilityCard />}
      {tab === "history" && <>
        <Suspense fallback={<Card title="Appointment history"><TableSkeleton rows={3} /></Card>}><AppointmentsCard staffId={member.id} history version={selectedId} /></Suspense>
        <Suspense fallback={<Card title="Historical experimental previews"><TableSkeleton rows={3} /></Card>}><HistoricalTeaching staffNumber={member.staffNumber} fullName={member.fullName} /></Suspense>
      </>}
    </section>
  </div>;
}

function TeachingUnavailable({ teaching, institution, staffId, tab, version }: { teaching: Live; institution: string; staffId: string; tab?: StaffTab; version?: string }) {
  if (teaching.status === "error") return <Card><LoadError error={teaching.error} what="teaching assignments and workload" retryHref={staffHref(staffId, tab, version)} service="Teaching" /></Card>;
  return <Card><EmptyState icon="calendar_month" title="No validated teaching timetable">{teaching.status === "empty" ? liveReason(teaching, institution) : "No timetable version is available for this staff member."} Assigned sessions and teaching totals will appear here when available.</EmptyState></Card>;
}

async function WorkloadCard({ staffId, version }: { staffId: string; version?: string }) {
  const result = await getWorkload(staffId);
  return (
    <Card
      title="Administrative workload"
      description={result.ok ? `Protected hours a week, as of ${formatDay(result.data.asOf)}` : "Protected hours a week"}
    >
      {!result.ok ? (
        <LoadError error={result.error} what="the workload" retryHref={staffHref(staffId, "workload", version)} service={ACADEMIC_SERVICE} />
      ) : (
        <WorkloadBody workload={result.data} />
      )}
    </Card>
  );
}

function WorkloadBody({ workload }: { workload: Workload }) {
  const { aggregate, appointments } = workload;
  return (
    <>
      <div className="px-5 py-5">
        {aggregate.computed ? (
          <>
            <p className="text-[2rem] font-semibold leading-none tracking-tight text-ink font-stretch-semi-expanded">
              {hours(aggregate.totalHoursPerWeek ?? 0)}
              <span className="ml-1.5 text-sm font-medium tracking-normal text-muted">a week</span>
            </p>
            <p className="mt-2 text-[13px] text-muted">{combinationRule(aggregate.rule)}</p>
          </>
        ) : (
          <p className="flex gap-2.5 rounded-xl bg-danger-soft px-3.5 py-3 text-[13px] leading-5 text-danger">
            <Icon name="info" size={18} filled className="mt-px" />
            <span>
              <span className="font-semibold">No single total.</span> {aggregate.reason} Timetables use each appointment&apos;s
              hours below.
            </span>
          </p>
        )}
      </div>
      {appointments.length > 0 && (
        <ul className="divide-y divide-line border-t border-line">
          {appointments.map((item) => (
            <li key={item.appointmentId} className="flex items-center justify-between gap-3 px-5 py-3">
              <div className="min-w-0">
                <p className="truncate text-sm font-semibold text-ink">{item.positionName}</p>
                <p className="truncate text-[13px] text-muted">
                  {[item.unitName ?? item.unitCode, item.hoursSource === "OVERRIDE" ? "Personal override" : null]
                    .filter(Boolean)
                    .join(" · ") || "Whole institution"}
                </p>
              </div>
              <span className="shrink-0 text-sm font-semibold tabular-nums text-ink">
                {item.hoursSource === "NO_POLICY" ? <span className="text-danger">No hours set</span> : hours(item.hoursPerWeek)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

async function AppointmentsCard({ staffId, history = false, version }: { staffId: string; history?: boolean; version?: string }) {
  const result = await listAppointments(staffId, { includeEnded: history });
  const base = staffHref(staffId, history ? "history" : "overview", version);

  return (
    <Card
      title={history ? "Appointment history" : "Current appointments"}
      description={history ? "Every appointment, including ended ones" : "Current and scheduled administrative positions"}
      actions={
        <nav aria-label="Appointments to show" className="inline-flex rounded-xl bg-background p-1">
          {[
            { label: "Current", href: staffHref(staffId, "overview", version), selected: !history },
            { label: "History", href: staffHref(staffId, "history", version), selected: history },
          ].map((option) => (
            <Link
              key={option.label}
              href={option.href}
              scroll={false}
              aria-current={option.selected ? "page" : undefined}
              className={`rounded-lg px-3 py-1 text-[13px] font-semibold transition-colors ${
                option.selected ? "bg-surface text-ink shadow-[0_1px_3px_rgb(29_33_66/0.12)]" : "text-muted hover:text-ink"
              }`}
            >
              {option.label}
            </Link>
          ))}
        </nav>
      }
    >
      {!result.ok ? (
        <LoadError error={result.error} what="appointments" retryHref={base} service={ACADEMIC_SERVICE} />
      ) : result.data.length === 0 ? (
        <EmptyState icon="work" title={history ? "No appointments on record" : "No current appointments"}>
          {history
            ? "This staff member has never held an administrative position."
            : "This staff member holds no administrative position now or in the future."}
        </EmptyState>
      ) : (
        <ul className="divide-y divide-line">
          {result.data.map((appointment) => (
            <AppointmentItem key={appointment.id} appointment={appointment} />
          ))}
        </ul>
      )}
    </Card>
  );
}

function AppointmentItem({ appointment }: { appointment: Appointment }) {
  const unit = appointment.unitName ?? appointment.unitCode;
  return (
    <li className="px-5 py-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <Link
            href={`/academic/positions/${appointment.positionId}`}
            className="text-sm font-semibold text-ink underline-offset-2 hover:text-primary-strong hover:underline"
          >
            {appointment.positionName}
          </Link>
          <p className="mt-0.5 text-[13px] text-muted">
            {unit ? `${appointment.unitType ? `${UNIT_TYPES[appointment.unitType]}: ` : ""}${unit}` : "Whole institution"}
            {appointment.unitCode && appointment.unitName && <span className="font-mono text-xs"> · {appointment.unitCode}</span>}
          </p>
        </div>
        <AppointmentBadge status={appointment.status} />
      </div>
      <p className="mt-2 flex items-center gap-1.5 text-[13px] text-ink">
        <Icon name="calendar_month" size={16} className="text-secondary" />
        {period(appointment.effectiveFrom, appointment.effectiveUntil)}
      </p>
      {appointment.hoursOverride !== null && (
        <p className="mt-1.5 flex items-start gap-1.5 text-[13px] text-ink">
          <Icon name="schedule" size={16} className="mt-px text-secondary" />
          <span>
            {hours(appointment.hoursOverride)} a week instead of the position&apos;s policy
            {appointment.overrideReason && <span className="text-muted"> · {appointment.overrideReason}</span>}
          </span>
        </p>
      )}
      {appointment.status === "ENDED" && appointment.endReason && (
        <p className="mt-1.5 text-[13px] text-muted">Ended: {appointment.endReason}</p>
      )}
      {appointment.notes && <p className="mt-1.5 text-[13px] leading-5 text-muted">{appointment.notes}</p>}
    </li>
  );
}
