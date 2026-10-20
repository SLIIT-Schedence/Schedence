"""Member 3 API: fairness-aware examination duty allocation and student seating (PREVIEW ONLY).

Reads the SYNTHETIC ``m3_demo`` data from one explicit source (LOCAL_DEMO seed pack or
NEON_READ_ONLY) and runs the existing algorithms. Nothing is saved, approved, published or
applied; previews live in a bounded in-memory store. GET routes need ``examinations:read``,
POST routes (which compute) need ``examinations:generate``.
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
from fastapi.responses import JSONResponse

from app.api import duty_schemas as s
from app.api import duty_views as views
from app.api.deps import DEFAULT_PAGE_SIZE, PERIOD_CODE, Limit, Offset
from app.auth.dependencies import GENERATE, require_any_permission, require_exam_data_access
from app.duty_allocation.demo_integration import requirement_mismatches
from app.duty_allocation.demo_service import DemoServiceError, Member3DemoService
from app.duty_allocation.orchestration import AllocationMode
from app.duty_allocation.types import DutyAssignment, DutyRole

READ = [Depends(require_exam_data_access)]
GENERATE_ONLY = [Depends(require_any_permission(GENERATE))]
router = APIRouter(prefix="/duty-allocation", tags=["duty allocation (member 3 preview)"])

PeriodPath = Annotated[str, Path(pattern=PERIOD_CODE)]
PeriodQuery = Annotated[str | None, Query(pattern=PERIOD_CODE, description="Defaults to M3_DEMO_PERIOD_CODE.")]
RunId = Annotated[str, Path(pattern=r"^[0-9a-f]{32}$", description="Preview id returned when it was generated.")]
RosterQuery = Annotated[Literal["BASELINE", "FAIR"] | None, Query(description="Default: FAIR if present.")]
NOT_FOUND = {404: {"description": "Not found"}}
SOURCE_ERRORS = {403: {"description": "Source forbidden or refused"},
                 503: {"description": "Source disabled, not configured or unavailable"}}


async def demo_service_error_handler(request: Request, exc: DemoServiceError) -> JSONResponse:
    body = {"detail": exc.message, "code": exc.code}
    if exc.problems:
        body["problems"] = list(exc.problems[:50])
    return JSONResponse(body, status_code=exc.status_code)


def get_demo_service(request: Request) -> Member3DemoService:
    """The service created with the app (it holds the snapshot cache and the preview store)."""
    return request.app.state.m3_demo_service


ServiceDep = Annotated[Member3DemoService, Depends(get_demo_service)]


def _ref(ref: s.AssignmentRef) -> DutyAssignment:
    return DutyAssignment(ref.session_id, ref.venue_id, str(ref.staff_id), ref.role)


# --- Source and data ----------------------------------------------------------------------------------

@router.get("/status", response_model=s.DemoStatusOut, dependencies=READ)
async def source_status(service: ServiceDep) -> s.DemoStatusOut:
    """Which data source is active and what is still missing. Never connects to a database."""
    info = service.status()
    problem = info.pop("problem")
    return s.DemoStatusOut(**info, problem_code=problem[1] if problem else None, problem=problem[2] if problem else None)


@router.post("/snapshot/refresh", response_model=s.DemoStatusOut, dependencies=GENERATE_ONLY, responses=SOURCE_ERRORS)
async def refresh_snapshot(service: ServiceDep) -> s.DemoStatusOut:
    """Re-read the (read-only) source now instead of using the cached snapshot."""
    await service.refresh()
    return await source_status(service)


@router.get("/exam-periods", response_model=s.PeriodsOut, dependencies=READ, responses=SOURCE_ERRORS)
async def list_periods(service: ServiceDep) -> s.PeriodsOut:
    """Examination periods in the synthetic data, with their session counts."""
    snapshot = await service.snapshot()
    return s.PeriodsOut(items=views.periods(snapshot.records),
                        meta=views.snapshot_meta(snapshot, service.settings.institution_id))


@router.get("/exam-periods/{period_code}/sessions", response_model=s.SessionsOut, dependencies=READ,
            responses={**SOURCE_ERRORS, **NOT_FOUND})
async def list_sessions(service: ServiceDep, period_code: PeriodPath) -> s.SessionsOut:
    """Sessions of a period with date, time, rooms and student counts (Member 2's timetable shape)."""
    context = await service.context(period_code)
    items = views.sessions(context)
    return s.SessionsOut(items=items, total=len(items), meta=views.meta(context))


@router.get("/exam-periods/{period_code}/requirements", response_model=s.RequirementsOut, dependencies=READ,
            responses={**SOURCE_ERRORS, **NOT_FOUND})
async def duty_requirements(service: ServiceDep, period_code: PeriodPath) -> s.RequirementsOut:
    """Supervisors and invigilators required per room, calculated from the staffing policy.
    Stored example requirements that differ are reported, never used."""
    context = await service.context(period_code)
    return views.requirements(context, requirement_mismatches(context.snapshot.records, context.inputs))


# --- Staff --------------------------------------------------------------------------------------------

@router.get("/staff", response_model=s.DemoPage[s.StaffOut], dependencies=READ, responses=SOURCE_ERRORS)
async def list_staff(service: ServiceDep, period_code: PeriodQuery = None,
                     eligible_role: Annotated[DutyRole | None, Query()] = None,
                     include_excluded: Annotated[bool, Query(description="Also list staff left out, with the reason.")] = False,
                     limit: Limit = DEFAULT_PAGE_SIZE, offset: Offset = 0) -> s.DemoPage[s.StaffOut]:
    """Active staff available for duties, sorted by staff number."""
    context = await service.context(period_code)
    items = views.staff_list(context, include_excluded=include_excluded,
                             role=None if eligible_role is None else eligible_role.value)
    return s.DemoPage[s.StaffOut](items=items[offset:offset + limit], total=len(items), limit=limit, offset=offset,
                                  meta=views.meta(context))


@router.get("/staff/workload", response_model=s.WorkloadPageOut, dependencies=READ, responses=SOURCE_ERRORS)
async def list_workloads(service: ServiceDep, period_code: PeriodQuery = None,
                         status_filter: Annotated[Literal["COMPUTED", "NOT_COMPUTED", "MISSING_RESPONSE", "NOT_LOADED"] | None,
                                                  Query(alias="status")] = None,
                         limit: Limit = DEFAULT_PAGE_SIZE, offset: Offset = 0) -> s.WorkloadPageOut:
    """Administrative workload per staff member. COMPUTED, NOT_COMPUTED and MISSING_RESPONSE are never merged."""
    context = await service.context(period_code)
    everything = views.workloads(context)
    counts: dict[str, int] = {}
    for w in everything:
        counts[w.status] = counts.get(w.status, 0) + 1
    items = [w for w in everything if status_filter is None or w.status == status_filter]
    return s.WorkloadPageOut(items=items[offset:offset + limit], total=len(items), limit=limit, offset=offset,
                             meta=views.meta(context), counts=counts)


def _staff_or_404(context, staff_id: UUID):
    found = next((x for x in views.staff_list(context, include_excluded=True, role=None)
                  if x.staff_id == str(staff_id)), None)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Staff {staff_id} not found")
    return found


@router.get("/staff/{staff_id}", response_model=s.StaffDetailOut, dependencies=READ,
            responses={**SOURCE_ERRORS, **NOT_FOUND})
async def get_staff(service: ServiceDep, staff_id: UUID, period_code: PeriodQuery = None) -> s.StaffDetailOut:
    """One staff member with administrative workload, availability, unavailability, preferences, history and limits."""
    context = await service.context(period_code)
    found = _staff_or_404(context, staff_id)
    workload = next((w for w in views.workloads(context) if w.staff_id == found.staff_id), None)
    return s.StaffDetailOut(**found.model_dump(), workload=workload,
                            constraints=views.constraints(context, found.staff_id) if found.eligible else None,
                            meta=views.meta(context))


@router.get("/staff/{staff_id}/constraints", response_model=s.ConstraintsResponse, dependencies=READ,
            responses={**SOURCE_ERRORS, **NOT_FOUND})
async def get_staff_constraints(service: ServiceDep, staff_id: UUID,
                                period_code: PeriodQuery = None) -> s.ConstraintsResponse:
    """Availability, unavailability, day preferences, module restrictions, duty history and duty limits."""
    context = await service.context(period_code)
    found = _staff_or_404(context, staff_id)
    if not found.eligible:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Staff {staff_id} is excluded from duties: {found.exclusion_reason}")
    return s.ConstraintsResponse(**views.constraints(context, found.staff_id).model_dump(), meta=views.meta(context))


# --- Allocation ---------------------------------------------------------------------------------------

async def _allocate(service: Member3DemoService, mode: AllocationMode, body: s.AllocationRequest) -> s.AllocationRunOut:
    run = await service.allocate(mode, body.period_code, body.time_limit_seconds)
    return views.allocation_run(run)


_ALLOCATE = dict(response_model=s.AllocationRunOut, dependencies=GENERATE_ONLY, status_code=status.HTTP_201_CREATED,
                 responses={**SOURCE_ERRORS, 422: {"description": "Invalid request or invalid m3_demo data"}})


@router.post("/allocations/baseline", **_ALLOCATE)
async def allocate_baseline(service: ServiceDep, body: s.AllocationRequest = s.AllocationRequest()) -> s.AllocationRunOut:
    """BASELINE preview: CP-SAT with hard constraints only. Not saved, approved or published."""
    return await _allocate(service, AllocationMode.BASELINE, body)


@router.post("/allocations/fair", **_ALLOCATE)
async def allocate_fair(service: ServiceDep, body: s.AllocationRequest = s.AllocationRequest()) -> s.AllocationRunOut:
    """FAIR preview: workload-, history- and preference-aware CP-SAT. Not saved, approved or published."""
    return await _allocate(service, AllocationMode.FAIR, body)


@router.post("/allocations/compare", **_ALLOCATE)
async def allocate_compare(service: ServiceDep, body: s.AllocationRequest = s.AllocationRequest()) -> s.AllocationRunOut:
    """Both rosters, measured with the same fairness metrics, plus a comparison."""
    return await _allocate(service, AllocationMode.COMPARE, body)


@router.get("/allocations/{run_id}", response_model=s.AllocationRunOut, dependencies=READ, responses=NOT_FOUND)
async def get_allocation(service: ServiceDep, run_id: RunId) -> s.AllocationRunOut:
    """A generated preview, as returned when it was created (in-memory; may have expired)."""
    return views.allocation_run(service.allocation(run_id))


@router.get("/allocations/{run_id}/roster", response_model=s.RosterResponse, dependencies=READ,
            responses={**NOT_FOUND, 409: {"description": "The run has no such roster"}})
async def get_roster(service: ServiceDep, run_id: RunId, roster: RosterQuery = None) -> s.RosterResponse:
    """The draft duty roster: staff, role, venue and time per duty."""
    run = service.allocation(run_id)
    wanted = roster or ("FAIR" if run.orchestration.fair is not None else "BASELINE")
    found = next((r for r in views.rosters(run, views.Lookup(run.context)) if r.label == wanted), None)
    if found is None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"This run has no {wanted} roster.")
    return s.RosterResponse(run_id=run.run_id, status=run.orchestration.status.value, roster=found,
                            meta=views.meta(run.context))


@router.get("/allocations/{run_id}/fairness", response_model=s.FairnessOut, dependencies=READ, responses=NOT_FOUND)
async def get_fairness(service: ServiceDep, run_id: RunId) -> s.FairnessOut:
    """Fairness metrics per roster and the BASELINE/FAIR comparison, with what the fairness scope covers."""
    return views.fairness(service.allocation(run_id))


@router.post("/allocations/{run_id}/validation", response_model=s.ValidationOut, dependencies=GENERATE_ONLY,
             responses={**NOT_FOUND, 409: {"description": "The run has no such roster"}})
async def validate_roster(service: ServiceDep, run_id: RunId,
                          body: s.ValidationRequest = s.ValidationRequest()) -> s.ValidationOut:
    """Independent validation (staffing, overlaps, minimum breaks, availability, rank, restrictions, duty limits)
    of the run's roster, or of a submitted roster against the run's data."""
    submitted = None if body.assignments is None else tuple(_ref(a) for a in body.assignments)
    run, label, checked, found = service.validate(run_id, body.roster, submitted)
    return s.ValidationOut(run_id=run.run_id, roster=label, source="RUN_ROSTER" if submitted is None else "SUBMITTED_ROSTER",
                           valid=not found, assignments_checked=len(checked), violations=views.violations(found),
                           meta=views.meta(run.context))


_CHANGE = dict(response_model=s.ChangePreviewOut, dependencies=GENERATE_ONLY,
               responses={**NOT_FOUND, 409: {"description": "The run has no such roster"}})


@router.post("/allocations/{run_id}/swap-preview", **_CHANGE)
async def preview_swap(service: ServiceDep, run_id: RunId, body: s.SwapPreviewRequest) -> s.ChangePreviewOut:
    """Preview two staff members exchanging their duties. Re-validated independently; never applied or saved."""
    run, label, _, proposal, rechecked = service.preview_swap(run_id, body.roster, _ref(body.first), _ref(body.second))
    return views.change_preview(run, label, proposal, rechecked)


@router.post("/allocations/{run_id}/replacement-preview", **_CHANGE)
async def preview_replacement(service: ServiceDep, run_id: RunId, body: s.ReplacementPreviewRequest) -> s.ChangePreviewOut:
    """Preview emergency replacements for one duty: valid candidates ranked by fairness, rejected ones with reasons.
    Never applied or saved."""
    window = None if body.unavailable_from is None else (body.unavailable_from, body.unavailable_until)
    preferred = None if body.preferred_staff_id is None else str(body.preferred_staff_id)
    run, label, _, proposal, rechecked = service.preview_replacement(
        run_id, body.roster, _ref(body.duty), body.reason, unavailable=window, preferred_staff_id=preferred)
    return views.change_preview(run, label, proposal, rechecked)


# --- Student seating ----------------------------------------------------------------------------------

@router.post("/seating", response_model=s.SeatingRunOut, dependencies=GENERATE_ONLY, status_code=status.HTTP_201_CREATED,
             responses=SOURCE_ERRORS)
async def generate_seating(service: ServiceDep, body: s.SeatingRequest = s.SeatingRequest()) -> s.SeatingRunOut:
    """Seat students inside Member 2's rooms (IT-number order; lab 90 %, lecture hall 50 %).
    Synthetic halls keep every seat PROVISIONAL/SYNTHETIC; nothing is verified against real rooms."""
    return views.seating_run(await service.seat(body.period_code))


@router.get("/seating/{seating_id}", response_model=s.SeatingRunOut, dependencies=READ, responses=NOT_FOUND)
async def get_seating(service: ServiceDep, seating_id: RunId) -> s.SeatingRunOut:
    """Provisional seating plan summary with room capacities."""
    return views.seating_run(service.seating(seating_id))


@router.get("/seating/{seating_id}/rooms/{session_id}/{venue_id}", response_model=s.RoomSeatsOut, dependencies=READ,
            responses=NOT_FOUND)
async def get_room_seats(service: ServiceDep, seating_id: RunId, session_id: Annotated[str, Path(max_length=64)],
                         venue_id: Annotated[str, Path(max_length=64)]) -> s.RoomSeatsOut:
    """Room-wise seat allocation of one session."""
    found = views.room_seats(service.seating(seating_id), session_id, venue_id)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No room {venue_id} in session {session_id}")
    return found
