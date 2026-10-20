"""Sequential two-cycle orchestration; reuses the solver and X1/X2 without changing their rules.

Peer constraints exist only in this request. Unapproved drafts never become global reservations.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
from uuid import uuid4

from app.generation.service import GenerationInputError, GenerationOutcome, GenerationRequest
from app.scheduling.contracts import Violation
from app.scheduling.protected import ProtectedSchedule, ProtectedSession, ProtectedTimetable, cross_timetable_violations

NOTICE = ("Sequential generation: Semester 1 is scheduled first, then Semester 2 works around its temporary "
          "assignments. This does not prove a jointly optimal solution. Neither timetable is approved or published.")


def policy_order(configuration) -> str:
    """JSONB reorders object keys; record tuple ordering used by the existing input fingerprint explicitly."""
    return json.dumps({key: [name for name, _ in getattr(configuration.scheduling, key)]
                       for key in ("venue_compatibility", "module_venue_overrides")})


def restore_batch_configuration(header, configuration):
    context = header.get("inputs", {}).get("batch")
    if context is None:
        return configuration  # existing single-cycle/legacy snapshots keep their contract
    try:
        order = json.loads(context["policy_order"])
        if set(order) != {"venue_compatibility", "module_venue_overrides"}:
            raise ValueError()
        values = {}
        for key, names in order.items():
            existing = dict(getattr(configuration.scheduling, key))
            if type(names) is not list or any(type(name) is not str for name in names) or len(names) != len(set(names)) or set(names) != set(existing):
                raise ValueError()
            values[key] = tuple((name, existing[name]) for name in names)
        return replace(configuration, scheduling=replace(configuration.scheduling, **values))
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ValueError("The stored batch scheduling order cannot be verified.") from None


@dataclass(frozen=True)
class BothOutcome:
    batch_id: str
    outcomes: tuple[GenerationOutcome, ...]
    period_codes: tuple[str, str]
    violations: tuple[Violation, ...]

    @property
    def generated(self) -> bool:
        return len(self.outcomes) == 2 and all(o.timetable is not None for o in self.outcomes) and not self.violations


def peer_schedule(outcome: GenerationOutcome) -> ProtectedSchedule:
    """Actual sitting/room/student assignments, never a module-level approximation or public student list."""
    if outcome.timetable is None:
        return ProtectedSchedule()
    p = outcome.snapshot.period
    sessions = tuple(ProtectedSession(s.session_id, s.module_code, s.start, s.end,
        tuple(r.venue_id for r in s.rooms), tuple(sorted({seat.student_id for r in s.rooms for seat in r.seats})))
        for s in outcome.timetable.sessions)
    return ProtectedSchedule((ProtectedTimetable(0, p.period_code, p.period_name, "TEMPORARY_BATCH_PEER", 0, 0,
                                                p.start_date, p.end_date, sessions),))


def pair_violations(outcomes: tuple[GenerationOutcome, ...]) -> tuple[Violation, ...]:
    if len(outcomes) != 2 or any(o.timetable is None for o in outcomes):
        return ()
    first, second = outcomes
    # Shared preferences normally make these equal; take the larger break as defence in depth.
    gap = max(o.snapshot.policy.minimum_break_minutes for o in outcomes)
    return cross_timetable_violations(peer_schedule(first), second.timetable.sessions, minimum_break_minutes=gap)


async def generate_both(generator, requests: tuple[GenerationRequest, GenerationRequest]) -> BothOutcome:
    if len(requests) != 2 or requests[0].period_code == requests[1].period_code:
        raise GenerationInputError((Violation("BOTH_SEMESTERS", "Choose two distinct FINAL cycles."),))
    if any(r.module_codes is not None or r.stored_preferences is not None for r in requests):
        raise GenerationInputError((Violation("BOTH_SEMESTERS", "Both Semesters schedules two complete cycles."),))
    first_request, second_request = requests
    if any(getattr(first_request, key) != getattr(second_request, key)
           for key in ("mode", "time_limit_seconds", "scheduling_preferences")):
        raise GenerationInputError((Violation("BOTH_SEMESTERS", "Both cycles must use shared scheduling preferences and mode."),))
    defaults = await generator._load_settings()
    prepared = []
    # All inputs are loaded and closed before either solver starts. Freeze shared defaults once.
    for request in requests:
        request = replace(request, scheduling_preferences=request.scheduling_preferences or {})
        prepared.append((request, await generator._prepare(request, defaults=defaults)))
    periods = [item[1][3].period for item in prepared]
    if ({p.semester for p in periods} != {1, 2} or any(p.exam_kind != "FINAL" for p in periods)
            or periods[0].academic_year_label != periods[1].academic_year_label):
        raise GenerationInputError((Violation("BOTH_SEMESTERS", "Select one Semester 1 and one Semester 2 FINAL cycle of the same academic year."),))
    prepared.sort(key=lambda item: item[1][3].period.semester)
    codes = tuple(item[0].period_code for item in prepared)
    batch_id = str(uuid4())
    first = await generator._generate(prepared[0][0], prepared=prepared[0][1])
    if first.timetable is None:
        return BothOutcome(batch_id, (first,), codes, ())
    request, (configuration, version, solver, snapshot, custom) = prepared[1]
    protected = snapshot.protected or ProtectedSchedule()
    snapshot = replace(snapshot, protected=ProtectedSchedule(
        (*protected.timetables, *peer_schedule(first).timetables), protected.overlapping_drafts))
    second = await generator._generate(request, prepared=(configuration, version, solver, snapshot, custom))
    outcomes = (first, second)
    return BothOutcome(batch_id, outcomes, codes, pair_violations(outcomes))
