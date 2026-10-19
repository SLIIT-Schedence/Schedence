"""Backup files: verification and type conversion (no database)."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from pathlib import Path

from app.seed.backup import TABLES, _to_json, load_rows, restore_seating, verify_backup


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


# --- Seating rules (migration 0005) ------------------------------------------------------

RULE = {
    "rule_id": 3, "scope": "venue", "venue_id": "F303", "percentage": 70, "version": 2, "reason": "Wide desks",
    "updated_by": "6f1c2a8e-1d2b-4c3d-9e8f-0a1b2c3d4e5f", "updated_at": "2026-10-15T08:30:00.123456+00:00",
}
HISTORY = {
    "history_id": 9, "rule_id": 3, "action": "update", "scope": "venue", "venue_id": "F303", "old_percentage": 60,
    "new_percentage": 70, "version": 2, "changed_by": None, "changed_at": "2026-10-15T08:30:00+05:30",
    "reason": "Wide desks",
}


def test_seating_rows_keep_their_ids_and_types(tmp_path: Path) -> None:
    backup = _write_backup(tmp_path, {"exam_seating_rules": [RULE], "exam_seating_rule_history": [HISTORY]})
    [rule] = load_rows(backup, "exam_seating_rules")
    [history] = load_rows(backup, "exam_seating_rule_history")
    assert rule["rule_id"] == 3 and history["history_id"] == 9 and history["rule_id"] == 3
    assert rule["updated_by"] == uuid.UUID(RULE["updated_by"])
    assert rule["updated_at"] == dt.datetime(2026, 10, 15, 8, 30, 0, 123456, tzinfo=dt.UTC)
    assert history["changed_at"].utcoffset() == dt.timedelta(hours=5, minutes=30)
    assert history["changed_by"] is None


def test_export_values_round_trip(tmp_path: Path) -> None:
    user = uuid.uuid4()
    when = dt.datetime(2026, 10, 15, 3, 0, 0, 1, tzinfo=dt.UTC)
    raw = {**RULE, "updated_by": _to_json(user), "updated_at": _to_json(when)}
    [rule] = load_rows(_write_backup(tmp_path, {"exam_seating_rules": [raw]}), "exam_seating_rules")
    assert rule["updated_by"] == user and rule["updated_at"] == when


def test_seed_identity_is_still_regenerated(tmp_path: Path) -> None:
    [row] = load_rows(_write_backup(tmp_path, {"seed_venue_blocks": [BLOCK]}), "seed_venue_blocks")
    assert "block_id" not in row


async def test_backup_from_0004_leaves_seating_alone(tmp_path: Path) -> None:
    backup = _write_backup(tmp_path, {"seed_modules": [OLD_MODULE]})

    class NoDatabase:
        def __getattr__(self, name):
            raise AssertionError("the database must not be used")

    result = await restore_seating(NoDatabase(), backup)  # type: ignore[arg-type]
    assert result.mode == "not_in_backup" and result.counts == {}


def test_export_order_puts_parents_first() -> None:
    assert TABLES[-2:] == ("exam_seating_rules", "exam_seating_rule_history")
    assert TABLES.index("seed_venues") < TABLES.index("exam_seating_rules")  # overrides need their venue


OLD_PERIOD = {
    "period_code": "PP1-2026-S2-FINAL", "period_name": "Synthetic Semester 2 Final Examinations (PP1)",
    "academic_year_label": "2026/2027", "semester": 2, "start_date": "2026-11-16", "end_date": "2026-11-28",
    "day_start_time": "08:30:00", "day_end_time": "17:30:00", "include_saturdays": False, "include_sundays": False,
}


def test_pre_0007_periods_load_as_final(tmp_path: Path) -> None:
    [row] = load_rows(_write_backup(tmp_path, {"seed_exam_periods": [OLD_PERIOD]}), "seed_exam_periods")
    assert row["exam_kind"] == "FINAL" and row["start_date"] == dt.date(2026, 11, 16)


def test_saved_exam_kind_is_kept(tmp_path: Path) -> None:
    backup = _write_backup(tmp_path, {"seed_exam_periods": [{**OLD_PERIOD, "exam_kind": "MID"}]})
    [row] = load_rows(backup, "seed_exam_periods")
    assert row["exam_kind"] == "MID"


def test_pre_0008_modules_load_as_final_only_and_regular(tmp_path: Path) -> None:
    [row] = load_rows(_write_backup(tmp_path, {"seed_modules": [OLD_MODULE]}), "seed_modules")
    assert (row["has_final_exam"], row["has_mid_exam"], row["mid_duration_minutes"], row["delivery_mode"],
            row["block_category"]) == (True, False, None, "regular", None)


def test_saved_module_exam_settings_are_kept(tmp_path: Path) -> None:
    saved = {**OLD_MODULE, "has_extra_reading_time": False, "has_final_exam": False, "has_mid_exam": True,
             "mid_duration_minutes": 45, "delivery_mode": "block", "block_category": "full_module"}
    [row] = load_rows(_write_backup(tmp_path, {"seed_modules": [saved]}), "seed_modules")
    assert (row["has_final_exam"], row["mid_duration_minutes"], row["block_category"]) == (False, 45, "full_module")
