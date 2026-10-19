"""Staff duty replacement and swap proposals for an existing, validated duty roster.

Nothing is applied: every function returns a ChangeProposal with the proposed roster, the
exact assignments removed and added, and (optionally) Step 5 fairness metrics before and
after. Only the requested duties change; every proposed roster must pass the independent
Step 4 validator (staffing, overlaps, minimum break, availability, rank, restrictions,
duty limits) before it is offered. With a fairness policy whose hard_avoid_days is set,
no new assignment may fall on that person's AVOID day (D11, as in Step 5).

Replacement: the absent staff member is marked unavailable for the duty's session (or a
longer window), then every other staff member is tried in their place. Rank, restriction
and availability reasons come from build_eligibility(); everything else from the validator.
Valid candidates are ranked by the Step 5 weighted fairness objective when a fairness
policy is given, otherwise by fewest duties.
Swap: two staff members exchange their duties (rooms and roles).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass, replace
from enum import Enum

from app.duty_allocation.allocation_policy import DutyLimitPolicy
from app.duty_allocation.eligibility import build_eligibility
from app.duty_allocation.eligibility_policy import EligibilityPolicy
from app.duty_allocation.fairness import AVOIDED_DAY, fairness_input_errors
from app.duty_allocation.fairness_metrics import FairnessMetrics, fairness_objective, is_avoided_day, measure_fairness
from app.duty_allocation.fairness_policy import DutyFairnessPolicy, FairnessInputs
from app.duty_allocation.types import DUTY_INPUT, DutyAllocationInput, DutyAssignment, StaffUnavailability
from app.duty_allocation.validation import validate_allocation
from app.scheduling.contracts import SchedulingValidationError, Violation

Fairness = tuple[FairnessInputs, DutyFairnessPolicy]


class ChangeStatus(str, Enum):
    PROPOSED = "PROPOSED"  # a valid proposal exists; it has NOT been applied
    INFEASIBLE = "INFEASIBLE"  # no change satisfies the hard constraints; see reasons
    INVALID_REQUEST = "INVALID_REQUEST"  # the request or the original roster is not usable


@dataclass(frozen=True)
class Candidate:
    staff_id: str
    staff_number: str
    duties_after: int  # this person's duties in the proposed roster
    metrics: FairnessMetrics | None  # whole-roster fairness if this candidate is chosen
    objective: int | None = None  # Step 5 weighted fairness objective (lower is fairer)


@dataclass(frozen=True)
class RejectedCandidate:
    staff_id: str
    staff_number: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class ChangeProposal:
    kind: str  # "REPLACEMENT" or "SWAP"
    status: ChangeStatus
    removed: tuple[DutyAssignment, ...] = ()
    added: tuple[DutyAssignment, ...] = ()
    proposed_roster: tuple[DutyAssignment, ...] | None = None
    proposed_data: DutyAllocationInput | None = None  # input including the new unavailability
    candidates: tuple[Candidate, ...] = ()  # valid replacements, best first
    rejected: tuple[RejectedCandidate, ...] = ()
    violations: tuple[Violation, ...] = ()
    reasons: tuple[str, ...] = ()
    original_metrics: FairnessMetrics | None = None
    proposed_metrics: FairnessMetrics | None = None
    original_objective: int | None = None
    proposed_objective: int | None = None
    applied: bool = False  # always False: proposals are never applied automatically


def _invalid(kind: str, *reasons: str) -> ChangeProposal:
    return ChangeProposal(kind, ChangeStatus.INVALID_REQUEST, reasons=tuple(reasons))


def _as_roster(value: object) -> tuple | None:
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        return None
    try:
        return tuple(value)
    except Exception:
        return None


def _check_request(data, eligibility, limits, roster, fairness, duties) -> tuple[str, ...]:
    if type(data) is not DutyAllocationInput or type(eligibility) is not EligibilityPolicy \
            or type(limits) is not DutyLimitPolicy:
        return ("Needs a DutyAllocationInput, EligibilityPolicy and DutyLimitPolicy.",)
    if roster is None:
        return ("Roster must be an ordered collection of DutyAssignment values.",)
    if fairness is not None:
        if not isinstance(fairness, tuple) or len(fairness) != 2:
            return ("fairness must be (FairnessInputs, DutyFairnessPolicy) or None.",)
        errors = fairness_input_errors(data, fairness[0], fairness[1])  # same checks as Step 5
        if errors:
            return tuple(v.message for v in errors)
    if any(type(d) is not DutyAssignment for d in (*roster, *duties)):
        return ("Roster and requested duties must be DutyAssignment values.",)
    problems = validate_allocation(data, eligibility, limits, roster)
    if problems:
        return ("The original roster is not valid; fix it before requesting changes.",
                *(f"{v.constraint}: {v.message}" for v in problems))
    missing = [d for d in duties if d not in roster]
    return tuple(f"Duty {d.session_id}/{d.venue_id} {d.role.value} for {d.staff_id} is not in the roster." for d in missing)


def _avoided_day_violations(data: DutyAllocationInput, fairness: Fairness | None,
                            added: Iterable[DutyAssignment]) -> tuple[Violation, ...]:
    """Step 5's opt-in hard rule, applied to the assignments a proposal adds."""
    if fairness is None or not fairness[1].hard_avoid_days:
        return ()
    sessions = {s.session_id: s for s in data.timetable.sessions}
    return tuple(Violation(AVOIDED_DAY, f"Staff {a.staff_id} would work {a.session_id} on an avoided day.")
                 for a in added if is_avoided_day(a.staff_id, sessions[a.session_id].start.date(), fairness[0]))


def _metrics(data, fairness, roster) -> FairnessMetrics | None:
    return None if fairness is None else measure_fairness(data, fairness[0], fairness[1], roster)


def _objective(data, fairness, roster) -> int | None:
    return None if fairness is None else fairness_objective(data, fairness[0], fairness[1], roster)


def unrelated_changes(original, proposed, removed, added) -> tuple[DutyAssignment, ...]:
    """Assignments that differ between rosters but are not part of the requested change."""
    expected = (set(original) - set(removed)) | set(added)
    return tuple(sorted(set(proposed) ^ expected, key=lambda a: (a.session_id, a.venue_id, a.role.value, a.staff_id)))


def propose_replacement(data: DutyAllocationInput, eligibility: EligibilityPolicy, limits: DutyLimitPolicy,
                        roster: Iterable[DutyAssignment], duty: DutyAssignment, reason: str, *,
                        unavailable: tuple[dt.datetime, dt.datetime] | None = None,
                        preferred_staff_id: str | None = None,
                        fairness: Fairness | None = None) -> ChangeProposal:
    """Replace duty.staff_id on this duty. unavailable defaults to the duty's session time."""
    kind = "REPLACEMENT"
    roster = _as_roster(roster)
    problems = _check_request(data, eligibility, limits, roster, fairness, (duty,))
    if problems:
        return _invalid(kind, *problems)
    session = next(s for s in data.timetable.sessions if s.session_id == duty.session_id)
    if unavailable is None:
        window = (session.start, session.end)
    elif isinstance(unavailable, tuple) and len(unavailable) == 2:
        window = unavailable
    else:
        return _invalid(kind, "unavailable must be a (start, end) pair of datetimes or None.")
    try:
        absence = StaffUnavailability(duty.staff_id, window[0], window[1], reason)
        proposed_data = replace(data, unavailability=data.unavailability + (absence,))
    except SchedulingValidationError as exc:
        return _invalid(kind, *(v.message for v in exc.violations))
    if not (absence.start <= session.start and session.end <= absence.end):
        return _invalid(kind, "The unavailable window must cover the whole duty session.")
    if preferred_staff_id is not None and type(preferred_staff_id) is not str:
        return _invalid(kind, "preferred_staff_id must be a staff id string or None.")

    rest = tuple(a for a in roster if a != duty)
    sessions = {s.session_id: s for s in data.timetable.sessions}
    also_hit = [a for a in rest if a.staff_id == duty.staff_id
                and absence.overlaps(sessions[a.session_id].start, sessions[a.session_id].end)]
    if also_hit:
        return ChangeProposal(kind, ChangeStatus.INFEASIBLE, proposed_data=proposed_data,
                              reasons=("The unavailable window also covers other duties of this staff member; "
                                       "request a replacement for each of them:",
                                       *(f"{a.session_id}/{a.venue_id} {a.role.value}" for a in also_hit)))

    room = build_eligibility(proposed_data, eligibility).room(duty.session_id, duty.venue_id)
    eligible = set(room.staff_for(duty.role))
    excluded = {e.staff_id: e.reason for e in room.exclusions if e.role is duty.role}
    staff = {m.staff_id: m for m in data.staff}
    candidates, rejected, valid_rosters = [], [], {}
    for member in sorted(data.staff, key=lambda m: m.staff_id):
        if member.staff_id == duty.staff_id:
            continue
        if member.staff_id not in eligible:
            rejected.append(RejectedCandidate(member.staff_id, member.staff_number, (excluded[member.staff_id],)))
            continue
        new_duty = replace(duty, staff_id=member.staff_id)
        trial = rest + (new_duty,)
        violations = validate_allocation(proposed_data, eligibility, limits, trial)
        violations += _avoided_day_violations(proposed_data, fairness, (new_duty,))
        if violations:
            rejected.append(RejectedCandidate(member.staff_id, member.staff_number,
                                              tuple(f"{v.constraint}: {v.message}" for v in violations)))
            continue
        valid_rosters[member.staff_id] = trial
        candidates.append(Candidate(member.staff_id, member.staff_number,
                                    sum(1 for a in trial if a.staff_id == member.staff_id),
                                    _metrics(proposed_data, fairness, trial), _objective(proposed_data, fairness, trial)))
    candidates.sort(key=lambda c: (c.objective if c.objective is not None else 0, c.duties_after, c.staff_id))
    before = dict(original_metrics=_metrics(data, fairness, roster), original_objective=_objective(data, fairness, roster))
    if not candidates:
        return ChangeProposal(kind, ChangeStatus.INFEASIBLE, rejected=tuple(rejected), proposed_data=proposed_data,
                              reasons=(f"No staff member can take {duty.role.value} in {duty.session_id}/"
                                       f"{duty.venue_id} without breaking a hard constraint.",), **before)
    if preferred_staff_id is not None and preferred_staff_id not in valid_rosters:
        why = next((r.reasons for r in rejected if r.staff_id == preferred_staff_id), ("unknown staff member",))
        return ChangeProposal(kind, ChangeStatus.INFEASIBLE, candidates=tuple(candidates), rejected=tuple(rejected),
                              proposed_data=proposed_data,
                              reasons=(f"Preferred replacement {preferred_staff_id} is not valid.", *why), **before)
    chosen = preferred_staff_id or candidates[0].staff_id
    proposed = valid_rosters[chosen]
    return ChangeProposal(kind, ChangeStatus.PROPOSED, (duty,), (replace(duty, staff_id=chosen),), proposed,
                          proposed_data, tuple(candidates), tuple(rejected),
                          proposed_metrics=_metrics(proposed_data, fairness, proposed),
                          proposed_objective=_objective(proposed_data, fairness, proposed),
                          reasons=(f"{staff[chosen].staff_number} replaces {staff[duty.staff_id].staff_number} as "
                                   f"{duty.role.value} in {duty.session_id}/{duty.venue_id}.",), **before)


def propose_swap(data: DutyAllocationInput, eligibility: EligibilityPolicy, limits: DutyLimitPolicy,
                 roster: Iterable[DutyAssignment], first: DutyAssignment, second: DutyAssignment, *,
                 fairness: Fairness | None = None) -> ChangeProposal:
    """first.staff_id takes second's duty and second.staff_id takes first's duty."""
    kind = "SWAP"
    roster = _as_roster(roster)
    problems = _check_request(data, eligibility, limits, roster, fairness, (first, second))
    if not problems and first.staff_id == second.staff_id:
        problems = ("A swap needs two different staff members.",)
    if problems:
        return _invalid(kind, *problems)
    added = (replace(second, staff_id=first.staff_id), replace(first, staff_id=second.staff_id))
    proposed = tuple(a for a in roster if a not in (first, second)) + added
    before = dict(original_metrics=_metrics(data, fairness, roster), original_objective=_objective(data, fairness, roster))
    violations = validate_allocation(data, eligibility, limits, proposed) + _avoided_day_violations(data, fairness, added)
    if violations:
        return ChangeProposal(kind, ChangeStatus.INFEASIBLE, violations=violations,
                              reasons=("The swap would break hard constraints:",
                                       *(f"{v.constraint}: {v.message}" for v in violations)), **before)
    return ChangeProposal(kind, ChangeStatus.PROPOSED, (first, second), added, proposed, data,
                          proposed_metrics=_metrics(data, fairness, proposed),
                          proposed_objective=_objective(data, fairness, proposed),
                          reasons=(f"{first.staff_id} and {second.staff_id} exchange duties.",), **before)


def check_proposal(original: Iterable[DutyAssignment], proposal: ChangeProposal, eligibility: EligibilityPolicy,
                   limits: DutyLimitPolicy, fairness: Fairness | None = None) -> tuple[Violation, ...]:
    """Independent re-check of a PROPOSED change: valid roster, no added duty on a hard
    AVOID day (when configured), and nothing else changed."""
    original = _as_roster(original)
    if original is None or type(proposal) is not ChangeProposal or proposal.status is not ChangeStatus.PROPOSED \
            or proposal.proposed_roster is None or proposal.proposed_data is None:
        return (Violation(DUTY_INPUT, "Only a PROPOSED change with a roster can be checked."),)
    errors = list(validate_allocation(proposal.proposed_data, eligibility, limits, proposal.proposed_roster))
    errors += _avoided_day_violations(proposal.proposed_data, fairness, proposal.added)
    errors += [Violation("D12", f"Unrequested change to {a.session_id}/{a.venue_id} {a.role.value} {a.staff_id}.")
               for a in unrelated_changes(original, proposal.proposed_roster, proposal.removed, proposal.added)]
    return tuple(errors)
