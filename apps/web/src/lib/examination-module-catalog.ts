import type { ExamModuleDetails } from "@/lib/examination";

export const MODULE_PAGE_SIZE = 25;
export const MODULE_COLUMNS = {
  module_name: "Name", academic_year: "Year", semester: "Semester", specializations: "Specializations",
  credits: "Credits", duration_minutes: "FINAL duration", difficulty: "Difficulty", candidate_count: "Registrations",
} as const;
export type ModuleColumn = keyof typeof MODULE_COLUMNS;
export type CatalogFilters = { q: string; year: string; semester: string; specialization: string; missing: string };
type Params = Record<string, string | string[] | undefined>;
const first = (value: Params[string]) => (Array.isArray(value) ? value[0] : value) ?? "";
const integer = (value: unknown, min: number, max = Number.MAX_SAFE_INTEGER): value is number =>
  typeof value === "number" && Number.isSafeInteger(value) && value >= min && value <= max;

/** Display only: no substitutions from module codes, names, or another service. */
export function moduleValue(module: ExamModuleDetails, field: ModuleColumn): string | null {
  const value = module[field];
  switch (field) {
    case "module_name": return typeof value === "string" && value.trim() ? value : null;
    case "specializations": return Array.isArray(value) && value.length && value.every((item) => typeof item === "string" && item.trim())
      ? value.join(", ") : null;
    case "academic_year": return integer(value, 1, 4) ? String(value) : null;
    case "semester": return integer(value, 1, 2) ? String(value) : null;
    case "credits": return integer(value, 1) ? String(value) : null;
    case "difficulty": return integer(value, 1, 5) ? `${value} / 5` : null;
    case "candidate_count": return integer(value, 0) ? value.toLocaleString("en-GB") : null;
    case "duration_minutes": return module.has_final_exam === false ? "No FINAL exam" : integer(value, 1) ? `${value} min` : null;
  }
}

export function missingModuleFields(module: ExamModuleDetails): string[] {
  return (Object.keys(MODULE_COLUMNS) as ModuleColumn[]).filter((field) => moduleValue(module, field) === null)
    .map((field) => MODULE_COLUMNS[field]);
}

export function catalogOptions(modules: ExamModuleDetails[]) {
  return {
    years: [...new Set(modules.flatMap((module) => integer(module.academic_year, 1, 4) ? [module.academic_year] : []))].sort(),
    semesters: [...new Set(modules.flatMap((module) => integer(module.semester, 1, 2) ? [module.semester] : []))].sort(),
    specializations: [...new Set(modules.flatMap((module) => Array.isArray(module.specializations)
      ? module.specializations.filter((item) => typeof item === "string" && item.trim()) : []))].sort(),
  };
}

export function readCatalogFilters(params: Params): CatalogFilters {
  return {
    q: first(params.q).trim(), year: /^[1-4]$/.test(first(params.year)) ? first(params.year) : "",
    semester: /^[12]$/.test(first(params.semester)) ? first(params.semester) : "",
    specialization: first(params.specialization), missing: first(params.missing) === "yes" ? "yes" : "",
  };
}

export function filterCatalog(modules: ExamModuleDetails[], filters: CatalogFilters): ExamModuleDetails[] {
  const search = filters.q.toLowerCase();
  return modules.filter((module) => (!search || `${module.module_code} ${module.module_name ?? ""}`.toLowerCase().includes(search))
    && (!filters.year || String(module.academic_year) === filters.year)
    && (!filters.semester || String(module.semester) === filters.semester)
    && (!filters.specialization || module.specializations?.includes(filters.specialization))
    && (!filters.missing || missingModuleFields(module).length > 0))
    .sort((a, b) => a.module_code.localeCompare(b.module_code));
}
