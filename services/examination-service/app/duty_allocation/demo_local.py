"""LOCAL_DEMO row source: the synthetic Member 3 seed pack (manifest.json + csv/*.csv) as m3_demo rows.

No database is involved. Files are only read. The CSV columns and types come from the seed
manifest and are checked against ``TABLE_COLUMNS`` by ``load_m3_demo_records`` like live rows.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.duty_allocation.demo_database import TABLE_COLUMNS, DemoDatabaseUnavailable, DemoSchemaError

_CONVERTERS = {
    "INTEGER": int, "NUMERIC": Decimal, "BOOLEAN": lambda v: v.strip().lower() in ("true", "1", "t", "yes"),
    "DATE": dt.date.fromisoformat, "TIME": dt.time.fromisoformat, "TIMESTAMP": dt.datetime.fromisoformat,
}


class CsvRowSource:
    """RowSource over the seed-pack files. Parsed once, then served from memory."""

    def __init__(self, seed_dir: str | Path) -> None:
        self.seed_dir = Path(seed_dir)
        self._tables: dict[str, list[dict]] | None = None

    def _load(self) -> dict[str, list[dict]]:
        if self._tables is not None:
            return self._tables
        manifest_path = self.seed_dir / "manifest.json"
        if not manifest_path.is_file():
            raise DemoDatabaseUnavailable("The local demo seed pack was not found (manifest.json is missing).")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            tables: dict[str, list[dict]] = {}
            for name, info in manifest["sheets"].items():
                if not info["seed"]:
                    continue
                with (self.seed_dir / "csv" / f"{name.lower()}.csv").open(encoding="utf-8-sig", newline="") as handle:
                    tables[name.lower()] = [
                        {c: self._convert(row[c], info["column_types"].get(c, "TEXT")) for c in info["headers"]}
                        for row in csv.DictReader(handle)]
        except (OSError, KeyError, ValueError, InvalidOperation, json.JSONDecodeError):
            raise DemoSchemaError("The local demo seed pack could not be read as the Member 3 seed format.") from None
        self._tables = tables
        return tables

    @staticmethod
    def _convert(value: str, kind: str):
        return None if value == "" else _CONVERTERS.get(kind, str)(value)

    async def begin_read_only(self) -> None:  # files only: nothing to open
        return None

    async def schema_columns(self) -> dict[str, set[str]]:
        tables = self._load()
        return {name: set(rows[0]) if rows else set(TABLE_COLUMNS.get(name, ())) for name, rows in tables.items()}

    async def fetch(self, table: str) -> list[dict]:
        if table not in TABLE_COLUMNS:
            raise DemoSchemaError(f"Table {table!r} is not on the m3_demo read allow-list.")
        tables = self._load()
        if table not in tables:
            raise DemoSchemaError(f"m3_demo.{table} is missing from the seed pack.")
        return [{c: row.get(c) for c in TABLE_COLUMNS[table]} for row in tables[table]]
