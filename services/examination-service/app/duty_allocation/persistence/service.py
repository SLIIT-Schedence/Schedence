"""Saved-roster lifecycle, change requests and seating drafts.

Fairness-Aware Staff Allocation for Examination Duties.

* DRAFT -> VALIDATED -> APPROVED -> PUBLISHED; REJECTED and SUPERSEDED are terminal.
* A roster is re-validated against freshly rebuilt inputs at VALIDATE and again at APPROVE;
  hard-constraint violations (or changed inputs) block both. PUBLISH needs a non-synthetic
  roster and a verified published timetable that the roster is compatible with; the
  default provider fails closed, so nothing is ever published on an invented reference.
* A PUBLISHED roster is never edited: changes go through a revision (a new DRAFT version).
* Swaps / replacements: request -> approve | reject -> apply. Apply is one transaction that
  re-checks the roster version, the inputs and the proposal, so a stale request cannot apply.
* Every action is appended to the audit trail (including refusals).

Callers have already checked permissions; ``actor_id`` is the Identity Service user id.
"""

from __future__ import annotations

import datetime as dt
import functools
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, NoReturn, TypeVar

from app.duty_allocation.demo_service import DemoServiceError, StoredAllocation, StoredSeating
from app.duty_allocation.duty_changes import (
    ChangeProposal, ChangeStatus as ProposalStatus, check_proposal, propose_replacement, propose_swap,
)
from app.duty_allocation.fairness import AVOIDED_DAY
from app.duty_allocation.fairness_metrics import FairnessMetrics, is_avoided_day
from app.duty_allocation.orchestration import OrchestrationStatus
from app.duty_allocation.persistence.context import RosterContext, RosterContextResolver, assemble_context, violations_json
from app.duty_allocation.persistence.domain import (
    OPEN_CHANGE, WORKING, AssignmentRecord, ChangeKind, ChangeRequestRecord, ChangeStatus, EventRecord, PersistenceConflict,
    RosterRecord, RosterStatus, SeatingDraftRecord, SeatingDraftStatus, SeatRecord, can_transition,
)
from app.duty_allocation.persistence.published_timetable import (
    PublishedTimetableProvider, PublishedTimetableUnavailable, UnavailablePublishedTimetableProvider, timetable_signature,
)
from app.duty_allocation.persistence.repository import DutyRosterRepository, overlaps
from app.duty_allocation.types import DutyAssignment, DutyRole
from app.duty_allocation.validation import validate_allocation
from app.scheduling.contracts import SchedulingValidationError, Violation

T = TypeVar("T")


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _fail(status: int, code: str, message: str, **details: Any) -> NoReturn:
    raise DemoServiceError(status, code, message, details=details or None)


def guarded(fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
    """Any failure leaves nothing half-done: roll back, then report a safe, coded error."""

    @functools.wraps(fn)
    async def wrapper(self: DutyRosterService, *args: Any, **kwargs: Any) -> T:
        try:
            return await fn(self, *args, **kwargs)
        except PersistenceConflict as exc:
            await self.repo.rollback()
            status = 503 if exc.code == "PERSISTENCE_NOT_MIGRATED" else 409
            raise DemoServiceError(status, exc.code, exc.message, details=exc.details or None) from None
        except BaseException:
            await self.repo.rollback()  # harmless after a commit
            raise

    return wrapper


def metrics_json(context: RosterContext, m: FairnessMetrics | None) -> dict[str, Any]:
    """Fairness metrics as stored with a roster (readable per-staff figures, no database ids)."""
    if m is None:
        return {}
    staff, duties = context.staff, dict(m.duty_counts)
    return {"min_load": m.min_load, "max_load": m.max_load, "mean_load": m.mean_load, "std_load": m.std_load,
            "gini": m.gini, "max_duties": m.max_duties, "min_duties": m.min_duties,
            "staff_without_duties": m.staff_without_duties, "supervisor_duties_range": m.supervisor_duties_range,
            "back_to_back_pairs": m.back_to_back_pairs, "preference_breaches": m.preference_breaches,
            "staff_missing_admin_hours": list(m.missing_admin_hours),
            "per_staff": [{"staff_id": sid, "staff_number": getattr(staff.get(sid), "staff_number", None),
                           "staff_name": getattr(staff.get(sid), "full_name", None), "duties": duties.get(sid, 0),
                           "total_load": load} for sid, load in m.total_loads]}


def to_assignments(records: Sequence[AssignmentRecord]) -> tuple[DutyAssignment, ...]:
    return tuple(DutyAssignment(r.session_id, r.venue_id, r.staff_id, DutyRole(r.role)) for r in records)


def to_records(context: RosterContext, assignments: Sequence[DutyAssignment]) -> list[AssignmentRecord]:
    sessions, staff = {s.session_id: s for s in context.data.timetable.sessions}, context.staff
    out = []
    for a in assignments:
        s = sessions[a.session_id]
        out.append(AssignmentRecord(a.session_id, a.venue_id, a.staff_id, a.role.value,
                                    getattr(staff.get(a.staff_id), "staff_number", None), s.module_code, s.start, s.end))
    return sorted(out, key=lambda r: (r.session_id, r.venue_id, r.role, r.staff_id))


def find_violations(context: RosterContext, roster: Sequence[DutyAssignment]) -> list[Violation]:
    """Independent hard-constraint check (staffing, overlaps, breaks, availability, rank, restrictions, limits)."""
    try:
        found = list(validate_allocation(context.data, context.eligibility, context.limits, tuple(roster)))
    except SchedulingValidationError as exc:
        found = list(exc.violations)
    if context.fairness is not None and context.fairness[1].hard_avoid_days:
        sessions = {s.session_id: s for s in context.data.timetable.sessions}
        found += [Violation(AVOIDED_DAY, f"Staff {a.staff_id} works {a.session_id} on an avoided day.") for a in roster
                  if a.session_id in sessions and is_avoided_day(a.staff_id, sessions[a.session_id].start.date(), context.fairness[0])]
    return found


@dataclass(frozen=True)
class ChangeSpec:
    """A swap (two duties) or an emergency replacement (one duty)."""
    kind: ChangeKind
    first: DutyAssignment
    second: DutyAssignment | None = None
    reason: str = ""
    unavailable: tuple[dt.datetime, dt.datetime] | None = None
    preferred_staff_id: str | None = None

    def parameters(self) -> dict[str, Any]:
        duty = lambda a: None if a is None else {"session_id": a.session_id, "venue_id": a.venue_id,  # noqa: E731
                                                  "staff_id": a.staff_id, "role": a.role.value}
        return {"first": duty(self.first), "second": duty(self.second), "reason": self.reason,
                "unavailable": None if self.unavailable is None else [t.isoformat() for t in self.unavailable],
                "preferred_staff_id": self.preferred_staff_id}

    @classmethod
    def from_parameters(cls, kind: ChangeKind, p: dict[str, Any]) -> ChangeSpec:
        duty = lambda d: None if d is None else DutyAssignment(d["session_id"], d["venue_id"], d["staff_id"], DutyRole(d["role"]))  # noqa: E731
        window = None if p.get("unavailable") is None else tuple(dt.datetime.fromisoformat(t) for t in p["unavailable"])
        return cls(kind, duty(p["first"]), duty(p.get("second")), p.get("reason", ""), window, p.get("preferred_staff_id"))


class DutyRosterService:
    def __init__(self, repo: DutyRosterRepository, resolver: RosterContextResolver, institution_id: uuid.UUID, *,
                 published: PublishedTimetableProvider | None = None, clock: Callable[[], dt.datetime] = _now) -> None:
        self.repo, self.resolver, self.institution_id = repo, resolver, institution_id
        self.published = published or UnavailablePublishedTimetableProvider()
        self._clock = clock

    # --- helpers -----------------------------------------------------------------------------------

    async def _roster(self, roster_id: uuid.UUID, *, lock: bool = False) -> RosterRecord:
        roster = await self.repo.get_roster(roster_id, self.institution_id, lock=lock)
        if roster is None:
            _fail(404, "ROSTER_NOT_FOUND", "No such duty roster.")
        return roster

    @staticmethod
    def _expect(roster: RosterRecord, expected_version: int | None) -> None:
        if expected_version is not None and expected_version != roster.row_version:
            _fail(409, "STALE_ROSTER", "The roster was changed after you loaded it; reload and try again.",
                  current_version=roster.row_version, expected_version=expected_version)

    async def _event(self, roster: RosterRecord, action: str, actor: uuid.UUID, to_status: RosterStatus | None = None, *,
                     reason: str | None = None, **details: Any) -> None:
        await self.repo.add_event(roster.roster_id, action, roster.status.value, (to_status or roster.status).value, actor,
                                  reason, details)

    async def _refuse(self, roster: RosterRecord, action: str, actor: uuid.UUID, status: int, code: str, message: str,
                      **details: Any) -> NoReturn:
        """Record a refusal in the audit trail (kept), then answer with the error."""
        await self._event(roster, action, actor, code=code, **details)
        await self.repo.commit()
        _fail(status, code, message, **details)

    async def _current_context(self, roster: RosterRecord, actor: uuid.UUID, action: str) -> RosterContext:
        """Inputs rebuilt now; refused when they differ from those the roster was built on."""
        try:
            context = await self.resolver.resolve(roster.period_code)
        except SchedulingValidationError as exc:
            _fail(422, "ROSTER_INPUTS_INVALID", "The current inputs are invalid.", violations=violations_json(exc.violations))
        if context.digest != roster.input_digest:
            await self._refuse(roster, action, actor, 409, "ROSTER_INPUT_CHANGED",
                               "The timetable, staff or constraints changed since this roster was made; create a revision.",
                               saved_digest=roster.input_digest, current_digest=context.digest)
        return context

    async def _staff_conflicts(self, period_code: str, records: Sequence[AssignmentRecord]) -> list[dict[str, Any]]:
        """Duties of the same people in OTHER periods' working or published rosters that overlap in time."""
        by_staff: dict[str, list[AssignmentRecord]] = defaultdict(list)
        for r in records:
            by_staff[r.staff_id].append(r)
        found = []
        for staff_id, mine in by_staff.items():
            lo, hi = min(r.starts_at for r in mine), max(r.ends_at for r in mine)
            for roster_id, other_period, other in await self.repo.overlapping_assignments(
                    self.institution_id, [staff_id], lo, hi, period_code):
                if any(overlaps(r.starts_at, r.ends_at, other.starts_at, other.ends_at) for r in mine):
                    found.append({"staff_id": staff_id, "roster_id": str(roster_id), "period_code": other_period,
                                  "session_id": other.session_id, "starts_at": other.starts_at.isoformat()})
        return found

    async def _transition(self, roster: RosterRecord, new: RosterStatus, actor: uuid.UUID, action: str, *,
                          reason: str | None = None, details: dict[str, Any] | None = None, **fields: Any) -> RosterRecord:
        if not can_transition(roster.status, new):
            _fail(409, "INVALID_TRANSITION", f"A {roster.status.value} roster cannot become {new.value}.")
        updated = await self.repo.update_roster(roster.roster_id, roster.row_version, status=new, **fields)
        await self._event(roster, action, actor, new, reason=reason, **(details or {}))
        return updated

    # --- save, read ---------------------------------------------------------------------------------

    @guarded
    async def save_draft(self, actor: uuid.UUID, run: StoredAllocation, label: str | None,
                         ) -> tuple[RosterRecord, list[AssignmentRecord]]:
        o = run.orchestration
        if o.status is not OrchestrationStatus.COMPLETED or o.allocation_input is None:
            _fail(422, "RUN_NOT_COMPLETED", f"Only a COMPLETED allocation can be saved (this one is {o.status.value}).")
        label = label or ("FAIR" if o.fair is not None else "BASELINE")
        assignments = run.roster(label)
        if assignments is None:
            _fail(409, "ROSTER_NOT_IN_RUN", f"This run has no {label} roster.")
        context = assemble_context(run.context)
        if context.data != o.allocation_input:
            _fail(409, "RUN_INPUT_MISMATCH", "The run no longer matches the current inputs; generate it again.")
        found = find_violations(context, assignments)
        if found:  # validated before saving: an invalid roster is never stored
            _fail(422, "ROSTER_INVALID", "The roster breaks hard constraints and was not saved.",
                  violations=violations_json(found))
        records = to_records(context, assignments)
        conflicts = await self._staff_conflicts(run.context.period_code, records)
        if conflicts:
            _fail(409, "STAFF_CONFLICT", "Some staff already hold overlapping duties in another active roster.", conflicts=conflicts)
        measured = o.fair_metrics if label == "FAIR" else o.baseline_metrics
        now = self._clock()
        roster = RosterRecord(
            roster_id=uuid.uuid4(), institution_id=self.institution_id, period_code=run.context.period_code,
            version=await self.repo.latest_version(self.institution_id, run.context.period_code) + 1,
            status=RosterStatus.DRAFT, row_version=1, algorithm_mode=run.mode.value, roster_label=label,
            timetable_reference=context.timetable_reference, timetable_fingerprint=context.timetable_fingerprint,
            timetable_verified=False, input_digest=context.digest, data_source=context.data_source,
            synthetic=context.synthetic, fairness_metrics=metrics_json(context, measured),
            validation={"valid": True, "violations": [], "checked_at": now.isoformat(), "assignments_checked": len(records)},
            created_by=actor, created_at=now, updated_at=now)
        await self.repo.insert_roster(roster, records)
        await self.repo.add_event(roster.roster_id, "CREATED", None, RosterStatus.DRAFT.value, actor, None,
                                  {"run_id": run.run_id, "algorithm_mode": run.mode.value, "roster_label": label,
                                   "assignments": len(records), "synthetic": context.synthetic})
        await self.repo.commit()
        return roster, records

    async def get(self, roster_id: uuid.UUID) -> tuple[RosterRecord, list[AssignmentRecord]]:
        roster = await self._roster(roster_id)
        return roster, await self.repo.get_assignments(roster_id)

    async def list(self, *, period_code: str | None, status: RosterStatus | None, limit: int, offset: int):
        return await self.repo.list_rosters(self.institution_id, period_code=period_code, status=status, limit=limit, offset=offset)

    async def history(self, roster_id: uuid.UUID) -> list[EventRecord]:
        await self._roster(roster_id)
        return await self.repo.list_events(roster_id)

    # --- lifecycle ----------------------------------------------------------------------------------

    @guarded
    async def validate(self, actor: uuid.UUID, roster_id: uuid.UUID, expected_version: int | None) -> RosterRecord:
        roster = await self._roster(roster_id, lock=True)
        self._expect(roster, expected_version)
        if roster.status not in (RosterStatus.DRAFT, RosterStatus.VALIDATED):
            _fail(409, "INVALID_TRANSITION", f"A {roster.status.value} roster cannot be validated.")
        context = await self._current_context(roster, actor, "VALIDATION_REFUSED")
        found = find_violations(context, to_assignments(await self.repo.get_assignments(roster_id)))
        now = self._clock()
        report = {"valid": not found, "violations": violations_json(found), "checked_at": now.isoformat(),
                  "assignments_checked": len(await self.repo.get_assignments(roster_id))}
        if found:
            await self.repo.update_roster(roster_id, roster.row_version, validation=report)
            await self._event(roster, "VALIDATION_FAILED", actor, violations=len(found))
            await self.repo.commit()
            _fail(422, "ROSTER_HAS_VIOLATIONS", "The roster breaks hard constraints and cannot be validated.",
                  violations=report["violations"])
        updated = await self._transition(roster, RosterStatus.VALIDATED, actor, "VALIDATED", validation=report,
                                         validated_by=actor, validated_at=now)
        await self.repo.commit()
        return updated

    @guarded
    async def approve(self, actor: uuid.UUID, roster_id: uuid.UUID, expected_version: int | None,
                      note: str | None = None) -> RosterRecord:
        roster = await self._roster(roster_id, lock=True)
        self._expect(roster, expected_version)
        if roster.status is not RosterStatus.VALIDATED:
            _fail(409, "INVALID_TRANSITION", f"Only a VALIDATED roster can be approved (this one is {roster.status.value}).")
        context = await self._current_context(roster, actor, "APPROVAL_REFUSED")
        records = await self.repo.get_assignments(roster_id)
        found = find_violations(context, to_assignments(records))
        if found:
            await self._refuse(roster, "APPROVAL_REFUSED", actor, 422, "ROSTER_HAS_VIOLATIONS",
                               "A roster with hard-constraint violations cannot be approved.",
                               violations=violations_json(found))
        now = self._clock()
        report = {"valid": True, "violations": [], "checked_at": now.isoformat(), "assignments_checked": len(records)}
        updated = await self._transition(roster, RosterStatus.APPROVED, actor, "APPROVED", reason=note, validation=report,
                                         approved_by=actor, approved_at=now)
        await self.repo.commit()
        return updated

    @guarded
    async def publish(self, actor: uuid.UUID, roster_id: uuid.UUID, expected_version: int | None) -> RosterRecord:
        roster = await self._roster(roster_id, lock=True)
        self._expect(roster, expected_version)
        if roster.status is not RosterStatus.APPROVED:
            _fail(409, "INVALID_TRANSITION", f"Only an APPROVED roster can be published (this one is {roster.status.value}).")
        if roster.synthetic:
            await self._refuse(roster, "PUBLISH_REFUSED", actor, 409, "SYNTHETIC_ROSTER_CANNOT_BE_PUBLISHED",
                               "A roster built on synthetic demo data can never be published.")
        try:
            published = await self.published.get_published(self.institution_id, roster.period_code)
        except PublishedTimetableUnavailable as exc:
            await self._refuse(roster, "PUBLISH_REFUSED", actor, 503, "PUBLISHED_TIMETABLE_UNAVAILABLE", str(exc))
        if published is None or not published.reference.strip():
            await self._refuse(roster, "PUBLISH_REFUSED", actor, 409, "PUBLISHED_TIMETABLE_NOT_FOUND",
                               "There is no published timetable for this period to check the roster against.")
        context = await self._current_context(roster, actor, "PUBLISH_REFUSED")
        problems = []
        if published.fingerprint != roster.timetable_fingerprint:
            problems.append("The published timetable has a different fingerprint than the one the roster was built on.")
        if timetable_signature(published.draft) != timetable_signature(context.data.timetable):
            problems.append("The published timetable's sessions, times or rooms differ from the roster's timetable.")
        records = await self.repo.get_assignments(roster_id)
        if not problems:
            try:
                found = find_violations(context.on_timetable(published.draft), to_assignments(records))
            except SchedulingValidationError as exc:
                found = list(exc.violations)
            problems += [f"{v.constraint}: {v.message}" for v in found]
        if problems:
            await self._refuse(roster, "PUBLISH_REFUSED", actor, 409, "TIMETABLE_INCOMPATIBLE",
                               "The roster is not compatible with the published timetable.", problems=problems[:50])
        if roster.parent_roster_id is not None:  # the previous published version steps aside first
            parent = await self.repo.get_roster(roster.parent_roster_id, self.institution_id, lock=True)
            if parent is not None and parent.status is RosterStatus.PUBLISHED:
                await self._transition(parent, RosterStatus.SUPERSEDED, actor, "SUPERSEDED",
                                       details={"by_roster_id": str(roster.roster_id)})
        updated = await self._transition(
            roster, RosterStatus.PUBLISHED, actor, "PUBLISHED", details={"timetable_reference": published.reference},
            timetable_reference=published.reference, timetable_verified=True, published_timetable_reference=published.reference,
            published_by=actor, published_at=self._clock())
        await self.repo.commit()
        return updated

    @guarded
    async def reject(self, actor: uuid.UUID, roster_id: uuid.UUID, expected_version: int | None, reason: str) -> RosterRecord:
        roster = await self._roster(roster_id, lock=True)
        self._expect(roster, expected_version)
        if roster.status not in WORKING:
            _fail(409, "INVALID_TRANSITION", f"A {roster.status.value} roster cannot be rejected"
                  + (" (create a revision instead)." if roster.status is RosterStatus.PUBLISHED else "."))
        updated = await self._transition(roster, RosterStatus.REJECTED, actor, "REJECTED", reason=reason,
                                         rejected_by=actor, rejected_at=self._clock(), rejection_reason=reason)
        for request in await self.repo.list_change_requests(roster_id):  # open requests can no longer apply
            if request.status in OPEN_CHANGE:
                await self.repo.update_change_request(request.request_id, request.status, status=ChangeStatus.STALE)
        await self.repo.commit()
        return updated

    @guarded
    async def revise(self, actor: uuid.UUID, roster_id: uuid.UUID, expected_version: int | None, reason: str,
                     ) -> tuple[RosterRecord, list[AssignmentRecord]]:
        """Controlled revision: a new DRAFT version copied from a PUBLISHED or REJECTED roster."""
        source = await self._roster(roster_id, lock=True)
        self._expect(source, expected_version)
        if source.status not in (RosterStatus.PUBLISHED, RosterStatus.REJECTED):
            _fail(409, "INVALID_TRANSITION", f"Only a PUBLISHED or REJECTED roster can be revised (this one is {source.status.value}).")
        records = await self.repo.get_assignments(roster_id)
        conflicts = await self._staff_conflicts(source.period_code, records)
        if conflicts:
            _fail(409, "STAFF_CONFLICT", "Some staff already hold overlapping duties in another active roster.", conflicts=conflicts)
        now = self._clock()
        revision = RosterRecord(
            roster_id=uuid.uuid4(), institution_id=self.institution_id, period_code=source.period_code,
            version=await self.repo.latest_version(self.institution_id, source.period_code) + 1,
            status=RosterStatus.DRAFT, row_version=1, algorithm_mode=source.algorithm_mode, roster_label=source.roster_label,
            timetable_reference=source.timetable_reference, timetable_fingerprint=source.timetable_fingerprint,
            timetable_verified=False,  # must be verified again at publication
            input_digest=source.input_digest, data_source=source.data_source, synthetic=source.synthetic,
            fairness_metrics=source.fairness_metrics, validation={"valid": None, "violations": [], "checked_at": None,
                                                                   "assignments_checked": 0},
            created_by=actor, created_at=now, updated_at=now, parent_roster_id=source.roster_id)
        await self.repo.insert_roster(revision, records)
        await self.repo.add_event(revision.roster_id, "CREATED_AS_REVISION", None, RosterStatus.DRAFT.value, actor, reason,
                                  {"parent_roster_id": str(source.roster_id), "parent_version": source.version})
        await self.repo.add_event(source.roster_id, "REVISION_CREATED", source.status.value, source.status.value, actor, reason,
                                  {"revision_roster_id": str(revision.roster_id), "revision_version": revision.version})
        await self.repo.commit()
        return revision, records

    # --- swaps and emergency replacements -----------------------------------------------------------

    def _require_changeable(self, roster: RosterRecord) -> None:
        if roster.status is RosterStatus.PUBLISHED:
            _fail(409, "REVISION_REQUIRED", "A published roster is changed only through a controlled revision: "
                  "create a revision, change it, then validate, approve and publish it again.")
        if roster.status not in WORKING:
            _fail(409, "ROSTER_NOT_ACTIVE", f"A {roster.status.value} roster cannot be changed.")

    @staticmethod
    def _propose(context: RosterContext, roster: Sequence[DutyAssignment], spec: ChangeSpec) -> ChangeProposal:
        if spec.kind is ChangeKind.SWAP:
            return propose_swap(context.data, context.eligibility, context.limits, roster, spec.first, spec.second,
                                fairness=context.fairness)
        return propose_replacement(context.data, context.eligibility, context.limits, roster, spec.first, spec.reason,
                                   unavailable=spec.unavailable, preferred_staff_id=spec.preferred_staff_id,
                                   fairness=context.fairness)

    async def preview_change(self, actor: uuid.UUID, roster_id: uuid.UUID, spec: ChangeSpec,
                             ) -> tuple[RosterRecord, ChangeProposal, tuple[Violation, ...] | None, RosterContext]:
        """Validate a proposed change against the saved roster. Changes nothing."""
        roster = await self._roster(roster_id)
        self._require_changeable(roster)
        context = await self._current_context(roster, actor, "CHANGE_PREVIEW_REFUSED")
        current = to_assignments(await self.repo.get_assignments(roster_id))
        proposal = self._propose(context, current, spec)
        rechecked = None
        if proposal.status is ProposalStatus.PROPOSED:
            rechecked = check_proposal(current, proposal, context.eligibility, context.limits, context.fairness)
        return roster, proposal, rechecked, context

    @guarded
    async def submit_change(self, actor: uuid.UUID, roster_id: uuid.UUID, expected_version: int | None, spec: ChangeSpec,
                            ) -> ChangeRequestRecord:
        roster = await self._roster(roster_id, lock=True)
        self._expect(roster, expected_version)
        roster, proposal, rechecked, context = await self.preview_change(actor, roster_id, spec)
        if proposal.status is not ProposalStatus.PROPOSED or rechecked:
            _fail(409, "CHANGE_NOT_VALID", "The change cannot be made; nothing was requested.",
                  proposal_status=proposal.status.value, reasons=list(proposal.reasons),
                  violations=violations_json(list(proposal.violations) + list(rechecked or ())))
        removed, added = to_records(context, proposal.removed), to_records(context, proposal.added)
        touched = {r.key for r in removed}
        for other in await self.repo.list_change_requests(roster_id):
            if other.status in OPEN_CHANGE and (touched & {r.key for r in other.removed}
                                                or {(a.session_id, a.staff_id) for a in added}
                                                & {(a.session_id, a.staff_id) for a in other.added}):
                _fail(409, "CONFLICTING_REQUEST", "Another open request already changes one of these duties or people.",
                      request_id=str(other.request_id))
        conflicts = await self._staff_conflicts(roster.period_code, added)
        if conflicts:
            _fail(409, "STAFF_CONFLICT", "A new assignee already holds an overlapping duty in another active roster.",
                  conflicts=conflicts)
        candidates = [{"staff_id": c.staff_id, "staff_number": c.staff_number, "duties_after": c.duties_after,
                       "objective": c.objective} for c in proposal.candidates[:5]]
        summary = {"proposal_status": proposal.status.value, "reasons": list(proposal.reasons),
                   "original_objective": proposal.original_objective, "proposed_objective": proposal.proposed_objective,
                   "original_metrics": metrics_json(context, proposal.original_metrics),
                   "proposed_metrics": metrics_json(context, proposal.proposed_metrics), "candidates": candidates}
        request = ChangeRequestRecord(
            request_id=uuid.uuid4(), roster_id=roster_id, institution_id=self.institution_id, kind=spec.kind,
            status=ChangeStatus.REQUESTED, base_row_version=roster.row_version, removed=tuple(removed), added=tuple(added),
            reason=spec.reason, parameters=spec.parameters(), summary=summary, requested_by=actor, requested_at=self._clock())
        await self.repo.insert_change_request(request)
        await self._event(roster, "CHANGE_REQUESTED", actor, reason=spec.reason, request_id=str(request.request_id),
                          kind=spec.kind.value)
        await self.repo.commit()
        return request

    async def _request(self, request_id: uuid.UUID) -> ChangeRequestRecord:
        request = await self.repo.get_change_request(request_id, self.institution_id)
        if request is None:
            _fail(404, "CHANGE_REQUEST_NOT_FOUND", "No such change request.")
        return request

    @staticmethod
    def _reject_stale(request: ChangeRequestRecord) -> None:
        if request.status is ChangeStatus.STALE:
            _fail(409, "STALE_CHANGE_REQUEST", "The roster changed after this request was made; submit it again.",
                  base_version=request.base_row_version)

    async def get_change_request(self, request_id: uuid.UUID) -> ChangeRequestRecord:
        return await self._request(request_id)

    async def list_change_requests(self, roster_id: uuid.UUID) -> list[ChangeRequestRecord]:
        await self._roster(roster_id)
        return await self.repo.list_change_requests(roster_id)

    @guarded
    async def decide_change(self, actor: uuid.UUID, request_id: uuid.UUID, approve: bool, reason: str | None,
                            ) -> ChangeRequestRecord:
        request = await self._request(request_id)
        self._reject_stale(request)
        if request.status is not ChangeStatus.REQUESTED:
            _fail(409, "INVALID_TRANSITION", f"A {request.status.value} request cannot be decided.")
        roster = await self._roster(request.roster_id, lock=True)
        if approve and request.base_row_version != roster.row_version:
            await self.repo.update_change_request(request_id, ChangeStatus.REQUESTED, status=ChangeStatus.STALE)
            await self._event(roster, "CHANGE_STALE", actor, request_id=str(request_id))
            await self.repo.commit()
            _fail(409, "STALE_CHANGE_REQUEST", "The roster changed after this request was made; submit it again.",
                  base_version=request.base_row_version, current_version=roster.row_version)
        status = ChangeStatus.APPROVED if approve else ChangeStatus.REJECTED
        updated = await self.repo.update_change_request(request_id, ChangeStatus.REQUESTED, status=status, decided_by=actor,
                                                        decided_at=self._clock(), decision_reason=reason)
        await self._event(roster, "CHANGE_APPROVED" if approve else "CHANGE_REJECTED", actor, reason=reason,
                          request_id=str(request_id))
        await self.repo.commit()
        return updated

    @guarded
    async def cancel_change(self, actor: uuid.UUID, request_id: uuid.UUID, reason: str | None, *,
                            may_cancel_any: bool) -> ChangeRequestRecord:
        request = await self._request(request_id)
        if request.status not in OPEN_CHANGE:
            _fail(409, "INVALID_TRANSITION", f"A {request.status.value} request cannot be cancelled.")
        if request.requested_by != actor and not may_cancel_any:
            _fail(403, "NOT_YOUR_REQUEST", "Only the requester (or a manager) can cancel this request.")
        roster = await self._roster(request.roster_id)
        updated = await self.repo.update_change_request(request_id, request.status, status=ChangeStatus.CANCELLED,
                                                        decided_by=actor, decided_at=self._clock(), decision_reason=reason)
        await self._event(roster, "CHANGE_CANCELLED", actor, reason=reason, request_id=str(request_id))
        await self.repo.commit()
        return updated

    @guarded
    async def apply_change(self, actor: uuid.UUID, request_id: uuid.UUID) -> tuple[ChangeRequestRecord, RosterRecord]:
        """Apply an APPROVED change atomically: the roster version, inputs and proposal are all re-checked."""
        request = await self._request(request_id)
        self._reject_stale(request)
        if request.status is not ChangeStatus.APPROVED:
            _fail(409, "INVALID_TRANSITION", f"Only an APPROVED request can be applied (this one is {request.status.value}).")
        roster = await self._roster(request.roster_id, lock=True)
        self._require_changeable(roster)
        if request.base_row_version != roster.row_version:
            await self.repo.update_change_request(request_id, ChangeStatus.APPROVED, status=ChangeStatus.STALE)
            await self._event(roster, "CHANGE_STALE", actor, request_id=str(request_id))
            await self.repo.commit()
            _fail(409, "STALE_CHANGE_REQUEST", "The roster changed after this request was approved; submit it again.",
                  base_version=request.base_row_version, current_version=roster.row_version)
        context = await self._current_context(roster, actor, "CHANGE_REFUSED")
        current = to_assignments(await self.repo.get_assignments(roster.roster_id))
        proposal = self._propose(context, current, ChangeSpec.from_parameters(request.kind, request.parameters))
        removed, added = to_records(context, proposal.removed), to_records(context, proposal.added)
        same = {r.key for r in removed} == {r.key for r in request.removed} and {a.key for a in added} == {a.key for a in request.added}
        problems = list(check_proposal(current, proposal, context.eligibility, context.limits, context.fairness)) \
            if proposal.status is ProposalStatus.PROPOSED else [Violation("D_CHANGE", "; ".join(proposal.reasons))]
        if not same or problems:
            await self.repo.update_change_request(request_id, ChangeStatus.APPROVED, status=ChangeStatus.STALE)
            await self._event(roster, "CHANGE_REFUSED", actor, request_id=str(request_id))
            await self.repo.commit()
            _fail(409, "CHANGE_NO_LONGER_VALID", "The change no longer gives the approved result; nothing was changed.",
                  violations=violations_json(problems))
        conflicts = await self._staff_conflicts(roster.period_code, added)
        if conflicts:
            _fail(409, "STAFF_CONFLICT", "A new assignee already holds an overlapping duty in another active roster.",
                  conflicts=conflicts)
        await self.repo.apply_assignment_change(roster.roster_id, request.removed, added)
        now = self._clock()
        after = to_assignments(await self.repo.get_assignments(roster.roster_id))
        found_after = find_violations(context, after)
        report = {"valid": not found_after, "violations": violations_json(found_after), "checked_at": now.isoformat(),
                  "assignments_checked": len(after)}
        fields: dict[str, Any] = dict(validation=report, fairness_metrics=metrics_json(context, proposal.proposed_metrics))
        new_status = roster.status
        if roster.status is RosterStatus.APPROVED:  # an approval never carries over a changed roster
            new_status = RosterStatus.VALIDATED
            fields.update(approved_by=None, approved_at=None)
        updated = await self.repo.update_roster(roster.roster_id, roster.row_version, status=new_status, **fields)
        applied = await self.repo.update_change_request(request_id, ChangeStatus.APPROVED, status=ChangeStatus.APPLIED,
                                                        applied_by=actor, applied_at=now)
        stale = await self.repo.mark_open_requests_stale(roster.roster_id, request_id)
        await self._event(roster, "CHANGE_APPLIED", actor, new_status, request_id=str(request_id), kind=request.kind.value,
                          withdrawn_approval=roster.status is RosterStatus.APPROVED, requests_made_stale=stale)
        await self.repo.commit()
        return applied, updated

    # --- seating drafts ----------------------------------------------------------------------------

    @guarded
    async def save_seating(self, actor: uuid.UUID, stored: StoredSeating) -> tuple[SeatingDraftRecord, list[SeatRecord]]:
        seating = stored.result.seating
        plan = seating.plan
        if plan is None or seating.status.value != "COMPLETE":
            _fail(422, "SEATING_NOT_COMPLETE", f"Only a COMPLETE seating plan can be saved (this one is {seating.status.value}).")
        draft = stored.context.inputs.timetable.draft
        logical = {(s.session_id, r.venue_id, seat.student_id): seat.seat_number
                   for s in draft.sessions for r in s.rooms for seat in r.seats}
        synthetic_halls, provisional_halls = set(plan.synthetic_halls), set(plan.provisional_halls)
        seats = []
        for a in plan.assignments:
            if a.venue_id in synthetic_halls:
                status = "SYNTHETIC"
            elif a.venue_id in provisional_halls or not a.seat_verified:
                status = "PROVISIONAL"
            else:
                status = "VERIFIED"  # only reachable for a hall whose layout the source marked VERIFIED
            if a.seat_verified and status != "VERIFIED":
                _fail(409, "SEAT_VERIFICATION_MISMATCH", "A seat is marked verified in a hall without a verified layout.")
            seats.append(SeatRecord(a.session_id, a.venue_id, a.student_id, a.module_code,
                                    logical[(a.session_id, a.venue_id, a.student_id)], a.seat_number, a.seat_label, status))
        states = {s.verification_status for s in seats}
        state = "SYNTHETIC" if "SYNTHETIC" in states else "PROVISIONAL" if "PROVISIONAL" in states else "VERIFIED"
        record = SeatingDraftRecord(
            draft_id=uuid.uuid4(), institution_id=self.institution_id, period_code=stored.context.period_code,
            version=await self.repo.latest_seating_version(self.institution_id, stored.context.period_code) + 1,
            status=SeatingDraftStatus.DRAFT, timetable_reference=draft.input_fingerprint,
            data_source=stored.context.snapshot.data_source.value, synthetic=True, seat_state=state,
            capacity_rules_source=plan.capacity_rules_source,
            summary={"assignments": len(seats), "rooms": len(seating.rooms),
                     "synthetic_halls": sorted(synthetic_halls), "provisional_halls": sorted(provisional_halls),
                     "all_seats_verified": plan.all_seats_verified},
            created_by=actor, created_at=self._clock())
        await self.repo.insert_seating_draft(record, seats)
        await self.repo.commit()
        return record, seats

    async def get_seating(self, draft_id: uuid.UUID) -> tuple[SeatingDraftRecord, list[SeatRecord]]:
        draft = await self.repo.get_seating_draft(draft_id, self.institution_id)
        if draft is None:
            _fail(404, "SEATING_DRAFT_NOT_FOUND", "No such seating draft.")
        return draft, await self.repo.get_seats(draft_id)

    async def list_seating(self, *, period_code: str | None, limit: int, offset: int):
        return await self.repo.list_seating_drafts(self.institution_id, period_code=period_code, limit=limit, offset=offset)
