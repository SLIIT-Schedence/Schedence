"""Read-only saved-roster exports. OOXML uses the standard library; no new dependency."""

from __future__ import annotations

import io
import re
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

DEMO_WARNING = "SYNTHETIC DEMO – NOT AN OFFICIAL PUBLISHED ROSTER"
UNPUBLISHED_WARNING = "NOT AN OFFICIAL PUBLISHED ROSTER"
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
HEADERS = ("Examination period", "Timetable reference", "Exam date", "Start time", "End time", "Module code",
           "Module name", "Room / venue", "Assigned staff name", "Staff ID", "Staff number", "Duty role",
           "Roster version", "Approval status")


def export_warning(roster) -> str | None:
    if roster.synthetic or roster.timetable_reference.startswith("DEMO-UNPUBLISHED:"):
        return DEMO_WARNING
    if roster.status != "PUBLISHED" or not roster.timetable_verified:
        return UNPUBLISHED_WARNING
    return None


def export_filename(roster, extension: str) -> str:
    period = re.sub(r"[^A-Za-z0-9_-]", "_", roster.period_code)
    return f"Schedence-duty-roster-{period}-v{roster.version}-{roster.status}.{extension}"


def build_xlsx(data) -> bytes:
    """All assignments as a styled Excel table; every text cell is literal, never a formula."""
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    def xml(value):
        return escape(re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(value)))
    def cell(column, row, value, style=0):
        if isinstance(value, int):
            return f'<c r="{column}{row}" s="{style}"><v>{value}</v></c>'
        return f'<c r="{column}{row}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{xml(value if value is not None else "Not recorded")}</t></is></c>'
    r = data.roster
    metadata = ["SCHEDENCE / EXAMINATION DUTY ROSTER", f"Examination period: {data.period_name} ({r.period_code})",
                f"Status: {r.status} | Version: {r.version} | Assignments: {len(data.assignments)} | Times: examination local time",
                f"Timetable reference: {r.timetable_reference}", data.warning or "OFFICIAL PUBLISHED ROSTER",
                " ".join(data.notes)]
    rows = [f'<row r="{i}" ht="{30 if i == 6 else 24}" customHeight="1">{cell("A", i, value, 2 if i in (1, 5) else 0)}</row>'
            for i, value in enumerate(metadata, 1)]
    rows.append('<row r="8" ht="32" customHeight="1">' + ''.join(cell(chr(65+i), 8, value, 1) for i, value in enumerate(HEADERS)) + '</row>')
    for index, a in enumerate(data.assignments, 9):
        values = [r.period_code, r.timetable_reference, a.starts_at.date().isoformat(), a.starts_at.strftime("%H:%M"),
                  a.ends_at.strftime("%H:%M"), a.module_code, a.module_name, a.venue_id, a.staff_name,
                  a.staff_id, a.staff_number, a.role, r.version, r.status]
        rows.append(f'<row r="{index}" ht="32" customHeight="1">' + ''.join(cell(chr(65+i), index, value) for i, value in enumerate(values)) + '</row>')
    end = 8 + len(data.assignments)
    table_range = f"A8:N{end}"
    widths = (26, 48, 14, 12, 12, 15, 48, 20, 32, 40, 18, 18, 15, 18)
    cols = ''.join(f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>' for i, w in enumerate(widths, 1))
    sheet = (f'<worksheet xmlns="{ns}" xmlns:r="{rel}"><sheetPr><pageSetUpPr fitToPage="1"/></sheetPr>'
             '<sheetViews><sheetView workbookViewId="0"><pane ySplit="8" topLeftCell="A9" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
             f'<cols>{cols}</cols><sheetData>{"".join(rows)}</sheetData><mergeCells count="6">'
             + ''.join(f'<mergeCell ref="A{i}:N{i}"/>' for i in range(1, 7))
             + '</mergeCells><pageMargins left="0.25" right="0.25" top="0.4" bottom="0.4" header="0.2" footer="0.2"/>'
             '<pageSetup paperSize="9" orientation="landscape" fitToWidth="1" fitToHeight="0"/>'
             '<headerFooter><oddFooter>&amp;CPage &amp;P of &amp;N</oddFooter></headerFooter>'
             '<tableParts count="1"><tablePart r:id="rId1"/></tableParts></worksheet>')
    table = (f'<table xmlns="{ns}" id="1" name="DutyRoster" displayName="DutyRoster" ref="{table_range}" totalsRowShown="0">'
             f'<autoFilter ref="{table_range}"/><tableColumns count="14">'
             + ''.join(f'<tableColumn id="{i}" name="{xml(h)}"/>' for i, h in enumerate(HEADERS, 1))
             + '</tableColumns><tableStyleInfo name="TableStyleMedium2" showFirstColumn="0" showLastColumn="0" showRowStripes="1" showColumnStripes="0"/></table>')
    styles = (f'<styleSheet xmlns="{ns}"><fonts count="3"><font><sz val="11"/><name val="Calibri"/></font>'
              '<font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Calibri"/></font>'
              '<font><b/><sz val="13"/><name val="Calibri"/></font></fonts>'
              '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
              '<fill><patternFill patternType="solid"><fgColor rgb="FF315BD6"/><bgColor indexed="64"/></patternFill></fill></fills>'
              '<borders count="1"><border/></borders><cellStyleXfs count="1"><xf/></cellStyleXfs><cellXfs count="3">'
              '<xf xfId="0" fontId="0" fillId="0" borderId="0" numFmtId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>'
              '<xf xfId="0" fontId="1" fillId="2" borderId="0" numFmtId="0" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf>'
              '<xf xfId="0" fontId="2" fillId="0" borderId="0" numFmtId="0" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf>'
              '</cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')
    content = ('<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
               '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
               '<Default Extension="xml" ContentType="application/xml"/>'
               '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
               '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
               '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
               '<Override PartName="/xl/tables/table1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.table+xml"/></Types>')
    def relationships(items):
        return '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + ''.join(
            f'<Relationship Id="rId{i}" Type="{rel}/{kind}" Target="{target}"/>' for i, kind, target in items) + '</Relationships>'
    parts = {"[Content_Types].xml": content, "_rels/.rels": relationships([(1, "officeDocument", "xl/workbook.xml")]),
             "xl/workbook.xml": f'<workbook xmlns="{ns}" xmlns:r="{rel}"><sheets><sheet name="Duty roster" sheetId="1" r:id="rId1"/></sheets><definedNames><definedName name="_xlnm.Print_Titles" localSheetId="0">\'Duty roster\'!$1:$8</definedName></definedNames></workbook>',
             "xl/_rels/workbook.xml.rels": relationships([(1, "worksheet", "worksheets/sheet1.xml"), (2, "styles", "styles.xml")]),
             "xl/worksheets/sheet1.xml": sheet, "xl/styles.xml": styles, "xl/tables/table1.xml": table,
             "xl/worksheets/_rels/sheet1.xml.rels": relationships([(1, "table", "../tables/table1.xml")])}
    output = io.BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for name, value in parts.items():
            archive.writestr(name, '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' + value)
    return output.getvalue()
