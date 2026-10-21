import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import ts from "typescript";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import * as jsxRuntime from "react/jsx-runtime";

// Existing Node/TypeScript test pattern. All API calls and authorization are mocked; no database access.
function load(source, mocks = {}) {
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

const helpers = load("src/lib/examination-module-catalog.ts");
const BASE = {
  module_code: "IT1010", module_name: "Introduction to Programming", academic_year: 1, semester: 1,
  specializations: ["IT", "SE"], credits: 4, duration_minutes: 120, difficulty: 3, candidate_count: 82,
  exam_type: "written", has_final_exam: true, has_mid_exam: false, mid_duration_minutes: null,
  has_extra_reading_time: false, reading_time_minutes: 0, delivery_mode: "regular", block_category: null,
};
const MODULES = [BASE,
  { ...BASE, module_code: "SE3020", module_name: "Distributed Systems", academic_year: 3, semester: 2, specializations: ["SE"], candidate_count: 0 },
  { ...BASE, module_code: "IT2040", module_name: "Database Systems", academic_year: 2, semester: 2, credits: null, difficulty: null },
];
const filters = (params = {}) => helpers.readCatalogFilters(params);
const codes = (rows) => rows.map((item) => item.module_code);

test("catalog search and combined filters use returned metadata without changing records", () => {
  const before = structuredClone(MODULES);
  assert.deepEqual(codes(helpers.filterCatalog(MODULES, filters())), ["IT1010", "IT2040", "SE3020"]);
  assert.deepEqual(codes(helpers.filterCatalog(MODULES, filters({ q: "  PROGRAMMING " }))), ["IT1010"]);
  assert.deepEqual(codes(helpers.filterCatalog(MODULES, filters({ q: "se30", year: "3", semester: "2", specialization: "SE" }))), ["SE3020"]);
  assert.deepEqual(codes(helpers.filterCatalog(MODULES, filters({ missing: "yes" }))), ["IT2040"]);
  assert.deepEqual(helpers.filterCatalog(MODULES, filters({ year: "4" })), []);
  assert.deepEqual(helpers.filterCatalog(MODULES, filters({ specialization: "UNKNOWN" })), []);
  assert.deepEqual(helpers.catalogOptions(MODULES), { years: [1, 2, 3], semesters: [1, 2], specializations: ["IT", "SE"] });
  assert.deepEqual(filters({ year: ["2", "3"], semester: "3", missing: "false" }), { q: "", year: "2", semester: "", specialization: "", missing: "" });
  assert.deepEqual(MODULES, before);
});

test("unknown metadata stays missing, zero registrations stays zero, and disabled FINAL is not missing", () => {
  assert.deepEqual(helpers.missingModuleFields(BASE), []);
  assert.equal(helpers.moduleValue({ ...BASE, candidate_count: 0 }, "candidate_count"), "0");
  assert.equal(helpers.moduleValue({ ...BASE, has_final_exam: false, duration_minutes: null }, "duration_minutes"), "No FINAL exam");
  const missing = { module_code: "IT9999", specializations: [], candidate_count: null };
  assert.deepEqual(helpers.missingModuleFields(missing), Object.values(helpers.MODULE_COLUMNS));
  assert.deepEqual(helpers.catalogOptions([missing]), { years: [], semesters: [], specializations: [] });
  assert.equal(helpers.moduleValue({ ...BASE, difficulty: 0 }, "difficulty"), null);
  assert.equal(helpers.moduleValue({ ...BASE, credits: 0 }, "credits"), null);
});

const link = ({ children, ...props }) => {
  delete props.prefetch;
  return React.createElement("a", props, children);
};
const icons = load("src/components/icon.tsx", { "react/jsx-runtime": jsxRuntime });
const ui = load("src/components/console/ui.tsx", {
  "react/jsx-runtime": jsxRuntime, "next/link": { default: link }, "@/components/icon": icons,
});
const values = load("src/app/(examination)/exams/modules/_components/module-values.tsx", {
  "react/jsx-runtime": jsxRuntime, "@/components/console/ui": ui, "@/lib/examination-module-catalog": helpers,
});
const refresh = { RefreshButton: ({ label }) => React.createElement("button", { type: "button" }, label) };
const errors = load("src/app/(examination)/exams/_components/read-error.tsx", {
  "react/jsx-runtime": jsxRuntime, "@/components/console/ui": ui, "./refresh-button": refresh,
});

async function resolve(element) {
  if (Array.isArray(element)) return React.Children.toArray(await Promise.all(element.map(resolve)));
  if (!React.isValidElement(element)) return element;
  if (typeof element.type === "function") return resolve(await element.type(element.props));
  if (element.type === React.Suspense) return resolve(element.props.children);
  const children = await resolve(element.props.children);
  return React.createElement(element.type, { ...element.props, key: element.key }, ...(Array.isArray(children) ? children : [children]));
}

function pages({ result = { ok: true, data: MODULES }, detail = { ok: true, data: BASE }, authorized = true } = {}) {
  const calls = [];
  const guard = { requireExaminationAdmin: async () => {
    calls.push("authorize");
    if (!authorized) throw new Error("unauthorized");
  } };
  const api = {
    listExamModules: async () => { calls.push("list"); return result; },
    getExamModule: async (code) => { calls.push(["detail", code]); return detail; },
  };
  const common = { "react/jsx-runtime": jsxRuntime, react: React, "next/link": { default: link },
    "@/components/console/ui": ui, "@/lib/examination-access": guard, "@/lib/examination": api,
    "@/lib/examination-module-catalog": helpers };
  const catalog = load("src/app/(examination)/exams/modules/page.tsx", { ...common,
    "next/form": { default: (props) => React.createElement("form", props) },
    "../_components/read-error": errors, "../_components/refresh-button": refresh, "./_components/module-values": values,
  });
  const details = load("src/app/(examination)/exams/modules/[code]/page.tsx", { ...common,
    "next/navigation": { notFound: () => { throw new Error("not-found"); } },
    "../../_components/read-error": errors, "../../_components/refresh-button": refresh, "../_components/module-values": values,
  });
  return { calls,
    catalog: async (params = {}) => renderToStaticMarkup(await resolve(React.createElement(catalog.default, { searchParams: Promise.resolve(params) }))),
    detail: async (code = "IT1010") => renderToStaticMarkup(await resolve(React.createElement(details.default, { params: Promise.resolve({ code }) }))),
  };
}

test("Modules page displays existing records, accessible filters, missing badges and read-only details links", async () => {
  const h = pages();
  const html = await h.catalog();
  for (const text of ["Modules", "Read only", "Search modules", "Academic year", "Semester", "Specialization", "Only modules with missing data",
    "Module code", "Credits", "FINAL duration", "Difficulty", "Registrations", "120 min", "3 / 5", "82", "SE3020", "Not available", "1 with missing data",
    'href="/exams/modules/IT1010"', 'aria-label="View details for IT1010"', 'scope="col"', 'scope="row"']) assert.ok(html.includes(text), text);
  assert.ok(!html.includes("Coming Soon"));
  assert.ok(!/type="submit"[^>]*>.*(?:Save|Create|Update)/.test(html));
  assert.deepEqual(h.calls, ["authorize", "list"]);
  const filtered = await pages().catalog({ q: "distributed", semester: "2" });
  assert.ok(filtered.includes("Distributed Systems"));
  assert.ok(!filtered.includes("Introduction to Programming"));
});

test("catalog distinguishes empty, no matches, failures and pages outside the result set", async () => {
  assert.match(await pages({ result: { ok: true, data: [] } }).catalog(), /No examination modules available/);
  assert.match(await pages().catalog({ q: "not-a-module" }), /No modules match these filters/);
  assert.match(await pages().catalog({ page: "999" }), /This page is empty/);
  for (const status of [403, 503]) {
    const html = await pages({ result: { ok: false, error: { status, message: "Service unavailable" } } }).catalog();
    assert.match(html, /Couldn&#x27;t load modules/);
    assert.ok(!html.includes("No examination modules available"));
    assert.ok(!html.includes("IT1010"));
    if (status === 403) assert.match(html, /does not have access/);
  }
});

test("pagination preserves filters and lists each module once per page", async () => {
  const many = Array.from({ length: 30 }, (_, i) => ({ ...BASE, module_code: `IT${String(1000 + i)}`, credits: null }));
  const h = pages({ result: { ok: true, data: many } });
  const first = await h.catalog({ q: "IT", missing: "yes", year: "1", specialization: "SE" });
  assert.match(first, /aria-label="Next page"/);
  assert.match(first, /q=IT&amp;year=1&amp;specialization=SE&amp;missing=yes&amp;page=2/);
  const second = await h.catalog({ q: "IT", missing: "yes", year: "1", specialization: "SE", page: "2" });
  assert.equal((second.match(/aria-label="View details for/g) ?? []).length, 5);
  assert.ok(second.includes("IT1025"));
  assert.ok(!second.includes("IT1000"));
});

test("module details shows recorded data and distinguishes not applicable, unknown and zero", async () => {
  const h = pages();
  const html = await h.detail();
  for (const text of ["IT1010", "Introduction to Programming", "Credits", "120 min", "3 / 5", "Registrations", "82", "written", "No MID exam", "0 min", "Not applicable", "Read only", "Back to modules"]) assert.ok(html.includes(text), text);
  assert.ok(!html.includes("<form"));
  assert.deepEqual(h.calls, ["authorize", ["detail", "IT1010"]]);
  const missing = await pages({ detail: { ok: true, data: { module_code: "IT1010", candidate_count: 0 } } }).detail();
  assert.match(missing, /Missing data:/);
  assert.match(missing, /Module name not available/);
  assert.match(missing, /Not available/);
  assert.ok(!missing.includes("120 min"));
  assert.match(missing, />0<\/dd>/);
});

test("both routes authorize before reading; invalid and absent modules use not-found", async () => {
  for (const route of ["catalog", "detail"]) {
    const denied = pages({ authorized: false });
    await assert.rejects(denied[route](), /unauthorized/);
    assert.deepEqual(denied.calls, ["authorize"]);
  }
  const invalid = pages();
  await assert.rejects(invalid.detail("../settings"), /not-found/);
  assert.deepEqual(invalid.calls, ["authorize"]);
  await assert.rejects(pages({ detail: { ok: false, error: { status: 404 } } }).detail(), /not-found/);
  const forbidden = await pages({ detail: { ok: false, error: { status: 403, message: "Forbidden" } } }).detail();
  assert.match(forbidden, /does not have access/);
  assert.ok(!forbidden.includes("Registrations"));
});

test("module reads reuse the authenticated server API and never request registration rows or write methods", async () => {
  const calls = [];
  const api = load("src/lib/examination.ts", { "server-only": {}, react: { cache: (fn) => fn },
    "@/lib/examination-api": { examinationRequest: async (...args) => { calls.push(args); return { ok: true, data: BASE }; } },
  });
  await api.listExamModules();
  await api.getExamModule("IT1010");
  assert.equal(calls[0][0], "/api/v1/modules");
  assert.equal(calls[1][0], "/api/v1/modules/IT1010");
  assert.match(calls[1][1].sessionExpiredPath, /callbackUrl=%2Fexams%2Fmodules%2FIT1010/);
  for (const [url, options] of calls) {
    assert.ok(!url.includes("registrations"));
    assert.equal(options?.method ?? "GET", "GET");
    assert.equal(options?.body, undefined);
  }
});
