"use server";

import { requireExaminationAdmin } from "@/lib/examination-access";
import { examinationRequest, type ExaminationApiError } from "@/lib/examination-api";
import { listExamPeriods, listExamModules, type ExamModule, type ExamPeriod } from "@/lib/examination";
import { calendarErrors, type ExamCalendarInput, parseCalendar } from "@/lib/examination-calendar";
import type { GeneratedTimetable, GenerationProblem, GenerationState, SaveDraftState, WorkloadScore } from "@/lib/examination-generation-types";
import { parseSchedulingPreferences, publicPreferences, type SchedulingPreferences } from "@/lib/examination-settings";
import { publicAnalysis } from "@/lib/examination-evaluation";

const GENERATION_TIMEOUT_MS = 360_000;
const GENERATION_LOGIN = "/login?error=SessionExpired&callbackUrl=%2Fexams%2Fgenerate";
const input = (data: FormData, name: string) => {
  const value = data.get(name);
  return typeof value === "string" ? value.trim() : "";
};
// Backend validation can mention a student identifier in prose. Redact it before serialization too.
const publicText = (value: string) => value.replace(/[A-Z]{2}[0-9]{8}/gi, "[student identifier omitted]");

function failure(error: ExaminationApiError): Extract<GenerationState, { status: "error" }> {
  const titles: Record<number, string> = {
    401: "Your session has ended",
    403: "Generation access denied",
    409: "Generation request conflicts with the current data",
    422: "Check the cycle inputs and solver settings",
    429: "The timetable solver is busy",
    503: "Generation is temporarily unavailable",
  };
  const hint = error.status === 403 ? " Your account needs examinations:generate in the Examination Service's institution."
    : error.status === 429 ? " Wait for the current generation to finish before trying again."
      : error.code === "TIMEOUT" || error.code === "UNREACHABLE"
        ? " The solver may still be running. Wait before submitting another request." : "";
  return {
    status: "error", httpStatus: error.status,
    title: titles[error.status] ?? (error.code === "TIMEOUT" ? "Generation request timed out" : "Could not generate a timetable"),
    message: publicText(error.message) + hint,
  };
}

function publicWorkload(score: WorkloadScore | null): WorkloadScore | null {
  return score === null ? null : {
    ...Object.fromEntries((["objective_units", "same_day_student_pairs", "student_days_with_multiple_exams", "short_gap_student_pairs",
      "difficult_pairs_below_target", "minimum_gap_minutes", "median_gap_minutes", "lower_decile_gap_minutes", "maximum_window_exams",
      "maximum_window_load_points", "worst_student_weighted_penalty", "repeat_student_mean_weighted_penalty"] as const)
      .filter((key) => score[key] !== undefined).map((key) => [key, score[key]])),
    total_weighted_penalty: score.total_weighted_penalty,
    penalties: score.penalties.map((penalty) => ({
      constraint: publicText(penalty.constraint), weight: penalty.weight,
      penalty_points: penalty.penalty_points, weighted_points: penalty.weighted_points,
    })),
  };
}

/** Explicitly pick public fields; never spread raw responses or individual seating/scoring records. */
function publicResult(raw: GeneratedTimetable, catalog: ExamModule[], period: ExamPeriod, generatedAt: string): GeneratedTimetable {
  const problems = (items: GenerationProblem[]) => items.map((item) => ({ rule: publicText(item.rule), message: publicText(item.message) }));
  const fairness = raw.optimization?.fairness;
  return {
    status: raw.status, generated: raw.generated, generated_at: generatedAt,
    analysis: publicAnalysis(raw.analysis),
    solver: { mode: raw.solver.mode, time_limit_seconds: raw.solver.time_limit_seconds, wall_time_seconds: raw.solver.wall_time_seconds },
    inputs: {
      scheduling_preferences: publicPreferences(raw.inputs.scheduling_preferences),
      period_code: publicText(raw.inputs.period_code), period_name: publicText(raw.inputs.period_name),
      academic_year_label: period.academic_year_label ? publicText(period.academic_year_label) : undefined,
      semester: period.semester, exam_kind: period.exam_kind,
      modules: raw.inputs.modules, students: raw.inputs.students,
      calendar: raw.inputs.start_date && raw.inputs.end_date ? {
        source: raw.inputs.calendar_source === "selected" ? "selected" : "period",
        start_date: raw.inputs.start_date, end_date: raw.inputs.end_date, exam_days: raw.inputs.exam_days?.length ?? 0,
        day_start_time: raw.inputs.day_start_time ?? null, day_end_time: raw.inputs.day_end_time ?? null,
        include_saturdays: raw.inputs.include_saturdays ?? null, include_sundays: raw.inputs.include_sundays ?? null,
        exclusions: (raw.inputs.exclusions ?? []).map((item) => ({ excluded_date: item.excluded_date, reason: publicText(item.reason) })),
      } : null,
    },
    validation: { passed: raw.validation.passed, rules_checked: raw.validation.rules_checked.map(publicText), violations: problems(raw.validation.violations) },
    summary: { sessions: raw.summary.sessions, exam_days_used: raw.summary.exam_days_used, seat_allocations: raw.summary.seat_allocations },
    sessions: raw.generated && raw.validation.passed === true ? raw.sessions.map((session) => ({
      academic_year: session.academic_year, semester: session.semester, delivery_mode: session.delivery_mode,
      specializations: catalog.find((module) => module.module_code === session.module_code)?.specializations.map(publicText),
      normal_candidates: session.normal_candidates, repeat_candidates: session.repeat_candidates,
      date: session.date, start_time: session.start_time, end_time: session.end_time,
      module_code: publicText(session.module_code), module_name: publicText(session.module_name),
      session_number: session.session_number, session_count: session.session_count, candidates: session.candidates,
      rooms: session.rooms.map((room) => ({ venue_id: publicText(room.venue_id), venue_name: publicText(room.venue_name), seats: room.seats })),
    })) : [],
    workload: publicWorkload(raw.workload),
    optimization: raw.optimization === null ? null : {
      status: raw.optimization.status === null ? null : publicText(raw.optimization.status), improved: raw.optimization.improved,
      baseline: publicWorkload(raw.optimization.baseline), selected: publicWorkload(raw.optimization.selected),
      objective_bound_units: raw.optimization.objective_bound_units, relative_gap: raw.optimization.relative_gap,
      fairness: fairness ? {
        protection_applied: fairness.protection_applied, limits_satisfied: fairness.limits_satisfied, students: fairness.students,
        students_improved: fairness.students_improved, students_worsened: fairness.students_worsened, students_unchanged: fairness.students_unchanged,
        baseline_worst_normalized_penalty: fairness.baseline_worst_normalized_penalty,
        selected_worst_normalized_penalty: fairness.selected_worst_normalized_penalty,
        maximum_individual_increase_points: fairness.maximum_individual_increase_points,
        worst_student_increase_points: fairness.worst_student_increase_points,
      } : null,
    },
    violations: problems(raw.violations), diagnostics: raw.diagnostics.map(publicText), notices: raw.notices.map(publicText),
    excluded_registrations: {
      note: publicText(raw.excluded_registrations.note), total: raw.excluded_registrations.total,
      normal: raw.excluded_registrations.normal, repeat: raw.excluded_registrations.repeat,
    },
  };
}

type GenerationBody = { period_code: string; mode: string; time_limit_seconds?: number; calendar?: ExamCalendarInput; scheduling_preferences?: SchedulingPreferences };
type Checked = { ok: true; body: GenerationBody; period: ExamPeriod } | { ok: false; state: Extract<GenerationState, { status: "error" }> };

/**
 * The checks shared by preview and save: trusted FINAL cycle, valid mode, limit and selected exam calendar, unchanged
 * reviewed inputs. The calendar (when sent) is used for this generation only; the saved period is not changed.
 */
async function checkedGeneration(formData: FormData): Promise<Checked> {
  const periodCode = input(formData, "period_code");
  const mode = input(formData, "mode");
  const limitText = input(formData, "time_limit_seconds");
  const limit = limitText === "" ? undefined : Number(limitText);
  const fieldErrors: Record<string, string> = {};
  const preferencesText = input(formData, "scheduling_preferences");
  let preferences: SchedulingPreferences | null = null;
  if (preferencesText) {
    try { preferences = parseSchedulingPreferences(JSON.parse(preferencesText)); } catch { preferences = null; }
    if (!preferences) fieldErrors.scheduling_preferences = "Check the Advanced Settings in the Generate step. Only supported, valid preferences can be submitted.";
  }
  if (!/^[A-Z0-9-]{1,32}$/.test(periodCode)) fieldErrors.period_code = "Choose a FINAL examination cycle.";
  if (mode !== "feasibility" && mode !== "optimized") fieldErrors.mode = "Choose Feasibility or Optimized.";
  if (limit !== undefined && (!Number.isFinite(limit) || limit <= 0 || limit > 300)) {
    fieldErrors.time_limit_seconds = "Enter a time limit greater than 0 and no more than 300 seconds.";
  }
  const calendarText = input(formData, "calendar");
  let calendar: ExamCalendarInput | null = null;
  if (calendarText) {
    try { calendar = parseCalendar(JSON.parse(calendarText)); } catch { calendar = null; }
    if (!calendar) fieldErrors.calendar = "The selected exam calendar could not be read. Review the Exam calendar step.";
  }
  if (Object.keys(fieldErrors).length) return { ok: false, state: { status: "error", title: "Check your choices", message: "Correct the highlighted fields and try again.", fieldErrors } };

  // Re-read trusted cycle data on every submission, including forged/stale forms.
  const periods = await listExamPeriods();
  if (!periods.ok) return { ok: false, state: failure(periods.error) };
  const period = periods.data.find((period) => period.period_code === periodCode && period.exam_kind === "FINAL");
  if (!period) {
    return { ok: false, state: { status: "error", title: "FINAL cycle unavailable", message: "Select an available FINAL cycle. Refresh this page if the cycle list has changed.", fieldErrors: { period_code: "This cycle is not an available FINAL examination cycle." } } };
  }
  if (calendar) {
    const problems = calendarErrors(calendar, periods.data.find((period) => period.period_code === periodCode)?.academic_year_label);
    if (Object.keys(problems).length) {
      return { ok: false, state: { status: "error", title: "Check the exam calendar", message: Object.values(problems).join(" "), fieldErrors: problems } };
    }
  }
  const expectedPeriod = input(formData, "expected_period_fingerprint");
  if (expectedPeriod && periods.data.find((period) => period.period_code === periodCode)?.fingerprint !== expectedPeriod) {
    return { ok: false, state: { status: "error", httpStatus: 409, title: "The period changed", message: "Reload the cycle and review its saved calendar before generating." } };
  }
  const expectedSettings = input(formData, "expected_settings_fingerprint");
  const expectedSeating = input(formData, "expected_seating_version");
  if (expectedSettings || expectedSeating) {
    const [settings, seating] = await Promise.all([
      examinationRequest<{ fingerprint: string | null }>("/api/v1/settings/scheduling", { query: { history_limit: 0 } }),
      examinationRequest<{ ruleset_version: number }>("/api/v1/settings/exam-seating", { query: { history_limit: 0 } }),
    ]);
    if (!settings.ok) return { ok: false, state: failure(settings.error) };
    if (!seating.ok) return { ok: false, state: failure(seating.error) };
    if ((expectedSettings && settings.data.fingerprint !== expectedSettings) || (expectedSeating && String(seating.data.ruleset_version) !== expectedSeating)) {
      return { ok: false, state: { status: "error", httpStatus: 409, title: "Scheduling or seating settings changed", message: "Reload the cycle and review the current settings before generating." } };
    }
  }
  return { ok: true, period, body: { period_code: periodCode, mode, ...(limit === undefined ? {} : { time_limit_seconds: limit }), ...(calendar ? { calendar } : {}), ...(preferences ? { scheduling_preferences: preferences } : {}) } };
}

export async function generateFinalTimetable(_previous: GenerationState, formData: FormData): Promise<GenerationState> {
  await requireExaminationAdmin();
  const checked = await checkedGeneration(formData);
  if (!checked.ok) return checked.state;
  const result = await examinationRequest<GeneratedTimetable>("/api/v1/timetables/generate", {
    method: "POST", timeoutMs: GENERATION_TIMEOUT_MS, sessionExpiredPath: GENERATION_LOGIN, body: { ...checked.body, include_analysis: true },
  });
  if (!result.ok) return failure(result.error);
  const generatedAt = new Date().toISOString();
  const catalog = await listExamModules();
  try {
    const output = publicResult(result.data, catalog.ok ? catalog.data : [], checked.period, generatedAt);
    if (!catalog.ok) output.notices.push("Specialization metadata could not be loaded. Specialization filters are unavailable for this result.");
    return { status: "complete", result: output };
  } catch {
    return { status: "error", title: "Could not read the generation result", message: "The Examination Service returned an unexpected result. Refresh the page before trying again." };
  }
}

const SAVE_TITLES: Record<number, string> = {
  403: "Saving drafts is not allowed for your account",
  409: "The draft could not be saved because the data changed",
  422: "No validated timetable was saved",
  429: "The timetable solver is busy",
  503: "Saving is temporarily unavailable",
};

/**
 * Generate & Save Draft: the backend runs a NEW generation, validates it again and stores it as a DRAFT
 * (POST /timetables/drafts). It may differ from an earlier unsaved preview. Needs explicit confirmation.
 */
export async function saveFinalTimetableDraft(_previous: SaveDraftState, formData: FormData): Promise<SaveDraftState> {
  await requireExaminationAdmin();
  if (formData.get("confirm_save") !== "yes") {
    return { status: "error", title: "Confirm the save", message: "Confirm that saving runs a fresh generation before saving a draft." };
  }
  const checked = await checkedGeneration(formData);
  if (!checked.ok) return checked.state;
  const saved = await examinationRequest<{ timetable_id: number; draft_number: number; period_code: string; status: string }>(
    "/api/v1/timetables/drafts",
    { method: "POST", timeoutMs: GENERATION_TIMEOUT_MS, sessionExpiredPath: GENERATION_LOGIN, body: checked.body },
  );
  if (!saved.ok) {
    const state = failure(saved.error);
    const problems = (saved.error.problems ?? []).filter((problem) => problem.rule !== "NOT_SAVED")
      .slice(0, 6).map((problem) => `${publicText(problem.rule)}: ${publicText(problem.message)}`);
    const interrupted = saved.error.code === "TIMEOUT" || saved.error.code === "UNREACHABLE";
    return {
      status: "error", httpStatus: saved.error.status,
      title: SAVE_TITLES[saved.error.status] ?? (saved.error.code === "TIMEOUT" ? "The save request timed out" : "Could not save the draft"),
      message: interrupted ? `${state.message} Check Saved timetables before trying again: the draft may have been saved.`
        : saved.error.status === 422 && problems.length ? `Nothing was saved. ${problems.join(" · ")}` : state.message,
    };
  }
  const { timetable_id: timetableId, draft_number: draftNumber, period_code: periodCode, status } = saved.data;
  if (!Number.isSafeInteger(timetableId) || timetableId < 1 || status !== "DRAFT") {
    return { status: "error", title: "Could not read the saved draft", message: "The Examination Service returned an unexpected answer. Open Saved timetables to check whether the draft was saved." };
  }
  return { status: "saved", timetableId, draftNumber, periodCode: publicText(periodCode) };
}
