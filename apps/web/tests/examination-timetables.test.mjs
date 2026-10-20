import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import * as jsxRuntime from "react/jsx-runtime";

// F3: saved timetables, Save Draft, Approve, Publish and History. Mocked API answers exist only inside
// these tests; nothing here reaches the Examination Service or Neon.

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
    localRequire, testModule, testModule.exports, globals.fetch, { env: globals.env ?? {} }, AbortSignal,
  );
  return testModule.exports;
}

const helpers = load("src/lib/examination-timetables.ts", {});
const STUDENT = "IT30123456";
const PERIODS = [
  { period_code: "PP1-2026-S1-FINAL", period_name: "Semester 1", semester: 1, start_date: "2026-06-15" },
  { period_code: "PP1-2026-S2-FINAL", period_name: "Semester 2", semester: 2, start_date: "2026-11-16" },
];

// --- API client keeps workflow rule codes -------------------------------------------------------

function apiClient(status, body) {
  const api = load("src/lib/examination-api.ts", {
    "@/auth": { auth: async () => ({ accessToken: "test-token" }) },
    "next/navigation": { redirect: (to) => { throw Object.assign(new Error("Redirect"), { destination: to }); } },
    "@/lib/examination-access": { requireExaminationAdmin: async () => {} },
  }, { fetch: async () => Response.json(body, { status }) });
  return api.examinationRequest;
}

test("workflow problems keep their rule codes; request validation errors keep VALIDATION_ERROR", async () => {
  const stale = await apiClient(409, { detail: [{ rule: "STALE", message: "Regenerate it." }, { rule: "STALE_INPUTS", message: "Inputs changed." }] })("/api/v1/timetables/1/approve");
  assert.equal(stale.error.code, "STALE");
  assert.deepEqual(stale.error.problems, [{ rule: "STALE", message: "Regenerate it." }, { rule: "STALE_INPUTS", message: "Inputs changed." }]);
  const invalid = await apiClient(422, { detail: [{ loc: ["body", "reason"], msg: "too short", type: "string_too_short" }] })("/api/v1/timetables/1/approve");
  assert.equal(invalid.error.code, "VALIDATION_ERROR");
  assert.equal(invalid.error.problems, undefined);
  assert.equal(invalid.error.fieldErrors.reason, "too short");
});

// --- List filters ---------------------------------------------------------------------------------

test("filters keep only known semesters, FINAL cycles and statuses", () => {
  assert.deepEqual(helpers.readTimetableFilters({ semester: "3", period: "FORGED", status: "DELETED", page: "-1" }, PERIODS),
    { semester: undefined, period: undefined, status: undefined, page: 1 });
  assert.deepEqual(helpers.readTimetableFilters({ semester: "1", period: "PP1-2026-S1-FINAL", status: "APPROVED", page: "2" }, PERIODS),
    { semester: 1, period: "PP1-2026-S1-FINAL", status: "APPROVED", page: 2 });
  // A cycle of another semester than the chosen one is ignored.
  assert.equal(helpers.readTimetableFilters({ semester: "1", period: "PP1-2026-S2-FINAL" }, PERIODS).period, undefined);
});

test("a semester filter queries each FINAL cycle of that semester and merges pages newest first", () => {
  const one = helpers.timetableQueries({ period: "PP1-2026-S2-FINAL", status: "DRAFT", page: 2 }, PERIODS);
  assert.deepEqual(one, [{ period_code: "PP1-2026-S2-FINAL", status: "DRAFT", limit: 25, offset: 25 }]);
  const twoCycles = [...PERIODS, { period_code: "PP2-2027-S1-FINAL", period_name: "Other", semester: 1, start_date: "2027-06-14" }];
  const merged = helpers.timetableQueries({ semester: 1, page: 1 }, twoCycles);
  assert.deepEqual(merged.map((item) => item.period_code), ["PP1-2026-S1-FINAL", "PP2-2027-S1-FINAL"]);
  assert.ok(merged.every((item) => item.offset === 0 && item.limit === 25));
  assert.equal(helpers.timetableQueries({ semester: 2, page: 1 }, [PERIODS[0]]), null);
  const row = (id, created) => ({ timetable_id: id, created_at: created });
  const pages = [{ items: [row(1, "2026-10-01"), row(3, "2026-10-03")], total: 2 }, { items: [row(2, "2026-10-02")], total: 1 }];
  const result = helpers.mergeTimetablePages(pages, { page: 1 }, true);
  assert.deepEqual(result.items.map((item) => item.timetable_id), [3, 2, 1]);
  assert.equal(result.total, 3);
});

// --- Detail mapping and reasons -------------------------------------------------------------------

test("saved sessions expose display fields only and never student identifiers", () => {
  const sessions = helpers.savedSessions({ sessions: [{
    session_id: "S1", module_code: "IT1010", module_name: `Module for ${STUDENT}`, exam_type: "written", candidate_scope: "all",
    academic_year: 1, semester: 2, delivery_mode: "regular", session_number: 1, session_count: 1, date: "2026-11-16",
    start_time: "08:30:00", end_time: "09:40:00", candidates: 118, normal_candidates: 110, repeat_candidates: 8,
    rooms: [{ venue_id: "F303", venue_name: "F303", venue_type: "lecture_hall", seats: 118, exam_capacity: 120 }],
    seats: [{ student_id: STUDENT }],
  }] }, [{ module_code: "IT1010", specializations: ["IT", "SE"] }]);
  assert.deepEqual(Object.keys(sessions[0]).sort(), ["academic_year", "candidates", "date", "delivery_mode", "end_time", "module_code",
    "module_name", "normal_candidates", "repeat_candidates", "rooms", "semester", "session_count", "session_number", "specializations", "start_time"]);
  assert.deepEqual(sessions[0].rooms, [{ venue_id: "F303", venue_name: "F303", seats: 118 }]);
  assert.deepEqual(sessions[0].specializations, ["IT", "SE"]);
  assert.ok(!JSON.stringify(sessions).includes(STUDENT));
});

test("reasons become one line and follow the backend length and character rules", () => {
  assert.equal(helpers.normalizeReason("  Exam board\n approved   2026  "), "Exam board approved 2026");
  assert.equal(helpers.reasonError("ok"), "Enter a reason of 5–500 characters. It is kept in the timetable history.");
  assert.equal(helpers.reasonError("x".repeat(501)) !== null, true);
  assert.equal(helpers.reasonError("Board decision"), null);
  assert.equal(helpers.actorLabel("8c6f5a1e-0000-4000-8000-000000000000", "8c6f5a1e-0000-4000-8000-000000000000"), "You");
  assert.equal(helpers.actorLabel("8c6f5a1e-0000-4000-8000-000000000000", "other"), "User 8c6f5a1e");
});

// --- Approve and publish actions ------------------------------------------------------------------

function workflow(response) {
  const calls = [];
  let refreshes = 0;
  const actions = load("src/lib/examination-timetable-actions.ts", {
    "next/cache": { refresh: () => { refreshes++; } },
    "@/lib/examination-access": { requireExaminationAdmin: async () => {} },
    "@/lib/examination-api": { examinationRequest: async (...args) => { calls.push(args); return response; } },
    "@/lib/examination-timetables": helpers,
  });
  const form = (fields) => { const data = new FormData(); for (const [k, v] of Object.entries(fields)) data.set(k, v); return data; };
  return { actions, calls, form, refreshes: () => refreshes };
}

const changed = (extra = {}) => ({ ok: true, data: { timetable: { revision: 2, draft_number: 3, status: "APPROVED" }, archived_timetable: null, ...extra } });

test("approve sends the reason and expected revision, refreshes, and never publishes", async () => {
  const { actions, calls, form, refreshes } = workflow(changed());
  const bad = await actions.approveSavedTimetable(7, 1, { status: "idle" }, form({ reason: "no" }));
  assert.equal(bad.status, "error");
  assert.ok(bad.fieldErrors.reason);
  assert.equal(calls.length, 0);
  const ok = await actions.approveSavedTimetable(7, 1, { status: "idle" }, form({ reason: " Exam board\napproval " }));
  assert.equal(ok.status, "success");
  assert.match(ok.message, /not published/);
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], "/api/v1/timetables/7/approve");
  assert.deepEqual(calls[0][1].body, { expected_revision: 1, reason: "Exam board approval" });
  assert.equal(calls[0][1].method, "POST");
  assert.equal(calls[0][1].timeoutMs, 120_000);
  assert.ok(!calls.some(([url]) => url.includes("/publish")));
  assert.equal(refreshes(), 1);
  assert.equal((await actions.approveSavedTimetable(0, 1, { status: "idle" }, form({ reason: "Valid reason" }))).status, "error");
  assert.equal(calls.length, 1);
});

test("stale inputs, revisions, permissions and busy services are explained without student identifiers", async () => {
  const stale = workflow({ ok: false, error: { status: 409, code: "STALE", message: "x", problems: [
    { rule: "STALE", message: "Regenerate." }, { rule: "H1", message: `Student ${STUDENT} has overlapping exams.` }] } });
  const answer = await stale.actions.approveSavedTimetable(7, 1, { status: "idle" }, stale.form({ reason: "Board decision" }));
  assert.match(answer.message, /not approved/);
  assert.match(answer.message, /H1: Student \[student identifier omitted\]/);
  assert.ok(!answer.message.includes(STUDENT));
  assert.equal(stale.refreshes(), 0);
  const cases = [
    [{ status: 409, code: "REVISION", message: "x" }, /changed since you opened it/],
    [{ status: 403, code: "HTTP_403", message: "x" }, /examinations:approve/],
    [{ status: 429, code: "HTTP_429", message: "x" }, /busy/],
    [{ status: 503, code: "HTTP_503", message: "x" }, /temporarily unavailable/],
    [{ status: 0, code: "TIMEOUT", message: "x" }, /Reload the page to see whether the change was made/],
  ];
  for (const [error, pattern] of cases) {
    const h = workflow({ ok: false, error });
    assert.match((await h.actions.approveSavedTimetable(7, 1, { status: "idle" }, h.form({ reason: "Board decision" }))).message, pattern);
  }
});

test("publish needs PUBLISH, archives the published timetable only when chosen, and explains conflicts", async () => {
  const h = workflow(changed({ archived_timetable: { draft_number: 1 } }));
  const unconfirmed = await h.actions.publishSavedTimetable(7, 2, { status: "idle" }, h.form({ reason: "Board decision", confirm: "publish" }));
  assert.equal(unconfirmed.status, "error");
  assert.ok(unconfirmed.fieldErrors.confirm);
  assert.equal(h.calls.length, 0);
  await h.actions.publishSavedTimetable(7, 2, { status: "idle" }, h.form({ reason: "Board decision", confirm: "PUBLISH", replace_published: "keep" }));
  assert.deepEqual(h.calls[0][1].body, { expected_revision: 2, reason: "Board decision", replace_published: false });
  const archived = await h.actions.publishSavedTimetable(7, 2, { status: "idle" }, h.form({ reason: "Board decision", confirm: "PUBLISH", replace_published: "archive" }));
  assert.equal(h.calls[1][0], "/api/v1/timetables/7/publish");
  assert.equal(h.calls[1][1].body.replace_published, true);
  assert.match(archived.message, /Draft #1 was archived/);
  const conflict = workflow({ ok: false, error: { status: 409, code: "ALREADY_PUBLISHED", message: "x" } });
  assert.match((await conflict.actions.publishSavedTimetable(7, 2, { status: "idle" }, conflict.form({ reason: "Board decision", confirm: "PUBLISH" }))).message,
    /already published/);
});

// --- Generate & Save Draft --------------------------------------------------------------------------

function saveAction(response) {
  const calls = [];
  const actions = load("src/lib/examination-generation-actions.ts", {
    "@/lib/examination-evaluation": load("src/lib/examination-evaluation.ts", {}),
    "@/lib/examination-settings": load("src/lib/examination-settings.ts", {}),
    "@/lib/examination-calendar": load("src/lib/examination-calendar.ts", {}),
    "@/lib/examination-access": { requireExaminationAdmin: async () => {} },
    "@/lib/examination": {
      listExamModules: async () => ({ ok: true, data: [] }),
      listExamPeriods: async () => ({ ok: true, data: [{ period_code: "FINAL-CYCLE", exam_kind: "FINAL" }, { period_code: "MID-CYCLE", exam_kind: "MID" }] }),
    },
    "@/lib/examination-api": { examinationRequest: async (...args) => { calls.push(args); return response; } },
  });
  const save = (fields = {}) => {
    const data = new FormData();
    for (const [key, value] of Object.entries({ period_code: "FINAL-CYCLE", mode: "optimized", time_limit_seconds: "30", confirm_save: "yes", ...fields })) data.set(key, value);
    return actions.saveFinalTimetableDraft({ status: "idle" }, data);
  };
  return { save, calls };
}

test("saving a draft needs explicit confirmation and posts a fresh generation to /timetables/drafts", async () => {
  const h = saveAction({ ok: true, data: { timetable_id: 12, draft_number: 4, period_code: "FINAL-CYCLE", status: "DRAFT" } });
  assert.equal((await h.save({ confirm_save: "" })).status, "error");
  assert.equal((await h.save({ period_code: "MID-CYCLE" })).status, "error");
  assert.equal(h.calls.length, 0);
  const saved = await h.save();
  assert.deepEqual(saved, { status: "saved", timetableId: 12, draftNumber: 4, periodCode: "FINAL-CYCLE" });
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0][0], "/api/v1/timetables/drafts");
  assert.deepEqual(h.calls[0][1].body, { period_code: "FINAL-CYCLE", mode: "optimized", time_limit_seconds: 30 });
  assert.equal(h.calls[0][1].timeoutMs, 360_000);
});

test("Save Draft forwards the same supported timetable preferences without a global PUT", async () => {
  const h = saveAction({ ok: true, data: { timetable_id: 12, draft_number: 4, period_code: "FINAL-CYCLE", status: "DRAFT" } });
  const preferences = { scheduling: { minimum_break_minutes: 90 }, seating: { default_percentage: 60 } };
  assert.equal((await h.save({ scheduling_preferences: JSON.stringify(preferences) })).status, "saved");
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0][0], "/api/v1/timetables/drafts");
  assert.equal(h.calls[0][1].method, "POST");
  assert.deepEqual(h.calls[0][1].body.scheduling_preferences, preferences);
  const refused = saveAction({ ok: true, data: {} });
  assert.equal((await refused.save({ scheduling_preferences: '{"H1":false}' })).status, "error");
  assert.equal(refused.calls.length, 0);
});

test("a refused or interrupted save says what happened and never invents a draft", async () => {
  const refused = saveAction({ ok: false, error: { status: 422, code: "NOT_SAVED", message: "x",
    problems: [{ rule: "NOT_SAVED", message: "Not saved." }, { rule: "H1", message: `Overlap for ${STUDENT}` }] } });
  const answer = await refused.save();
  assert.equal(answer.status, "error");
  assert.match(answer.message, /Nothing was saved/);
  assert.ok(!answer.message.includes(STUDENT));
  const timeout = await saveAction({ ok: false, error: { status: 0, code: "TIMEOUT", message: "Too slow." } }).save();
  assert.match(timeout.message, /may have been saved/);
  const busy = await saveAction({ ok: false, error: { status: 429, code: "HTTP_429", message: "Busy." } }).save();
  assert.equal(busy.title, "The timetable solver is busy");
  const odd = await saveAction({ ok: true, data: { timetable_id: 5, draft_number: 1, period_code: "FINAL-CYCLE", status: "PUBLISHED" } }).save();
  assert.equal(odd.status, "error");
});

test("the save card explains the fresh generation and stays disabled until confirmed", () => {
  const icons = load("src/components/icon.tsx", { "react/jsx-runtime": jsxRuntime });
  const ui = load("src/components/console/ui.tsx", { "react/jsx-runtime": jsxRuntime, "next/link": { default: (p) => createElement("a", p) }, "@/components/icon": icons });
  const { SaveDraftCard } = load("src/app/(examination)/exams/generate/_components/save-draft.tsx", {
    "react/jsx-runtime": jsxRuntime, "next/link": { default: (p) => createElement("a", p) }, "@/components/console/ui": ui, "@/components/icon": icons,
    "./period-calendar": { primaryButton: "primary" },
  });
  const props = { periodName: "Semester 1", mode: "feasibility", timeLimit: "", onConfirm: () => {}, onSave: () => {}, disabled: false, saving: false, state: { status: "idle" } };
  const unconfirmed = renderToStaticMarkup(createElement(SaveDraftCard, { ...props, confirmed: false }));
  assert.match(unconfirmed, /Saving runs a fresh generation/);
  assert.match(unconfirmed, /can differ from any preview/);
  assert.match(unconfirmed, /<button[^>]*disabled=""[^>]*>.*Generate &amp; Save Draft/);
  const confirmed = renderToStaticMarkup(createElement(SaveDraftCard, { ...props, confirmed: true }));
  assert.doesNotMatch(confirmed, /<button[^>]*disabled=""/);
  const saving = renderToStaticMarkup(createElement(SaveDraftCard, { ...props, confirmed: true, disabled: true, saving: true }));
  assert.match(saving, /Generating and saving…/);
});
