import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import ts from "typescript";
import * as React from "react";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import * as jsxRuntime from "react/jsx-runtime";

// Step 3 module browser: display-only filters over real-shaped mapped modules. No requests are made.

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

const browser = load("src/lib/examination-module-browser.ts", {});
const icons = load("src/components/icon.tsx", { "react/jsx-runtime": jsxRuntime });
const ui = load("src/components/console/ui.tsx", { "react/jsx-runtime": jsxRuntime, "next/link": { default: (p) => createElement("a", p) }, "@/components/icon": icons });
const { AcademicGroups } = load("src/app/(examination)/exams/generate/_components/academic-groups.tsx", {
  "react/jsx-runtime": jsxRuntime, react: React, "@/components/console/ui": ui, "@/components/icon": icons,
  "@/lib/examination-module-browser": browser, "./period-calendar": { fieldClass: "field", secondaryButton: "secondary" },
});

const module = (code, name, year, semester, specializations, total, repeat, extra = {}) => ({
  module_code: code, module_name: name, academic_year: year, semester, specializations, delivery_mode: "regular",
  candidate_count: total, repeat_candidates: repeat, normal_candidates: repeat === null ? null : total - repeat, ...extra,
});
const MODULES = [
  module("IT1010", "Introduction to Programming", 1, 2, ["CYBER", "DS", "IT", "SE"], 118, 4),
  module("IT2040", "Database Management Systems", 2, 2, ["DS", "IT", "SE"], 90, 6, { delivery_mode: "block" }),
  module("SE3020", "Distributed Systems", 3, 2, ["SE"], 28, 0),
  module("IT1060", "Software Process Modeling", 1, 1, ["IT"], 35, 5),
];
const MAPPING = [
  ...MODULES.slice(0, 3).map((item) => ({ period_code: "C", module_code: item.module_code, candidate_scope: "all" })),
  { period_code: "C", module_code: "IT1060", candidate_scope: "repeat_only" },
  { period_code: "C", module_code: "ZZ9999", candidate_scope: "all" },
];
const filters = (patch) => ({ ...browser.NO_FILTERS, ...patch });
const codes = (rows) => rows.map((row) => row.module_code);

test("year, semester, specialization and search filter the mapped modules for display only", () => {
  assert.deepEqual(codes(browser.filterModules(MODULES, browser.NO_FILTERS)), ["IT1060", "IT1010", "IT2040", "SE3020"]);
  assert.deepEqual(codes(browser.filterModules(MODULES, filters({ year: 1 }))), ["IT1060", "IT1010"]);
  assert.deepEqual(codes(browser.filterModules(MODULES, filters({ year: 1, semester: 2 }))), ["IT1010"]);
  assert.deepEqual(codes(browser.filterModules(MODULES, filters({ specialization: "SE" }))), ["IT1010", "IT2040", "SE3020"]);
  assert.deepEqual(codes(browser.filterModules(MODULES, filters({ search: " database " }))), ["IT2040"]);
  assert.deepEqual(codes(browser.filterModules(MODULES, filters({ search: "se30" }))), ["SE3020"]);
  assert.deepEqual(browser.filterModules(MODULES, filters({ year: 4 })), []);
  assert.deepEqual(browser.yearCounts(MODULES, filters({ semester: 2, year: 3 })), { 1: 1, 2: 1, 3: 1, 4: 0 });
  assert.deepEqual(browser.moduleSpecializations(MODULES), ["CYBER", "DS", "IT", "SE"]);
  assert.deepEqual(browser.moduleSemesters(MODULES), [1, 2]);
  assert.equal(browser.isFiltered(browser.NO_FILTERS), false);
  assert.equal(browser.isFiltered(filters({ search: "  " })), false);
});

test("candidate counts are exact per module, repeat-only modules examine repeats only, and unknown splits stay unknown", () => {
  assert.deepEqual(browser.cycleCandidates(MODULES[0], "all"), { normal: 114, repeat: 4, total: 118, repeatOnly: false });
  assert.deepEqual(browser.cycleCandidates(MODULES[3], "repeat_only"), { normal: 0, repeat: 5, total: 5, repeatOnly: true });
  const unknown = module("IT9000", "Unknown split", 1, 1, ["IT"], 20, null);
  assert.deepEqual(browser.cycleCandidates(unknown, "all"), { normal: null, repeat: null, total: 20, repeatOnly: false });
  assert.deepEqual(browser.cycleCandidates(unknown, "repeat_only"), { normal: 0, repeat: null, total: null, repeatOnly: true });
});

test("the module browser shows real details, counts and the full-generation reminder", () => {
  const html = renderToStaticMarkup(createElement(AcademicGroups, { modules: MODULES, mapping: MAPPING }));
  for (const text of ["All years", "Year 1", "Year 4", "All semesters", "Semester 1", "Semester 2", "All specializations", "Search modules",
    "All 5 mapped modules are generated", "Showing <span", ">4</span> of <span", ">5</span> mapped modules",
    "IT1010", "Introduction to Programming", "Year 1 · Semester 2", "CYBER", "block", "114", "118",
    "Repeat candidates only", "Mapped modules without catalog details", "ZZ9999", "never split by specialization"]) {
    assert.ok(html.includes(text), text);
  }
  assert.ok(!html.includes("Clear filters"), "no filters are active initially");
  const unsplit = renderToStaticMarkup(createElement(AcademicGroups, { modules: [module("IT1010", "Programming", 1, 2, ["IT"], 118, null)], mapping: MAPPING.slice(0, 1) }));
  assert.ok(unsplit.includes("could not be read, so only the total is shown"));
  const empty = renderToStaticMarkup(createElement(AcademicGroups, { modules: [], mapping: MAPPING }));
  assert.ok(empty.includes("No module details available"));
});
