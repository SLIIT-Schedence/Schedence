import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";
import { connection } from "next/server";
import { Suspense } from "react";
import { ActivityFeed } from "@/components/console/activity";
import { BarChart, ColumnChart } from "@/components/console/charts";
import {
  Card,
  EmptyState,
  formatDate,
  Kpi,
  LoadError,
  PageHeader,
  Skeleton,
  TableSkeleton,
  ToneIcon,
} from "@/components/console/ui";
import { Icon } from "@/components/icon";
import { QUIET_EVENTS } from "@/lib/audit-events";
import type { InstitutionSummary } from "@/lib/identity";
import { daysAgo } from "@/lib/identity-records";
import {
  auditSince,
  countUsers,
  getAdminContext,
  listInstitutionAudit,
  listProvisioning,
  listUsers,
  OPEN_PROVISIONING,
} from "@/lib/institution";
import { INSTITUTION_ROLES, INVITABLE_ROLE_CODES, roleInfo } from "@/lib/roles";
import { countByWeek, recentWeeks } from "@/lib/weeks";
import { InviteDialog } from "./_components/dialogs";
import { InvitationStatus } from "./_components/invitation-status";

export const metadata: Metadata = { title: "Institution overview" };

const WEEKS = 12;

export default function InstitutionOverviewPage() {
  return (
    <Suspense fallback={<OverviewSkeleton />}>
      <Overview />
    </Suspense>
  );
}

async function Overview() {
  await connection();
  const context = await getAdminContext();
  if (!context) redirect("/");
  const { account, institutions, primary } = context;
  const scope = institutions.length === 1 ? primary.id : undefined;
  const weeks = recentWeeks(WEEKS);
  const monthAgo = daysAgo(30);

  const [active, disabled, roleCounts, open, failed, recentInvites, created, activity, people] = await Promise.all([
    countUsers({ status: "ACTIVE" }),
    countUsers({ status: "DISABLED" }),
    Promise.all(INSTITUTION_ROLES.map((role) => countUsers({ roleCode: role.code }))),
    listProvisioning({ statuses: OPEN_PROVISIONING, pageSize: 1 }),
    listProvisioning({ statuses: ["FAILED"], pageSize: 1 }),
    listProvisioning({ pageSize: 6 }),
    auditSince("user.created", new Date(weeks[0].start).toISOString(), { institutionId: scope }),
    listInstitutionAudit({ institutionId: scope, pageSize: 20 }),
    listUsers({ pageSize: 100 }),
  ]);

  if (active === undefined && disabled === undefined) {
    return (
      <div className="space-y-6">
        <Header institutions={institutions} name={primary.name} />
        <Card>
          <LoadError
            error={{ status: 0, code: "UNREACHABLE", message: "" }}
            what="your institution's accounts"
            retryHref="/admin"
          />
        </Card>
      </div>
    );
  }

  const total = (active ?? 0) + (disabled ?? 0);
  const activeShare = total ? Math.round(((active ?? 0) / total) * 100) : 0;
  const createdTimes = created.ok ? created.data.map((entry) => entry.occurredAt) : [];
  const addedThisMonth = createdTimes.filter((time) => time >= monthAgo).length;
  const weekly = countByWeek(weeks, createdTimes);
  const roleCount = (code: string) => roleCounts[INSTITUTION_ROLES.findIndex((role) => role.code === code)];
  const openCount = open.ok ? open.data.total : undefined;
  const failedCount = failed.ok ? failed.data.total : 0;
  const lookups = {
    me: account.id,
    institutions: new Map(institutions.map((institution) => [institution.id, institution])),
    people: new Map((people.ok ? people.data.items : []).map((person) => [person.id, person.fullName])),
  };
  const feed = activity.ok ? activity.data.items.filter((entry) => !QUIET_EVENTS.has(entry.action)).slice(0, 8) : [];

  return (
    <div className="space-y-6">
      <Header institutions={institutions} name={institutions.length === 1 ? primary.name : undefined} />

      <div className="grid grid-cols-2 gap-3 sm:gap-4 xl:grid-cols-4">
        <Kpi
          icon="group"
          tone="primary"
          label="People"
          value={total}
          hint={created.ok ? `+${addedThisMonth} in the last 30 days` : "Accounts in your institution"}
          href="/admin/users"
        />
        <Kpi
          icon="how_to_reg"
          tone="accent"
          label="Active accounts"
          value={active ?? "—"}
          href="/admin/users?status=ACTIVE"
          hint={
            <span className="flex items-center gap-2">
              <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-secondary-soft">
                <span className="block h-full rounded-full bg-accent-ink" style={{ width: `${activeShare}%` }} />
              </span>
              {activeShare}% of all
            </span>
          }
        />
        <Kpi
          icon="school"
          tone="neutral"
          label="Lecturers"
          value={roleCount("LECTURER") ?? "—"}
          hint="With lecturer portal access"
          href="/admin/users?role=LECTURER"
        />
        <Kpi
          icon={failedCount ? "error" : "mail"}
          tone={failedCount ? "danger" : "accent"}
          label="Open invitations"
          value={openCount ?? "—"}
          hint={failedCount ? `${failedCount} failed · retry them` : "None waiting on Cognito"}
          href="/admin/invitations"
        />
      </div>

      <section aria-labelledby="quick-invite">
        <h2 id="quick-invite" className="sr-only">
          Quick invite
        </h2>
        <div className="grid gap-3 sm:gap-4 xl:grid-cols-3">
          {INVITABLE_ROLE_CODES.map((code) => {
            const role = roleInfo(code);
            const people = roleCount(code);
            return (
              <div
                key={code}
                className="flex flex-col gap-4 rounded-2xl border border-line bg-surface p-4 sm:flex-row sm:items-center sm:p-5 xl:flex-col xl:items-stretch"
              >
                <div className="flex min-w-0 flex-1 items-start gap-3">
                  <ToneIcon icon={role.icon} tone="primary" />
                  <div className="min-w-0">
                    <p className="text-[15px] font-semibold text-ink">
                      {role.name}s{" "}
                      <span className="font-normal text-muted">
                        · {people === undefined ? "—" : `${people} ${people === 1 ? "person" : "people"}`}
                      </span>
                    </p>
                    <p className="mt-1 text-[13px] leading-5 text-muted">{role.description}</p>
                  </div>
                </div>
                <div className="shrink-0">
                  <InviteDialog institutions={institutions} role={code} label={`Invite ${role.name.toLowerCase()}`} variant="secondary" />
                </div>
              </div>
            );
          })}
        </div>
      </section>

      <div className="grid items-start gap-6 xl:grid-cols-2">
        <Card title="People by role" description="Active role holders. A person with two roles counts in both.">
          <div className="px-5 py-5">
            <BarChart
              caption="People by role"
              unit={{ one: "person", other: "people" }}
              bars={INSTITUTION_ROLES.map((role, index) => ({
                key: role.code,
                label: role.name,
                value: roleCounts[index] ?? 0,
              }))}
            />
          </div>
        </Card>
        <Card
          title="New accounts"
          description={`Accounts created per week, last ${WEEKS} weeks`}
          actions={
            created.ok ? (
              <span className="text-[13px] text-muted">
                <span className="font-semibold text-ink">{createdTimes.length}</span> in total
              </span>
            ) : undefined
          }
        >
          <div className="px-5 py-5">
            {created.ok ? (
              <ColumnChart
                caption={`Accounts created per week, last ${WEEKS} weeks`}
                unit={{ one: "account", other: "accounts" }}
                points={weeks.map((week, index) => ({ key: week.key, label: week.label, title: week.title, value: weekly[index] }))}
              />
            ) : (
              <LoadError error={created.error} what="account history" retryHref="/admin" />
            )}
          </div>
        </Card>
      </div>

      <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
        <Card
          title="Recent invitations"
          description="The latest people invited and how it went"
          actions={<ViewAll href="/admin/invitations">All invitations</ViewAll>}
        >
          {!recentInvites.ok ? (
            <LoadError error={recentInvites.error} what="invitations" retryHref="/admin" />
          ) : recentInvites.data.items.length === 0 ? (
            <EmptyState icon="mail" title="No invitations yet" action={<InviteDialog institutions={institutions} variant="secondary" />}>
              Invite your examination admins, venue managers and lecturers to get started.
            </EmptyState>
          ) : (
            <ul className="divide-y divide-line">
              {recentInvites.data.items.map((invite) => (
                <li key={invite.id} className="flex items-center gap-3 px-5 py-3.5">
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-semibold text-ink">{invite.fullName}</p>
                    <p className="truncate text-[13px] text-muted">{invite.email}</p>
                  </div>
                  <div className="shrink-0 text-right">
                    <InvitationStatus status={invite.status} />
                    <p className="mt-1 text-xs text-muted">{formatDate(invite.createdAt)}</p>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card title="Recent activity" description="Changes to accounts and roles">
          {!activity.ok ? (
            <LoadError error={activity.error} what="activity" retryHref="/admin" />
          ) : feed.length === 0 ? (
            <EmptyState icon="history" title="No activity yet">
              Invitations, role changes and account updates will show up here.
            </EmptyState>
          ) : (
            <ActivityFeed entries={feed} lookups={lookups} showInstitution={institutions.length > 1} />
          )}
        </Card>
      </div>
    </div>
  );
}

function Header({ institutions, name }: { institutions: InstitutionSummary[]; name?: string }) {
  return (
    <PageHeader
      title="Dashboard"
      description={
        name
          ? `Accounts, roles and invitations at ${name}.`
          : "Accounts, roles and invitations across the institutions you manage."
      }
      actions={<InviteDialog institutions={institutions} />}
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
      <div className="grid gap-3 sm:gap-4 xl:grid-cols-3">
        {[0, 1, 2].map((key) => (
          <Skeleton key={key} className="h-28 rounded-2xl" />
        ))}
      </div>
      <div className="grid gap-6 xl:grid-cols-2">
        <Card title="People by role">
          <TableSkeleton rows={4} />
        </Card>
        <Card title="New accounts">
          <TableSkeleton rows={4} />
        </Card>
      </div>
    </div>
  );
}
