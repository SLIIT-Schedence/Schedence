"""Regression cases from final Phase 1 QA: boundary safety and standard JSON."""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import FrozenInstanceError, replace

import pytest

from app.scheduling import SchedulingPolicy, SchedulingValidationError, SolverSettings, generate_timetable, load_snapshot, validate_timetable
from app.scheduling.__main__ import emit_json, main
from app.scheduling.inputs import SnapshotError
from app.scheduling.types import DraftTimetable, ExamSession, RoomAllocation, SeatAllocation, SolveStatus
from app.sources.types import VenueBlock
from scheduling_support import MONDAY, make_case


async def valid_case():
    snapshot, source, store = await make_case()
    seats = tuple(SeatAllocation(r.student_id, number) for number, r in enumerate(snapshot.registrations, 1))
    start = dt.datetime.combine(MONDAY, dt.time(8, 30))
    session = ExamSession("S1", "IT1001", "TEST", start, start + dt.timedelta(hours=1), (RoomAllocation("R1", seats),))
    draft = DraftTimetable("TEST", snapshot.fingerprint, (session,))
    assert validate_timetable(snapshot, draft) == ()
    return snapshot, source, store, draft


@pytest.mark.parametrize("collection", [tuple, list, iter, lambda rows: (row for row in rows)])
async def test_no_seat_sessions_cannot_bypass_validation_with_an_iterable(collection):
    snapshot, _, _, draft = await valid_case()
    bad = replace(draft, sessions=collection((replace(draft.sessions[0], rooms=()),)))
    assert type(bad.sessions) is tuple
    for _ in range(2):
        errors = {v.constraint for v in validate_timetable(snapshot, bad)}
        assert {"H10", "H11"} <= errors


async def test_validator_materializes_a_bypassed_generator_once_before_all_checks():
    snapshot, _, _, draft = await valid_case()
    # Even unsafe deserialization that bypasses __post_init__ cannot skip checks.
    object.__setattr__(draft, "sessions", (replace(s, rooms=()) for s in draft.sessions))
    assert {"H10", "H11"} <= {v.constraint for v in validate_timetable(snapshot, draft)}
    assert "H10" in {v.constraint for v in validate_timetable(snapshot, draft)}


async def test_nested_generators_are_frozen_and_checked():
    snapshot, _, _, draft = await valid_case()
    room = RoomAllocation("R1", (seat for seat in draft.sessions[0].rooms[0].seats))
    session = replace(draft.sessions[0], rooms=(value for value in (room,)))
    supported = replace(draft, sessions=iter((session,)))
    assert type(supported.sessions) is type(session.rooms) is type(room.seats) is tuple
    assert validate_timetable(snapshot, supported) == ()
    empty_room = RoomAllocation("R1", (seat for seat in ()))
    bad = replace(supported, sessions=(replace(session, rooms=iter((empty_room,))),))
    assert {"H10", "H11"} <= {v.constraint for v in validate_timetable(snapshot, bad)}


@pytest.mark.parametrize("unsupported", [None, "sessions", b"sessions", {}, set(), 123])
async def test_unsupported_session_collections_have_structured_errors(unsupported):
    _, _, _, draft = await valid_case()
    with pytest.raises(SchedulingValidationError) as exc:
        replace(draft, sessions=unsupported)
    assert exc.value.violations[0].constraint == "H11"


async def test_result_containers_do_not_retain_caller_owned_lists():
    snapshot, _, _, draft = await valid_case()
    seat_list = list(draft.sessions[0].rooms[0].seats)
    room = RoomAllocation("R1", seat_list)
    room_list = [room]
    session = replace(draft.sessions[0], rooms=room_list)
    session_list = [session]
    copied = replace(draft, sessions=session_list)
    seat_list.append(seat_list[0])
    room_list.clear()
    session_list.clear()
    assert len(copied.sessions) == len(copied.sessions[0].rooms) == 1
    assert len(copied.sessions[0].rooms[0].seats) == 2
    assert validate_timetable(snapshot, copied) == ()
    for value, field, replacement in ((copied, "sessions", ()), (session, "rooms", ()),
                                       (room, "seats", ()), (room.seats[0], "student_id", "CHANGED")):
        with pytest.raises(FrozenInstanceError):
            setattr(value, field, replacement)


async def test_failing_iterators_produce_structured_errors():
    _, _, _, draft = await valid_case()

    def broken():
        yield draft.sessions[0]
        raise RuntimeError("Synthetic iterator failure")

    with pytest.raises(SchedulingValidationError) as exc:
        replace(draft, sessions=broken())
    assert exc.value.violations[0].constraint == "H11"


@pytest.mark.parametrize("target", ["seat_student", "seat_number", "room", "session", "draft"])
async def test_mutable_output_fields_are_rejected_with_structured_errors(target):
    _, _, _, draft = await valid_case()
    with pytest.raises(SchedulingValidationError) as exc:
        if target == "seat_student":
            SeatAllocation(["IT30000001"], 1)
        elif target == "seat_number":
            SeatAllocation("IT30000001", [1])
        elif target == "room":
            replace(draft.sessions[0].rooms[0], venue_id=["R1"])
        elif target == "session":
            replace(draft.sessions[0], module_code=["IT1001"])
        else:
            replace(draft, period_code=["TEST"])
    assert exc.value.violations


@pytest.mark.parametrize("field", ["module_code", "student_id", "registration_module", "registration_student", "venue_id"])
@pytest.mark.parametrize("bad_value", [[], {}, None, 1])
async def test_source_identifier_types_are_checked_before_hashing_or_sorting(monkeypatch, field, bad_value):
    _, source, store = await make_case()
    method, attribute = {
        "module_code": ("list_modules", "module_code"), "student_id": ("list_students", "student_id"),
        "registration_module": ("list_registrations", "module_code"),
        "registration_student": ("list_registrations", "student_id"), "venue_id": ("list_venues", "venue_id"),
    }[field]
    original = getattr(source, method)

    async def malformed(**kwargs):
        result = await original(**kwargs)
        if hasattr(result, "items"):
            return replace(result, items=(replace(result.items[0], **{attribute: bad_value}), *result.items[1:]))
        return [replace(result[0], **{attribute: bad_value}), *result[1:]]

    monkeypatch.setattr(source, method, malformed)
    with pytest.raises(SnapshotError) as exc:
        await load_snapshot(source, store, period_code="TEST")
    assert exc.value.violations[0].constraint == "H11"
    assert "must be a non-empty string" in str(exc.value)


@pytest.mark.parametrize("bad", [None, "08:30", {}, set(), ([8, 30],), ("08:30",), (None,)])
def test_malformed_start_time_types_have_structured_errors(bad):
    with pytest.raises(SchedulingValidationError) as exc:
        SchedulingPolicy(permitted_start_times=bad)
    assert exc.value.violations[0].constraint == "H11"


@pytest.mark.parametrize("bad", [(([], ("lecture_hall",)),), (("written", (["lecture_hall"],)),), (("written",),)])
def test_malformed_policy_mapping_types_have_structured_errors(bad):
    with pytest.raises(SchedulingValidationError) as exc:
        SchedulingPolicy(venue_compatibility=bad)
    assert exc.value.violations


async def test_snapshot_constructor_reports_invalid_identifier_without_fingerprinting_it():
    snapshot, _, _ = await make_case()
    with pytest.raises(SchedulingValidationError) as exc:
        replace(snapshot, modules=(replace(snapshot.modules[0], module_code={1: "bad", "x": "bad"}),))
    assert "module_code must be a non-empty string" in str(exc.value)


@pytest.mark.parametrize("field,bad", [("module_name", {1: "bad", "x": "bad"}), ("duration_minutes", []),
                                      ("specializations", None)])
async def test_other_malformed_source_fields_also_have_structured_errors(monkeypatch, field, bad):
    _, source, store = await make_case()
    modules = await source.list_modules()

    async def malformed(**kwargs):
        return [replace(modules[0], **{field: bad})]

    monkeypatch.setattr(source, "list_modules", malformed)
    with pytest.raises(SnapshotError) as exc:
        await load_snapshot(source, store, period_code="TEST")
    assert exc.value.violations[0].constraint == "H11"


@pytest.mark.parametrize("group,field", [("modules", "module_name"), ("students", "specialization"),
                                        ("venues", "venue_name"), ("venues", "building"),
                                        ("period", "period_name"), ("period", "academic_year_label")])
async def test_type_hardening_does_not_add_new_non_empty_metadata_rules(group, field):
    pytest.importorskip("ortools")
    snapshot, _, _ = await make_case()
    if group == "period":
        changed = replace(snapshot, period=replace(snapshot.period, **{field: ""}))
    else:
        rows = getattr(snapshot, group)
        changed = replace(snapshot, **{group: (replace(rows[0], **{field: ""}), *rows[1:])})
    result = generate_timetable(changed)
    assert result.status in (SolveStatus.FEASIBLE, SolveStatus.OPTIMAL)
    assert not validate_timetable(changed, result.timetable)


async def test_solver_and_validator_reject_malformed_ids_even_after_unsafe_deserialization():
    snapshot, _, _, draft = await valid_case()
    object.__setattr__(snapshot.modules[0], "module_code", [])
    result = generate_timetable(snapshot)
    assert result.status == SolveStatus.MODEL_INVALID and result.violations
    assert validate_timetable(snapshot, draft)[0].constraint == "H11"


@pytest.mark.parametrize("field,bad", [("venue_id", []), ("start_time", [8, 30]), ("end_time", "10:00"), ("block_date", {})])
async def test_block_field_types_are_checked_before_time_comparisons(monkeypatch, field, bad):
    _, source, store = await make_case()
    block = VenueBlock("R1", MONDAY, dt.time(8, 30), dt.time(10), "simulated_teaching", "SIMULATED", None)

    async def blocks(**kwargs):
        return [replace(block, **{field: bad})]

    monkeypatch.setattr(source, "list_venue_blocks", blocks)
    with pytest.raises(SnapshotError) as exc:
        await load_snapshot(source, store, period_code="TEST")
    assert exc.value.violations[0].constraint == "H11"


@pytest.mark.parametrize("field,bad", [("period_code", []), ("start_date", {}), ("day_start_time", [8, 30])])
async def test_period_field_types_are_checked_before_queries_or_comparisons(monkeypatch, field, bad):
    _, source, store = await make_case()
    period = await source.get_exam_period("TEST")

    async def malformed(code):
        return replace(period, **{field: bad})

    monkeypatch.setattr(source, "get_exam_period", malformed)
    with pytest.raises(SnapshotError) as exc:
        await load_snapshot(source, store, period_code="TEST")
    assert exc.value.violations[0].constraint == "H11"


@pytest.mark.parametrize("bad", [["IT1001"], {"code": "IT1001"}, None, 1])
async def test_selected_module_identifier_types_are_checked_before_set_creation(bad):
    _, source, store = await make_case()
    with pytest.raises(SnapshotError):
        await load_snapshot(source, store, period_code="TEST", module_codes=(bad,))


def strict_json(text):
    def reject(token):
        raise AssertionError(f"Non-standard JSON constant: {token}")
    return json.loads(text, parse_constant=reject)


@pytest.mark.parametrize("text", ["nan", "NaN", "inf", "+inf", "Infinity", "-Infinity", "-inf", "1e309", "-1e309", "0", "-1"])
def test_non_finite_or_non_positive_cli_time_limits_are_rejected_as_standard_json(text, capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--time-limit=" + text])
    captured = capsys.readouterr()
    payload = strict_json(captured.out)
    assert exc.value.code == 2 and not captured.err
    assert payload["status"] == "MODEL_INVALID" and payload["error"]["code"] == "USAGE_ERROR"
    assert "finite number greater than zero" in payload["error"]["message"]


@pytest.mark.parametrize("args", [["--unknown"], ["--starts", "bad"], ["--time-limit"], ["--minimum-break", "bad"]])
def test_all_usage_errors_are_standard_json(args, capsys):
    with pytest.raises(SystemExit) as exc:
        main(args)
    assert exc.value.code == 2
    assert strict_json(capsys.readouterr().out)["error"]["code"] == "USAGE_ERROR"


def test_cli_help_is_standard_json(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    payload = strict_json(capsys.readouterr().out)
    assert exc.value.code == 0 and payload["status"] == "HELP"
    assert "--minimum-break" in payload["help"]


def test_cli_missing_solver_error_is_standard_json(monkeypatch, capsys):
    import app.scheduling.__main__ as cli

    def missing(*args):
        raise RuntimeError("Offline solver is not installed. Install [solver].")

    monkeypatch.setattr(cli, "generate_timetable", missing)
    assert main(["--module", "IT1010"]) == 2
    assert strict_json(capsys.readouterr().out)["error"]["code"] == "SOLVER_UNAVAILABLE"


def test_json_encoder_never_emits_a_non_standard_constant(capsys):
    assert emit_json({"unexpected": float("nan")}) is False
    assert strict_json(capsys.readouterr().out)["error"]["code"] == "INVALID_OUTPUT"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), 10**1000])
async def test_python_time_limits_reject_non_finite_or_unrepresentable_values(value):
    snapshot, _, _ = await make_case()
    result = generate_timetable(snapshot, SolverSettings(time_limit_seconds=value))
    assert result.status == SolveStatus.MODEL_INVALID and result.timetable is None
    assert any("finite" in v.message for v in result.violations)


def test_cli_success_and_model_invalid_results_are_standard_json(capsys):
    pytest.importorskip("ortools")
    assert main(["--module", "IT1010", "--include-draft"]) == 0
    assert strict_json(capsys.readouterr().out)["summary"]["independent_validation_passed"] is True
    assert main(["--module", "UNKNOWN"]) == 1
    assert strict_json(capsys.readouterr().out)["status"] == "MODEL_INVALID"
