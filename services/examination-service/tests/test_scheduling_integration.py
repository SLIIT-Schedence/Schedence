"""Offline synthetic dataset acceptance and command-line behavior."""

from __future__ import annotations

import builtins
import asyncio
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("ortools", reason="install the examination service [solver] extra")

from app.scheduling import generate_timetable, load_snapshot, validate_timetable
from app.scheduling.__main__ import main
from app.scheduling.types import SolveStatus
from app.seating.memory import MemorySeatingStore
from app.seed.generator import generate_dataset
from app.sources import DatasetSource
from scheduling_support import make_case


async def test_full_42_module_dataset_has_1700_valid_seats():
    source = DatasetSource(generate_dataset())
    snapshot = await load_snapshot(source, MemorySeatingStore(), period_code="PP1-2026-S2-FINAL")
    result = generate_timetable(snapshot)
    assert result.status in (SolveStatus.OPTIMAL, SolveStatus.FEASIBLE), result.diagnostics
    draft = result.timetable
    assert draft is not None and len(draft.sessions) == 42
    assert sum(len(r.seats) for s in draft.sessions for r in s.rooms) == 1700
    assert sum(r.registration_type == "repeat" for r in snapshot.registrations) == 63
    assert validate_timetable(snapshot, draft) == ()
    assert all(b.source.startswith("simulated_") and "SIMULATED" in b.reason for b in snapshot.blocks)
    sessions = {s.module_code: s for s in draft.sessions}
    assert len(sessions["IT1010"].rooms) >= 4 and len(sessions["IT1100"].rooms) >= 4


def test_cli_uses_synthetic_data_and_accepts_custom_policy(capsys):
    assert main(["--module", "IT1010", "--starts", "09:00", "14:00", "--minimum-break", "30",
                 "--time-limit", "10", "--include-draft"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["prototype"] is True
    assert "not institutional bookings" in payload["data_notice"]
    assert payload["policy"]["minimum_break_minutes"] == 30
    assert payload["settings"]["time_limit_seconds"] == 10
    assert payload["summary"]["sessions"] == 1 and payload["summary"]["seat_allocations"] == 111
    assert payload["timetable"]["sessions"][0]["start"].endswith(("09:00:00", "14:00:00"))


def test_cli_updated_break_is_used_on_the_next_run(monkeypatch, capsys):
    import app.scheduling.__main__ as cli
    # Two synthetic papers for one student, including a repeat. Each lasts an
    # hour and the available starts leave exactly 30 minutes between papers.
    _, source, _ = asyncio.run(make_case(candidates=(("IT30000001",), ("IT30000001",))))
    monkeypatch.setattr(cli, "generate_dataset", lambda: None)
    monkeypatch.setattr(cli, "DatasetSource", lambda _: source)
    fingerprints = {}
    for minutes, feasible in ((60, False), (0, True), (30, True), (90, False), (0, True)):
        exit_code = main(["--period", "TEST", "--starts", "08:30", "10:00", "--minimum-break", str(minutes)])
        payload = json.loads(capsys.readouterr().out)
        assert payload["policy"]["minimum_break_minutes"] == minutes
        assert exit_code == (0 if feasible else 1)
        assert payload["summary"]["independent_validation_passed"] is feasible
        assert payload["status"] in (("OPTIMAL", "FEASIBLE") if feasible else ("INFEASIBLE",))
        if minutes in fingerprints:
            assert payload["input_fingerprint"] == fingerprints[minutes]
        fingerprints[minutes] = payload["input_fingerprint"]
    assert len(set(fingerprints.values())) == 4
    # Omitting the flag on a later invocation restores the documented default,
    # rather than accidentally retaining the previous run's zero-minute setting.
    assert main(["--period", "TEST", "--starts", "08:30", "10:00"]) == 1
    default = json.loads(capsys.readouterr().out)
    assert default["policy"]["minimum_break_minutes"] == 60
    assert default["input_fingerprint"] == fingerprints[60]


@pytest.mark.parametrize("args,status", [
    (["--module", "UNKNOWN"], "MODEL_INVALID"),
    (["--module", "IT1010", "--starts", "17:00"], "INFEASIBLE"),  # no start leaves time for the exam
    (["--module", "IT1010", "--max-rooms", "1"], "MODEL_INVALID"),  # needs more sessions than allowed
    (["--module", "IT1010", "--time-limit", "0.000000001"], "UNKNOWN"),
    (["--period", "UNKNOWN"], "MODEL_INVALID"),
])
def test_cli_failed_runs_never_report_a_valid_draft(args, status, capsys):
    assert main(args) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == status and not payload["summary"]["independent_validation_passed"]
    assert "timetable" not in payload


@pytest.mark.parametrize("args", [["--starts", "bad"], ["--seating-percentage", "0"],
                                  ["--compatibility", "bad"]])
def test_cli_invalid_argument_has_a_clear_usage_error(args, capsys):
    with pytest.raises(SystemExit) as exc:
        main(args)
    captured = capsys.readouterr()
    assert exc.value.code == 2 and not captured.err
    assert json.loads(captured.out)["error"]["code"] == "USAGE_ERROR"


async def test_service_does_not_return_output_rejected_by_independent_validator(monkeypatch):
    import app.scheduling.cp_sat as engine
    snapshot, _, _ = await make_case()
    real = generate_timetable(snapshot)
    corrupt = replace(real, timetable=replace(real.timetable, sessions=()))
    monkeypatch.setattr(engine, "solve", lambda *_: corrupt)
    result = generate_timetable(snapshot)
    assert result.status == SolveStatus.MODEL_INVALID and result.timetable is None
    assert any(v.constraint == "H10" for v in result.violations)


def test_api_and_validator_import_without_optional_solver():
    # A fresh interpreter proves import isolation even after other tests loaded OR-Tools.
    script = '''
import builtins, sys
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.startswith('ortools'):
        raise AssertionError('API or validation imported OR-Tools')
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
import app.main
import app.scheduling
assert 'ortools' not in sys.modules
'''
    result = subprocess.run([sys.executable, "-B", "-c", script], cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_cli_splits_a_module_into_numbered_sessions(capsys):
    assert main(["--module", "IT1010", "--max-rooms", "1", "--max-sessions", "4", "--include-draft"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["multi_session_modules"] == 1 and payload["summary"]["independent_validation_passed"]
