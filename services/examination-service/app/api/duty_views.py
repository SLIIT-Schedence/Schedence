"""Builds the Fairness-Aware Staff Allocation for Examination Duties API responses from domain results (no business logic, no I/O)."""

from __future__ import annotations

from collections import Counter

from app.api import duty_schemas as s
from app.duty_allocation.demo_service import DemoContext, Snapshot, StoredAllocation, StoredSeating
from app.duty_allocation.duty_changes import ChangeProposal
from app.duty_allocation.fairness_metrics import FairnessMetrics
from app.duty_allocation.types import DutyAssignment
from app.scheduling.contracts import Violation


def meta(context: DemoContext) -> s.MetaOut:
    return s.MetaOut(data_source=context.snapshot.data_source, source_description=context.snapshot.source_description,
                     institution_id=context.institution_id,
                     period_code=context.period_code, snapshot_loaded_at=context.snapshot.loaded_at)


def violations(items) -> list[s.ViolationOut]:
    return [s.ViolationOut(constraint=v.constraint, message=v.message) for v in (items or ())]


class Lookup:
    """Names and times of staff, venues and sessions, for readable assignments."""

    def __init__(self, context: DemoContext) -> None:
        inputs = context.inputs
        self.staff = {m.staff_id: m for m in inputs.staff.staff.staff}
        self.excluded = {e.staff_id: e for e in inputs.staff.staff.excluded}
        self.venues = {v.venue_id: v for v in inputs.timetable.venues}
        self.sessions = {x.session_id: x for x in inputs.timetable.draft.sessions}
        self.modules = {r["module_code"]: r.get("module_name") for r in context.snapshot.records.rows("modules")}

    def number(self, staff_id: str) -> str | None:
        found = self.staff.get(staff_id) or self.excluded.get(staff_id)
        return None if found is None else found.staff_number

    def name(self, staff_id: str) -> str | None:
        found = self.staff.get(staff_id)
        return None if found is None else found.full_name

    def assignment(self, a: DutyAssignment) -> s.AssignmentOut:
        session, venue = self.sessions.get(a.session_id), self.venues.get(a.venue_id)
        return s.AssignmentOut(
            session_id=a.session_id, venue_id=a.venue_id, venue_name=None if venue is None else venue.venue_name,
            module_code=None if session is None else session.module_code,
            date=None if session is None else session.start.date(),
            start=None if session is None else session.start, end=None if session is None else session.end,
            staff_id=a.staff_id, staff_number=self.number(a.staff_id), staff_name=self.name(a.staff_id),
            role=a.role.value)


def assignments(lookup: Lookup, roster) -> list[s.AssignmentOut]:
    return [lookup.assignment(a) for a in sorted(roster, key=lambda a: (a.session_id, a.venue_id, a.role.value, a.staff_id))]


def metrics(lookup: Lookup, m: FairnessMetrics | None) -> s.FairnessMetricsOut | None:
    if m is None:
        return None
    duties = dict(m.duty_counts)
    per_staff = [s.StaffLoadOut(staff_id=sid, staff_number=lookup.number(sid), staff_name=lookup.name(sid),
                                duties=duties.get(sid, 0), total_load=load) for sid, load in m.total_loads]
    return s.FairnessMetricsOut(
        min_load=m.min_load, max_load=m.max_load, mean_load=m.mean_load, std_load=m.std_load, gini=m.gini,
        max_duties=m.max_duties, min_duties=m.min_duties, staff_without_duties=m.staff_without_duties,
        supervisor_duties_range=m.supervisor_duties_range, back_to_back_pairs=m.back_to_back_pairs,
        preference_breaches=m.preference_breaches, staff_missing_admin_hours=list(m.missing_admin_hours),
        per_staff=per_staff)


def comparison(run: StoredAllocation) -> s.ComparisonOut | None:
    o = run.orchestration
    b, f = o.baseline_metrics, o.fair_metrics
    if b is None or f is None or o.baseline is None or o.fair is None:
        return None
    different = len(set(o.baseline.assignments) ^ set(o.fair.assignments)) // 2
    return s.ComparisonOut(
        fairer_by_gini="FAIR" if f.gini < b.gini else "BASELINE" if b.gini < f.gini else "TIE",
        gini_baseline=b.gini, gini_fair=f.gini, std_load_baseline=b.std_load, std_load_fair=f.std_load,
        load_range_baseline=b.max_load - b.min_load, load_range_fair=f.max_load - f.min_load,
        back_to_back_baseline=b.back_to_back_pairs, back_to_back_fair=f.back_to_back_pairs,
        preference_breaches_baseline=b.preference_breaches, preference_breaches_fair=f.preference_breaches,
        assignments_different=different)


def rosters(run: StoredAllocation, lookup: Lookup) -> list[s.RosterOut]:
    o = run.orchestration
    rechecked = dict(o.revalidation)
    out = []
    for label, result, measured in (("BASELINE", o.baseline, o.baseline_metrics), ("FAIR", o.fair, o.fair_metrics)):
        if result is None:
            continue
        found = rechecked.get(label)
        out.append(s.RosterOut(
            label=label, solver_status=result.status.value, assignments=assignments(lookup, result.assignments),
            metrics=metrics(lookup, measured), revalidation_violations=violations(found),
            independently_valid=None if found is None else not found, diagnostics=list(result.diagnostics),
            wall_time_seconds=result.wall_time_seconds))
    return out


def warnings(run: StoredAllocation) -> list[str]:
    o = run.orchestration
    out = [d for d in o.diagnostics if "NOT AVAILABLE" in d or "PARTIAL" in d or "missing" in d.lower()
           or "No workload record" in d]
    if run.result.requirement_mismatches:
        out.append(f"{len(run.result.requirement_mismatches)} stored duty requirement(s) differ from the calculation.")
    if not o.fairness_complete and o.fairness_scope:
        out.append("Fairness is partial: it covers only " + ", ".join(o.fairness_scope) + ".")
    return list(dict.fromkeys(out))


def allocation_run(run: StoredAllocation) -> s.AllocationRunOut:
    o, lookup = run.orchestration, Lookup(run.context)
    return s.AllocationRunOut(
        run_id=run.run_id, mode=run.mode, status=o.status.value, created_at=run.created_at,
        rosters=rosters(run, lookup), comparison=comparison(run), fairness_complete=o.fairness_complete,
        fairness_scope=list(o.fairness_scope),
        data_availability=[s.DataAvailabilityOut(name=d.name, status=d.status.value, source=d.source, records=d.records,
                                                 origin=d.origin.value) for d in o.data_availability],
        violations=violations(o.violations), diagnostics=list(o.diagnostics), warnings=warnings(run),
        requirement_mismatches=list(run.result.requirement_mismatches),
        example_roster_violations=violations(run.result.example_roster_violations), meta=meta(run.context))


def fairness(run: StoredAllocation) -> s.FairnessOut:
    o, lookup = run.orchestration, Lookup(run.context)
    notes = [d for d in o.diagnostics if "admin" in d.lower() or "Fairness" in d or "workload" in d.lower()]
    return s.FairnessOut(run_id=run.run_id, status=o.status.value, baseline=metrics(lookup, o.baseline_metrics),
                         fair=metrics(lookup, o.fair_metrics), comparison=comparison(run),
                         fairness_complete=o.fairness_complete, fairness_scope=list(o.fairness_scope), notes=notes,
                         meta=meta(run.context))


def change_preview(run: StoredAllocation, label: str, proposal: ChangeProposal,
                   rechecked: tuple[Violation, ...] | None) -> s.ChangePreviewOut:
    lookup = Lookup(run.context)
    return s.ChangePreviewOut(
        run_id=run.run_id, roster=label, kind=proposal.kind, status=proposal.status.value,
        removed=assignments(lookup, proposal.removed), added=assignments(lookup, proposal.added),
        proposed_roster=None if proposal.proposed_roster is None else assignments(lookup, proposal.proposed_roster),
        candidates=[s.CandidateOut(staff_id=c.staff_id, staff_number=c.staff_number, staff_name=lookup.name(c.staff_id),
                                   duties_after=c.duties_after, objective=c.objective,
                                   metrics=metrics(lookup, c.metrics)) for c in proposal.candidates],
        rejected=[s.RejectedCandidateOut(staff_id=r.staff_id, staff_number=r.staff_number,
                                         staff_name=lookup.name(r.staff_id), reasons=list(r.reasons))
                  for r in proposal.rejected],
        violations=violations(proposal.violations),
        independent_check_violations=None if rechecked is None else violations(rechecked),
        reasons=list(proposal.reasons), original_metrics=metrics(lookup, proposal.original_metrics),
        proposed_metrics=metrics(lookup, proposal.proposed_metrics), original_objective=proposal.original_objective,
        proposed_objective=proposal.proposed_objective, meta=meta(run.context))


# --- Seating ------------------------------------------------------------------------------------------

def seat_state(stored: StoredSeating) -> str:
    plan = stored.result.seating.plan
    if plan is None:
        return "NONE"
    if plan.synthetic_halls:
        return "SYNTHETIC"
    if plan.provisional_halls:
        return "PROVISIONAL"
    return "VERIFIED" if plan.all_seats_verified else "PROVISIONAL"


def seating_run(stored: StoredSeating) -> s.SeatingRunOut:
    seating, plan = stored.result.seating, stored.result.seating.plan
    return s.SeatingRunOut(
        seating_id=stored.seating_id, status=seating.status.value, created_at=stored.created_at,
        seat_state=seat_state(stored), all_seats_verified=bool(plan and plan.all_seats_verified),
        synthetic_halls=list(plan.synthetic_halls) if plan else [],
        provisional_halls=list(plan.provisional_halls) if plan else [],
        capacity_rules_source=plan.capacity_rules_source if plan else None,
        assignment_count=len(plan.assignments) if plan else 0,
        rooms=[s.RoomSeatingOut(session_id=r.session_id, venue_id=r.venue_id, venue_type=r.venue_type,
                                full_capacity=r.full_capacity, capacity_percentage=r.percentage,
                                exam_capacity=r.exam_capacity, usable_seats=r.usable_seats, students=r.students,
                                seat_limit=r.limit) for r in seating.rooms],
        violations=violations(seating.violations), diagnostics=list(seating.diagnostics),
        example_problems=list(stored.result.example_problems), meta=meta(stored.context))


def room_seats(stored: StoredSeating, session_id: str, venue_id: str) -> s.RoomSeatsOut | None:
    plan = stored.result.seating.plan
    rows = [a for a in (plan.assignments if plan else ()) if a.session_id == session_id and a.venue_id == venue_id]
    known = {(r.session_id, r.venue_id) for r in stored.result.seating.rooms}
    if not rows and (session_id, venue_id) not in known:
        return None
    seats = [s.SeatOut(student_id=a.student_id, module_code=a.module_code, seat_number=a.seat_number,
                       seat_label=a.seat_label, seat_verified=a.seat_verified)
             for a in sorted(rows, key=lambda a: a.seat_number)]
    return s.RoomSeatsOut(seating_id=stored.seating_id, session_id=session_id, venue_id=venue_id,
                          seat_state=seat_state(stored), students=len(seats), seats=seats, meta=meta(stored.context))



# --- Periods, sessions, staff, requirements -----------------------------------------------------------

def snapshot_meta(snapshot: Snapshot, institution_id) -> s.MetaOut:
    return s.MetaOut(data_source=snapshot.data_source, source_description=snapshot.source_description,
                     institution_id=institution_id, snapshot_loaded_at=snapshot.loaded_at)


def periods(records) -> list[s.PeriodOut]:
    counts = Counter(r["period_code"] for r in records.rows("exam_sessions"))
    return [s.PeriodOut(period_code=p["period_code"], period_name=p.get("period_name"),
                        academic_year_label=p.get("academic_year_label"), semester=p.get("semester"),
                        start_date=p["start_date"], end_date=p["end_date"], day_start_time=p["day_start_time"],
                        day_end_time=p["day_end_time"], include_saturdays=bool(p["include_saturdays"]),
                        include_sundays=bool(p["include_sundays"]), session_count=counts.get(p["period_code"], 0))
            for p in sorted(records.rows("exam_periods"), key=lambda r: r["period_code"])]


def sessions(context: DemoContext) -> list[s.SessionOut]:
    lookup = Lookup(context)
    status = {r["session_id"]: r.get("publication_status") for r in context.snapshot.records.rows("exam_sessions")}
    out = []
    for x in sorted(context.inputs.timetable.draft.sessions, key=lambda x: (x.start, x.session_id)):
        rooms = [s.RoomOut(venue_id=r.venue_id, venue_name=getattr(lookup.venues.get(r.venue_id), "venue_name", None),
                           venue_type=getattr(lookup.venues.get(r.venue_id), "venue_type", None),
                           building=getattr(lookup.venues.get(r.venue_id), "building", None), students=len(r.seats))
                 for r in x.rooms]
        out.append(s.SessionOut(session_id=x.session_id, module_code=x.module_code,
                                module_name=lookup.modules.get(x.module_code), period_code=x.period_code,
                                date=x.start.date(), start=x.start, end=x.end,
                                duration_minutes=int((x.end - x.start).total_seconds() // 60),
                                students=sum(r.students for r in rooms), rooms=rooms,
                                publication_status=status.get(x.session_id)))
    return out


def staff_list(context: DemoContext, *, include_excluded: bool, role: str | None) -> list[s.StaffOut]:
    lookup = Lookup(context)
    out = [s.StaffOut(staff_id=m.staff_id, staff_number=m.staff_number, full_name=m.full_name,
                      academic_rank=m.academic_rank, eligible_roles=[r.value for r in m.eligible_roles], eligible=True)
           for m in lookup.staff.values() if role is None or role in {r.value for r in m.eligible_roles}]
    if include_excluded and role is None:
        out += [s.StaffOut(staff_id=e.staff_id, staff_number=e.staff_number, full_name=None, academic_rank=None,
                           eligible_roles=[], eligible=False, exclusion_reason=e.reason)
                for e in lookup.excluded.values()]
    return sorted(out, key=lambda x: x.staff_number)


def workloads(context: DemoContext) -> list[s.WorkloadOut]:
    """COMPUTED, NOT_COMPUTED and MISSING_RESPONSE stay distinct; hours are never estimated."""
    lookup, staff = Lookup(context), context.inputs.staff
    out = {w.staff_id: s.WorkloadOut(staff_id=w.staff_id, staff_number=lookup.number(w.staff_id),
                                     status="COMPUTED" if w.hours_per_week is not None else "NOT_COMPUTED",
                                     hours_per_week=None if w.hours_per_week is None else float(w.hours_per_week),
                                     note=w.note) for w in staff.admin}
    for m in staff.missing_workload:
        out[m.staff_id] = s.WorkloadOut(staff_id=m.staff_id, staff_number=lookup.number(m.staff_id),
                                        status="MISSING_RESPONSE", hours_per_week=None, note=m.reason)
    for sid, member in lookup.staff.items():
        out.setdefault(sid, s.WorkloadOut(staff_id=sid, staff_number=member.staff_number, status="NOT_LOADED",
                                          hours_per_week=None, note="No workload record was loaded."))
    return sorted(out.values(), key=lambda w: (w.staff_number or "", w.staff_id))


def constraints(context: DemoContext, staff_id: str) -> s.ConstraintsOut:
    inputs = context.inputs
    sup, policies = inputs.supplemental, inputs.policies
    pick = lambda rows: [r for r in (rows or ()) if r.staff_id == staff_id]
    notes = [f"{label} data was not supplied; it is not considered." for label, rows in
             (("Unavailability", sup.unavailability), ("Availability", sup.availability),
              ("Day preference", sup.day_preferences), ("Duty history", sup.duty_history),
              ("Restriction", sup.restrictions)) if rows is None]
    history = next(iter(pick(sup.duty_history)), None)
    return s.ConstraintsOut(
        staff_id=staff_id,
        unavailability=[s.WindowOut(start=u.start, end=u.end, reason=u.reason) for u in pick(sup.unavailability)],
        availability=[s.WindowOut(start=a.start, end=a.end) for a in pick(sup.availability)],
        preferences=[s.PreferenceOut(day=p.day, kind=p.kind.value) for p in pick(sup.day_preferences)],
        restrictions=[s.RestrictionOut(module_code=r.module_code, session_id=r.session_id,
                                       role=None if r.role is None else r.role.value, reason=r.reason)
                      for r in pick(sup.restrictions)],
        history=None if history is None else s.HistoryOut(prior_duties=history.prior_duties,
                                                          prior_minutes=history.prior_minutes),
        limits=s.DutyLimitsOut(max_duties_per_day=policies.limits.max_duties_per_day,
                               max_duties_per_period=policies.limits.max_duties_per_period,
                               minimum_gap_minutes=policies.eligibility.minimum_gap_minutes,
                               source=policies.limits.source),
        notes=notes)


def requirements(context: DemoContext, mismatches: tuple[str, ...]) -> s.RequirementsOut:
    from app.duty_allocation.requirements import calculate_requirements
    inputs = context.inputs
    calc = calculate_requirements(inputs.timetable.draft, inputs.policies.staffing)
    lookup = Lookup(context)
    students = {(x.session_id, r.venue_id): len(r.seats) for x in inputs.timetable.draft.sessions for r in x.rooms}
    items = [s.RequirementOut(session_id=r.session_id, venue_id=r.venue_id,
                              module_code=lookup.sessions[r.session_id].module_code,
                              students=students.get((r.session_id, r.venue_id), 0), supervisors=r.supervisors,
                              invigilators=r.invigilators) for r in calc.requirements]
    return s.RequirementsOut(
        period_code=context.period_code, policy_source=calc.policy_source, complete=calc.is_complete, items=items,
        total_supervisors=sum(i.supervisors for i in items), total_invigilators=sum(i.invigilators for i in items),
        violations=violations(calc.violations), stored_example_mismatches=list(mismatches), meta=meta(context))
