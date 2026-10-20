// Step 1 of the generator: choose an examination by academic year, type and semester. Pure and client-safe.
// Every option is a real backend period; nothing is invented when no period matches.

export type ExamKind = "MID" | "FINAL";
export const EXAM_KINDS: ExamKind[] = ["FINAL", "MID"];

/** One configured examination period, with counts the server could determine exactly (null otherwise). */
export type ExamOption = {
  period_code: string;
  period_name: string;
  academic_year_label: string;
  semester: number;
  exam_kind: ExamKind;
  start_date: string;
  end_date: string;
  exam_days: number;
  module_count: number | null;
  registration_count: number | null;
};

export type ExamChoice = { year: string; kind: ExamKind; semester: 1 | 2 | null; periodCode: string };

/** Only FINAL timetables can be generated in this wizard. */
export const isGeneratable = (option: Pick<ExamOption, "exam_kind">) => option.exam_kind === "FINAL";

/** Academic years that have at least one period, newest first. */
export function academicYears(options: ExamOption[]): string[] {
  return [...new Set(options.map((option) => option.academic_year_label))].sort((a, b) => b.localeCompare(a));
}

export function matchExaminations(options: ExamOption[], { year, kind, semester }: Pick<ExamChoice, "year" | "kind" | "semester">): ExamOption[] {
  if (!semester) return [];
  return options.filter((option) => option.academic_year_label === year && option.exam_kind === kind && option.semester === semester)
    .sort((a, b) => a.start_date.localeCompare(b.start_date) || a.period_code.localeCompare(b.period_code));
}

export const countFor = (options: ExamOption[], year: string, kind: ExamKind, semester?: 1 | 2) =>
  options.filter((option) => option.academic_year_label === year && option.exam_kind === kind && (!semester || option.semester === semester)).length;

/** The newest academic year, and FINAL when that year has a FINAL period. No semester is preselected. */
export function initialChoice(options: ExamOption[]): ExamChoice {
  const year = academicYears(options)[0] ?? "";
  const kind = EXAM_KINDS.find((candidate) => countFor(options, year, candidate) > 0) ?? "FINAL";
  return { year, kind, semester: null, periodCode: "" };
}

/** A changed year, type or semester selects the matching period when exactly one exists. */
export function updateChoice(options: ExamOption[], next: Omit<ExamChoice, "periodCode">): ExamChoice {
  const matches = matchExaminations(options, next);
  return { ...next, periodCode: matches.length === 1 ? matches[0].period_code : "" };
}

/**
 * Registrations examined in a cycle, from the real mapping and module catalog. Exact only when every mapped
 * module is scope "all" and has catalog data: a repeat_only module examines only part of its registrations.
 */
export function cycleRegistrations(
  mapping: { module_code: string; candidate_scope: string }[],
  catalog: { module_code: string; candidate_count: number }[],
): number | null {
  const counts = new Map(catalog.map((module) => [module.module_code, module.candidate_count]));
  let total = 0;
  for (const member of mapping) {
    const count = counts.get(member.module_code);
    if (member.candidate_scope !== "all" || count === undefined) return null;
    total += count;
  }
  return total;
}
