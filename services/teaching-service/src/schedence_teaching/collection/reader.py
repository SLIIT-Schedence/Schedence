"""CSV parsing with preserved identifiers and safe physical-row diagnostics."""

import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path
import re

from schedence_teaching.integration import ReadinessDiagnostic, SourceAttestation

from .specification import SOURCE_COLUMNS, TABLES, headers


class CellError(ValueError):
    pass


def text(value, *, optional=False):
    if value is None or not value.strip():
        if optional:
            return None
        raise CellError("MISSING")
    return value


def integer(value, *, optional=False):
    value = text(value, optional=optional)
    if value is None:
        return None
    if not re.fullmatch(r"[0-9]+", value):
        raise CellError("INVALID")
    return int(value)


def boolean(value, *, optional=False):
    value = text(value, optional=optional)
    if value is None:
        return None
    if value not in {"TRUE", "FALSE"}:
        raise CellError("INVALID")
    return value == "TRUE"


def identifiers(value, *, allow_none=False):
    value = text(value)
    if value == "NONE" and allow_none:
        return []
    values = value.split(";")
    if any(not item.strip() for item in values) or len(values) != len(set(values)):
        raise CellError("INVALID")
    return values


def windows(value):
    value = text(value)
    if value == "NONE":
        return []
    result = []
    for interval in value.split(";"):
        match = re.fullmatch(r"([0-9]{2}):([0-9]{2})-([0-9]{2}):([0-9]{2})", interval)
        if not match:
            raise CellError("INVALID")
        first_h, first_m, last_h, last_m = map(int, match.groups())
        if first_h >= 24 or first_m >= 60 or last_h > 24 or last_m >= 60 or last_h == 24 and last_m != 0:
            raise CellError("INVALID")
        start, end = first_h * 60 + first_m, last_h * 60 + last_m
        if start >= end:
            raise CellError("INVALID")
        result.append(dict(start_minute=start, end_minute=end))
    return result


@dataclass(frozen=True)
class CollectionRow:
    table: str
    number: int
    values: dict[str, str]

    def issue(self, status, code, column, message, *, path=()):
        return ReadinessDiagnostic(status, code, path, message, f"{self.table}.csv", "CSV", self.number, column)

    def attestation(self, institution, owner, synthetic):
        return SourceAttestation(owner=owner, institution_id=institution, status=self.values["Confirmation Status"],
            provenance=dict(source=text(self.values["Source"]), reference=text(self.values["Source Reference"])),
            confirmation_reference=text(self.values["Confirmation Reference"]),
            effective_from=date.fromisoformat(text(self.values["Valid From"])),
            effective_until=date.fromisoformat(text(self.values["Valid Until"])), synthetic=synthetic,
            location=dict(workbook=f"{self.table}.csv", worksheet="CSV", row=self.number),
            columns={item.field: item.header for item in TABLES[self.table] + SOURCE_COLUMNS})


def read_table(directory: Path, table: str):
    file = directory / f"{table}.csv"
    rows, issues = [], []
    try:
        with file.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle, strict=True)
            supplied = next(reader, [])
            expected = headers(table)
            if len(supplied) != len(set(supplied)) or set(supplied) != set(expected):
                issues.append(CollectionRow(table, 1, {}).issue("INVALID", "template_headers", None,
                    "Use the supplied distinct headers without adding or removing columns."))
                return rows, issues
            previous_line = reader.line_num
            for values in reader:
                number = previous_line + 1
                previous_line = reader.line_num
                if not values or all(not value.strip() for value in values):
                    continue
                if len(values) != len(supplied):
                    issues.append(CollectionRow(table, number, {}).issue("INVALID", "template_width", None,
                        "Provide exactly one cell for every header; quote cells containing commas."))
                    continue
                rows.append(CollectionRow(table, number, dict(zip(supplied, values))))
    except FileNotFoundError:
        issues.append(CollectionRow(table, 1, {}).issue("MISSING", "template_file", None,
            "Supply the completed template, or its header-only file where an explicit policy permits no records."))
    except (OSError, UnicodeError, csv.Error):
        issues.append(CollectionRow(table, 1, {}).issue("INVALID", "template_unreadable", None,
            "Supply readable UTF-8 CSV with valid quoting."))
    return rows, issues
