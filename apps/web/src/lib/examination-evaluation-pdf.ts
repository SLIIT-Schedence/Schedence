import { jsPDF } from "jspdf";
import { autoTable } from "jspdf-autotable";
import type { GeneratedTimetable } from "./examination-generation-types";
import { ALL_COHORTS, cohortLabel, cohortStatistics, comparableAnalysis, EVALUATION_METRICS, gapValue, metricChange,
  metricValue, publicAnalysis, type AnalysisExam, type CohortFilter } from "./examination-evaluation";
import { exportText } from "./examination-export";
import { HARD_RULES } from "./examination-presentation";

const BLUE: [number, number, number] = [71, 114, 221];
function safe(value: string) {
  const text = exportText(value).replace(/[\u2010-\u2015]/g, "-").replace(/[\u2018\u2019]/g, "'")
    .replace(/[\u201c\u201d]/g, '"').replace(/\u2026/g, "...");
  if (/[^\u0020-\u00ff]/.test(text)) throw new Error("Unsupported PDF font characters");
  return text;
}
const exam = (e: AnalysisExam) => `${e.module_code} / Session ${e.session_number}\n${e.date}\n${e.start_time.slice(0, 5)} - ${e.end_time.slice(0, 5)}`;

/** Separate read-only report; never calls generation or persistence. */
export function createEvaluationPdf(result: GeneratedTimetable, filter: CohortFilter = ALL_COHORTS, exportedAt = new Date()) {
  const a = publicAnalysis(result.analysis);
  if (!a) throw new Error("Evaluation analysis unavailable");
  const doc = new jsPDF({ orientation: "landscape", unit: "mm", format: "a4", compress: true });
  const width = doc.internal.pageSize.getWidth(), height = doc.internal.pageSize.getHeight();
  const comparable = comparableAnalysis({ ...result, analysis: a });
  // Preserve validated provenance digests exactly; generic ID redaction can match a hex substring.
  const fingerprints = new Set([a.input_fingerprint, a.configuration_fingerprint, a.policy.scoring_fingerprint]
    .filter((value) => /^[a-f0-9]{64}$/.test(value)));
  let y = 28;
  const table = (title: string, labels: string[], rows: string[][]) => {
    if (y > height - 55) { doc.addPage(); y = 28; }
    autoTable(doc, { startY: y, margin: { top: 28, bottom: 22, left: 12, right: 12 }, theme: "grid",
      head: [[{ content: safe(title), colSpan: labels.length }], labels.map(safe)],
      body: rows.map((r) => r.map((value) => value.split("\n").map((line) => fingerprints.has(line) ? line : safe(line)).join("\n"))),
      showHead: "everyPage", rowPageBreak: "avoid", styles: { fontSize: 9, cellPadding: 2.5, overflow: "linebreak", valign: "top" },
      headStyles: { fillColor: BLUE }, alternateRowStyles: { fillColor: [247, 248, 255] },
    });
    y = (doc as jsPDF & { lastAutoTable: { finalY: number } }).lastAutoTable.finalY + 7;
  };
  const date = (value?: string) => value && Number.isFinite(Date.parse(value))
    ? new Date(value).toLocaleString("en-GB", { timeZone: "Asia/Colombo", hour12: false }) + " (Sri Lanka)" : "Not recorded";
  doc.setProperties({ title: safe(`${result.inputs.period_name} - Evaluation Report - UNSAVED`), author: "Schedence", subject: "Examination research evaluation" });
  table("Examination evaluation report", ["Item", "Recorded value"], [
    ["Examination", `${result.inputs.period_name} / ${result.inputs.period_code}`],
    ["Academic year / semester / type", `${result.inputs.academic_year_label || "Not provided"} / ${result.inputs.semester ?? "Not provided"} / ${result.inputs.exam_kind || "Not provided"}`],
    ["Timetable status", "UNSAVED PREVIEW - Not an approved or published examination notice."],
    ["Solver status", `${result.status} / ${result.solver.mode} / runtime ${result.solver.wall_time_seconds}s / limit ${result.solver.time_limit_seconds}s`],
    ["Optimality", result.status === "OPTIMAL" ? "Solver reported OPTIMAL for its modeled objective." : "Optimality is not proven."],
    ["Generated / exported", `${date(result.generated_at)} / ${date(exportedAt.toISOString())}`],
    ["Calendar", result.inputs.calendar ? `${result.inputs.calendar.start_date} to ${result.inputs.calendar.end_date} / ${result.inputs.calendar.source}` : "Not reported"],
    ["Comparison cohort", cohortLabel(filter)],
    ["Research interpretation", "S1-S5 are estimated workload penalties, not validated stress measurements. Soft targets are not guarantees. Hard validation is separate."],
  ]);
  table("Applied preparation targets", ["Setting", "Applied value"], [
    ["Difficult-exam threshold", `${a.policy.high_difficulty_minimum} to 5`],
    ["Short gap", `${a.policy.short_gap_minutes} minutes / ${gapValue(a.policy.short_gap_minutes)}`],
    ["General preparation target", `${a.policy.preparation_target_minutes} minutes / ${gapValue(a.policy.preparation_target_minutes)}`],
    ["Difficult-exam target", `${a.policy.difficult_target_minutes} minutes / ${gapValue(a.policy.difficult_target_minutes)}`],
    ["S1 / S2 / S3", "S1: close pairs of difficult exams. S2: insufficient general preparation. S3: same-day or very short gaps. Zero-weight penalties do not influence the objective."],
    ["S4 / S5", "S4: difficulty, credits and duration concentration. S5: exam-count concentration."],
  ]);
  table("Independent hard validation (selected timetable)", ["Rule", "Description", "Actual evidence"], HARD_RULES.map(([code, title, description]) => [
    `${code} / ${title}`, description,
    result.validation.violations.some((v) => v.rule === code) ? result.validation.violations.filter((v) => v.rule === code).map((v) => v.message).join("; ")
      : result.validation.rules_checked.includes(code) ? "Listed in overall validation; no individual success flag returned." : "No evidence returned for this rule.",
  ]));
  table("Overall validation results", ["Timetable", "Result"], [
    ["Selected", result.validation.passed === true ? "Overall H1-H11 passed" : result.validation.passed === false ? "Overall validation failed" : "Not evaluated"],
    ["Baseline analysis", a.baseline?.validation.passed ? `Overall passed; checked ${a.baseline.validation.rules_checked.join(", ")}` : a.baseline_unavailable_reason || "Unavailable"],
    ["Selected analysis", a.selected?.validation.passed ? `Overall passed; checked ${a.selected.validation.rules_checked.join(", ")}` : a.selected_unavailable_reason || "Unavailable"],
  ]);
  const before = cohortStatistics(a.baseline, filter), after = cohortStatistics(a.selected, filter);
  table(`Preparation gaps and workload / ${cohortLabel(filter)}`, ["Metric", "Feasibility baseline", "Selected", "Change (gap changes in minutes)"], EVALUATION_METRICS.map((m) => [
    m.label, m.gap ? gapValue(before?.[m.key]) : metricValue(before?.[m.key]),
    m.gap ? gapValue(after?.[m.key]) : metricValue(after?.[m.key]),
    comparable && m.compare ? metricChange(before?.[m.key], after?.[m.key], m.lowerBetter) : "No comparison",
  ]));
  table("Calculation definitions and comparison limits", ["Definition", "Meaning"], [
    ["Consecutive gaps", "For each student, order their actual assigned sittings. Gap = next start minus previous end, including reserved reading time. A student with one exam contributes no gap."],
    ["Mean / median / lower decile", "Mean = pooled gap sum / consecutive-gap count. Median uses sorted consecutive gaps. Lower decile uses index floor((count - 1) / 10). No gaps means unavailable, not zero."],
    ["All-pair counts", "Short-gap and difficult-pair counts consider every exam pair for each student, including nonconsecutive exams. Strictly below the applied threshold counts as below target."],
    ["Cohorts", "Membership comes from actual registered students, not module specialization mappings. Repeat group = students with any repeat registration, considering all their scheduled exams; normal-only group has none."],
    ["Comparison", comparable ? "Same frozen inputs and effective preferences. Baseline is this optimizer's real feasibility solution. Individual metrics may regress even when the total workload improves." : `Comparison unavailable. ${a.baseline_unavailable_reason || ""} ${a.selected_unavailable_reason || ""}`],
    ["Cohort bounds", `Maximum ${a.limits.max_cohorts} cohort summaries. Missing or omitted cohorts are unavailable; their medians are not inferred from other groups.`],
  ]);
  const baseScore = a.baseline?.workload ?? result.optimization?.baseline;
  const selectedScore = a.selected?.workload ?? result.optimization?.selected ?? result.workload;
  table("S1-S5 independent workload scores / FULL CYCLE (not cohort-filtered)", ["Score", "Weight baseline / selected", "Baseline penalty / weighted", "Selected penalty / weighted"],
    ["S1", "S2", "S3", "S4", "S5"].map((code) => {
      const b = baseScore?.penalties.find((p) => p.constraint === code), s = selectedScore?.penalties.find((p) => p.constraint === code);
      return [code, `${metricValue(b?.weight)} / ${metricValue(s?.weight)}`, `${metricValue(b?.penalty_points)} / ${metricValue(b?.weighted_points)}`, `${metricValue(s?.penalty_points)} / ${metricValue(s?.weighted_points)}`];
    }).concat([["Weighted total", "", metricValue(baseScore?.total_weighted_penalty), metricValue(selectedScore?.total_weighted_penalty)]]));
  for (const source of ["baseline", "selected"] as const) {
    const run = a[source];
    table(`Difficult-exam pairs / ${source.toUpperCase()} / FULL CYCLE (not cohort-filtered)`, ["First examination", "Second examination", "Gap / soft target", "Shared candidates"],
      run?.difficult_pairs.length ? run.difficult_pairs.map((p) => [exam(p.first), exam(p.second), `${p.gap_minutes} minutes\n${p.below_target ? "Below target" : "Meets target"}`,
        `${p.shared_candidates} total\n${p.normal_only_candidates} normal-only\n${p.repeat_candidates} with repeat registrations`]) : [["Unavailable / no pairs", "", "", run ? "No shared difficult pairs" : a[`${source}_unavailable_reason`] || "Unavailable"]]);
    if (run) table(`${source} pair-detail coverage`, ["Returned rows", "Complete population statistics"], [[`${run.difficult_pairs.length} of ${run.difficult_pair_rows_total} session-pair groups`, run.difficult_pair_rows_truncated ? "Closest pairs shown, below-target pairs first. Summary pair counts cover all pairs within the analysis limit." : "All difficult session-pair groups returned."]]);
  }
  table("Actual module difficulty / frozen inputs", ["Module", "Module academic group", "Difficulty"], a.modules.map((m) => [
    `${m.module_code} / ${m.module_name}`, `Year ${m.academic_year} / Semester ${m.semester}\n${m.specializations.join(", ") || "Specialization unavailable"}`,
    `${m.difficulty ?? "Unavailable"}${m.difficult ? " / Difficult" : ""}`,
  ]));
  table("Evaluation provenance", ["Item", "Value"], [["Input fingerprint", a.input_fingerprint || "Unavailable"],
    ["Effective configuration fingerprint", a.configuration_fingerprint || "Unavailable"], ["Scoring fingerprint", a.policy.scoring_fingerprint || "Unavailable"],
    ["Scoring version / analysis version", `${a.policy.scoring_version} / ${a.version}`],
    ["Analysis bounds", `${a.limits.max_student_pairs} student-exam pairs per timetable; ${a.limits.max_pair_rows} detail rows; ${a.limits.max_cohorts} cohort summaries`],
  ]);
  const pages = doc.getNumberOfPages();
  for (let page = 1; page <= pages; page++) {
    doc.setPage(page); doc.setFont("helvetica", "bold"); doc.setFontSize(12); doc.setTextColor(...BLUE);
    doc.text("SCHEDENCE / EXAMINATION EVALUATION REPORT", 12, 13);
    doc.setFont("helvetica", "normal"); doc.setFontSize(9); doc.setTextColor(60, 70, 85);
    doc.text(safe(`${result.inputs.period_code} / UNSAVED / Solver: ${result.status}`), 12, 20);
    doc.setFontSize(8); doc.text("UNSAVED research evaluation - Not an approved or published examination notice.", 12, height - 12);
    doc.text(`Page ${page} of ${pages}`, width - 12, height - 7, { align: "right" });
  }
  return doc;
}

export async function downloadEvaluationPdf(result: GeneratedTimetable, filter: CohortFilter) {
  await createEvaluationPdf(result, filter).save(`evaluation-${result.inputs.period_code}-UNSAVED.pdf`.replace(/[^a-zA-Z0-9._-]/g, "-"), { returnPromise: true });
}
