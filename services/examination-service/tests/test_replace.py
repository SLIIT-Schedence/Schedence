"""Atomic SYN -> new dataset replacement (no database: fake session)."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path

import pytest

from schedence_db import session_scope

from app.seed import replace as replace_module
from app.seed.backup import BACKUP_ROOT, load_rows, verify_backup
from app.seed.generator import generate_dataset
from app.seed.replace import ReplacementError, expected_counts, plan_deletions, replace_dataset
from app.seed.seeder import TABLE_SPECS, SeedResult, TableChange


@pytest.fixture(scope="module")
def data():
    return generate_dataset()


def _write_backup(path: Path, tables: dict[str, list[dict]]) -> Path:
    path.mkdir()
    manifest = {"tables": {}}
    for table, rows in tables.items():
        f = path / f"{table}.jsonl"
        f.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        manifest["tables"][table] = {"rows": len(rows), "sha256": hashlib.sha256(f.read_bytes()).hexdigest()}
    (path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return path


SYN_STUDENT = {"student_id": "STU-0001", "academic_year": 1, "specialization": "Common"}
# The 9 fictional venues currently in the database (before the campus venue replacement).
OLD_VENUES = [
    {"venue_id": vid, "venue_name": name, "venue_type": vtype, "normal_capacity": normal, "exam_capacity": exam}
    for vid, name, vtype, normal, exam in (
        ("V-AUD", "Main Auditorium", "auditorium", 300, 150), ("V-EXH", "Examination Hall A", "exam_hall", 100, 100),
        ("V-EXH2", "Examination Hall B", "exam_hall", 80, 80), ("V-LH1", "Lecture Hall 1", "lecture_hall", 100, 50),
        ("V-LH2", "Lecture Hall 2", "lecture_hall", 80, 40), ("V-LAB1", "Computer Lab 1", "computer_lab", 60, 45),
        ("V-LAB2", "Computer Lab 2", "computer_lab", 40, 30), ("V-LAB3", "Computer Lab 3", "computer_lab", 60, 50),
        ("V-LAB4", "Computer Lab 4", "computer_lab", 50, 40),
    )
]
OLD_BLOCK = {"venue_id": "V-LAB1", "block_date": "2026-11-17", "start_time": "10:30:00", "end_time": "12:30:00",
             "source": "teaching_timetable", "reason": "Simulated teaching session"}


def test_plan_deletes_only_backup_rows_missing_from_new_dataset(data) -> None:
    shared = dataclasses.asdict(data.venues[0])
    plan = plan_deletions({"seed_students": [SYN_STUDENT], "seed_venues": [OLD_VENUES[0], shared]}, data)
    assert plan["seed_students"] == [("STU-0001",)]
    assert plan["seed_venues"] == [("V-AUD",)]  # identical campus venue in the new dataset is kept
    assert plan["seed_registrations"] == []  # nothing outside the backup is ever planned


def test_venue_replacement_plan_touches_only_venues_and_blocks(tmp_path, data) -> None:
    # A backup of today's database: same non-venue data, 9 old venues, old blocks.
    current = {spec.table: [dataclasses.asdict(r) for r in spec.records(data)] for spec in TABLE_SPECS}
    current["seed_venues"] = OLD_VENUES
    current["seed_venue_blocks"] = [OLD_BLOCK]
    backup = _write_backup(tmp_path / "b", {table: [json.loads(json.dumps(r, default=str)) for r in rows]
                                           for table, rows in current.items()})
    rows = {spec.table: load_rows(backup, spec.table) for spec in TABLE_SPECS}
    planned = {t: len(keys) for t, keys in plan_deletions(rows, data).items()}
    assert planned == {
        "seed_exam_period_exclusions": 0, "seed_exam_periods": 0, "seed_venue_blocks": 1, "seed_venues": 9,
        "seed_registrations": 0, "seed_students": 0, "seed_module_specializations": 0, "seed_modules": 0,
    }
    assert "building" not in rows["seed_venues"][0]  # pre-0003 rows load without the new columns


def test_plan_deletes_children_before_parents(data) -> None:
    order = list(plan_deletions({}, data))
    assert order.index("seed_registrations") < order.index("seed_students")
    assert order.index("seed_registrations") < order.index("seed_modules")
    assert order.index("seed_module_specializations") < order.index("seed_modules")
    assert order.index("seed_venue_blocks") < order.index("seed_venues")
    assert order.index("seed_exam_period_exclusions") < order.index("seed_exam_periods")


def test_expected_final_counts(data) -> None:
    assert expected_counts(data) == {
        "seed_modules": 42, "seed_module_specializations": 91, "seed_students": 390,
        "seed_registrations": 1700, "seed_venues": 62, "seed_venue_blocks": 27,
        "seed_exam_periods": 1, "seed_exam_period_exclusions": 1,
    }


def _local_backup() -> Path | None:
    backups = sorted(BACKUP_ROOT.glob("seed_backup_*")) if BACKUP_ROOT.is_dir() else []
    return backups[0] if backups else None


@pytest.mark.skipif(_local_backup() is None, reason="no local SYN backup")
def test_real_backup_delete_plan(data) -> None:
    backup = _local_backup()
    assert verify_backup(backup) == []
    rows = {spec.table: load_rows(backup, spec.table) for spec in TABLE_SPECS}
    planned = {t: len(keys) for t, keys in plan_deletions(rows, data).items()}
    # The original SYN backup: its 6 fictional venues and 22 blocks are all gone from the new dataset.
    assert planned == {
        "seed_exam_period_exclusions": 1, "seed_exam_periods": 1, "seed_venue_blocks": 22, "seed_venues": 6,
        "seed_registrations": 748, "seed_students": 200, "seed_module_specializations": 0, "seed_modules": 20,
    }


# --- Atomicity with a fake session -------------------------------------------------


class FakeResult:
    rowcount = 0

    def scalar_one(self):
        return 0


class FakeSession:
    def __init__(self) -> None:
        self.events: list[str] = []

    async def execute(self, statement, *args, **kwargs):
        self.events.append("execute")
        return FakeResult()

    async def commit(self):
        self.events.append("commit")

    async def rollback(self):
        self.events.append("rollback")

    async def close(self):
        self.events.append("close")


async def test_failure_after_deletes_rolls_back_and_never_commits(tmp_path, monkeypatch, data) -> None:
    backup = _write_backup(tmp_path / "b", {"seed_students": [SYN_STUDENT]})

    async def failing_seed(*args, **kwargs):
        raise RuntimeError("insert failed")

    monkeypatch.setattr(replace_module, "seed_dataset", failing_seed)
    session = FakeSession()
    with pytest.raises(RuntimeError):
        async with session_scope(lambda: session, commit=True) as s:
            await replace_dataset(s, data, backup)
    assert "execute" in session.events  # deletes were issued ...
    assert "commit" not in session.events  # ... but never committed
    assert session.events[-2:] == ["rollback", "close"]


async def test_leftover_rows_fail_the_safety_check(tmp_path, monkeypatch, data) -> None:
    backup = _write_backup(tmp_path / "b", {})

    async def seed_with_leftovers(*args, **kwargs):
        return SeedResult(False, [TableChange("seed_students", extra=3)], {}, [])

    monkeypatch.setattr(replace_module, "seed_dataset", seed_with_leftovers)
    session = FakeSession()
    with pytest.raises(ReplacementError, match="rows not in the dataset remain"):
        async with session_scope(lambda: session, commit=True) as s:
            await replace_dataset(s, data, backup)
    assert "commit" not in session.events and "rollback" in session.events


async def test_tampered_backup_stops_before_any_statement(tmp_path, data) -> None:
    backup = _write_backup(tmp_path / "b", {"seed_students": [SYN_STUDENT]})
    (backup / "seed_students.jsonl").write_text("{}\n", encoding="utf-8")
    session = FakeSession()
    with pytest.raises(ReplacementError, match="verification"):
        await replace_dataset(session, data, backup)
    assert session.events == []
