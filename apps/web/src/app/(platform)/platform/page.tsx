import type { Metadata } from "next";
import Link from "next/link";
import { connection } from "next/server";
import { Suspense } from "react";
import { Icon } from "@/components/icon";
import { getAccount } from "@/lib/identity";
import { daysAgo, listInstitutions, listPlatformAudit } from "@/lib/platform";
import { ActivityFeed } from "@/components/console/activity";
import { CreateInstitutionDialog } from "./_components/dialogs";
import {
  Card,
  CodeTile,
  EmptyState,
  formatDate,
  Kpi,
  LoadError,
  PageHeader,
  Skeleton,
  StatusBadge,
  TableSkeleton,
} from "@/components/console/ui";

export const metadata: Metadata = { title: "Platform overview" };

export default function PlatformOverviewPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Platform overview"
        description="Institutions registered on Schedence, the admins who run them, and recent changes."
        actions={<CreateInstitutionDialog />}
      />
      <Suspense fallback={<OverviewSkeleton />}>
        <Overview />
      </Suspense>
    </div>
  );
}

async function Overview() {
  await connection();
  const weekAgo = daysAgo(7);
  const [account, all, active, disabled, lastWeek, recent] = await Promise.all([
    getAccount(),
    listInstitutions({ pageSize: 100 }),
    listInstitutions({ status: "ACTIVE", pageSize: 1 }),
    listInstitutions({ status: "DISABLED", pageSize: 1 }),
    listPlatformAudit({ occurredFrom: weekAgo, pageSize: 1 }),
    listPlatformAudit({ pageSize: 8 }),
  ]);

  if (!all.ok) {
    return (
      <Card>
        <LoadError error={all.error} what="the platform overview" retryHref="/platform" />
      </Card>
    );
  }

  const total = all.data.total;
  const activeCount = active.ok ? active.data.total : undefined;
  const disabledCount = disabled.ok ? disabled.data.total : undefined;
  const activeShare = total && activeCount !== undefined ? Math.round((activeCount / total) * 100) : 0;
  const lookups = {
    me: account.status === "ok" ? account.account.id : "",
    institutions: new Map(all.data.items.map((institution) => [institution.id, institution])),
  };

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-3 sm:gap-4 xl:grid-cols-4">
        <Kpi icon="apartment" tone="primary" label="Institutions" value={total} hint="Registered on Schedence" href="/platform/institutions" />
        <Kpi
          icon="check_circle"
          tone="accent"
          label="Active"
          value={activeCount ?? "—"}
          href="/platform/institutions?status=ACTIVE"
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
          icon="domain_disabled"
          tone="danger"
          label="Disabled"
          value={disabledCount ?? "—"}
          hint="Their roles grant no access"
          href="/platform/institutions?status=DISABLED"
        />
        <Kpi
          icon="history"
          tone="neutral"
          label="Changes this week"
          value={lastWeek.ok ? lastWeek.data.total : "—"}
          hint="Platform events in the last 7 days"
          href="/platform/audit"
        />
      </div>

      <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1.45fr)_minmax(0,1fr)]">
        <Card
          title="Institutions"
          description={total > 6 ? `First 6 of ${total}, A–Z` : "A–Z"}
          actions={<ViewAll href="/platform/institutions">All institutions</ViewAll>}
        >
          {all.data.items.length === 0 ? (
            <EmptyState icon="apartment" title="No institutions yet" action={<CreateInstitutionDialog variant="secondary" />}>
              Register the first institution, then invite its Institution Admin.
            </EmptyState>
          ) : (
            <ul className="divide-y divide-line">
              {all.data.items.slice(0, 6).map((institution) => (
                <li key={institution.id}>
                  <Link
                    href={`/platform/institutions/${institution.id}`}
                    className="flex items-center gap-4 px-5 py-3.5 transition-colors hover:bg-background"
                  >
                    <CodeTile code={institution.code} />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-semibold text-ink">{institution.name}</p>
                      <p className="font-mono text-xs text-muted">
                        {institution.code} · since {formatDate(institution.createdAt)}
                      </p>
                    </div>
                    <StatusBadge status={institution.status} />
                    <Icon name="chevron_right" className="hidden text-secondary sm:inline-block" />
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card title="Recent activity" description="Latest platform changes" actions={<ViewAll href="/platform/audit">Audit log</ViewAll>}>
          {!recent.ok ? (
            <LoadError error={recent.error} what="recent activity" retryHref="/platform" />
          ) : recent.data.items.length === 0 ? (
            <EmptyState icon="history" title="No activity yet">
              Changes to institutions and their admins will show up here.
            </EmptyState>
          ) : (
            <ActivityFeed entries={recent.data.items} lookups={lookups} institutionHref={(id) => `/platform/institutions/${id}`} />
          )}
        </Card>
      </div>
    </div>
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
    <div className="space-y-6" aria-busy="true" aria-label="Loading the overview">
      <div className="grid grid-cols-2 gap-3 sm:gap-4 xl:grid-cols-4">
        {[0, 1, 2, 3].map((key) => (
          <Skeleton key={key} className="h-[8.5rem] rounded-2xl" />
        ))}
      </div>
      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.45fr)_minmax(0,1fr)]">
        <Card title="Institutions">
          <TableSkeleton rows={5} />
        </Card>
        <Card title="Recent activity">
          <TableSkeleton rows={5} />
        </Card>
      </div>
    </div>
  );
}
