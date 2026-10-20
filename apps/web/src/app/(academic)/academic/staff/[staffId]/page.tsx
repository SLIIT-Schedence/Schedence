import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";
import { Card, EmptyState, first, formatDate, LoadError, PageSkeleton, TableSkeleton } from "@/components/console/ui";
import { Icon } from "@/components/icon";
import { type Appointment, getAcademicContext, getStaff, getWorkload, listAppointments, type Workload } from "@/lib/academic";
import { isUuid } from "@/lib/identity-records";
import {
  ACADEMIC_SERVICE,
  AppointmentBadge,
  CodeTag,
  combinationRule,
  EmploymentBadge,
  Fact,
  formatDay,
  hours,
  period,
  StaffAvatar,
  UNIT_TYPES,
} from "../../_components/academic-ui";
import { AssignedModulesCard, AvailabilityCard, TeachingWorkloadCard } from "./_components/teaching-sections";

export const metadata: Metadata = { title: "Staff member" };

type Params = PageProps<"/academic/staff/[staffId]">["params"];
type SearchParams = PageProps<"/academic/staff/[staffId]">["searchParams"];

export default function StaffMemberPage({ params, searchParams }: PageProps<"/academic/staff/[staffId]">) {
  return (
    <div className="space-y-6">
      <Link
        href="/academic/staff"
        className="inline-flex items-center gap-1.5 rounded-lg text-sm font-semibold text-muted transition-colors hover:text-primary-strong"
      >
        <Icon name="arrow_back" size={18} />
        All staff
      </Link>
      <Suspense fallback={<PageSkeleton />}>
        <StaffDetail params={params} searchParams={searchParams} />
      </Suspense>
    </div>
  );
}

async function StaffDetail({ params, searchParams }: { params: Params; searchParams: SearchParams }) {
  const { staffId } = await params;
  if (!isUuid(staffId)) notFound();

  const result = await getStaff(staffId);
  if (!result.ok) {
    if (result.error.status === 404) notFound();
    return (
      <Card>
        <LoadError error={result.error} what="this staff member" retryHref={`/academic/staff/${staffId}`} service={ACADEMIC_SERVICE} />
      </Card>
    );
  }
  const member = result.data;
  const institution =
    (await getAcademicContext())?.institutions.find((row) => row.id === member.institutionId)?.name ?? "This institution";
  const teaching = { institutionId: member.institutionId, institution, staffNumber: member.staffNumber, fullName: member.fullName };

  return (
    <div className="space-y-6">
      <div className="flex min-w-0 items-start gap-4">
        <StaffAvatar staff={member} size="lg" />
        <div className="min-w-0">
          <h1 className="text-[1.75rem] font-semibold leading-tight tracking-[-0.02em] text-ink font-stretch-semi-expanded sm:text-[2rem]">
            {member.fullName}
          </h1>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <CodeTag>{member.staffNumber}</CodeTag>
            <EmploymentBadge status={member.employmentStatus} />
          </div>
        </div>
      </div>

      <dl className="grid grid-cols-2 gap-3 sm:gap-4 xl:grid-cols-4">
        <Fact label="Academic rank" value={member.academicRank ?? "Not recorded"}>
          Informational only
        </Fact>
        <Fact
          label="Email"
          value={
            <span className="block truncate text-[15px] sm:text-base" title={member.email ?? undefined}>
              {member.email ?? "Not recorded"}
            </span>
          }
        />
        <Fact label="Schedence login" value={member.identityUserId ? "Linked" : "None"}>
          {member.identityUserId ? "Can sign in to Schedence" : "Can still hold positions"}
        </Fact>
        <Fact label="In the register since" value={formatDate(member.createdAt)} />
      </dl>

      <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)]">
        <Suspense
          fallback={
            <Card title="Administrative workload">
              <TableSkeleton rows={3} />
            </Card>
          }
        >
          <WorkloadCard staffId={member.id} />
        </Suspense>
        <Suspense
          fallback={
            <Card title="Appointments">
              <TableSkeleton rows={3} />
            </Card>
          }
        >
          <AppointmentsCard staffId={member.id} searchParams={searchParams} />
        </Suspense>
      </div>

      <div className="grid items-start gap-6 xl:grid-cols-2">
        <Suspense fallback={<Card title="Assigned modules"><TableSkeleton rows={3} /></Card>}>
          <AssignedModulesCard {...teaching} />
        </Suspense>
        <Suspense fallback={<Card title="Teaching workload"><TableSkeleton rows={3} /></Card>}>
          <TeachingWorkloadCard {...teaching} />
        </Suspense>
      </div>
      <AvailabilityCard />
    </div>
  );
}

async function WorkloadCard({ staffId }: { staffId: string }) {
  const result = await getWorkload(staffId);
  return (
    <Card
      title="Administrative workload"
      description={result.ok ? `Protected hours a week, as of ${formatDay(result.data.asOf)}` : "Protected hours a week"}
    >
      {!result.ok ? (
        <LoadError error={result.error} what="the workload" retryHref={`/academic/staff/${staffId}`} service={ACADEMIC_SERVICE} />
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

async function AppointmentsCard({ staffId, searchParams }: { staffId: string; searchParams: SearchParams }) {
  const history = first((await searchParams).history) === "1";
  const result = await listAppointments(staffId, { includeEnded: history });
  const base = `/academic/staff/${staffId}`;

  return (
    <Card
      title="Appointments"
      description={history ? "Every appointment, including ended ones" : "Current and scheduled administrative positions"}
      actions={
        <nav aria-label="Appointments to show" className="inline-flex rounded-xl bg-background p-1">
          {[
            { label: "Current", href: base, selected: !history },
            { label: "History", href: `${base}?history=1`, selected: history },
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
