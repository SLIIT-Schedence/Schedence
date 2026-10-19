// Step 3 module browser: DISPLAY filters over the mapped modules of the selected cycle. Pure and client-safe.
// Nothing here changes the generation request: generation always includes every mapped module.

import type { AcademicModule, CycleModule } from "@/lib/examination-workspace-types";

export const YEARS = [1, 2, 3, 4] as const;
export type ModuleFilters = { year: number | null; semester: number | null; specialization: string; search: string };
export const NO_FILTERS: ModuleFilters = { year: null, semester: null, specialization: "", search: "" };

export const isFiltered = (filters: ModuleFilters) =>
  filters.year !== null || filters.semester !== null || Boolean(filters.specialization) || Boolean(filters.search.trim());

/** Specializations that real mapped modules list, A-Z. */
export function moduleSpecializations(modules: Pick<AcademicModule, "specializations">[]): string[] {
  return [...new Set(modules.flatMap((module) => module.specializations))].sort();
}

/** Semesters that real mapped modules have (a cycle can include another semester's repeat-only modules). */
export function moduleSemesters(modules: Pick<AcademicModule, "semester">[]): number[] {
  return [...new Set(modules.map((module) => module.semester))].sort((a, b) => a - b);
}

function matches(module: AcademicModule, filters: ModuleFilters, ignoreYear = false): boolean {
  const search = filters.search.trim().toLowerCase();
  return (ignoreYear || filters.year === null || module.academic_year === filters.year)
    && (filters.semester === null || module.semester === filters.semester)
    && (!filters.specialization || module.specializations.includes(filters.specialization))
    && (!search || module.module_code.toLowerCase().includes(search) || module.module_name.toLowerCase().includes(search));
}

export function filterModules(modules: AcademicModule[], filters: ModuleFilters): AcademicModule[] {
  return modules.filter((module) => matches(module, filters))
    .sort((a, b) => a.academic_year - b.academic_year || a.semester - b.semester || a.module_code.localeCompare(b.module_code));
}

/** Modules per year under the other filters, for the year tabs. */
export function yearCounts(modules: AcademicModule[], filters: ModuleFilters): Record<number, number> {
  const counts: Record<number, number> = { 1: 0, 2: 0, 3: 0, 4: 0 };
  for (const item of modules) if (matches(item, filters, true)) counts[item.academic_year] = (counts[item.academic_year] ?? 0) + 1;
  return counts;
}

/**
 * The candidates this cycle examines for a module, from exact per-module counts. A repeat_only module examines
 * only its repeat registrations. Never split per specialization: a shared module's candidates are not divisible.
 */
export function cycleCandidates(module: AcademicModule, scope: CycleModule["candidate_scope"] | undefined) {
  if (scope === "repeat_only") {
    return { normal: 0, repeat: module.repeat_candidates, total: module.repeat_candidates, repeatOnly: true };
  }
  return { normal: module.normal_candidates, repeat: module.repeat_candidates, total: module.candidate_count, repeatOnly: false };
}
