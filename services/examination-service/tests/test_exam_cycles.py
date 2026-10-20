"""Examination cycle module mapping: selection, registrations, seeding (SYNTHETIC data, no database)."""

from __future__ import annotations

import dataclasses
from collections import Counter
from dataclasses import replace

import pytest

from app.scheduling import generate_timetable, load_snapshot
from app.scheduling.inputs import SnapshotError, validate_inputs
from app.scheduling.types import SolveStatus
from app.seed.cycles import build_synthetic_cycle
from app.seed.generator import generate_dataset
from app.seed.records import ExamPeriodModule
from app.seed.validation import validate_dataset
from app.sources import DatasetSource
from scheduling_support import make_case
from test_seeder import FakeStore

A, B, C = ("IT30000001", "IT30000002"), ("IT30000003",), ("IT30000004", "IT30000005")
THREE = (A, B, C)  # IT1001 normal candidates; IT1002 and IT1003 repeat candidates


async def _cycle(cycle, *, module_codes=None, **case):
    _, source, store = await make_case(candidates=case.pop("candidates", THREE), cycle=cycle, load=False, **case)
    return await load_snapshot(source, store, period_code="TEST", module_codes=module_codes, from_cycle=True)


# --- Selection -----------------------------------------------------------------------------


async def test_full_cycle_schedules_only_mapped_modules():
    # IT1003 is a semester 2 module of a semester 2 period, but it is not mapped: never scheduled.
    snapshot = await _cycle((("IT1001", "all"), ("IT1002", "all")))
    assert [m.module_code for m in snapshot.modules] == ["IT1001", "IT1002"]
    assert {r.module_code for r in snapshot.registrations} == {"IT1001", "IT1002"}
    assert [c.module_code for c in snapshot.cycle_modules] == ["IT1001", "IT1002"]
    assert generate_timetable(snapshot).status in (SolveStatus.OPTIMAL, SolveStatus.FEASIBLE)


async def test_repeat_only_module_loads_only_repeat_registrations():
    # IT1003 (semester 1) is re-examined in the semester 2 cycle for repeat candidates only.
    candidates = (A, B, C + ("IT30000001",))
    _, source, store = await make_case(candidates=candidates, semesters=(2, 2, 1),
                                       cycle=(("IT1001", "all"), ("IT1003", "repeat_only")))
    snapshot = await load_snapshot(source, store, period_code="TEST", from_cycle=True)
    modules = {m.module_code: m for m in snapshot.modules}
    assert set(modules) == {"IT1001", "IT1003"} and modules["IT1003"].candidate_count == 3
    assert all(r.registration_type == "repeat" for r in snapshot.registrations if r.module_code == "IT1003")
    result = generate_timetable(snapshot)
    assert result.status in (SolveStatus.OPTIMAL, SolveStatus.FEASIBLE)
    # The repeat candidate sitting both modules gets two non-overlapping exams (H1).
    first, second = sorted(result.timetable.sessions, key=lambda s: s.start)
    assert first.end <= second.start


async def test_repeat_only_excludes_normal_registrations():
    # IT1001's candidates are normal: as repeat_only it has nobody to examine.
    with pytest.raises(SnapshotError, match="IT1001 has no repeat registrations"):
        await _cycle((("IT1001", "repeat_only"), ("IT1002", "all")))


async def test_partial_generation_uses_listed_cycle_modules():
    snapshot = await _cycle((("IT1001", "all"), ("IT1002", "all")), module_codes=("IT1002",))
    assert [m.module_code for m in snapshot.modules] == ["IT1002"]
    assert [c.module_code for c in snapshot.cycle_modules] == ["IT1002"]


async def test_listed_modules_must_be_in_the_cycle():
    with pytest.raises(SnapshotError, match="Modules not in cycle TEST: IT1003"):
        await _cycle((("IT1001", "all"),), module_codes=("IT1001", "IT1003"))


async def test_period_without_mapping_needs_listed_modules():
    with pytest.raises(SnapshotError, match="has no cycle module mapping"):
        await _cycle(())
    listed = await _cycle((), module_codes=("IT1001",))
    assert listed.cycle_modules == () and [m.module_code for m in listed.modules] == ["IT1001"]


async def test_listed_modules_without_mapping_must_match_the_semester():
    with pytest.raises(SnapshotError, match="IT1002 is a semester 1 module"):
        await _cycle((), module_codes=("IT1001", "IT1002"), semesters=(2, 1, 2))


@pytest.mark.parametrize("cycle, case, expected", [
    ((("IT1001", "all"), ("IT1002", "all")), {"semesters": (2, 1, 2)}, "IT1002 is a semester 1 module"),
    ((("IT1001", "all"),), {"has_final": (False, True, True)}, "IT1001 has no FINAL examination"),
    ((("IT1001", "all"),), {"exam_kind": "MID"}, "IT1001 has no MID examination"),
    ((("IT1001", "all"), ("ZZ9999", "all")), {}, "maps unknown modules: ZZ9999"),
    ((("IT1001", "sometimes"),), {}, "candidate_scope must be one of all, repeat_only"),
])
async def test_inconsistent_cycle_mappings_fail_clearly(cycle, case, expected):
    with pytest.raises(SnapshotError, match=expected):
        await _cycle(cycle, **case)


async def test_module_without_registrations_fails_clearly():
    with pytest.raises(SnapshotError, match="IT1002 has no registrations to examine"):
        await _cycle((("IT1001", "all"), ("IT1002", "all")), candidates=(A, ()))


async def test_block_module_of_another_semester_may_be_a_full_member():
    snapshot = await _cycle((("IT1001", "all"), ("IT1002", "all")), semesters=(2, 1, 2),
                            delivery=(("regular", None), ("block", "pro_rata"), ("regular", None)))
    assert {m.module_code for m in snapshot.modules} == {"IT1001", "IT1002"}


async def test_mid_cycle_uses_mid_durations():
    snapshot = await _cycle((("IT1001", "all"),), exam_kind="MID", durations=(120, 120, 120),
                            mid_durations=(40, None, None))
    assert [(m.module_code, m.duration_minutes) for m in snapshot.modules] == [("IT1001", 40)]


# --- Independent snapshot checks (defence in depth) ------------------------------------------


async def test_snapshot_cycle_rows_must_match_its_modules():
    snapshot = await _cycle((("IT1001", "all"), ("IT1002", "all")))
    changed = replace(snapshot, cycle_modules=snapshot.cycle_modules[:1])
    assert any("match the selected modules" in v.message for v in validate_inputs(changed))
    assert generate_timetable(changed).status == SolveStatus.MODEL_INVALID


async def test_snapshot_repeat_only_module_cannot_seat_normal_candidates():
    snapshot = await _cycle((("IT1001", "all"), ("IT1002", "all")))
    changed = replace(snapshot, cycle_modules=tuple(replace(c, candidate_scope="repeat_only")
                                                    if c.module_code == "IT1001" else c for c in snapshot.cycle_modules))
    assert any(v.constraint == "H10" for v in validate_inputs(changed))


async def test_fingerprint_covers_the_cycle_mapping():
    full = await _cycle((("IT1001", "all"), ("IT1002", "all")))
    listed = await _cycle((), module_codes=("IT1001", "IT1002"))
    assert full.registrations == listed.registrations and full.fingerprint != listed.fingerprint


# --- SYNTHETIC cycle seeding ------------------------------------------------------------------------


@pytest.fixture(scope="module")
def base():
    return generate_dataset()


@pytest.fixture(scope="module")
def cycle_data(base):
    return build_synthetic_cycle(base)


def test_default_dataset_maps_no_modules(base) -> None:
    assert base.exam_period_modules == ()


def test_synthetic_cycle_is_valid_deterministic_and_additive(base, cycle_data) -> None:
    assert validate_dataset(cycle_data).ok
    assert build_synthetic_cycle(base) == cycle_data
    for name in ("modules", "module_specializations", "registrations"):
        assert set(getattr(base, name)) <= set(getattr(cycle_data, name)), name  # nothing removed or changed
    assert (cycle_data.students, cycle_data.venues, cycle_data.exam_periods) == (
        base.students, base.venues, base.exam_periods)


def test_synthetic_cycle_covers_every_year_and_repeat_only_modules(base, cycle_data) -> None:
    [period] = cycle_data.exam_periods
    scopes = {c.module_code: c.candidate_scope for c in cycle_data.exam_period_modules}
    modules = {m.module_code: m for m in cycle_data.modules}
    assert {code for code, scope in scopes.items() if scope == "all"} == {m.module_code for m in base.modules}
    assert {modules[c].academic_year for c in scopes} == {1, 2, 3, 4}
    repeat_only = [c for c, scope in scopes.items() if scope == "repeat_only"]
    assert len(repeat_only) == 3 and all(modules[c].semester != period.semester for c in repeat_only)
    added = [r for r in cycle_data.registrations if r.module_code in repeat_only]
    assert added and all(r.registration_type == "repeat" for r in added)


async def test_synthetic_full_cycle_loads_only_cycle_registrations(cycle_data) -> None:
    from app.seating.memory import MemorySeatingStore

    snapshot = await load_snapshot(DatasetSource(cycle_data), MemorySeatingStore(), period_code=cycle_data.exam_periods[0]
                                   .period_code, from_cycle=True)
    assert len(snapshot.modules) == len(cycle_data.exam_period_modules)
    assert len(snapshot.registrations) == len(cycle_data.registrations)  # every synthetic registration is in the cycle
    assert sum(c.candidate_scope == "repeat_only" for c in snapshot.cycle_modules) == 3


def test_synthetic_cycle_seeding_is_idempotent_and_preserves_rows(base, cycle_data) -> None:
    store = FakeStore()
    store.seed(base)
    before = {table: dict(rows) for table, rows in store.tables.items()}
    first = {c.table: c for c in store.seed(cycle_data)}
    assert all(not c.updates for c in first.values())  # existing rows are never changed
    assert len(first["seed_exam_period_modules"].inserts) == len(cycle_data.exam_period_modules)
    for table, rows in before.items():
        assert all(store.tables[table][key] == row for key, row in rows.items()), table
    second = store.seed(cycle_data)
    assert all(not c.inserts and not c.updates for c in second)


@pytest.mark.parametrize("change, fragment", [
    (lambda d: d.exam_period_modules + (d.exam_period_modules[0],), "duplicate cycle module"),
    (lambda d: (ExamPeriodModule("NO-PERIOD", d.modules[0].module_code),), "unknown period"),
    (lambda d: (ExamPeriodModule(d.exam_periods[0].period_code, "ZZ9999"),), "unknown module"),
    (lambda d: (ExamPeriodModule(d.exam_periods[0].period_code, d.modules[0].module_code, "maybe"),),
     "candidate_scope must be one of"),
    (lambda d: (ExamPeriodModule(d.exam_periods[0].period_code,
                                 next(m.module_code for m in d.modules if m.academic_year == 4), "repeat_only"),),
     "no registrations to examine for scope repeat_only"),
])
def test_invalid_cycle_rows_are_rejected(cycle_data, change, fragment) -> None:
    report = validate_dataset(dataclasses.replace(cycle_data, exam_period_modules=change(cycle_data)))
    assert not report.ok and any(fragment in e for e in report.errors), report.errors


# --- Potentially excluded registrations (counts only) ---------------------------------------------------------


async def test_unmapped_modules_are_reported_with_counts_only():
    # IT1003 (repeat candidates) is not in the cycle: its registrations are left out and reported.
    snapshot = await _cycle((("IT1001", "all"), ("IT1002", "all")))
    [excluded] = snapshot.excluded_modules
    assert (excluded.module_code, excluded.normal_registrations, excluded.repeat_registrations) == ("IT1003", 0, 2)
    assert excluded.other_cycles == () and "IT1003" not in {m.module_code for m in snapshot.modules}
    assert all(r.module_code != "IT1003" for r in snapshot.registrations)  # never included automatically
    assert not any(hasattr(excluded, name) for name in ("student_id", "students"))


async def test_partial_selection_does_not_report_mapped_modules():
    snapshot = await _cycle((("IT1001", "all"), ("IT1002", "all")), module_codes=("IT1001",))
    assert [m.module_code for m in snapshot.excluded_modules] == ["IT1003"]  # IT1002 is mapped, only not listed


async def test_excluded_report_does_not_change_the_input_fingerprint():
    snapshot = await _cycle((("IT1001", "all"), ("IT1002", "all")))
    assert replace(snapshot, excluded_modules=()).fingerprint == snapshot.fingerprint


async def test_excluded_module_lists_the_other_cycles_that_map_it(cycle_data):
    from app.seating.memory import MemorySeatingStore

    [final] = cycle_data.exam_periods
    moved = next(c for c in cycle_data.exam_period_modules if c.candidate_scope == "all")
    other = dataclasses.replace(final, period_code="PP1-2026-S2-OTHER")  # SYNTHETIC second cycle
    data = dataclasses.replace(
        cycle_data, exam_periods=(final, other),
        exam_period_modules=tuple(c for c in cycle_data.exam_period_modules if c != moved)
        + (ExamPeriodModule(other.period_code, moved.module_code),))
    snapshot = await load_snapshot(DatasetSource(data), MemorySeatingStore(), period_code=final.period_code,
                                   from_cycle=True)
    [excluded] = snapshot.excluded_modules
    assert excluded.module_code == moved.module_code and excluded.other_cycles == ("PP1-2026-S2-OTHER",)
    counts = Counter(r.registration_type for r in data.registrations if r.module_code == moved.module_code)
    assert (excluded.normal_registrations, excluded.repeat_registrations) == (counts["normal"], counts["repeat"])


def test_seed_warns_about_repeat_registrations_outside_every_cycle(base, cycle_data) -> None:
    [warning] = [w for w in validate_dataset(base).warnings if "examination cycle" in w]
    assert warning.startswith("no examination cycle mapping yet:") and "repeat registration" in warning
    assert not [w for w in validate_dataset(cycle_data).warnings if "examination cycle" in w]  # all mapped
    first = cycle_data.exam_period_modules[0]
    dropped = dataclasses.replace(cycle_data, exam_period_modules=cycle_data.exam_period_modules[1:])
    repeats = sum(r.registration_type == "repeat" and r.module_code == first.module_code for r in cycle_data.registrations)
    warnings = [w for w in validate_dataset(dropped).warnings if "examination cycle" in w]
    assert warnings == ([f"module {first.module_code}: {repeats} repeat registration(s) not mapped to any "
                         "examination cycle"] if repeats else [])
