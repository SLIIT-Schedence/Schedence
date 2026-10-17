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
