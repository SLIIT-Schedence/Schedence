"""The data a saved roster is validated against, rebuilt on demand.

A saved roster stores only assignments; to validate it again (or check a change) the same
inputs the allocator used must be rebuilt: timetable, staff, requirements, unavailability,
availability, eligibility and limits. :func:`assemble_context` builds them from a demo
context exactly as ``plan_duty_allocation`` does (a test compares the two). A digest of those
inputs is stored with the roster, so a roster is never validated or approved against changed
data without noticing.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from typing import Protocol

from app.duty_allocation.allocation_policy import DutyLimitPolicy
from app.duty_allocation.demo_service import DemoContext, Member3DemoService
from app.duty_allocation.eligibility_policy import EligibilityPolicy
from app.duty_allocation.fairness_policy import DutyFairnessPolicy, FairnessInputs
from app.duty_allocation.requirements import calculate_requirements
from app.duty_allocation.staffing_policy import StaffingPolicy
from app.duty_allocation.types import DutyAllocationInput, StaffMember
from app.scheduling.contracts import SchedulingValidationError, Violation
from app.scheduling.types import DraftTimetable


@dataclass(frozen=True)
class RosterContext:
    data: DutyAllocationInput
    eligibility: EligibilityPolicy
    limits: DutyLimitPolicy
    staffing: StaffingPolicy
    fairness: tuple[FairnessInputs, DutyFairnessPolicy] | None
    digest: str
    data_source: str
    synthetic: bool
    timetable_reference: str
    timetable_fingerprint: str

    @property
    def staff(self) -> dict[str, StaffMember]:
        return {m.staff_id: m for m in self.data.staff}

    def on_timetable(self, draft: DraftTimetable) -> RosterContext:
        """The same staff and constraints on another timetable (requirements recalculated)."""
        calc = calculate_requirements(draft, self.staffing)
        if not calc.is_complete:
            raise SchedulingValidationError(calc.violations)
        data = DutyAllocationInput(draft, self.data.staff, calc.requirements, self.data.unavailability, self.data.availability)
        return replace(self, data=data)


class RosterContextResolver(Protocol):
    async def resolve(self, period_code: str) -> RosterContext: ...


def inputs_digest(data: DutyAllocationInput, eligibility: EligibilityPolicy, limits: DutyLimitPolicy) -> str:
    """SHA-256 over the inputs that decide whether a roster is valid (canonical JSON, stable across runs)."""
    doc = {
        "sessions": sorted([s.session_id, s.module_code, s.start.isoformat(), s.end.isoformat(),
                            sorted([r.venue_id, len(r.seats)] for r in s.rooms)] for s in data.timetable.sessions),
        "staff": sorted([m.staff_id, m.academic_rank, sorted(r.value for r in m.eligible_roles)] for m in data.staff),
        "requirements": sorted([r.session_id, r.venue_id, r.supervisors, r.invigilators] for r in data.requirements),
        "unavailability": sorted([u.staff_id, u.start.isoformat(), u.end.isoformat()] for u in data.unavailability),
        "availability": sorted([a.staff_id, a.start.isoformat(), a.end.isoformat()] for a in data.availability),
        "restrictions": sorted([r.staff_id, r.role.value if r.role else None, r.session_id, r.module_code]
                               for r in eligibility.restrictions),
        "rank_order": list(eligibility.rank_order), "minimum_gap": eligibility.minimum_gap_minutes,
        "limits": [limits.max_duties_per_day, limits.max_duties_per_period],
        "role_rules": sorted([r.role.value, r.minimum_rank, r.allow_unranked] for r in eligibility.role_rules),
    }
    return hashlib.sha256(json.dumps(doc, sort_keys=True, default=str).encode()).hexdigest()


def assemble_context(context: DemoContext) -> RosterContext:
    """Rebuild the allocator's inputs for a demo context, the way ``plan_duty_allocation`` assembles them."""
    inputs = context.inputs
    policies, sup = inputs.policies, inputs.supplemental
    known = {m.staff_id for m in inputs.staff.staff.staff}
    keep = lambda rows: tuple(r for r in (rows or ()) if r.staff_id in known)  # noqa: E731  (rows of excluded staff are ignored)
    draft = inputs.timetable.draft
    calc = calculate_requirements(draft, policies.staffing)
    if not calc.is_complete:
        raise SchedulingValidationError(calc.violations)
    data = DutyAllocationInput(draft, inputs.staff.staff.staff, calc.requirements, keep(sup.unavailability),
                               keep(sup.availability))
    eligibility = replace(policies.eligibility, restrictions=policies.eligibility.restrictions + keep(sup.restrictions))
    fairness = (FairnessInputs(keep(sup.duty_history), keep(inputs.staff.admin), keep(sup.day_preferences)), policies.fairness)
    return RosterContext(data, eligibility, policies.limits, policies.staffing, fairness,
                         inputs_digest(data, eligibility, policies.limits), context.snapshot.data_source.value,
                         True,  # every m3_demo source is synthetic, whichever way it was read
                         draft.input_fingerprint, draft.input_fingerprint)


class DemoRosterContextResolver:
    """Rebuilds a saved roster's inputs from the current demo snapshot."""

    def __init__(self, service: Member3DemoService) -> None:
        self._service = service

    async def resolve(self, period_code: str) -> RosterContext:
        return assemble_context(await self._service.context(period_code))


def violations_json(violations: tuple[Violation, ...] | list[Violation]) -> list[dict[str, str]]:
    return [{"constraint": v.constraint, "message": v.message} for v in violations]


__all__ = ["DemoRosterContextResolver", "RosterContext", "RosterContextResolver", "assemble_context", "inputs_digest",
           "violations_json", "uuid"]
