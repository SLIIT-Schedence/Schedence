import type { NextRequest } from "next/server";
import { getAcademicContext } from "@/lib/academic";
import { groupLabel, loadTimetableView, SESSION_TYPES, type ViewParams } from "@/lib/teaching";

// CSV of the sessions the viewer shows, for the same selection. Route handlers sit outside the console layout,
// so this checks the Academic Admin context itself; the teaching service still authorizes every read.

const KEYS = ["semester", "version", "year", "stream", "specialization", "group", "view", "lecturer", "venue"] as const;

/** Quotes a CSV field and neutralizes spreadsheet formulas (=, +, -, @ at the start). */
function cell(value: string | number) {
  const text = String(value);
  const safe = /^[=+\-@\t\r]/.test(text) ? `'${text}` : text;
  return `"${safe.replaceAll('"', '""')}"`;
}

export async function GET(request: NextRequest) {
  const context = await getAcademicContext();
  if (!context) return new Response("Forbidden", { status: 403 });
  const search = request.nextUrl.searchParams;
  const params = Object.fromEntries(KEYS.map((key) => [key, search.get(key)?.slice(0, 300) ?? undefined])) as ViewParams;
  const institution = context.institutions.find((row) => row.id === search.get("institution")) ?? context.primary;

  const result = await loadTimetableView(institution.id, params);
  if (result.status !== "ok") return new Response("No validated timetable is available for this selection.", { status: 404 });
  const { view } = result;
  if (!view.validation.ok || view.validation.data.status !== "PASSED") {
    return new Response("Only independently validated timetables can be exported.", { status: 409 });
  }

  const header = ["day", "date", "start", "end", "module_code", "module_name", "session_type", "student_groups", "lecturer", "venue", "delivery_mode"];
  const lines = view.sessions.map((session) => {
    const subject = session.coverage[0];
    return [
      session.day,
      session.teaching_date,
      session.start_time,
      session.end_time,
      subject?.module_code ?? "",
      subject?.module_name ?? "",
      SESSION_TYPES[subject?.session_type ?? ""] ?? subject?.session_type ?? "",
      [...new Set(session.coverage.map((row) => groupLabel(row.group_id)))].join("; "),
      session.lecturer.staff_name,
      session.delivery_mode === "ONLINE" ? "Online" : (session.venue?.venue_name ?? ""),
      session.delivery_mode,
    ]
      .map(cell)
      .join(",");
  });
  const note = view.version.preview_label
    ? [cell(`${view.version.preview_label}. Solver ${view.version.solver_status}; independent validation PASSED.`)]
    : [];
  const body = [...note, header.map(cell).join(","), ...lines].join("\r\n");
  const name = `teaching-timetable-semester-${view.semester}-${view.view}.csv`;
  return new Response(`﻿${body}\r\n`, {
    headers: {
      "Content-Type": "text/csv; charset=utf-8",
      "Content-Disposition": `attachment; filename="${name}"`,
      "Cache-Control": "no-store",
    },
  });
}
