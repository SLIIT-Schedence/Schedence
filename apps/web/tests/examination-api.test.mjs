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
  assert.deepEqual(calls[0][1].body, { period_code: "FINAL-CYCLE", mode: "feasibility" });
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
  const settings = load("src/app/(examination)/exams/generate/_components/settings-review.tsx", common);
  const browser = load("src/app/(examination)/exams/generate/_components/timetable-browser.tsx", { ...common, "./academic-groups": groups, "./period-calendar": styles });
  const results = load("src/app/(examination)/exams/generate/_components/generation-results.tsx", {
    "react/jsx-runtime": jsxRuntime,
    "@/components/console/ui": ui,
    "@/lib/examination-presentation": presentation,
    "./settings-review": settings,
    "./timetable-browser": browser,
  });
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
  return { ...common, ...results, ...groups, ...settings, ...browser, icons, "next/navigation": navigation, "./save-draft": saveDraft,
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
  for (const text of ["IT1001", "Test module", "A304", "16 Nov 2026", "08:30", "10:30", "FEASIBLE", "Passed", "S1", "S5", "Unsaved result"]) {
    assert.ok(markup.includes(text), `Missing ${text}`);
  }
  assert.match(markup, /10 candidates/);
  assert.equal(markup.includes("student_id"), false);
  assert.equal(markup.includes("IT33123456"), false);
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
  const seating = { ruleset_version: 1, default: { percentage: 50 }, venue_count: 1, total_normal_capacity: 100, total_exam_capacity: 50, unavailable_venues: [], venue_overrides: [] };
  const actions = load("src/lib/examination-workspace-actions.ts", {
    "@/lib/examination-calendar": load("src/lib/examination-calendar.ts", {}),
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

test("results expose academic metadata and Regular/Repeat counts, with honest hard-rule evidence", async () => {
  const raw = generationFixture();
  Object.assign(raw.sessions[0], { academic_year: 1, semester: 2, normal_candidates: 8, repeat_candidates: 2, delivery_mode: "regular" });
  const action = generationAction({ response: { ok: true, data: raw } });
  const result = (await action.submit()).result;
  assert.deepEqual(result.sessions[0].specializations, ["IT", "SE"]);
  assert.equal(result.sessions[0].normal_candidates, 8);
  const markup = resultsView(result);
  for (const text of ["Calendar View", "Table View", "Academic year", "Semester", "Specialization", "8 Regular", "2 Repeat", "Validation and optimization summary", "no individual status", "rule-H1", "optimality is not proven"]) assert.ok(markup.includes(text), text);
  assert.equal(markup.includes("H1 · Passed"), false);
});

test("generation refuses a stale reviewed period without submitting a partial or full solve", async () => {
  const h = generationAction({ periodResult: { ok: true, data: [{ period_code: "FINAL-CYCLE", exam_kind: "FINAL", fingerprint: "new" }] } });
  const result = await h.submit({ expected_period_fingerprint: "old" });
  assert.equal(result.httpStatus, 409);
  assert.equal(h.calls.length, 0);
});

test("the wizard renders six steps without loading, generating or saving on initial render", () => {
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
  for (const label of ["Select Examination", "Exam calendar", "Academic groups", "Scheduling rules", "Generate", "Results", "Back", "Continue",
    "Academic year", "2030/2031", "FINAL examination", "MID examination", "Semester 1", "Semester 2"]) assert.ok(html.includes(label), label);
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
  const html = renderToStaticMarkup(createElement(modules.SettingsReview, { review }));
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
  return { exports, modules, render, find, button, wait: () => work, html: () => renderToStaticMarkup(render()) };
}

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

test("the calendar step starts from the configured period and generation and saving send the selected calendar", async () => {
  const modules = renderModules();
  const initial = (await periodHarness().loadCycleReview("FINAL-CYCLE")).data;
  const saved = [];
  const h = componentHarness("src/app/(examination)/exams/generate/_components/generation-form.tsx", {
    "@/components/icon": modules.icons,
    "@/lib/examination-generation-actions": {
      generateFinalTimetable: () => { throw new Error("Generation must not be invoked"); },
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
  for (let i = 0; i < 3; i++) h.button("Next").props.onClick();
  assert.deepEqual(JSON.parse(h.find((node) => node.type === "input" && node.props.name === "calendar").props.value), chosen);
  const card = () => h.find((node) => typeof node.props?.onSave === "function");
  card().props.onConfirm(true);
  card().props.onSave();
  await h.wait();
  assert.equal(saved.length, 1);
  assert.deepEqual(JSON.parse(saved[0].calendar), chosen);
  assert.equal(saved[0].confirm_save, "yes");
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
