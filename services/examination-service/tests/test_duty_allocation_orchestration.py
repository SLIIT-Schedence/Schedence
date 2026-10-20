"""End-to-end in-memory duty allocation orchestration (Step 9B).

Staffing uses the project's CONFIRMED_EXAMPLES_POLICY (60 -> 1+1, 150 -> 2+2), so rooms
hold exactly 60 or 150 students. Staff, ranks, workloads, policies and the Academic Data
responses are SYNTHETIC; no real service is called.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections import Counter

import httpx
import pytest

pytest.importorskip("ortools", reason="install the examination service [solver] extra")

from app.duty_allocation import (
    CONFIRMED_EXAMPLES_POLICY, AcademicDataClient, AcademicStaffData, AdminWorkload, AllocationMode,
    AllocationPolicies, DataOrigin, DataStatus, DayPreference, DayPreferenceKind, DutyFairnessPolicy, DutyHistory,
    DutyLimitPolicy, DutyRole, EligibilityPolicy, FairnessWeights, LoadUnit, MissingAdminHours,
    OrchestrationStatus, RoleRule, StaffingPolicy, StaffingRule, StaffRestriction, StaffUnavailability,
    SupplementalStaffData, import_staff, plan_duty_allocation, plan_duty_allocation_from_academic,
    validate_allocation,
)
from app.duty_allocation.eligibility import ROOM_SHORTAGE
from app.duty_allocation.requirements import UNCOVERED_ROOM
from app.duty_allocation.staff_source import STAFF_RECORD
from app.scheduling.policy import SolverSettings
from app.scheduling.types import DraftTimetable, ExamSession, RoomAllocation, SeatAllocation

INST = uuid.UUID(int=1000)
TOKEN = "synthetic-token"
DAY = dt.date(2026, 11, 2)
FAST = SolverSettings(time_limit_seconds=10)
ELIGIBILITY = EligibilityPolicy(("Rank A", "Rank B"), (RoleRule(DutyRole.SUPERVISOR, "Rank A", False),
                                                      RoleRule(DutyRole.INVIGILATOR, None, True)), 60, "synthetic")
FAIRNESS = DutyFairnessPolicy("synthetic", LoadUnit.MINUTES, 100, 100, 100, 0, MissingAdminHours.ZERO, 0,
                              FairnessWeights(1, 0, 0, 0))


def sid(n: int) -> str:
    return str(uuid.UUID(int=n))


def at(hour, minute=0, day=0) -> dt.datetime:
    return dt.datetime.combine(DAY + dt.timedelta(days=day), dt.time(hour, minute))


def room(venue, students, prefix="IT26") -> RoomAllocation:
    return RoomAllocation(venue, tuple(SeatAllocation(f"{prefix}{venue}{n:04}", n) for n in range(1, students + 1)))


def draft(*sessions) -> DraftTimetable:
    return DraftTimetable("SYN-PERIOD", "member2-fingerprint", tuple(sessions))


def exam(session_id, start, end, *rooms, module="IT1010") -> ExamSession:
    return ExamSession(session_id, module, "SYN-PERIOD", start, end, tuple(rooms))


# Two days, three sessions, 150- and 60-student rooms: 2+2, 1+1, 1+1, 2+2 positions.
STANDARD = draft(exam("D0-AM", at(8, 30), at(10, 30), room("A501", 150), room("B401", 60)),
                 exam("D0-PM", at(13, 30), at(15, 30), room("C201", 60), module="IT1050"),
                 exam("D1-AM", at(8, 30, 1), at(10, 30, 1), room("A501", 150, "IT27"), module="IT2020"))


def records(ranks, status=None) -> list[dict]:
    status = status or {}
    return [{"id": sid(n), "institution_id": str(INST), "staff_number": f"EMP{n:03}", "full_name": f"Staff {n}",
             "academic_rank": rank, "employment_status": status.get(n, "ACTIVE")}
            for n, rank in enumerate(ranks, 1)]


RANKS = ["Rank A"] * 5 + ["Rank B"] * 7 + [None]  # 13 synthetic staff


def academic(ranks=RANKS, status=None, admin=(), missing=()) -> AcademicStaffData:
    return AcademicStaffData(import_staff(records(ranks, status), ELIGIBILITY), tuple(admin), tuple(missing))


def policies(staffing=CONFIRMED_EXAMPLES_POLICY, limits=DutyLimitPolicy(2, None, "synthetic"), fairness=FAIRNESS,
             eligibility=ELIGIBILITY) -> AllocationPolicies:
    return AllocationPolicies(staffing, eligibility, limits, fairness, FAST)


def run(d=STANDARD, data=None, mode=AllocationMode.BASELINE, **kwargs):
    # BASELINE by default to keep tests fast; fairness scope and diagnostics are still
    # computed whenever a fairness policy is supplied. Fairness results use COMPARE/FAIR.
    pol = kwargs.pop("pol", None) or policies()
    return plan_duty_allocation(d, data or academic(), pol, mode=mode, **kwargs)


# --- 1-3, 12: successful allocation with independent validation ----------------------------------------

def test_baseline_allocation_fills_every_position_and_is_revalidated():
    result = run(mode=AllocationMode.BASELINE)
    assert result.status is OrchestrationStatus.COMPLETED and result.fair is None
    counts = Counter((a.session_id, a.venue_id, a.role) for a in result.roster)
    assert counts[("D0-AM", "A501", DutyRole.SUPERVISOR)] == 2 and counts[("D0-AM", "B401", DutyRole.INVIGILATOR)] == 1
    assert len(result.roster) == result.requirements.total_supervisors + result.requirements.total_invigilators == 12
    assert result.revalidation == (("BASELINE", ()),)
    assert validate_allocation(result.allocation_input, ELIGIBILITY, policies().limits, result.roster) == ()


def test_fair_and_compare_modes():
    fair = run(mode=AllocationMode.FAIR)
    assert fair.status is OrchestrationStatus.COMPLETED and fair.baseline is None and fair.fair_metrics is not None
    both = run(mode=AllocationMode.COMPARE)
    assert both.status is OrchestrationStatus.COMPLETED
    assert both.fair_metrics.std_load <= both.baseline_metrics.std_load
    assert dict(both.revalidation) == {"BASELINE": (), "FAIR": ()}


def test_outputs_are_labelled_offline_drafts():
    result = run(mode=AllocationMode.COMPARE)
    assert result.saved is False and result.approved is False and result.published is False
    assert "OFFLINE IN-MEMORY DRAFT" in result.label and result.diagnostics[0] == result.label


# --- 4-7: staffing limits and hard constraints ----------------------------------------------------------

def test_insufficient_eligible_staff_is_infeasible_with_reasons():
    result = run(data=academic(["Rank A"] + ["Rank B"] * 12))  # A501 needs 2 Supervisors
    assert result.status is OrchestrationStatus.INFEASIBLE
    assert ROOM_SHORTAGE in {v.constraint for v in result.violations}


def test_only_ranked_staff_supervise():
    result = run()
    supervisors = {a.staff_id for a in result.roster if a.role is DutyRole.SUPERVISOR}
    assert supervisors <= {sid(n) for n in range(1, 6)}  # Rank A only


def test_unavailable_staff_and_overlapping_exams():
    overlapping = draft(exam("X1", at(8, 30), at(10, 30), room("A501", 150)),
                        exam("X2", at(9), at(11), room("B401", 60), module="IT1050"))
    off = SupplementalStaffData("synthetic leave register",
                                unavailability=(StaffUnavailability(sid(1), at(7), at(12), "external board"),))
    result = run(overlapping, supplemental=off)
    assert result.status is OrchestrationStatus.COMPLETED
    assert sid(1) not in {a.staff_id for a in result.roster}
    x1 = {a.staff_id for a in result.roster if a.session_id == "X1"}
    x2 = {a.staff_id for a in result.roster if a.session_id == "X2"}
    assert not x1 & x2


def test_duty_limits_are_respected_or_reported():
    limited = run(pol=policies(limits=DutyLimitPolicy(1, None, "synthetic")))
    per_day = Counter((a.staff_id, a.session_id[:2]) for a in limited.roster)
    assert limited.status is OrchestrationStatus.COMPLETED and max(per_day.values()) == 1
    tight = run(data=academic(["Rank A"] * 2 + ["Rank B"] * 2), pol=policies(limits=DutyLimitPolicy(None, 2, "synthetic")))
    assert tight.status is OrchestrationStatus.INFEASIBLE  # 12 positions, 4 people x 2 duties


def test_infeasible_solver_result_is_not_hidden():
    result = run(data=academic(["Rank A"] * 4 + ["Rank B"] * 2), pol=policies(limits=DutyLimitPolicy(None, 1, "synthetic")))
    assert result.status is OrchestrationStatus.INFEASIBLE and result.roster == ()
    assert any("No duty roster satisfies" in d or "shortage" in d for d in result.diagnostics)


# --- 8: administrative workload ------------------------------------------------------------------------

ADMIN_FAIRNESS = DutyFairnessPolicy("synthetic", LoadUnit.DUTIES, 100, 100, 100, 10, MissingAdminHours.REJECT, 0,
                                    FairnessWeights(1, 0, 0, 0))


PARTIAL_ADMIN = [AdminWorkload(sid(1), 6.0), AdminWorkload(sid(2), None, "NOT_COMPUTED")]


@pytest.mark.parametrize("mode", [AllocationMode.FAIR, AllocationMode.COMPARE])
def test_missing_admin_workload_with_reject_policy_is_invalid_for_fairness_modes(mode):
    result = run(data=academic(admin=PARTIAL_ADMIN), pol=policies(fairness=ADMIN_FAIRNESS), mode=mode)
    assert result.status is OrchestrationStatus.INVALID_INPUT
    assert any("admin hours missing for staff" in v.message for v in result.violations)


def test_baseline_is_not_blocked_by_reject_and_reports_metrics_unavailable():
    result = run(data=academic(admin=PARTIAL_ADMIN), pol=policies(fairness=ADMIN_FAIRNESS), mode=AllocationMode.BASELINE)
    assert result.status is OrchestrationStatus.COMPLETED and len(result.roster) == 12
    assert result.revalidation == (("BASELINE", ()),)  # hard constraints still checked
    assert result.baseline_metrics is None  # not estimated
    assert any("Baseline fairness metrics UNAVAILABLE" in d and "none were estimated" in d for d in result.diagnostics)
    supervisors = {a.staff_id for a in result.roster if a.role is DutyRole.SUPERVISOR}
    assert supervisors <= {sid(n) for n in range(1, 6)}  # rank rules still apply


def test_baseline_still_rejects_other_fairness_input_errors():
    stranger = academic(admin=[AdminWorkload(sid(99), 1.0)])  # admin row for unknown staff
    result = run(data=stranger, pol=policies(fairness=ADMIN_FAIRNESS), mode=AllocationMode.BASELINE)
    assert result.status is OrchestrationStatus.INVALID_INPUT
    assert any("unknown staff" in v.message for v in result.violations)


def test_baseline_with_zero_policy_computes_metrics_and_reports_missing():
    zero = DutyFairnessPolicy("synthetic", LoadUnit.DUTIES, 100, 100, 100, 10, MissingAdminHours.ZERO, 0,
                              FairnessWeights(1, 0, 0, 0))
    result = run(data=academic(admin=PARTIAL_ADMIN), pol=policies(fairness=zero), mode=AllocationMode.BASELINE)
    assert result.status is OrchestrationStatus.COMPLETED and result.baseline_metrics is not None
    assert any("counted as 0 because missing_admin_hours=ZERO" in d for d in result.diagnostics)


def test_missing_admin_workload_with_zero_policy_is_reported_and_partial():
    zero = DutyFairnessPolicy("synthetic", LoadUnit.DUTIES, 100, 100, 100, 10, MissingAdminHours.ZERO, 0,
                              FairnessWeights(1, 0, 0, 0))
    result = run(data=academic(admin=[AdminWorkload(sid(1), 6.0)]), pol=policies(fairness=zero))
    assert result.status is OrchestrationStatus.COMPLETED and not result.fairness_complete
    assert "admin workload" not in result.fairness_scope
    assert any("counted as 0 because missing_admin_hours=ZERO" in d for d in result.diagnostics)


# --- Missing data is reported, not invented -------------------------------------------------------------

def test_unavailable_data_is_distinguished_from_empty_data():
    result = run(supplemental=SupplementalStaffData("synthetic", duty_history=(), unavailability=None))
    status = {d.name: d.status for d in result.data_availability}
    assert status["duty history"] is DataStatus.PROVIDED_EMPTY
    assert status["unavailability"] is DataStatus.NOT_AVAILABLE and status["day preferences"] is DataStatus.NOT_AVAILABLE
    assert status["admin workload"] is DataStatus.NOT_AVAILABLE
    assert any("day preferences: NOT AVAILABLE" in d for d in result.diagnostics)
    assert not result.fairness_complete and any("Fairness is PARTIAL" in d for d in result.diagnostics)


def test_fairness_complete_only_with_history_preferences_and_admin_hours():
    admin = [AdminWorkload(sid(n), 2.0) for n in range(1, 14)]
    full = DutyFairnessPolicy("synthetic", LoadUnit.DUTIES, 100, 100, 100, 10, MissingAdminHours.REJECT, 0,
                              FairnessWeights(1, 0, 0, 1))
    supplemental = SupplementalStaffData("synthetic", duty_history=(DutyHistory(sid(6), 3, 360),),
                                         day_preferences=(DayPreference(sid(7), DAY, DayPreferenceKind.AVOID),))
    result = run(data=academic(admin=admin), pol=policies(fairness=full), supplemental=supplemental)
    assert result.status is OrchestrationStatus.COMPLETED and result.fairness_complete
    assert {"duty history", "day preferences", "admin workload"} <= set(result.fairness_scope)


def test_supplemental_restrictions_are_applied():
    supplemental = SupplementalStaffData("synthetic lecturer-module list",
                                         restrictions=(StaffRestriction(sid(1), "module lecturer", module_code="IT1010"),))
    result = run(supplemental=supplemental)
    assert sid(1) not in {a.staff_id for a in result.roster if a.session_id == "D0-AM"}


def test_supplemental_data_for_unknown_or_excluded_staff():
    unknown = SupplementalStaffData("synthetic", duty_history=(DutyHistory(sid(99), 1, 60),))
    assert run(supplemental=unknown).status is OrchestrationStatus.INVALID_INPUT
    excluded = SupplementalStaffData("synthetic", unavailability=(StaffUnavailability(sid(13), at(8), at(9)),))
    result = run(data=academic(status={13: "INACTIVE"}), supplemental=excluded)
    assert result.status is OrchestrationStatus.COMPLETED
    assert any("ignored: that staff member was excluded at import" in d for d in result.diagnostics)


# --- 9-10: staff import and staffing policy ---------------------------------------------------------------

def test_staff_import_errors_are_not_skipped_silently():
    data = academic(status={12: "ON_LEAVE"})
    blocked = run(data=data)
    assert blocked.status is OrchestrationStatus.INVALID_INPUT
    assert STAFF_RECORD in {v.constraint for v in blocked.violations}
    allowed = run(data=data, allow_staff_import_errors=True)
    assert allowed.status is OrchestrationStatus.COMPLETED
    assert any("could not be read and were left out (allowed explicitly)" in d for d in allowed.diagnostics)


def test_inactive_staff_are_excluded_and_reported():
    result = run(data=academic(status={1: "INACTIVE"}))
    assert sid(1) not in {a.staff_id for a in result.roster}
    assert any("EMP001 excluded: employment status is INACTIVE" in d for d in result.diagnostics)


def test_rooms_not_covered_by_staffing_policy_are_invalid_not_guessed():
    odd = draft(exam("X1", at(9), at(11), room("A501", 100)))
    result = run(odd)
    assert result.status is OrchestrationStatus.INVALID_INPUT
    assert [v.constraint for v in result.violations] == [UNCOVERED_ROOM, "D_INPUT"]  # specific + summary
    assert "no requirement was guessed" in result.violations[-1].message


@pytest.mark.parametrize("bad", [
    lambda: plan_duty_allocation(STANDARD, academic(), AllocationPolicies({"60": "1+1"}, ELIGIBILITY,
                                                                          DutyLimitPolicy(2, None, "s"))),
    lambda: plan_duty_allocation("draft", academic(), policies()),
    lambda: plan_duty_allocation(STANDARD, {"staff": []}, policies()),
    lambda: plan_duty_allocation(STANDARD, academic(), policies(fairness=None), mode=AllocationMode.FAIR),
    lambda: plan_duty_allocation(STANDARD, academic(), policies(), mode="FAIR"),
    lambda: plan_duty_allocation(STANDARD, academic(["Rank B"], status={1: "INACTIVE"}), policies()),
])
def test_invalid_inputs(bad):
    assert bad().status is OrchestrationStatus.INVALID_INPUT


def test_baseline_mode_works_without_a_fairness_policy():
    result = run(mode=AllocationMode.BASELINE, pol=policies(fairness=None))
    assert result.status is OrchestrationStatus.COMPLETED and result.baseline_metrics is None


# --- 13: immutability and determinism ------------------------------------------------------------------------

def test_inputs_are_not_changed_and_results_are_deterministic():
    data = academic()
    before = (repr(STANDARD), repr(data))
    first, second = run(data=data, mode=AllocationMode.COMPARE), run(data=data, mode=AllocationMode.COMPARE)
    assert (repr(STANDARD), repr(data)) == before
    assert first.roster == second.roster and first.fair_metrics == second.fair_metrics


# --- 14: realistic end-to-end -------------------------------------------------------------------------------

def mock_academic_service(staff_rows, workloads):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        path = request.url.path.removeprefix("/api/v1/academic")
        if path == "/staff":
            page, size = int(request.url.params["page"]), int(request.url.params["page_size"])
            return httpx.Response(200, json={"items": staff_rows[(page - 1) * size: page * size],
                                             "total": len(staff_rows), "page": page, "page_size": size})
        staff_id = path.split("/")[2]
        if staff_id in workloads:
            return httpx.Response(200, json=workloads[staff_id])
        return httpx.Response(404, json={"error": {"code": "NOT_FOUND", "message": "x"}})
    return AcademicDataClient("http://academic.test", http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
                              page_size=5)


def workload(n, hours, status="COMPUTED"):
    return {"staff_id": sid(n), "institution_id": str(INST), "as_of": "2026-11-01", "appointments": [],
            "aggregate": {"status": status, "rule": "SUM", "total_admin_hours_per_week": hours, "reason": None}}


async def test_end_to_end_with_mocked_academic_data_service():
    staff_rows = records(RANKS, {13: "INACTIVE"})
    workloads = {sid(n): workload(n, 4.0 * (n % 3)) for n in range(1, 12)}  # staff 12 has none (404)
    admin_policy = DutyFairnessPolicy("synthetic", LoadUnit.MINUTES, 100, 100, 100, 600, MissingAdminHours.ZERO, 0,
                                      FairnessWeights(1, 1, 0, 0))
    client = mock_academic_service(staff_rows, workloads)
    result = await plan_duty_allocation_from_academic(
        client, TOKEN, institution_id=INST, draft=STANDARD, policies=policies(fairness=admin_policy),
        supplemental=SupplementalStaffData("synthetic leave register", unavailability=()), as_of=dt.date(2026, 11, 1))
    assert result.status is OrchestrationStatus.COMPLETED
    assert dict(result.revalidation) == {"BASELINE": (), "FAIR": ()}
    assert any("EMP013 excluded" in d for d in result.diagnostics)
    assert any(f"No workload record for staff {sid(12)}" in d for d in result.diagnostics)
    assert {d.name: d.status for d in result.data_availability}["unavailability"] is DataStatus.PROVIDED_EMPTY
    assert result.fair_metrics.gini <= result.baseline_metrics.gini


async def test_member2_scheduler_draft_rooms_are_reported_against_confirmed_staffing_examples():
    """A real Member 2 offline draft: its room sizes are not 60 or 150, so the confirmed
    examples cover none of them and nothing is guessed. A synthetic banded policy then
    shows the same draft allocates end to end."""
    from app.scheduling import SchedulingPolicy, generate_timetable, load_snapshot
    from app.seating.memory import MemorySeatingStore
    from app.seed.assumptions import DEFAULT_ASSUMPTIONS
    from app.seed.generator import generate_dataset
    from app.sources import DatasetSource

    snapshot = await load_snapshot(DatasetSource(generate_dataset()), MemorySeatingStore(),
                                   period_code=DEFAULT_ASSUMPTIONS.period_code, policy=SchedulingPolicy(),
                                   module_codes=("IT1010", "IT1100"))
    member2 = generate_timetable(snapshot, SolverSettings(time_limit_seconds=30)).timetable
    before = repr(member2)
    staff = academic(["Rank A"] * 20 + ["Rank B"] * 20)
    confirmed = plan_duty_allocation(member2, staff, policies())
    assert confirmed.status is OrchestrationStatus.INVALID_INPUT
    assert {v.constraint for v in confirmed.violations} == {UNCOVERED_ROOM, "D_INPUT"}
    assert sum(v.constraint == UNCOVERED_ROOM for v in confirmed.violations) == sum(len(s.rooms) for s in member2.sessions)
    synthetic_bands = StaffingPolicy((StaffingRule(1, 60, 1, 1), StaffingRule(61, 150, 2, 2)),
                                     "SYNTHETIC test bands, not SLIIT policy")
    banded = plan_duty_allocation(member2, staff, policies(staffing=synthetic_bands), mode=AllocationMode.BASELINE)
    assert banded.status is OrchestrationStatus.COMPLETED
    assert {(a.session_id, a.venue_id) for a in banded.roster} == {(s.session_id, r.venue_id)
                                                                   for s in member2.sessions for r in s.rooms}
    assert repr(member2) == before


# --- Workload fetching and data provenance -----------------------------------------------------------

ADMIN_WEIGHTED = DutyFairnessPolicy("synthetic", LoadUnit.DUTIES, 100, 100, 100, 10, MissingAdminHours.REJECT, 0,
                                    FairnessWeights(1, 0, 0, 0))


def counting_service():
    staff_rows = records(RANKS)
    workloads = {sid(n): workload(n, 2.0) for n in range(1, 14)}
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        path = request.url.path.removeprefix("/api/v1/academic")
        if path == "/staff":
            page, size = int(request.url.params["page"]), int(request.url.params["page_size"])
            return httpx.Response(200, json={"items": staff_rows[(page - 1) * size: page * size],
                                             "total": len(staff_rows), "page": page, "page_size": size})
        return httpx.Response(200, json=workloads[path.split("/")[2]])
    client = AcademicDataClient("http://academic.test", http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    return client, calls


async def test_baseline_from_academic_does_not_fetch_workload():
    client, calls = counting_service()
    result = await plan_duty_allocation_from_academic(client, TOKEN, institution_id=INST, draft=STANDARD,
                                                      policies=policies(fairness=ADMIN_WEIGHTED),
                                                      mode=AllocationMode.BASELINE)
    assert result.status is OrchestrationStatus.COMPLETED
    assert not any(p.endswith("/administrative-workload") for p in calls)
    admin = {d.name: d for d in result.data_availability}["admin workload"]
    assert admin.status is DataStatus.NOT_AVAILABLE and admin.source == "not loaded" and admin.origin is DataOrigin.NONE
    assert result.baseline_metrics is None and any("UNAVAILABLE" in d for d in result.diagnostics)


async def test_fairness_modes_from_academic_do_fetch_workload():
    client, calls = counting_service()
    result = await plan_duty_allocation_from_academic(client, TOKEN, institution_id=INST, draft=STANDARD,
                                                      policies=policies(fairness=ADMIN_WEIGHTED),
                                                      mode=AllocationMode.FAIR)
    assert result.status is OrchestrationStatus.COMPLETED
    assert sum(p.endswith("/administrative-workload") for p in calls) == 13
    admin = {d.name: d for d in result.data_availability}["admin workload"]
    assert admin.status is DataStatus.PROVIDED and admin.origin is DataOrigin.ACADEMIC_DATA_CLIENT


async def test_client_loaded_data_is_labelled_as_academic_data_service():
    client, _ = counting_service()
    result = await plan_duty_allocation_from_academic(client, TOKEN, institution_id=INST, draft=STANDARD,
                                                      policies=policies(), mode=AllocationMode.BASELINE)
    staff = {d.name: d for d in result.data_availability}["staff"]
    assert staff.origin is DataOrigin.ACADEMIC_DATA_CLIENT and staff.source == "Academic Data Service (loaded via client)"
    assert "Staff data source: Academic Data Service (loaded via client)." in result.diagnostics


def test_preloaded_data_is_not_labelled_as_live():
    result = run(data=academic(admin=[AdminWorkload(sid(1), 3.0)]))
    items = {d.name: d for d in result.data_availability}
    assert items["staff"].origin is DataOrigin.PRELOADED and items["admin workload"].origin is DataOrigin.PRELOADED
    assert "Academic Data Service" not in items["staff"].source and "not verified as live" in items["staff"].source
    assert any(d.startswith("Staff data source: preloaded by caller") for d in result.diagnostics)


def test_supplemental_and_missing_data_have_their_own_origin():
    result = run(supplemental=SupplementalStaffData("synthetic leave register", unavailability=()))
    items = {d.name: d for d in result.data_availability}
    assert items["unavailability"].origin is DataOrigin.SUPPLEMENTAL
    assert items["unavailability"].source == "supplemental: synthetic leave register"
    assert items["duty history"].origin is DataOrigin.NONE and items["duty history"].source == "no source"


def test_loaded_but_empty_workload_is_distinguished_from_not_loaded():
    loaded = run(admin_loaded=True)
    assert {d.name: d for d in loaded.data_availability}["admin workload"].source.endswith("no workload records")
    skipped = run(admin_loaded=False)
    assert {d.name: d for d in skipped.data_availability}["admin workload"].source == "not loaded"


def test_invalid_staff_origin_is_rejected():
    assert run(staff_origin=DataOrigin.SUPPLEMENTAL).status is OrchestrationStatus.INVALID_INPUT
    assert run(staff_origin="live").status is OrchestrationStatus.INVALID_INPUT
