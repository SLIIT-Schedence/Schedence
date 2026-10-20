"""DutyRosterService: atomicity, concurrency, validation before saving and input fidelity (no API, no database)."""

from __future__ import annotations

import dataclasses
import datetime as dt
import uuid

import pytest

pytest.importorskip("ortools", reason="install the examination service [solver] extra")

from app.duty_allocation.demo_service import DemoServiceError, Member3DemoService
from app.duty_allocation.demo_settings import PersistenceMode
from app.duty_allocation.orchestration import AllocationMode
from app.duty_allocation.persistence import service as svc
from app.duty_allocation.persistence.context import DemoRosterContextResolver, assemble_context, inputs_digest
from app.duty_allocation.persistence.domain import ChangeKind, RosterStatus
from app.duty_allocation.persistence.published_timetable import (
    PublishedTimetableUnavailable, UnavailablePublishedTimetableProvider, timetable_signature,
)
from app.duty_allocation.persistence.repository import MemoryDutyRosterRepository, MemoryState
from app.duty_allocation.persistence.service import ChangeSpec, DutyRosterService, to_assignments
from app.duty_allocation.types import DutyAssignment, DutyRole
from duty_allocation_demo_support import PERIOD
from test_duty_allocation_api import AUTH_INSTITUTION, fake_source, local_settings

ACTOR = uuid.uuid4()


async def build(change=None):
    demo = Member3DemoService(local_settings(persistence=PersistenceMode.MEMORY), app_env="testing",
                              real_institution_id=AUTH_INSTITUTION, row_source=fake_source(change))
    state = MemoryState()

    def service() -> DutyRosterService:
        return DutyRosterService(MemoryDutyRosterRepository(state), DemoRosterContextResolver(demo), AUTH_INSTITUTION)

    run = await demo.allocate(AllocationMode.FAIR, PERIOD, 10)
    return demo, state, service, run


async def test_rebuilt_inputs_match_exactly_what_the_allocator_used():
    demo, _, _, run = await build()
    context = assemble_context(run.context)
    o = run.orchestration
    assert context.data == o.allocation_input  # guards against drift from plan_duty_allocation
    policies, sup = run.context.inputs.policies, run.context.inputs.supplemental
    assert context.eligibility == dataclasses.replace(policies.eligibility, restrictions=policies.eligibility.restrictions + sup.restrictions)
    assert context.limits == policies.limits and context.synthetic is True and context.data_source == "LOCAL_DEMO"
    assert context.timetable_reference.startswith("DEMO-UNPUBLISHED:")


async def test_digest_is_stable_and_notices_changed_inputs():
    _, _, _, run = await build()
    c = assemble_context(run.context)
    assert c.digest == inputs_digest(c.data, c.eligibility, c.limits) and len(c.digest) == 64
    _, _, _, other = await build(lambda t: t["staff_unavailability"].append(dict(
        unavailability_id="U9", staff_id="00000000-0000-4000-8000-000000000004",
        start_at=dt.datetime(2026, 11, 16, 7), end_at=dt.datetime(2026, 11, 16, 18), reason="x", source="S")))
    assert assemble_context(other.context).digest != c.digest
    _, _, _, same = await build()
    assert assemble_context(same.context).digest == c.digest


async def test_a_roster_is_validated_before_it_is_saved(monkeypatch):
    _, state, service, run = await build()
    monkeypatch.setattr(svc, "find_violations", lambda ctx, roster: [svc.Violation("D1", "forced for the test")])
    with pytest.raises(DemoServiceError) as refused:
        await service().save_draft(ACTOR, run, None)
    assert refused.value.code == "ROSTER_INVALID" and refused.value.details["violations"][0]["constraint"] == "D1"
    assert state.data.rosters == {} and state.data.events == []  # nothing was stored


async def test_save_records_creator_timestamps_and_the_initial_audit_event():
    _, state, service, run = await build()
    roster, records = await service().save_draft(ACTOR, run, "FAIR")
    assert roster.created_by == ACTOR and roster.created_at.tzinfo is not None and roster.status is RosterStatus.DRAFT
    events = await service().history(roster.roster_id)
    assert [(e.action, e.actor_id) for e in events] == [("CREATED", ACTOR)] and events[0].details["run_id"] == run.run_id
    assert {r.staff_id for r in records} <= {m.staff_id for m in run.orchestration.allocation_input.staff}
    assert all(r.ends_at > r.starts_at and r.module_code for r in records)


async def swap_request(service, run):
    roster, records = await service().save_draft(ACTOR, run, None)
    swaps = [(a, b) for a in records for b in records
             if a.role == b.role == "INVIGILATOR" and a.staff_id < b.staff_id]
    for a, b in swaps:
        spec = ChangeSpec(ChangeKind.SWAP, DutyAssignment(a.session_id, a.venue_id, a.staff_id, DutyRole(a.role)),
                          DutyAssignment(b.session_id, b.venue_id, b.staff_id, DutyRole(b.role)), "Swap")
        try:
            return roster, await service().submit_change(ACTOR, roster.roster_id, None, spec)
        except DemoServiceError:
            continue
    raise AssertionError("no valid swap")


async def test_apply_is_atomic_when_something_fails_midway(monkeypatch):
    _, state, service, run = await build()
    roster, request = await swap_request(service, run)
    await service().decide_change(ACTOR, request.request_id, True, None)
    before_assignments = list(state.data.assignments[roster.roster_id])
    before_events = len(state.data.events)

    original = MemoryDutyRosterRepository.add_event

    async def failing(self, roster_id, action, *args, **kwargs):
        if action == "CHANGE_APPLIED":
            raise RuntimeError("disk failure")
        return await original(self, roster_id, action, *args, **kwargs)

    monkeypatch.setattr(MemoryDutyRosterRepository, "add_event", failing)
    with pytest.raises(RuntimeError):
        await service().apply_change(ACTOR, request.request_id)
    monkeypatch.undo()
    assert state.data.assignments[roster.roster_id] == before_assignments  # roster untouched
    assert state.data.rosters[roster.roster_id].row_version == roster.row_version
    assert state.data.requests[request.request_id].status.value == "APPROVED" and len(state.data.events) == before_events
    applied, updated = await service().apply_change(ACTOR, request.request_id)  # and it can still be applied afterwards
    assert applied.status.value == "APPLIED" and updated.row_version == roster.row_version + 1


async def test_two_users_acting_at_once_cannot_both_succeed():
    _, state, service, run = await build()
    roster, _ = await service().save_draft(ACTOR, run, None)
    a, b = service(), service()  # two requests, each with its own repository (unit of work)
    first = await a.validate(ACTOR, roster.roster_id, 1)
    assert first.status is RosterStatus.VALIDATED
    with pytest.raises(DemoServiceError) as second:
        await b.reject(uuid.uuid4(), roster.roster_id, 1, "too late")  # b still believes version 1
    assert second.value.code == "STALE_ROSTER" or second.value.code == "CONCURRENT_CHANGE"
    assert state.data.rosters[roster.roster_id].status is RosterStatus.VALIDATED


async def test_interleaved_commits_are_detected_as_concurrent_changes():
    _, state, service, run = await build()
    roster, _ = await service().save_draft(ACTOR, run, None)
    a, b = service(), service()
    await a.repo.get_roster(roster.roster_id, AUTH_INSTITUTION)
    await b.reject(ACTOR, roster.roster_id, 1, "first")  # b commits while a is still working on its copy
    with pytest.raises(DemoServiceError) as clash:
        await a.validate(ACTOR, roster.roster_id, 1)
    assert clash.value.code == "CONCURRENT_CHANGE"
    assert state.data.rosters[roster.roster_id].status is RosterStatus.REJECTED


async def test_publishing_fails_closed_by_default_and_provider_is_replaceable():
    with pytest.raises(PublishedTimetableUnavailable):
        await UnavailablePublishedTimetableProvider().get_published(AUTH_INSTITUTION, PERIOD)
    demo, _, service, _ = await build()
    assert isinstance(service().published, UnavailablePublishedTimetableProvider)
    context = await demo.context(PERIOD)
    draft = context.inputs.timetable.draft
    assert timetable_signature(draft) == timetable_signature(draft) and len(timetable_signature(draft)) == 3
    shifted = dataclasses.replace(draft, sessions=tuple(
        dataclasses.replace(s, start=s.start + dt.timedelta(minutes=30), end=s.end + dt.timedelta(minutes=30)) for s in draft.sessions))
    assert timetable_signature(shifted) != timetable_signature(draft)


async def test_change_spec_round_trips_for_recomputation():
    duty = DutyAssignment("S1", "H1", "00000000-0000-4000-8000-000000000001", DutyRole.INVIGILATOR)
    window = (dt.datetime(2026, 11, 16, 7), dt.datetime(2026, 11, 16, 19))
    spec = ChangeSpec(ChangeKind.REPLACEMENT, duty, None, "Ill", window, "00000000-0000-4000-8000-000000000002")
    assert ChangeSpec.from_parameters(ChangeKind.REPLACEMENT, spec.parameters()) == spec
    swap = ChangeSpec(ChangeKind.SWAP, duty, dataclasses.replace(duty, session_id="S2"), "Swap")
    assert ChangeSpec.from_parameters(ChangeKind.SWAP, swap.parameters()) == swap


async def test_a_change_is_refused_when_the_inputs_changed_after_approval():
    demo, state, service, run = await build()
    roster, request = await swap_request(service, run)
    await service().decide_change(ACTOR, request.request_id, True, None)
    demo._row_source = fake_source(lambda t: t["staff_unavailability"].append(dict(
        unavailability_id="U9", staff_id="00000000-0000-4000-8000-000000000003",
        start_at=dt.datetime(2026, 11, 16, 7), end_at=dt.datetime(2026, 11, 16, 18), reason="x", source="S")))
    await demo.refresh()
    with pytest.raises(DemoServiceError) as changed:
        await service().apply_change(ACTOR, request.request_id)
    assert changed.value.code == "ROSTER_INPUT_CHANGED"
    assert to_assignments(state.data.assignments[roster.roster_id])  # unchanged
    assert [e.action for e in state.data.events if e.action.startswith("CHANGE_")][-1] == "CHANGE_REFUSED"
