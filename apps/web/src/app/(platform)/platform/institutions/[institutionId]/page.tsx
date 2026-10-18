import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { type ReactNode, Suspense } from "react";
import { Icon } from "@/components/icon";
import { getAccount } from "@/lib/identity";
import { adminSince, getInstitution, isUuid, listInstitutionAdmins, listPlatformAudit, type PlatformUser } from "@/lib/platform";
import { ActivityFeed } from "@/components/console/activity";
import {
  AdminStatusDialog,
  EditInstitutionDialog,
  InstitutionStatusDialog,
  InviteAdminDialog,
  ResendInvitationDialog,
  RevokeAdminDialog,
} from "../../_components/dialogs";
import {
  Card,
  CodeTile,
  EmptyState,
  formatDate,
  formatTime,
  initials,
  LoadError,
  PageSkeleton,
  StatusBadge,
} from "@/components/console/ui";

export const metadata: Metadata = { title: "Institution" };

export default function InstitutionPage({ params }: PageProps<"/platform/institutions/[institutionId]">) {
  return (
    <div className="space-y-6">
      <Link
        href="/platform/institutions"
        className="inline-flex items-center gap-1.5 rounded-lg text-sm font-semibold text-muted transition-colors hover:text-primary-strong"
      >
        <Icon name="arrow_back" size={18} />
        All institutions
      </Link>
      <Suspense fallback={<PageSkeleton />}>
        <InstitutionDetail params={params} />
      </Suspense>
    </div>
  );
}

async function InstitutionDetail({ params }: { params: Promise<{ institutionId: string }> }) {
  const { institutionId } = await params;
  if (!isUuid(institutionId)) notFound();

  const [account, institutionResult, adminsResult, activityResult] = await Promise.all([
    getAccount(),
    getInstitution(institutionId),
    listInstitutionAdmins(institutionId),
    listPlatformAudit({ institutionId, pageSize: 10 }),
  ]);

  if (!institutionResult.ok) {
    if (institutionResult.error.status === 404) notFound();
    return (
      <Card>
        <LoadError error={institutionResult.error} what="this institution" retryHref={`/platform/institutions/${institutionId}`} />
      </Card>
    );
  }

  const institution = institutionResult.data;
  const admins = adminsResult.ok ? adminsResult.data.items : [];
  const activeAdmins = admins.filter((admin) => admin.accountStatus === "ACTIVE").length;
  const lookups = {
    me: account.status === "ok" ? account.account.id : "",
    institutions: new Map([[institution.id, institution]]),
    people: new Map(admins.map((admin) => [admin.id, admin.fullName])),
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">
        <div className="flex min-w-0 items-start gap-4">
          <CodeTile code={institution.code} size="lg" />
          <div className="min-w-0">
            <h1 className="text-[1.75rem] font-semibold leading-tight tracking-[-0.02em] text-ink font-stretch-semi-expanded sm:text-[2rem]">
              {institution.name}
            </h1>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <span className="rounded-md bg-secondary-soft px-2 py-0.5 font-mono text-xs font-semibold text-secondary-ink">
                {institution.code}
              </span>
              <StatusBadge status={institution.status} />
            </div>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <EditInstitutionDialog institution={institution} />
          <InstitutionStatusDialog institution={institution} />
        </div>
      </div>

      {institution.status === "DISABLED" && (
        <p className="flex gap-3 rounded-2xl border border-danger/15 bg-danger-soft px-4 py-3.5 text-sm leading-5 text-danger">
          <Icon name="info" filled className="mt-px" />
          This institution is disabled. Roles held in it grant no permissions, and you can&apos;t invite admins until you
          re-enable it.
        </p>
      )}

      <dl className="grid grid-cols-2 gap-3 sm:gap-4 xl:grid-cols-4">
        <Fact label="Institution admins" value={adminsResult.ok ? `${activeAdmins} active` : "—"}>
          {adminsResult.ok && admins.length !== activeAdmins ? `${admins.length - activeAdmins} disabled` : "Manage them below"}
        </Fact>
        <Fact label="Registered" value={formatDate(institution.createdAt)}>
          at {formatTime(institution.createdAt)}
        </Fact>
        <Fact label="Last updated" value={formatDate(institution.updatedAt)}>
          at {formatTime(institution.updatedAt)}
        </Fact>
        <Fact label="Institution ID" value={<span className="block break-all font-mono text-[13px] leading-5">{institution.id}</span>} />
      </dl>

      <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <Card
          title="Institution admins"
          description="They manage this institution's users, roles and settings."
          actions={<InviteAdminDialog institution={institution} />}
        >
          {!adminsResult.ok ? (
            <LoadError error={adminsResult.error} what="admins" retryHref={`/platform/institutions/${institution.id}`} />
          ) : admins.length === 0 ? (
            <EmptyState icon="admin_panel_settings" title="No admins yet">
              Invite an Institution Admin so someone can set up this institution.
            </EmptyState>
          ) : (
            <AdminList institutionId={institution.id} admins={admins} />
          )}
        </Card>

        <Card
          title="Activity"
          description="Platform changes for this institution"
          actions={
            <Link
              href={`/platform/audit?institution=${institution.id}`}
              className="inline-flex items-center gap-1 rounded-lg text-sm font-semibold text-primary-strong underline-offset-2 hover:underline"
            >
              Full log
              <Icon name="arrow_forward" size={16} />
            </Link>
          }
        >
          {!activityResult.ok ? (
            <LoadError error={activityResult.error} what="activity" retryHref={`/platform/institutions/${institution.id}`} />
          ) : activityResult.data.items.length === 0 ? (
            <EmptyState icon="history" title="No activity yet" />
          ) : (
            <ActivityFeed entries={activityResult.data.items} lookups={lookups} showInstitution={false} />
          )}
        </Card>
      </div>
    </div>
  );
}

function Fact({ label, value, children }: { label: string; value: ReactNode; children?: ReactNode }) {
  return (
    <div className="min-w-0 rounded-2xl border border-line bg-surface px-4 py-4 sm:px-5">
      <dt className="text-[13px] font-semibold text-muted">{label}</dt>
      <dd className="mt-1 text-base font-semibold text-ink sm:text-lg">{value}</dd>
      {children && <dd className="mt-0.5 text-[13px] text-muted">{children}</dd>}
    </div>
  );
}

function AdminList({ institutionId, admins }: { institutionId: string; admins: PlatformUser[] }) {
  return (
    <ul className="divide-y divide-line">
      {admins.map((admin) => {
        const since = adminSince(admin, institutionId);
        return (
          <li key={admin.id} className="flex flex-col gap-3 px-5 py-4 sm:flex-row sm:items-center">
            <div className="flex min-w-0 flex-1 items-center gap-3">
              <span
                aria-hidden="true"
                className={`grid size-10 shrink-0 place-items-center rounded-full text-[13px] font-semibold ${
                  admin.accountStatus === "ACTIVE" ? "bg-primary-soft text-primary-strong" : "bg-secondary-soft text-secondary-ink"
                }`}
              >
                {initials(admin.fullName || admin.email)}
              </span>
              <div className="min-w-0">
                <p className="truncate text-sm font-semibold text-ink">{admin.fullName}</p>
                <p className="truncate text-[13px] text-muted">{admin.email}</p>
              </div>
            </div>
            <div className="flex items-center justify-between gap-3 pl-[3.25rem] sm:justify-end sm:pl-0">
              <div className="text-left sm:text-right">
                <StatusBadge status={admin.accountStatus} />
                {since && <p className="mt-1 text-xs text-muted">Admin since {formatDate(since)}</p>}
              </div>
              <div className="flex items-center gap-0.5">
                <ResendInvitationDialog institutionId={institutionId} admin={admin} />
                <AdminStatusDialog institutionId={institutionId} admin={admin} />
                <RevokeAdminDialog institutionId={institutionId} admin={admin} />
              </div>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
