"""Synthetic-data-only offline runner. Outputs JSON to stdout; writes no files."""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import math
from dataclasses import asdict, replace
from pathlib import Path

from app.scheduling import SchedulingPolicy, SolverSettings, generate_timetable, load_snapshot
from app.scheduling.inputs import SnapshotError
from app.scheduling.types import SolveResult, SolveStatus
from app.scheduling.configuration import SETTINGS_FORMAT, SchedulingConfiguration
from app.scheduling.contracts import SchedulingValidationError, Violation
from app.scheduling.optimization_policy import OptimizationPolicy
from app.scheduling.policy import MAX_MINIMUM_BREAK_MINUTES, MAX_SEARCH_WORKERS, MAX_TIME_LIMIT_SECONDS
from app.scheduling.optimization import optimize_timetable
from app.scheduling.validation import validate_timetable
from app.scheduling.fairness_policy import FairnessPolicy
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


# Options that a --settings-file replaces; they are SUPPRESSed so "given" can be detected.
SETTINGS_FILE_OPTIONS = ("starts", "minimum_break", "time_limit", "seed", "workers", "max_rooms", "max_sessions",
                         "compatibility", "module_venues")


async def run(args: argparse.Namespace) -> tuple[dict, bool]:
    configuration = read_configuration(args.settings_file) if args.settings_file else None
    if configuration is not None:
        optimization_policy = configuration.optimization if args.optimize else None
        fairness_policy = configuration.fairness if args.optimize else FairnessPolicy()
        policy, settings = configuration.scheduling, configuration.solver
    else:
        optimization_policy = read_optimization_policy(args.optimization_config) if args.optimize else None
        fairness_policy = read_fairness_policy(args.fairness_config) if args.optimize else FairnessPolicy()
        if args.fairness is not None:
            fairness_policy = replace(fairness_policy, enabled=args.fairness)
        compatibility = dict(SchedulingPolicy().venue_compatibility)
        compatibility.update(getattr(args, "compatibility", []))
        policy = SchedulingPolicy(
            permitted_start_times=tuple(getattr(args, "starts", SchedulingPolicy().permitted_start_times)),
            minimum_break_minutes=getattr(args, "minimum_break", SchedulingPolicy().minimum_break_minutes),
            venue_compatibility=tuple(compatibility.items()),
            module_venue_overrides=tuple(getattr(args, "module_venues", [])),
            max_rooms_per_exam=getattr(args, "max_rooms", None),
            max_sessions_per_exam=getattr(args, "max_sessions", SchedulingPolicy().max_sessions_per_exam),
        )
        defaults = SolverSettings()
        settings = SolverSettings(getattr(args, "time_limit", defaults.time_limit_seconds),
                                  getattr(args, "seed", defaults.random_seed),
                                  getattr(args, "workers", defaults.num_search_workers))
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
        result = (optimize_timetable(snapshot, settings, optimization_policy, fairness=fairness_policy) if args.optimize
                  else generate_timetable(snapshot, settings))
    payload = asdict(result)
    if args.optimize:
        payload["optimization_policy"] = asdict(optimization_policy)
    if not args.include_draft:
        payload.pop("timetable")
        payload.pop("baseline", None)
        for name in ("baseline_evaluation", "selected_evaluation"):
            if payload.get(name) is not None:
                payload[name].pop("student_penalties", None)
    draft = result.timetable
    payload.update(
        prototype=True,
        data_notice="Synthetic students/registrations. Simulated blocks are test inputs, not institutional bookings.",
        policy=asdict(policy),
        times="Local calendar times, as supplied by the examination period; logical seats only.",
        objective=("Estimated workload optimization S1-S5. Not a scientifically validated stress measurement."
                   if args.optimize else "Feasibility only. One exam per student per day is a future soft preference, not enforced or optimized."),
        settings_source="file" if configuration else "command line",
        settings_fingerprint=configuration.fingerprint if configuration else None,
        input_fingerprint=snapshot.fingerprint if snapshot else None,
        capacity_ruleset_version=snapshot.capacities.ruleset_version if snapshot else None,
        summary={
            "modules": len(snapshot.modules) if snapshot else 0,
            "registrations": len(snapshot.registrations) if snapshot else 0,
            "sessions": len(draft.sessions) if draft else 0,
            "room_allocations": sum(len(s.rooms) for s in draft.sessions) if draft else 0,
            "seat_allocations": sum(len(r.seats) for s in draft.sessions for r in s.rooms) if draft else 0,
            "multi_room_exams": sum(len(s.rooms) > 1 for s in draft.sessions) if draft else 0,
            "multi_session_modules": len({s.module_code for s in draft.sessions if s.session_count > 1}) if draft else 0,
            "independent_validation_passed": draft is not None and not validate_timetable(snapshot, draft),
        },
    )
    return payload, draft is not None


def read_optimization_policy(path: str | None) -> OptimizationPolicy:
    return read_policy(path, OptimizationPolicy, "OPTIMIZATION")


def read_fairness_policy(path: str | None) -> FairnessPolicy:
    return read_policy(path, FairnessPolicy, "FAIRNESS")


def read_policy(path, policy_type, code):
    if path is None:
        return policy_type()
    return policy_type.from_mapping(read_json(path, code))


def read_configuration(path: str) -> SchedulingConfiguration:
    """A saved settings document, or a whole ``GET /api/v1/settings/scheduling`` response."""
    content = read_json(path, "SETTINGS")
    if type(content) is dict and "settings" in content and "format" in content:
        if content.get("status") == "invalid":
            raise SchedulingValidationError((Violation("SETTINGS", "The exported settings are invalid. An Examination "
                                                                   "Admin must repair them before scheduling."),))
        if content["format"] != SETTINGS_FORMAT:
            raise SchedulingValidationError((Violation("SETTINGS", f"Unsupported settings format {content['format']!r}; "
                                                                   f"expected {SETTINGS_FORMAT}."),))
        content = content["settings"]
    return SchedulingConfiguration.from_document(content)


def read_json(path, code):
    def pairs(rows):
        result = {}
        for key, value in rows:
            if key in result:
                raise ValueError("Duplicate configuration setting: " + key)
            result[key] = value
        return result

    def reject_constant(value):
        raise ValueError("Non-finite JSON value is not permitted: " + value)

    try:
        content = Path(path).read_text(encoding="utf-8")
        return json.loads(content, object_pairs_hook=pairs, parse_constant=reject_constant)
    except (OSError, UnicodeError, ValueError) as exc:
        raise SchedulingValidationError((Violation(code, "Cannot load " + code.lower() + " configuration: " + str(exc)),)) from exc


def main(argv: list[str] | None = None) -> int:
    parser = JsonArgumentParser(description=__doc__)
    parser.add_argument("--period", default=DEFAULT_ASSUMPTIONS.period_code)
    parser.add_argument("--module", action="append", help="Select module code; repeatable. Default: all synthetic modules.")
    # SUPPRESS: an option the user did not give is absent, so it can conflict with --settings-file.
    hidden = argparse.SUPPRESS
    parser.add_argument("--starts", nargs="+", type=local_time, default=hidden,
                        help=f"Default: {' '.join(t.strftime('%H:%M') for t in SchedulingPolicy().permitted_start_times)}.")
    parser.add_argument("--minimum-break", type=int, default=hidden,
                        help="Hard minimum minutes after the complete preceding exam, including reading time. "
                             f"Default: {SchedulingPolicy().minimum_break_minutes}; at most {MAX_MINIMUM_BREAK_MINUTES}. "
                             "Zero permits back-to-back exams, never overlaps.")
    parser.add_argument("--time-limit", type=finite_time_limit, default=hidden,
                        help=f"Default: {SolverSettings().time_limit_seconds}; at most {MAX_TIME_LIMIT_SECONDS}.")
    parser.add_argument("--seed", type=int, default=hidden, help=f"Default: {SolverSettings().random_seed}.")
    parser.add_argument("--workers", type=int, default=hidden,
                        help=f"Default: {SolverSettings().num_search_workers}; at most {MAX_SEARCH_WORKERS}.")
    parser.add_argument("--max-rooms", type=int, default=hidden, help="Default: no limit.")
    parser.add_argument("--max-sessions", type=int, default=hidden,
                        help="Most sessions per module when one sitting cannot seat everyone. Default: 3.")
    parser.add_argument("--seating-percentage", type=seating_percentage, default=INITIAL_DEFAULT_PERCENTAGE,
                        help="Prototype in-memory seating default only.")
    parser.add_argument("--compatibility", type=venue_mapping, action="append", default=hidden, metavar="EXAM_TYPE=TYPES")
    parser.add_argument("--module-venues", type=venue_mapping, action="append", default=hidden, metavar="MODULE_CODE=TYPES")
    parser.add_argument("--settings-file",
                        help="Use saved scheduling settings: the 'settings' document (or the whole response) of "
                             "GET /api/v1/settings/scheduling. Replaces the policy, solver, optimization and "
                             "fairness options, which cannot be combined with it.")
    parser.add_argument("--include-draft", action="store_true", help="Include every session and logical seat in stdout JSON.")
    parser.add_argument("--optimize", action="store_true", help="Enable optional S1-S5 estimated workload optimization.")
    parser.add_argument("--optimization-config", help="Read prototype optimization settings from a JSON object.")
    parser.add_argument("--fairness", action=argparse.BooleanOptionalAction, default=None,
                        help="Enable/disable optional baseline-relative workload protection; default is off.")
    parser.add_argument("--fairness-config", help="Read optional fairness settings from a JSON object.")
    args = parser.parse_args(argv)
    if args.optimization_config and not args.optimize:
        parser.error("--optimization-config requires --optimize")
    if (args.fairness is not None or args.fairness_config) and not args.optimize:
        parser.error("--fairness/--fairness-config requires --optimize")
    if args.settings_file:
        given = [name for name in SETTINGS_FILE_OPTIONS if hasattr(args, name)]
        given += [name for name in ("optimization_config", "fairness_config", "fairness")
                  if getattr(args, name) is not None]
        if given:
            parser.error("--settings-file cannot be combined with: "
                         + ", ".join("--" + name.replace("_", "-") for name in given))
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
