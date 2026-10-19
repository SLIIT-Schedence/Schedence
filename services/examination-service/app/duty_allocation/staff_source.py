"""Turn Academic Data Service staff records into duty-allocation StaffMember values.

Input rows have the academic-data StaffOut shape (snake_case JSON from GET /staff, or the
same fields as a mapping): id, staff_number, full_name, academic_rank, employment_status.
That service's code cannot be imported here (both services are the top-level package
`app`), so its records are accepted as plain data. Nothing is fetched or written.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from app.duty_allocation.eligibility_policy import EligibilityPolicy
from app.duty_allocation.types import DUTY_INPUT, StaffMember
from app.scheduling.contracts import SchedulingValidationError, Violation

# Matches academic-data EMPLOYMENT_ACTIVE / EMPLOYMENT_INACTIVE.
EMPLOYMENT_ACTIVE, EMPLOYMENT_INACTIVE = "ACTIVE", "INACTIVE"
STAFF_RECORD = "D_STAFF_RECORD"

_REQUIRED = ("id", "staff_number", "full_name", "employment_status")


@dataclass(frozen=True)
class ExcludedStaff:
    staff_id: str
    staff_number: str
    reason: str


@dataclass(frozen=True)
class StaffImport:
    staff: tuple[StaffMember, ...]  # active staff eligible for at least one duty role
    excluded: tuple[ExcludedStaff, ...]  # valid records left out, with the reason
    violations: tuple[Violation, ...]  # records that could not be read; never guessed

    @property
    def is_clean(self) -> bool:
        return not self.violations


def page_items(page: Mapping) -> list:
    """Rows of an academic-data Page[StaffOut] response ({"items": [...], "total": ...})."""
    if not isinstance(page, Mapping) or not isinstance(page.get("items"), list):
        raise SchedulingValidationError((Violation(DUTY_INPUT, "staff page must have an 'items' list."),))
    return page["items"]


def _staff_id(value: object) -> str | None:
    if isinstance(value, uuid.UUID):
        return str(value)
    try:
        return str(uuid.UUID(value)) if type(value) is str else None
    except ValueError:
        return None


def import_staff(records: Iterable[Mapping], policy: EligibilityPolicy) -> StaffImport:
    if type(policy) is not EligibilityPolicy:
        raise SchedulingValidationError((Violation(DUTY_INPUT, "import_staff needs an EligibilityPolicy."),))
    staff, excluded, violations, seen = [], [], [], set()
    for index, row in enumerate(records):
        label = f"staff record [{index}]"
        if not isinstance(row, Mapping) or any(key not in row for key in _REQUIRED):
            violations.append(Violation(STAFF_RECORD, f"{label} needs {', '.join(_REQUIRED)}."))
            continue
        staff_id = _staff_id(row["id"])
        if staff_id is None:
            violations.append(Violation(STAFF_RECORD, f"{label} id is not a UUID."))
            continue
        if staff_id in seen:
            violations.append(Violation(STAFF_RECORD, f"{label} repeats staff {staff_id}."))
            continue
        seen.add(staff_id)
        status, number = row["employment_status"], row["staff_number"]
        if status == EMPLOYMENT_INACTIVE:
            excluded.append(ExcludedStaff(staff_id, str(number), "employment status is INACTIVE"))
            continue
        if status != EMPLOYMENT_ACTIVE:
            violations.append(Violation(STAFF_RECORD, f"{label} has unknown employment_status {status!r}."))
            continue
        rank = row.get("academic_rank")
        if rank is not None and type(rank) is not str:
            violations.append(Violation(STAFF_RECORD, f"{label} academic_rank must be text or null."))
            continue
        if rank is not None and not rank.strip():
            rank = None  # blank rank text means no rank
        roles, reasons = policy.roles_for_rank(rank)
        if not roles:
            excluded.append(ExcludedStaff(staff_id, str(number), "; ".join(reasons)))
            continue
        try:
            staff.append(StaffMember(staff_id, number, row["full_name"], rank, roles))
        except SchedulingValidationError as exc:
            violations.extend(Violation(STAFF_RECORD, f"{label}: {v.message}") for v in exc.violations)
    return StaffImport(tuple(staff), tuple(excluded), tuple(violations))
