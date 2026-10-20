"""Backup files: verification and type conversion (no database)."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from pathlib import Path

import pytest

from app.saved_timetables.content import content_fingerprint
from app.seed.backup import (
    EXPORT_TABLES,
    TABLES,
    RestoreError,
    _check_backup_timetables,
    _to_json,
    load_rows,
    restore_seating,
    restore_timetables,
    verify_backup,
)


def _write_backup(tmp_path: Path, tables: dict[str, list[dict]]) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
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



# --- Saved timetables (migrations 0010 and 0011) ----------------------------------------------

USER = "6f1c2a8e-1d2b-4c3d-9e8f-0a1b2c3d4e5f"
INSTITUTION = "11111111-1111-4111-8111-111111111111"
SESSION = {"timetable_id": 1, "session_id": "S1", "module_code": "IT1010", "module_name": "M", "exam_type": "written",
           "candidate_scope": "all", "academic_year": 1, "semester": 2, "delivery_mode": "regular",
           "session_number": 1, "session_count": 1, "starts_at": "2026-11-16T09:00:00",
           "ends_at": "2026-11-16T11:00:00", "duration_minutes": 120, "reading_time_minutes": 0, "candidates": 1,
           "normal_candidates": 1, "repeat_candidates": 0}
ROOM = {"timetable_id": 1, "session_id": "S1", "venue_id": "F303", "venue_name": "F303", "venue_type": "lecture_hall",
        "seats": 1, "exam_capacity": 10}
SEAT = {"timetable_id": 1, "session_id": "S1", "venue_id": "F303", "seat_number": 1, "student_id": "IT10000001",
        "registration_type": "normal"}


def _timetable(tmp_path: Path, **changes) -> Path:
    loaded = [load_rows(_write_backup(tmp_path / t, {t: [r]}), t)[0] for t, r in (
        ("exam_timetable_sessions", SESSION), ("exam_timetable_rooms", ROOM), ("exam_timetable_seats", SEAT))]
    header = {"timetable_id": 1, "institution_id": INSTITUTION, "period_code": "PP1-2026-S2-FINAL", "draft_number": 1,
              "status": "PUBLISHED", "revision": 3, "approved_by": USER, "approved_at": "2026-10-20T08:00:00+00:00",
              "published_by": USER, "published_at": "2026-10-21T08:00:00+00:00",
              "content_fingerprint": content_fingerprint([loaded[0]], [loaded[1]], [loaded[2]]), **changes}
    return _write_backup(tmp_path / "b", {"exam_timetables": [header], "exam_timetable_sessions": [SESSION],
                                          "exam_timetable_rooms": [ROOM], "exam_timetable_seats": [SEAT]})


def _check(backup: Path) -> None:
    headers = {r["timetable_id"]: r for r in load_rows(backup, "exam_timetables")}
    children = {}
    for table in ("exam_timetable_sessions", "exam_timetable_rooms", "exam_timetable_seats"):
        for row in load_rows(backup, table):
            children.setdefault(table, {}).setdefault(row["timetable_id"], []).append(row)
    _check_backup_timetables(headers, children)


def test_timetables_are_exported_after_seed_and_seating_tables() -> None:
    assert EXPORT_TABLES[:len(TABLES)] == TABLES
    assert EXPORT_TABLES[len(TABLES):] == ("exam_timetables", "exam_timetable_sessions", "exam_timetable_rooms",
                                           "exam_timetable_seats", "exam_timetable_history")


def test_timetable_rows_keep_their_ids_and_types(tmp_path: Path) -> None:
    [header] = load_rows(_timetable(tmp_path), "exam_timetables")
    assert header["timetable_id"] == 1 and header["approved_at"].tzinfo is not None
    [session] = load_rows(tmp_path / "b", "exam_timetable_sessions")
    assert session["starts_at"].isoformat() == "2026-11-16T09:00:00"


def test_a_consistent_backup_passes_the_checks(tmp_path: Path) -> None:
    _check(_timetable(tmp_path))


@pytest.mark.parametrize("changes,message", [
    ({"revision": 2}, "does not match status"),
    ({"status": "LIVE"}, "unknown status"),
    ({"published_at": None}, "do not match status"),
    ({"status": "DRAFT", "revision": 1}, "do not match status"),  # approval details on a draft
    ({"content_fingerprint": "0" * 64}, "content fingerprint"),
])
def test_inconsistent_timetables_are_refused(tmp_path: Path, changes: dict, message: str) -> None:
    with pytest.raises(RestoreError, match=message):
        _check(_timetable(tmp_path, **changes))


def test_seats_of_a_missing_timetable_are_refused(tmp_path: Path) -> None:
    backup = _write_backup(tmp_path, {"exam_timetables": [], "exam_timetable_seats": [SEAT]})
    with pytest.raises(RestoreError, match="not in the backup"):
        _check(backup)


async def test_backup_without_timetables_leaves_them_alone(tmp_path: Path) -> None:
    backup = _write_backup(tmp_path, {"seed_modules": [OLD_MODULE]})

    class NoDatabase:
        def __getattr__(self, name):
            raise AssertionError("the database must not be used")

    result = await restore_timetables(NoDatabase(), backup)  # type: ignore[arg-type]
    assert result.mode == "not_in_backup" and result.counts == {}
    skipped = await restore_timetables(NoDatabase(), _timetable(tmp_path), skip=True)  # type: ignore[arg-type]
    assert skipped.mode == "skipped"
