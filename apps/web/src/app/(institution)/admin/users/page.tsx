import type { Metadata } from "next";
import Form from "next/form";
import Link from "next/link";
import { redirect } from "next/navigation";
import { Suspense } from "react";
import {
  Card,
  EmptyState,
  first,
  formatDate,
  initials,
  LoadError,
  PageHeader,
  pageNumber,
  Pagination,
  query,
  StatusBadge,
  TableSkeleton,
} from "@/components/console/ui";
import { Icon } from "@/components/icon";
import type { UserRecord } from "@/lib/identity-records";
import { getAdminContext, listUsers } from "@/lib/institution";
import { INSTITUTION_ROLES, roleInfo } from "@/lib/roles";
import { InviteDialog, ResendInvitationDialog } from "../_components/dialogs";

export const metadata: Metadata = { title: "People" };

const PAGE_SIZE = 20;
const STATUSES = [
  { label: "All", value: undefined },
  { label: "Active", value: "ACTIVE" },
  { label: "Disabled", value: "DISABLED" },
] as const;

type SearchParams = PageProps<"/admin/users">["searchParams"];

export default function PeoplePage({ searchParams }: PageProps<"/admin/users">) {
  return (
    <div className="space-y-6">
      <Suspense fallback={<PageHeader title="People" description="Everyone with an account in your institution." />}>
        <Header />
      </Suspense>
      <Card>
        <Suspense fallback={<TableSkeleton rows={8} />}>
          <PeopleTable searchParams={searchParams} />
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
      title="People"
      description={`Everyone with an account at ${context.institutions.length === 1 ? context.primary.name : "your institutions"}. Invite academic admins, examination admins, venue managers and lecturers here.`}
      actions={<InviteDialog institutions={context.institutions} />}
    />
  );
}

async function PeopleTable({ searchParams }: { searchParams: SearchParams }) {
  const context = await getAdminContext();
  if (!context) redirect("/");
  const params = await searchParams;
  const search = first(params.q)?.trim().slice(0, 100) ?? "";
  const statusParam = first(params.status);
  const status = statusParam === "ACTIVE" || statusParam === "DISABLED" ? statusParam : undefined;
  const roleParam = first(params.role);
  const role = INSTITUTION_ROLES.some((candidate) => candidate.code === roleParam) ? roleParam : undefined;
  const page = pageNumber(params.page);

  const result = await listUsers({ search, status, roleCode: role, page, pageSize: PAGE_SIZE });
  const href = (values: { status?: string; page?: number }) => `/admin/users${query({ q: search, role, status, ...values })}`;
  const multi = context.institutions.length > 1;
  const institutionCode = (id: string) => context.institutions.find((institution) => institution.id === id)?.code;

  return (
    <>
      <div className="flex flex-col gap-3 border-b border-line p-4 sm:px-5 lg:flex-row lg:items-center lg:justify-between">
        <nav aria-label="Filter by status" className="inline-flex self-start rounded-xl bg-background p-1">
          {STATUSES.map((option) => {
            const selected = option.value === status;
            return (
              <Link
                key={option.label}
                aria-current={selected ? "page" : undefined}
                href={href({ status: option.value ?? "", page: 1 })}
                className={`rounded-lg px-3.5 py-1.5 text-sm font-semibold transition-colors ${
                  selected ? "bg-surface text-ink shadow-[0_1px_3px_rgb(29_33_66/0.12)]" : "text-muted hover:text-ink"
                }`}
              >
                {option.label}
              </Link>
            );
          })}
        </nav>

        <Form action="/admin/users" className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_12rem_auto] lg:w-[36rem]">
          {status && <input type="hidden" name="status" value={status} />}
          <label className="relative block">
            <span className="sr-only">Search people</span>
            <Icon name="search" size={20} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-secondary" />
            <input
              name="q"
              type="search"
              defaultValue={search}
              placeholder="Search by name or email"
              className="h-10 w-full rounded-xl border border-line bg-surface pl-10 pr-3 text-sm text-ink placeholder:text-secondary transition-colors hover:border-secondary/70 focus:border-primary"
            />
          </label>
          <label className="relative block">
            <span className="sr-only">Role</span>
            <select
              name="role"
              defaultValue={role ?? ""}
              className="h-10 w-full appearance-none rounded-xl border border-line bg-surface pl-3 pr-9 text-sm text-ink transition-colors hover:border-secondary/70 focus:border-primary"
            >
              <option value="">All roles</option>
              {INSTITUTION_ROLES.map((option) => (
                <option key={option.code} value={option.code}>
                  {option.name}
                </option>
              ))}
            </select>
            <Icon
              name="chevron_right"
              size={18}
              className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 rotate-90 text-secondary"
            />
          </label>
          <button
            type="submit"
            className="inline-flex h-10 items-center justify-center gap-2 rounded-xl bg-primary px-4 text-sm font-semibold text-white transition-colors hover:bg-primary-strong"
          >
            Apply
          </button>
        </Form>
      </div>

      {!result.ok ? (
        <LoadError error={result.error} what="people" retryHref={href({})} />
      ) : result.data.items.length === 0 ? (
        search || status || role ? (
          <EmptyState
            icon="filter_alt_off"
            title="Nobody matches"
            action={
              <Link href="/admin/users" className="text-sm font-semibold text-primary-strong hover:underline">
                Clear filters
              </Link>
            }
          >
            Try another name, email, role or status.
          </EmptyState>
        ) : (
          <EmptyState icon="group" title="No one here yet" action={<InviteDialog institutions={context.institutions} variant="secondary" />}>
            Invite your academic admins, examination admins, venue managers and lecturers.
          </EmptyState>
        )
      ) : (
        <>
          {/* Phones: one card per person. */}
          <ul className="divide-y divide-line md:hidden">
            {result.data.items.map((user) => (
              <li key={user.id} className="flex gap-3 px-4 py-4">
                <Avatar user={user} />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-semibold text-ink">{user.fullName}</p>
                  <p className="truncate text-[13px] text-muted">{user.email}</p>
                  <div className="mt-2 flex flex-wrap items-center gap-1.5">
                    <StatusBadge status={user.accountStatus} />
                    <RoleChips user={user} institutionCode={multi ? institutionCode : undefined} />
                  </div>
                </div>
                <ResendInvitationDialog user={user} />
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
                  Roles
                </th>
                <th scope="col" className="px-5 py-3">
                  Status
                </th>
                <th scope="col" className="hidden px-5 py-3 lg:table-cell">
                  Added
                </th>
                <th scope="col" className="w-14 px-5 py-3">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {result.data.items.map((user) => (
                <tr key={user.id} className="transition-colors hover:bg-background/60">
                  <td className="px-5 py-3.5">
                    <div className="flex items-center gap-3">
                      <Avatar user={user} />
                      <div className="min-w-0">
                        <p className="truncate font-semibold text-ink">{user.fullName}</p>
                        <p className="truncate text-[13px] text-muted">{user.email}</p>
                      </div>
                    </div>
                  </td>
                  <td className="px-5 py-3.5">
                    <div className="flex flex-wrap gap-1.5">
                      <RoleChips user={user} institutionCode={multi ? institutionCode : undefined} />
                    </div>
                  </td>
                  <td className="px-5 py-3.5">
                    <StatusBadge status={user.accountStatus} />
                  </td>
                  <td className="hidden px-5 py-3.5 text-muted lg:table-cell">{formatDate(user.createdAt)}</td>
                  <td className="px-5 py-3.5 text-right">
                    <ResendInvitationDialog user={user} />
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

function Avatar({ user }: { user: UserRecord }) {
  return (
    <span
      aria-hidden="true"
      className={`grid size-10 shrink-0 place-items-center rounded-full text-[13px] font-semibold ${
        user.accountStatus === "ACTIVE" ? "bg-primary-soft text-primary-strong" : "bg-secondary-soft text-secondary-ink"
      }`}
    >
      {initials(user.fullName || user.email)}
    </span>
  );
}

function RoleChips({ user, institutionCode }: { user: UserRecord; institutionCode?: (id: string) => string | undefined }) {
  if (!user.roles.length) return <span className="text-[13px] text-muted">No roles</span>;
  return user.roles.map((role) => (
    <span
      key={`${role.institutionId}-${role.roleCode}`}
      className="inline-flex items-center gap-1 rounded-md bg-primary-soft px-2 py-0.5 text-xs font-semibold text-primary-strong"
    >
      <Icon name={roleInfo(role.roleCode).icon} size={14} filled />
      {role.roleName}
      {institutionCode && <span className="font-mono font-medium opacity-75">· {institutionCode(role.institutionId)}</span>}
    </span>
  ));
}
