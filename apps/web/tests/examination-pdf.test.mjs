import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import test from "node:test";
import { inflateSync } from "node:zlib";
import ts from "typescript";
import { metadata, emptyFilters, sessions } from "./examination-pdf-fixture.mjs";

const require = createRequire(import.meta.url);
function load(source, mocks = {}) {
  const code = ts.transpileModule(readFileSync(path.join(import.meta.dirname, "..", source), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const testModule = { exports: {} };
  new Function("require", "module", "exports", code)((name) => mocks[name] ?? require(name), testModule, testModule.exports);
  return testModule.exports;
}
const helpers = load("src/lib/examination-export.ts");
const presentation = load("src/lib/examination-presentation.ts");
function pdfHarness() {
  const cells = [];
  const pdf = load("src/lib/examination-pdf.ts", {
    "./examination-export": helpers,
    "jspdf-autotable": { autoTable: (doc, options) => require("jspdf-autotable").autoTable(doc, {
      ...options, didDrawCell: (data) => cells.push({ page: doc.getNumberOfPages(), section: data.section,
        x: data.cell.x, y: data.cell.y, width: data.cell.width, height: data.cell.height, text: [...data.cell.text] }),
    }) },
  });
  return { ...pdf, cells };
}

function pdfText(doc) {
  const bytes = Buffer.from(doc.output("arraybuffer"));
  assert.equal(bytes.subarray(0, 5).toString(), "%PDF-");
  const binary = bytes.toString("latin1");
  const streams = [...binary.matchAll(/stream\r?\n([\s\S]*?)\r?\nendstream/g)].map((match) => {
    const stream = Buffer.from(match[1], "latin1");
    try { return inflateSync(stream).toString("latin1"); } catch { return stream.toString("latin1"); }
  }).join("\n");
  return { binary, streams };
}

test("Both Semesters exports remain separate documents with their own cycle, semester and room assignments", () => {
  const rows = [1, 2].map((semester) => ({ ...sessions[0], semester, module_code: semester === 1 ? "IT1001" : "IT2001",
    module_name: `Synthetic Semester ${semester}`, rooms: [{ venue_id: `R${semester}`, venue_name: `Room ${semester}`, seats: 15 }] }));
  const names = [];
  for (let i = 0; i < 2; i++) {
    const meta = { ...metadata, periodCode: `LOCAL-S${i + 1}-FINAL`, periodName: `Semester ${i + 1} FINAL`, semester: i + 1 };
    const { streams } = pdfText(pdfHarness().createTimetablePdf([rows[i]], meta, emptyFilters));
    assert.ok(streams.includes(`Examination semester: ${i + 1}`));
    assert.ok(streams.includes(meta.periodCode));
    assert.ok(streams.includes(rows[i].module_code));
    assert.ok(streams.includes(`Room ${i + 1}`));
    assert.equal(streams.includes(rows[1 - i].module_code), false);
    assert.ok(streams.includes("UNSAVED PREVIEW"));
    names.push(helpers.exportFilename(meta, emptyFilters));
  }
  assert.notEqual(names[0], names[1]);
});

test("PDF is real A4, wraps long names, paginates, repeats headers and keeps cells within margins", () => {
  const { createTimetablePdf, cells } = pdfHarness();
  const doc = createTimetablePdf(sessions, metadata, emptyFilters, new Date("2026-10-19T09:00:00Z"));
  assert.ok(Math.abs(doc.internal.pageSize.getWidth() - 297) < 0.1);
  assert.ok(Math.abs(doc.internal.pageSize.getHeight() - 210) < 0.1);
  assert.ok(doc.getNumberOfPages() >= 4);
  for (const cell of cells) {
    assert.ok(cell.x >= 11.9 && cell.x + cell.width <= 285.1, "horizontal bounds");
    assert.ok(cell.y >= 27.9 && cell.y + cell.height <= 190.1, "vertical bounds leave space for header/footer");
  }
  assert.ok(cells.some((cell) => cell.section === "body" && cell.text.join(" ").includes("Advanced Research") && cell.text.length > 3));
  const { streams } = pdfText(doc);
  for (let page = 1; page <= doc.getNumberOfPages(); page++) {
    assert.ok(cells.some((cell) => cell.page === page && cell.section === "head" && cell.text.includes("Exam date")));
    assert.ok(streams.includes(`Page ${page} of ${doc.getNumberOfPages()}`));
  }
  for (const value of ["2026/2027", "Examination semester: 2", "FINAL", "UNSAVED PREVIEW", "TEST000", "TEST069", "2 of 2", "Computing Laboratory B", "12 normal", "3 repeat", "15 total"]) assert.ok(streams.includes(value), value);
  assert.ok(cells.some((cell) => cell.section === "body" && cell.text.join(" ").includes("10 candidates")));
  assert.ok(cells.some((cell) => cell.section === "body" && cell.text.join(" ").includes("5 candidates")));
});

test("filters select export rows without changing full-cycle sessions or multiplying shared-session counts", () => {
  const before = JSON.stringify(sessions);
  const filters = { year: "1", semester: "1", specialization: "Software Engineering", module: "" };
  const rows = presentation.filterSessions(sessions, filters);
  assert.equal(rows.length, 60);
  const groups = helpers.exportGroups(rows);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].rows.length, 60);
  assert.equal(groups[0].rows.reduce((sum, row) => sum + row.candidates, 0), 900);
  const { streams } = pdfText(pdfHarness().createTimetablePdf(rows, metadata, filters));
  assert.ok(streams.includes("Year 1"));
  assert.ok(streams.includes("shared module"));
  assert.ok(streams.includes("TEST059"));
  assert.equal(streams.includes("TEST060"), false);
  assert.equal(JSON.stringify(sessions), before);
  const one = presentation.filterSessions(sessions, { ...filters, module: "TEST000" });
  assert.equal(one.length, 1);
});

test("PDF statuses and absent counts are truthful; private fields and identifiers are omitted", () => {
  const row = structuredClone(sessions[0]);
  row.normal_candidates = undefined; row.repeat_candidates = undefined;
  row.module_name = "Exam IT33123456";
  row.student_id = "IT33123456";
  row.rooms[0].individual_seats = [{ student_id: "IT33123456" }];
  for (const status of ["UNSAVED", "DRAFT", "APPROVED", "PUBLISHED", "ARCHIVED"]) {
    const { streams } = pdfText(pdfHarness().createTimetablePdf([row], { ...metadata, status }, emptyFilters));
    assert.equal(streams.includes("IT33123456"), false);
    assert.equal(streams.includes("individual_seats"), false);
    assert.ok(streams.includes(status));
    assert.ok(streams.includes("not provided normal"));
    assert.ok(streams.includes("not provided repeat"));
    assert.equal(streams.includes("0 normal"), false);
    if (status === "UNSAVED" || status === "DRAFT") assert.ok(streams.includes("Do not use as an official"));
  }
});

test("empty exports and unsupported fonts fail explicitly without misrepresenting names", () => {
  const { createTimetablePdf } = pdfHarness();
  assert.throws(() => createTimetablePdf([], metadata, emptyFilters), /No timetable sessions/);
  assert.throws(() => createTimetablePdf([{ ...sessions[0], module_name: "විභාග" }], metadata, emptyFilters), /characters the PDF font cannot display/);
  assert.match(helpers.exportFilename({ ...metadata, periodCode: "../bad/name" }, emptyFilters), /^[a-zA-Z0-9._-]+\.pdf$/);
});
