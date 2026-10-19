import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";
import { Card, EmptyState, first, LoadError, PageHeader, pageNumber, Pagination, query, TableSkeleton } from "@/components/console/ui";
import { Icon } from "@/components/icon";
import { listPositions } from "@/lib/academic";
import { ACADEMIC_SERVICE, CodeTag, combinationRule, hours, PositionStatus, UNIT_TYPES } from "../_components/academic-ui";

export const metadata: Metadata = { title: "Positions" };

const PAGE_SIZE = 20;

type SearchParams = PageProps<"/academic/positions">["searchParams"];

export default function PositionsPage({ searchParams }: PageProps<"/academic/positions">) {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Administrative positions"
        description="Roles such as Head of Department or Program Coordinator, and the weekly hours each one takes away from teaching. Your Institution Admin sets them."
      />
      <Card>
        <Suspense fallback={<TableSkeleton rows={6} />}>
          <PositionsTable searchParams={searchParams} />
        </Suspense>
      </Card>
    </div>
  );
}

async function PositionsTable({ searchParams }: { searchParams: SearchParams }) {
  const params = await searchParams;
  const all = first(params.show) === "all";
  const page = pageNumber(params.page);

  const result = await listPositions({ includeInactive: all, page, pageSize: PAGE_SIZE });
  const href = (values: { show?: string; page?: number }) => `/academic/positions${query({ show: all ? "all" : undefined, ...values })}`;

  return (
    <>
      <div className="flex items-center justify-between gap-3 border-b border-line p-4 sm:px-5">
        <nav aria-label="Positions to show" className="inline-flex rounded-xl bg-background p-1">
          {[
            { label: "Active", show: "", selected: !all },
            { label: "All", show: "all", selected: all },
          ].map((option) => (
            <Link
              key={option.label}
              aria-current={option.selected ? "page" : undefined}
              href={href({ show: option.show, page: 1 })}
              className={`rounded-lg px-3.5 py-1.5 text-sm font-semibold transition-colors ${
                option.selected ? "bg-surface text-ink shadow-[0_1px_3px_rgb(29_33_66/0.12)]" : "text-muted hover:text-ink"
              }`}
            >
              {option.label}
            </Link>
          ))}
        </nav>
        {result.ok && (
          <p className="text-sm text-muted">
            <span className="font-semibold text-ink">{result.data.total}</span> {result.data.total === 1 ? "position" : "positions"}
          </p>
        )}
      </div>

      {!result.ok ? (
        <LoadError error={result.error} what="positions" retryHref={href({})} service={ACADEMIC_SERVICE} />
      ) : result.data.items.length === 0 ? (
        <EmptyState icon="work" title={all ? "No positions yet" : "No active positions"}>
          Your Institution Admin defines positions such as Head of Department or Program Coordinator, with the weekly hours
          each one takes.
        </EmptyState>
      ) : (
        <>
          {/* Phones: one card per position. */}
          <ul className="divide-y divide-line md:hidden">
            {result.data.items.map((position) => (
              <li key={position.id}>
                <Link href={`/academic/positions/${position.id}`} className="flex gap-3 px-4 py-4 transition-colors hover:bg-background/60">
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-semibold text-ink">{position.name}</p>
                    <p className="mt-0.5 text-[13px] text-muted">
                      {position.unitType ? `Per ${UNIT_TYPES[position.unitType].toLowerCase()}` : "No unit"} ·{" "}
                      {position.hoursPerWeek === null ? "no hours in force" : `${hours(position.hoursPerWeek)} a week`}
                    </p>
                    <div className="mt-2 flex flex-wrap items-center gap-1.5">
                      <CodeTag>{position.code}</CodeTag>
                      <PositionStatus position={position} />
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
                  Position
                </th>
                <th scope="col" className="px-5 py-3">
                  Held per
                </th>
                <th scope="col" className="px-5 py-3 text-right">
                  Hours a week
                </th>
                <th scope="col" className="hidden px-5 py-3 lg:table-cell">
                  With other appointments
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
              {result.data.items.map((position) => (
                <tr key={position.id} className="relative transition-colors hover:bg-background/60">
                  <td className="px-5 py-3.5">
                    {/* The name's link covers the whole row, so the row opens the position. */}
                    <Link
                      href={`/academic/positions/${position.id}`}
                      className="font-semibold text-ink after:absolute after:inset-0 hover:text-primary-strong"
                    >
                      {position.name}
                    </Link>
                    <p className="mt-0.5 font-mono text-xs text-muted">{position.code}</p>
                  </td>
                  <td className="px-5 py-3.5 text-ink">
                    {position.unitType ? UNIT_TYPES[position.unitType] : <span className="text-muted">—</span>}
                    {position.exclusivePerUnit && <p className="text-xs text-muted">One holder at a time</p>}
                  </td>
                  <td className="px-5 py-3.5 text-right font-semibold tabular-nums text-ink">
                    {position.hoursPerWeek === null ? <span className="font-normal text-muted">—</span> : hours(position.hoursPerWeek)}
                  </td>
                  <td className="hidden px-5 py-3.5 text-muted lg:table-cell">
                    {position.policy ? combinationRule(position.policy.combinationRule, position.policy.capHoursPerWeek) : "—"}
                  </td>
                  <td className="px-5 py-3.5">
                    <PositionStatus position={position} />
                  </td>
                  <td className="px-5 py-3.5 text-right">
                    <Icon name="chevron_right" className="text-secondary" />
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
