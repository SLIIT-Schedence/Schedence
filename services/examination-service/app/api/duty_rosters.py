"""Saved duty rosters, approval workflow, swap / replacement requests and seating drafts.

Fairness-Aware Staff Allocation for Examination Duties. Mounted with the preview API; every
route answers 503 until ``M3_DEMO_PERSISTENCE`` is MEMORY (development) or DATABASE.

Permissions (checked by the Identity Service contract, never from the request body):

====================================  ==========================================================
examinations:read                     every GET
examinations:generate                 save a draft, validate, preview / submit / cancel a change
examinations:approve                  approve, publish, approve / reject / apply a change
examinations:approve or :manage       reject a roster
examinations:manage                   create a revision of a published or rejected roster
====================================  ==========================================================

A synthetic (m3_demo) roster can never be published, and publishing fails closed while the
published-timetable contract is unavailable.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Request, Response, status

from app.api import duty_roster_schemas as s
from app.api.deps import DEFAULT_PAGE_SIZE, PERIOD_CODE, Limit, Offset
from app.auth.dependencies import (
    APPROVE, GENERATE, MANAGE, ExamAccess, current_institution, require_any_permission, require_exam_data_access,
)
from app.duty_allocation.demo_service import DemoServiceError, Member3DemoService
from app.duty_allocation.demo_settings import PersistenceMode
from app.duty_allocation.persistence.context import DemoRosterContextResolver, violations_json
from app.duty_allocation.persistence.domain import (
    AssignmentRecord, ChangeKind, ChangeRequestRecord, ChangeStatus, RosterRecord, RosterStatus,
    SeatingDraftRecord,
)
from app.duty_allocation.persistence.repository import MemoryDutyRosterRepository
from app.duty_allocation.persistence.service import ChangeSpec, DutyRosterService, metrics_json, to_records
from app.duty_allocation.persistence.sql_repository import SqlDutyRosterRepository
from app.duty_allocation.persistence.published_timetable import SavedPublishedTimetableProvider
from app.duty_allocation.roster_exports import build_xlsx, export_filename, export_warning, XLSX_TYPE
from app.saved_timetables.store import DatabaseTimetableStore
from app.duty_allocation.types import DutyAssignment
from schedence_db import session_scope

AsRead = Annotated[ExamAccess, Depends(require_exam_data_access)]
AsGenerate = Annotated[ExamAccess, Depends(require_any_permission(GENERATE))]
AsApprove = Annotated[ExamAccess, Depends(require_any_permission(APPROVE))]
AsManage = Annotated[ExamAccess, Depends(require_any_permission(MANAGE))]
AsRejecter = Annotated[ExamAccess, Depends(require_any_permission(APPROVE, MANAGE))]

TAG = "Fairness-Aware Staff Allocation for Examination Duties (saved drafts and approval)"
router = APIRouter(prefix="/duty-allocation", tags=[TAG])
RosterId = Annotated[UUID, Path(description="Roster id")]
RequestId = Annotated[UUID, Path(description="Change request id")]
GATE = {503: {"description": "Saving is disabled / not configured, or the table migration is not applied"}}
CONFLICT = {409: {"description": "Conflict: stale version, invalid transition, or a rule refused the action"}}
NOT_FOUND = {404: {"description": "Not found"}}


@dataclass
class RosterEnv:
    service: DutyRosterService
    mode: PersistenceMode
    demo: Member3DemoService
    institution_id: UUID

    @property
    def info(self) -> s.PersistenceInfo:
        durable = self.mode is PersistenceMode.DATABASE
        return s.PersistenceInfo(
            mode=self.mode, durable=durable,
            notice=("Saved in the database as a working draft record; not published." if durable else
                    "MEMORY mode: working drafts are kept in this process only and are lost on restart."))


@asynccontextmanager
async def _read_saved_timetables(request: Request):
    # Existing read-only application session; the timetable owner is never modified.
    async with session_scope(request.app.state.session_factory, commit=False) as session:
        yield DatabaseTimetableStore(session)


async def get_env(request: Request) -> AsyncIterator[RosterEnv]:
    demo: Member3DemoService = request.app.state.m3_demo_service
    settings = demo.settings
    # Rosters belong to the institution the route's permission check resolved (it always runs first).
    institution_id = current_institution(request)
    demo.check_caller_institution(institution_id)
    problem = settings.persistence_problem()
    if problem is not None:
        code = "M3_PERSISTENCE_DISABLED" if settings.persistence is PersistenceMode.DISABLED else "M3_PERSISTENCE_NOT_CONFIGURED"
        raise DemoServiceError(503, code, problem)
    provider = getattr(request.app.state, "m3_published_timetable_provider", None)
    if provider is None and settings.persistence is PersistenceMode.DATABASE:
        provider = SavedPublishedTimetableProvider(lambda: _read_saved_timetables(request))
    resolver = DemoRosterContextResolver(demo)
    if settings.persistence is PersistenceMode.MEMORY:
        repo = MemoryDutyRosterRepository(request.app.state.m3_memory_state)
        yield RosterEnv(DutyRosterService(repo, resolver, institution_id, published=provider),
                        settings.persistence, demo, institution_id)
        return
    async with session_scope(request.app.state.write_session_factory, commit=False) as session:
        yield RosterEnv(DutyRosterService(SqlDutyRosterRepository(session), resolver, institution_id,
                                          published=provider), settings.persistence, demo, institution_id)


Env = Annotated[RosterEnv, Depends(get_env)]


# --- Response builders ----------------------------------------------------------------------------------

_ACTIONS = {RosterStatus.DRAFT: ("VALIDATE", "REJECT"), RosterStatus.VALIDATED: ("VALIDATE", "APPROVE", "REJECT"),
            RosterStatus.APPROVED: ("PUBLISH", "REJECT"), RosterStatus.PUBLISHED: ("REVISE",),
            RosterStatus.REJECTED: ("REVISE",), RosterStatus.SUPERSEDED: ()}


def _assignment(r: AssignmentRecord) -> s.RosterAssignmentOut:
    return s.RosterAssignmentOut(session_id=r.session_id, venue_id=r.venue_id, staff_id=r.staff_id, staff_number=r.staff_number,
                                 role=r.role, module_code=r.module_code, starts_at=r.starts_at, ends_at=r.ends_at)


def _roster_notice(r: RosterRecord) -> str:
    text = {RosterStatus.PUBLISHED: "PUBLISHED roster.", RosterStatus.REJECTED: "REJECTED: create a revision to continue.",
            RosterStatus.SUPERSEDED: "SUPERSEDED by a newer published revision."}.get(
        r.status, f"{r.status.value}: working draft, not published.")
    return text + (" Built on SYNTHETIC demo data: it can never be published." if r.synthetic else "")


def roster_out(env: RosterEnv, r: RosterRecord, assignments: list[AssignmentRecord] | None = None, count: int | None = None):
    actions = [a for a in _ACTIONS[r.status] if not (a == "PUBLISH" and r.synthetic)]
    fields = {f: getattr(r, f) for f in s.RosterOut.model_fields if hasattr(r, f)}
    fields.update(status=r.status.value, assignment_count=len(assignments) if assignments is not None else (count or 0),
                  next_actions=actions, can_be_published=not r.synthetic, notice=_roster_notice(r), persistence=env.info)
    if assignments is None:
        return s.RosterOut(**fields)
    return s.RosterDetailOut(**fields, assignments=[_assignment(a) for a in assignments])


def request_out(env: RosterEnv, r: ChangeRequestRecord) -> s.ChangeRequestOut:
    actions = {ChangeStatus.REQUESTED: ["APPROVE", "REJECT", "CANCEL"], ChangeStatus.APPROVED: ["APPLY", "CANCEL"]}.get(r.status, [])
    notice = {ChangeStatus.APPLIED: "APPLIED to the working roster (approval, if any, was withdrawn).",
              ChangeStatus.STALE: "STALE: the roster changed after this request; submit it again."}.get(
        r.status, f"{r.status.value}: not applied to any roster yet.")
    return s.ChangeRequestOut(
        request_id=r.request_id, roster_id=r.roster_id, kind=r.kind.value, status=r.status.value,
        base_row_version=r.base_row_version, removed=[_assignment(a) for a in r.removed], added=[_assignment(a) for a in r.added],
        reason=r.reason, summary=r.summary, requested_by=r.requested_by, requested_at=r.requested_at, decided_by=r.decided_by,
        decided_at=r.decided_at, decision_reason=r.decision_reason, applied_by=r.applied_by, applied_at=r.applied_at,
        next_actions=actions, notice=notice, persistence=env.info)


def seating_out(env: RosterEnv, d: SeatingDraftRecord) -> s.SeatingDraftOut:
    return s.SeatingDraftOut(
        **{f: getattr(d, f) for f in s.SeatingDraftOut.model_fields if hasattr(d, f) and f != "status"}, status=d.status.value,
        notice=f"SEATING DRAFT ({d.seat_state}): not verified against real room layouts; not published.", persistence=env.info)


def _ref(ref) -> DutyAssignment:
    return DutyAssignment(ref.session_id, ref.venue_id, str(ref.staff_id), ref.role)


def _swap_spec(body: s.SwapChangeRequest) -> ChangeSpec:
    return ChangeSpec(ChangeKind.SWAP, _ref(body.first), _ref(body.second), body.reason)


def _replacement_spec(body: s.ReplacementChangeRequest) -> ChangeSpec:
    window = None if body.unavailable_from is None else (body.unavailable_from, body.unavailable_until)
    preferred = None if body.preferred_staff_id is None else str(body.preferred_staff_id)
    return ChangeSpec(ChangeKind.REPLACEMENT, _ref(body.duty), None, body.reason, window, preferred)


async def _preview(env: RosterEnv, actor: ExamAccess, roster_id: UUID, spec: ChangeSpec) -> s.ChangePreviewOut:
    roster, proposal, rechecked, context = await env.service.preview_change(actor.user_id, roster_id, spec)
    names = context.staff
    return s.ChangePreviewOut(
        roster_id=roster.roster_id, roster_version=roster.row_version, kind=spec.kind.value, status=proposal.status.value,
        removed=[_assignment(a) for a in to_records(context, proposal.removed)],
        added=[_assignment(a) for a in to_records(context, proposal.added)],
        candidates=[s.CandidateOut(staff_id=c.staff_id, staff_number=c.staff_number,
                                   staff_name=getattr(names.get(c.staff_id), "full_name", None), duties_after=c.duties_after,
                                   objective=c.objective, metrics=metrics_json(context, c.metrics)) for c in proposal.candidates],
        rejected=[s.RejectedCandidateOut(staff_id=x.staff_id, staff_number=x.staff_number, reasons=list(x.reasons))
                  for x in proposal.rejected],
        violations=violations_json(list(proposal.violations)),
        independent_check_violations=None if rechecked is None else violations_json(list(rechecked)),
        reasons=list(proposal.reasons), original_metrics=metrics_json(context, proposal.original_metrics),
        proposed_metrics=metrics_json(context, proposal.proposed_metrics), persistence=env.info)


# --- Rosters --------------------------------------------------------------------------------------------

@router.post("/rosters", response_model=s.RosterDetailOut, status_code=status.HTTP_201_CREATED,
             responses={**GATE, **CONFLICT, **NOT_FOUND, 422: {"description": "Run not completed or roster invalid"}})
async def save_roster(actor: AsGenerate, env: Env, body: s.SaveRosterRequest) -> s.RosterDetailOut:
    """Save a generated roster as a DRAFT. It is validated first (an invalid roster is never stored); a period has at
    most one working roster; staff cannot overlap duties in other active rosters. Not approved or published."""
    run = env.demo.allocation(body.run_id)
    roster, records = await env.service.save_draft(actor.user_id, run, body.roster)
    return roster_out(env, roster, records)


@router.get("/rosters", response_model=s.Page[s.RosterOut], dependencies=[Depends(require_exam_data_access)], responses=GATE)
async def list_rosters(env: Env, period_code: Annotated[str | None, Query(pattern=PERIOD_CODE)] = None,
                       status_filter: Annotated[RosterStatus | None, Query(alias="status")] = None,
                       limit: Limit = DEFAULT_PAGE_SIZE, offset: Offset = 0) -> s.Page[s.RosterOut]:
    """Saved rosters, newest version first within each period."""
    items, total = await env.service.list(period_code=period_code, status=status_filter, limit=limit, offset=offset)
    shown = [await _after(env, r) for r in items]
    return s.Page[s.RosterOut](items=shown, total=total, limit=limit, offset=offset,
                               persistence=env.info)


@router.get("/rosters/{roster_id}", response_model=s.RosterDetailOut, dependencies=[Depends(require_exam_data_access)],
            responses={**GATE, **NOT_FOUND})
async def get_roster(env: Env, roster_id: RosterId) -> s.RosterDetailOut:
    """A saved roster with its assignments, fairness metrics, last validation and the actions possible now."""
    roster, records = await env.service.get(roster_id)
    return roster_out(env, roster, records)


@router.get("/rosters/{roster_id}/publication", response_model=s.PublicationCheckOut,
            dependencies=[Depends(require_exam_data_access)], responses={**GATE, **NOT_FOUND})
async def publication_check(env: Env, roster_id: RosterId, response: Response) -> s.PublicationCheckOut:
    """Read-only publication blockers; publishing itself still requires examinations:approve."""
    response.headers["Cache-Control"] = "private, no-store"
    return s.PublicationCheckOut(**await env.service.publication_check(roster_id))


async def _export_data(env: RosterEnv, request: Request, roster_id: UUID, expected_version: int | None) -> s.RosterExportOut:
    roster, records = await env.service.get(roster_id)
    if expected_version is not None and expected_version != roster.row_version:
        raise DemoServiceError(409, "STALE_ROSTER", "The roster changed after this page loaded. Refresh it before downloading.")
    names = {p["staff_id"]: p.get("staff_name") for p in roster.fairness_metrics.get("per_staff", []) if p.get("staff_id")}
    modules, period_name, notes = {}, roster.period_code, []
    if roster.synthetic and roster.timetable_reference == f"DEMO-UNPUBLISHED:m3_demo/{roster.period_code}":
        snapshot = await env.demo.snapshot()
        modules = {m["module_code"]: m["module_name"] for m in snapshot.records.rows("modules")}
        period_name = next((p["period_name"] for p in snapshot.records.rows("exam_periods") if p["period_code"] == roster.period_code), roster.period_code)
        notes.append("Module names are current read-only source labels. Assignments and recorded staff names come from the saved roster.")
    elif roster.timetable_reference.startswith("EXAM-TIMETABLE:") and env.mode is PersistenceMode.DATABASE:
        raw_id = roster.timetable_reference.removeprefix("EXAM-TIMETABLE:")
        if raw_id.isdecimal() and 0 < int(raw_id) < 2**63:
            async with _read_saved_timetables(request) as store:
                timetable = await store.get(env.institution_id, int(raw_id))
                if timetable is not None and timetable.header["period_code"] == roster.period_code:
                    modules = {m["module_code"]: m["module_name"] for m in timetable.sessions}
                    period_name = timetable.header["period_name"]
    if any(not names.get(a.staff_id) or not modules.get(a.module_code) for a in records):
        notes.append("Not recorded means the saved staff name or source module label is unavailable; no assignment has been omitted.")
    ordered = sorted(records, key=lambda a: (a.starts_at, a.ends_at, a.module_code or "", a.venue_id, a.role, a.staff_id))
    return s.RosterExportOut(roster=roster_out(env, roster, count=len(records)), period_name=period_name,
        warning=export_warning(roster), notes=notes,
        assignments=[s.ExportAssignmentOut(**_assignment(a).model_dump(), staff_name=names.get(a.staff_id),
                                            module_name=modules.get(a.module_code)) for a in ordered])


@router.get("/rosters/{roster_id}/export-data", response_model=s.RosterExportOut,
            dependencies=[Depends(require_exam_data_access)], responses={**GATE, **CONFLICT, **NOT_FOUND})
async def roster_export_data(request: Request, env: Env, roster_id: RosterId, response: Response,
                             expected_version: Annotated[int | None, Query(ge=1)] = None) -> s.RosterExportOut:
    """Complete saved assignments for printable exports, independent of temporary allocation runs."""
    response.headers["Cache-Control"] = "private, no-store"
    return await _export_data(env, request, roster_id, expected_version)


@router.get("/rosters/{roster_id}/export.xlsx", dependencies=[Depends(require_exam_data_access)],
            responses={200: {"content": {XLSX_TYPE: {}}}, **GATE, **CONFLICT, **NOT_FOUND})
async def roster_excel(request: Request, env: Env, roster_id: RosterId,
                       expected_version: Annotated[int | None, Query(ge=1)] = None) -> Response:
    data = await _export_data(env, request, roster_id, expected_version)
    return Response(content=build_xlsx(data), media_type=XLSX_TYPE,
                    headers={"Content-Disposition": f'attachment; filename="{export_filename(data.roster, "xlsx")}"',
                             "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@router.get("/rosters/{roster_id}/history", response_model=s.HistoryOut, dependencies=[Depends(require_exam_data_access)],
            responses={**GATE, **NOT_FOUND})
async def roster_history(env: Env, roster_id: RosterId) -> s.HistoryOut:
    """Audit trail: who did what and when, including refusals."""
    events = await env.service.history(roster_id)
    return s.HistoryOut(roster_id=roster_id, persistence=env.info, events=[s.EventOut(
        event_id=e.event_id, action=e.action, from_status=e.from_status, to_status=e.to_status, actor_id=e.actor_id, at=e.at,
        reason=e.reason, details=e.details) for e in events])


async def _after(env: RosterEnv, roster: RosterRecord) -> s.RosterOut:
    return roster_out(env, roster, count=len(await env.service.repo.get_assignments(roster.roster_id)))


_TRANSITION = dict(response_model=s.RosterOut, responses={**GATE, **CONFLICT, **NOT_FOUND, 422: {"description": "Hard-constraint violations"}})


@router.post("/rosters/{roster_id}/validate", **_TRANSITION)
async def validate_saved_roster(actor: AsGenerate, env: Env, roster_id: RosterId, body: s.TransitionRequest = s.TransitionRequest()):
    """DRAFT -> VALIDATED. Rebuilds the inputs and re-runs the independent validation; violations (or changed inputs) block it."""
    return await _after(env, await env.service.validate(actor.user_id, roster_id, body.expected_version))


@router.post("/rosters/{roster_id}/approve", **_TRANSITION)
async def approve_roster(actor: AsApprove, env: Env, roster_id: RosterId, body: s.TransitionRequest = s.TransitionRequest()):
    """VALIDATED -> APPROVED. Validates again; a roster with any hard-constraint violation cannot be approved."""
    return await _after(env, await env.service.approve(actor.user_id, roster_id, body.expected_version, body.note))


@router.post("/rosters/{roster_id}/publish", **_TRANSITION)
async def publish_roster(actor: AsApprove, env: Env, roster_id: RosterId, body: s.TransitionRequest = s.TransitionRequest()):
    """APPROVED -> PUBLISHED. Needs a verified published timetable reference and a compatibility check against it.
    Synthetic demo rosters are refused; with no published-timetable contract this fails closed (503)."""
    return await _after(env, await env.service.publish(actor.user_id, roster_id, body.expected_version))


@router.post("/rosters/{roster_id}/reject", **_TRANSITION)
async def reject_roster(actor: AsRejecter, env: Env, roster_id: RosterId, body: s.ReasonRequest):
    """DRAFT / VALIDATED / APPROVED -> REJECTED, with a reason. Revise it to continue; a published roster is revised, not rejected."""
    return await _after(env, await env.service.reject(actor.user_id, roster_id, body.expected_version, body.reason))


@router.post("/rosters/{roster_id}/revisions", response_model=s.RosterDetailOut, status_code=status.HTTP_201_CREATED,
             responses={**GATE, **CONFLICT, **NOT_FOUND})
async def revise_roster(actor: AsManage, env: Env, roster_id: RosterId, body: s.ReasonRequest) -> s.RosterDetailOut:
    """Controlled revision: copy a PUBLISHED or REJECTED roster into a new DRAFT version (the published one stays in force
    until the revision is published). Only one working roster per period."""
    revision, records = await env.service.revise(actor.user_id, roster_id, body.expected_version, body.reason)
    return roster_out(env, revision, records)


# --- Swaps and emergency replacements -------------------------------------------------------------------

_PREVIEW = dict(response_model=s.ChangePreviewOut, responses={**GATE, **CONFLICT, **NOT_FOUND})


@router.post("/rosters/{roster_id}/change-previews/swap", **_PREVIEW)
async def preview_roster_swap(actor: AsGenerate, env: Env, roster_id: RosterId, body: s.SwapChangeRequest):
    """PREVIEW ONLY: validate a swap against the saved roster. Nothing is requested or changed."""
    return await _preview(env, actor, roster_id, _swap_spec(body))


@router.post("/rosters/{roster_id}/change-previews/replacement", **_PREVIEW)
async def preview_roster_replacement(actor: AsGenerate, env: Env, roster_id: RosterId, body: s.ReplacementChangeRequest):
    """PREVIEW ONLY: ranked emergency replacement candidates for one duty. Nothing is requested or changed."""
    return await _preview(env, actor, roster_id, _replacement_spec(body))


_REQUEST = dict(response_model=s.ChangeRequestOut, status_code=status.HTTP_201_CREATED,
                responses={**GATE, **CONFLICT, **NOT_FOUND})


@router.post("/rosters/{roster_id}/change-requests/swap", **_REQUEST)
async def request_swap(actor: AsGenerate, env: Env, roster_id: RosterId, body: s.SwapChangeRequest):
    """Submit a swap request (stored only when it validates). It changes nothing until approved AND applied."""
    spec = _swap_spec(body)
    return request_out(env, await env.service.submit_change(actor.user_id, roster_id, body.expected_version, spec))


@router.post("/rosters/{roster_id}/change-requests/replacement", **_REQUEST)
async def request_replacement(actor: AsGenerate, env: Env, roster_id: RosterId, body: s.ReplacementChangeRequest):
    """Submit an emergency replacement request (stored only when it validates). Nothing changes until approved AND applied."""
    spec = _replacement_spec(body)
    return request_out(env, await env.service.submit_change(actor.user_id, roster_id, body.expected_version, spec))


@router.get("/rosters/{roster_id}/change-requests", response_model=list[s.ChangeRequestOut],
            dependencies=[Depends(require_exam_data_access)], responses={**GATE, **NOT_FOUND})
async def list_change_requests(env: Env, roster_id: RosterId) -> list[s.ChangeRequestOut]:
    """Change requests of a roster, oldest first."""
    return [request_out(env, r) for r in await env.service.list_change_requests(roster_id)]


@router.get("/change-requests/{request_id}", response_model=s.ChangeRequestOut,
            dependencies=[Depends(require_exam_data_access)], responses={**GATE, **NOT_FOUND})
async def get_change_request(env: Env, request_id: RequestId) -> s.ChangeRequestOut:
    return request_out(env, await env.service.get_change_request(request_id))


_DECISION = dict(response_model=s.ChangeRequestOut, responses={**GATE, **CONFLICT, **NOT_FOUND})


@router.post("/change-requests/{request_id}/approve", **_DECISION)
async def approve_change(actor: AsApprove, env: Env, request_id: RequestId, body: s.DecisionRequest = s.DecisionRequest()):
    """REQUESTED -> APPROVED (refused as STALE if the roster changed since the request). Does not change the roster yet."""
    return request_out(env, await env.service.decide_change(actor.user_id, request_id, True, body.reason))


@router.post("/change-requests/{request_id}/reject", **_DECISION)
async def reject_change(actor: AsApprove, env: Env, request_id: RequestId, body: s.RejectDecisionRequest):
    """REQUESTED -> REJECTED, with a reason."""
    return request_out(env, await env.service.decide_change(actor.user_id, request_id, False, body.reason))


@router.post("/change-requests/{request_id}/cancel", **_DECISION)
async def cancel_change(actor: AsGenerate, env: Env, request_id: RequestId, body: s.DecisionRequest = s.DecisionRequest()):
    """Cancel an open request: the requester, or a user with examinations:manage."""
    manager = actor.principal.has_permission(MANAGE, actor.institution_id)
    return request_out(env, await env.service.cancel_change(actor.user_id, request_id, body.reason, may_cancel_any=manager))


@router.post("/change-requests/{request_id}/apply", response_model=s.ChangeAppliedOut, responses={**GATE, **CONFLICT, **NOT_FOUND})
async def apply_change(actor: AsApprove, env: Env, request_id: RequestId) -> s.ChangeAppliedOut:
    """Apply an APPROVED change atomically (roster version, inputs and result re-checked). Approval of the roster, if any,
    is withdrawn and it must be approved again. A published roster is never modified here (use a revision)."""
    request, roster = await env.service.apply_change(actor.user_id, request_id)
    return s.ChangeAppliedOut(request=request_out(env, request), roster=await _after(env, roster))


# --- Seating drafts -------------------------------------------------------------------------------------

@router.post("/seating/{seating_id}/save", response_model=s.SeatingDraftOut, status_code=status.HTTP_201_CREATED,
             responses={**GATE, **CONFLICT, **NOT_FOUND, 422: {"description": "Seating plan not complete"}})
async def save_seating(actor: AsGenerate, env: Env,
                       seating_id: Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")]) -> s.SeatingDraftOut:
    """Save a generated seating plan as a DRAFT (session, venue, student, logical seat, provisional physical seat, state).
    Seats are never marked VERIFIED without a verified hall layout; demo seats stay SYNTHETIC."""
    record, _ = await env.service.save_seating(actor.user_id, env.demo.seating(seating_id))
    return seating_out(env, record)


@router.get("/seating-drafts", response_model=s.Page[s.SeatingDraftOut], dependencies=[Depends(require_exam_data_access)],
            responses=GATE)
async def list_seating_drafts(env: Env, period_code: Annotated[str | None, Query(pattern=PERIOD_CODE)] = None,
                              limit: Limit = DEFAULT_PAGE_SIZE, offset: Offset = 0) -> s.Page[s.SeatingDraftOut]:
    items, total = await env.service.list_seating(period_code=period_code, limit=limit, offset=offset)
    return s.Page[s.SeatingDraftOut](items=[seating_out(env, d) for d in items], total=total, limit=limit, offset=offset,
                                     persistence=env.info)


@router.get("/seating-drafts/{draft_id}", response_model=s.SeatingDraftOut, dependencies=[Depends(require_exam_data_access)],
            responses={**GATE, **NOT_FOUND})
async def get_seating_draft(env: Env, draft_id: UUID) -> s.SeatingDraftOut:
    draft, _ = await env.service.get_seating(draft_id)
    return seating_out(env, draft)


@router.get("/seating-drafts/{draft_id}/rooms/{session_id}/{venue_id}", response_model=s.SeatingRoomOut,
            dependencies=[Depends(require_exam_data_access)], responses={**GATE, **NOT_FOUND})
async def get_seating_draft_room(env: Env, draft_id: UUID, session_id: Annotated[str, Path(max_length=64)],
                                 venue_id: Annotated[str, Path(max_length=64)]) -> s.SeatingRoomOut:
    """Saved room-wise seats of one session."""
    draft, seats = await env.service.get_seating(draft_id)
    rows = sorted((x for x in seats if x.session_id == session_id and x.venue_id == venue_id), key=lambda x: x.physical_seat_number)
    if not rows:
        raise DemoServiceError(404, "SEATING_ROOM_NOT_FOUND", f"No saved seats for room {venue_id} in session {session_id}.")
    return s.SeatingRoomOut(draft_id=draft.draft_id, session_id=session_id, venue_id=venue_id, seat_state=draft.seat_state,
                            students=len(rows), persistence=env.info, seats=[s.SeatOut(
                                session_id=x.session_id, venue_id=x.venue_id, student_id=x.student_id, module_code=x.module_code,
                                logical_seat_number=x.logical_seat_number, physical_seat_number=x.physical_seat_number,
                                physical_seat_label=x.physical_seat_label, verification_status=x.verification_status) for x in rows])
