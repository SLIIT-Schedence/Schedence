"""Configurable duty-fairness policy and fairness inputs. No SLIIT weights are built in.

Load model (integers so CP-SAT can optimise it exactly):

    duty load      = role weight x (1 per duty | exam minutes), by load_unit
    history load   = history_weight x prior duties | prior minutes   (same unit)
    admin load     = round(admin_load_per_weekly_hour x admin hours per week)
    total load     = duty load + history load + admin load

Role weights and history_weight are percentages: 100 means one duty (or minute) counts
once. Objective weights choose how much each soft factor matters; 0 turns a factor off.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum

from app.duty_allocation.types import DUTY_INPUT, DutyRole, is_staff_uuid
from app.scheduling.contracts import SchedulingValidationError, Violation, frozen_sequence


def _fail(message: str) -> None:
    raise SchedulingValidationError((Violation(DUTY_INPUT, message),))


def _non_negative_int(value: object, label: str, minimum: int = 0) -> None:
    if type(value) is not int or value < minimum:
        _fail(f"{label} must be an int >= {minimum}.")


class LoadUnit(str, Enum):
    DUTIES = "DUTIES"
    MINUTES = "MINUTES"


class MissingAdminHours(str, Enum):
    ZERO = "ZERO"  # count missing or NOT_COMPUTED admin hours as 0 and report them
    REJECT = "REJECT"  # refuse to allocate until every staff member has admin hours


@dataclass(frozen=True)
class FairnessWeights:
    balance: int  # sum over staff pairs of |load difference| (Gini numerator)
    peak: int  # highest single total load
    back_to_back: int  # per pair of one person's duties closer than back_to_back_minutes
    preference: int  # per duty on an avoided day or outside a person's preferred days

    def __post_init__(self) -> None:
        for name in ("balance", "peak", "back_to_back", "preference"):
            _non_negative_int(getattr(self, name), f"fairness weight {name}")
        if not any((self.balance, self.peak, self.back_to_back, self.preference)):
            _fail("at least one fairness weight must be positive.")


@dataclass(frozen=True)
class DutyFairnessPolicy:
    source: str
    load_unit: LoadUnit
    supervisor_weight: int
    invigilator_weight: int
    history_weight: int
    admin_load_per_weekly_hour: int
    missing_admin_hours: MissingAdminHours
    back_to_back_minutes: int  # gap below this between one person's duties is back-to-back; 0 = off
    weights: FairnessWeights
    hard_avoid_days: bool = False  # True turns AVOID day preferences into hard constraints

    def __post_init__(self) -> None:
        if type(self.source) is not str or not self.source.strip():
            _fail("fairness policy source must be a non-empty string.")
        if type(self.load_unit) is not LoadUnit or type(self.missing_admin_hours) is not MissingAdminHours:
            _fail("fairness policy load_unit and missing_admin_hours must be enum values.")
        _non_negative_int(self.supervisor_weight, "supervisor_weight", 1)
        _non_negative_int(self.invigilator_weight, "invigilator_weight", 1)
        for name in ("history_weight", "admin_load_per_weekly_hour", "back_to_back_minutes"):
            _non_negative_int(getattr(self, name), name)
        if type(self.weights) is not FairnessWeights or type(self.hard_avoid_days) is not bool:
            _fail("fairness policy needs FairnessWeights and a bool hard_avoid_days.")

    def role_weight(self, role: DutyRole) -> int:
        return self.supervisor_weight if role is DutyRole.SUPERVISOR else self.invigilator_weight

    def duty_load(self, role: DutyRole, minutes: int) -> int:
        return self.role_weight(role) * (minutes if self.load_unit is LoadUnit.MINUTES else 1)

    @classmethod
    def from_mapping(cls, data: Mapping) -> DutyFairnessPolicy:
        """{"source": "...", "load_unit": "MINUTES", "supervisor_weight": 100, "invigilator_weight": 100,
            "history_weight": 100, "admin_load_per_weekly_hour": 0, "missing_admin_hours": "ZERO",
            "back_to_back_minutes": 0, "hard_avoid_days": false,
            "weights": {"balance": 1, "peak": 0, "back_to_back": 0, "preference": 0}}"""
        keys = {"source", "load_unit", "supervisor_weight", "invigilator_weight", "history_weight",
                "admin_load_per_weekly_hour", "missing_admin_hours", "back_to_back_minutes", "weights"}
        if not isinstance(data, Mapping) or not keys <= set(data) or set(data) - keys - {"hard_avoid_days"}:
            _fail(f"fairness config needs exactly {', '.join(sorted(keys))} (hard_avoid_days optional).")
        w = data["weights"]
        if not isinstance(w, Mapping) or set(w) != {"balance", "peak", "back_to_back", "preference"}:
            _fail("fairness config weights needs exactly balance, peak, back_to_back, preference.")
        try:
            unit, missing = LoadUnit(data["load_unit"]), MissingAdminHours(data["missing_admin_hours"])
        except ValueError:
            _fail("fairness config load_unit or missing_admin_hours is not a known value.")
        return cls(data["source"], unit, data["supervisor_weight"], data["invigilator_weight"],
                   data["history_weight"], data["admin_load_per_weekly_hour"], missing,
                   data["back_to_back_minutes"],
                   FairnessWeights(w["balance"], w["peak"], w["back_to_back"], w["preference"]),
                   data.get("hard_avoid_days", False))


# --- Fairness inputs ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DutyHistory:
    """Duties a staff member already did in earlier periods (no source system exists yet)."""
    staff_id: str
    prior_duties: int
    prior_minutes: int

    def __post_init__(self) -> None:
        if not is_staff_uuid(self.staff_id):
            _fail("history staff_id must be a lowercase UUID string.")
        _non_negative_int(self.prior_duties, "prior_duties")
        _non_negative_int(self.prior_minutes, "prior_minutes")

    def amount(self, unit: LoadUnit) -> int:
        return self.prior_minutes if unit is LoadUnit.MINUTES else self.prior_duties


@dataclass(frozen=True)
class AdminWorkload:
    """Weekly administrative hours from academic-data AdministrativeWorkloadOut.
    hours_per_week is None when that service reports NOT_COMPUTED."""
    staff_id: str
    hours_per_week: float | None
    note: str = ""

    def __post_init__(self) -> None:
        if not is_staff_uuid(self.staff_id):
            _fail("admin workload staff_id must be a lowercase UUID string.")
        if self.hours_per_week is not None and (type(self.hours_per_week) not in (int, float)
                                                or not 0 <= self.hours_per_week < float("inf")):
            _fail("admin hours_per_week must be None or a finite number >= 0.")


class DayPreferenceKind(str, Enum):
    PREFER = "PREFER"  # with any PREFER days, duties on other days count against preference
    AVOID = "AVOID"


@dataclass(frozen=True)
class DayPreference:
    staff_id: str
    day: dt.date
    kind: DayPreferenceKind

    def __post_init__(self) -> None:
        if not is_staff_uuid(self.staff_id) or type(self.day) is not dt.date or type(self.kind) is not DayPreferenceKind:
            _fail("day preference needs a UUID staff_id, a date and a DayPreferenceKind.")


@dataclass(frozen=True)
class FairnessInputs:
    history: tuple[DutyHistory, ...] = ()
    admin: tuple[AdminWorkload, ...] = ()
    preferences: tuple[DayPreference, ...] = ()

    def __post_init__(self) -> None:
        for name, record in (("history", DutyHistory), ("admin", AdminWorkload), ("preferences", DayPreference)):
            rows = frozen_sequence(getattr(self, name), name, record)
            object.__setattr__(self, name, rows)
        for name in ("history", "admin"):
            ids = [row.staff_id for row in getattr(self, name)]
            if len(ids) != len(set(ids)):
                _fail(f"fairness {name} must have at most one row per staff member.")

    def unknown_staff(self, staff_ids: Iterable[str]) -> tuple[str, ...]:
        known = set(staff_ids)
        rows = (*self.history, *self.admin, *self.preferences)
        return tuple(sorted({row.staff_id for row in rows if row.staff_id not in known}))


def admin_workload_from_records(records: Iterable[Mapping]) -> tuple[AdminWorkload, ...]:
    """Read academic-data AdministrativeWorkloadOut JSON rows (GET /staff/{id}/administrative-workload)."""
    rows = []
    for index, row in enumerate(records):
        aggregate = row.get("aggregate") if isinstance(row, Mapping) else None
        if not isinstance(aggregate, Mapping) or "staff_id" not in row:
            _fail(f"admin workload record [{index}] needs staff_id and aggregate.")
        staff_id = str(row["staff_id"]).lower()
        if aggregate.get("status") == "COMPUTED" and aggregate.get("total_admin_hours_per_week") is not None:
            rows.append(AdminWorkload(staff_id, aggregate["total_admin_hours_per_week"], aggregate.get("rule") or ""))
        elif aggregate.get("status") in ("COMPUTED", "NOT_COMPUTED"):
            rows.append(AdminWorkload(staff_id, None, aggregate.get("reason") or "NOT_COMPUTED"))
        else:
            _fail(f"admin workload record [{index}] has an unknown aggregate status.")
    return tuple(rows)
