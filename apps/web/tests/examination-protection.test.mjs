import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import * as jsxRuntime from "react/jsx-runtime";

// API fixtures only. No server, credentials, database connection or mutations.
function load(source, mocks = {}) {
  const code = ts.transpileModule(readFileSync(path.join(import.meta.dirname, "..", source), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const result = { exports: {} };
  new Function("require", "module", "exports", code)((name) => {
    assert.ok(Object.hasOwn(mocks, name), `Unexpected dependency: ${name}`);
    return mocks[name];
  }, result, result.exports);
  return result.exports;
}
const helpers = load("src/lib/examination-timetables.ts");
const lib = load("src/lib/examination-protection.ts", { "@/lib/examination-timetables": helpers });
const STUDENT = "IT30123456";
const detail = (changes = {}) => ({
  timetable_id: 7, period_code: "P-S1-FINAL", period_name: "Semester 1", status: "PUBLISHED", draft_number: 2,
  created_at: "2026-10-01", inputs: { start_date: "2026-11-16", end_date: "2026-11-28" },
  sessions: [{ date: "2026-11-16", start_time: "08:30:00", end_time: "10:30:00", module_code: "IT1001",
    rooms: [{ venue_id: "R1" }, { venue_id: "R2" }], seats: [{ student_id: STUDENT }] }], ...changes,
});
const preview = (details = [detail()], start = "2026-11-16", end = "2026-11-28", gap = 60, incomplete = false) =>
  lib.protectionPreview(details, "P-S2-FINAL", start, end, gap, incomplete);
const summary = (changes = {}) => ({ ...preview(), shared_students: 12, student_conflict_status: "clear", venue_conflicts: 0,
  student_conflicts: 0, overlapping_drafts: 2, protected_fingerprint: "abc", blocking_issues: [], ...changes });

test("only protected other cycles appear; multi-room slots are kept without seating records", () => {
  const data = preview([detail(), detail({ timetable_id: 8, status: "DRAFT" }),
    detail({ timetable_id: 9, status: "ARCHIVED" }), detail({ timetable_id: 10, period_code: "P-S2-FINAL" })]);
  assert.equal(data.protected_timetables.length, 1);
  assert.deepEqual(data.occupied_slots.map((s) => s.venue_id), ["R1", "R2"]);
  assert.equal(data.occupied_slot_total, 2);
  assert.ok(!JSON.stringify(data).includes(STUDENT));
  assert.equal(data.shared_students, undefined); // Never infer student overlap from module or semester metadata.
});

test("selected dates and minimum-break boundary buffer determine the slots", () => {
  assert.equal(preview(undefined, "2026-12-01", "2026-12-02").protected_timetables.length, 0);
  const before = detail({ inputs: { start_date: "2026-11-15", end_date: "2026-11-15" },
    sessions: [{ date: "2026-11-15", start_time: "22:30:00", end_time: "23:30:00", module_code: "IT1001", rooms: [{ venue_id: "R1" }] }] });
  assert.equal(preview([before], "2026-11-16", "2026-11-16", 0).occupied_slot_total, 0);
  assert.equal(preview([before], "2026-11-16", "2026-11-16", 60).occupied_slot_total, 1);
  const after = detail({ inputs: { start_date: "2026-11-17", end_date: "2026-11-17" },
    sessions: [{ date: "2026-11-17", start_time: "00:30:00", end_time: "01:30:00", module_code: "IT1001", rooms: [{ venue_id: "R1" }] }] });
  assert.equal(preview([after], "2026-11-16", "2026-11-16", 60).occupied_slot_total, 1);
});

test("generation summary retains every backend metric and redacts identifiers and unknown fields", () => {
  const raw = summary({ blocking_issues: [`X2: Student ${STUDENT} needs a break.`], student_ids: [STUDENT] });
  const result = lib.publicProtection(raw);
  assert.equal(result.shared_students, 12);
  assert.equal(result.protected_fingerprint, "abc");
  assert.match(result.blocking_issues[0], /student identifier omitted/);
  assert.ok(!JSON.stringify(result).includes(STUDENT));
  assert.equal(result.student_ids, undefined);
  assert.equal(lib.publicProtection(undefined), null);
});

function action(response, guard = async () => {}) {
  const calls = [];
  const actions = load("src/lib/examination-protection-actions.ts", {
    "@/lib/examination-access": { requireExaminationAdmin: guard },
    "@/lib/examination-protection": lib,
    "@/lib/examination-api": { examinationRequest: async (...args) => { calls.push(args); return response(...args); } },
  });
  return { calls, run: (code = "P-S2-FINAL", start = "2026-11-16", end = "2026-11-28", gap = 60) =>
    actions.loadProtectionPreview(code, start, end, gap) };
}

test("preview reuses only existing read APIs, includes MID protection and excludes the selected cycle", async () => {
  const a = action((url, options) => ({ ok: true, data: url.endsWith("/timetables") ? {
    total: options.query.status === "APPROVED" ? 0 : 2,
    items: options.query.status === "APPROVED" ? [] : [detail({ period_code: "P-S1-MID" }), detail({ timetable_id: 8, period_code: "P-S2-FINAL" })],
  } : detail({ period_code: "P-S1-MID" }) }));
  const answer = await a.run();
  assert.equal(answer.ok, true);
  assert.equal(answer.data.protected_timetables[0].period_code, "P-S1-MID");
  assert.deepEqual(a.calls.map(([url]) => url), ["/api/v1/timetables", "/api/v1/timetables", "/api/v1/timetables/7"]);
  assert.ok(a.calls.every(([, options]) => !options?.method && !options?.body && !options?.query?.exam_kind));
  assert.ok(!JSON.stringify(answer).includes(STUDENT));
});

test("authorization and invalid calendar checks happen before fetching", async () => {
  const denied = action(() => assert.fail("No request expected"), async () => { throw new Error("denied"); });
  await assert.rejects(denied.run(), /denied/);
  for (const args of [["../x"], ["P-S2-FINAL", "2026-02-30"], ["P-S2-FINAL", "2026-12-01", "2026-11-01"],
    ["P-S2-FINAL", "2026-11-16", "2026-11-28", 1441]]) {
    const a = action(() => assert.fail("No request expected"));
    assert.equal((await a.run(...args)).error.status, 422);
    assert.equal(a.calls.length, 0);
  }
});

test("failed reads and bounded previews cannot masquerade as an all-clear result", async () => {
  const unavailable = action(() => ({ ok: false, error: { status: 503, code: "HTTP_503", message: "Unavailable" } }));
  assert.equal((await unavailable.run()).ok, false);
  const partial = action((url, options) => url.endsWith("/timetables") ? { ok: true, data: {
    total: 200, items: options.query.status === "APPROVED" ? Array.from({ length: 50 }, (_, i) => detail({ timetable_id: i + 1 })) : [],
  } } : { ok: false, error: { status: 403 } });
  const answer = await partial.run();
  assert.equal(answer.data.incomplete, true);
  assert.equal(answer.data.protected_timetables.length, 0);
  assert.equal(partial.calls.length, 27); // Two lists, at most 25 details.
  assert.match(answer.data.notices[0], /incomplete/);
});

const { ProtectedTimetables } = load("src/app/(examination)/exams/generate/_components/protected-timetables.tsx", {
  "react/jsx-runtime": jsxRuntime,
  "next/link": { default: (props) => createElement("a", props) },
  "@/components/console/ui": { Badge: ({ children }) => createElement("span", {}, children), formatDate: (date) => date },
});
const render = (data, resultGenerated) => renderToStaticMarkup(createElement(ProtectedTimetables, { data, resultGenerated }));

test("UI shows linked protected timetables, dates, occupied times, rooms and aggregate student checks", () => {
  const html = render(summary(), true);
  for (const text of ["Semester 1", "/exams/timetables/7", "PUBLISHED", "2026-11-16", "08:30", "10:30", "R1", "R2",
    "12 candidates", "passed cross-cycle validation", "Drafts do not reserve slots"]) assert.ok(html.includes(text), text);
  assert.ok(!html.includes(STUDENT));
});

test("UI distinguishes blocked, unavailable and incomplete results without inventing success", () => {
  const blocked = render(summary({ student_conflict_status: "blocked", blocking_issues: ["X1: Room R1 is occupied."] }), false);
  assert.match(blocked, /No clash-free timetable was established/);
  assert.match(blocked, /X1: Room R1 is occupied/);
  assert.ok(!blocked.includes("passed cross-cycle validation"));
  assert.match(render(null), /unavailable/);
  assert.match(render(preview([], "2026-11-16", "2026-11-28", 60, true)), /preview is incomplete/);
  assert.match(render(preview()), /not a room reservation or a student-clash check/);
});
