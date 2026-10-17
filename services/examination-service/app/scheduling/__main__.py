"""Synthetic-data-only offline runner. Outputs JSON to stdout; writes no files."""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import math
from dataclasses import asdict

from app.scheduling import SchedulingPolicy, SolverSettings, generate_timetable, load_snapshot
from app.scheduling.inputs import SnapshotError
from app.scheduling.types import SolveResult, SolveStatus
from app.scheduling.contracts import SchedulingValidationError
from app.seating.memory import MemorySeatingStore
from app.seating.rules import INITIAL_DEFAULT_PERCENTAGE, check_percentage
from app.seed.assumptions import DEFAULT_ASSUMPTIONS
from app.seed.generator import generate_dataset
from app.sources import DatasetSource


def emit_json(payload: dict) -> bool:
    """Every command response, including errors, must be standard JSON."""
    try:
        output = json.dumps(payload, default=str, indent=2, allow_nan=False)
    except (TypeError, ValueError):
        print(json.dumps({"status": "ERROR", "error": {
            "code": "INVALID_OUTPUT", "message": "Command output could not be encoded as standard JSON."}}, allow_nan=False))
        return False
    print(output)
    return True


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        emit_json({"status": "MODEL_INVALID", "error": {"code": "USAGE_ERROR", "message": message}})
        self.exit(2)

    def print_help(self, file=None) -> None:
        emit_json({"status": "HELP", "help": self.format_help()})


def finite_time_limit(text: str) -> float:
    try:
        value = float(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Solver time limit must be a finite number greater than zero.") from exc
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError("Solver time limit must be a finite number greater than zero.")
    return value


def local_time(text: str) -> dt.time:
    try:
        return dt.time.fromisoformat(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use a local time such as 08:30.") from exc


def venue_mapping(text: str) -> tuple[str, tuple[str, ...]]:
    key, separator, types = text.partition("=")
    if not separator or not key or not types:
        raise argparse.ArgumentTypeError("Use KEY=venue_type,venue_type.")
    return key, tuple(types.split(","))


def seating_percentage(text: str) -> int:
    try:
        return check_percentage(int(text))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Seating percentage must be an integer between 1 and 100.") from exc


async def run(args: argparse.Namespace) -> tuple[dict, bool]:
    compatibility = dict(SchedulingPolicy().venue_compatibility)
    compatibility.update(args.compatibility)
    policy = SchedulingPolicy(
        permitted_start_times=tuple(args.starts), minimum_break_minutes=args.minimum_break,
        venue_compatibility=tuple(compatibility.items()), module_venue_overrides=tuple(args.module_venues),
        max_rooms_per_exam=args.max_rooms,
    )
    settings = SolverSettings(args.time_limit, args.seed, args.workers)
    source = DatasetSource(generate_dataset())
    try:
        snapshot = await load_snapshot(
            source, MemorySeatingStore(default_percentage=args.seating_percentage), period_code=args.period,
            policy=policy, module_codes=None if args.module is None else tuple(args.module),
        )
    except SnapshotError as exc:
        result = SolveResult(SolveStatus.MODEL_INVALID, violations=exc.violations, settings=settings)
        snapshot = None
    else:
        result = generate_timetable(snapshot, settings)
    payload = asdict(result)
    if not args.include_draft:
        payload.pop("timetable")
    draft = result.timetable
    payload.update(
        prototype=True,
        data_notice="Synthetic students/registrations. Simulated blocks are test inputs, not institutional bookings.",
        policy=asdict(policy),
        times="Local calendar times, as supplied by the examination period; logical seats only.",
        objective="Feasibility only. One exam per student per day is a future soft preference, not enforced or optimized.",
        input_fingerprint=snapshot.fingerprint if snapshot else None,
        capacity_ruleset_version=snapshot.capacities.ruleset_version if snapshot else None,
        summary={
            "modules": len(snapshot.modules) if snapshot else 0,
            "registrations": len(snapshot.registrations) if snapshot else 0,
            "sessions": len(draft.sessions) if draft else 0,
            "room_allocations": sum(len(s.rooms) for s in draft.sessions) if draft else 0,
            "seat_allocations": sum(len(r.seats) for s in draft.sessions for r in s.rooms) if draft else 0,
            "multi_room_exams": sum(len(s.rooms) > 1 for s in draft.sessions) if draft else 0,
            "independent_validation_passed": draft is not None and not result.violations,
        },
    )
    return payload, draft is not None


def main(argv: list[str] | None = None) -> int:
    parser = JsonArgumentParser(description=__doc__)
    parser.add_argument("--period", default=DEFAULT_ASSUMPTIONS.period_code)
    parser.add_argument("--module", action="append", help="Select module code; repeatable. Default: all synthetic modules.")
    parser.add_argument("--starts", nargs="+", type=local_time, default=SchedulingPolicy().permitted_start_times)
    parser.add_argument("--minimum-break", type=int, default=SchedulingPolicy().minimum_break_minutes,
                        help="Hard minimum minutes after the complete preceding exam, including reading time. "
                             "Default: %(default)s. Zero permits back-to-back exams, never overlaps.")
    parser.add_argument("--time-limit", type=finite_time_limit, default=SolverSettings().time_limit_seconds)
    parser.add_argument("--seed", type=int, default=SolverSettings().random_seed)
    parser.add_argument("--workers", type=int, default=SolverSettings().num_search_workers)
    parser.add_argument("--max-rooms", type=int, default=None)
    parser.add_argument("--seating-percentage", type=seating_percentage, default=INITIAL_DEFAULT_PERCENTAGE,
                        help="Prototype in-memory seating default only.")
    parser.add_argument("--compatibility", type=venue_mapping, action="append", default=[], metavar="EXAM_TYPE=TYPES")
    parser.add_argument("--module-venues", type=venue_mapping, action="append", default=[], metavar="MODULE_CODE=TYPES")
    parser.add_argument("--include-draft", action="store_true", help="Include every session and logical seat in stdout JSON.")
    args = parser.parse_args(argv)
    try:
        payload, success = asyncio.run(run(args))
    except SchedulingValidationError as exc:
        emit_json({"status": "MODEL_INVALID", "violations": [asdict(v) for v in exc.violations]})
        return 1
    except RuntimeError as exc:
        emit_json({"status": "ERROR", "error": {"code": "SOLVER_UNAVAILABLE", "message": str(exc)}})
        return 2
    if not emit_json(payload):
        return 2
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
