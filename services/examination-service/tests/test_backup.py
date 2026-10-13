"""Backup files: verification and type conversion (no database)."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

from app.seed.backup import load_rows, verify_backup


def _write_backup(tmp_path: Path, tables: dict[str, list[dict]]) -> Path:
    manifest = {"tables": {}}
    for table, rows in tables.items():
        path = tmp_path / f"{table}.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        manifest["tables"][table] = {"rows": len(rows), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return tmp_path


OLD_MODULE = {
    "module_code": "SYN1204", "module_name": "Web Development Fundamentals", "academic_year": 1, "semester": 2,
    "specialization": "Common", "credits": 3, "difficulty": 2, "exam_type": "practical",
    "duration_minutes": 120, "reading_time_minutes": 0,
}
BLOCK = {
    "block_id": 7, "venue_id": "V-LAB1", "block_date": "2026-11-17", "start_time": "10:30:00",
    "end_time": "12:30:00", "source": "teaching_timetable", "reason": "Simulated teaching session",
}


def test_valid_backup_verifies(tmp_path: Path) -> None:
    assert verify_backup(_write_backup(tmp_path, {"seed_modules": [OLD_MODULE]})) == []


def test_tampered_backup_is_detected(tmp_path: Path) -> None:
    backup = _write_backup(tmp_path, {"seed_modules": [OLD_MODULE]})
    (backup / "seed_modules.jsonl").write_text("{}\n{}\n", encoding="utf-8")
    problems = verify_backup(backup)
    assert any("checksum" in p for p in problems) and any("2 rows in file" in p for p in problems)


def test_pre_0002_module_rows_get_reading_flag(tmp_path: Path) -> None:
    backup = _write_backup(tmp_path, {"seed_modules": [OLD_MODULE, {**OLD_MODULE, "module_code": "SYN1201",
                                                                      "reading_time_minutes": 10}]})
    rows = load_rows(backup, "seed_modules")
    assert [r["has_extra_reading_time"] for r in rows] == [False, True]
    assert rows[0]["specialization"] == "Common" and rows[0]["exam_type"] == "practical"


def test_types_are_restored_and_identity_is_dropped(tmp_path: Path) -> None:
    [row] = load_rows(_write_backup(tmp_path, {"seed_venue_blocks": [BLOCK]}), "seed_venue_blocks")
    assert "block_id" not in row
    assert row["block_date"] == dt.date(2026, 11, 17) and row["start_time"] == dt.time(10, 30)


def test_missing_table_file_loads_nothing(tmp_path: Path) -> None:
    assert load_rows(_write_backup(tmp_path, {}), "seed_students") == []
