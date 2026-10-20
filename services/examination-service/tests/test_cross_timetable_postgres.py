"""Cross-semester protection on disposable localhost PostgreSQL, never the configured database.

Uses the existing SCHEDENCE_TEST_POSTGRES_URL safety checks and migrations. Every case creates
and drops its own database. Authentication uses the project's test principal; all inputs are synthetic.
"""

from __future__ import annotations

import asyncio
import dataclasses
import datetime as dt
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy import insert, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.pool import NullPool

pytest.importorskip("psycopg")
pytest.importorskip("ortools")

from app.api.deps import get_timetable_generator  # noqa: E402
from app.api.timetables import _generate_and_save  # noqa: E402
from app.generation.service import GenerationRequest  # noqa: E402
from app.saved_timetables import DatabaseTimetableStore, workflow  # noqa: E402
from app.saved_timetables.errors import TimetableWorkflowError  # noqa: E402
from app.scheduling.protected import cross_timetable_violations  # noqa: E402
from app.seed.records import (  # noqa: E402
    Dataset, ExamPeriod, ExamPeriodModule, Module, ModuleSpecialization, Registration, Student, Venue,
)
from app.seed.seeder import TABLE_SPECS  # noqa: E402
from auth_support import EXAM_ADMIN, INSTITUTION  # noqa: E402
from schedence_db import create_db_engine, create_session_factory, load_settings  # noqa: E402
from test_seating_postgres import ADMIN_URL, _admin, _alembic, _query, _use_database  # noqa: E402

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="set SCHEDENCE_TEST_POSTGRES_URL to local PostgreSQL")
S1, S2 = "LOCAL-2026-S1-FINAL", "LOCAL-2026-S2-FINAL"
DAY = dt.date(2026, 11, 16)
A, B = "IT30000001", "IT30000002"
REASON = "Isolated cross-semester integration test"
AUTH = SimpleNamespace(settings=SimpleNamespace(institution_id=INSTITUTION))


@pytest.fixture
async def cross_database(request, monkeypatch, tmp_path):
    name = f"exam_cross_{uuid.uuid4().hex[:12]}"
    _admin(f"CREATE DATABASE {name}")  # helper refuses non-local URLs before connecting
    engine = None
    try:
        url = _use_database(monkeypatch, tmp_path, name)
        _query(url, "CREATE SCHEMA examinations")
        _alembic("upgrade", "0011")
        engine = create_db_engine(load_settings(), poolclass=NullPool)
        scenario = getattr(request, "param", "both")
        repeat = scenario != "rooms"
        data = Dataset(
            modules=(Module("IT2001", "Synthetic semester one", 2, 1, 3, 3, "written", 60, False, 0),
                     Module("IT1001", "Synthetic semester two", 1, 2, 3, 4, "written", 60, False, 0)),
            module_specializations=(ModuleSpecialization("IT2001", "IT"), ModuleSpecialization("IT1001", "IT")),
            students=(Student(A, 2, "IT"), Student(B, 1, "IT")),
            registrations=(Registration(A, "IT2001", "normal"),
                           Registration(A if repeat else B, "IT1001", "repeat" if repeat else "normal")),
            venues=tuple(Venue(f"R{i}", f"Synthetic room {i}", "lecture_hall", "R", 1, 4, 2)
                         for i in range(1, 3 if scenario == "repeat" else 2)),
            venue_blocks=(),
            exam_periods=tuple(ExamPeriod(code, f"Synthetic semester {semester}", "2026/2027", semester,
                                         DAY, DAY, dt.time(8, 30), dt.time(17, 30), False, False)
                               for code, semester in ((S1, 1), (S2, 2))),
            exam_period_exclusions=(),
            exam_period_modules=(ExamPeriodModule(S1, "IT2001"), ExamPeriodModule(S2, "IT1001")),
        )
        async with AsyncSession(engine) as session:
            for spec in TABLE_SPECS:
                rows = [dataclasses.asdict(row) for row in spec.records(data)]
                if rows:
                    await session.execute(insert(spec.model), rows)
            await session.commit()
        state = SimpleNamespace(engine=engine, generation_lock=asyncio.Lock(), session_factory=create_session_factory(
            engine.execution_options(postgresql_readonly=True)))
        generator = get_timetable_generator(SimpleNamespace(app=SimpleNamespace(state=state)), AUTH)
        yield SimpleNamespace(engine=engine, generator=generator, url=url, scenario=scenario)
    finally:
        if engine is not None:
            await engine.dispose()
        _admin(f"DROP DATABASE {name} WITH (FORCE)")


def request_for(period):
    return GenerationRequest(period, None, time_limit_seconds=5)


async def save(case, period=S1, *, outcome=None, store_type=DatabaseTimetableStore):
    @asynccontextmanager
    async def opener():
        async with AsyncSession(case.engine) as session:
            yield store_type(session)

    class CompletedGeneration:
        async def generate(self, request):
            return outcome

    return await _generate_and_save(request_for(period), case.generator if outcome is None else CompletedGeneration(),
                                    AUTH, opener, EXAM_ADMIN)


async def change(case, timetable, action, *, store_type=DatabaseTimetableStore):
    async with AsyncSession(case.engine) as session:
        extra = {"replace_published": False} if action == "publish" else {}
        return await getattr(workflow, action)(store_type(session), case.generator, INSTITUTION,
                                               timetable.timetable_id, expected_revision=timetable.revision,
                                               user_id=EXAM_ADMIN.user_id, reason=REASON, **extra)


def updated(timetable, change):
    return timetable.model_copy(update=change.timetable)


async def caught(awaitable):
    try:
        return await awaitable
    except TimetableWorkflowError as exc:
        return exc


def gates():
    held, release, attempted = asyncio.Event(), asyncio.Event(), asyncio.Event()
    waiting = SimpleNamespace(pid=None)

    class Holder(DatabaseTimetableStore):
        async def lock_protected(self, institution):
            await super().lock_protected(institution)
            held.set()
            await asyncio.wait_for(release.wait(), 10)

    class Waiter(DatabaseTimetableStore):
        async def lock_protected(self, institution):
            waiting.pid = (await self._session.execute(text("SELECT pg_backend_pid()"))).scalar_one()
            attempted.set()
            await super().lock_protected(institution)

    return SimpleNamespace(held=held, release=release, attempted=attempted, waiting=waiting,
                           Holder=Holder, Waiter=Waiter)


async def assert_waiting(case, gate):
    await asyncio.wait_for(gate.attempted.wait(), 5)
    async with asyncio.timeout(5):
        while True:
            async with case.engine.connect() as conn:
                blocked = (await conn.execute(text(
                    "SELECT count(*) FROM pg_locks WHERE pid = :pid AND locktype = 'advisory' AND NOT granted"),
                    {"pid": gate.waiting.pid})).scalar_one()
            if blocked:
                return
            await asyncio.sleep(0.02)


async def contend(case, first, second):
    """Assert real PostgreSQL lock contention; always release/drain both tasks on failure."""
    gate = gates()
    tasks = [asyncio.create_task(caught(first(gate.Holder)))]
    try:
        await asyncio.wait_for(gate.held.wait(), 5)
        tasks.append(asyncio.create_task(caught(second(gate.Waiter))))
        await assert_waiting(case, gate)
    finally:
        gate.release.set()
        results = await asyncio.wait_for(asyncio.gather(*tasks), 15)
    return results


@pytest.mark.parametrize("cross_database", ["rooms", "repeat"], indirect=True)
async def test_overlapping_semesters_generate_save_approve_publish_without_clashes(cross_database):
    case = cross_database
    assert _query(case.url, "SELECT semester, start_date, end_date FROM examinations.seed_exam_periods"
                           " ORDER BY semester") == [(1, DAY, DAY), (2, DAY, DAY)]
    first = await save(case)
    first = updated(first, await change(case, first, "approve"))
    first = updated(first, await change(case, first, "publish"))
    before = _query(case.url, "SELECT row_to_json(t) FROM examinations.exam_timetables t WHERE timetable_id = %s",
                    first.timetable_id)
    outcome = await case.generator.generate(request_for(S2))
    assert outcome.timetable is not None and outcome.validation_violations == ()
    assert len(outcome.snapshot.protected.timetables) == 1
    assert cross_timetable_violations(outcome.snapshot.protected, outcome.timetable.sessions,
                                      minimum_break_minutes=60) == ()
    current = outcome.timetable.sessions[0]
    protected = first.sessions[0]
    assert abs((current.start - protected.start).total_seconds()) >= (120 if case.scenario == "repeat" else 60) * 60
    if case.scenario == "repeat":
        assert {r.registration_type for r in outcome.snapshot.registrations} == {"repeat"}
        # A different room cannot hide a repeat student's simultaneous examination.
        other_room = next(v.venue_id for v in outcome.snapshot.venues if v.venue_id != protected.rooms[0].venue_id)
        rooms = tuple(dataclasses.replace(r, venue_id=other_room) for r in current.rooms)
    else:
        rooms = current.rooms
    simultaneous = dataclasses.replace(current, start=protected.start, end=protected.end, rooms=rooms)
    assert {v.constraint for v in cross_timetable_violations(outcome.snapshot.protected, [simultaneous],
                                                             minimum_break_minutes=60)} == {
        "X2" if case.scenario == "repeat" else "X1"}
    second = await save(case, S2, outcome=outcome)
    second = updated(second, await change(case, second, "approve"))
    second = updated(second, await change(case, second, "publish"))
    assert second.status == "PUBLISHED"
    assert _query(case.url, "SELECT row_to_json(t) FROM examinations.exam_timetables t WHERE timetable_id = %s",
                  first.timetable_id) == before


async def test_concurrent_cross_semester_approvals_have_one_safe_winner(cross_database):
    case = cross_database
    first, second = await save(case), await save(case, S2)
    assert first.sessions[0].start == second.sessions[0].start  # drafts do not reserve resources
    winner, loser = await contend(case,
        lambda store: change(case, first, "approve", store_type=store),
        lambda store: change(case, second, "approve", store_type=store))
    assert winner.timetable["status"] == "APPROVED"
    assert isinstance(loser, TimetableWorkflowError) and loser.rule == "STALE"
    assert {p["rule"] for p in loser.detail} == {"STALE", "X1", "X2"}
    assert _query(case.url, "SELECT status, revision FROM examinations.exam_timetables ORDER BY timetable_id") == [
        ("APPROVED", 2), ("DRAFT", 1)]
    assert _query(case.url, "SELECT count(*) FROM examinations.exam_timetable_history") == [(3,)]


@pytest.mark.parametrize("action", ["approve", "publish"])
async def test_save_rechecks_after_waiting_for_concurrent_protection(cross_database, action):
    case = cross_database
    stale = await case.generator.generate(request_for(S2))
    first = await save(case)
    if action == "publish":
        first = updated(first, await change(case, first, "approve"))
    winner, refused = await contend(case,
        lambda store: change(case, first, action, store_type=store),
        lambda store: save(case, S2, outcome=stale, store_type=store))
    assert winner.timetable["status"] == ("APPROVED" if action == "approve" else "PUBLISHED")
    assert isinstance(refused, TimetableWorkflowError) and refused.rule == "PROTECTED_CHANGED"
    assert {p["rule"] for p in refused.detail} == {"PROTECTED_CHANGED", "X1", "X2"}
    assert _query(case.url, "SELECT count(*) FROM examinations.exam_timetables") == [(1,)]


async def test_publication_serializes_with_other_semester_approval(cross_database):
    case = cross_database
    first, second = await save(case), await save(case, S2)
    first = updated(first, await change(case, first, "approve"))
    published, refused = await contend(case,
        lambda store: change(case, first, "publish", store_type=store),
        lambda store: change(case, second, "approve", store_type=store))
    assert published.timetable["status"] == "PUBLISHED"
    assert isinstance(refused, TimetableWorkflowError) and refused.rule == "STALE"
    assert {p["rule"] for p in refused.detail} == {"STALE", "X1", "X2"}
    assert _query(case.url, "SELECT status FROM examinations.exam_timetables ORDER BY timetable_id") == [
        ("PUBLISHED",), ("DRAFT",)]


async def test_publish_revalidates_legacy_conflicting_approved_timetables(cross_database):
    case = cross_database
    first, second = await save(case), await save(case, S2)
    first = updated(first, await change(case, first, "approve"))
    # Construct a legacy conflicting approved record in this disposable fixture only.
    # The public approval workflow correctly refuses to create this state.
    async with AsyncSession(case.engine) as session:
        store = DatabaseTimetableStore(session)
        changed = await store.change_status(INSTITUTION, second.timetable_id, from_status="DRAFT", to_status="APPROVED",
                                           expected_revision=1, user_id=EXAM_ADMIN.user_id, reason=REASON, details={})
        await store.commit()
    second = second.model_copy(update=changed[0])
    response = await caught(change(case, second, "publish"))
    assert isinstance(response, TimetableWorkflowError) and response.rule == "STALE"
    assert {p["rule"] for p in response.detail} == {"STALE", "X1", "X2"}
    assert _query(case.url, "SELECT status, revision FROM examinations.exam_timetables ORDER BY timetable_id") == [
        ("APPROVED", 2), ("APPROVED", 2)]
    assert _query(case.url, "SELECT count(*) FROM examinations.exam_timetable_history WHERE action = 'publish'") == [(0,)]


async def test_concurrent_save_requests_keep_unique_drafts_and_do_not_deadlock(cross_database):
    case = cross_database
    outcome = await case.generator.generate(request_for(S1))
    first, second = await contend(case,
        lambda store: save(case, outcome=outcome, store_type=store),
        lambda store: save(case, outcome=outcome, store_type=store))
    assert [first.draft_number, second.draft_number] == [1, 2]
    assert _query(case.url, "SELECT status, revision FROM examinations.exam_timetables ORDER BY draft_number") == [
        ("DRAFT", 1), ("DRAFT", 1)]
