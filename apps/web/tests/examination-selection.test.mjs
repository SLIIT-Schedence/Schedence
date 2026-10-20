import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import * as jsxRuntime from "react/jsx-runtime";

// Step 1 "Select Examination": real-shaped period options only; no requests are made.

function load(source, mocks) {
  const code = ts.transpileModule(readFileSync(path.join(import.meta.dirname, "..", source), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const testModule = { exports: {} };
  new Function("require", "module", "exports", code)((name) => {
    assert.ok(Object.hasOwn(mocks, name), `Unexpected dependency: ${name}`);
    return mocks[name];
  }, testModule, testModule.exports);
  return testModule.exports;
}

const selection = load("src/lib/examination-selection.ts", {});
const icons = load("src/components/icon.tsx", { "react/jsx-runtime": jsxRuntime });
const link = { default: (props) => createElement("a", props) };
const ui = load("src/components/console/ui.tsx", { "react/jsx-runtime": jsxRuntime, "next/link": link, "@/components/icon": icons });
const { ExamSelection } = load("src/app/(examination)/exams/generate/_components/exam-selection.tsx", {
  "react/jsx-runtime": jsxRuntime, "@/components/console/ui": ui, "@/components/icon": icons,
  "@/lib/examination-selection": selection, "./period-calendar": { fieldClass: "field" },
});

const option = (changes) => ({
  period_code: "P-S2-FINAL", period_name: "Semester 2 Finals", academic_year_label: "2026/2027", semester: 2, exam_kind: "FINAL",
  start_date: "2026-11-16", end_date: "2026-11-28", exam_days: 9, module_count: 42, registration_count: 1700, ...changes,
});
const OPTIONS = [
  option({}),
  option({ period_code: "P-S1-FINAL", period_name: "Semester 1 Finals", semester: 1, start_date: "2026-06-15", end_date: "2026-07-03", exam_days: 14, module_count: 46, registration_count: 1877 }),
  option({ period_code: "OLD-S2-FINAL", period_name: "Older finals", academic_year_label: "2025/2026", start_date: "2025-11-17", end_date: "2025-11-29" }),
];
const render = (choice, options = OPTIONS) =>
  renderToStaticMarkup(createElement(ExamSelection, { options, choice, onChange: () => {}, disabled: false }));

test("academic years and the first selection come from the period data", () => {
  assert.deepEqual(selection.academicYears(OPTIONS), ["2026/2027", "2025/2026"]);
  assert.deepEqual(selection.initialChoice(OPTIONS), { year: "2026/2027", kind: "FINAL", semester: null, periodCode: "" });
  const midOnly = [option({ exam_kind: "MID", period_code: "P-MID" })];
  assert.equal(selection.initialChoice(midOnly).kind, "MID");
  assert.deepEqual(selection.initialChoice([]), { year: "", kind: "FINAL", semester: null, periodCode: "" });
});

test("year, type and semester select the matching period only when exactly one exists", () => {
  assert.equal(selection.updateChoice(OPTIONS, { year: "2026/2027", kind: "FINAL", semester: 1 }).periodCode, "P-S1-FINAL");
  assert.equal(selection.updateChoice(OPTIONS, { year: "2026/2027", kind: "MID", semester: 1 }).periodCode, "");
  const twice = [...OPTIONS, option({ period_code: "P-S2-FINAL-B", period_name: "Supplementary finals", start_date: "2026-12-01", end_date: "2026-12-05" })];
  assert.equal(selection.updateChoice(twice, { year: "2026/2027", kind: "FINAL", semester: 2 }).periodCode, "");
  assert.deepEqual(selection.matchExaminations(twice, { year: "2026/2027", kind: "FINAL", semester: 2 }).map((item) => item.period_code), ["P-S2-FINAL", "P-S2-FINAL-B"]);
  assert.equal(selection.isGeneratable(option({ exam_kind: "MID" })), false);
});

test("registrations are counted only when the mapping makes the number exact", () => {
  const catalog = [{ module_code: "A", candidate_count: 10 }, { module_code: "B", candidate_count: 5 }];
  assert.equal(selection.cycleRegistrations([{ module_code: "A", candidate_scope: "all" }, { module_code: "B", candidate_scope: "all" }], catalog), 15);
  assert.equal(selection.cycleRegistrations([{ module_code: "A", candidate_scope: "repeat_only" }], catalog), null);
  assert.equal(selection.cycleRegistrations([{ module_code: "Z", candidate_scope: "all" }], catalog), null);
  assert.equal(selection.cycleRegistrations([], catalog), 0);
});

test("the selected examination summary shows real values with the period code as secondary information", () => {
  const html = render({ year: "2026/2027", kind: "FINAL", semester: 1, periodCode: "P-S1-FINAL" });
  for (const text of ["1. Select Examination", "Academic year", "2025/2026", "FINAL examination", "MID examination", "Semester 1", "Semester 2",
    "Selected examination", "Semester 1 Finals", "14 allowed exam days", "46", "1,877", "P-S1-FINAL"]) assert.ok(html.includes(text), text);
  assert.ok(html.includes("None configured in 2026/2027"), "MID availability comes from the data");
});

test("no match says the period is not configured; several matches ask the admin to choose", () => {
  const none = render({ year: "2025/2026", kind: "FINAL", semester: 1, periodCode: "" });
  assert.ok(none.includes("Examination period not configured"));
  assert.ok(!none.includes("Selected examination"));
  const twice = [...OPTIONS, option({ period_code: "P-S2-FINAL-B", period_name: "Supplementary finals" })];
  const choose = render({ year: "2026/2027", kind: "FINAL", semester: 2, periodCode: "" }, twice);
  assert.ok(choose.includes("2 examinations match"));
  assert.ok(choose.includes("Supplementary finals") && choose.includes("Semester 2 Finals"));
});

test("a MID period is shown truthfully but cannot be generated here; unknown counts are not invented", () => {
  const mid = [option({ exam_kind: "MID", period_code: "P-MID", period_name: "Semester 2 Mid", module_count: null, registration_count: null })];
  const html = render({ year: "2026/2027", kind: "MID", semester: 2, periodCode: "P-MID" }, mid);
  assert.ok(html.includes("MID timetable generation is not available in this wizard yet"));
  assert.ok(html.includes("Not available") && html.includes("Not accurately available"));
});
