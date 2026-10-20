"""Explicit DEMO MODE: run the existing Member 3 workflow on a SYNTHETIC m3_demo snapshot.

    M3DemoRecords (read-only snapshot)
      -> demo_mapper: DraftTimetable, venues, policies, staff, supplemental data, halls
      -> plan_duty_allocation (Step 9B; CP-SAT, fairness, independent validation)
      -> plan_student_seating (Step 6; synthetic halls, so every seat stays PROVISIONAL)

Nothing is saved, approved or published. The example-output tables (duty_requirements,
duty_roster_example, seat_assignments) are only cross-checked, never used as results.
All policies come from m3_demo and are labelled synthetic, not SLIIT rules.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections import Counter
from dataclasses import dataclass, replace

from app.duty_allocation.academic_adapter import AcademicStaffData
from app.duty_allocation.demo_database import M3DemoRecords
from app.duty_allocation.demo_mapper import (
    DemoPolicies, DemoTimetable, check_institution, map_capacity_rules, map_halls, map_policies, map_staff,
    map_supplemental, map_timetable,
)
from app.duty_allocation.orchestration import (
    AllocationMode, AllocationPolicies, DataOrigin, OrchestrationResult, SupplementalStaffData, plan_duty_allocation,
)
from app.duty_allocation.requirements import calculate_requirements
from app.duty_allocation.student_seating import PROJECT_CAPACITY_RULES, Hall, SeatingResult, plan_student_seating
from app.duty_allocation.types import DutyAssignment, DutyRole
from app.duty_allocation.validation import validate_allocation
from app.scheduling.contracts import SchedulingValidationError, Violation
from app.scheduling.policy import SolverSettings

DEMO_MODE_LABEL = ("DEMO MODE: SYNTHETIC m3_demo data and synthetic policies (not SLIIT data or rules). "
                   "Results are offline drafts; nothing is saved, approved or published.")


@dataclass(frozen=True)
class M3DemoInputs:
    timetable: DemoTimetable
    policies: DemoPolicies
    staff: AcademicStaffData
    supplemental: SupplementalStaffData
    halls: tuple[Hall, ...]
    notes: tuple[str, ...]


def build_demo_inputs(records: M3DemoRecords, *, institution_id: uuid.UUID, period_code: str, demo_reference: str,
                      as_of: dt.date | None = None) -> M3DemoInputs:
    """Map the snapshot into existing models. Raises DemoDataError listing every problem."""
    check_institution(records, institution_id)
    timetable = map_timetable(records, period_code=period_code, demo_reference=demo_reference)
    policies = map_policies(records)
    staff = map_staff(records, institution_id=institution_id, eligibility=policies.eligibility, as_of=as_of)
    supplemental = map_supplemental(records)
    halls, hall_notes = map_halls(records)
    notes = (DEMO_MODE_LABEL, *timetable.notes, *policies.notes, *map_capacity_rules(records, PROJECT_CAPACITY_RULES),
             *hall_notes, f"Session statuses in m3_demo: {', '.join(timetable.publication_statuses)}.")
    return M3DemoInputs(timetable, policies, staff, supplemental, halls, notes)


def requirement_mismatches(records: M3DemoRecords, inputs: M3DemoInputs) -> tuple[str, ...]:
    """Compare stored duty_requirements with what the staffing policy calculates now."""
    calc = calculate_requirements(inputs.timetable.draft, inputs.policies.staffing)
    computed = {r.room_key: (r.supervisors, r.invigilators) for r in calc.requirements}
    stored = {(r.get("session_id"), r.get("venue_id")): (r.get("supervisors_required"), r.get("invigilators_required"))
              for r in records.rows("duty_requirements")}
    out = [f"{v.constraint}: {v.message}" for v in calc.violations]
    for key in sorted(set(computed) | set(stored), key=str):
        if computed.get(key) != stored.get(key):
            out.append(f"duty_requirements {key[0]}/{key[1]}: stored {stored.get(key)}, calculated {computed.get(key)}.")
    return tuple(out)


def example_roster_violations(records: M3DemoRecords, result: OrchestrationResult,
                              inputs: M3DemoInputs) -> tuple[Violation, ...] | None:
    """Independently validate the workbook's greedy example roster (never used as a result)."""
    if result.allocation_input is None:
        return None
    try:
        roster = tuple(DutyAssignment(r.get("session_id"), r.get("venue_id"), str(r.get("staff_id")).lower(),
                                      DutyRole(r.get("role"))) for r in records.rows("duty_roster_example"))
    except (SchedulingValidationError, ValueError) as exc:
        return (Violation("D_INPUT", f"duty_roster_example is malformed: {exc}"),)
    base = inputs.policies.eligibility  # the same restrictions the orchestrator applied
    eligibility = replace(base, restrictions=base.restrictions + inputs.supplemental.restrictions)
    return validate_allocation(result.allocation_input, eligibility, inputs.policies.limits, roster)


@dataclass(frozen=True)
class DemoAllocationResult:
    orchestration: OrchestrationResult
    requirement_mismatches: tuple[str, ...]
    example_roster_violations: tuple[Violation, ...] | None
    notes: tuple[str, ...]


def run_demo_duty_allocation(records: M3DemoRecords, inputs: M3DemoInputs, *, mode: AllocationMode,
                             settings: SolverSettings = SolverSettings()) -> DemoAllocationResult:
    policies = AllocationPolicies(inputs.policies.staffing, inputs.policies.eligibility, inputs.policies.limits,
                                  inputs.policies.fairness, settings)
    result = plan_duty_allocation(inputs.timetable.draft, inputs.staff, policies, mode=mode,
                                  supplemental=inputs.supplemental, staff_origin=DataOrigin.PRELOADED,
                                  admin_loaded=True)
    return DemoAllocationResult(result, requirement_mismatches(records, inputs),
                                example_roster_violations(records, result, inputs), inputs.notes)


@dataclass(frozen=True)
class DemoSeatingResult:
    seating: SeatingResult
    example_problems: tuple[str, ...]  # problems found in the stored seat_assignments example
    notes: tuple[str, ...]


def check_example_seat_assignments(records: M3DemoRecords) -> tuple[str, ...]:
    """The stored example plan must match logical seats, use real layout seats once each and stay PROVISIONAL."""
    problems = []
    logical = {(r.get("session_id"), r.get("student_id")): r for r in records.rows("exam_logical_seats")}
    labels = {(s.get("layout_id"), s.get("seat_label")) for s in records.rows("physical_seats")}
    layout_venue = {r.get("layout_id"): r.get("venue_id") for r in records.rows("physical_layouts")}
    for plan in records.rows("seating_plans"):
        if plan.get("physical_verification") != "PROVISIONAL" or plan.get("approved") is not False:
            problems.append(f"seating_plans[{plan.get('plan_id')}] must be PROVISIONAL and unapproved.")
    used = Counter()
    for r in records.rows("seat_assignments"):
        key = (r.get("session_id"), r.get("student_id"))
        where = f"seat_assignments[{r.get('plan_id')}/{key[0]}/{key[1]}]"
        seat = logical.get(key)
        if seat is None:
            problems.append(f"{where} has no matching logical seat.")
        elif seat.get("venue_id") != r.get("venue_id") or seat.get("logical_seat_number") != r.get("logical_seat_number"):
            problems.append(f"{where} venue or logical seat differs from exam_logical_seats.")
        if layout_venue.get(r.get("layout_id")) != r.get("venue_id"):
            problems.append(f"{where} layout belongs to another venue.")
        if (r.get("layout_id"), r.get("provisional_physical_label")) not in labels:
            problems.append(f"{where} label {r.get('provisional_physical_label')!r} is not in its layout.")
        if r.get("verification_status") != "PROVISIONAL" or r.get("physically_verified") is not False:
            problems.append(f"{where} must stay PROVISIONAL and unverified.")
        used[(r.get("plan_id"), key[0], r.get("venue_id"), r.get("provisional_physical_label"))] += 1
    problems += [f"Seat {k[3]} in {k[1]}/{k[2]} is assigned {n} times." for k, n in used.items() if n > 1]
    return tuple(problems)


def run_demo_seating(records: M3DemoRecords, inputs: M3DemoInputs) -> DemoSeatingResult:
    seating = plan_student_seating(inputs.timetable.draft, inputs.timetable.venues, PROJECT_CAPACITY_RULES,
                                   inputs.halls)
    return DemoSeatingResult(seating, check_example_seat_assignments(records), inputs.notes)
