import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import ts from "typescript";
import * as jsxRuntime from "react/jsx-runtime";

// Entirely isolated API fixtures and component state checks. No server, credentials or database access.
function load(source, mocks = {}) {
  const code = ts.transpileModule(readFileSync(path.join(import.meta.dirname, "..", source), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const result = { exports: {} };
  new Function("require", "module", "exports", code)((name) => {
    assert.ok(Object.hasOwn(mocks, name), `Unexpected dependency ${name}`); return mocks[name];
  }, result, result.exports);
  return result.exports;
}
const settings = load("src/lib/examination-settings.ts");
const calendarLib = load("src/lib/examination-calendar.ts");
const S1 = "LOCAL-S1-FINAL", S2 = "LOCAL-S2-FINAL";
const calendar = (day = "2026-11-16") => ({ start_date: day, end_date: day, day_start_time: "08:30", day_end_time: "17:30",
  include_saturdays: false, include_sundays: false, exclusions: [] });
const periods = [1, 2].map((semester) => ({ period_code: semester === 1 ? S1 : S2, period_name: `Semester ${semester}`,
  academic_year_label: "2026/2027", semester, exam_kind: "FINAL", ...calendar(), exam_days: ["2026-11-16"], fingerprint: "period" }));
const formData = (extra = {}) => {
  const data = new FormData();
  for (const [key, value] of Object.entries({ cycles: JSON.stringify(periods.map((p, i) => ({ period_code: p.period_code,
    calendar: calendar(i ? "2026-11-17" : "2026-11-16"), expected_period_fingerprint: "period" }))),
  mode: "optimized", time_limit_seconds: "10", scheduling_preferences: '{"scheduling":{"minimum_break_minutes":120}}', ...extra })) data.set(key, value);
  return data;
};
const generated = (i) => ({ status: "FEASIBLE", generated: true, solver: { mode: "optimized", time_limit_seconds: 10, wall_time_seconds: 1 },
  inputs: { period_code: periods[i].period_code, period_name: periods[i].period_name, modules: 1, students: 1,
    start_date: i ? "2026-11-17" : "2026-11-16", end_date: i ? "2026-11-17" : "2026-11-16", calendar_source: "selected", exam_days: [i ? "2026-11-17" : "2026-11-16"],
    batch: { batch_id: "batch", peer_period_code: periods[1 - i].period_code, strategy: "semester_1_then_2" } },
  validation: { passed: true, rules_checked: ["H1"], violations: [] }, summary: { sessions: 1, exam_days_used: 1, seat_allocations: 1 },
  sessions: [{ date: i ? "2026-11-17" : "2026-11-16", start_time: "08:30", end_time: "09:30", module_code: i ? "IT1001" : "IT2001",
    module_name: "Synthetic exam", session_number: 1, session_count: 1, candidates: 1, normal_candidates: i ? 0 : 1, repeat_candidates: i ? 1 : 0,
    rooms: [{ venue_id: "R1", venue_name: "Room one", seats: 1 }] }], workload: null, optimization: null,
  violations: [], diagnostics: [], notices: [], excluded_registrations: { note: "Test", total: 0, normal: 0, repeat: 0 } });
const pair = () => ({ batch_id: "batch", generated: true, sequential_order: [S1, S2], skipped_period_codes: [], results: [generated(0), generated(1)],
  pair_validation: { passed: true, violations: [] }, shared_candidates: 1, notices: ["Sequential, not jointly optimal."] });
function actions(response, options = {}) {
  const calls = [];
  const lib = load("src/lib/examination-generation-actions.ts", {
    "@/lib/examination-access": { requireExaminationAdmin: options.guard ?? (async () => {}) },
    "@/lib/examination-api": { examinationRequest: async (...args) => { calls.push(args); return response; } },
    "@/lib/examination": { listExamPeriods: async () => ({ ok: true, data: options.periods ?? periods }), listExamModules: async () => ({ ok: true, data: [] }) },
    "@/lib/examination-calendar": calendarLib, "@/lib/examination-settings": settings,
    "@/lib/examination-evaluation": load("src/lib/examination-evaluation.ts"),
    "@/lib/examination-protection": load("src/lib/examination-protection.ts", { "@/lib/examination-timetables": load("src/lib/examination-timetables.ts") }),
  });
  return { calls, generate: (fields) => lib.generateBothFinalTimetables({ status: "idle" }, formData(fields)),
    save: (fields) => lib.saveBothFinalTimetableDrafts({ status: "idle" }, formData(fields)) };
}

test("Both Semesters sends separate calendars and one shared preference object without saving previews", async () => {
  const a = actions({ ok: true, data: pair() });
  const answer = await a.generate();
  assert.equal(answer.status, "complete");
  assert.deepEqual(answer.result.results.map((r) => [r.inputs.semester, r.inputs.calendar.start_date]), [[1, "2026-11-16"], [2, "2026-11-17"]]);
  assert.deepEqual(answer.result.results.map((r) => r.inputs.batch.peer_period_code), [S2, S1]);
  assert.equal(a.calls.length, 1);
  assert.equal(a.calls[0][0], "/api/v1/timetables/generate/both-semesters");
  assert.equal(a.calls[0][1].timeoutMs, 720_000);
  assert.deepEqual(a.calls[0][1].body.scheduling_preferences, { scheduling: { minimum_break_minutes: 120 } });
  assert.equal(a.calls[0][1].body.include_analysis, true);
  assert.equal(a.calls[0][1].body.cycles[1].calendar.start_date, "2026-11-17");
});

test("batch inputs and roles are revalidated on the server; partial cycles and invalid preferences are rejected", async () => {
  const denied = actions({ ok: true }, { guard: async () => { throw new Error("Denied"); } });
  await assert.rejects(denied.generate(), /Denied/); assert.equal(denied.calls.length, 0);
  for (const fields of [{ cycles: "[]" }, { cycles: "broken" }, { mode: "bad" }, { time_limit_seconds: "301" }, { scheduling_preferences: '{"H1":false}' }]) {
    const a = actions({ ok: true, data: pair() }); assert.equal((await a.generate(fields)).status, "error"); assert.equal(a.calls.length, 0);
  }
  const wrongYear = actions({ ok: true, data: pair() }, { periods: [periods[0], { ...periods[1], academic_year_label: "2027/2028" }] });
  assert.equal((await wrongYear.generate()).status, "error"); assert.equal(wrongYear.calls.length, 0);
});

test("partial results and no-solution messages are retained without claiming a complete pair", async () => {
  const raw = pair(); raw.generated = false; raw.results[1] = { ...raw.results[1], generated: false, status: "INFEASIBLE", sessions: [], validation: { passed: null, rules_checked: [], violations: [] } };
  raw.pair_validation = { passed: null, violations: [] };
  const answer = await actions({ ok: true, data: raw }).generate();
  assert.equal(answer.result.generated, false); assert.equal(answer.result.results[1].status, "INFEASIBLE");
  assert.equal(answer.result.results[1].sessions.length, 0);
  raw.generated = true;
  assert.equal((await actions({ ok: true, data: raw }).generate()).status, "error");
});

test("saving requires confirmation, sends a fresh coordinated request and returns separate draft links", async () => {
  const rows = periods.map((p, i) => ({ timetable_id: i + 7, draft_number: 1, period_code: p.period_code, status: "DRAFT" }));
  const a = actions({ ok: true, data: { timetables: rows } });
  assert.equal((await a.save()).status, "error"); assert.equal(a.calls.length, 0);
  const saved = await a.save({ confirm_save: "yes" });
  assert.equal(saved.status, "saved"); assert.deepEqual(saved.timetables.map((t) => t.timetableId), [7, 8]);
  assert.equal(a.calls[0][0], "/api/v1/timetables/drafts/both-semesters");
  assert.equal(a.calls.some(([url]) => /approve|publish/.test(url)), false);
  const interrupted = actions({ ok: false, error: { status: 0, code: "TIMEOUT", message: "Too slow" } });
  assert.match((await interrupted.save({ confirm_save: "yes" })).message, /Both drafts may have been saved/);
  const stale = actions({ ok: false, error: { status: 409, code: "STALE", message: "Changed" } });
  assert.match((await stale.save({ confirm_save: "yes" })).message, /Neither draft was saved/);
});

const review = (i) => ({ period: periods[i], mapping: [{ module_code: i ? "IT1001" : "IT2001", candidate_scope: "all" }], modules: [], warnings: [],
  scheduling: { status: "valid", fingerprint: "settings" }, seating: { min_percentage: 1, max_percentage: 100, ruleset_version: 1, venue_count: 1 } });
// Institution defaults as GET /settings/scheduling returns them (built-in: 1 worker, 60 s) and a seating review, for the real settings code.
const institutionDocument = () => ({
  scheduling: { permitted_start_times: ["08:30", "13:30"], minimum_break_minutes: 60, max_rooms_per_exam: null, max_sessions_per_exam: 3,
    venue_compatibility: { written: ["lecture_hall"] }, module_venue_overrides: {} },
  solver: { time_limit_seconds: 60.0, random_seed: 20261019, num_search_workers: 1 },
  optimization: { s1_weight: 25, s2_weight: 20, s3_weight: 30, s4_weight: 15, s5_weight: 10, short_gap_minutes: 720, preparation_target_minutes: 2880,
    difficult_target_minutes: 4320, high_difficulty_minimum: 4, window_days: 3, preferred_window_exams: 2, difficulty_weight: 50, credits_weight: 25,
    duration_weight: 25, reference_difficulty: 3, reference_credits: 4, reference_duration_minutes: 120, score_scale: 1000, load_scale: 1000, baseline_budget_percent: 10 },
  fairness: { enabled: false, max_individual_increase_points: 5.0, protect_worst_student: true, max_worst_increase_points: 0.0, automatic_relaxation: false },
});
const realReview = (i) => ({ ...review(i),
  scheduling: settings.schedulingReview({ institution_id: "inst", source: "saved", status: "valid", problems: [], version: 4, format: "scheduling-settings-v1",
    fingerprint: "settings", reason: "Initial", updated_by: "user", settings: institutionDocument(), mandatory_rules: [], history: [] }),
  seating: settings.seatingReview({ min_percentage: 1, max_percentage: 100, ruleset_version: 1, venue_count: 1, total_normal_capacity: 10, total_exam_capacity: 5,
    default: { scope: "default", venue_id: null, percentage: 50, version: 1, reason: "Initial", updated_by: null, updated_at: "2026-10-01T00:00:00Z" },
    venue_overrides: [], unavailable_venues: [], history: [] }) });
test("loading both reviews rejects mismatched settings versions and preserves one review per semester", async () => {
  let second = review(1);
  const lib = load("src/lib/examination-both-actions.ts", { "@/lib/examination-access": { requireExaminationAdmin: async () => {} },
    "@/lib/examination-workspace-actions": { loadCycleReview: async (code) => ({ ok: true, data: code === S1 ? review(0) : second }) } });
  assert.equal((await lib.loadBothCycleReviews([S2, S1])).data[0].period.semester, 1);
  second = { ...second, seating: { ...second.seating, ruleset_version: 2 } };
  assert.equal((await lib.loadBothCycleReviews([S1, S2])).error.status, 409);
});

function uiHarness({ real = false } = {}) {
  let cursor = 0, work;
  const states = [], generatedCalls = [], saves = [];
  const initial = { minimum_break_minutes: "60", time_limit_seconds: "60", num_search_workers: "1", seating_percentage: "50", permitted_start_times: ["08:30", "13:30"] };
  const jsx = jsxRuntime.jsx;
  const Calendar = () => null, Settings = () => null, Results = () => null;
  const lib = load("src/app/(examination)/exams/generate/_components/both-semesters-form.tsx", {
    "react/jsx-runtime": jsxRuntime,
    react: { useState: (value) => { const i = cursor++; if (!(i in states)) states[i] = typeof value === "function" ? value() : value;
      return [states[i], (next) => { states[i] = typeof next === "function" ? next(states[i]) : next; }]; }, useEffect() {},
    useTransition: () => [false, (callback) => { work = Promise.resolve(callback()); }] },
    "next/link": { default: (props) => jsx("a", props) },
    "@/components/console/ui": { Card: ({ children }) => jsx("section", { children }), Badge: ({ children }) => jsx("span", { children }), formatDate: (d) => d },
    "@/lib/examination-calendar": calendarLib,
    "@/lib/examination-both-actions": { loadBothCycleReviews: async () => ({ ok: true, data: real ? [realReview(0), realReview(1)] : [review(0), review(1)] }) },
    "@/lib/examination-generation-actions": {
      generateBothFinalTimetables: async (_, data) => { generatedCalls.push(data); return { status: "complete", result: { ...pair(), results: [0, 1].map((i) => ({ ...generated(i), inputs: { ...generated(i).inputs, semester: i + 1 } })) } }; },
      saveBothFinalTimetableDrafts: async (_, data) => { saves.push(data); return { status: "saved", timetables: periods.map((p, i) => ({ timetableId: i + 7, periodCode: p.period_code, draftNumber: 1 })) }; },
    },
    "@/lib/examination-selection": load("src/lib/examination-selection.ts"),
    "@/lib/examination-settings": real ? settings : { settingsForm: () => ({ ...initial }), withRecommendedProfile: (f) => f, effectiveForm: (f) => f,
      settingsErrors: () => ({}), schedulingPreferences: (f) => ({ scheduling: { minimum_break_minutes: Number(f.minimum_break_minutes) } }) },
    "./academic-groups": { AcademicGroups: () => null }, "./generation-calendar": { GenerationCalendar: Calendar },
    "./generation-results": { GenerationResults: Results }, "./period-calendar": { fieldClass: "field", primaryButton: "primary", secondaryButton: "secondary" },
    "./settings-review": { SettingsReview: Settings }, "./protection-preview": { ProtectionPreview: () => null },
  });
  const render = () => { cursor = 0; return lib.BothSemestersForm({ cycles: periods }); };
  const nodes = (n) => !n || typeof n !== "object" ? [] : Array.isArray(n) ? n.flatMap(nodes) : [n, ...nodes(n.props?.children)];
  const text = (n) => n == null || typeof n === "boolean" ? "" : typeof n !== "object" ? String(n) : Array.isArray(n) ? n.map(text).join("") : text(n.props?.children);
  const find = (predicate) => { const found = nodes(render()).find(predicate); assert.ok(found); return found; };
  const button = (name) => find((n) => n.type === "button" && text(n) === name);
  return { render, nodes, text, find, button, wait: () => work, Calendar, Settings, Results, generatedCalls, saves };
}

test("wizard retains separate calendars and shared edits across steps; results/PDF inputs stay separate", async () => {
  const h = uiHarness();
  h.button("Load Both Cycles").props.onClick(); await h.wait();
  h.find((n) => n.type === h.Calendar).props.onChange(calendar("2026-11-17"));
  h.button("Semester 2").props.onClick();
  assert.equal(h.find((n) => n.type === h.Calendar).props.calendar.start_date, "2026-11-16");
  h.button("Next").props.onClick();
  let editor = h.find((n) => n.type === h.Settings);
  editor.props.onChange({ ...editor.props.form, minimum_break_minutes: "120" });
  assert.equal(h.button("Next").props.disabled, true);
  h.button("Back").props.onClick(); h.button("Next").props.onClick();
  assert.equal(h.find((n) => n.type === h.Settings).props.form.minimum_break_minutes, "120");
  h.button("Apply Preferences").props.onClick(); h.button("Next").props.onClick();
  h.button("Generate Both Timetables").props.onClick(); await h.wait();
  const submitted = h.generatedCalls[0];
  assert.equal(JSON.parse(submitted.get("cycles"))[0].calendar.start_date, "2026-11-17");
  assert.equal(JSON.parse(submitted.get("cycles"))[1].calendar.start_date, "2026-11-16");
  assert.equal(JSON.parse(submitted.get("scheduling_preferences")).scheduling.minimum_break_minutes, 120);
  assert.equal(h.find((n) => n.type === h.Results).props.result.inputs.period_code, S1);
  h.button("Semester 2 results & PDFs").props.onClick();
  assert.equal(h.find((n) => n.type === h.Results).props.result.inputs.period_code, S2);
  assert.equal(h.button("Generate again and Save Both Drafts").props.disabled, true);
  h.find((n) => n.type === "input" && n.props.type === "checkbox").props.onChange({ target: { checked: true } });
  h.button("Generate again and Save Both Drafts").props.onClick(); await h.wait();
  assert.equal(h.saves[0].get("confirm_save"), "yes");
  const links = h.nodes(h.render()).filter((n) => n.props?.href?.startsWith("/exams/timetables/")).map((n) => n.props.href);
  assert.deepEqual(links, ["/exams/timetables/7", "/exams/timetables/8"]);
});

test("Optimized sends the 4-worker / 120 s profile once as shared preferences; Feasibility keeps the institution's solver values", async () => {
  const h = uiHarness({ real: true });
  h.button("Load Both Cycles").props.onClick(); await h.wait();
  h.button("Next").props.onClick(); h.button("Next").props.onClick();
  assert.match(h.find((n) => n.type === "input" && n.props.type === "number").props.placeholder, /Use 120 seconds per semester/);
  h.button("Generate Both Timetables").props.onClick(); await h.wait();
  const optimized = h.generatedCalls[0];
  const solver = JSON.parse(optimized.get("scheduling_preferences")).solver;
  assert.deepEqual([optimized.get("mode"), solver.num_search_workers, solver.time_limit_seconds], ["optimized", 4, 120]);
  assert.equal(optimized.has("time_limit_seconds"), false, "no separate limit unless the admin types one: the profile's 120 s applies to each semester");
  assert.equal(JSON.parse(optimized.get("cycles")).length, 2, "one request carries both cycles with the same shared preferences");
  // A typed limit is sent as well; the profile stays untouched.
  h.button("Review inputs / generate again").props.onClick();
  h.find((n) => n.type === "input" && n.props.type === "number").props.onChange({ target: { value: "45" } });
  h.button("Generate Both Timetables").props.onClick(); await h.wait();
  assert.equal(h.generatedCalls[1].get("time_limit_seconds"), "45");
  assert.equal(JSON.parse(h.generatedCalls[1].get("scheduling_preferences")).solver.num_search_workers, 4);

  const f = uiHarness({ real: true });
  f.button("Load Both Cycles").props.onClick(); await f.wait();
  f.button("Next").props.onClick(); f.button("Next").props.onClick();
  f.find((n) => n.type === "select" && n.props.value === "optimized").props.onChange({ target: { value: "feasibility" } });
  f.button("Generate Both Timetables").props.onClick(); await f.wait();
  const feasibility = f.generatedCalls[0];
  const institution = JSON.parse(feasibility.get("scheduling_preferences")).solver;
  assert.deepEqual([feasibility.get("mode"), institution.num_search_workers, institution.time_limit_seconds], ["feasibility", 1, 60]);
});
