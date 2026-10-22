import { dutyRequest, getRosterExport, UUID_PATTERN } from "@/lib/duty-allocation";

const XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
const headers = { "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff" };

export async function GET(request: Request, { params }: { params: Promise<{ rosterId: string; format: string }> }) {
  const { rosterId, format } = await params;
  if (!UUID_PATTERN.test(rosterId) || !["xlsx", "pdf"].includes(format)) return Response.json({ message: "Download not found." }, { status: 404, headers });
  const version = new URL(request.url).searchParams.get("version");
  if (!version || !/^[1-9][0-9]{0,9}$/.test(version)) return Response.json({ message: "Refresh the roster before downloading." }, { status: 400, headers });
  const rowVersion = Number(version);
  const result = format === "xlsx"
    ? await dutyRequest<ArrayBuffer>(`/rosters/${rosterId}/export.xlsx`, { responseType: "binary", query: { expected_version: rowVersion }, timeoutMs: 90_000 })
    : await getRosterExport(rosterId, rowVersion);
  if (!result.ok) return Response.json({ message: result.error.message, code: result.error.code }, { status: result.error.status >= 400 ? result.error.status : 502, headers });
  try {
    let body: ArrayBuffer;
    if (format === "xlsx") body = result.data as ArrayBuffer;
    else {
      const { createDutyRosterPdf } = await import("@/lib/duty-roster-pdf");
      body = createDutyRosterPdf(result.data as import("@/lib/duty-allocation-types").RosterExport).output("arraybuffer");
    }
    return new Response(body, { headers: { ...headers, "Content-Type": format === "xlsx" ? XLSX_TYPE : "application/pdf",
      "Content-Disposition": `attachment; filename="Schedence-duty-roster-${rosterId}-revision${rowVersion}.${format}"` } });
  } catch (cause) {
    return Response.json({ message: cause instanceof Error && cause.name === "DutyRosterPdfError" ? cause.message
      : "The roster file could not be generated. Please try again." }, { status: 500, headers });
  }
}
