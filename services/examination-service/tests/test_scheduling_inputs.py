"""Snapshot consistency, isolation, policy validation, and API-independent imports."""

from __future__ import annotations

import builtins
import datetime as dt
from contextlib import asynccontextmanager
from dataclasses import FrozenInstanceError, replace
from uuid import UUID

import pytest

from app.scheduling import SchedulingPolicy, SolverSettings, generate_timetable, load_snapshot
from app.scheduling.inputs import SnapshotError, load_database_snapshot, validate_inputs
from app.scheduling.types import SolveStatus
from app.seating.errors import SeatingNotConfiguredError
from app.sources.types import VenueBlock
from scheduling_support import MONDAY, make_case


async def test_snapshot_is_frozen_and_captures_capacity_before_later_changes():
    snapshot, source, store = await make_case()
    rule = await store.get_rule(None)
    await store.update_rule(rule.rule_id, expected_version=rule.version, percentage=100,
                            reason="SYNTHETIC TEST", updated_by=UUID(int=1))
    later = await load_snapshot(source, store, period_code="TEST")
    assert snapshot.capacities.exam_capacity("R1") == 2
    assert later.capacities.exam_capacity("R1") == 4
    assert snapshot.fingerprint != later.fingerprint
    with pytest.raises(FrozenInstanceError):
        snapshot.modules = ()
    with pytest.raises(FrozenInstanceError):
        snapshot.modules[0].candidate_count = 99


async def test_snapshot_fingerprint_is_stable_but_covers_non_capacity_inputs():
    snapshot, source, store = await make_case()
    again = await load_snapshot(source, store, period_code="TEST")
    assert snapshot.fingerprint == again.fingerprint
    assert snapshot.fingerprint != replace(snapshot, policy=SchedulingPolicy(minimum_break_minutes=30)).fingerprint
    block = VenueBlock("R1", MONDAY, dt.time(10), dt.time(11), "simulated_teaching", "SIMULATED", None)
    assert snapshot.fingerprint != replace(snapshot, blocks=(block,)).fingerprint
    assert snapshot.fingerprint != replace(snapshot, registrations=snapshot.registrations[:-1]).fingerprint


async def test_snapshot_copies_mutable_containers():
    snapshot, _, _ = await make_case()
    modules = list(snapshot.modules)
    specials = ["IT"]
    modules[0] = replace(modules[0], specializations=specials)
    copied = replace(snapshot, modules=modules)
    modules.clear()
    specials.append("OTHER")
    assert len(copied.modules) == 1 and copied.modules[0].specializations == ("IT",)
    starts = [dt.time(8, 30)]
    policy = SchedulingPolicy(permitted_start_times=starts)
    starts.append(dt.time(10))
    assert policy.permitted_start_times == (dt.time(8, 30),)


async def test_explicit_module_selection_keeps_all_repeat_registrations():
    _, source, store = await make_case(candidates=(("IT30000001",), ("IT30000001", "IT30000002")))
    selected = await load_snapshot(source, store, period_code="TEST", module_codes=("IT1002",))
    assert len(selected.modules) == 1 and len(selected.registrations) == 2
    assert all(r.registration_type == "repeat" for r in selected.registrations)
    for codes in ((), ("UNKNOWN",), ("IT1001", "IT1001")):
        with pytest.raises(SnapshotError):
            await load_snapshot(source, store, period_code="TEST", module_codes=codes)
    with pytest.raises(SnapshotError):
        await load_snapshot(source, store, period_code="UNKNOWN")


@pytest.mark.parametrize("kind", ["students", "registrations"])
async def test_source_must_return_complete_pages(kind):
    _, source, store = await make_case()
    method = getattr(source, "list_" + kind)

    async def incomplete(**kwargs):
        return replace(await method(**kwargs), items=())

    setattr(source, "list_" + kind, incomplete)
    with pytest.raises(SnapshotError, match="incomplete"):
        await load_snapshot(source, store, period_code="TEST")


async def test_missing_seating_configuration_is_explicit():
    _, source, store = await make_case()

    async def no_rules():
        return []

    store.list_rules = no_rules
    with pytest.raises(SeatingNotConfiguredError):
        await load_snapshot(source, store, period_code="TEST")


async def test_database_adapter_loads_inside_one_snapshot_transaction(monkeypatch):
    import app.scheduling.inputs as inputs
    events, engine, session = [], object(), object()
    expected, _, _ = await make_case()

    @asynccontextmanager
    async def transaction(value):
        assert value is engine
        events.append("begin repeatable read, read only")
        yield session
        events.append("rollback and close")

    async def load(source, store, **kwargs):
        assert source == ("source", session) and store == ("store", session)
        assert kwargs == {"period_code": "TEST"}
        events.append("load all inputs")
        return expected

    monkeypatch.setattr(inputs, "snapshot_session", transaction)
    monkeypatch.setattr(inputs, "DatabaseSource", lambda s: ("source", s))
    monkeypatch.setattr(inputs, "DatabaseSeatingStore", lambda s: ("store", s))
    monkeypatch.setattr(inputs, "load_snapshot", load)
    assert await load_database_snapshot(engine, period_code="TEST") is expected
    assert events == ["begin repeatable read, read only", "load all inputs", "rollback and close"]


@pytest.mark.parametrize("changes", [
    {"minimum_break_minutes": -1}, {"minimum_break_minutes": True},
    {"permitted_start_times": ()}, {"permitted_start_times": (dt.time(8, 30, 1),)},
    {"permitted_start_times": (dt.time(8, 30), dt.time(8, 30))},
    {"permitted_start_times": (dt.time(8, 30, tzinfo=dt.UTC),)},
    {"max_rooms_per_exam": 0}, {"max_rooms_per_exam": False},
    {"venue_compatibility": (("written", ()),)},
    {"venue_compatibility": (("written", ("lecture_hall",)), ("written", ("lecture_hall",)))},
])
async def test_invalid_policies_are_model_invalid(changes):
    snapshot, _, _ = await make_case()
    result = generate_timetable(replace(snapshot, policy=replace(snapshot.policy, **changes)))
    assert result.status == SolveStatus.MODEL_INVALID and result.violations


@pytest.mark.parametrize("settings", [
    SolverSettings(time_limit_seconds=0), SolverSettings(time_limit_seconds=float("nan")),
    SolverSettings(time_limit_seconds=float("inf")), SolverSettings(time_limit_seconds=True),
    SolverSettings(random_seed=-1), SolverSettings(random_seed=2**31), SolverSettings(num_search_workers=0),
])
async def test_invalid_solver_settings_are_model_invalid(settings):
    snapshot, _, _ = await make_case()
    assert generate_timetable(snapshot, settings).status == SolveStatus.MODEL_INVALID


@pytest.mark.parametrize("mode", [
    "duplicate_module", "duplicate_student", "duplicate_registration", "duplicate_venue", "unknown_student",
    "unknown_module", "candidate_count", "reading_flag", "duration", "capacity", "missing_capacity",
    "unknown_block_venue", "bad_block_time", "wrong_exclusion_period", "cached_days", "period_range", "hours",
    "exam_kind",
])
async def test_invalid_input_relationships_are_rejected(mode):
    snapshot, _, _ = await make_case()
    if mode.startswith("duplicate_"):
        field = {"module": "modules", "student": "students", "registration": "registrations", "venue": "venues"}[mode[10:]]
        rows = getattr(snapshot, field)
        changed = replace(snapshot, **{field: (*rows, rows[0])})
    elif mode in ("unknown_student", "unknown_module"):
        field = "student_id" if mode == "unknown_student" else "module_code"
        changed = replace(snapshot, registrations=(replace(snapshot.registrations[0], **{field: "UNKNOWN"}),))
    elif mode in ("candidate_count", "reading_flag", "duration"):
        field, value = {"candidate_count": ("candidate_count", 9), "reading_flag": ("has_extra_reading_time", True),
                        "duration": ("duration_minutes", -1)}[mode]
        changed = replace(snapshot, modules=(replace(snapshot.modules[0], **{field: value}),))
    elif mode in ("capacity", "missing_capacity"):
        caps = snapshot.capacities.venues
        caps = (replace(caps[0], exam_capacity=999), *caps[1:]) if mode == "capacity" else caps[1:]
        changed = replace(snapshot, capacities=replace(snapshot.capacities, venues=caps))
    elif mode in ("unknown_block_venue", "bad_block_time"):
        block = VenueBlock("UNKNOWN" if mode == "unknown_block_venue" else "R1", MONDAY,
                           dt.time(10), dt.time(9) if mode == "bad_block_time" else dt.time(11),
                           "simulated_teaching", "SIMULATED", None)
        changed = replace(snapshot, blocks=(block,))
    else:
        period = snapshot.period
        if mode == "cached_days":
            period = replace(period, exam_days=())
        elif mode == "period_range":
            period = replace(period, end_date=MONDAY - dt.timedelta(days=1))
        elif mode == "hours":
            period = replace(period, day_end_time=dt.time(7))
        elif mode == "exam_kind":
            period = replace(period, exam_kind="MIDTERM")
        else:
            from app.sources.types import ExamPeriodExclusion
            period = replace(period, exclusions=(ExamPeriodExclusion("OTHER", MONDAY, "SIMULATED"),))
        changed = replace(snapshot, period=period)
    assert validate_inputs(changed)
    assert generate_timetable(changed).status == SolveStatus.MODEL_INVALID


async def test_missing_solver_has_clear_install_message_and_validation_needs_no_solver(monkeypatch):
    snapshot, _, _ = await make_case()
    original = builtins.__import__

    def without_solver(name, *args, **kwargs):
        if name == "app.scheduling.cp_sat":
            raise ModuleNotFoundError("No module named ortools", name="ortools")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_solver)
    assert validate_inputs(snapshot) == ()
    with pytest.raises(RuntimeError, match=r"\[solver\]"):
        generate_timetable(snapshot)


@pytest.mark.parametrize("kind", ["MID", "FINAL"])
async def test_mid_and_final_periods_are_scheduled_by_the_same_rules(kind):
    # SYNTHETIC MID duration equal to the FINAL one, so only the kind differs.
    snapshot, _, _ = await make_case(exam_kind=kind, mid_durations=(60,))
    assert snapshot.period.exam_kind == kind and validate_inputs(snapshot) == ()
    result = generate_timetable(snapshot)
    assert result.status in (SolveStatus.OPTIMAL, SolveStatus.FEASIBLE)
    final, _, _ = await make_case(exam_kind="FINAL", mid_durations=(60,))
    # The kind is part of the input identity, but does not change the timetable yet.
    assert (snapshot.fingerprint == final.fingerprint) == (kind == "FINAL")
    assert [(s.start, s.end) for s in result.timetable.sessions] == [
        (s.start, s.end) for s in generate_timetable(final).timetable.sessions]


@pytest.mark.parametrize("kind", ["MIDTERM", "final", "", None])
async def test_unknown_exam_kind_is_rejected_when_loading(kind):
    with pytest.raises(SnapshotError):
        await make_case(exam_kind=kind)


# --- Module examination eligibility (SYNTHETIC values) ---------------------------------------


async def test_mid_period_uses_the_mid_duration():
    snapshot, _, _ = await make_case(exam_kind="MID", durations=(120,), mid_durations=(45,))
    [module] = snapshot.modules
    assert module.duration_minutes == module.mid_duration_minutes == 45
    [session] = generate_timetable(snapshot).timetable.sessions
    assert session.end - session.start == dt.timedelta(minutes=45)
    final, _, _ = await make_case(exam_kind="FINAL", durations=(120,), mid_durations=(45,))
    assert final.modules[0].duration_minutes == 120


async def test_unselected_period_kind_selects_only_eligible_modules():
    candidates = (("IT30000001",), ("IT30000002",))
    mid, _, _ = await make_case(exam_kind="MID", candidates=candidates, mid_durations=(None, 30))
    assert [m.module_code for m in mid.modules] == ["IT1002"]
    assert {r.module_code for r in mid.registrations} == {"IT1002"}
    final, _, _ = await make_case(candidates=candidates, has_final=(True, False), mid_durations=(None, 30))
    assert [m.module_code for m in final.modules] == ["IT1001"]


async def test_explicit_selection_rejects_modules_without_that_exam():
    _, source, store = await make_case(exam_kind="MID", candidates=(("IT30000001",), ("IT30000002",)),
                                       mid_durations=(None, 30))
    with pytest.raises(SnapshotError, match="without a MID examination.*IT1001"):
        await load_snapshot(source, store, period_code="TEST", module_codes=("IT1001", "IT1002"))


@pytest.mark.parametrize("changes", [
    {"has_mid_exam": True, "mid_duration_minutes": None},
    {"has_mid_exam": False, "mid_duration_minutes": 30},
    {"has_mid_exam": True, "mid_duration_minutes": 0},
    {"delivery_mode": "weekend"},
    {"delivery_mode": "block", "block_category": None},
    {"delivery_mode": "regular", "block_category": "pro_rata"},
    {"has_final_exam": False},  # FINAL period, module without a FINAL exam
])
async def test_inconsistent_module_exam_fields_are_model_invalid(changes):
    snapshot, _, _ = await make_case(durations=(60,))
    changed = replace(snapshot, modules=(replace(snapshot.modules[0], **changes),))
    assert validate_inputs(changed)
    assert generate_timetable(changed).status == SolveStatus.MODEL_INVALID


async def test_block_modules_are_scheduled_like_regular_modules():
    snapshot, _, _ = await make_case()
    block = replace(snapshot, modules=(replace(snapshot.modules[0], delivery_mode="block",
                                               block_category="full_module"),))
    assert validate_inputs(block) == ()
    assert generate_timetable(block).status in (SolveStatus.OPTIMAL, SolveStatus.FEASIBLE)


async def test_mid_snapshot_duration_must_be_the_mid_duration():
    snapshot, _, _ = await make_case(exam_kind="MID", durations=(120,), mid_durations=(45,))
    changed = replace(snapshot, modules=(replace(snapshot.modules[0], duration_minutes=120),))
    assert any("no MID examination of 120 minutes" in v.message for v in validate_inputs(changed))
    assert generate_timetable(changed).status == SolveStatus.MODEL_INVALID
