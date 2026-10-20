import { jsPDF } from "jspdf";
import { autoTable } from "jspdf-autotable";
import type { GenerationSession } from "./examination-generation-types";
import type { AcademicFilters } from "./examination-presentation";
import { candidateText, exportFilename, exportFilterLabel, exportGroups, exportText, type TimetableExportMetadata } from "./examination-export";

const BLUE: [number, number, number] = [71, 114, 221];
const INK: [number, number, number] = [31, 41, 55];
const MUTED: [number, number, number] = [83, 95, 115];
const MARGIN = 12;

// Built-in PDF fonts support Latin text. Fail visibly rather than corrupting unsupported names.
function text(value: string): string {
  const clean = exportText(value).replace(/[\u2010-\u2015]/g, "-").replace(/[\u2018\u2019]/g, "'")
    .replace(/[\u201c\u201d]/g, '"').replace(/\u2026/g, "...");
  if (/[^\u0020-\u00ff]/.test(clean)) throw new Error("This timetable contains characters the PDF font cannot display. Use the table view; a font supporting these names is needed for PDF export.");
  return clean;
}

function timestamp(value?: string): string {
  if (!value || !Number.isFinite(Date.parse(value))) return "Not recorded";
  // Explicit zone: examination clock times remain the local times supplied by the service.
  return new Date(value).toLocaleString("en-GB", { timeZone: "Asia/Colombo", hour12: false }) + " (Sri Lanka)";
}

/** Real text/vector A4 document. Inputs are the already filtered, validated public sessions. */
export function createTimetablePdf(sessions: GenerationSession[], metadata: TimetableExportMetadata, filters: AcademicFilters, exportedAt = new Date()) {
  if (!sessions.length) throw new Error("No timetable sessions match these filters. Choose another academic group before downloading.");
  const doc = new jsPDF({ orientation: "landscape", unit: "mm", format: "a4", compress: true });
  const width = doc.internal.pageSize.getWidth();
  const height = doc.internal.pageSize.getHeight();
  const contentWidth = width - MARGIN * 2;
  const documentTitle = text(metadata.periodName);
  const status = metadata.status;
  const warning = status === "UNSAVED" ? "UNSAVED PREVIEW - Not saved or approved. Do not use as an official examination notice."
    : status === "DRAFT" ? "DRAFT - Not approved or published. Do not use as an official examination notice."
      : status === "APPROVED" ? "APPROVED - Not published." : status === "ARCHIVED" ? "ARCHIVED - Superseded timetable." : "PUBLISHED";
  doc.setProperties({ title: `${documentTitle} - ${status}`, subject: "Public examination timetable", author: "Schedence", creator: "Schedence" });
  const line = (value: string, y: number, size = 9) => {
    doc.setFontSize(size);
    const lines: string[] = doc.splitTextToSize(text(value), contentWidth);
    doc.text(lines, MARGIN, y);
    return y + lines.length * (size * 0.3528 * 1.2) + 2;
  };
  doc.setTextColor(...BLUE); doc.setFont("helvetica", "bold");
  let y = line("SCHEDENCE / EXAMINATION TIMETABLE", 16, 15);
  doc.setTextColor(...INK);
  y = line(documentTitle, y, 12);
  doc.setFont("helvetica", "normal");
  y = line(`Academic year: ${metadata.academicYearLabel || "Not provided"} | Examination semester: ${metadata.semester ?? "Not provided"} | Type: ${metadata.examKind || "Not provided"}`, y);
  y = line(`Cycle: ${metadata.periodCode} | ${metadata.recordedAtLabel === "Saved" ? "Generated: Not recorded | " : ""}${metadata.recordedAtLabel}: ${timestamp(metadata.recordedAt)}${metadata.draftNumber == null ? "" : ` | Draft #${metadata.draftNumber} / Revision ${metadata.revision ?? "Not provided"}`}`, y);
  y = line(`Exam calendar: ${metadata.calendarStart || "Not provided"} to ${metadata.calendarEnd || "Not provided"} | Times: examination local time`, y);
  doc.setFont("helvetica", "bold"); y = line(warning, y);
  doc.setFont("helvetica", "normal"); doc.setTextColor(...MUTED);
  y = line(`Display/export selection: ${exportFilterLabel(filters)} | ${sessions.length} sessions`, y, 8);
  if (metadata.periodMetadataSource === "current") y = line("Academic year and examination semester are current period labels; they were not recorded in the saved input snapshot.", y, 8);
  y = line("Normal/repeat figures are registrations in each full session, not unique students or specialization totals. Shared sessions appear once. Specializations use current module mappings, not a historical snapshot. Not provided means unavailable.", y, 8);
  const groups = exportGroups(sessions);
  for (const group of groups) {
    // Keep each group heading with its repeated column headings and first session.
    if (y > height - 55) { doc.addPage(); y = 28; }
    const groupTitle = text(group.label);
    autoTable(doc, {
      startY: y, margin: { top: 28, bottom: 20, left: MARGIN, right: MARGIN },
      theme: "grid", showHead: "everyPage", rowPageBreak: "avoid",
      head: [
        [{ content: groupTitle, colSpan: 6, styles: { fillColor: [237, 242, 255], textColor: INK } }],
        ["Exam date", "Start / end", "Module / semester", "Session", "Allocated venue(s)", "Registrations"],
      ],
      body: group.rows.map((session) => [
        text(session.date), `${text(session.start_time.slice(0, 5))}\n${text(session.end_time.slice(0, 5))}`,
        `${text(session.module_code)}\n${text(session.module_name)}\nSemester ${session.semester ?? "not provided"}`,
        `${session.session_number} of ${session.session_count}\n${session.rooms.length} room(s)`,
        session.rooms.length ? session.rooms.map((room) => `${text(room.venue_name)} (${room.seats} candidates)`).join("\n") : "Not provided",
        candidateText(session),
      ]),
      styles: { font: "helvetica", fontSize: 9, cellPadding: 2.5, overflow: "linebreak", textColor: INK, lineColor: [222, 228, 239], valign: "top" },
      headStyles: { fillColor: BLUE, textColor: [255, 255, 255], fontStyle: "bold" },
      alternateRowStyles: { fillColor: [247, 248, 255] },
      columnStyles: { 0: { cellWidth: 25 }, 1: { cellWidth: 23 }, 2: { cellWidth: 91 }, 3: { cellWidth: 24 }, 4: { cellWidth: 70 }, 5: { cellWidth: contentWidth - 233 } },
    });
    y = (doc as jsPDF & { lastAutoTable: { finalY: number } }).lastAutoTable.finalY + 7;
  }
  const pages = doc.getNumberOfPages();
  for (let page = 1; page <= pages; page++) {
    doc.setPage(page);
    doc.setTextColor(...MUTED); doc.setFont("helvetica", "normal"); doc.setFontSize(8);
    if (page > 1) {
      doc.setFont("helvetica", "bold"); doc.text(`EXAMINATION TIMETABLE / ${status}`, MARGIN, 12);
      doc.setFont("helvetica", "normal"); doc.text(doc.splitTextToSize(documentTitle, contentWidth), MARGIN, 17);
    }
    doc.setDrawColor(...BLUE); doc.line(MARGIN, height - 16, width - MARGIN, height - 16);
    doc.setFontSize(7); doc.text(warning, MARGIN, height - 11);
    doc.text(`Exported ${timestamp(exportedAt.toISOString())}`, MARGIN, height - 6);
    doc.text(`Page ${page} of ${pages}`, width - MARGIN, height - 6, { align: "right" });
  }
  return doc;
}

export async function downloadTimetablePdf(sessions: GenerationSession[], metadata: TimetableExportMetadata, filters: AcademicFilters) {
  const doc = createTimetablePdf(sessions, metadata, filters);
  await doc.save(exportFilename(metadata, filters), { returnPromise: true });
}
