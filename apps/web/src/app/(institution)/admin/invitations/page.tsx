import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";
import { Suspense } from "react";
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
} from "@/components/console/ui";
import { describeFailure } from "@/lib/audit-events";
import type { Provisioning, ProvisioningStatus } from "@/lib/identity-records";
import { getAdminContext, listProvisioning, OPEN_PROVISIONING } from "@/lib/institution";
import { CancelInvitationDialog, InviteDialog, RetryInvitationDialog } from "../_components/dialogs";
import { canCancel, canRetry, InvitationStatus } from "../_components/invitation-status";

export const metadata: Metadata = { title: "Invitations" };

const PAGE_SIZE = 20;
const VIEWS: { key: string; label: string; statuses: ProvisioningStatus[] }[] = [
  { key: "open", label: "Needs attention", statuses: OPEN_PROVISIONING },
  { key: "sent", label: "Sent", statuses: ["COMPLETED"] },
  { key: "cancelled", label: "Cancelled", statuses: ["CANCELLED"] },
  { key: "all", label: "All", statuses: [] },
];

type SearchParams = PageProps<"/admin/invitations">["searchParams"];

export default function InvitationsPage({ searchParams }: PageProps<"/admin/invitations">) {
  return (
    <div className="space-y-6">
      <Suspense fallback={<PageHeader title="Invitations" />}>
        <Header />
      </Suspense>
      <Card>
        <Suspense fallback={<TableSkeleton rows={8} />}>
          <InvitationTable searchParams={searchParams} />
        </Suspense>
      </Card>
    </div>
  );
}

async function Header() {
  const context = await getAdminContext();
  if (!context) redirect("/");
  return (
    <PageHeader
      title="Invitations"
      description="Every invitation sent from Schedence and how it went. If Cognito couldn't send one, retry it here."
      actions={<InviteDialog institutions={context.institutions} />}
    />
  );
}

async function InvitationTable({ searchParams }: { searchParams: SearchParams }) {
  const context = await getAdminContext();
  if (!context) redirect("/");
  const params = await searchParams;
  const view = VIEWS.find((candidate) => candidate.key === first(params.view)) ?? VIEWS[0];
  const page = pageNumber(params.page);
  const result = await listProvisioning({ statuses: view.statuses, page, pageSize: PAGE_SIZE });
  const href = (values: { view?: string; page?: number }) =>
    `/admin/invitations${query({ view: view.key === "open" ? undefined : view.key, ...values })}`;

  return (
    <>
      <div className="border-b border-line p-4 sm:px-5">
        <nav aria-label="Filter invitations" className="inline-flex max-w-full overflow-x-auto rounded-xl bg-background p-1">
          {VIEWS.map((option) => {
            const selected = option.key === view.key;
            return (
              <Link
                key={option.key}
                aria-current={selected ? "page" : undefined}
                href={`/admin/invitations${query({ view: option.key === "open" ? undefined : option.key })}`}
                className={`whitespace-nowrap rounded-lg px-3.5 py-1.5 text-sm font-semibold transition-colors ${
                  selected ? "bg-surface text-ink shadow-[0_1px_3px_rgb(29_33_66/0.12)]" : "text-muted hover:text-ink"
                }`}
              >
                {option.label}
              </Link>
            );
          })}
        </nav>
      </div>

      {!result.ok ? (
        <LoadError error={result.error} what="invitations" retryHref={href({ page })} />
      ) : result.data.items.length === 0 ? (
        view.key === "open" ? (
          <EmptyState icon="check_circle" title="Nothing needs attention">
            Every invitation went through. Failed ones would show up here for you to retry.
          </EmptyState>
        ) : (
          <EmptyState icon="mail" title="No invitations here" action={<InviteDialog institutions={context.institutions} variant="secondary" />}>
            Invitations you send will be listed here.
          </EmptyState>
        )
      ) : (
        <>
          {/* Phones: one card per invitation. */}
          <ul className="divide-y divide-line md:hidden">
            {result.data.items.map((record) => (
              <li key={record.id} className="flex gap-3 px-4 py-4">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-semibold text-ink">{record.fullName}</p>
                  <p className="truncate text-[13px] text-muted">{record.email}</p>
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    <InvitationStatus status={record.status} />
                    <span className="text-xs text-muted">{formatDate(record.createdAt)}</span>
                  </div>
                  <Outcome record={record} />
                </div>
                <Actions record={record} />
              </li>
            ))}
          </ul>

          {/* Tablets and up: a table. */}
          <table className="hidden w-full text-left text-sm md:table">
            <thead className="bg-background/70 text-xs font-semibold uppercase tracking-wider text-muted">
              <tr>
                <th scope="col" className="px-5 py-3">
                  Person
                </th>
                <th scope="col" className="px-5 py-3">
                  Status
                </th>
                <th scope="col" className="px-5 py-3">
                  Details
                </th>
                <th scope="col" className="hidden px-5 py-3 lg:table-cell">
                  Requested
                </th>
                <th scope="col" className="w-24 px-5 py-3">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {result.data.items.map((record) => (
                <tr key={record.id} className="align-top transition-colors hover:bg-background/60">
                  <td className="px-5 py-3.5">
                    <p className="font-semibold text-ink">{record.fullName}</p>
                    <p className="text-[13px] text-muted">{record.email}</p>
                  </td>
                  <td className="px-5 py-3.5">
                    <InvitationStatus status={record.status} />
                  </td>
                  <td className="max-w-xs px-5 py-3.5">
                    <Outcome record={record} />
                  </td>
                  <td className="hidden whitespace-nowrap px-5 py-3.5 lg:table-cell">
                    <span className="block text-ink">{formatDate(record.createdAt)}</span>
                    <span className="font-mono text-xs text-muted">{formatTime(record.createdAt)}</span>
                  </td>
                  <td className="px-5 py-3.5">
                    <div className="flex justify-end">
                      <Actions record={record} />
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          <Pagination
            page={result.data.page}
            pageSize={result.data.pageSize}
            total={result.data.total}
            hrefFor={(target) => href({ page: target })}
          />
        </>
      )}
    </>
  );
}

/** What happened, in a sentence: when it was sent, why it failed, or that it's still under way. */
function Outcome({ record }: { record: Provisioning }) {
  const attempts = record.attemptCount > 1 ? ` after ${record.attemptCount} attempts` : "";
  const text =
    record.status === "COMPLETED"
      ? `Account created ${record.completedAt ? formatDate(record.completedAt) : ""}${attempts}.`
      : record.status === "FAILED"
        ? `${record.errorCategory ? describeFailure(record.errorCategory) : "Cognito couldn't create the sign-in."} Tried ${record.attemptCount} ${record.attemptCount === 1 ? "time" : "times"}.`
        : record.status === "CANCELLED"
          ? "Cancelled before the account was created."
          : "Waiting on Cognito. If it stays here, retry it.";
  return <p className={`mt-1 text-[13px] leading-5 md:mt-0 ${record.status === "FAILED" ? "text-danger" : "text-muted"}`}>{text}</p>;
}

function Actions({ record }: { record: Provisioning }) {
  if (!canRetry(record.status) && !canCancel(record.status)) return null;
  return (
    <div className="flex shrink-0 items-start gap-0.5">
      {canRetry(record.status) && <RetryInvitationDialog record={record} />}
      {canCancel(record.status) && <CancelInvitationDialog record={record} />}
    </div>
  );
}
