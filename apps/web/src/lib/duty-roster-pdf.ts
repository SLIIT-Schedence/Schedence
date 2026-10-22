import { jsPDF } from "jspdf";
import { autoTable } from "jspdf-autotable";
import type { RosterExport } from "./duty-allocation-types";

export class DutyRosterPdfError extends Error {
  constructor(message: string) { super(message); this.name = "DutyRosterPdfError"; }
}

export function createDutyRosterPdf(data: RosterExport) {
  const labels = [data.period_name, data.roster.period_code, data.roster.timetable_reference,
    ...data.assignments.flatMap((a) => [a.module_code, a.module_name, a.venue_id, a.staff_name, a.staff_number, a.staff_id])];
  if (labels.some((value) => value && /[^\u0020-\u007e\u00a0-\u00ff\u2013\u2014\u2018\u2019\u201c\u201d]/u.test(value))) {
    throw new DutyRosterPdfError("A roster label contains characters the PDF font cannot display. Download Excel to preserve these names; PDF needs a font supporting those characters.");
  }
  const doc = new jsPDF({ orientation: "landscape", unit: "mm", format: "a4", compress: true });
  const width = doc.internal.pageSize.getWidth(), height = doc.internal.pageSize.getHeight();
  const margin = 12, contentWidth = width - 2 * margin;
  const r = data.roster;
  // Derive the label from saved flags as well as the API warning, never just APPROVED status.
  const warning = r.synthetic || r.timetable_reference.startsWith("DEMO-UNPUBLISHED:")
    ? "SYNTHETIC DEMO – NOT AN OFFICIAL PUBLISHED ROSTER"
    : r.status !== "PUBLISHED" || !r.timetable_verified ? "NOT AN OFFICIAL PUBLISHED ROSTER" : null;
  doc.setFontSize(9);
  const periodLines: string[] = doc.splitTextToSize(`Examination period: ${data.period_name} (${r.period_code})`, contentWidth);
  const refLines: string[] = doc.splitTextToSize(`Timetable reference: ${r.timetable_reference}`, contentWidth);
  const headerBottom = 32 + 4 * (periodLines.length + refLines.length) + (warning ? 7 : 0);
  const rooms = new Map<string, RosterExport["assignments"]>();
  for (const a of [...data.assignments].sort((a, b) => a.starts_at.localeCompare(b.starts_at) || a.module_code?.localeCompare(b.module_code ?? "") || a.venue_id.localeCompare(b.venue_id) || a.staff_id.localeCompare(b.staff_id))) {
    const key = `${a.session_id}\u0000${a.venue_id}`;
    rooms.set(key, [...(rooms.get(key) ?? []), a]);
  }
  const label = (a: RosterExport["assignments"][number]) => `${a.staff_name ?? "Name not recorded"}\n${a.staff_number ?? a.staff_id}`;
  autoTable(doc, {
    startY: headerBottom + 3,
    margin: { top: headerBottom + 3, bottom: 20, left: margin, right: margin },
    theme: "grid", showHead: "everyPage", rowPageBreak: "avoid",
    head: [["Exam date", "Start / end", "Module", "Room / venue", "Supervisors", "Invigilators"]],
    body: [...rooms.values()].map((duties) => {
      const a = duties[0];
      return [a.starts_at.slice(0, 10), `${a.starts_at.slice(11, 16)}\n${a.ends_at.slice(11, 16)}`,
        `${a.module_code ?? "Not recorded"}\n${a.module_name ?? "Module name not recorded"}`, a.venue_id,
        duties.filter((a) => a.role === "SUPERVISOR").map(label).join("\n\n") || "None assigned",
        duties.filter((a) => a.role === "INVIGILATOR").map(label).join("\n\n") || "None assigned"];
    }),
    styles: { font: "helvetica", fontSize: 8.5, cellPadding: 2.5, overflow: "linebreak", valign: "top", lineColor: [218, 225, 239], textColor: [24, 36, 65] },
    headStyles: { fillColor: [49, 91, 214], textColor: [255, 255, 255], fontStyle: "bold" },
    alternateRowStyles: { fillColor: [246, 248, 255] },
    columnStyles: { 0: { cellWidth: 24 }, 1: { cellWidth: 22 }, 2: { cellWidth: 77 }, 3: { cellWidth: 25 }, 4: { cellWidth: 62 }, 5: { cellWidth: contentWidth - 210 } },
  });
  const pages = doc.getNumberOfPages();
  for (let page = 1; page <= pages; page++) {
    doc.setPage(page);
    doc.setFont("helvetica", "bold"); doc.setTextColor(24, 36, 65); doc.setFontSize(15);
    doc.text("SCHEDENCE / EXAMINATION DUTY ROSTER", margin, 13);
    doc.setFont("helvetica", "normal"); doc.setFontSize(9);
    let y = 21;
    doc.text(periodLines, margin, y); y += periodLines.length * 4 + 2;
    doc.text(`Roster status: ${r.status} | Version: ${r.version} | Assignments: ${data.assignments.length} | Times: examination local time`, margin, y); y += 6;
    doc.text(refLines, margin, y); y += refLines.length * 4 + 2;
    if (warning) {
      doc.setFont("helvetica", "bold"); doc.setTextColor(173, 35, 56); doc.text(warning, margin, y);
    }
    doc.setFont("helvetica", "normal"); doc.setFontSize(7); doc.setTextColor(85, 99, 122);
    doc.setDrawColor(49, 91, 214); doc.line(margin, height - 15, width - margin, height - 15);
    doc.text("Complete saved roster. Module names use source labels; staff names use the saved roster. Missing labels are shown explicitly.", margin, height - 10);
    doc.text(`Roster ${r.roster_id} | ${data.assignments.length} saved assignments`, margin, height - 5);
    doc.text(`Page ${page} of ${pages}`, width - margin, height - 5, { align: "right" });
  }
  return doc;
}
