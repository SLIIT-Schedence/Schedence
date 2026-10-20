"""Generate examination timetables, save validated ones as drafts, approve and publish them.

* ``POST /timetables/generate`` (``examinations:generate``): generate only; nothing is saved.
* ``POST /timetables/drafts`` (``examinations:generate``): generate, validate again and
  save as a new DRAFT (201). Nothing is saved without a validated timetable (422).
* ``GET /timetables`` and ``GET /timetables/{id}`` (``examinations:read``): saved
  timetables of this institution, without student IDs.
* ``GET /timetables/{id}/seats`` (``examinations:generate`` or ``examinations:approve``):
  the individual seat allocations, with student IDs.
* ``POST /timetables/{id}/approve`` and ``/publish`` (``examinations:approve``):
  DRAFT -> APPROVED -> PUBLISHED, after integrity, stale-input and H1-H11 checks
  (see :mod:`app.saved_timetables.workflow`); invalid changes are 409.
* ``POST /timetables/{id}/regenerate`` (``examinations:generate``): a new DRAFT from
  the same cycle and modules; the old timetable is kept unchanged.
* ``GET /timetables/{id}/history`` (``examinations:read``): every status change.

Generation solver outcomes (OPTIMAL, FEASIBLE, INFEASIBLE, UNKNOWN, MODEL_INVALID) are
answered with 200 and ``generated`` true/false; request and input problems with
422; a running generation with 429; missing settings tables, invalid saved
settings, an unavailable database or solver with 503.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections import Counter
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status

from app.api.deps import (
    PERIOD_CODE,
    Limit,
    Offset,
    SavedTimetableReaderDep,
    SavedTimetableWriterDep,
    TimetableGeneratorDep,
)
from app.api.schemas import (
    ApproveTimetableIn,
    PublishTimetableIn,
    RegenerateTimetableIn,
    TimetableHistoryOut,
    TimetableStatusChangeOut,
    ExamKind,
    ExcludedModuleOut,
    ExcludedRegistrationsOut,
    FairnessOut,
    GenerateTimetableIn,
    GeneratedRoomOut,
    GeneratedSessionOut,
    GeneratedTimetableOut,
    GenerationInputsOut,
    GenerationSummaryOut,
    GenerationValidationOut,
    OptimizationOut,
    PageOut,
    SavedSeatOut,
    SavedTimetableOut,
    SavedTimetableSummaryOut,
    SettingsProblemOut,
    SolverRunOut,
    TimetableStatus,
    WorkloadMetricsOut,
)
from app.auth.context import AuthContext
from app.auth.dependencies import (
    get_auth,
    require_exam_approval,
    require_exam_data_access,
    require_exam_generation,
    require_exam_seat_access,
)
from app.auth.principal import Principal
from app.exam_periods.rules import saved_calendar
from app.generation.service import GenerationOutcome, GenerationRequest
from app.saved_timetables import ListFilter, NewTimetable, TimetableDetail, workflow
from app.saved_timetables.content import content_fingerprint
from app.generation.preferences import checked_saved_preferences
from app.saved_timetables.errors import SavedTimetableNotFoundError, TimetableNotSavedError, TimetableWorkflowError
from app.scheduling.optimization import OptimizationResult
from app.scheduling.validation import validate_timetable
from app.api.analysis_schemas import EvaluationAnalysisOut
from app.generation.analysis import build_analysis

router = APIRouter(prefix="/timetables", tags=["timetables"])

Generator = Annotated[Principal, Depends(require_exam_generation)]
Reader = Annotated[Principal, Depends(require_exam_data_access)]
SeatReader = Annotated[Principal, Depends(require_exam_seat_access)]
Approver = Annotated[Principal, Depends(require_exam_approval)]
Auth = Annotated[AuthContext, Depends(get_auth)]
TimetableId = Annotated[int, Path(ge=1, le=2**63 - 1)]
HARD_RULES = [f"H{i}" for i in range(1, 12)]
NOTICES = [
    "Not saved: generated timetables are not stored or published yet.",
    "Seat numbers are logical seats, not a physical room layout.",
]
SELECTION_NOTICES = {
    "full_cycle": "Modules and registrations come from the period's configured examination cycle mapping.",
    "partial_cycle": "Partial run: only the listed modules of the period's examination cycle were scheduled.",
    "listed_modules": "Modules were listed by the administrator: this period has no examination cycle mapping.",
}


def _problems(violations) -> list[SettingsProblemOut]:
    return [SettingsProblemOut(rule=v.constraint, message=v.message) for v in violations]


def _present(outcome: GenerationOutcome) -> GeneratedTimetableOut:
    snapshot, result, timetable = outcome.snapshot, outcome.result, outcome.timetable
    selection = ("listed_modules" if not snapshot.cycle_modules
                 else "full_cycle" if outcome.request.module_codes is None else "partial_cycle")
    modules = {m.module_code: m for m in snapshot.modules}
    venues = {v.venue_id: v for v in snapshot.venues}
    capacities = {c.venue_id: c.exam_capacity for c in snapshot.capacities.venues}
    scopes = {c.module_code: c.candidate_scope for c in snapshot.cycle_modules}
    excluded = _excluded(snapshot)
    kinds = {(r.student_id, r.module_code): r.registration_type for r in snapshot.registrations}
    sessions = []
    for s in sorted(timetable.sessions if timetable else (), key=lambda s: (s.start, s.module_code)):
        m = modules[s.module_code]
        # Counted from the students actually seated in this session (one session of a split module).
        seated = Counter(kinds[seat.student_id, s.module_code] for r in s.rooms for seat in r.seats)
        sessions.append(GeneratedSessionOut(
            session_id=s.session_id, module_code=m.module_code, module_name=m.module_name, exam_type=m.exam_type,
            candidate_scope=scopes.get(m.module_code, "all"), academic_year=m.academic_year, semester=m.semester,
            delivery_mode=m.delivery_mode, session_number=s.session_number, session_count=s.session_count,
            session_label=f"Session {s.session_number:02d}", date=s.start.date(), start_time=s.start.time(), end_time=s.end.time(), start=s.start, end=s.end,
            duration_minutes=m.duration_minutes, reading_time_minutes=m.reading_time_minutes,
            candidates=sum(len(r.seats) for r in s.rooms),
            normal_candidates=seated["normal"], repeat_candidates=seated["repeat"],
            rooms=[GeneratedRoomOut(venue_id=r.venue_id, venue_name=venues[r.venue_id].venue_name,
                                    venue_type=venues[r.venue_id].venue_type, seats=len(r.seats),
                                    exam_capacity=capacities[r.venue_id]) for r in s.rooms],
        ))
    optimization = None
    if isinstance(result, OptimizationResult):
        optimization = OptimizationOut(
            status=result.optimization_status.value if result.optimization_status else None,
            improved=result.improved,
            baseline=WorkloadMetricsOut.model_validate(result.baseline_evaluation) if result.baseline_evaluation else None,
            selected=WorkloadMetricsOut.model_validate(result.selected_evaluation) if result.selected_evaluation else None,
            objective_bound_units=result.objective_bound_units, relative_gap=result.relative_gap,
            fairness=FairnessOut.model_validate(result.fairness_comparison) if result.fairness_comparison else None,
        )
    solver_version = result.solver_version
    period = snapshot.period
    return GeneratedTimetableOut(
        status=outcome.status.value,
        generated=timetable is not None,
        solver=SolverRunOut(
            mode=outcome.request.mode, time_limit_seconds=outcome.solver_settings.time_limit_seconds,
            num_search_workers=outcome.solver_settings.num_search_workers,
            random_seed=outcome.solver_settings.random_seed, wall_time_seconds=round(outcome.wall_time_seconds, 3),
            solver_version=solver_version,
        ),
        inputs=GenerationInputsOut(
            scheduling_preferences=outcome.preferences_snapshot,
            period_code=period.period_code, period_name=period.period_name, exam_kind=period.exam_kind,
            calendar_source="period" if outcome.request.calendar is None else "selected",
            start_date=period.start_date, end_date=period.end_date,
            day_start_time=period.day_start_time, day_end_time=period.day_end_time,
            include_saturdays=period.include_saturdays, include_sundays=period.include_sundays,
            exclusions=[{"excluded_date": e.excluded_date, "reason": e.reason} for e in period.exclusions],
            exam_days=list(period.exam_days), selection=selection,
            modules=len(snapshot.modules),
            repeat_only_modules=sum(c.candidate_scope == "repeat_only" for c in snapshot.cycle_modules),
            students=len({r.student_id for r in snapshot.registrations}), registrations=len(snapshot.registrations),
            repeat_registrations=sum(r.registration_type == "repeat" for r in snapshot.registrations),
            venues=len(snapshot.venues), input_fingerprint=snapshot.fingerprint,
            capacity_ruleset_version=snapshot.capacities.ruleset_version,
            capacity_fingerprint=snapshot.capacities.fingerprint, settings_version=outcome.settings_version,
            settings_fingerprint=outcome.configuration.fingerprint,
        ),
        validation=GenerationValidationOut(
            passed=True if timetable is not None else (False if outcome.validation_violations else None),
            rules_checked=HARD_RULES, violations=_problems(outcome.validation_violations)),
        summary=GenerationSummaryOut(
            sessions=len(sessions), exam_days_used=len({s.date for s in sessions}),
            room_allocations=sum(len(s.rooms) for s in sessions),
            seat_allocations=sum(r.seats for s in sessions for r in s.rooms),
            multi_room_exams=sum(len(s.rooms) > 1 for s in sessions),
            multi_session_modules=len({s.module_code for s in sessions if s.session_count > 1}),
        ),
        sessions=sessions,
        workload=WorkloadMetricsOut.model_validate(outcome.evaluation) if outcome.evaluation else None,
        optimization=optimization,
        violations=_problems(result.violations),
        diagnostics=list(result.diagnostics),
        notices=[NOTICES[0], SELECTION_NOTICES[selection], *_excluded_notice(excluded), *NOTICES[1:]],
        excluded_registrations=excluded,
    )


EXCLUDED_NOTE = ("Potentially excluded: these registrations were not scheduled because their module is not in this "
                 "examination cycle's mapping. Some may legitimately belong to another cycle (see other_cycles); "
                 "add the module to the cycle mapping if it should be examined here.")


def _excluded(snapshot) -> ExcludedRegistrationsOut:
    rows = [ExcludedModuleOut(**{**dataclasses.asdict(m), "other_cycles": list(m.other_cycles)})
            for m in snapshot.excluded_modules]
    normal = sum(m.normal_registrations for m in rows)
    repeat = sum(m.repeat_registrations for m in rows)
    return ExcludedRegistrationsOut(note=EXCLUDED_NOTE, total=normal + repeat, normal=normal, repeat=repeat, modules=rows)


def _excluded_notice(excluded: ExcludedRegistrationsOut) -> list[str]:
    if not excluded.total:
        return []
    return [f"Warning: {excluded.total} registration(s) ({excluded.repeat} repeat) of {len(excluded.modules)} "
            "module(s) not in this cycle were not scheduled; see excluded_registrations."]


@router.post("/generate", response_model=GeneratedTimetableOut)
async def generate_timetable(
    _: Generator, body: GenerateTimetableIn, generator: TimetableGeneratorDep,
) -> GeneratedTimetableOut:
    """Generate and independently validate a timetable for one examination cycle (not saved).

    Without ``module_codes``: every module of the period's cycle mapping. With them: only those modules.
    """
    outcome = await generator.generate(_request(body))
    presented = _present(outcome)
    if body.include_analysis:
        presented.analysis = EvaluationAnalysisOut.model_validate(await asyncio.to_thread(build_analysis, outcome))
    return presented


def _request(body: GenerateTimetableIn) -> GenerationRequest:
    return GenerationRequest(
        period_code=body.period_code,
        module_codes=None if body.module_codes is None else tuple(body.module_codes), mode=body.mode,
        time_limit_seconds=body.time_limit_seconds, calendar=None if body.calendar is None else body.calendar.to_calendar(),
        scheduling_preferences=body.scheduling_preferences,
    )


# --- Saved timetables -----------------------------------------------------------------------------------------

SAVED_NOTICE = "Saved as a draft: not approved or published."
SEAT_NOTICE = "Seat allocations (student IDs) are only returned by GET /timetables/{timetable_id}/seats."


def _dump(model) -> dict | None:
    return None if model is None else model.model_dump(mode="json")


def _new_timetable(outcome: GenerationOutcome, presented: GeneratedTimetableOut, user_id,
                   source_timetable_id: int | None = None) -> NewTimetable:
    inputs, request = presented.inputs, outcome.request
    header = dict(
        period_code=inputs.period_code, period_name=inputs.period_name, exam_kind=inputs.exam_kind,
        mode=request.mode, selection=inputs.selection,
        module_codes=None if request.module_codes is None else list(request.module_codes),
        solver_status=presented.status, solver_version=presented.solver.solver_version,
        solver_run=_dump(presented.solver), input_fingerprint=inputs.input_fingerprint,
        settings_version=inputs.settings_version, settings_fingerprint=inputs.settings_fingerprint,
        seating_ruleset_version=inputs.capacity_ruleset_version, capacity_fingerprint=inputs.capacity_fingerprint,
        validation_passed=True, validation=_dump(presented.validation), inputs=_dump(inputs),
        summary=_dump(presented.summary), workload=_dump(presented.workload),
        optimization=_dump(presented.optimization), excluded_registrations=_dump(presented.excluded_registrations),
        notices=[SAVED_NOTICE, *(n for n in presented.notices if n != NOTICES[0]), SEAT_NOTICE],
        created_by=user_id, source_timetable_id=source_timetable_id,
    )
    sessions = [dict(
        session_id=s.session_id, module_code=s.module_code, module_name=s.module_name, exam_type=s.exam_type,
        candidate_scope=s.candidate_scope, academic_year=s.academic_year, semester=s.semester,
        delivery_mode=s.delivery_mode, session_number=s.session_number, session_count=s.session_count,
        starts_at=s.start, ends_at=s.end, duration_minutes=s.duration_minutes,
        reading_time_minutes=s.reading_time_minutes, candidates=s.candidates,
        normal_candidates=s.normal_candidates, repeat_candidates=s.repeat_candidates,
    ) for s in presented.sessions]
    rooms = [dict(session_id=s.session_id, venue_id=r.venue_id, venue_name=r.venue_name, venue_type=r.venue_type,
                  seats=r.seats, exam_capacity=r.exam_capacity) for s in presented.sessions for r in s.rooms]
    kinds = {(r.student_id, r.module_code): r.registration_type for r in outcome.snapshot.registrations}
    seats = [dict(session_id=s.session_id, venue_id=room.venue_id, seat_number=int(seat.seat_number),
                  student_id=seat.student_id, registration_type=kinds[seat.student_id, s.module_code])
             for s in outcome.timetable.sessions for room in s.rooms for seat in room.seats]
    header["content_fingerprint"] = content_fingerprint(sessions, rooms, seats)  # checked before every approval
    return NewTimetable(header, sessions, rooms, seats)


_SESSION_FIELDS = ("session_id", "module_code", "module_name", "exam_type", "candidate_scope", "academic_year",
                   "semester", "delivery_mode", "session_number", "session_count", "duration_minutes",
                   "reading_time_minutes", "candidates", "normal_candidates", "repeat_candidates")


def _detail(detail: TimetableDetail) -> SavedTimetableOut:
    rooms: dict[str, list[GeneratedRoomOut]] = {}
    for r in detail.rooms:
        rooms.setdefault(r["session_id"], []).append(GeneratedRoomOut.model_validate(r))
    sessions = [GeneratedSessionOut(
        **{k: s[k] for k in _SESSION_FIELDS},
        session_label=f"Session {s['session_number']:02d}", date=s["starts_at"].date(),
        start_time=s["starts_at"].time(), end_time=s["ends_at"].time(), start=s["starts_at"], end=s["ends_at"],
        rooms=rooms.get(s["session_id"], []),
    ) for s in detail.sessions]
    return SavedTimetableOut.model_validate({**detail.header, "solver": detail.header["solver_run"],
                                             "sessions": sessions})


@router.post("/drafts", response_model=SavedTimetableOut, status_code=status.HTTP_201_CREATED)
async def save_timetable_draft(
    admin: Generator, body: GenerateTimetableIn, generator: TimetableGeneratorDep, auth: Auth,
    open_store: SavedTimetableWriterDep,
) -> SavedTimetableOut:
    """Generate a timetable (as ``/generate``), validate it again and save it as a new DRAFT.

    Every save is a new draft with the next ``draft_number`` of the cycle; older drafts
    are kept. Without a validated timetable nothing is saved (422).
    """
    return await _generate_and_save(_request(body), generator, auth, open_store, admin)


async def _generate_and_save(request: GenerationRequest, generator, auth: AuthContext, open_store, admin: Principal,
                             source_timetable_id: int | None = None) -> SavedTimetableOut:
    outcome = await generator.generate(request)
    presented = _present(outcome)
    if outcome.timetable is None:
        problems = presented.validation.violations + presented.violations
        raise TimetableNotSavedError(presented.status, [p.model_dump() for p in problems])
    # Independent H1-H11 check of exactly what is saved (the generator checked it too).
    violations = validate_timetable(outcome.snapshot, outcome.timetable)
    if violations:
        raise TimetableNotSavedError("MODEL_INVALID", [p.model_dump() for p in _problems(violations)])
    record = _new_timetable(outcome, presented, admin.user_id, source_timetable_id)
    institution = auth.settings.institution_id
    async with open_store() as store:  # opened only now: no transaction is held during the solve
        header = await store.save(institution, record)
        await store.commit()
        detail = await store.get(institution, header["timetable_id"])
    return _detail(detail)


@router.get("", response_model=PageOut[SavedTimetableSummaryOut])
async def list_saved_timetables(
    _: Reader, auth: Auth, store: SavedTimetableReaderDep,
    period_code: Annotated[str | None, Query(pattern=PERIOD_CODE, description="Examination cycle")] = None,
    exam_kind: Annotated[ExamKind | None, Query(description="MID or FINAL")] = None,
    timetable_status: Annotated[TimetableStatus | None, Query(alias="status")] = None,
    limit: Limit = 50, offset: Offset = 0,
) -> PageOut[SavedTimetableSummaryOut]:
    """Saved timetables of this institution, newest first. No sessions and no student IDs."""
    rows, total = await store.list(auth.settings.institution_id,
                                   ListFilter(period_code=period_code, exam_kind=exam_kind, status=timetable_status),
                                   limit=limit, offset=offset)
    return PageOut(items=[SavedTimetableSummaryOut.model_validate(r) for r in rows], total=total, limit=limit,
                   offset=offset)


@router.get("/{timetable_id}", response_model=SavedTimetableOut)
async def get_saved_timetable(
    _: Reader, timetable_id: TimetableId, auth: Auth, store: SavedTimetableReaderDep,
) -> SavedTimetableOut:
    """One saved timetable with its sessions, venues and seat counts (no student IDs)."""
    detail = await store.get(auth.settings.institution_id, timetable_id)
    if detail is None:
        raise SavedTimetableNotFoundError()
    return _detail(detail)


@router.get("/{timetable_id}/seats", response_model=PageOut[SavedSeatOut])
async def list_saved_timetable_seats(
    _: SeatReader, timetable_id: TimetableId, auth: Auth, store: SavedTimetableReaderDep,
    session_id: Annotated[str | None, Query(max_length=64, pattern=r"^[A-Z0-9:-]+$")] = None,
    limit: Limit = 500, offset: Offset = 0,
) -> PageOut[SavedSeatOut]:
    """Individual seat allocations of a saved timetable (student IDs; restricted permission)."""
    found = await store.seats(auth.settings.institution_id, timetable_id, session_id=session_id,
                              limit=limit, offset=offset)
    if found is None:
        raise SavedTimetableNotFoundError()
    rows, total = found
    return PageOut(items=[SavedSeatOut.model_validate(r) for r in rows], total=total, limit=limit, offset=offset)


# --- Approval and publication ------------------------------------------------------------------------------

CHECKS = ["status and revision", "stored content fingerprint", "scheduling settings unchanged",
          "seating capacities unchanged", "input snapshot unchanged", "H1-H11 on the current inputs"]


def _status_change(change: workflow.StatusChange) -> TimetableStatusChangeOut:
    return TimetableStatusChangeOut(
        timetable=SavedTimetableSummaryOut.model_validate(change.timetable),
        history=TimetableHistoryOut.model_validate(change.history),
        archived_timetable=None if change.archived is None else SavedTimetableSummaryOut.model_validate(change.archived),
        checks=CHECKS,
    )


@router.post("/{timetable_id}/approve", response_model=TimetableStatusChangeOut)
async def approve_timetable(
    approver: Approver, timetable_id: TimetableId, body: ApproveTimetableIn, generator: TimetableGeneratorDep,
    auth: Auth, open_store: SavedTimetableWriterDep,
) -> TimetableStatusChangeOut:
    """DRAFT -> APPROVED.

    Refused (409) if the status or revision differ, the stored content changed, or the
    settings or inputs changed since generation: then regenerate a new draft.
    """
    async with open_store() as store:
        change = await workflow.approve(store, generator, auth.settings.institution_id, timetable_id,
                                        expected_revision=body.expected_revision, user_id=approver.user_id,
                                        reason=body.reason)
    return _status_change(change)


@router.post("/{timetable_id}/publish", response_model=TimetableStatusChangeOut)
async def publish_timetable(
    approver: Approver, timetable_id: TimetableId, body: PublishTimetableIn, generator: TimetableGeneratorDep,
    auth: Auth, open_store: SavedTimetableWriterDep,
) -> TimetableStatusChangeOut:
    """APPROVED -> PUBLISHED, after the same checks as approval.

    One published timetable per cycle: ``replace_published`` archives the current one.
    Nobody is notified yet.
    """
    async with open_store() as store:
        change = await workflow.publish(store, generator, auth.settings.institution_id, timetable_id,
                                        expected_revision=body.expected_revision, user_id=approver.user_id,
                                        reason=body.reason, replace_published=body.replace_published)
    return _status_change(change)


@router.post("/{timetable_id}/regenerate", response_model=SavedTimetableOut, status_code=status.HTTP_201_CREATED)
async def regenerate_timetable(
    admin: Generator, timetable_id: TimetableId, body: RegenerateTimetableIn, generator: TimetableGeneratorDep,
    auth: Auth, open_store: SavedTimetableWriterDep,
) -> SavedTimetableOut:
    """A new DRAFT for the same cycle and modules, from the current inputs and settings.

    The old timetable (any status) is kept unchanged; the new one records it as its source.
    """
    async with open_store() as store:  # read and closed before the solver runs
        source = await store.get(auth.settings.institution_id, timetable_id)
    if source is None:
        raise SavedTimetableNotFoundError()
    header = source.header
    try:
        calendar = saved_calendar(header["inputs"])  # the same calendar as the source timetable
        preferences = checked_saved_preferences(header)
    except ValueError:
        raise TimetableWorkflowError("INTEGRITY", "The stored calendar or scheduling preferences cannot be verified.") from None
    request = GenerationRequest(
        period_code=header["period_code"],
        module_codes=None if header["module_codes"] is None else tuple(header["module_codes"]),
        mode=body.mode or header["mode"], time_limit_seconds=body.time_limit_seconds, calendar=calendar,
        stored_preferences=preferences[1] if preferences else None,
        stored_settings_version=header["settings_version"],
    )
    return await _generate_and_save(request, generator, auth, open_store, admin, source_timetable_id=timetable_id)


@router.get("/{timetable_id}/history", response_model=list[TimetableHistoryOut])
async def get_timetable_history(
    _: Reader, timetable_id: TimetableId, auth: Auth, store: SavedTimetableReaderDep,
) -> list[TimetableHistoryOut]:
    """Every status change of a saved timetable, oldest first: who, when, why and what was checked."""
    rows = await store.history(auth.settings.institution_id, timetable_id)
    if rows is None:
        raise SavedTimetableNotFoundError()
    return [TimetableHistoryOut.model_validate(r) for r in rows]
