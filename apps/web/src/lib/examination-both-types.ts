import type { GeneratedTimetable, GenerationProblem, GenerationState } from "@/lib/examination-generation-types";
import type { ExamCalendarInput } from "@/lib/examination-calendar";

export type BothCycleInput = { period_code: string; calendar: ExamCalendarInput; expected_period_fingerprint: string | null };
export type BothGenerationResult = {
  batch_id: string; generated: boolean; sequential_order: string[]; skipped_period_codes: string[];
  results: GeneratedTimetable[]; pair_validation: { passed: boolean | null; violations: GenerationProblem[] };
  shared_candidates: number | null; notices: string[];
};
export type BothGenerationState = { status: "idle" } | Extract<GenerationState, { status: "error" }>
  | { status: "complete"; result: BothGenerationResult };
export type BothSaveState = { status: "idle" } | Extract<GenerationState, { status: "error" }>
  | { status: "saved"; timetables: { timetableId: number; draftNumber: number; periodCode: string }[] };
