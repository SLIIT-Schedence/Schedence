import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import * as jsxRuntime from "react/jsx-runtime";
import * as React from "react";
import * as crypto from "node:crypto";

// Use the existing TypeScript dependency and Node runner; no test packages or real credentials.
function load(source, mocks, globals = {}) {
  const code = ts.transpileModule(readFileSync(path.join(import.meta.dirname, "..", source), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const testModule = { exports: {} };
  const localRequire = (name) => {
    if (name === "server-only") return {};
    assert.ok(Object.hasOwn(mocks, name), `Unexpected dependency: ${name}`);
    return mocks[name];
  };
  new Function("require", "module", "exports", "fetch", "process", "AbortSignal", code)(
    localRequire, testModule, testModule.exports, globals.fetch, { env: globals.env ?? {} }, globals.AbortSignal ?? AbortSignal,
  );
  return testModule.exports;
}

function redirect(destination) {
  throw Object.assign(new Error("Redirect"), { destination });
}

function client(options = {}) {
  const calls = [];
  const token = randomUUID();
  const api = load("src/lib/examination-api.ts", {
    "@/auth": { auth: async () => Object.hasOwn(options, "session") ? options.session : { accessToken: token } },
    "next/navigation": { redirect },
    "@/lib/examination-access": { requireExaminationAdmin: options.guard ?? (async () => {}) },
  }, {
    ...options,
    fetch: async (...args) => {
      calls.push(args);
      return options.fetch ? options.fetch(...args) : Response.json(options.body ?? [], { status: options.status ?? 200 });
    },
  });
  return { request: api.examinationRequest, calls, token };
}

test("forwards the session token only in an uncached server request and encodes queries", async () => {
  const { request, calls, token } = client();
  const result = await request("/api/v1/modules", { query: { name: "A & B", offset: 0, missing: undefined } });
  assert.deepEqual(result, { ok: true, data: [] });
  const [url, init] = calls[0];
  assert.equal(url.origin, "http://localhost:8003");
  assert.equal(url.searchParams.get("name"), "A & B");
  assert.equal(url.searchParams.get("offset"), "0");
  assert.equal(url.searchParams.has("missing"), false);
  assert.equal(init.headers.Authorization, `Bearer ${token}`);
  assert.equal(init.cache, "no-store");
  assert.equal(init.method, "GET");
  assert.equal(init.redirect, "error");
  assert.equal(JSON.stringify(result).includes(token), false);
});

test("honors the service URL and configurable request timeout", async () => {
  const timeouts = [];
  const { request, calls } = client({ env: { EXAMINATION_SERVICE_URL: "http://127.0.0.1:9003/" }, AbortSignal: { timeout: (ms) => { timeouts.push(ms); return AbortSignal.timeout(ms); } } });
  await request("/api/v1/modules");
  await request("/api/v1/modules", { timeoutMs: 120_000 });
  assert.equal(calls[0][0].origin, "http://127.0.0.1:9003");
  assert.deepEqual(timeouts, [15_000, 120_000]);
});

test("denies access before fetching when the role guard rejects", async () => {
  const { request, calls } = client({ guard: async () => redirect("/") });
  await assert.rejects(request("/api/v1/modules"), { destination: "/" });
  assert.equal(calls.length, 0);
});

test("missing, expired and backend-rejected sessions redirect without swallowing framework errors", async () => {
  for (const options of [{ session: null }, { session: { error: "RefreshTokenError" } }, { status: 401, body: { detail: "Expired" } }]) {
    const { request } = client(options);
    await assert.rejects(request("/api/v1/modules"), { destination: "/login?error=SessionExpired&callbackUrl=%2Fexams" });
  }
});

test("parses FastAPI permission and database error messages", async () => {
  for (const status of [403, 503]) {
    const { request } = client({ status, body: { detail: "Service-provided error" } });
    const result = await request("/api/v1/modules");
    assert.deepEqual(result, { ok: false, error: { status, code: `HTTP_${status}`, message: "Service-provided error" } });
  }
});

test("parses validation arrays without retaining rejected input values", async () => {
  const rejectedInput = randomUUID();
  const { request } = client({ status: 422, body: { detail: [{ loc: ["query", "limit"], msg: "Must be positive", input: rejectedInput }] } });
  const result = await request("/api/v1/modules");
  assert.equal(result.error.code, "VALIDATION_ERROR");
  assert.equal(result.error.message, "query.limit: Must be positive");
  assert.equal(JSON.stringify(result).includes(rejectedInput), false);
});

test("parses domain problem arrays and structured detail envelopes", async () => {
  const problems = client({ status: 409, body: { detail: [{ rule: "REVISION", message: "Revision changed" }] } });
  assert.equal((await problems.request("/api/v1/modules")).error.message, "Revision changed");
  const structured = client({ status: 409, body: { detail: { code: "CONFLICT", message: "Changed", details: { revision: 2 } } } });
  assert.deepEqual((await structured.request("/api/v1/modules")).error, { status: 409, code: "CONFLICT", message: "Changed", details: { revision: 2 } });
});

test("period reason validation becomes friendly field errors without regex or rejected input", async () => {
  const rejectedInput = randomUUID();
  const pattern = "private-pattern-example";
  for (const type of ["string_pattern_mismatch", "string_too_short", "string_too_long"]) {
    const h = client({ status: 422, body: { detail: [{ type, loc: ["body", "exclusions", 0, "reason"],
      msg: `String should match pattern '${pattern}'`, ctx: { pattern }, input: rejectedInput }] } });
    const result = await h.request("/api/v1/exam-periods/FINAL-CYCLE", { method: "PUT", query: { dry_run: true } });
    assert.equal(result.error.status, 422);
    assert.match(result.error.message, /Excluded date 1 reason:/);
    assert.ok(result.error.fieldErrors["exclusions.0.reason"]);
    assert.equal(JSON.stringify(result).includes(pattern), false);
    assert.equal(JSON.stringify(result).includes(rejectedInput), false);
  }
  const h = client({ status: 422, body: { detail: [{ type: "string_pattern_mismatch", loc: ["body", "period_name"], msg: `String should match pattern '${pattern}'` }] } });
  assert.equal((await h.request("/api/v1/exam-periods/FINAL-CYCLE")).error.fieldErrors.period_name, "Use the required format for this field.");
});

test("handles non-JSON errors and malformed successful responses", async () => {
  for (const status of [200, 502]) {
    const { request } = client({ fetch: async () => new Response("<html>proxy error</html>", { status }) });
    const result = await request("/api/v1/modules");
    assert.equal(result.ok, false);
    assert.equal(result.error.code, status === 200 ? "INVALID_RESPONSE" : "HTTP_502");
    assert.equal(result.error.message.includes("<html>"), false);
  }
});

test("returns network and body-read failures without leaking exception details", async () => {
  for (const fetch of [async () => { throw new Error("sensitive transport details"); }, async () => ({ text: async () => { throw new Error("body failure"); } })]) {
    const { request } = client({ fetch });
    assert.equal((await request("/api/v1/modules")).error.code, "UNREACHABLE");
  }
});

test("distinguishes a timeout from a network failure", async () => {
  const { request } = client({ AbortSignal: { timeout: () => AbortSignal.abort() }, fetch: async () => { throw new Error("aborted"); } });
  assert.equal((await request("/api/v1/modules", { timeoutMs: 1 })).error.code, "TIMEOUT");
});

test("rejects invalid service configuration, paths and timeouts before fetching", async () => {
  for (const options of [{ env: { EXAMINATION_SERVICE_URL: "invalid" } }, { env: { EXAMINATION_SERVICE_URL: "file:///tmp" } }]) {
    const { request, calls } = client(options);
    assert.equal((await request("/api/v1/modules")).error.code, "CONFIGURATION_ERROR");
    assert.equal(calls.length, 0);
  }
  for (const [path, timeoutMs] of [["https://elsewhere.example", 100], ["/api/v1/../modules", 100], ["/api/v1/modules", 0], ["/api/v1/modules", 2 ** 32]]) {
    const { request, calls } = client();
    assert.equal((await request(path, { timeoutMs })).error.code, "CONFIGURATION_ERROR");
    assert.equal(calls.length, 0);
  }
});

test("Examination Admin guard excludes other roles and disabled accounts or institutions", async () => {
  const account = { accountStatus: "ACTIVE", roles: [{ roleCode: "EXAMINATION_ADMIN", institutionId: "one" }], institutions: [{ id: "one", status: "ACTIVE" }] };
  let result = { status: "ok", account };
  const access = load("src/lib/examination-access.ts", {
    react: { cache: (fn) => fn },
    "next/navigation": { redirect },
    "@/lib/identity": { getAccount: async () => result },
  });
  assert.equal(access.isExaminationAdmin(account), true);
  assert.equal(await access.requireExaminationAdmin(), account);
  for (const invalid of [
    { ...account, roles: [] },
    { ...account, roles: [{ roleCode: "SYSTEM_ADMIN", institutionId: "one" }] },
    { ...account, roles: [{ roleCode: "INSTITUTION_ADMIN", institutionId: "one" }] },
    { ...account, accountStatus: "DISABLED" },
    { ...account, institutions: [{ id: "one", status: "DISABLED" }] },
  ]) {
    assert.equal(access.isExaminationAdmin(invalid), false);
    result = { status: "ok", account: invalid };
    await assert.rejects(access.requireExaminationAdmin(), { destination: "/" });
  }
  result = { status: "unavailable" };
  await assert.rejects(access.requireExaminationAdmin(), { destination: "/" });
});

test("generation POST serializes JSON with the same secure headers and extended timeout", async () => {
  const timeouts = [];
  const { request, calls } = client({ AbortSignal: { timeout: (ms) => { timeouts.push(ms); return AbortSignal.timeout(ms); } } });
  const body = { period_code: "PP1-2026-S2-FINAL", mode: "optimized", time_limit_seconds: 300 };
  await request("/api/v1/timetables/generate", { method: "POST", body, timeoutMs: 360_000 });
  assert.equal(calls[0][1].method, "POST");
  assert.equal(calls[0][1].headers["Content-Type"], "application/json");
  assert.deepEqual(JSON.parse(calls[0][1].body), body);
  assert.deepEqual(timeouts, [360_000]);
  assert.equal(calls[0][1].cache, "no-store");
});

test("an expired generation session returns to the generation page after existing login", async () => {
  const sessionExpiredPath = "/login?error=SessionExpired&callbackUrl=%2Fexams%2Fgenerate";
  const { request } = client({ status: 401 });
  await assert.rejects(request("/api/v1/timetables/generate", { method: "POST", body: {}, sessionExpiredPath }), { destination: sessionExpiredPath });
});

// Unit fixtures exercise the client boundary; application pages always use real API responses.
function generationFixture() {
  return {
    status: "FEASIBLE", generated: true,
    solver: { mode: "feasibility", time_limit_seconds: 60, wall_time_seconds: 2 },
    inputs: { period_code: "FINAL-CYCLE", period_name: "Final cycle", modules: 1, students: 10 },
    validation: { passed: true, rules_checked: ["H1"], violations: [] },
    summary: { sessions: 1, exam_days_used: 1, seat_allocations: 10 },
    sessions: [{ date: "2026-11-16", start_time: "08:30:00", end_time: "10:30:00", module_code: "IT1001", module_name: "Test module", session_number: 1, session_count: 1, candidates: 10, rooms: [{ venue_id: "A304", venue_name: "A304", seats: 10 }] }],
    workload: { total_weighted_penalty: 2, penalties: [{ constraint: "S1", weight: 25, penalty_points: 8, weighted_points: 2 }] },
    optimization: null, violations: [], diagnostics: [], notices: [],
    excluded_registrations: { note: "Excluded from cycle", total: 0, normal: 0, repeat: 0 },
  };
}

function generationAction(options = {}) {
  const calls = [];
  let reads = 0;
  const actions = load("src/lib/examination-generation-actions.ts", {
    "@/lib/examination-evaluation": load("src/lib/examination-evaluation.ts", {}),
    "@/lib/examination-settings": load("src/lib/examination-settings.ts", {}),
    "@/lib/examination-calendar": load("src/lib/examination-calendar.ts", {}),
    "@/lib/examination-access": { requireExaminationAdmin: options.guard ?? (async () => {}) },
    "@/lib/examination": { listExamModules: async () => ({ ok: true, data: [{ module_code: "IT1001", specializations: ["IT", "SE"] }] }), listExamPeriods: async () => {
      reads++;
      return options.periodResult ?? { ok: true, data: [{ period_code: "FINAL-CYCLE", exam_kind: "FINAL" }, { period_code: "MID-CYCLE", exam_kind: "MID" }] };
    } },
    "@/lib/examination-api": { examinationRequest: async (...args) => {
      calls.push(args);
      return options.response ?? { ok: true, data: generationFixture() };
    } },
  });
  const submit = (fields = {}) => {
    const data = new FormData();
    for (const [key, value] of Object.entries({ period_code: "FINAL-CYCLE", mode: "feasibility", ...fields })) data.set(key, value);
    return actions.generateFinalTimetable({ status: "idle" }, data);
  };
  return { submit, calls, readCount: () => reads };
}

test("FINAL generation submits the full cycle without module overrides or persistence calls", async () => {
  const { submit, calls, readCount } = generationAction();
  const result = await submit();
  assert.equal(result.status, "complete");
  assert.equal(readCount(), 1);
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], "/api/v1/timetables/generate");
  assert.equal(calls[0][1].timeoutMs, 360_000);
  assert.equal(calls[0][1].method, "POST");
  assert.deepEqual(calls[0][1].body, { period_code: "FINAL-CYCLE", mode: "feasibility", include_analysis: true });
});

test("the recommended solver profile reaches the generation request as per-timetable preferences only", async () => {
  const lib = load("src/lib/examination-settings.ts", {});
  const review = (await periodHarness().loadCycleReview("FINAL-CYCLE")).data;
  const institution = lib.settingsForm(review.scheduling, review.seating);
  const preferences = lib.schedulingPreferences(lib.withRecommendedProfile(institution));
  const { submit, calls } = generationAction();
  assert.equal((await submit({ mode: "optimized", scheduling_preferences: JSON.stringify(preferences) })).status, "complete");
  const [path, init] = calls[0];
  assert.equal(path, "/api/v1/timetables/generate");
  const sent = init.body.scheduling_preferences;
  assert.deepEqual([init.body.mode, sent.solver.num_search_workers, sent.solver.time_limit_seconds], ["optimized", 4, 120]);
  // S1-S5 and every other preference equal the institution's values; only the two solver numbers differ.
  assert.deepEqual({ ...sent, solver: null }, { ...lib.schedulingPreferences(institution), solver: null });
  assert.equal(init.body.time_limit_seconds, undefined, "no request-level override unless the admin shortens the run");
  assert.ok(calls.every(([target, request]) => target === "/api/v1/timetables/generate" && request.method === "POST"), "no institution settings are written");
});

test("export metadata uses the trusted period and server completion time without changing the generation contract", async () => {
  const { submit, calls } = generationAction({ periodResult: { ok: true, data: [{ period_code: "FINAL-CYCLE", exam_kind: "FINAL", academic_year_label: "2026/2027", semester: 2 }] } });
  const before = Date.now();
  const result = (await submit({ academic_year_label: "Forged label", semester: "1" })).result;
  assert.equal(result.inputs.academic_year_label, "2026/2027");
  assert.equal(result.inputs.semester, 2);
  assert.equal(result.inputs.exam_kind, "FINAL");
  assert.ok(Date.parse(result.generated_at) >= before && Date.parse(result.generated_at) <= Date.now());
  assert.deepEqual(calls[0][1].body, { period_code: "FINAL-CYCLE", mode: "feasibility", include_analysis: true });
});

test("the action enforces roles and rejects forged MID, unknown cycles, invalid modes and time limits", async () => {
  const denied = generationAction({ guard: async () => redirect("/") });
  await assert.rejects(denied.submit(), { destination: "/" });
  assert.equal(denied.readCount(), 0);
  for (const fields of [{ period_code: "MID-CYCLE" }, { period_code: "MISSING" }, { mode: "other" }, ...["NaN", "Infinity", "0", "-1", "300.01"].map((time_limit_seconds) => ({ time_limit_seconds }))]) {
    const action = generationAction();
    assert.equal((await action.submit(fields)).status, "error");
    assert.equal(action.calls.length, 0);
  }
  const action = generationAction();
  await action.submit({ mode: "optimized", time_limit_seconds: "300" });
  assert.equal(action.calls[0][1].body.time_limit_seconds, 300);
  assert.equal(action.calls[0][1].body.mode, "optimized");
});

test("generation errors preserve HTTP statuses and service messages without student identifiers", async () => {
  for (const status of [401, 403, 409, 422, 429, 503]) {
    const { submit } = generationAction({ response: { ok: false, error: { status, code: `HTTP_${status}`, message: "Problem for student IT33123456" } } });
    const state = await submit();
    assert.equal(state.status, "error");
    assert.equal(state.httpStatus, status);
    assert.ok(state.title.length > 0);
    assert.equal(state.message.includes("IT33123456"), false);
    assert.ok(state.message.includes("Problem for student"));
  }
  const unreachable = generationAction({ response: { ok: false, error: { status: 0, code: "TIMEOUT", message: "Timed out" } } });
  assert.match((await unreachable.submit()).message, /solver may still be running/);
  const unavailablePeriods = generationAction({ periodResult: { ok: false, error: { status: 503, code: "HTTP_503", message: "Unavailable" } } });
  assert.equal((await unavailablePeriods.submit()).httpStatus, 503);
  assert.equal(unavailablePeriods.calls.length, 0);
});

test("action results pick public fields and remove individual records and identifiers from prose", async () => {
  const raw = generationFixture();
  raw.student_id = "IT33123456";
  raw.sessions[0].student_id = "IT33123456";
  raw.sessions[0].rooms[0].individual_seats = [{ student_id: "IT33123456" }];
  raw.workload.student_penalties = [{ student_id: "IT33123456", penalty: 4 }];
  raw.diagnostics = ["Student IT33123456 was mentioned in validation", "diagnostic_IT33123456_session"];
  const { submit } = generationAction({ response: { ok: true, data: raw } });
  const state = await submit();
  assert.equal(state.status, "complete");
  const serialized = JSON.stringify(state);
  assert.equal(serialized.includes("IT33123456"), false);
  assert.equal(serialized.includes('"student_id"'), false);
  assert.equal(serialized.includes("individual_seats"), false);
  assert.equal(state.result.sessions[0].candidates, 10);
  assert.deepEqual(state.result.workload.penalties, raw.workload.penalties);
});

test("generation forwards every existing aggregate workload metric and optional evaluation analysis", async () => {
  const raw = JSON.parse(readFileSync(new URL("./fixtures/examination-evaluation.json", import.meta.url), "utf8"));
  raw.workload.student_penalties = [{ student_id: "IT33123456" }];
  raw.analysis.selected.statistics.student_id = "IT33123456";
  const action = generationAction({ response: { ok: true, data: raw } });
  const result = (await action.submit({ mode: "optimized" })).result;
  const expected = { ...raw.workload };
  delete expected.student_penalties;
  assert.deepEqual(result.workload, expected);
  assert.deepEqual(result.optimization.baseline, raw.optimization.baseline);
  assert.equal(result.analysis.selected.statistics.mean_gap_minutes, raw.analysis.selected.statistics.mean_gap_minutes);
  assert.equal(JSON.stringify(result).includes("IT33123456"), false);
  assert.equal(action.calls[0][1].body.include_analysis, true);
});

test("no-solution and failed-validation results never expose timetable rows", async () => {
  for (const status of ["UNKNOWN", "INFEASIBLE", "MODEL_INVALID"]) {
    const raw = generationFixture();
    raw.status = status;
    raw.generated = false;
    raw.validation.passed = null;
    raw.workload = null;
    const { submit } = generationAction({ response: { ok: true, data: raw } });
    const result = await submit();
    assert.equal(result.status, "complete");
    assert.equal(result.result.status, status);
    assert.deepEqual(result.result.sessions, []);
  }
  const raw = generationFixture();
  raw.validation.passed = false;
  const invalid = generationAction({ response: { ok: true, data: raw } });
  assert.deepEqual((await invalid.submit()).result.sessions, []);
  const malformed = generationAction({ response: { ok: true, data: {} } });
  assert.equal((await malformed.submit()).status, "error");
});

test("optimized results retain actual baseline, selected and aggregate fairness values", async () => {
  const raw = generationFixture();
  raw.solver.mode = "optimized";
  raw.optimization = {
    status: "FEASIBLE", improved: true, baseline: raw.workload,
    selected: { total_weighted_penalty: 1, penalties: [{ constraint: "S1", weight: 25, penalty_points: 4, weighted_points: 1 }] },
    objective_bound_units: null, relative_gap: null,
    fairness: { protection_applied: false, limits_satisfied: null, students: 10, students_improved: 8, students_worsened: 1, students_unchanged: 1, baseline_worst_normalized_penalty: 5, selected_worst_normalized_penalty: 4, maximum_individual_increase_points: 2, worst_student_increase_points: 1 },
  };
  const { submit } = generationAction({ response: { ok: true, data: raw } });
  const result = await submit({ mode: "optimized" });
  assert.deepEqual(result.result.optimization, raw.optimization);
});

function renderModules() {
  const icons = load("src/components/icon.tsx", { "react/jsx-runtime": jsxRuntime });
  const ui = load("src/components/console/ui.tsx", {
    "react/jsx-runtime": jsxRuntime,
    "next/link": { default: (props) => createElement("a", props) },
    "@/components/icon": icons,
  });
  const presentation = load("src/lib/examination-presentation.ts", {});
  const calendar = load("src/lib/examination-calendar.ts", {});
  const common = { "react/jsx-runtime": jsxRuntime, react: React, "@/components/console/ui": ui, "@/lib/examination-presentation": presentation, "@/lib/examination-calendar": calendar };
  const styles = { fieldClass: "field", primaryButton: "primary", secondaryButton: "secondary" };
  const moduleBrowser = load("src/lib/examination-module-browser.ts", {});
  const groups = load("src/app/(examination)/exams/generate/_components/academic-groups.tsx", {
    ...common, "./period-calendar": styles, "@/components/icon": icons, "@/lib/examination-module-browser": moduleBrowser,
  });
  const settingsLib = load("src/lib/examination-settings.ts", {});
  const settings = load("src/app/(examination)/exams/generate/_components/settings-review.tsx", {
    ...common, "./period-calendar": styles, "@/lib/examination-settings": settingsLib,
    "@/lib/examination-settings-actions": { previewSettingsChange: () => { throw new Error("No settings request on render"); }, saveSettingsChange: () => { throw new Error("No settings request on render"); } },
  });
  const browser = load("src/app/(examination)/exams/generate/_components/timetable-browser.tsx", { ...common, "./academic-groups": groups, "./period-calendar": styles });
  const evaluationLib = load("src/lib/examination-evaluation.ts", {});
  const resultsLib = load("src/lib/examination-results.ts", { "./examination-evaluation": evaluationLib });
  const evaluation = load("src/app/(examination)/exams/generate/_components/evaluation-dashboard.tsx", { ...common,
    "./period-calendar": styles, "@/lib/examination-evaluation": evaluationLib });
  const preferencesSummary = load("src/app/(examination)/exams/generate/_components/preferences-summary.tsx", { ...common, "@/lib/examination-settings": settingsLib });
  const panels = load("src/app/(examination)/exams/generate/_components/result-panels.tsx", { ...common, "./period-calendar": styles,
    "@/lib/examination-evaluation": evaluationLib, "@/lib/examination-results": resultsLib, "./evaluation-dashboard": evaluation, "./preferences-summary": preferencesSummary });
  const results = load("src/app/(examination)/exams/generate/_components/generation-results.tsx", { ...common, "./period-calendar": styles,
    "@/lib/examination-evaluation": evaluationLib, "@/lib/examination-results": resultsLib, "./result-panels": panels, "./timetable-browser": browser });
  const link = { default: (props) => createElement("a", props) };
  const saveDraft = load("src/app/(examination)/exams/generate/_components/save-draft.tsx", {
    "react/jsx-runtime": jsxRuntime, "next/link": link, "@/components/console/ui": ui, "@/components/icon": icons, "./period-calendar": styles,
  });
  const selection = load("src/lib/examination-selection.ts", {});
  const examSelection = load("src/app/(examination)/exams/generate/_components/exam-selection.tsx", {
    "react/jsx-runtime": jsxRuntime, "@/components/console/ui": ui, "@/components/icon": icons, "@/lib/examination-selection": selection,
    "./period-calendar": styles,
  });
  // The wizard navigates to a saved draft with useRouter; tests never navigate.
  const navigation = { useRouter: () => ({ push: () => {} }) };
  const generationCalendar = load("src/app/(examination)/exams/generate/_components/generation-calendar.tsx", {
    ...common, "./period-calendar": { ...styles, CalendarGrid: () => null },
  });
  return { ...common, ...results, ...panels, ...evaluation, ...preferencesSummary, ...groups, ...settings, ...browser, icons, "@/lib/examination-settings": settingsLib, "@/lib/examination-results": resultsLib, "@/lib/examination-evaluation": evaluationLib, "next/navigation": navigation, "./save-draft": saveDraft,
    "@/lib/examination-selection": selection, "./exam-selection": examSelection, "./generation-calendar": generationCalendar };
}

// One real-shaped period option (the server builds these from /exam-periods and each /modules mapping).
function examOption(changes = {}) {
  return { period_code: "FINAL-CYCLE", period_name: "Final cycle", academic_year_label: "2030/2031", semester: 2, exam_kind: "FINAL",
    start_date: "2026-11-16", end_date: "2026-11-28", exam_days: 9, module_count: 42, registration_count: 1700, ...changes };
}

function resultsView(result) {
  const { GenerationResults } = renderModules();
  return renderToStaticMarkup(createElement(GenerationResults, { result }));
}

test("the results view renders returned session, venue, candidate and workload values", () => {
  const markup = resultsView(generationFixture());
  for (const text of ["IT1001", "Test module", "A304", "16 Nov 2026", "08:30", "10:30", "FEASIBLE", "Passed", "S1", "S5", "UNSAVED", "Download PDF"]) {
    assert.ok(markup.includes(text), `Missing ${text}`);
  }
  assert.match(markup, /10 candidates/);
  assert.equal(markup.includes("student_id"), false);
  assert.equal(markup.includes("IT33123456"), false);
});

test("generation sends only validated timetable preferences and never writes global settings", async () => {
  const preferences = { scheduling: { permitted_start_times: ["09:00", "13:30"], minimum_break_minutes: 90 },
    solver: { time_limit_seconds: 120 }, seating: { default_percentage: 60 }, fairness: { enabled: true } };
  const action = generationAction();
  const result = await action.submit({ scheduling_preferences: JSON.stringify(preferences) });
  assert.equal(result.status, "complete");
  assert.deepEqual(action.calls[0][1].body.scheduling_preferences, preferences);
  assert.ok(action.calls.every(([path, init]) => path === "/api/v1/timetables/generate" && init.method === "POST"));
  for (const invalid of ["broken json", "[]", "null", '{"H1":false}', '{"seating":{"default_percentage":101}}',
    '{"solver":{"time_limit_seconds":301}}', '{"scheduling":{"venue_compatibility":{}}}', '{"fairness":{"automatic_relaxation":true}}']) {
    const refused = generationAction();
    assert.equal((await refused.submit({ scheduling_preferences: invalid })).status, "error");
    assert.equal(refused.calls.length, 0);
  }
});

test("Advanced Settings validate locally without a global-save interface", async () => {
  const modules = renderModules();
  const review = (await periodHarness().loadCycleReview("FINAL-CYCLE")).data;
  const lib = modules["@/lib/examination-settings"];
  const original = lib.settingsForm(review.scheduling, review.seating);
  const props = { review, form: original, applied: original, onChange: (form) => { props.form = form; },
    onApply: () => { props.applied = props.form; }, onReset: () => { props.form = original; props.applied = original; } };
  const h = componentHarness("src/app/(examination)/exams/generate/_components/settings-review.tsx", {
    "@/lib/examination-settings": lib, "./period-calendar": { fieldClass: "field", primaryButton: "primary", secondaryButton: "secondary" },
  }, props, "SettingsReview");
  assert.match(h.html(), /This timetable only/);
  assert.equal(h.html().includes("Save changes"), false);
  assert.equal(h.html().includes("Reason for the change"), false);
  const input = () => h.find((node) => node.type === "input" && node.props.id === "setting-minimum_break_minutes");
  input().props.onChange({ target: { value: "-1" } });
  assert.equal(input().props["aria-invalid"], true);
  input().props.onChange({ target: { value: "90" } });
  assert.equal(input().props["aria-invalid"], false);
  assert.equal(props.form.minimum_break_minutes, "90");
  assert.equal(props.applied.minimum_break_minutes, "60");
});

// Five-step wizard: Select Exam -> Choose Dates -> Review Modules -> Generate -> Results.
async function wizardHarness() {
  const modules = renderModules();
  const review = (await periodHarness().loadCycleReview("FINAL-CYCLE")).data;
  const submitted = [];
  const saved = [];
  const h = componentHarness("src/app/(examination)/exams/generate/_components/generation-form.tsx", {
    "@/components/icon": modules.icons,
    "@/lib/examination-generation-actions": {
      generateFinalTimetable: async (_state, data) => { submitted.push(Object.fromEntries(data)); return { status: "complete", result: generationFixture() }; },
      saveFinalTimetableDraft: async (_state, data) => { saved.push(Object.fromEntries(data)); return { status: "idle" }; },
    },
    "@/lib/examination-workspace-actions": { loadCycleReview: async () => ({ ok: true, data: review }) },
    "./generation-results": modules, "./period-calendar": { fieldClass: "field", primaryButton: "primary", secondaryButton: "secondary" },
    "./academic-groups": modules, "./settings-review": modules,
  }, { cycles: [examOption()] }, "GenerationForm");
  h.button("Continue").props.onClick(); await h.wait();
  h.button("Next").props.onClick(); h.button("Next").props.onClick(); // Choose Dates -> Review Modules -> Generate
  const lib = modules["@/lib/examination-settings"];
  // The institution's own settings, as they would be sent without any profile.
  const institution = lib.schedulingPreferences(lib.settingsForm(review.scheduling, review.seating));
  const find = h.find;
  return {
    ...h, review, submitted, saved, institution,
    prefs: () => JSON.parse(find((node) => node.type === "input" && node.props.name === "scheduling_preferences").props.value),
    advanced: () => find((node) => node.type === "details" && node.props.id === "advanced-settings"),
    editor: () => find((node) => node.props?.form && node.props.onChange),
    radio: (value) => find((node) => node.type === "input" && node.props.name === "mode" && node.props.value === value),
    limit: () => find((node) => node.type === "input" && node.props.name === "time_limit_seconds"),
    generateButton: () => h.button("Generate Timetable"),
    saveCard: () => find((node) => typeof node.props?.onSave === "function"),
    toggleAdvanced(open) { const target = { open }; find((node) => node.type === "details" && node.props.id === "advanced-settings").props.onToggle({ target, currentTarget: target }); },
    // What the browser would submit: hidden inputs, the shortened-run input and the checked mode.
    formEntries() {
      const data = new FormData();
      for (const node of h.all()) {
        if (node.type !== "input" || typeof node.props.name !== "string") continue;
        if (node.props.type === "radio") { if (node.props.checked) data.set(node.props.name, node.props.value); } else data.set(node.props.name, String(node.props.value ?? ""));
      }
      return data;
    },
  };
}

test("Optimized is selected by default and the recommended profile is applied without opening Advanced Settings", async () => {
  const w = await wizardHarness();
  assert.equal(w.radio("optimized").props.checked, true);
  assert.equal(w.radio("feasibility").props.checked, false);
  assert.equal(w.advanced().props.open, false, "Advanced Settings start collapsed");
  // Only the two solver values differ from the institution settings; S1-S5, fairness and every other value are unchanged.
  const sent = w.prefs();
  assert.deepEqual(sent.solver, { ...w.institution.solver, num_search_workers: 4, time_limit_seconds: 120 });
  assert.deepEqual({ ...sent, solver: null }, { ...w.institution, solver: null });
  assert.deepEqual([w.institution.solver.num_search_workers, w.institution.solver.time_limit_seconds], [1, 60], "institution defaults are not changed");
  const html = w.html();
  for (const text of ["Generation summary", "Dates", "Modules", "mapped modules", "exam days", "Optimized", "4 search workers · 120-second limit (recommended profile)",
    "H1–H11 always apply", "Advanced Settings (Optional)", "Recommended profile"]) assert.ok(html.includes(text), text);
  assert.equal(w.generateButton().props.disabled, false);
  w.find((node) => node.type === "form").props.action(w.formEntries()); await w.wait();
  assert.equal(w.submitted.length, 1);
  assert.equal(w.submitted[0].mode, "optimized");
  assert.deepEqual(JSON.parse(w.submitted[0].scheduling_preferences), sent);
  // Save Draft keeps working and saves with the same profile.
  assert.equal(w.saveCard().props.timeLimit, "120");
  assert.equal(typeof w.find((node) => node.props?.result).props.children.props.onSave, "function", "Save Draft opens inside the results' action area");
  assert.equal(w.all().filter((node) => typeof node.props?.onSave === "function").length, 1, "one Save Draft card, not repeated at the end of the page");
  w.saveCard().props.onConfirm(true); w.saveCard().props.onSave(); await w.wait();
  assert.equal(w.saved[0].mode, "optimized");
  assert.deepEqual(JSON.parse(w.saved[0].scheduling_preferences), sent);
});

test("Advanced Settings override the profile only after Apply, are validated, and Reset restores the recommended profile", async () => {
  const w = await wizardHarness();
  w.toggleAdvanced(true);
  assert.equal(w.advanced().props.open, true);
  const base = w.editor().props.form;
  assert.deepEqual([base.num_search_workers, base.time_limit_seconds], ["4", "120"], "the editor shows the profile in use");
  w.editor().props.onChange({ ...base, num_search_workers: "8", s1_weight: "37", minimum_break_minutes: "90", seating_percentage: "60" });
  assert.equal(w.generateButton().props.disabled, true, "unapplied edits never generate");
  assert.match(w.html(), /Unapplied changes\. Apply Preferences before generating\./);
  assert.equal(w.prefs().solver.num_search_workers, 4, "the sent preferences stay on the profile until Apply");
  // Edits survive leaving the Generate step, with a reminder.
  w.button("Back").props.onClick();
  assert.match(w.html(), /You have unapplied Advanced Settings/);
  w.button("Next").props.onClick();
  assert.equal(w.editor().props.form.s1_weight, "37");
  w.button("Apply Preferences").props.onClick();
  assert.equal(w.generateButton().props.disabled, false);
  const custom = w.prefs();
  assert.deepEqual([custom.solver.num_search_workers, custom.solver.time_limit_seconds], [8, 120]);
  assert.deepEqual([custom.optimization.s1_weight, custom.scheduling.minimum_break_minutes, custom.seating.default_percentage], [37, 90, 60]);
  assert.equal(custom.optimization.s2_weight, w.institution.optimization.s2_weight, "untouched S1-S5 weights are unchanged");
  assert.ok(w.html().includes("8 search workers · 120-second limit (custom)"));
  // Invalid values are reported, cannot be applied, and keep the section open.
  w.editor().props.onChange({ ...w.editor().props.form, time_limit_seconds: "301" });
  assert.equal(w.button("Apply Preferences").props.disabled, true);
  assert.match(w.html(), /Check 1 preference field before applying\./);
  w.toggleAdvanced(false);
  assert.equal(w.advanced().props.open, true, "a section with errors stays open");
  w.button("Reset to Defaults").props.onClick();
  assert.equal(w.editor().props.form.num_search_workers, "4");
  assert.deepEqual(w.prefs().solver, { ...w.institution.solver, num_search_workers: 4, time_limit_seconds: 120 });
  assert.equal(w.prefs().optimization.s1_weight, w.institution.optimization.s1_weight);
  assert.equal(w.generateButton().props.disabled, false);
  // Pressing Enter in an Advanced Settings field must not start a generation.
  const form = () => w.find((node) => node.type === "form");
  let prevented = 0;
  const key = (event) => form().props.onKeyDown({ preventDefault: () => prevented++, ...event });
  key({ key: "Enter", target: { tagName: "INPUT" } }); key({ key: "Enter", target: { tagName: "BUTTON" } }); key({ key: "a", target: { tagName: "INPUT" } });
  assert.equal(prevented, 1);
});

test("Feasibility keeps the institution solver values unless the admin changed them, and a shortened run respects the limit in use", async () => {
  const w = await wizardHarness();
  w.radio("feasibility").props.onChange();
  assert.deepEqual(w.prefs().solver, w.institution.solver, "the profile belongs to Optimized mode");
  assert.ok(w.html().includes("1 search worker · 60-second limit (institution settings)"));
  w.radio("optimized").props.onChange();
  assert.equal(w.prefs().solver.num_search_workers, 4);
  // A value the admin applied is kept in every mode.
  w.editor().props.onChange({ ...w.editor().props.form, num_search_workers: "8" });
  w.button("Apply Preferences").props.onClick();
  w.radio("feasibility").props.onChange();
  assert.deepEqual([w.prefs().solver.num_search_workers, w.prefs().solver.time_limit_seconds], [8, 120]);
  w.radio("optimized").props.onChange();
  // Shorten this run: never longer than the limit in use.
  w.limit().props.onChange({ target: { value: "200" } });
  assert.equal(w.limit().props["aria-invalid"], true);
  assert.equal(w.generateButton().props.disabled, true);
  assert.equal(w.advanced().props.open, true, "the invalid field is revealed");
  assert.match(w.html(), /Enter a time above 0 and no more than 120 seconds\./);
  w.limit().props.onChange({ target: { value: "90" } });
  assert.equal(w.generateButton().props.disabled, false);
  w.radio("feasibility").props.onChange();
  assert.equal(w.limit().props.value, "", "changing the mode clears a shortened run");
  w.radio("optimized").props.onChange();
  w.limit().props.onChange({ target: { value: "90" } });
  w.find((node) => node.type === "form").props.action(w.formEntries()); await w.wait();
  assert.equal(w.submitted[0].time_limit_seconds, "90");
  assert.equal(w.submitted[0].mode, "optimized");
  assert.equal(w.saveCard().props.timeLimit, "90");
  w.saveCard().props.onConfirm(true); w.saveCard().props.onSave(); await w.wait();
  assert.equal(w.saved[0].time_limit_seconds, "90");
  assert.equal(JSON.parse(w.saved[0].scheduling_preferences).solver.num_search_workers, 8);
});

async function preferenceHarness(changeReview = () => {}) {
  const modules = renderModules();
  const review = structuredClone((await periodHarness().loadCycleReview("FINAL-CYCLE")).data);
  changeReview(review);
  const lib = modules["@/lib/examination-settings"];
  const props = { review, form: lib.settingsForm(review.scheduling, review.seating), onChange: (form) => { props.form = form; } };
  const h = componentHarness("src/app/(examination)/exams/generate/_components/settings-review.tsx", {
    "@/lib/examination-settings": lib, "./period-calendar": { fieldClass: "field", primaryButton: "primary", secondaryButton: "secondary" },
  }, props, "SettingsReview");
  const input = (id) => h.find((node) => node.type === "input" && node.props.id === id);
  const tab = (id) => h.find((node) => node.props?.role === "tab" && node.props.id === `preference-tab-${id}`);
  const panel = (id) => h.find((node) => node.props?.role === "tabpanel" && node.props.id === `preference-panel-${id}`);
  return { ...h, props, lib, input, tab, panel };
}

test("preference tabs have accessible relationships, keyboard navigation and persistent values", async () => {
  const h = await preferenceHarness();
  assert.equal(h.panel("basic").props.hidden, false);
  assert.equal(h.panel("workload").props.hidden, true);
  h.input("setting-minimum_break_minutes").props.onChange({ target: { value: "90" } });
  h.tab("workload").props.onClick();
  h.input("setting-s1_weight").props.onChange({ target: { value: "37" } });
  h.tab("advanced").props.onClick();
  h.input("setting-random_seed").props.onChange({ target: { value: "123" } });
  h.tab("basic").props.onClick();
  assert.equal(h.input("setting-minimum_break_minutes").props.value, "90");
  assert.equal(h.input("setting-s1_weight").props.value, "37");
  assert.equal(h.input("setting-random_seed").props.value, "123");
  let focus = -1;
  let prevented = 0;
  const keyboard = (id, key) => h.tab(id).props.onKeyDown({ key, preventDefault: () => prevented++,
    currentTarget: { parentElement: { querySelectorAll: () => [0, 1, 2, 3].map((index) => ({ focus: () => { focus = index; } })) } } });
  keyboard("basic", "End"); assert.equal(focus, 3); assert.equal(h.tab("advanced").props["aria-selected"], true);
  keyboard("advanced", "Home"); assert.equal(focus, 0);
  keyboard("basic", "ArrowLeft"); assert.equal(focus, 3);
  keyboard("advanced", "ArrowRight"); assert.equal(focus, 0);
  assert.equal(prevented, 4);
  for (const id of ["basic", "workload", "fairness", "advanced"]) {
    assert.equal(h.tab(id).props["aria-controls"], h.panel(id).props.id);
    assert.equal(h.panel(id).props["aria-labelledby"], h.tab(id).props.id);
    assert.equal(h.tab(id).props.tabIndex, id === "basic" ? 0 : -1);
  }
  assert.equal(h.input("setting-minimum_break_minutes").props.value, "90");
});

test("Balanced restores actual institution workload values and preserves other preferences", async () => {
  const h = await preferenceHarness((review) => {
    Object.assign(review.scheduling.settings.optimization, { s1_weight: 41, s2_weight: 31, difficulty_weight: 63, baseline_budget_percent: 17 });
  });
  const original = structuredClone(h.props.review);
  h.tab("workload").props.onClick();
  assert.equal(h.button("Use Balanced Values").props.disabled, true);
  const customize = h.find((node) => node.type === "details" && typeof node.props.onToggle === "function");
  assert.equal(customize.props.open, false);
  const opened = { open: true };
  customize.props.onToggle({ target: opened, currentTarget: opened });
  h.input("setting-s1_weight").props.onChange({ target: { value: "55" } });
  h.props.onChange({ ...h.props.form, difficulty_weight: "74", baseline_budget_percent: "20", minimum_break_minutes: "90", random_seed: "123", fairness_enabled: true });
  assert.equal(h.button("Use Balanced Values").props.disabled, false);
  h.button("Use Balanced Values").props.onClick();
  assert.equal(h.props.form.s1_weight, "41");
  assert.equal(h.props.form.s2_weight, "31");
  assert.equal(h.props.form.difficulty_weight, "63");
  assert.equal(h.props.form.baseline_budget_percent, "17");
  assert.equal(h.props.form.minimum_break_minutes, "90");
  assert.equal(h.props.form.random_seed, "123");
  assert.equal(h.props.form.fairness_enabled, true);
  assert.deepEqual(h.props.review, original);
  h.tab("basic").props.onClick(); h.tab("workload").props.onClick();
  assert.equal(h.find((node) => node.type === "details" && typeof node.props.onToggle === "function").props.open, true);
});

test("fairness limits hide when off without losing values or bypassing validation", async () => {
  const h = await preferenceHarness();
  h.tab("fairness").props.onClick();
  const limits = () => h.find((node) => node.type === "div" && node.props.hidden !== undefined && node.props.className === "space-y-4");
  assert.equal(limits().props.hidden, true);
  const toggle = (checked) => h.input("setting-fairness-enabled").props.onChange({ target: { checked } });
  toggle(true);
  assert.equal(limits().props.hidden, false);
  h.input("setting-max_individual_increase_points").props.onChange({ target: { value: "7" } });
  h.input("setting-max_worst_increase_points").props.onChange({ target: { value: "2" } });
  toggle(false);
  assert.equal(limits().props.hidden, true);
  assert.equal(h.lib.schedulingPreferences(h.props.form).fairness.max_individual_increase_points, 7);
  assert.equal(h.lib.schedulingPreferences(h.props.form).fairness.max_worst_increase_points, 2);
  h.tab("advanced").props.onClick(); h.tab("fairness").props.onClick(); toggle(true);
  assert.equal(h.input("setting-max_individual_increase_points").props.value, "7");
  h.input("setting-max_individual_increase_points").props.onChange({ target: { value: "101" } });
  toggle(false);
  assert.match(h.html(), /A retained fairness limit is invalid/);
  assert.equal(h.exports.preferenceErrorTabs(h.lib.settingsErrors(h.props.form)).fairness, 1);
});

test("all supported number controls still update serialized preferences and hidden errors are discoverable", async () => {
  const h = await preferenceHarness();
  const original = structuredClone(h.props.form);
  for (const [name, rule] of Object.entries(h.lib.NUMBER_FIELDS)) {
    const value = original[name] === "" ? "2" : String(Number(original[name]) + 1);
    h.input(`setting-${name}`).props.onChange({ target: { value } });
    assert.equal(h.lib.schedulingPreferences(h.props.form)[rule.section][name], Number(value), name);
    h.props.onChange({ ...original });
  }
  h.props.onChange({ ...original, preparation_target_minutes: "1", random_seed: "-1", difficulty_weight: "-1" });
  const counts = h.exports.preferenceErrorTabs(h.lib.settingsErrors(h.props.form));
  assert.equal(counts.workload, 1);
  assert.equal(counts.advanced, 2);
  assert.equal(h.tab("workload").props.children[1].props.children[0], 1);
  h.tab("workload").props.onClick();
  assert.equal(h.find((node) => node.type === "details" && typeof node.props.onToggle === "function").props.open, true);
  const warnings = [];
  const previousError = console.error;
  try { console.error = (...args) => warnings.push(args.map(String).join(" ")); h.html(); }
  finally { console.error = previousError; }
  assert.deepEqual(warnings.filter((message) => /unique.*key|same key/i.test(message)), []);
});

test("effective preference summaries show returned values and strip unexpected private fields", async () => {
  const fixture = generationFixture();
  fixture.inputs.scheduling_preferences = { format: "timetable-preferences-v1", fingerprint: "a".repeat(64),
    settings: { scheduling: { minimum_break_minutes: 90, permitted_start_times: ["09:00"] }, solver: { time_limit_seconds: 120 },
      unexpected: { student_id: "IT33123456" } }, seating: { default_percentage: 60, venue_overrides: { A304: 80, IT33123456: 75 } } };
  const h = generationAction({ response: { ok: true, data: fixture } });
  const result = await h.submit();
  assert.equal(JSON.stringify(result).includes("IT33123456"), false);
  const markup = resultsView(result.result);
  for (const text of ["Effective scheduling preferences", "Default exam seating: 60%", "A304: 80%", "09:00", "90", "a".repeat(64)]) assert.ok(markup.includes(text), text);
});

test("a no-solution view shows diagnostics and unavailable scores without claiming success", () => {
  const result = generationFixture();
  result.generated = false;
  result.status = "UNKNOWN";
  result.sessions = [];
  result.validation.passed = null;
  result.workload = null;
  result.diagnostics = ["Solver time limit reached"];
  const markup = resultsView(result);
  for (const text of ["No validated timetable generated", "No timetable to display", "Not evaluated", "No workload scores returned", "Solver time limit reached"]) assert.ok(markup.includes(text));
});

function periodFixture() {
  return { period_code: "FINAL-CYCLE", period_name: "Final cycle", academic_year_label: "2026/2027", semester: 2, exam_kind: "FINAL",
    start_date: "2026-11-16", end_date: "2026-11-28", day_start_time: "08:30:00", day_end_time: "17:30:00",
    include_saturdays: false, include_sundays: false, exclusions: [{ excluded_date: "2026-11-24", reason: "Excluded day" }],
    exam_days: ["2026-11-16", "2026-11-17"], fingerprint: "a".repeat(64) };
}

function periodHarness(options = {}) {
  const period = periodFixture();
  const calls = [];
  const edit = { expected_fingerprint: period.fingerprint, start_date: "2026-11-17", end_date: period.end_date,
    include_saturdays: false, include_sundays: false, exclusions: period.exclusions };
  const settings = { source: "defaults", status: "valid", version: 0, fingerprint: "settings", mandatory_rules: ["H1"], problems: [],
    settings: { solver: { time_limit_seconds: 60 }, optimization: { s1_weight: 25 }, fairness: { enabled: false } } };
  for (const [key, rule] of Object.entries(load("src/lib/examination-settings.ts", {}).NUMBER_FIELDS)) {
    (settings.settings[rule.section] ??= {})[key] = rule.default;
  }
  settings.settings.scheduling.permitted_start_times = ["08:30", "13:30"];
  settings.settings.fairness.protect_worst_student = true;
  const seating = { ruleset_version: 1, default: { percentage: 50 }, venue_count: 1, total_normal_capacity: 100, total_exam_capacity: 50, unavailable_venues: [], venue_overrides: [] };
  const actions = load("src/lib/examination-workspace-actions.ts", {
    "@/lib/examination-calendar": load("src/lib/examination-calendar.ts", {}),
    "@/lib/examination-settings": load("src/lib/examination-settings.ts", {}),
    "node:crypto": crypto,
    "@/lib/examination-access": { requireExaminationAdmin: async () => { if (options.denied) redirect("/"); return { id: options.userId ?? "owner" }; } },
    "@/lib/examination": { listExamModules: async () => ({ ok: true, data: [{ module_code: "IT1001", module_name: "Mapped", academic_year: 1, semester: 2, specializations: ["IT"], candidate_count: 10, delivery_mode: "regular" }, { module_code: "IT2002", module_name: "Not mapped" }] }) },
    "@/lib/examination-api": { examinationRequest: async (path, init = {}) => {
      calls.push({ path, ...init });
      if (init.method === "PUT") {
        if (options.locked) return { ok: false, error: { status: 409, code: "HTTP_409", message: "PERIOD_LOCKED: published timetable exists" } };
        if (options.forbidden) return { ok: false, error: { status: 403, code: "HTTP_403", message: "Permission denied" } };
        return { ok: true, data: { dry_run: init.query.dry_run, changed: true, changed_fields: ["start_date"], draft_timetables: 2,
          notices: ["Two drafts become stale"], period: { ...period, ...init.body, fingerprint: init.query.dry_run ? period.fingerprint : "b".repeat(64), exam_days: ["2026-11-17"] } } };
      }
      if (path === "/api/v1/exam-periods/FINAL-CYCLE") return { ok: true, data: { ...period, fingerprint: options.stale ? "c".repeat(64) : period.fingerprint } };
      if (path.endsWith("/modules")) return { ok: true, data: options.emptyMapping ? [] : [{ period_code: "FINAL-CYCLE", module_code: "IT1001", candidate_scope: "all" }] };
      if (path === "/api/v1/settings/scheduling") return { ok: true, data: settings };
      if (path === "/api/v1/settings/exam-seating") return { ok: true, data: seating };
      if (path === "/api/v1/timetables") return { ok: true, data: { total: init.query.status === "DRAFT" ? 2 : options.locked ? 1 : 0 } };
      if (path === "/api/v1/registrations" && init.query.registration_type === "repeat") {
        if (options.repeatsFail) return { ok: false, error: { status: 503, code: "HTTP_503", message: "Unavailable" } };
        return { ok: true, data: { items: [{ student_id: "IT30000001", module_code: "IT1001", registration_type: "repeat" },
          { student_id: "IT30000002", module_code: "IT1001", registration_type: "repeat" }], total: 2, limit: 500, offset: 0 } };
      }
      throw new Error(`Unexpected frontend request: ${path}`);
    } },
  }, { env: { AUTH_SECRET: randomUUID() } });
  return { ...actions, calls, edit, options, period };
}

test("period edits preview without saving and preserve untouched backend period fields", async () => {
  const h = periodHarness();
  const preview = await h.previewExamPeriod("FINAL-CYCLE", h.edit);
  assert.equal(preview.ok, true);
  assert.equal(preview.data.change.dry_run, true);
  assert.match(preview.data.preview_token, /^\d+\.[a-f0-9]{64}$/);
  assert.equal(h.calls.filter((call) => call.method === "PUT" && call.query.dry_run === false).length, 0);
  const put = h.calls.find((call) => call.method === "PUT");
  for (const name of ["period_name", "academic_year_label", "semester", "exam_kind", "day_start_time", "day_end_time"]) assert.deepEqual(put.body[name], h.period[name]);
  assert.equal(put.body.start_date, h.edit.start_date);
  const saved = await h.saveExamPeriod("FINAL-CYCLE", h.edit, preview.data.preview_token, true);
  assert.equal(saved.ok, true);
  assert.equal(saved.data.change.dry_run, false);
  assert.equal(h.calls.filter((call) => call.method === "PUT" && call.query.dry_run === false).length, 1);
});

test("saving dates requires explicit confirmation and the exact preview signed for the current user", async () => {
  for (const alteration of ["no-confirmation", "changed-dates", "changed-user", "forged-token"]) {
    const h = periodHarness();
    const preview = await h.previewExamPeriod("FINAL-CYCLE", h.edit);
    const edit = alteration === "changed-dates" ? { ...h.edit, start_date: "2026-11-18" } : h.edit;
    if (alteration === "changed-user") h.options.userId = "someone-else";
    const token = alteration === "forged-token" ? `${Date.now()}.${"0".repeat(64)}` : preview.data.preview_token;
    const result = await h.saveExamPeriod("FINAL-CYCLE", edit, token, alteration !== "no-confirmation");
    assert.equal(result.ok, false);
    assert.equal(h.calls.filter((call) => call.method === "PUT" && call.query.dry_run === false).length, 0);
  }
});

test("stale versions, backend locks, and manage permission failures never cause a period save", async () => {
  for (const options of [{ stale: true }, { locked: true }, { forbidden: true }]) {
    const h = periodHarness(options);
    const preview = await h.previewExamPeriod("FINAL-CYCLE", h.edit);
    assert.equal(preview.ok, false);
    assert.equal(h.calls.some((call) => call.method === "PUT" && call.query.dry_run === false), false);
  }
  const h = periodHarness();
  const preview = await h.previewExamPeriod("FINAL-CYCLE", h.edit);
  h.options.locked = true;
  const result = await h.saveExamPeriod("FINAL-CYCLE", h.edit, preview.data.preview_token, true);
  assert.equal(result.error.status, 409);
  assert.equal(h.calls.some((call) => call.method === "PUT" && call.query.dry_run === false), false);
  const denied = periodHarness({ denied: true });
  await assert.rejects(denied.previewExamPeriod("FINAL-CYCLE", denied.edit), { destination: "/" });
  assert.equal(denied.calls.length, 0);
});

test("cycle review uses only real mapped metadata and reports empty mappings and released periods", async () => {
  const h = periodHarness();
  const result = await h.loadCycleReview("FINAL-CYCLE");
  assert.equal(result.ok, true);
  assert.deepEqual(result.data.modules.map((module) => module.module_code), ["IT1001"]);
  // Exact split: 10 registrations of which 2 are repeat; student IDs never leave the server.
  assert.deepEqual([result.data.modules[0].normal_candidates, result.data.modules[0].repeat_candidates], [8, 2]);
  assert.ok(!JSON.stringify(result.data).includes("IT30000001"));
  const unsplit = (await periodHarness({ repeatsFail: true }).loadCycleReview("FINAL-CYCLE")).data.modules[0];
  assert.deepEqual([unsplit.normal_candidates, unsplit.repeat_candidates], [null, null]);
  assert.equal(result.data.draft_count, 2);
  assert.equal(result.data.locked, false);
  assert.equal((await periodHarness({ locked: true }).loadCycleReview("FINAL-CYCLE")).data.locked, true);
  const empty = await periodHarness({ emptyMapping: true }).loadCycleReview("FINAL-CYCLE");
  assert.deepEqual(empty.data.modules, []);
  assert.match(empty.data.warnings[0], /No modules are mapped/);
});

test("academic filters retain full sessions and use the returned year, semester and specializations", () => {
  const presentation = load("src/lib/examination-presentation.ts", {});
  const sessions = [
    { ...generationFixture().sessions[0], academic_year: 1, semester: 2, specializations: ["IT", "SE"], normal_candidates: 8, repeat_candidates: 2 },
    { ...generationFixture().sessions[0], module_code: "IT2002", academic_year: 2, semester: 1, specializations: ["DS"] },
  ];
  const filters = { year: "1", semester: "2", specialization: "SE", module: "IT1001" };
  assert.deepEqual(presentation.filterSessions(sessions, filters), [sessions[0]]);
  assert.deepEqual(presentation.filterSessions(sessions, { ...filters, year: "4" }), []);
  assert.equal(sessions.length, 2);
  assert.equal(sessions[0].candidates, 10);
  assert.deepEqual(presentation.calendarMonths("2026-12-22", "2027-01-03"), ["2026-12", "2027-01"]);
  assert.ok(presentation.monthCells("2028-02").includes("2028-02-29"));
});

test("results expose academic metadata and Normal/Repeat counts, with honest hard-rule evidence", async () => {
  const raw = generationFixture();
  Object.assign(raw.sessions[0], { academic_year: 1, semester: 2, normal_candidates: 8, repeat_candidates: 2, delivery_mode: "regular" });
  const action = generationAction({ response: { ok: true, data: raw } });
  const result = (await action.submit()).result;
  assert.deepEqual(result.sessions[0].specializations, ["IT", "SE"]);
  assert.equal(result.sessions[0].normal_candidates, 8);
  const markup = resultsView(result);
  for (const text of ["Calendar View", "Table View", "Academic year", "Semester", "Specialization", "8 Normal", "2 Repeat", "Independent hard-rule validation", "no individual status", "rule-H1", "optimality is not proven"]) assert.ok(markup.includes(text), text);
  assert.equal(markup.includes("H1 · Passed"), false);
});

test("generation refuses a stale reviewed period without submitting a partial or full solve", async () => {
  const h = generationAction({ periodResult: { ok: true, data: [{ period_code: "FINAL-CYCLE", exam_kind: "FINAL", fingerprint: "new" }] } });
  const result = await h.submit({ expected_period_fingerprint: "old" });
  assert.equal(result.httpStatus, 409);
  assert.equal(h.calls.length, 0);
});

test("the wizard renders five steps without loading, generating or saving on initial render", () => {
  const modules = renderModules();
  let actions = 0;
  const workspace = { previewExamPeriod: () => { actions++; }, saveExamPeriod: () => { actions++; }, loadCycleReview: () => { actions++; } };
  const periodUi = load("src/app/(examination)/exams/generate/_components/period-calendar.tsx", { ...modules, "@/lib/examination-workspace-actions": workspace });
  const { GenerationForm } = load("src/app/(examination)/exams/generate/_components/generation-form.tsx", {
    ...modules, "@/components/icon": modules.icons,
    "@/lib/examination-generation-actions": { generateFinalTimetable: () => { actions++; } },
    "@/lib/examination-workspace-actions": workspace,
    "./generation-results": modules, "./period-calendar": periodUi, "./academic-groups": modules, "./settings-review": modules,
  });
  const html = renderToStaticMarkup(createElement(GenerationForm, { cycles: [examOption()] }));
  for (const label of ["Select Exam", "Choose Dates", "Review Modules", "Generate", "Results", "Back", "Continue",
    "Academic year", "2030/2031", "FINAL examination", "MID examination", "Semester 1", "Semester 2"]) assert.ok(html.includes(label), label);
  assert.equal(html.includes("Scheduling preferences"), false, "preferences are no longer a wizard step");
  assert.equal(html.includes("Step 1 of 5") || /Step (<!-- -->)?1(<!-- -->)? of (<!-- -->)?5/.test(html), true);
  const nav = html.slice(html.indexOf('aria-label="Timetable generation steps"'), html.indexOf("</nav>"));
  assert.equal((nav.match(/<li>/g) ?? []).length, 5, "five wizard steps");
  assert.ok(!html.includes("Load cycle and next"));
  assert.equal(actions, 0);
  const calendar = renderToStaticMarkup(createElement(periodUi.PeriodCalendar, { period: periodFixture(), locked: true, drafts: 2, onEditing: () => {}, onSaved: () => {} }));
  assert.ok(calendar.includes("Excluded: Excluded day"));
  assert.ok(calendar.includes("Allowed exam day"));
  assert.ok(calendar.includes("Date changes are locked"));
  assert.equal(actions, 0);
});

test("reviewing disabled fairness explains that individual fairness limits are not enforced", async () => {
  const modules = renderModules();
  const { data: review } = await periodHarness().loadCycleReview("FINAL-CYCLE");
  const form = modules["@/lib/examination-settings"].settingsForm(review.scheduling, review.seating);
  const html = renderToStaticMarkup(createElement(modules.SettingsReview, { review, form, applied: form, onChange: () => {}, onApply: () => {}, onReset: () => {} }));
  assert.match(html, /Fairness protection is disabled/);
  assert.match(html, /individual fairness limits are not enforced/);
  const stale = generationAction();
  const result = await stale.submit({ expected_settings_fingerprint: "old-settings", expected_seating_version: "1" });
  assert.equal(result.httpStatus, 409);
  assert.equal(stale.calls.some(([, init]) => init.method === "POST"), false);
});

function componentHarness(source, mocks, props, exportName) {
  const modules = renderModules();
  const slots = [];
  let cursor = 0;
  let work = Promise.resolve();
  const hooks = {
    useState(initial) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = typeof initial === "function" ? initial() : initial;
      return [slots[index], (value) => { slots[index] = typeof value === "function" ? value(slots[index]) : value; }];
    },
    useRef(initial) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = { current: initial };
      return slots[index];
    },
    useTransition() { cursor++; return [false, (callback) => { work = Promise.resolve(callback()); }]; },
    useEffect() {},
  };
  const exports = load(source, { ...modules, ...mocks, react: hooks });
  const render = () => { cursor = 0; return exports[exportName](props); };
  const nodes = (node) => !node || typeof node !== "object" ? [] : Array.isArray(node) ? node.flatMap(nodes) : [node, ...nodes(node.props?.children)];
  const text = (node) => node == null || typeof node === "boolean" ? "" : typeof node !== "object" ? String(node) : Array.isArray(node) ? node.map(text).join("") : text(node.props?.children);
  const find = (predicate) => { const result = nodes(render()).find(predicate); assert.ok(result, "Expected rendered control"); return result; };
  const button = (label) => find((node) => node.type === "button" && text(node) === label);
  return { exports, modules, render, find, button, all: () => nodes(render()), wait: () => work, html: () => renderToStaticMarkup(render()) };
}

test("the download action exports only the display selection, preserves the source and disables empty or invalid results", async () => {
  const modules = renderModules();
  const first = { ...generationFixture().sessions[0], academic_year: 1, semester: 1, specializations: ["SE", "IT"] };
  const second = { ...first, module_code: "IT2001", academic_year: 2, semester: 2, specializations: ["IT"] };
  const props = { sessions: [first, second], valid: true, statusMessage: "", exportMetadata: {
    periodCode: "FINAL-CYCLE", periodName: "Final cycle", status: "UNSAVED", recordedAtLabel: "Generated",
  } };
  const before = JSON.stringify(props.sessions);
  const downloads = [];
  const h = componentHarness("src/app/(examination)/exams/generate/_components/timetable-browser.tsx", {
    "./academic-groups": modules, "./period-calendar": { fieldClass: "field", secondaryButton: "secondary" },
    "@/lib/examination-pdf": { downloadTimetablePdf: async (...args) => { downloads.push(args); } },
  }, props, "TimetableBrowser");
  const filters = { year: "1", semester: "1", specialization: "SE", module: "" };
  const filter = () => h.find((node) => node.props?.filters && typeof node.props.onChange === "function");
  filter().props.onChange(filters);
  await h.button("Download PDF").props.onClick();
  assert.deepEqual(downloads, [[[first], props.exportMetadata, filters]]);
  assert.equal(JSON.stringify(props.sessions), before);
  filter().props.onChange({ ...filters, module: "MISSING" });
  assert.equal(h.button("Download PDF").props.disabled, true);
  await h.button("Download PDF").props.onClick();
  assert.equal(downloads.length, 1);
  props.valid = false;
  filter().props.onChange({ year: "", semester: "", specialization: "", module: "" });
  assert.equal(h.button("Download PDF").props.disabled, true);
});

function calendarHarness(options = {}) {
  const period = periodFixture();
  const calendar = load("src/lib/examination-calendar.ts", {});
  const previewCalls = [];
  const saveCalls = [];
  const saved = [];
  const change = (edit, dry_run) => ({ ok: true, data: {
    preview_token: randomUUID(),
    change: { dry_run, changed: !calendar.sameProposedCalendar(edit, period), changed_fields: ["start_date"], draft_timetables: 2, notices: [],
      period: { ...calendar.proposedCalendar(period, edit), fingerprint: dry_run ? period.fingerprint : "b".repeat(64) } },
  } });
  const h = componentHarness("src/app/(examination)/exams/generate/_components/period-calendar.tsx", {
    "@/lib/examination-workspace-actions": {
      previewExamPeriod: async (code, edit) => { previewCalls.push({ code, edit }); return options.preview ? options.preview(edit, change) : change(edit, true); },
      saveExamPeriod: async (code, edit, token, confirmed) => { saveCalls.push({ code, edit, token, confirmed }); return change(edit, false); },
    },
  }, { period, locked: false, drafts: 2, onEditing: () => {}, onSaved: (value) => saved.push(value) }, "PeriodCalendar");
  const input = (description) => h.find((node) => node.type === "input" && node.props["aria-describedby"] === description);
  const date = (name, value) => input(`period-${name}-error`).props.onChange({ target: { value } });
  const checkbox = (label, checked) => {
    const node = h.find((item) => item.type === "label" && Array.isArray(item.props.children) && item.props.children.some((child) => child === label));
    node.props.children.find((child) => child?.type === "input").props.onChange({ target: { checked } });
  };
  const proposed = () => h.find((node) => node.type === h.exports.CalendarGrid && node.props.proposed).props.period;
  h.button("Edit period dates").props.onClick();
  return { ...h, period, previewCalls, saveCalls, saved, change, input, date, checkbox, proposed };
}

test("reason validation accepts English spaces and preserves backend length and control-character rules", async () => {
  const c = load("src/lib/examination-calendar.ts", {});
  for (const reason of ["poya day", "Poya day", "University holiday", "sunday", " Poya day ", "University  holiday", "x".repeat(120)]) {
    assert.equal(c.exclusionReasonError(reason), undefined);
    const h = periodHarness();
    const edit = { ...h.edit, exclusions: [{ excluded_date: "2026-11-25", reason }] };
    const result = await h.previewExamPeriod("FINAL-CYCLE", edit);
    assert.equal(result.ok, true);
    const put = h.calls.find((call) => call.method === "PUT");
    assert.equal(put.query.dry_run, true);
    assert.equal(put.body.exclusions[0].reason, c.normalizeExclusionReason(reason));
    assert.equal(c.sameProposedCalendar(edit, result.data.change.period), true);
    assert.equal(h.calls.some((call) => call.method === "PUT" && !call.query.dry_run), false);
  }
  for (const reason of ["", "  ", " ab ", "x".repeat(121), "Poya\nday", "Poya\tday", "Poya\u0000day", "Poya\u007fday", "\nPoya day"]) {
    assert.ok(c.exclusionReasonError(reason));
    const h = periodHarness();
    const result = await h.previewExamPeriod("FINAL-CYCLE", { ...h.edit, exclusions: [{ excluded_date: "2026-11-25", reason }] });
    assert.equal(result.ok, false);
    assert.ok(result.error.fieldErrors["exclusions.0.reason"]);
    assert.equal(h.calls.length, 0);
  }
});

test("invalid reasons show inline errors before dry-run without sending requests", () => {
  const h = calendarHarness();
  for (const reason of [" ab ", "University\tholiday", "x".repeat(121)]) {
    h.input("exclusion-0-reason-error").props.onChange({ target: { value: reason } });
    assert.equal(h.input("exclusion-0-reason-error").props["aria-invalid"], true);
    assert.equal(h.button("Validate dry-run preview").props.disabled, true);
    h.button("Validate dry-run preview").props.onClick();
    assert.equal(h.previewCalls.length, 0);
    assert.equal(h.saveCalls.length, 0);
    assert.match(h.html(), /3–120 characters|without tabs or control characters/);
  }
});

test("trimmed backend reasons match the dry-run while subsequent edits still invalidate it", async () => {
  const h = calendarHarness({ preview: (edit, change) => change({ ...edit,
    exclusions: edit.exclusions.map((item) => ({ ...item, reason: item.reason.trim() })) }, true) });
  h.input("exclusion-0-reason-error").props.onChange({ target: { value: " Poya day " } });
  assert.equal(h.button("Validate dry-run preview").props.disabled, false);
  h.button("Validate dry-run preview").props.onClick();
  await h.wait();
  assert.match(h.html(), /Backend dry-run validated these exact proposed values/);
  h.checkbox("I confirm saving these period changes and understand the impact on existing drafts.", true);
  assert.equal(h.button("Confirm and save period dates").props.disabled, false);
  h.input("exclusion-0-reason-error").props.onChange({ target: { value: "Poya day" } });
  assert.equal(h.button("Confirm and save period dates").props.disabled, true);
  assert.equal(h.saveCalls.length, 0);
});

test("backend reason errors appear inline and clear when the proposal changes", async () => {
  const message = "Use a single line of text. Spaces between words are allowed.";
  const h = calendarHarness({ preview: async () => ({ ok: false, error: { status: 422, code: "VALIDATION_ERROR", message,
    fieldErrors: { "exclusions.0.reason": message } } }) });
  h.button("Validate dry-run preview").props.onClick();
  await h.wait();
  assert.equal(h.input("exclusion-0-reason-error").props["aria-invalid"], true);
  assert.match(h.html(), /id="exclusion-0-reason-error"[^>]*>Use a single line of text/);
  assert.equal(h.button("Confirm and save period dates").props.disabled, true);
  h.input("exclusion-0-reason-error").props.onChange({ target: { value: "University holiday" } });
  assert.equal(h.input("exclusion-0-reason-error").props["aria-invalid"], false);
  assert.equal(h.button("Validate dry-run preview").props.disabled, false);
  assert.equal(h.saveCalls.length, 0);
});

test("the proposed November calendar immediately follows dates and weekend flags without API requests", () => {
  const h = calendarHarness();
  h.date("start", "2026-11-01");
  h.date("end", "2026-11-30");
  h.checkbox("Allow Saturdays", true);
  h.checkbox("Allow Sundays", true);
  const proposed = h.proposed();
  assert.equal(proposed.start_date, "2026-11-01");
  assert.equal(proposed.end_date, "2026-11-30");
  assert.ok(proposed.exam_days.includes("2026-11-01")); // Sunday
  assert.ok(proposed.exam_days.includes("2026-11-07")); // Saturday
  assert.equal(proposed.exam_days.includes("2026-11-24"), false);
  assert.equal(proposed.exam_days.length, 29);
  assert.equal(h.period.start_date, "2026-11-16");
  assert.equal(h.period.end_date, "2026-11-28");
  assert.ok(h.html().includes("Saved Calendar"));
  assert.ok(h.html().includes("Proposed Calendar — not saved"));
  assert.equal(h.previewCalls.length, 0);
  assert.equal(h.saveCalls.length, 0);
});

test("excluded dates outside the proposal show inline errors, picker bounds and disabled validation", () => {
  const h = calendarHarness();
  h.date("start", "2026-11-25");
  const date = h.input("exclusion-0-date-error");
  assert.equal(date.props.min, "2026-11-25");
  assert.equal(date.props.max, "2026-11-28");
  assert.equal(date.props["aria-invalid"], true);
  assert.match(h.html(), /Excluded dates must be between 2026-11-25 and 2026-11-28/);
  assert.equal(h.button("Validate dry-run preview").props.disabled, true);
  assert.equal(h.button("Confirm and save period dates").props.disabled, true);
  h.input("new-exclusion-error").props.onChange({ target: { value: "2026-11-30" } });
  assert.equal(h.button("Add exclusion").props.disabled, true);
  assert.equal(h.previewCalls.length, 0);
});

test("dry-run receives the proposal and save requires a matching dry-run plus explicit confirmation", async () => {
  const h = calendarHarness();
  h.date("start", "2026-11-01");
  h.date("end", "2026-11-30");
  h.checkbox("Allow Saturdays", true);
  h.checkbox("Allow Sundays", true);
  assert.equal(h.button("Confirm and save period dates").props.disabled, true);
  h.button("Validate dry-run preview").props.onClick();
  await h.wait();
  assert.equal(h.previewCalls[0].edit.start_date, "2026-11-01");
  assert.equal(h.previewCalls[0].edit.end_date, "2026-11-30");
  assert.equal(h.previewCalls[0].edit.include_saturdays, true);
  assert.equal(h.previewCalls[0].edit.include_sundays, true);
  assert.equal(h.button("Confirm and save period dates").props.disabled, true);
  h.checkbox("I confirm saving these period changes and understand the impact on existing drafts.", true);
  assert.equal(h.button("Confirm and save period dates").props.disabled, false);
  h.button("Confirm and save period dates").props.onClick();
  await h.wait();
  assert.equal(h.saveCalls.length, 1);
  assert.equal(h.saveCalls[0].confirmed, true);
  assert.equal(h.saved[0].start_date, "2026-11-01");
  assert.equal(h.saved[0].end_date, "2026-11-30");
});

test("every changed period field invalidates its validated preview and confirmation", async () => {
  const mutations = [
    (h) => h.date("start", "2026-11-02"),
    (h) => h.date("end", "2026-11-29"),
    (h) => h.checkbox("Allow Saturdays", true),
    (h) => h.checkbox("Allow Sundays", true),
    (h) => h.input("exclusion-0-reason-error").props.onChange({ target: { value: "Updated reason" } }),
    (h) => h.input("exclusion-0-date-error").props.onChange({ target: { value: "2026-11-25" } }),
    (h) => h.button("Remove").props.onClick(),
    (h) => h.input("new-exclusion-error").props.onChange({ target: { value: "2026-11-26" } }),
  ];
  for (const mutate of mutations) {
    const h = calendarHarness();
    h.date("start", "2026-11-01");
    h.button("Validate dry-run preview").props.onClick();
    await h.wait();
    h.checkbox("I confirm saving these period changes and understand the impact on existing drafts.", true);
    assert.equal(h.button("Confirm and save period dates").props.disabled, false);
    mutate(h);
    assert.equal(h.button("Confirm and save period dates").props.disabled, true);
    assert.equal(h.html().includes("Backend dry-run validated these exact proposed values"), false);
    assert.equal(h.saveCalls.length, 0);
  }
});

test("failed, mismatched, unchanged and late dry-runs cannot enable save", async () => {
  for (const preview of [
    async () => ({ ok: false, error: { status: 409, message: "Period locked" } }),
    async (edit, change) => change({ ...edit, end_date: "2026-11-27" }, true),
  ]) {
    const h = calendarHarness({ preview });
    h.date("start", "2026-11-01");
    h.button("Validate dry-run preview").props.onClick();
    await h.wait();
    assert.equal(h.button("Confirm and save period dates").props.disabled, true);
  }
  const unchanged = calendarHarness();
  unchanged.button("Validate dry-run preview").props.onClick();
  await unchanged.wait();
  assert.match(unchanged.html(), /There is nothing to save/);
  assert.equal(unchanged.button("Confirm and save period dates").props.disabled, true);

  let finish;
  let reply;
  const h = calendarHarness({ preview: (edit, change) => { reply = change(edit, true); return new Promise((resolve) => { finish = resolve; }); } });
  h.date("start", "2026-11-01");
  h.button("Validate dry-run preview").props.onClick();
  h.date("end", "2026-11-29");
  h.date("end", "2026-11-28"); // Even changing back must not revive an in-flight preview.
  finish(reply);
  await h.wait();
  assert.equal(h.html().includes("Backend dry-run validated these exact proposed values"), false);
  assert.equal(h.button("Confirm and save period dates").props.disabled, true);
});

test("calendar validation covers invalid ranges, duplicate exclusions, disabled weekends and empty calendars", async () => {
  const c = load("src/lib/examination-calendar.ts", {});
  const base = periodHarness().edit;
  assert.ok(c.periodEditErrors({ ...base, end_date: "2026-11-01" }).end_date);
  assert.ok(c.periodEditErrors({ ...base, start_date: "2026-02-30" }).start_date);
  assert.ok(c.periodEditErrors({ ...base, end_date: "2027-05-01" }).end_date);
  assert.ok(c.periodEditErrors({ ...base, exclusions: [base.exclusions[0], base.exclusions[0]] })["exclusions.1.excluded_date"]);
  assert.ok(c.periodEditErrors({ ...base, exclusions: [{ excluded_date: "2026-11-21", reason: "Weekend" }] })["exclusions.0.excluded_date"]);
  assert.ok(c.periodEditErrors({ ...base, start_date: "2026-11-21", end_date: "2026-11-22", exclusions: [] }).calendar);
  const h = periodHarness();
  const result = await h.previewExamPeriod("FINAL-CYCLE", { ...h.edit, exclusions: [{ excluded_date: "2026-12-01", reason: "Outside range" }] });
  assert.equal(result.ok, false);
  assert.equal(h.calls.length, 0);
});

test("the calendar step retains the selected calendar and draft saving is available only after generation", async () => {
  const modules = renderModules();
  const initial = (await periodHarness().loadCycleReview("FINAL-CYCLE")).data;
  const saved = [];
  const h = componentHarness("src/app/(examination)/exams/generate/_components/generation-form.tsx", {
    "@/components/icon": modules.icons,
    "@/lib/examination-generation-actions": {
      generateFinalTimetable: async () => ({ status: "complete", result: generationFixture() }),
      saveFinalTimetableDraft: async (_state, data) => { saved.push(Object.fromEntries(data)); return { status: "idle" }; },
    },
    "@/lib/examination-workspace-actions": { loadCycleReview: async () => ({ ok: true, data: initial }) },
    "./generation-results": modules, "./period-calendar": { fieldClass: "field", primaryButton: "primary", secondaryButton: "secondary" },
    "./academic-groups": modules, "./settings-review": modules,
  }, { cycles: [examOption()] }, "GenerationForm");
  h.find((node) => node.props?.choice && typeof node.props.onChange === "function")
    .props.onChange({ year: "2030/2031", kind: "FINAL", semester: 2, periodCode: "FINAL-CYCLE" });
  h.button("Continue").props.onClick();
  await h.wait();
  const step = () => h.find((node) => node.props?.calendar && typeof node.props.onChange === "function");
  // No period editing or saving: the step is prefilled from the configured period.
  assert.deepEqual(step().props.calendar, { start_date: "2026-11-16", end_date: "2026-11-28", day_start_time: "08:30", day_end_time: "17:30",
    include_saturdays: false, include_sundays: false, exclusions: [{ excluded_date: "2026-11-24", reason: "Excluded day" }] });
  step().props.onChange({ ...step().props.calendar, end_date: "2026-11-01" });
  assert.equal(h.button("Next").props.disabled, true);
  const chosen = { ...step().props.calendar, start_date: "2026-11-30", end_date: "2026-12-11", day_start_time: "09:00", exclusions: [] };
  step().props.onChange(chosen);
  assert.equal(h.button("Next").props.disabled, false);
  for (let i = 0; i < 2; i++) h.button("Next").props.onClick(); // Review Modules, then Generate
  assert.deepEqual(JSON.parse(h.find((node) => node.type === "input" && node.props.name === "calendar").props.value), chosen);
  assert.equal(h.all().some((node) => typeof node.props?.onSave === "function"), false);
  assert.equal(h.button("Generate Timetable").props.disabled, false);
  assert.match(h.html(), /Optimized \(recommended\)/);
  assert.equal(h.find((node) => node.type === "input" && node.props.name === "mode" && node.props.value === "optimized").props.checked, true);
  h.find((node) => node.type === "form").props.action(new FormData());
  await h.wait();
  const card = () => h.find((node) => typeof node.props?.onSave === "function");
  card().props.onConfirm(true);
  card().props.onSave();
  await h.wait();
  assert.equal(saved.length, 1);
  assert.deepEqual(JSON.parse(saved[0].calendar), chosen);
  assert.equal(saved[0].confirm_save, "yes");
  h.button("Back").props.onClick();
  h.find((node) => node.type === "input" && node.props.name === "mode" && node.props.value === "feasibility").props.onChange();
  assert.equal(h.all().some((node) => node.props?.result), false);
  const resultStep = h.find((node) => node.type === "button" && node.props["aria-current"] === undefined &&
    Array.isArray(node.props.children) && node.props.children.includes("Results"));
  assert.equal(resultStep.props.disabled, true, "changing generation inputs invalidates the previous preview");
});

test("the generation action sends a valid selected calendar and refuses an invalid one without solving", async () => {
  const chosen = { start_date: "2026-11-30", end_date: "2026-12-11", day_start_time: "09:00", day_end_time: "17:00",
    include_saturdays: false, include_sundays: false, exclusions: [{ excluded_date: "2026-12-03", reason: "  University holiday " }] };
  const ok = generationAction();
  assert.equal((await ok.submit({ calendar: JSON.stringify(chosen) })).status, "complete");
  assert.deepEqual(ok.calls[0][1].body.calendar, { ...chosen, exclusions: [{ excluded_date: "2026-12-03", reason: "University holiday" }] });
  for (const calendar of ["not json", JSON.stringify({ ...chosen, extra: 1 }), JSON.stringify({ ...chosen, end_date: "2026-11-01" }),
    JSON.stringify({ ...chosen, day_end_time: "08:00" }), JSON.stringify({ ...chosen, exclusions: [{ excluded_date: "2027-01-05", reason: "Outside" }] })]) {
    const refused = generationAction();
    const result = await refused.submit({ calendar });
    assert.equal(result.status, "error", calendar);
    assert.equal(refused.calls.length, 0, calendar);
  }
});

test("results show the exam calendar the service used, including selected dates and exclusions", () => {
  const fixture = generationFixture();
  fixture.inputs.calendar = { source: "selected", start_date: "2026-11-30", end_date: "2026-12-11", exam_days: 9, day_start_time: "09:00:00",
    day_end_time: "17:00:00", include_saturdays: false, include_sundays: false, exclusions: [{ excluded_date: "2026-12-03", reason: "University holiday" }] };
  const markup = resultsView(fixture);
  for (const text of ["Exam calendar:", "30 Nov 2026", "11 Dec 2026", "9 exam days", "09:00–17:00", "No weekends", "Selected for this generation", "University holiday"]) {
    assert.ok(markup.includes(text), text);
  }
});

// ---- Results dashboard: summary, actions, tabs and panels ----
const evaluationFixture = JSON.parse(readFileSync(new URL("./fixtures/examination-evaluation.json", import.meta.url), "utf8"));
const RESULT_TAB_IDS = ["timetable", "issues", "hard", "load", "details"];
// Real-shaped analysis (optimized, baseline versus selected) with one scheduled session to show and export.
const dashboardResult = () => ({ ...structuredClone(evaluationFixture), sessions: generationFixture().sessions });
const failedResult = () => ({ ...dashboardResult(), generated: false, sessions: [], status: "MODEL_INVALID",
  validation: { passed: false, rules_checked: Array.from({ length: 11 }, (_, i) => `H${i + 1}`),
    violations: [{ rule: "H1", message: "Students overlap" }, { rule: "H1", message: "Second overlap" }, { rule: "H5", message: "Room clash" }] },
  violations: [{ rule: "NOT_SAVED", message: "Nothing was saved" }] });
const between = (markup, from, to) => markup.slice(markup.indexOf(from), to ? markup.indexOf(to) : undefined);

function resultsHarness(result = dashboardResult(), extra = {}) {
  const modules = renderModules();
  const downloads = [];
  const h = componentHarness("src/app/(examination)/exams/generate/_components/generation-results.tsx", {
    "./period-calendar": { fieldClass: "field", primaryButton: "primary", secondaryButton: "secondary" },
    "./result-panels": modules, "./timetable-browser": modules,
    "@/lib/examination-pdf": { downloadTimetablePdf: async (...args) => downloads.push(["timetable", ...args]) },
    "@/lib/examination-evaluation-pdf": { downloadEvaluationPdf: async (...args) => downloads.push(["evaluation", ...args]) },
  }, { result, ...extra }, "GenerationResults");
  const tab = (id) => h.find((node) => node.props?.role === "tab" && node.props.id === `result-tab-${id}`);
  const panel = (id) => h.find((node) => node.props?.role === "tabpanel" && node.props.id === `result-panel-${id}`);
  return { ...h, downloads, tab, panel,
    selected: () => RESULT_TAB_IDS.filter((id) => tab(id).props["aria-selected"]),
    download: (label) => h.find((node) => node.props?.label === label) };
}

test("the results dashboard leads with the headline facts and actions, then five accessible tabs", () => {
  const markup = resultsView(dashboardResult());
  const at = (text) => { const index = markup.indexOf(text); assert.ok(index >= 0, `Missing ${text}`); return index; };
  assert.ok(at("Result summary") < at("Result actions") && at("Result actions") < at('role="tablist"'));
  for (const text of ["Hard rules H1–H11", "Solver status", "FEASIBLE", "Not proven optimal", "Workload: baseline → selected", "Improved by 63.8%",
    "Preparation gaps", "47 hours average", "Difficult pairs below target", "Students improved / worsened / unchanged", "Not available",
    "Download Timetable PDF", "Download Evaluation PDF", "Unsaved preview.", "Save Draft runs a fresh generation, so the saved draft can differ from what you see here.",
    "Timetable", "Issues to Review", "Hard Constraints", "Cognitive Load (S1–S5)", "Fairness &amp; Details"]) at(text);
  assert.equal((markup.match(/role="tab"/g) ?? []).length, 5);
  assert.equal((markup.match(/aria-selected="true"/g) ?? []).length, 1);
  const panels = markup.match(/<section[^>]*role="tabpanel"[^>]*>/g) ?? [];
  assert.equal(panels.length, 5);
  assert.equal(panels.filter((tag) => /\shidden=""/.test(tag)).length, 4, "only the selected panel is visible");
  for (const id of RESULT_TAB_IDS) {
    assert.match(markup, new RegExp(`id="result-tab-${id}"[^>]*aria-controls="result-panel-${id}"`));
    assert.match(markup, new RegExp(`id="result-panel-${id}"[^>]*aria-labelledby="result-tab-${id}"`));
  }
  assert.equal(markup.includes("student_id"), false);
  assert.equal(/\b[A-Z]{2}\d{8}\b/.test(markup), false);
});

test("result tabs follow the tabs pattern: roving focus, arrow, Home and End keys, and every panel stays mounted", () => {
  const w = resultsHarness();
  assert.deepEqual(w.selected(), ["timetable"]);
  assert.deepEqual([w.tab("timetable").props.tabIndex, w.tab("issues").props.tabIndex], [0, -1]);
  assert.deepEqual([w.panel("timetable").props.hidden, w.panel("hard").props.hidden], [false, true]);
  let focus = -1;
  let prevented = 0;
  const press = (id, key) => w.tab(id).props.onKeyDown({ key, preventDefault: () => prevented++,
    currentTarget: { parentElement: { querySelectorAll: () => [0, 1, 2, 3, 4].map((index) => ({ focus: () => { focus = index; } })) } } });
  press("timetable", "ArrowRight"); assert.deepEqual(w.selected(), ["issues"]); assert.equal(focus, 1);
  press("issues", "End"); assert.deepEqual(w.selected(), ["details"]); assert.equal(focus, 4);
  press("details", "ArrowRight"); assert.deepEqual(w.selected(), ["timetable"], "wraps to the first tab");
  press("timetable", "ArrowLeft"); assert.deepEqual(w.selected(), ["details"], "wraps to the last tab");
  press("details", "Home"); assert.deepEqual(w.selected(), ["timetable"]); assert.equal(focus, 0);
  press("timetable", "a"); assert.deepEqual(w.selected(), ["timetable"]);
  assert.equal(prevented, 5, "only navigation keys are handled");
  w.tab("load").props.onClick();
  assert.deepEqual(w.selected(), ["load"]);
  assert.equal(w.all().filter((node) => node.props?.role === "tabpanel").length, 5, "hidden panels keep their filters and open sections");
  assert.deepEqual(resultsHarness(failedResult()).selected(), ["hard"], "an invalid result opens on Hard Constraints");
});

test("the action area keeps the PDFs and Save Draft within reach, and the PDFs match the filters on screen", async () => {
  const save = createElement("div", { id: "stub-save-card" });
  const w = resultsHarness(dashboardResult(), { children: save });
  assert.equal(w.button("Save Draft").props["aria-expanded"], false);
  const panel = () => w.find((node) => node.props?.id === "save-draft-panel");
  assert.equal(panel().props.hidden, true);
  assert.equal(panel().props.children, save, "the existing Save Draft card is shown here");
  w.button("Save Draft").props.onClick();
  assert.equal(w.button("Hide Save Draft").props["aria-expanded"], true);
  assert.equal(panel().props.hidden, false);
  assert.match(w.html(), /Unsaved preview\./);
  await w.download("Download Timetable PDF").props.run();
  assert.deepEqual(w.downloads[0].slice(0, 1), ["timetable"]);
  assert.equal(w.downloads[0][1].length, 1);
  assert.equal(w.downloads[0][2].status, "UNSAVED");
  assert.deepEqual(w.downloads[0][3], { year: "", semester: "", specialization: "", module: "" });
  // The Timetable tab owns the filters; the PDF follows them.
  w.find((node) => typeof node.props?.onFiltersChange === "function").props.onFiltersChange({ year: "4", semester: "", specialization: "", module: "" });
  assert.equal(w.download("Download Timetable PDF").props.disabled, true, "nothing matches, so there is nothing to export");
  assert.match(w.html(), /Timetable PDF: 0 of 1 sessions, the ones shown in the Timetable tab\./);
  w.find((node) => typeof node.props?.onFiltersChange === "function").props.onFiltersChange({ year: "", semester: "", specialization: "", module: "IT1001" });
  await w.download("Download Timetable PDF").props.run();
  assert.equal(w.downloads[1][3].module, "IT1001");
  await w.download("Download Evaluation PDF").props.run();
  assert.deepEqual(w.downloads[2].slice(0, 1).concat([w.downloads[2][2]]), ["evaluation", { year: "", specialization: "", registration: "all" }]);
  w.find((node) => typeof node.props?.onCohortChange === "function").props.onCohortChange({ year: "2", specialization: "DS", registration: "all" });
  await w.download("Download Evaluation PDF").props.run();
  assert.deepEqual(w.downloads[3][2], { year: "2", specialization: "DS", registration: "all" });
  // Without a validated timetable or the optional evaluation, the buttons say so instead of failing later.
  assert.equal(resultsHarness(failedResult()).download("Download Timetable PDF").props.disabled, true);
  assert.equal(resultsHarness({ ...dashboardResult(), analysis: null }).download("Download Evaluation PDF").props.disabled, true);
  assert.equal(resultsHarness().all().some((node) => node.props?.id === "save-draft-panel"), false, "no Save Draft without a validated result to save");
});

test("download buttons show progress, success and the specific failure", async () => {
  const outcomes = [];
  let fail = null;
  const h = componentHarness("src/app/(examination)/exams/generate/_components/generation-results.tsx", {
    "./period-calendar": { fieldClass: "field", primaryButton: "primary", secondaryButton: "secondary" },
    "./result-panels": renderModules(), "./timetable-browser": renderModules(),
  }, { label: "Download Timetable PDF", busyLabel: "Preparing PDF…", errorText: "The PDF could not be downloaded.", disabled: false,
    run: async () => { outcomes.push("run"); if (fail) throw fail; } }, "DownloadButton");
  const text = () => { const node = h.find((item) => item.type === "p"); return [node.props.role, node.props.children]; };
  await h.button("Download Timetable PDF").props.onClick();
  assert.deepEqual(text(), ["status", "Download Timetable PDF: download requested. Check your browser downloads."]);
  fail = new Error("This timetable contains characters the PDF cannot print.");
  await h.button("Download Timetable PDF").props.onClick();
  assert.deepEqual(text(), ["alert", "This timetable contains characters the PDF cannot print."]);
  fail = new Error("internal detail");
  await h.button("Download Timetable PDF").props.onClick();
  assert.deepEqual(text(), ["alert", "The PDF could not be downloaded."], "internal error text is not shown");
  assert.equal(outcomes.length, 3);
});

test("Issues to Review keeps soft-target concerns apart from hard-rule failures and shows real pair details", () => {
  const markup = between(resultsView(dashboardResult()), 'id="result-panel-issues"', 'id="result-panel-hard"');
  for (const text of ["Hard-rule failures", "Mandatory rules", "No hard-rule failures were reported by the independent validator", "Soft-target concerns", "Preferences, not rules",
    "does not make the timetable invalid", "Difficult exams close together", "Needs review", "Short gaps between exams", "Same-day exams", "No concern", "Selected timetable", "Baseline",
    "Improved (-3)", "Workload regressions compared with the baseline", "No measured value is worse than in the baseline",
    "Difficult exam pairs sat by the same students", "Specialization is never used to guess a clash", "IT1001", "IT1002", "16 Nov 2026", "18 Nov 2026",
    "Session 1", "47 hours", "2,820 minutes", "Below target", "Students who sit both", "0 normal-only · 1 with repeat registrations", "Show pairs from"]) assert.ok(markup.includes(text), text);
  const failed = between(resultsView(failedResult()), 'id="result-panel-issues"', 'id="result-panel-hard"');
  for (const text of ["The independent validator reported hard-rule problems. Do not use this timetable.", "H1: Students overlap", "H5: Room clash", "NOT_SAVED: Nothing was saved", "Open Hard Constraints"]) assert.ok(failed.includes(text), text);
  const feasibility = { ...dashboardResult(), solver: { ...dashboardResult().solver, mode: "feasibility" }, optimization: null };
  const plain = between(resultsView(feasibility), 'id="result-panel-issues"', 'id="result-panel-hard"');
  assert.match(plain, /Not available\. Feasibility mode has no baseline, so regressions cannot be measured\./);
  assert.equal(plain.includes("Show pairs from"), false);
});

test("the baseline pair view flags same-day and short-gap pairs only from the returned dates and gaps", () => {
  const modules = renderModules();
  const h = componentHarness("src/app/(examination)/exams/generate/_components/result-panels.tsx", {
    "./period-calendar": { fieldClass: "field" }, "./evaluation-dashboard": modules, "./preferences-summary": modules,
  }, { result: dashboardResult(), onOpenHard: () => {} }, "IssuesPanel");
  assert.equal(h.html().includes("Same day"), false, "the selected timetable keeps the pair on different days");
  h.find((node) => node.type === "select").props.onChange({ target: { value: "baseline" } });
  const html = h.html();
  for (const text of ["Same day", "Short gap", "Below target", "4 hours", "240 minutes"]) assert.ok(html.includes(text), text);
  let opened = 0;
  const failed = componentHarness("src/app/(examination)/exams/generate/_components/result-panels.tsx", {
    "./period-calendar": { fieldClass: "field" }, "./evaluation-dashboard": modules, "./preferences-summary": modules,
  }, { result: failedResult(), onOpenHard: () => opened++ }, "IssuesPanel");
  failed.button("Open Hard Constraints").props.onClick();
  assert.equal(opened, 1);
});

test("Hard Constraints shows the overall validator result and only reported violations, never invented per-rule passes", () => {
  const ok = between(resultsView(dashboardResult()), 'id="result-panel-hard"', 'id="result-panel-load"');
  for (const text of ["Independent hard-rule validation: Passed", "It reported no violations of H1–H11", "no individual status", "No setting and no optimization weight can switch them off",
    "H1 · Student overlap and breaks", "H11 · Consistency"]) assert.ok(ok.includes(text), text);
  for (let rule = 1; rule <= 11; rule++) {
    const start = ok.indexOf(`id="rule-H${rule}"`);
    assert.ok(start >= 0, `rule-H${rule}`);
    assert.equal(/Passed|PASS|Failed|violation/.test(ok.slice(start, ok.indexOf("</summary>", start))), false, `H${rule} shows no invented status`);
  }
  const bad = between(resultsView(failedResult()), 'id="result-panel-hard"', 'id="result-panel-load"');
  for (const text of ["Independent hard-rule validation: Failed", "Do not use this timetable", "2 violations reported", "1 violation reported", "Students overlap", "Second overlap", "Room clash",
    "Generation violations", "NOT_SAVED: Nothing was saved"]) assert.ok(bad.includes(text), text);
  assert.equal((bad.match(/violations? reported/g) ?? []).length, 2, "only the rules named in the violations carry a count");
  const unknown = { ...dashboardResult(), generated: false, validation: { passed: null, rules_checked: [], violations: [] } };
  assert.match(between(resultsView(unknown), 'id="result-panel-hard"', 'id="result-panel-load"'), /Independent hard-rule validation: Not evaluated/);
  assert.match(resultsView(failedResult()), /id="result-tab-hard"[^>]*aria-selected="true"/);
});

test("Cognitive Load explains S1–S5 as soft goals with weights, baseline, selected and clear change indicators", () => {
  const markup = between(resultsView(dashboardResult()), 'id="result-panel-load"', 'id="result-panel-details"');
  for (const text of ["What these scores mean", "They are goals, not rules", "A weight of 0 means the goal is ignored", "Research estimates only", "Baseline penalty", "Selected penalty",
    "Weighted contribution", "Weight", "Preparation for difficult exams", "General preparation gaps", "Same-day and short-gap exams", "Demanding-exam concentration", "Exam-count concentration",
    "Improved (-44)", "Improved (-29.5)", "Unchanged", "Total weighted penalty", "Lower is better", "Preparation-gap statistics and student-group filters", "Evaluation Dashboard"]) assert.ok(markup.includes(text), text);
  for (const code of ["S1", "S2", "S3", "S4", "S5"]) assert.ok(markup.includes(`>${code}</span>`), code);
  assert.equal(/\b(?:PASS|FAIL|Passed|Failed)\b/.test(markup), false, "soft goals are never labelled as mandatory pass or fail");
  const plain = { ...dashboardResult(), solver: { ...dashboardResult().solver, mode: "feasibility" }, optimization: null, analysis: null };
  const feasibility = between(resultsView(plain), 'id="result-panel-load"', 'id="result-panel-details"');
  assert.equal(feasibility.includes("Baseline penalty"), false);
  assert.match(feasibility, /Feasibility mode has no baseline, so there is no comparison\./);
  assert.match(feasibility, /Not available: this result did not include the optional preparation-gap evaluation\./);
  const none = { ...dashboardResult(), workload: null, optimization: null, analysis: null };
  assert.match(resultsView(none), /No workload scores returned/);
});

test("Fairness & Details shows the solver run, fairness, effective preferences and the evaluation inputs", () => {
  const withFairness = dashboardResult();
  withFairness.optimization.fairness = { protection_applied: false, limits_satisfied: null, students: 10, students_improved: 8, students_worsened: 1, students_unchanged: 1,
    baseline_worst_normalized_penalty: 5, selected_worst_normalized_penalty: 4, maximum_individual_increase_points: 2, worst_student_increase_points: 1 };
  const markup = between(resultsView(withFairness), 'id="result-panel-details"');
  for (const text of ["Solver run", "Runtime", "of a 60 s limit", "Optimization status", "Objective bound", "Relative gap", "Not available", "not a quality score", "Fairness comparison",
    "Fairness protection was not applied", "Improved / worsened / unchanged", "8 / 1 / 1", "Effective scheduling preferences", "Module difficulty", "Evaluation provenance and limits"]) assert.ok(markup.includes(text), text);
  const without = between(resultsView(dashboardResult()), 'id="result-panel-details"');
  assert.match(without, /No fairness details returned/);
});
