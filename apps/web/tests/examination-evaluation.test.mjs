import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import test from "node:test";
import { inflateSync } from "node:zlib";
import ts from "typescript";
import * as React from "react";
import * as jsxRuntime from "react/jsx-runtime";
import { renderToStaticMarkup } from "react-dom/server";

const require = createRequire(import.meta.url);
const fixture = JSON.parse(readFileSync(new URL("./fixtures/examination-evaluation.json", import.meta.url), "utf8"));
function load(source, mocks = {}) {
  const code = ts.transpileModule(readFileSync(path.join(import.meta.dirname, "..", source), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const testModule = { exports: {} };
  new Function("require", "module", "exports", code)((name) => mocks[name] ?? require(name), testModule, testModule.exports);
  return testModule.exports;
}
const evaluation = load("src/lib/examination-evaluation.ts");
const exportsLib = load("src/lib/examination-export.ts");
const presentation = load("src/lib/examination-presentation.ts");
const icons = load("src/components/icon.tsx");
const ui = load("src/components/console/ui.tsx", { "next/link": { default: ({ children, ...props }) => React.createElement("a", props, children) }, "@/components/icon": icons });
const styles = { fieldClass: "field", secondaryButton: "secondary" };
const componentMocks = { "react/jsx-runtime": jsxRuntime, "@/components/console/ui": ui, "@/lib/examination-evaluation": evaluation, "./period-calendar": styles };

test("all public evaluation fields survive mapping while private nested extras and IDs are omitted", () => {
  const raw = structuredClone(fixture.analysis);
  raw.student_id = "IT33123456";
  raw.selected.statistics.student_id = "IT33123456";
  raw.selected.cohorts[0].student_names = ["Private student name"];
  raw.selected.difficult_pairs[0].first.seats = [{ student_id: "IT33123456" }];
  raw.modules[0].module_name = "Exam IT33123456";
  const mapped = evaluation.publicAnalysis(raw);
  const serialized = JSON.stringify(mapped);
  assert.equal(serialized.includes("IT33123456"), false);
  assert.equal(serialized.includes("student_id"), false);
  assert.equal(serialized.includes("Private student name"), false);
  assert.equal(serialized.includes("seats"), false);
  assert.deepEqual(mapped.selected.statistics, fixture.analysis.selected.statistics);
  assert.deepEqual(mapped.baseline, fixture.analysis.baseline);
  assert.equal(evaluation.publicAnalysis(undefined), null);
  assert.equal(evaluation.publicAnalysis({ version: "future" }), null);
  raw.selected.difficult_pairs = Array.from({ length: 130 }, () => raw.selected.difficult_pairs[0]);
  raw.selected.cohorts = Array.from({ length: 160 }, () => raw.selected.cohorts[0]);
  const bounded = evaluation.publicAnalysis(raw);
  assert.equal(bounded.selected.difficult_pairs.length, 100);
  assert.equal(bounded.selected.difficult_pair_rows_total, 130);
  assert.equal(bounded.selected.difficult_pair_rows_truncated, true);
  assert.equal(bounded.selected.cohorts.length, 128);
  assert.equal(bounded.selected.cohorts_truncated, true);
});

test("cohorts use exact returned student membership and never infer missing intersections", () => {
  const filter = { year: "2", specialization: "DS", registration: "includes_repeat" };
  const stats = evaluation.cohortStatistics(fixture.analysis.selected, filter);
  assert.equal(stats.students, 1);
  assert.equal(stats.consecutive_gap_count, 2);
  assert.equal(evaluation.cohortStatistics(fixture.analysis.selected, { ...filter, specialization: "SE" }), null);
  assert.equal(evaluation.cohortStatistics(null, filter), null);
  assert.equal(evaluation.comparableAnalysis(fixture), true);
  const stale = structuredClone(fixture);
  stale.analysis.baseline.input_fingerprint = "b".repeat(64);
  assert.equal(evaluation.comparableAnalysis(stale), false);
  assert.match(evaluation.metricChange(60, 30), /Regressed/);
  assert.match(evaluation.metricChange(5, 3, true), /Improved/);
  assert.equal(evaluation.metricChange(0, 0), "Unchanged");
  assert.equal(evaluation.metricChange(null, 0), "No comparison");
});

test("dashboard shows actual thresholds, denominators, separate hard validation, bounded pair details and honest optimality", () => {
  const { EvaluationDashboard } = load("src/app/(examination)/exams/generate/_components/evaluation-dashboard.tsx", componentMocks);
  const markup = renderToStaticMarkup(React.createElement(EvaluationDashboard, { result: fixture }));
  for (const value of ["Difficulty 4", "33.33 hours", "83.33 hours", "consecutive-gap count", "All exam pairs", "Difficult-exam pairs", "IT1001", "IT1002", "Normal-only", "not proven", "H1–H11", "not this table", "UNSAVED", "Download Evaluation Report PDF"]) assert.ok(markup.includes(value), value);
  const old = renderToStaticMarkup(React.createElement(EvaluationDashboard, { result: { ...fixture, analysis: null } }));
  assert.ok(old.includes("Detailed evaluation unavailable"));
  assert.equal(old.includes("Download Evaluation Report PDF"), false);
});

test("changing evaluation filters preserves the timetable and downloads the selected cohort only as report context", async () => {
  const slots = []; let cursor = 0; const downloads = [];
  const hooks = { useState(initial) { const i = cursor++; if (!(i in slots)) slots[i] = initial; return [slots[i], (value) => { slots[i] = value; }]; } };
  const { EvaluationDashboard } = load("src/app/(examination)/exams/generate/_components/evaluation-dashboard.tsx", { ...componentMocks,
    react: hooks, "@/lib/examination-evaluation-pdf": { downloadEvaluationPdf: async (...args) => downloads.push(args) } });
  const nodes = (node) => !node || typeof node !== "object" ? [] : Array.isArray(node) ? node.flatMap(nodes) : [node, ...nodes(node.props?.children)];
  const render = () => { cursor = 0; return EvaluationDashboard({ result: fixture }); };
  const before = JSON.stringify(fixture);
  nodes(render()).filter((n) => n.type === "select")[0].props.onChange({ target: { value: "2" } });
  nodes(render()).filter((n) => n.type === "select")[1].props.onChange({ target: { value: "DS" } });
  const button = nodes(render()).find((n) => n.type === "button" && n.props.children === "Download Evaluation Report PDF");
  await button.props.onClick();
  assert.equal(downloads.length, 1);
  assert.deepEqual(downloads[0][1], { year: "2", specialization: "DS", registration: "all" });
  assert.equal(JSON.stringify(fixture), before);
});

test("evaluation PDF contains the real baseline/selected numbers, validation evidence, provenance and no private fields", () => {
  const cells = [];
  const { createEvaluationPdf } = load("src/lib/examination-evaluation-pdf.ts", {
    "./examination-evaluation": evaluation, "./examination-export": exportsLib, "./examination-presentation": presentation,
    "jspdf-autotable": { autoTable: (doc, options) => require("jspdf-autotable").autoTable(doc, { ...options, didDrawCell: (data) => cells.push({ page: doc.getNumberOfPages(), ...data.cell }) }) },
  });
  const result = structuredClone(fixture);
  result.analysis.student_id = "IT33123456";
  result.analysis.modules[0].module_name += " IT33123456";
  const before = JSON.stringify(result);
  const doc = createEvaluationPdf(result, { year: "2", specialization: "DS", registration: "includes_repeat" }, new Date("2026-10-20T09:00:00Z"));
  const bytes = Buffer.from(doc.output("arraybuffer"));
  assert.equal(bytes.subarray(0, 5).toString(), "%PDF-");
  assert.ok(doc.getNumberOfPages() > 3);
  for (const cell of cells) {
    assert.ok(cell.x >= 11.9 && cell.x + cell.width <= 285.1);
    assert.ok(cell.y >= 27.9 && cell.y + cell.height <= 188.1);
  }
  const text = [...bytes.toString("latin1").matchAll(/stream\r?\n([\s\S]*?)\r?\nendstream/g)].map((match) => inflateSync(Buffer.from(match[1], "latin1")).toString("latin1")).join("\n");
  for (const value of ["UNSAVED", "FEASIBLE", "not proven", "2000 minutes", "5000 minutes", "consecutive-gap count", "FULL CYCLE", "DS", "IT1001", "IT1002"]) assert.ok(text.includes(value), value);
  assert.ok(cells.some((cell) => cell.text.join("").includes(fixture.analysis.input_fingerprint)));
  assert.ok(cells.some((cell) => cell.text.join(" ").includes("no individual success flag")));
  assert.equal(text.includes("IT33123456"), false);
  assert.equal(text.includes("student_id"), false);
  assert.equal(JSON.stringify(result), before);
  for (let page = 1; page <= doc.getNumberOfPages(); page++) assert.ok(text.includes(`Page ${page} of ${doc.getNumberOfPages()}`));
  assert.throws(() => createEvaluationPdf({ ...fixture, analysis: null }), /unavailable/);
});

// ---- Results dashboard helpers: the numbers come only from what the service returned ----
const results = load("src/lib/examination-results.ts", { "./examination-evaluation": evaluation });
const summaryById = (result) => Object.fromEntries(results.summarize(result).map((item) => [item.id, item]));
const withoutAnalysis = () => {
  const plain = structuredClone(fixture);
  plain.solver.mode = "feasibility"; plain.optimization = null; plain.analysis = null;
  plain.workload = { total_weighted_penalty: 12.8625, penalties: [] };
  return plain;
};

test("the results summary shows the six headline facts from the returned data", () => {
  const items = results.summarize(fixture);
  assert.deepEqual(items.map((item) => item.id), ["validation", "solver", "workload", "gaps", "difficult", "students"]);
  const s = summaryById(fixture);
  assert.equal(s.validation.value, "Passed");
  assert.match(s.validation.detail, /11 mandatory rules checked by the independent validator/);
  assert.equal(s.solver.value, "FEASIBLE");
  assert.deepEqual(s.solver.badge, { text: "Not proven optimal", tone: "neutral" }, "FEASIBLE is never presented as OPTIMAL");
  assert.match(s.solver.detail, /optimality is not proven/);
  assert.match(s.workload.value, /^35\.51\d → 12\.86\d$/);
  assert.equal(s.workload.badge.text, "Improved");
  assert.match(s.workload.detail, /Improved by 63\.8%/);
  assert.equal(s.gaps.value, "47 hours average");
  assert.equal(s.gaps.detail, "Minimum 47 hours (baseline 4 hours). Baseline average 11 hours.");
  assert.equal(s.difficult.value, "1");
  assert.equal(s.difficult.detail, "of 1 difficult student pair; baseline 1. Soft target.");
  assert.equal(s.difficult.badge.text, "Unchanged");
  assert.equal(s.students.value, "Not available", "the fixture has no per-student comparison");
  const compared = structuredClone(fixture);
  compared.optimization.fairness = { protection_applied: false, limits_satisfied: null, students: 10, students_improved: 8, students_worsened: 1, students_unchanged: 1,
    baseline_worst_normalized_penalty: 5, selected_worst_normalized_penalty: 4, maximum_individual_increase_points: 2, worst_student_increase_points: 1 };
  assert.equal(summaryById(compared).students.value, "8 / 1 / 1");
  const optimal = structuredClone(fixture);
  optimal.status = "OPTIMAL";
  assert.equal(summaryById(optimal).solver.badge.text, "Proven optimal");
});

test("values the service did not return are Not available and are never estimated", () => {
  const s = summaryById(withoutAnalysis());
  assert.equal(s.workload.value, "Not available");
  assert.match(s.workload.detail, /Feasibility mode has no baseline comparison\. Score: 12\.86\d\./);
  for (const id of ["gaps", "difficult", "students"]) assert.equal(s[id].value, "Not available", id);
  assert.equal(s.solver.badge.text, "Not proven optimal");
  assert.equal(summaryById({ ...withoutAnalysis(), status: "OPTIMAL" }).solver.badge, null, "no optimality claim without an objective");
  const none = withoutAnalysis();
  none.generated = false; none.status = "UNKNOWN"; none.validation = { passed: null, rules_checked: [], violations: [] }; none.workload = null;
  const empty = summaryById(none);
  assert.equal(empty.validation.value, "Not evaluated");
  assert.equal(empty.validation.detail, "No timetable was available to validate.");
  assert.equal(results.scoreTable(none).hasScores, false);
  assert.equal(results.regressionsOf(none).available, false);
  assert.equal(results.changeOf(null, 3, true).text, "Not available");
});

test("S1–S5 rows carry the applied weights, both penalties and the direction of change", () => {
  const table = results.scoreTable(fixture);
  assert.equal(table.hasBaseline, true);
  assert.deepEqual(table.rows.map((row) => [row.code, row.weight]), [["S1", 25], ["S2", 20], ["S3", 30], ["S4", 15], ["S5", 10]]);
  const row = (code) => table.rows.find((item) => item.code === code);
  assert.equal(row("S1").change.direction, "same");
  assert.deepEqual([row("S2").baseline, row("S2").selected, row("S2").change.direction, row("S2").change.text], [50, 6, "improved", "Improved (-44)"]);
  assert.equal(row("S3").change.direction, "improved");
  assert.equal(table.total.change.direction, "improved");
  const worse = structuredClone(fixture);
  worse.optimization.selected.penalties.find((p) => p.constraint === "S1").penalty_points = 20;
  assert.equal(results.scoreTable(worse).rows[0].change.direction, "worse");
  const feasibility = results.scoreTable({ ...fixture, solver: { ...fixture.solver, mode: "feasibility" } });
  assert.equal(feasibility.hasBaseline, false);
  assert.equal(feasibility.rows[1].change.direction, "unknown");
});

test("soft concerns use real student-pair counts and pairs are flagged only from returned dates and gaps", () => {
  const concerns = Object.fromEntries(results.concernsOf(fixture).map((item) => [item.id, item]));
  assert.deepEqual([concerns.difficult.selected, concerns.difficult.baseline, concerns.difficult.of, concerns.difficult.review], [1, 1, 1, true]);
  assert.deepEqual([concerns.short.selected, concerns.short.baseline, concerns.short.of, concerns.short.review], [0, 3, 3, false]);
  assert.deepEqual([concerns.sameday.selected, concerns.sameday.baseline, concerns.sameday.review], [0, 1, false]);
  assert.match(concerns.difficult.explain, /difficulty 4–5\) less than 83\.33 hours apart/);
  assert.match(concerns.short.explain, /33\.33 hours/);
  const policy = fixture.analysis.policy;
  assert.deepEqual(results.pairFlags(fixture.analysis.baseline.difficult_pairs[0], policy.short_gap_minutes), ["below", "same_day", "short"]);
  assert.deepEqual(results.pairFlags(fixture.analysis.selected.difficult_pairs[0], policy.short_gap_minutes), ["below"]);
  assert.deepEqual(results.pairFlags({ ...fixture.analysis.selected.difficult_pairs[0], below_target: false, gap_minutes: 9000 }, policy.short_gap_minutes), []);
  const plain = results.concernsOf(withoutAnalysis());
  assert.deepEqual(plain.map((item) => [item.selected, item.baseline, item.review]), [[null, null, null], [null, null, null], [null, null, null]]);
});

test("regressions list everything that got worse, even when the total improves", () => {
  assert.deepEqual(results.regressionsOf(fixture), { available: true, reason: "", items: [] });
  const worse = structuredClone(fixture);
  worse.analysis.selected.statistics.minimum_gap_minutes = 120;
  worse.analysis.selected.statistics.difficult_pairs_below_target = 4;
  worse.optimization.selected.penalties.find((p) => p.constraint === "S1").penalty_points = 20;
  worse.optimization.fairness = { protection_applied: false, limits_satisfied: null, students: 2, students_improved: 0, students_worsened: 1, students_unchanged: 1,
    baseline_worst_normalized_penalty: 5, selected_worst_normalized_penalty: 6, maximum_individual_increase_points: 5, worst_student_increase_points: 1.5 };
  const report = results.regressionsOf(worse);
  assert.deepEqual(report.items.map((item) => item.id), ["min_gap", "difficult_below", "S1", "students"]);
  assert.equal(report.items[0].detail, "4 hours → 2 hours · Worse (-2 hours)");
  assert.equal(report.items[1].detail, "1 → 4 · Worse (+3)");
  assert.match(report.items[3].detail, /1 of 2 students are worse off than in the baseline\. Largest rise: 1\.5 points\./);
  assert.equal(results.regressionsOf(withoutAnalysis()).available, false);
});

test("hard-rule problems are grouped by the rule they name and counted apart from soft concerns", () => {
  const grouped = results.violationsByRule([{ rule: "H1", message: "a" }, { rule: "H5", message: "b" }, { rule: "H1", message: "c" }, { rule: "STALE", message: "d" }, { rule: "H12", message: "e" }]);
  assert.deepEqual(Object.keys(grouped.byRule).sort(), ["H1", "H5"]);
  assert.equal(grouped.byRule.H1.length, 2);
  assert.deepEqual(grouped.other.map((problem) => problem.rule), ["STALE", "H12"]);
  assert.equal(results.reviewCount(fixture), 1, "one soft concern needs review");
  const failed = structuredClone(fixture);
  failed.generated = false; failed.validation = { passed: false, rules_checked: [], violations: [{ rule: "H1", message: "Students overlap" }] };
  assert.equal(results.reviewCount(failed), 2, "a hard failure is counted separately from the soft concern");
});
