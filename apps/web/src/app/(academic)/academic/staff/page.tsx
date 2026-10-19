import type { Metadata } from "next";
import Form from "next/form";
import Link from "next/link";
import { Suspense } from "react";
import { Card, EmptyState, first, LoadError, PageHeader, pageNumber, Pagination, query, TableSkeleton } from "@/components/console/ui";
import { Icon } from "@/components/icon";
import { listStaff, type StaffRecord } from "@/lib/academic";
import { ACADEMIC_SERVICE, CodeTag, EmploymentBadge, StaffAvatar } from "../_components/academic-ui";

export const metadata: Metadata = { title: "Staff" };

const PAGE_SIZE = 20;

type SearchParams = PageProps<"/academic/staff">["searchParams"];

export default function StaffPage({ searchParams }: PageProps<"/academic/staff">) {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Staff"
        description="The academic staff register: who can hold administrative positions and be timetabled. Your Institution Admin adds and updates staff."
      />
      <Card>
        <Suspense fallback={<TableSkeleton rows={8} />}>
          <StaffTable searchParams={searchParams} />
        </Suspense>
      </Card>
    </div>
  );
}

async function StaffTable({ searchParams }: { searchParams: SearchParams }) {
  const params = await searchParams;
  const search = first(params.q)?.trim().slice(0, 100) ?? "";
  const page = pageNumber(params.page);

  const result = await listStaff({ search, page, pageSize: PAGE_SIZE });
  const href = (values: { page?: number }) => `/academic/staff${query({ q: search, ...values })}`;

  return (
    <>
      <div className="flex flex-col gap-3 border-b border-line p-4 sm:flex-row sm:items-center sm:justify-between sm:px-5">
        <p className="text-sm text-muted">
          {result.ok ? (
            <>
              <span className="font-semibold text-ink">{result.data.total}</span>{" "}
              {search ? `match${result.data.total === 1 ? "" : "es"} for “${search}”` : result.data.total === 1 ? "person" : "people"}
            </>
          ) : (
            "Staff register"
          )}
        </p>
        <Form action="/academic/staff" className="grid gap-2 sm:w-md sm:grid-cols-[minmax(0,1fr)_auto]">
          <label className="relative block">
            <span className="sr-only">Search staff</span>
            <Icon name="search" size={20} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-secondary" />
            <input
              name="q"
              type="search"
              defaultValue={search}
              placeholder="Search by name, staff number or email"
              className="h-10 w-full rounded-xl border border-line bg-surface pl-10 pr-3 text-sm text-ink placeholder:text-secondary transition-colors hover:border-secondary/70 focus:border-primary"
            />
          </label>
          <button
            type="submit"
            className="inline-flex h-10 items-center justify-center gap-2 rounded-xl bg-primary px-4 text-sm font-semibold text-white transition-colors hover:bg-primary-strong"
          >
            Search
          </button>
        </Form>
      </div>

      {!result.ok ? (
        <LoadError error={result.error} what="staff" retryHref={href({})} service={ACADEMIC_SERVICE} />
      ) : result.data.items.length === 0 ? (
        search ? (
          <EmptyState
            icon="filter_alt_off"
            title="Nobody matches"
            action={
              <Link href="/academic/staff" className="text-sm font-semibold text-primary-strong hover:underline">
                Clear search
              </Link>
            }
          >
            Try another name, staff number or email.
          </EmptyState>
        ) : (
          <EmptyState icon="badge" title="No staff yet">
            Staff appear here once your Institution Admin adds them to the register.
          </EmptyState>
        )
      ) : (
        <>
          {/* Phones: one card per person. */}
          <ul className="divide-y divide-line md:hidden">
            {result.data.items.map((member) => (
              <li key={member.id}>
                <Link href={`/academic/staff/${member.id}`} className="flex gap-3 px-4 py-4 transition-colors hover:bg-background/60">
                  <StaffAvatar staff={member} />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-semibold text-ink">{member.fullName}</p>
                    <p className="truncate text-[13px] text-muted">{member.academicRank ?? "Rank not recorded"}</p>
                    <div className="mt-2 flex flex-wrap items-center gap-1.5">
                      <CodeTag>{member.staffNumber}</CodeTag>
                      <EmploymentBadge status={member.employmentStatus} />
                      {!member.identityUserId && <NoLogin />}
                    </div>
                  </div>
                  <Icon name="chevron_right" className="self-center text-secondary" />
                </Link>
              </li>
            ))}
          </ul>

          {/* Tablets and up: a table. */}
          <table className="hidden w-full text-left text-sm md:table">
            <thead className="bg-background/70 text-xs font-semibold uppercase tracking-wider text-muted">
              <tr>
                <th scope="col" className="px-5 py-3">
                  Staff member
                </th>
                <th scope="col" className="px-5 py-3">
                  Staff number
                </th>
                <th scope="col" className="hidden px-5 py-3 lg:table-cell">
                  Academic rank
                </th>
                <th scope="col" className="px-5 py-3">
                  Status
                </th>
                <th scope="col" className="w-12 px-5 py-3">
                  <span className="sr-only">Open</span>
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {result.data.items.map((member) => (
                <StaffRow key={member.id} member={member} />
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

function StaffRow({ member }: { member: StaffRecord }) {
  const detail = `/academic/staff/${member.id}`;
  return (
    <tr className="relative transition-colors hover:bg-background/60">
      <td className="px-5 py-3.5">
        <div className="flex items-center gap-3">
          <StaffAvatar staff={member} />
          <div className="min-w-0">
            {/* The name's link covers the whole row, so the row opens the staff member. */}
            <Link href={detail} className="truncate font-semibold text-ink after:absolute after:inset-0 hover:text-primary-strong">
              {member.fullName}
            </Link>
            <p className="truncate text-[13px] text-muted">{member.email ?? "No email recorded"}</p>
          </div>
        </div>
      </td>
      <td className="px-5 py-3.5">
        <CodeTag>{member.staffNumber}</CodeTag>
      </td>
      <td className="hidden px-5 py-3.5 text-ink lg:table-cell">{member.academicRank ?? <span className="text-muted">Not recorded</span>}</td>
      <td className="px-5 py-3.5">
        <div className="flex flex-wrap gap-1.5">
          <EmploymentBadge status={member.employmentStatus} />
          {!member.identityUserId && <NoLogin />}
        </div>
      </td>
      <td className="px-5 py-3.5 text-right">
        <Icon name="chevron_right" className="text-secondary" />
      </td>
    </tr>
  );
}

function NoLogin() {
  return (
    <span className="inline-flex items-center gap-1 whitespace-nowrap rounded-full bg-background px-2.5 py-1 text-xs font-semibold text-muted">
      <Icon name="link_off" size={14} />
      No login
    </span>
  );
}
