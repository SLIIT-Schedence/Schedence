"""Minimal read-only ``.xlsx`` reader (standard library only).

Enough for the module-catalogue import: reads cell values of every sheet as
strings. No formulas are evaluated and no macros are run.
"""

from __future__ import annotations

import re
import zipfile
import xml.etree.ElementTree as ET
from os import PathLike

_NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}
_M = f"{{{_NS['m']}}}"


def _column_index(cell_ref: str) -> int:
    index = 0
    for letter in re.match(r"[A-Z]+", cell_ref).group():
        index = index * 26 + ord(letter) - 64
    return index - 1


def _text(node: ET.Element) -> str:
    return "".join(t.text or "" for t in node.iter(f"{_M}t"))


Row = tuple[int, list[str | None]]  # (Excel row number, cell values)


def read_workbook(path: str | PathLike[str]) -> dict[str, list[Row]]:
    """Return ``{sheet name: rows}``; each row is (row number, cell strings or None)."""
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        shared = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            shared = [_text(si) for si in root.findall("m:si", _NS)]

        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {rel.get("Id"): rel.get("Target") for rel in rels}

        sheets: dict[str, list[Row]] = {}
        for sheet in workbook.find("m:sheets", _NS):
            target = targets[sheet.get(f"{{{_NS['r']}}}id")].lstrip("/")
            target = target if target.startswith("xl/") else f"xl/{target}"
            rows = []
            for row in ET.fromstring(archive.read(target)).iter(f"{_M}row"):
                values: dict[int, str | None] = {}
                for cell in row.findall("m:c", _NS):
                    kind, value = cell.get("t"), cell.find("m:v", _NS)
                    if kind == "s" and value is not None:
                        values[_column_index(cell.get("r"))] = shared[int(value.text)]
                    elif kind == "inlineStr":
                        values[_column_index(cell.get("r"))] = _text(cell)
                    else:
                        values[_column_index(cell.get("r"))] = value.text if value is not None else None
                if values:
                    rows.append((int(row.get("r")), [values.get(i) for i in range(max(values) + 1)]))
            sheets[sheet.get("name")] = rows
        return sheets


def sheet_records(rows: list[Row]) -> list[dict[str, str | int | None]]:
    """Turn a sheet (first row = header) into dicts. ``_row`` is the Excel row number."""
    header = [(h or "").strip() for h in rows[0][1]]
    records = []
    for number, values in rows[1:]:
        record = dict(zip(header, values + [None] * (len(header) - len(values))))
        record["_row"] = number
        records.append(record)
    return records
