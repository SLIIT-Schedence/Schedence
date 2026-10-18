import type { Metadata } from "next";
import Form from "next/form";
import Link from "next/link";
import { type ReactNode, Suspense } from "react";
import { Icon } from "@/components/icon";
import { describeEvent, PLATFORM_EVENTS } from "@/lib/audit-events";
import { getAccount } from "@/lib/identity";
import { isUuid, listInstitutions, listPlatformAudit } from "@/lib/platform";
import { ActivityFeed, actorLabel, type AuditLookups } from "@/components/console/activity";
import {
  Card,
  EmptyState,
  first,
  formatDate,
  formatTime,
  LoadError,
  PageHeader,
  pageNumber,
  Pagination,
  query,
  TableSkeleton,
  ToneIcon,
} from "@/components/console/ui";

export const metadata: Metadata = { title: "Audit log" };

const PAGE_SIZE = 25;
const DAY = /^\d{4}-\d{2}-\d{2}$/;
const COLOMBO = "+05:30";

type SearchParams = PageProps<"/platform/audit">["searchParams"];

export default function AuditPage({ searchParams }: PageProps<"/platform/audit">) {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Audit log"
        description="Every platform change, newest first: institutions registered, renamed, disabled, and their admins invited or removed."
      />
      <Card>
        <Suspense fallback={<TableSkeleton rows={10} />}>
          <AuditTable searchParams={searchParams} />
        </Suspense>
      </Card>
    </div>
  );
}

/** A Colombo calendar day as an ISO instant: its start, or the start of the next day for an exclusive end. */
function dayStart(day: string, next = false): string {
  const start = new Date(`${day}T00:00:00${COLOMBO}`);
  return new Date(start.getTime() + (next ? 24 * 60 * 60 * 1000 : 0)).toISOString();
}

async function AuditTable({ searchParams }: { searchParams: SearchParams }) {
  const params = await searchParams;
  const action = first(params.action);
  const institutionId = first(params.institution);
  const from = first(params.from);
  const to = first(params.to);
  const filters = {
    action: action && action in PLATFORM_EVENTS ? action : undefined,
    institution: isUuid(institutionId) ? institutionId : undefined,
    from: from && DAY.test(from) ? from : undefined,
    to: to && DAY.test(to) ? to : undefined,
  };
  const page = pageNumber(params.page);

  const [account, institutions, result] = await Promise.all([
    getAccount(),
    listInstitutions({ pageSize: 100 }),
    listPlatformAudit({
      action: filters.action,
      institutionId: filters.institution,
      occurredFrom: filters.from ? dayStart(filters.from) : undefined,
      occurredTo: filters.to ? dayStart(filters.to, true) : undefined,
      page,
      pageSize: PAGE_SIZE,
    }),
  ]);

  const lookups: AuditLookups = {
    me: account.status === "ok" ? account.account.id : "",
    institutions: new Map((institutions.ok ? institutions.data.items : []).map((item) => [item.id, item])),
  };
  const filtered = Object.values(filters).some(Boolean);
  const href = (target: number) => `/platform/audit${query({ ...filters, page: target })}`;

  return (
    <>
      <Form action="/platform/audit" className="grid gap-3 border-b border-line p-4 sm:grid-cols-2 sm:px-5 xl:grid-cols-[1.3fr_1.3fr_1fr_1fr_auto]">
        <Select name="action" label="Event" defaultValue={filters.action}>
          <option value="">All events</option>
          {Object.entries(PLATFORM_EVENTS).map(([value, event]) => (
            <option key={value} value={value}>
              {event.label}
            </option>
          ))}
        </Select>
        <Select name="institution" label="Institution" defaultValue={filters.institution}>
          <option value="">All institutions</option>
          {[...lookups.institutions.values()].map((institution) => (
            <option key={institution.id} value={institution.id}>
              {institution.name} ({institution.code})
            </option>
          ))}
        </Select>
        <DateField name="from" label="From" defaultValue={filters.from} />
        <DateField name="to" label="To" defaultValue={filters.to} />
        <div className="flex items-end gap-2 sm:col-span-2 xl:col-span-1">
          <button
            type="submit"
            className="inline-flex h-10 flex-1 items-center justify-center gap-2 rounded-xl bg-primary px-4 text-sm font-semibold text-white transition-colors hover:bg-primary-strong xl:flex-none"
          >
            <Icon name="search" size={18} />
            Apply
          </button>
          {filtered && (
            <Link
              href="/platform/audit"
              className="inline-flex h-10 items-center rounded-xl border border-line bg-surface px-4 text-sm font-semibold text-ink transition-colors hover:bg-background"
            >
              Clear
            </Link>
          )}
        </div>
      </Form>

      {!result.ok ? (
        <LoadError error={result.error} what="the audit log" retryHref={href(page)} />
      ) : result.data.items.length === 0 ? (
        <EmptyState icon={filtered ? "filter_alt_off" : "history"} title={filtered ? "No events match these filters" : "No events yet"}>
          {filtered ? "Try a wider date range or another event." : "Platform changes will be recorded here."}
        </EmptyState>
      ) : (
        <>
          {/* Phones and tablets: a feed. */}
          <div className="lg:hidden">
            <ActivityFeed entries={result.data.items} lookups={lookups} institutionHref={(id) => `/platform/institutions/${id}`} />
          </div>

          {/* Desktop: a table. */}
          <table className="hidden w-full text-left text-sm lg:table">
            <thead className="bg-background/70 text-xs font-semibold uppercase tracking-wider text-muted">
              <tr>
                <th scope="col" className="w-36 px-5 py-3">
                  When
                </th>
                <th scope="col" className="px-5 py-3">
                  Event
                </th>
                <th scope="col" className="px-5 py-3">
                  Institution
                </th>
                <th scope="col" className="px-5 py-3">
                  By
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {result.data.items.map((entry) => {
                const event = describeEvent(entry);
                const institution = entry.institutionId ? lookups.institutions.get(entry.institutionId) : undefined;
                return (
                  <tr key={entry.id} className="align-top">
                    <td className="px-5 py-3.5 whitespace-nowrap">
                      <time dateTime={entry.occurredAt}>
                        <span className="block font-medium text-ink">{formatDate(entry.occurredAt)}</span>
                        <span className="font-mono text-xs text-muted">{formatTime(entry.occurredAt)}</span>
                      </time>
                    </td>
                    <td className="px-5 py-3.5">
                      <div className="flex gap-3">
                        <ToneIcon icon={event.icon} tone={event.tone} size="sm" />
                        <div className="min-w-0">
                          <p className="font-semibold leading-5 text-ink">{event.label}</p>
                          {event.detail && <p className="mt-0.5 break-words text-[13px] leading-5 text-muted">{event.detail}</p>}
                          {entry.targetUserId && (
                            <p className="mt-0.5 font-mono text-[11px] text-secondary-ink" title={entry.targetUserId}>
                              user {entry.targetUserId.slice(0, 8)}
                            </p>
                          )}
                        </div>
                      </div>
                    </td>
                    <td className="px-5 py-3.5">
                      {institution ? (
                        <Link href={`/platform/institutions/${institution.id}`} className="font-medium text-primary-strong hover:underline">
                          {institution.name}
                        </Link>
                      ) : entry.institutionId ? (
                        <span className="font-mono text-xs text-muted">{entry.institutionId.slice(0, 8)}</span>
                      ) : (
                        <span className="text-muted">—</span>
                      )}
                    </td>
                    <td className="px-5 py-3.5 text-muted">{actorLabel(entry, lookups.me)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>

          <Pagination page={result.data.page} pageSize={result.data.pageSize} total={result.data.total} hrefFor={href} />
        </>
      )}
    </>
  );
}

const CONTROL =
  "h-10 w-full rounded-xl border border-line bg-surface px-3 text-sm text-ink transition-colors hover:border-secondary/70 focus:border-primary";

function Select({
  name,
  label,
  defaultValue,
  children,
}: {
  name: string;
  label: string;
  defaultValue?: string;
  children: ReactNode;
}) {
  return (
    <label className="block min-w-0">
      <span className="mb-1.5 block text-xs font-semibold uppercase tracking-wider text-muted">{label}</span>
      <span className="relative block">
        <select name={name} defaultValue={defaultValue ?? ""} className={`${CONTROL} appearance-none truncate pr-9`}>
          {children}
        </select>
        <Icon name="chevron_right" size={18} className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 rotate-90 text-secondary" />
      </span>
    </label>
  );
}

function DateField({ name, label, defaultValue }: { name: string; label: string; defaultValue?: string }) {
  return (
    <label className="block min-w-0">
      <span className="mb-1.5 block text-xs font-semibold uppercase tracking-wider text-muted">{label}</span>
      <input type="date" name={name} defaultValue={defaultValue} className={CONTROL} />
    </label>
  );
}
