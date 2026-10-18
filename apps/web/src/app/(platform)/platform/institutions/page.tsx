import type { Metadata } from "next";
import Form from "next/form";
import Link from "next/link";
import { Suspense } from "react";
import { Icon } from "@/components/icon";
import { type InstitutionStatus, listInstitutions } from "@/lib/platform";
import { CreateInstitutionDialog } from "../_components/dialogs";
import {
  Card,
  CodeTile,
  EmptyState,
  first,
  formatDate,
  LoadError,
  PageHeader,
  pageNumber,
  Pagination,
  query,
  StatusBadge,
  TableSkeleton,
} from "@/components/console/ui";

export const metadata: Metadata = { title: "Institutions" };

const PAGE_SIZE = 20;
const FILTERS: { label: string; value?: InstitutionStatus }[] = [
  { label: "All" },
  { label: "Active", value: "ACTIVE" },
  { label: "Disabled", value: "DISABLED" },
];

type SearchParams = PageProps<"/platform/institutions">["searchParams"];

export default function InstitutionsPage({ searchParams }: PageProps<"/platform/institutions">) {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Institutions"
        description="Every institution on Schedence. Open one to manage its details and Institution Admins."
        actions={<CreateInstitutionDialog />}
      />
      <Card>
        <Suspense fallback={<TableSkeleton rows={8} />}>
          <InstitutionTable searchParams={searchParams} />
        </Suspense>
      </Card>
    </div>
  );
}

async function InstitutionTable({ searchParams }: { searchParams: SearchParams }) {
  const params = await searchParams;
  const search = first(params.q)?.trim().slice(0, 100) ?? "";
  const statusParam = first(params.status);
  const status = statusParam === "ACTIVE" || statusParam === "DISABLED" ? statusParam : undefined;
  const page = pageNumber(params.page);
  const result = await listInstitutions({ search, status, page, pageSize: PAGE_SIZE });
  const href = (values: { q?: string; status?: string; page?: number }) =>
    `/platform/institutions${query({ q: search, status, ...values })}`;

  return (
    <>
      <div className="flex flex-col gap-3 border-b border-line p-4 sm:flex-row sm:items-center sm:justify-between sm:px-5">
        <nav aria-label="Filter by status" className="inline-flex rounded-xl bg-background p-1">
          {FILTERS.map((filter) => {
            const selected = filter.value === status;
            return (
              <Link
                key={filter.label}
                aria-current={selected ? "page" : undefined}
                href={href({ status: filter.value ?? "", page: 1 })}
                className={`rounded-lg px-3.5 py-1.5 text-sm font-semibold transition-colors ${
                  selected ? "bg-surface text-ink shadow-[0_1px_3px_rgb(29_33_66/0.12)]" : "text-muted hover:text-ink"
                }`}
              >
                {filter.label}
              </Link>
            );
          })}
        </nav>

        <Form action="/platform/institutions" className="relative w-full sm:w-72">
          {status && <input type="hidden" name="status" value={status} />}
          <label htmlFor="institution-search" className="sr-only">
            Search institutions
          </label>
          <Icon name="search" size={20} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-secondary" />
          <input
            id="institution-search"
            name="q"
            type="search"
            defaultValue={search}
            placeholder="Search by name or code"
            className="h-10 w-full rounded-xl border border-line bg-surface pl-10 pr-3 text-sm text-ink placeholder:text-secondary transition-colors hover:border-secondary/70 focus:border-primary"
          />
        </Form>
      </div>

      {!result.ok ? (
        <LoadError error={result.error} what="institutions" retryHref={href({})} />
      ) : result.data.items.length === 0 ? (
        search || status ? (
          <EmptyState
            icon="filter_alt_off"
            title="No institutions match"
            action={
              <Link href="/platform/institutions" className="text-sm font-semibold text-primary-strong hover:underline">
                Clear filters
              </Link>
            }
          >
            Try a different name or code, or another status.
          </EmptyState>
        ) : (
          <EmptyState icon="apartment" title="No institutions yet" action={<CreateInstitutionDialog variant="secondary" />}>
            Register the first institution, then invite its Institution Admin.
          </EmptyState>
        )
      ) : (
        <>
          {/* Phones: one card per institution. */}
          <ul className="divide-y divide-line md:hidden">
            {result.data.items.map((institution) => (
              <li key={institution.id}>
                <Link href={`/platform/institutions/${institution.id}`} className="flex items-center gap-3 px-4 py-3.5 hover:bg-background">
                  <CodeTile code={institution.code} />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-semibold text-ink">{institution.name}</p>
                    <p className="mt-0.5 font-mono text-xs text-muted">{institution.code}</p>
                    <div className="mt-2">
                      <StatusBadge status={institution.status} />
                    </div>
                  </div>
                  <Icon name="chevron_right" className="text-secondary" />
                </Link>
              </li>
            ))}
          </ul>

          {/* Tablets and up: a table. */}
          <table className="hidden w-full text-left text-sm md:table">
            <thead className="bg-background/70 text-xs font-semibold uppercase tracking-wider text-muted">
              <tr>
                <th scope="col" className="px-5 py-3">
                  Institution
                </th>
                <th scope="col" className="px-5 py-3">
                  Status
                </th>
                <th scope="col" className="hidden px-5 py-3 lg:table-cell">
                  Registered
                </th>
                <th scope="col" className="px-5 py-3">
                  Last updated
                </th>
                <th scope="col" className="w-12 px-5 py-3">
                  <span className="sr-only">Open</span>
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {result.data.items.map((institution) => (
                <tr key={institution.id} className="group relative transition-colors hover:bg-background">
                  <td className="px-5 py-3.5">
                    <div className="flex items-center gap-3.5">
                      <CodeTile code={institution.code} />
                      <div className="min-w-0">
                        <Link
                          href={`/platform/institutions/${institution.id}`}
                          className="font-semibold text-ink after:absolute after:inset-0 group-hover:text-primary-strong"
                        >
                          {institution.name}
                        </Link>
                        <p className="font-mono text-xs text-muted">{institution.code}</p>
                      </div>
                    </div>
                  </td>
                  <td className="px-5 py-3.5">
                    <StatusBadge status={institution.status} />
                  </td>
                  <td className="hidden px-5 py-3.5 text-muted lg:table-cell">{formatDate(institution.createdAt)}</td>
                  <td className="px-5 py-3.5 text-muted">{formatDate(institution.updatedAt)}</td>
                  <td className="px-5 py-3.5 text-right">
                    <Icon name="chevron_right" className="text-secondary transition-transform group-hover:translate-x-0.5" />
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
