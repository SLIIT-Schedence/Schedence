import Form from "next/form";
import Link from "next/link";
import { Suspense } from "react";
import { Badge, Card, EmptyState, PageHeader, Pagination, pageNumber, query, TableSkeleton } from "@/components/console/ui";
import { requireExaminationAdmin } from "@/lib/examination-access";
import { listExamModules } from "@/lib/examination";
import { catalogOptions, filterCatalog, missingModuleFields, MODULE_COLUMNS, MODULE_PAGE_SIZE, readCatalogFilters } from "@/lib/examination-module-catalog";
import { ExaminationReadError } from "../_components/read-error";
import { RefreshButton } from "../_components/refresh-button";
import { ModuleValue } from "./_components/module-values";

export const metadata = { title: "Examination modules" };
type SearchParams = Promise<Record<string, string | string[] | undefined>>;
const field = "mt-1 h-10 w-full rounded-xl border border-line bg-surface px-3 text-sm text-ink focus:border-primary";
const button = "inline-flex h-10 items-center justify-center rounded-xl border border-line px-4 text-sm font-semibold text-ink hover:bg-background";

export default function ModulesPage({ searchParams }: { searchParams: SearchParams }) {
  return <div className="space-y-6">
    <PageHeader title="Modules" description="Browse existing examination modules and their recorded academic and examination details."
      actions={<><Badge tone="neutral">Read only</Badge><RefreshButton label="Refresh" /></>} />
    <Card><Suspense fallback={<TableSkeleton />}><ModuleCatalog searchParams={searchParams} /></Suspense></Card>
  </div>;
}

async function ModuleCatalog({ searchParams }: { searchParams: SearchParams }) {
  await requireExaminationAdmin();
  const [params, result] = await Promise.all([searchParams, listExamModules()]);
  if (!result.ok) return <ExaminationReadError error={result.error} what="modules" />;
  const modules = result.data;
  const filters = readCatalogFilters(params);
  const options = catalogOptions(modules);
  const filtered = filterCatalog(modules, filters);
  const page = pageNumber(params.page);
  const rows = filtered.slice((page - 1) * MODULE_PAGE_SIZE, page * MODULE_PAGE_SIZE);
  const missing = modules.filter((module) => missingModuleFields(module).length).length;
  const href = (page: number) => `/exams/modules${query({ ...filters, page })}`;

  return <>
    <Form action="/exams/modules" key={JSON.stringify(filters)} className="grid gap-3 border-b border-line p-5 sm:grid-cols-2 xl:grid-cols-4">
      <label className="text-xs font-semibold text-muted">Search modules
        <input type="search" name="q" defaultValue={filters.q} placeholder="Module code or name" className={field} />
      </label>
      <label className="text-xs font-semibold text-muted">Academic year
        <select name="year" defaultValue={filters.year} className={field}><option value="">All years</option>
          {options.years.map((year) => <option key={year} value={year}>Year {year}</option>)}</select>
      </label>
      <label className="text-xs font-semibold text-muted">Semester
        <select name="semester" defaultValue={filters.semester} className={field}><option value="">All semesters</option>
          {options.semesters.map((semester) => <option key={semester} value={semester}>Semester {semester}</option>)}</select>
      </label>
      <label className="text-xs font-semibold text-muted">Specialization
        <select name="specialization" defaultValue={filters.specialization} className={field}><option value="">All specializations</option>
          {filters.specialization && !options.specializations.includes(filters.specialization) && <option value={filters.specialization}>{filters.specialization} (unavailable)</option>}
          {options.specializations.map((item) => <option key={item} value={item}>{item}</option>)}</select>
      </label>
      <div className="flex flex-wrap items-center gap-3 sm:col-span-2 xl:col-span-4">
        <label className="mr-auto flex items-center gap-2 text-sm text-muted"><input type="checkbox" name="missing" value="yes" defaultChecked={filters.missing === "yes"} />Only modules with missing data</label>
        <button type="submit" className="h-10 rounded-xl bg-primary px-4 text-sm font-semibold text-white hover:bg-primary-strong">Apply filters</button>
        <Link href="/exams/modules" className={button}>Clear filters</Link>
      </div>
    </Form>
    <div className="space-y-2 border-b border-line px-5 py-4 text-sm text-muted">
      <p role="status">{filtered.length} of {modules.length} modules match. {missing > 0 && <Badge tone="accent">{missing} with missing data</Badge>}</p>
      <p className="text-xs">Highlighted fields are unavailable in the source. Registration totals include all registrations for each module; a normal/repeat split is not provided here.</p>
    </div>
    {!modules.length ? <EmptyState icon="menu_book" title="No examination modules available">No modules were returned by the Examination Service.</EmptyState>
      : !filtered.length ? <EmptyState icon="filter_alt_off" title="No modules match these filters" action={<Link href="/exams/modules" className={button}>Clear filters</Link>}>Try another search, year, semester or specialization.</EmptyState>
      : !rows.length ? <EmptyState title="This page is empty" action={<Link href={href(1)} className={button}>Go to the first page</Link>}>Choose the first page to see the matching modules.</EmptyState>
      : <>
        <div className="overflow-x-auto"><table className="w-full min-w-[70rem] text-left">
          <caption className="sr-only">Examination modules matching the selected filters</caption>
          <thead className="border-b border-line bg-background text-xs text-muted"><tr>
            <th scope="col" className="px-4 py-3 font-semibold">Module code</th>
            {Object.entries(MODULE_COLUMNS).map(([key, label]) => <th key={key} scope="col" className="px-4 py-3 font-semibold">{label}</th>)}
          </tr></thead>
          <tbody className="divide-y divide-line">{rows.map((module) => <tr key={module.module_code} className="align-top">
            <th scope="row" className="px-4 py-4 text-sm font-semibold"><Link href={`/exams/modules/${encodeURIComponent(module.module_code)}`} prefetch={false}
              aria-label={`View details for ${module.module_code}`} className="font-mono text-primary-strong underline-offset-2 hover:underline">{module.module_code}</Link></th>
            {(Object.keys(MODULE_COLUMNS) as (keyof typeof MODULE_COLUMNS)[]).map((key) => <td key={key} className="px-4 py-4 text-sm text-muted"><ModuleValue module={module} field={key} /></td>)}
          </tr>)}</tbody>
        </table></div>
        <Pagination page={page} pageSize={MODULE_PAGE_SIZE} total={filtered.length} hrefFor={href} />
      </>}
  </>;
}
