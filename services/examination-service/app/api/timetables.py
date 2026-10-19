"""Generate an examination timetable (``examinations:generate``). Nothing is saved.

Solver outcomes (OPTIMAL, FEASIBLE, INFEASIBLE, UNKNOWN, MODEL_INVALID) are
answered with 200 and ``generated`` true/false; request and input problems with
422; a running generation with 429; missing settings tables, invalid saved
settings, an unavailable database or solver with 503.
"""

from __future__ import annotations

import dataclasses
from collections import Counter
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import TimetableGeneratorDep
from app.api.schemas import (
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
    SettingsProblemOut,
    SolverRunOut,
    WorkloadMetricsOut,
)
from app.auth.dependencies import require_exam_generation
from app.auth.principal import Principal
from app.generation.service import GenerationOutcome, GenerationRequest
from app.scheduling.optimization import OptimizationResult

router = APIRouter(prefix="/timetables", tags=["timetables"])

Generator = Annotated[Principal, Depends(require_exam_generation)]
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
            period_code=period.period_code, period_name=period.period_name, exam_kind=period.exam_kind,
            start_date=period.start_date,
            end_date=period.end_date, exam_days=list(period.exam_days), selection=selection,
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
    outcome = await generator.generate(GenerationRequest(
        period_code=body.period_code,
        module_codes=None if body.module_codes is None else tuple(body.module_codes), mode=body.mode,
        time_limit_seconds=body.time_limit_seconds,
    ))
    return _present(outcome)
