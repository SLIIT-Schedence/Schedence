"""Admin-configurable scheduling settings as one versioned JSON document.

The document has four sections, each validated by the existing policy class:

* ``scheduling``: :class:`SchedulingPolicy` (starts, breaks, rooms, venue types);
* ``solver``: :class:`SolverSettings` (time limit, seed, workers);
* ``optimization``: :class:`OptimizationPolicy` (S1-S5 weights and thresholds);
* ``fairness``: :class:`FairnessPolicy`.

The hard rules H1-H11 are not settings: there is no key that can switch off
student clash prevention, room capacity or room conflict checks. Unknown keys
are rejected, and a document must be complete, so a missing key can never
silently reset a setting to its default.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from dataclasses import asdict, dataclass, fields
from typing import Any

from app.db.seed_models import EXAM_TYPES, VENUE_TYPES
from app.scheduling.contracts import SchedulingValidationError, Violation
from app.scheduling.fairness_policy import FairnessPolicy
from app.scheduling.optimization_policy import OptimizationPolicy
from app.scheduling.policy import SchedulingPolicy, SolverSettings

SETTINGS_FORMAT = "scheduling-settings-v1"
SECTIONS = ("scheduling", "solver", "optimization", "fairness")
SCHEDULING_KEYS = ("permitted_start_times", "minimum_break_minutes", "max_rooms_per_exam",
                   "venue_compatibility", "module_venue_overrides")
MANDATORY_RULES = (
    "H1: a student never sits two exams at once, and gets at least the minimum break between exams",
    "H2: room seat counts never exceed the room's effective exam capacity",
    "H3: rooms match the exam type's allowed venue types",
    "H4: rooms are never used during a venue block",
    "H5: a room never holds two exams at the same time",
    "H6: every exam is inside the selected exam period",
    "H7: exams start at a permitted time and finish within daily exam hours",
    "H8: no exams on weekends or excluded dates (as the period's calendar says)",
    "H9: every booking covers the full duration plus reading time",
    "H10: every module gets one sitting and every registered student gets exactly one seat",
    "H11: inputs and outputs are checked for consistent references and counts",
)
_TIME = re.compile(r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")
_MODULE_CODE = re.compile(r"^[A-Z]{2,4}[0-9]{4}$")


def _keys_errors(section: str, value: object, expected: tuple[str, ...]) -> list[Violation]:
    if type(value) is not dict:
        return [Violation("SETTINGS", f"{'settings' if section == 'top-level' else section} must be a JSON object.")]
    errors = []
    unknown = sorted(set(value) - set(expected))
    missing = [k for k in expected if k not in value]
    if unknown:
        errors.append(Violation("SETTINGS", f"Unknown {section} settings: {', '.join(map(str, unknown))}. "
                                            "Hard rules H1-H11 are always enforced and cannot be configured."))
    if missing:
        errors.append(Violation("SETTINGS", f"Missing {section} settings: {', '.join(missing)}. "
                                            "Send the complete settings document."))
    return errors


def _venue_map(name: str, value: object, key_ok, key_label: str) -> tuple[tuple[tuple[str, tuple[str, ...]], ...], list[Violation]]:
    if type(value) is not dict:
        return (), [Violation("SCHEDULING", f"{name} must be a JSON object of {key_label} to venue type lists.")]
    errors, rows = [], []
    for key, types in value.items():
        if not key_ok(key):
            errors.append(Violation("SCHEDULING", f"{name}: unknown {key_label} {key!r}."))
        if type(types) is not list or not types or any(type(t) is not str for t in types):
            errors.append(Violation("SCHEDULING", f"{name}[{key}] must be a non-empty list of venue types."))
            continue
        unknown = sorted(set(types) - set(VENUE_TYPES))
        if unknown:
            errors.append(Violation("SCHEDULING", f"{name}[{key}] has unknown venue types: {', '.join(unknown)}. "
                                                  f"Allowed: {', '.join(VENUE_TYPES)}."))
        rows.append((key, tuple(types)))
    return tuple(rows), errors


def _scheduling(section: dict) -> tuple[SchedulingPolicy | None, list[Violation]]:
    errors = []
    starts = section["permitted_start_times"]
    if type(starts) is not list or any(type(t) is not str or not _TIME.match(t) for t in starts):
        errors.append(Violation("SCHEDULING", 'permitted_start_times must be a list of local "HH:MM" times.'))
        starts = []
    compatibility, more = _venue_map("venue_compatibility", section["venue_compatibility"],
                                     lambda k: k in EXAM_TYPES, "exam type")
    errors += more
    missing_types = [t for t in EXAM_TYPES if t not in dict(compatibility)]
    if type(section["venue_compatibility"]) is dict and missing_types:
        errors.append(Violation("SCHEDULING", "venue_compatibility needs every exam type: missing "
                                              + ", ".join(missing_types) + "."))
    overrides, more = _venue_map("module_venue_overrides", section["module_venue_overrides"],
                                 lambda k: type(k) is str and bool(_MODULE_CODE.match(k)), "module code")
    errors += more
    if errors:
        return None, errors
    try:
        policy = SchedulingPolicy(
            permitted_start_times=tuple(dt.time.fromisoformat(t) for t in starts),
            minimum_break_minutes=section["minimum_break_minutes"],
            venue_compatibility=compatibility,
            module_venue_overrides=overrides,
            max_rooms_per_exam=section["max_rooms_per_exam"],
        )
    except SchedulingValidationError as exc:
        return None, list(exc.violations)
    errors = [Violation("SCHEDULING", message) for message in policy.errors()]
    return (None if errors else policy), errors


def _solver(section: dict) -> tuple[SolverSettings | None, list[Violation]]:
    limit = section["time_limit_seconds"]
    try:
        if type(limit) not in (int, float):  # bool is rejected: type(True) is bool
            raise TypeError
        limit = float(limit)
    except (TypeError, OverflowError):
        return None, [Violation("SOLVER", "time_limit_seconds must be a finite number.")]
    settings = SolverSettings(limit, section["random_seed"], section["num_search_workers"])
    errors = [Violation("SOLVER", message) for message in settings.errors()]
    return (None if errors else settings), errors


@dataclass(frozen=True)
class SchedulingConfiguration:
    scheduling: SchedulingPolicy = SchedulingPolicy()
    solver: SolverSettings = SolverSettings()
    optimization: OptimizationPolicy = OptimizationPolicy()
    fairness: FairnessPolicy = FairnessPolicy()

    @classmethod
    def from_document(cls, document: object) -> SchedulingConfiguration:
        """Validate a complete settings document; raises :class:`SchedulingValidationError` with every problem."""
        errors = _keys_errors("top-level", document, SECTIONS)
        if errors:
            raise SchedulingValidationError(tuple(errors))
        sections = {
            "scheduling": SCHEDULING_KEYS,
            "solver": tuple(f.name for f in fields(SolverSettings)),
            "optimization": tuple(f.name for f in fields(OptimizationPolicy)),
            "fairness": tuple(f.name for f in fields(FairnessPolicy)),
        }
        for name, keys in sections.items():
            errors += _keys_errors(name, document[name], keys)
        if errors:
            raise SchedulingValidationError(tuple(errors))
        scheduling, more = _scheduling(document["scheduling"])
        errors += more
        solver, more = _solver(document["solver"])
        errors += more
        values = {}
        for name, policy_type in (("optimization", OptimizationPolicy), ("fairness", FairnessPolicy)):
            try:
                values[name] = policy_type.from_mapping(document[name])
            except SchedulingValidationError as exc:
                errors += exc.violations
        if errors:
            raise SchedulingValidationError(tuple(errors))
        return cls(scheduling, solver, values["optimization"], values["fairness"])

    def to_document(self) -> dict[str, Any]:
        s = self.scheduling
        return {
            "scheduling": {
                "permitted_start_times": [t.isoformat(timespec="minutes") for t in s.permitted_start_times],
                "minimum_break_minutes": s.minimum_break_minutes,
                "max_rooms_per_exam": s.max_rooms_per_exam,
                "venue_compatibility": {key: list(types) for key, types in s.venue_compatibility},
                "module_venue_overrides": {key: list(types) for key, types in s.module_venue_overrides},
            },
            "solver": asdict(self.solver),
            "optimization": asdict(self.optimization),
            "fairness": asdict(self.fairness),
        }

    @property
    def fingerprint(self) -> str:
        content = json.dumps({"format": SETTINGS_FORMAT, **self.to_document()},
                             sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(content.encode()).hexdigest()


def changed_fields(old: object, new: dict[str, Any]) -> list[str]:
    """Dotted paths whose values differ, e.g. ``scheduling.minimum_break_minutes``.

    ``old=None`` (nothing saved yet) compares with the built-in defaults. ``old`` may be
    an invalid stored document: missing or non-object sections and extra keys count as changes.
    """
    if old is None:
        old = SchedulingConfiguration().to_document()
    old = old if type(old) is dict else {}
    paths = set()
    for section in set(old) | set(new):
        before = old.get(section) if type(old.get(section)) is dict else {}
        after = new.get(section, {})
        if section in old and type(old[section]) is not dict:
            paths.add(str(section))
        for key in set(before) | set(after):
            if key not in before or key not in after or before[key] != after[key]:
                paths.add(f"{section}.{key}")
    return sorted(paths)
