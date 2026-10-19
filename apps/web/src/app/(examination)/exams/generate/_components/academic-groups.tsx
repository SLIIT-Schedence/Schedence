"use client";

import { useId, useState } from "react";
import { Badge, Card, EmptyState } from "@/components/console/ui";
import { Icon } from "@/components/icon";
import {
  cycleCandidates, filterModules, isFiltered, moduleSemesters, moduleSpecializations, NO_FILTERS, YEARS, yearCounts, type ModuleFilters,
} from "@/lib/examination-module-browser";
import type { AcademicFilters } from "@/lib/examination-presentation";
import type { AcademicModule, CycleModule } from "@/lib/examination-workspace-types";
import { fieldClass, secondaryButton } from "./period-calendar";

const number = (value: number | null) => (value === null ? "—" : value.toLocaleString("en-GB"));
const tab = "flex min-h-11 items-center justify-center gap-2 rounded-xl border px-3 text-sm font-semibold transition-colors "
  + "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary disabled:cursor-not-allowed disabled:opacity-50";
const tabState = (active: boolean) => active ? "border-primary bg-primary text-white shadow-[0_8px_20px_-12px_var(--color-primary)]"
  : "border-line bg-surface text-ink hover:border-primary/40 hover:bg-primary-soft";

/** The timetable browser's academic filters (results and saved timetables). */
export function AcademicFilterBar({ items, filters, onChange, includeModule = false }: {
  items: { module_code: string; module_name?: string; academic_year?: number; semester?: number; specializations?: string[] }[];
  filters: AcademicFilters; onChange: (filters: AcademicFilters) => void; includeModule?: boolean;
}) {
  const id = useId();
  const specializations = [...new Set(items.flatMap((item) => item.specializations ?? []))].sort();
  const semesters = [...new Set(items.flatMap((item) => item.semester === undefined ? [] : [item.semester]))].sort();
  const modules = [...new Map(items.map((item) => [item.module_code, item])).values()].sort((a, b) => a.module_code.localeCompare(b.module_code));
  return <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
    <label htmlFor={`${id}-year`} className="text-xs font-medium text-muted">Academic year<select id={`${id}-year`} className={fieldClass} value={filters.year} onChange={(event) => onChange({ ...filters, year: event.target.value })}><option value="">All years</option>{[1, 2, 3, 4].map((year) => <option key={year} value={year}>Year {year}</option>)}</select></label>
    <label htmlFor={`${id}-semester`} className="text-xs font-medium text-muted">Semester<select id={`${id}-semester`} className={fieldClass} value={filters.semester} onChange={(event) => onChange({ ...filters, semester: event.target.value })}><option value="">All semesters</option>{semesters.map((semester) => <option key={semester} value={semester}>Semester {semester}</option>)}</select></label>
    <label htmlFor={`${id}-specialization`} className="text-xs font-medium text-muted">Specialization<select id={`${id}-specialization`} className={fieldClass} disabled={!specializations.length} value={filters.specialization} onChange={(event) => onChange({ ...filters, specialization: event.target.value })}><option value="">{specializations.length ? "All specializations" : "No metadata returned"}</option>{specializations.map((specialization) => <option key={specialization}>{specialization}</option>)}</select></label>
    {includeModule && <label htmlFor={`${id}-module`} className="text-xs font-medium text-muted">Module<select id={`${id}-module`} className={fieldClass} value={filters.module} onChange={(event) => onChange({ ...filters, module: event.target.value })}><option value="">All modules</option>{modules.map((module) => <option key={module.module_code} value={module.module_code}>{module.module_code} · {module.module_name}</option>)}</select></label>}
  </div>;
}

/** Step 3: browse the mapped modules. Every filter here is display-only; generation uses the whole mapping. */
export function AcademicGroups({ modules, mapping }: { modules: AcademicModule[]; mapping: CycleModule[] }) {
  const id = useId();
  const [filters, setFilters] = useState<ModuleFilters>(NO_FILTERS);
  const set = (patch: Partial<ModuleFilters>) => setFilters((previous) => ({ ...previous, ...patch }));
  const scopes = new Map(mapping.map((item) => [item.module_code, item.candidate_scope]));
  const rows = filterModules(modules, filters);
  const perYear = yearCounts(modules, filters);
  const specializations = moduleSpecializations(modules);
  const semesters = moduleSemesters(modules);
  const missing = mapping.filter((item) => !modules.some((module) => module.module_code === item.module_code));
  const splitUnavailable = modules.some((module) => module.repeat_candidates === null);

  return (
    <Card title="3. Academic groups and mapped modules" description="Browse the modules of this examination. These filters change only what you see."
      actions={<Badge tone="primary">All {mapping.length} mapped modules are generated</Badge>}>
      <div className="space-y-5 p-5">
        <div role="group" aria-label="Academic year" className="grid grid-cols-2 gap-2 sm:grid-cols-5">
          <button type="button" aria-pressed={filters.year === null} onClick={() => set({ year: null })} className={`${tab} ${tabState(filters.year === null)}`}>
            All years
          </button>
          {YEARS.map((year) => (
            <button key={year} type="button" aria-pressed={filters.year === year} onClick={() => set({ year })} className={`${tab} ${tabState(filters.year === year)}`}>
              Year {year}
              <span className={`rounded-full px-2 py-0.5 text-xs ${filters.year === year ? "bg-white/20" : "bg-background text-muted"}`}>{perYear[year]}</span>
            </button>
          ))}
        </div>

        <div className="grid gap-3 md:grid-cols-[auto_minmax(0,14rem)_minmax(0,1fr)] md:items-end">
          <div role="group" aria-label="Semester" className="inline-flex self-start rounded-xl bg-background p-1">
            {[null, ...semesters].map((semester) => (
              <button key={semester ?? "all"} type="button" aria-pressed={filters.semester === semester} onClick={() => set({ semester })}
                className={`rounded-lg px-3.5 py-1.5 text-sm font-semibold transition-colors ${filters.semester === semester ? "bg-surface text-ink shadow-[0_1px_3px_rgb(29_33_66/0.12)]" : "text-muted hover:text-ink"}`}>
                {semester === null ? "All semesters" : `Semester ${semester}`}
              </button>
            ))}
          </div>
          <label htmlFor={`${id}-specialization`} className="text-xs font-medium text-muted">Specialization
            <select id={`${id}-specialization`} className={fieldClass} value={filters.specialization} disabled={!specializations.length}
              onChange={(event) => set({ specialization: event.target.value })}>
              <option value="">{specializations.length ? "All specializations" : "No specialization data"}</option>
              {specializations.map((specialization) => <option key={specialization} value={specialization}>{specialization}</option>)}
            </select>
          </label>
          <label htmlFor={`${id}-search`} className="relative text-xs font-medium text-muted">Search modules
            <Icon name="search" size={18} className="pointer-events-none absolute bottom-2.5 left-3 text-secondary" />
            <input id={`${id}-search`} type="search" value={filters.search} placeholder="Module code or name" onChange={(event) => set({ search: event.target.value })}
              className={`${fieldClass} pl-9`} />
          </label>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3">
          <p role="status" className="text-sm text-muted">
            Showing <span className="font-semibold text-ink">{rows.length}</span> of <span className="font-semibold text-ink">{mapping.length}</span> mapped modules.
          </p>
          {isFiltered(filters) && <button type="button" className={secondaryButton} onClick={() => setFilters(NO_FILTERS)}>Clear filters</button>}
        </div>

        {!rows.length ? (
          <EmptyState icon="filter_alt_off" title={modules.length ? "No mapped modules match these filters" : "No module details available"}
            action={isFiltered(filters) ? <button type="button" className={secondaryButton} onClick={() => setFilters(NO_FILTERS)}>Clear filters</button> : undefined}>
            {modules.length ? "Try another year, semester, specialization or search. Generation still includes every mapped module." : "The mapped modules returned no catalog details."}
          </EmptyState>
        ) : (
          <div className="overflow-x-auto rounded-xl border border-line">
            <table className="w-full min-w-[56rem] text-left">
              <caption className="sr-only">Mapped modules matching the display filters</caption>
              <thead className="border-b border-line bg-background text-xs text-muted">
                <tr>{["Module", "Year · Semester", "Specializations", "Delivery", "Normal", "Repeat", "Examined"].map((label) =>
                  <th key={label} scope="col" className={`px-4 py-3 font-semibold ${["Normal", "Repeat", "Examined"].includes(label) ? "text-right" : ""}`}>{label}</th>)}</tr>
              </thead>
              <tbody className="divide-y divide-line">
                {rows.map((module) => {
                  const candidates = cycleCandidates(module, scopes.get(module.module_code));
                  return (
                    <tr key={module.module_code} className="align-top">
                      <th scope="row" className="px-4 py-3 text-sm font-medium text-ink">
                        <span className="font-mono text-primary-strong">{module.module_code}</span>
                        <span className="mt-0.5 block font-normal">{module.module_name}</span>
                        {candidates.repeatOnly && <span className="mt-1 inline-block"><Badge tone="neutral">Repeat candidates only</Badge></span>}
                      </th>
                      <td className="whitespace-nowrap px-4 py-3 text-sm text-muted">Year {module.academic_year} · Semester {module.semester}</td>
                      <td className="px-4 py-3 text-sm">
                        {module.specializations.length ? <span className="flex flex-wrap gap-1">{module.specializations.map((item) =>
                          <span key={item} className="rounded-md bg-secondary-soft px-1.5 py-0.5 font-mono text-[11px] text-secondary-ink">{item}</span>)}</span>
                          : <span className="text-muted">Not available</span>}
                      </td>
                      <td className="px-4 py-3 text-sm capitalize text-muted">{module.delivery_mode}</td>
                      <td className="px-4 py-3 text-right font-mono text-sm text-muted">{candidates.repeatOnly ? "—" : number(candidates.normal)}</td>
                      <td className="px-4 py-3 text-right font-mono text-sm text-muted">{number(candidates.repeat)}</td>
                      <td className="px-4 py-3 text-right font-mono text-sm font-semibold text-ink">{number(candidates.total)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {missing.length > 0 && <p className="rounded-xl bg-secondary-soft p-3 text-sm text-secondary-ink">Mapped modules without catalog details (not shown above, still generated): {missing.map((item) => item.module_code).join(", ")}.</p>}
        <p className="text-xs leading-5 text-muted">
          Candidate counts are per module, for every specialization that shares it; they are never split by specialization.
          {splitUnavailable ? " The normal/repeat split could not be read, so only the total is shown." : " Normal and repeat counts come from the module's registrations."}
          {" "}A repeat-only module examines its repeat candidates only.
        </p>
      </div>
    </Card>
  );
}
