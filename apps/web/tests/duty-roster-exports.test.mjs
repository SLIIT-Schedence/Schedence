// Use an existing, read-only Neon export for document integration checks; no data is seeded.
import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import test from "node:test";
import ts from "typescript";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

const require = createRequire(import.meta.url);
function load(source, mocks = {}) {
  const code = ts.transpileModule(readFileSync(path.join(import.meta.dirname, "..", source), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const compiled = { exports: {} };
  new Function("require", "module", "exports", code)((name) => mocks[name] ?? require(name), compiled, compiled.exports);
  return compiled.exports;
}
const fixturePath = process.env.DUTY_ROSTER_EXPORT_JSON;
const data = fixturePath ? JSON.parse(readFileSync(fixturePath, "utf8")) : null;

test("Excel/PDF buttons reference the displayed saved roster and do not accept preview data", () => {
  const { RosterDownloads } = load("src/app/(examination)/exams/duties/rosters/[rosterId]/roster-downloads.tsx", {
    "@/components/icon": { Icon: () => null },
  });
  const html = renderToStaticMarkup(React.createElement(RosterDownloads, { rosterId: data?.roster.roster_id ?? "id", rowVersion: data?.roster.row_version ?? 3 }));
  assert.match(html, /Download Excel/);
  assert.match(html, /Download PDF/);
  assert.equal((html.match(/<button/g) ?? []).length, 2);
  const source = readFileSync(new URL("../src/app/(examination)/exams/duties/rosters/[rosterId]/roster-downloads.tsx", import.meta.url), "utf8");
  assert.match(source, /version=\$\{rowVersion\}/);
  assert.match(source, /response\.ok/);
  assert.match(source, /role="alert"/);
});

test("existing saved assignments form a complete landscape PDF with repeated headers and bounded cells", { skip: !data }, () => {
  const cells = [];
  const { createDutyRosterPdf } = load("src/lib/duty-roster-pdf.ts", {
    "jspdf-autotable": { autoTable: (doc, options) => require("jspdf-autotable").autoTable(doc, {
      ...options, didDrawCell: (cell) => cells.push({ section: cell.section, page: cell.pageNumber, column: cell.column.index,
        x: cell.cell.x, y: cell.cell.y, w: cell.cell.width, h: cell.cell.height, text: cell.cell.text.join(" "), raw: cell.cell.raw }),
    }) },
  });
  const before = JSON.stringify(data);
  const doc = createDutyRosterPdf(data);
  assert.ok(Math.abs(doc.internal.pageSize.getWidth() - 297) < 0.1);
  assert.ok(Math.abs(doc.internal.pageSize.getHeight() - 210) < 0.1);
  assert.ok(doc.getNumberOfPages() > 1);
  const bytes = Buffer.from(doc.output("arraybuffer"));
  assert.equal(bytes.subarray(0, 5).toString(), "%PDF-");
  for (const cell of cells) {
    assert.ok(cell.x >= 11.9 && cell.x + cell.w <= 285.2, `Horizontal clipping: ${cell.text}`);
    assert.ok(cell.y >= 43 && cell.y + cell.h <= 190.1, `Vertical clipping: ${cell.text}`);
  }
  for (let page = 1; page <= doc.getNumberOfPages(); page++) {
    assert.ok(cells.some((c) => c.page === page && c.section === "head" && c.text === "Supervisors"));
  }
  const body = cells.filter((c) => c.section === "body");
  for (const a of data.assignments) {
    const raw = `${a.staff_name ?? "Name not recorded"}\n${a.staff_number ?? a.staff_id}`;
    assert.ok(body.some((c) => String(c.raw).includes(raw)), `Missing saved staff assignment: ${a.staff_id}`);
  }
  const roleCells = body.filter((c) => c.column === 4 || c.column === 5);
  const printedAssignments = roleCells.reduce((count, c) => count + (c.raw === "None assigned" ? 0 : String(c.raw).split("\n\n").length), 0);
  assert.equal(printedAssignments, data.assignments.length);
  assert.equal(body.length / 6, new Set(data.assignments.map((a) => `${a.session_id}|${a.venue_id}`)).size);
  assert.equal(JSON.stringify(data), before);
  writeFileSync(path.join(path.dirname(fixturePath), "roster.pdf"), bytes);
});

test("PDF never labels approved demo data as official and refuses unsupported fonts visibly", { skip: !data }, () => {
  const { createDutyRosterPdf, DutyRosterPdfError } = load("src/lib/duty-roster-pdf.ts");
  const copy = structuredClone(data);
  copy.assignments[0].staff_name = "සිංහල";
  assert.throws(() => createDutyRosterPdf(copy), DutyRosterPdfError);
  const pdf = createDutyRosterPdf(data);
  // jsPDF keeps one independently addressable content stream for every page.
  const streams = Object.values(pdf.internal.pages).flat().join("\n");
  assert.equal((streams.match(/SYNTHETIC DEMO/g) ?? []).length, pdf.getNumberOfPages());
  assert.match(streams, /NOT AN OFFICIAL PUBLISHED ROSTER/);
  for (let page = 1; page <= pdf.getNumberOfPages(); page++) assert.ok(streams.includes(`Page ${page} of ${pdf.getNumberOfPages()}`));
});

test("download routes forward saved-version checks, errors and authenticated reader requests", { skip: !data }, async () => {
  const calls = [];
  const api = {
    UUID_PATTERN: /^[0-9a-f-]{36}$/i,
    dutyRequest: async (...args) => { calls.push(args); return { ok: false, error: { status: 409, code: "STALE_ROSTER", message: "Refresh the roster." } }; },
    getRosterExport: async (...args) => { calls.push(args); return { ok: false, error: { status: 403, code: "FORBIDDEN", message: "Read permission is required." } }; },
  };
  const { GET } = load("src/app/(examination)/exams/duties/rosters/[rosterId]/download/[format]/route.ts", { "@/lib/duty-allocation": api });
  const ctx = (format) => ({ params: Promise.resolve({ rosterId: data.roster.roster_id, format }) });
  const excel = await GET(new Request(`http://localhost/download?version=${data.roster.row_version}`), ctx("xlsx"));
  assert.equal(excel.status, 409); assert.equal((await excel.json()).code, "STALE_ROSTER");
  assert.equal(calls[0][1].query.expected_version, data.roster.row_version);
  assert.equal(calls[0][1].responseType, "binary");
  const pdf = await GET(new Request(`http://localhost/download?version=${data.roster.row_version}`), ctx("pdf"));
  assert.equal(pdf.status, 403);
  assert.deepEqual(calls[1], [data.roster.roster_id, data.roster.row_version]);
  assert.equal((await GET(new Request("http://localhost/download"), ctx("pdf"))).status, 400);
});
