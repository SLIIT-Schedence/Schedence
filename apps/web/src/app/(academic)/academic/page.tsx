import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";
import { Suspense } from "react";
import { BarChart } from "@/components/console/charts";
import { Card, EmptyState, formatDate, Kpi, LoadError, PageHeader, Skeleton, TableSkeleton, ToneIcon } from "@/components/console/ui";
import { Icon } from "@/components/icon";
import { allStaff, getAcademicContext, listPositions, type StaffRecord } from "@/lib/academic";
import { ACADEMIC_SERVICE, CodeTag, EmploymentBadge, StaffAvatar } from "./_components/academic-ui";

export const metadata: Metadata = { title: "Academic overview" };

/** Ranks beyond this many are grouped as "Other ranks" in the chart. */
const TOP_RANKS = 6;
const RECENT_STAFF = 6;

export default function AcademicOverviewPage() {
  return (
    <Suspense fallback={<OverviewSkeleton />}>
      <Overview />
    </Suspense>
  );
}

async function Overview() {
  const context = await getAcademicContext();
  if (!context) redirect("/");
  const name = context.institutions.length === 1 ? context.primary.name : undefined;

  const [staffResult, positionsResult] = await Promise.all([allStaff(), listPositions({ includeInactive: true, pageSize: 100 })]);

  if (!staffResult.ok && !positionsResult.ok) {
    return (
      <div className="space-y-6">
        <Header name={name} />
        <Card>
          <LoadError error={staffResult.error} what="academic data" retryHref="/academic" service={ACADEMIC_SERVICE} />
        </Card>
      </div>
    );
  }

  const staff = staffResult.ok ? staffResult.data.items : [];
  const activeStaff = staff.filter((member) => member.employmentStatus === "ACTIVE").length;
  const linked = staff.filter((member) => member.identityUserId).length;
  const linkedShare = staff.length ? Math.round((linked / staff.length) * 100) : 0;

  const positions = positionsResult.ok ? positionsResult.data.items : [];
  const activePositions = positions.filter((position) => position.active);
  const withoutHours = activePositions.filter((position) => position.hoursPerWeek === null);
  const withHours = activePositions
    .filter((position) => position.hoursPerWeek !== null)
    .sort((a, b) => (b.hoursPerWeek ?? 0) - (a.hoursPerWeek ?? 0));

  const recent = staff.toSorted((a, b) => b.createdAt.localeCompare(a.createdAt)).slice(0, RECENT_STAFF);

  return (
    <div className="space-y-6">
      <Header name={name} />

      <div className="grid grid-cols-2 gap-3 sm:gap-4 xl:grid-cols-4">
        <Kpi
          icon="badge"
          tone="primary"
          label="Academic staff"
          value={staffResult.ok ? staffResult.data.total : "—"}
          hint={staffResult.ok ? `${activeStaff} currently employed` : "Couldn't load staff"}
          href="/academic/staff"
        />
        <Kpi
          icon="how_to_reg"
          tone="accent"
          label="With a Schedence login"
          value={staffResult.ok ? linked : "—"}
          hint={
            staffResult.ok ? (
              <span className="flex items-center gap-2">
                <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-secondary-soft">
                  <span className="block h-full rounded-full bg-accent-ink" style={{ width: `${linkedShare}%` }} />
                </span>
                {linkedShare}% of staff
              </span>
            ) : undefined
          }
        />
        <Kpi
          icon="work"
          tone="neutral"
          label="Administrative positions"
          value={positionsResult.ok ? activePositions.length : "—"}
          hint={
            positionsResult.ok
              ? positions.length > activePositions.length
                ? `${positions.length - activePositions.length} disabled`
                : "Such as Head of Department"
              : "Couldn't load positions"
          }
          href="/academic/positions"
        />
        <Kpi
          icon={withoutHours.length ? "pending_actions" : "schedule"}
          tone={withoutHours.length ? "danger" : "accent"}
          label="Without hours in force"
          value={positionsResult.ok ? withoutHours.length : "—"}
          hint={
            withoutHours.length
              ? "Timetables can't count these hours yet"
              : !positionsResult.ok
                ? undefined
                : activePositions.length
                  ? "Every active position has hours set"
                  : "No positions defined yet"
          }
          href="/academic/positions"
        />
      </div>

      <div className="grid items-start gap-6 xl:grid-cols-2">
        <Card
          title="Administrative hours by position"
          description="Weekly hours under the policy in force today, before any personal override"
          actions={<ViewAll href="/academic/positions">All positions</ViewAll>}
        >
          {!positionsResult.ok ? (
            <LoadError error={positionsResult.error} what="positions" retryHref="/academic" service={ACADEMIC_SERVICE} />
          ) : withHours.length === 0 ? (
            <EmptyState icon="work" title="No hours set yet">
              {activePositions.length
                ? "None of the active positions has an hours policy in force today."
                : "Your Institution Admin defines positions such as Head of Department or Program Coordinator, with the weekly hours each one takes."}
            </EmptyState>
          ) : (
            <div className="px-5 py-5">
              <BarChart
                caption="Weekly administrative hours by position"
                unit={{ one: "hour a week", other: "hours a week" }}
                shares={false}
                head={["Position", "Hours a week"]}
                bars={withHours.map((position) => ({ key: position.id, label: position.name, value: position.hoursPerWeek ?? 0 }))}
              />
            </div>
          )}
        </Card>

        <Card title="Staff by academic rank" description="Everyone in the staff register, as recorded">
          {!staffResult.ok ? (
            <LoadError error={staffResult.error} what="staff" retryHref="/academic" service={ACADEMIC_SERVICE} />
          ) : staff.length === 0 ? (
            <EmptyState icon="badge" title="No staff yet">
              Staff appear here once your Institution Admin adds them to the register.
            </EmptyState>
          ) : (
            <div className="px-5 py-5">
              <BarChart caption="Staff by academic rank" unit={{ one: "person", other: "people" }} head={["Rank", "Staff"]} bars={rankBars(staff)} />
            </div>
          )}
        </Card>
      </div>

      <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
        <Card title="Recently added staff" description="The latest additions to the staff register" actions={<ViewAll href="/academic/staff">All staff</ViewAll>}>
          {!staffResult.ok ? (
            <LoadError error={staffResult.error} what="staff" retryHref="/academic" service={ACADEMIC_SERVICE} />
          ) : recent.length === 0 ? (
            <EmptyState icon="badge" title="No staff yet" />
          ) : (
            <ul className="divide-y divide-line">
              {recent.map((member) => (
                <li key={member.id}>
                  <Link href={`/academic/staff/${member.id}`} className="flex items-center gap-3 px-5 py-3.5 transition-colors hover:bg-background/60">
                    <StaffAvatar staff={member} />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-semibold text-ink">{member.fullName}</p>
                      <p className="truncate text-[13px] text-muted">{member.academicRank ?? "Rank not recorded"}</p>
                    </div>
                    <div className="hidden shrink-0 text-right sm:block">
                      <CodeTag>{member.staffNumber}</CodeTag>
                      <p className="mt-1 text-xs text-muted">Added {formatDate(member.createdAt)}</p>
                    </div>
                    <span className="sm:hidden">
                      <EmploymentBadge status={member.employmentStatus} />
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card title="Teaching timetables">
          <div className="flex gap-4 px-5 py-5">
            <ToneIcon icon="calendar_month" tone="neutral" />
            <div className="min-w-0 text-sm leading-6 text-muted">
              <p className="font-semibold text-ink">Not connected yet</p>
              <p className="mt-1">
                Generating and approving teaching timetables will appear here once the teaching timetable service joins
                Schedence. It will read the staff and administrative hours shown on this console, so keeping them right
                now means fewer clashes later.
              </p>
              {withoutHours.length > 0 && (
                <p className="mt-3 flex gap-2 rounded-xl bg-danger-soft px-3 py-2.5 text-[13px] leading-5 text-danger">
                  <Icon name="info" size={18} filled className="mt-px" />
                  {withoutHours.length === 1
                    ? `${withoutHours[0].name} has no hours in force. Ask your Institution Admin to set them.`
                    : `${withoutHours.length} positions have no hours in force. Ask your Institution Admin to set them.`}
                </p>
              )}
            </div>
          </div>
        </Card>
      </div>
    </div>
  );
}

/** Staff counted per recorded rank, largest first, with the long tail and blanks grouped at the end. */
function rankBars(staff: StaffRecord[]) {
  const counts = new Map<string, number>();
  let unrecorded = 0;
  for (const member of staff) {
    const rank = member.academicRank?.trim();
    if (rank) counts.set(rank, (counts.get(rank) ?? 0) + 1);
    else unrecorded++;
  }
  const ranked = [...counts].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  const shown = ranked.length > TOP_RANKS + 1 ? ranked.slice(0, TOP_RANKS) : ranked;
  const other = ranked.slice(shown.length).reduce((sum, [, count]) => sum + count, 0);
  return [
    ...shown.map(([rank, value]) => ({ key: rank, label: rank, value })),
    ...(other ? [{ key: "_other", label: "Other ranks", value: other }] : []),
    ...(unrecorded ? [{ key: "_none", label: "Not recorded", value: unrecorded }] : []),
  ];
}

function Header({ name }: { name?: string }) {
  return (
    <PageHeader
      title="Dashboard"
      description={
        name
          ? `Academic staff, administrative positions and their weekly hours at ${name}.`
          : "Academic staff, administrative positions and their weekly hours across your institutions."
      }
      actions={
        <Link
          href="/adaptive"
          className="inline-flex h-10 items-center gap-2 rounded-xl bg-primary px-4 text-sm font-semibold text-white transition-colors hover:bg-primary-strong"
        >
          <Icon name="event_repeat" size={18} />
          Adaptive rescheduling
        </Link>
      }
    />
  );
}

function ViewAll({ href, children }: { href: string; children: string }) {
  return (
    <Link
      href={href}
      className="inline-flex items-center gap-1 rounded-lg text-sm font-semibold text-primary-strong underline-offset-2 hover:underline"
    >
      {children}
      <Icon name="arrow_forward" size={16} />
    </Link>
  );
}

function OverviewSkeleton() {
  return (
    <div className="space-y-6" aria-busy="true" aria-label="Loading the dashboard">
      <Skeleton className="h-9 w-48" />
      <div className="grid grid-cols-2 gap-3 sm:gap-4 xl:grid-cols-4">
        {[0, 1, 2, 3].map((key) => (
          <Skeleton key={key} className="h-[8.5rem] rounded-2xl" />
        ))}
      </div>
      <div className="grid gap-6 xl:grid-cols-2">
        <Card title="Administrative hours by position">
          <TableSkeleton rows={4} />
        </Card>
        <Card title="Staff by academic rank">
          <TableSkeleton rows={4} />
        </Card>
      </div>
    </div>
  );
}
