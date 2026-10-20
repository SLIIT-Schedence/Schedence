"""Explicit DEMO MODE: m3_demo snapshot -> Step 9B orchestrator and Step 6 seating.

Mocked row sources only (compact SYNTHETIC fixture, and the seed-pack CSVs when present).
Nothing connects to a database; nothing is saved, approved or published.
"""

from __future__ import annotations

import uuid

import pytest

pytest.importorskip("ortools", reason="install the examination service [solver] extra")

from app.duty_allocation import AllocationMode, DataOrigin, DataStatus, OrchestrationStatus, validate_allocation
from app.duty_allocation.demo_database import load_m3_demo_records
from app.duty_allocation.demo_integration import (
    DEMO_MODE_LABEL, build_demo_inputs, check_example_seat_assignments, run_demo_duty_allocation, run_demo_seating,
)
from app.duty_allocation.demo_mapper import DemoDataError
from app.duty_allocation.student_seating import SeatingStatus
from app.scheduling.policy import SolverSettings
from duty_allocation_demo_support import DEMO_REF, INST, PERIOD, compact_source, seed_pack_source, sid

FAST = SolverSettings(time_limit_seconds=10)


async def demo(change=None):
    snap = await load_m3_demo_records(compact_source(change))
    return snap, build_demo_inputs(snap, institution_id=uuid.UUID(INST), period_code=PERIOD, demo_reference=DEMO_REF)


def revalidate(result, inputs):
    from dataclasses import replace
    elig = replace(inputs.policies.eligibility,
                   restrictions=inputs.policies.eligibility.restrictions + inputs.supplemental.restrictions)
    return validate_allocation(result.allocation_input, elig, inputs.policies.limits, result.roster)


# --- Allocation --------------------------------------------------------------------------------------

async def test_baseline_allocation_from_m3_demo():
    snap, inputs = await demo()
    r = run_demo_duty_allocation(snap, inputs, mode=AllocationMode.BASELINE, settings=FAST)
    o = r.orchestration
    assert o.status is OrchestrationStatus.COMPLETED and len(o.roster) == 8  # 4 rooms x (1 + 1)
    assert o.revalidation == (("BASELINE", ()),) and revalidate(o, inputs) == ()
    assert o.saved is False and o.approved is False and o.published is False
    assert DEMO_MODE_LABEL in r.notes and r.requirement_mismatches == ()


async def test_fair_and_compare_allocation_from_m3_demo():
    snap, inputs = await demo()
    fair = run_demo_duty_allocation(snap, inputs, mode=AllocationMode.FAIR, settings=FAST).orchestration
    assert fair.status is OrchestrationStatus.COMPLETED and fair.fair_metrics is not None and revalidate(fair, inputs) == ()
    both = run_demo_duty_allocation(snap, inputs, mode=AllocationMode.COMPARE, settings=FAST).orchestration
    assert both.status is OrchestrationStatus.COMPLETED
    assert dict(both.revalidation) == {"BASELINE": (), "FAIR": ()}
    assert both.fair_metrics.std_load <= both.baseline_metrics.std_load


async def test_stored_constraints_are_applied():
    snap, inputs = await demo()
    roster = run_demo_duty_allocation(snap, inputs, mode=AllocationMode.BASELINE, settings=FAST).orchestration.roster
    assert sid(2) not in {a.staff_id for a in roster if a.session_id in ("X1-IT1010", "X2-IT1020")}  # day-0 leave
    assert sid(1) not in {a.staff_id for a in roster if a.session_id == "X1-IT1010"}  # module-lecturer restriction
    assert sid(8) not in {a.staff_id for a in roster}  # inactive
    supervisors = {a.staff_id for a in roster if a.role.value == "SUPERVISOR"}
    assert supervisors <= {sid(n) for n in (1, 2, 3, 4)}  # Senior Lecturer II and above


async def test_data_provenance_and_partial_fairness_are_reported():
    snap, inputs = await demo()
    o = run_demo_duty_allocation(snap, inputs, mode=AllocationMode.FAIR, settings=FAST).orchestration
    items = {d.name: d for d in o.data_availability}
    assert items["staff"].origin is DataOrigin.PRELOADED and "not verified as live" in items["staff"].source
    assert items["duty history"].status is DataStatus.PROVIDED and items["duty history"].origin is DataOrigin.SUPPLEMENTAL
    assert not o.fairness_complete  # staff 5 NOT_COMPUTED + staff 6 MISSING_RESPONSE -> admin not fully covered
    assert any("counted as 0 because missing_admin_hours=ZERO" in d for d in o.diagnostics)
    assert any(f"No workload record for staff {sid(6)}" in d for d in o.diagnostics)


async def test_infeasible_demo_is_reported_not_hidden():
    def short_staffed(t):  # leave only two Supervisor-ranked staff active
        for row in t["staff"][2:4]:
            row["employment_status"] = "INACTIVE"
    snap, inputs = await demo(short_staffed)
    o = run_demo_duty_allocation(snap, inputs, mode=AllocationMode.BASELINE, settings=FAST).orchestration
    assert o.status is OrchestrationStatus.INFEASIBLE and o.roster == () and o.violations


async def test_stored_requirements_are_cross_checked():
    snap, inputs = await demo(lambda t: t["duty_requirements"][0].update(supervisors_required=3))
    r = run_demo_duty_allocation(snap, inputs, mode=AllocationMode.BASELINE, settings=FAST)
    assert len(r.requirement_mismatches) == 1 and "stored (3, 1), calculated (1, 1)" in r.requirement_mismatches[0]


async def test_example_roster_is_validated_not_used():
    def example(t):
        t["duty_roster_example"] = [dict(assignment_id="A1", run_id="R", session_id="X1-IT1010", staff_id=sid(5),
                                         duty_date=None, session_ref=None, venue_id="H1", role="SUPERVISOR",
                                         generation_source="WORKBOOK_EXAMPLE", roster_state="DRAFT", approved=False)]
    snap, inputs = await demo(example)
    r = run_demo_duty_allocation(snap, inputs, mode=AllocationMode.BASELINE, settings=FAST)
    codes = {v.constraint for v in r.example_roster_violations}
    assert {"D1", "D6"} <= codes  # incomplete roster; a Lecturer cannot supervise
    assert r.orchestration.status is OrchestrationStatus.COMPLETED  # the solver's roster, not the example


async def test_inputs_are_not_modified():
    snap, inputs = await demo()
    before = (repr(snap.tables), repr(inputs))
    run_demo_duty_allocation(snap, inputs, mode=AllocationMode.COMPARE, settings=FAST)
    run_demo_seating(snap, inputs)
    assert (repr(snap.tables), repr(inputs)) == before
    with pytest.raises(TypeError):
        snap.tables["staff"] = ()  # the snapshot is read-only


async def test_mapping_errors_stop_the_demo():
    with pytest.raises(DemoDataError):
        await demo(lambda t: t["exam_room_allocations"][0].update(venue_id="Z9"))


# --- Seating --------------------------------------------------------------------------------------

async def test_demo_seating_is_complete_and_provisional():
    snap, inputs = await demo()
    r = run_demo_seating(snap, inputs)
    plan = r.seating.plan
    assert r.seating.status is SeatingStatus.COMPLETE and len(plan.assignments) == 11
    assert not any(a.seat_verified for a in plan.assignments) and not plan.all_seats_verified
    assert set(plan.synthetic_halls) == {"H1", "L1"} and plan.provisional_halls == ()
    labels = [(a.session_id, a.venue_id, a.seat_label) for a in plan.assignments]
    assert len(labels) == len(set(labels))
    assert r.example_problems == ()


@pytest.mark.parametrize("change,match", [
    (lambda t: t["seat_assignments"][1].update(provisional_physical_label="R01-S01"), "assigned 2 times"),
    (lambda t: t["seat_assignments"][0].update(verification_status="VERIFIED"), "must stay PROVISIONAL"),
    (lambda t: t["seat_assignments"][0].update(provisional_physical_label="R99-S99"), "not in its layout"),
    (lambda t: t["seat_assignments"][0].update(venue_id="L1"), "venue or logical seat differs"),
    (lambda t: t["seating_plans"][0].update(physical_verification="VERIFIED"), "must be PROVISIONAL"),
])
async def test_stored_seat_assignment_example_is_checked(change, match):
    snap, _ = await demo(change)
    assert any(match in p for p in check_example_seat_assignments(snap))


# --- Full seed pack (offline, mocked; skipped when the pack is not present) ------------------------------

@pytest.fixture(scope="module")
def seed_pack():
    loaded = seed_pack_source()
    if loaded is None:
        pytest.skip("Member 3 seed pack not found; set M3_SEED_PACK_DIR to run")
    return loaded


async def test_full_seed_pack_maps_and_allocates(seed_pack):
    source, manifest = seed_pack
    snap = await load_m3_demo_records(source)
    inputs = build_demo_inputs(snap, institution_id=uuid.UUID(manifest["institution_id"]),
                               period_code=manifest["period_code"],
                               demo_reference=f"DEMO-UNPUBLISHED:m3_demo/{manifest['dataset_id']}")
    d = inputs.timetable.draft
    assert (len(d.sessions), sum(len(s.rooms) for s in d.sessions)) == (24, 26)
    assert sum(len(r.seats) for s in d.sessions for r in s.rooms) == 1200
    assert len(inputs.staff.staff.staff) == 46 and len(inputs.staff.staff.excluded) == 2
    r = run_demo_duty_allocation(snap, inputs, mode=AllocationMode.COMPARE, settings=SolverSettings(time_limit_seconds=20))
    o = r.orchestration
    assert o.status is OrchestrationStatus.COMPLETED and dict(o.revalidation) == {"BASELINE": (), "FAIR": ()}
    assert len(o.roster) == 64 and r.requirement_mismatches == () and r.example_roster_violations == ()
    assert o.fair_metrics.gini <= o.baseline_metrics.gini
    seating = run_demo_seating(snap, inputs)
    assert seating.seating.status is SeatingStatus.COMPLETE and len(seating.seating.plan.assignments) == 1200
    assert not any(a.seat_verified for a in seating.seating.plan.assignments) and seating.example_problems == ()
    assert source.calls[0] == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
