"""Cross-cycle clash prevention: protected (APPROVED / PUBLISHED) timetables constrain a new timetable.

Small synthetic cases through the real CP-SAT model and the independent X1 / X2 validator; the in-memory
store for what counts as protected; the Save Draft / Approve / Publish API through the generation harness.
No database and no large solves.
"""

from __future__ import annotations

import asyncio
import collections
import copy
import dataclasses
import datetime as dt
import re
import uuid

import pytest

pytest.importorskip("ortools")

from sqlalchemy.dialects import postgresql  # noqa: E402

from app.generation.protected_summary import protected_summary  # noqa: E402
from app.generation.service import TimetableGenerator  # noqa: E402
from app.saved_timetables import DatabaseTimetableStore, NewTimetable  # noqa: E402
from app.saved_timetables.memory import MemoryTimetableStore  # noqa: E402
from app.scheduling import SolveStatus, SolverSettings, generate_timetable, validate_timetable  # noqa: E402
from app.scheduling.inputs import load_snapshot  # noqa: E402
from app.scheduling.protected import (  # noqa: E402
    ProtectedSchedule, ProtectedSession, ProtectedTimetable, cross_timetable_violations,
)
from app.scheduling.types import DraftTimetable, ExamSession, RoomAllocation, SeatAllocation  # noqa: E402
from app.seating.memory import MemorySeatingStore  # noqa: E402
from auth_support import INSTITUTION, FakeIdentityService  # noqa: E402
from schedence_auth.testing import TokenFactory  # noqa: E402
from scheduling_support import MONDAY, make_case  # noqa: E402
from test_saved_timetables import DRAFTS, LIST, client  # noqa: E402, F401  (fixture)
from test_timetable_generation import DATASET, SHARED, Harness, body, headers  # noqa: E402

A, B, C = "IT30000001", "IT30000002", "IT30000003"
MORNING, AFTERNOON = dt.time(8, 30), dt.time(13, 30)
STUDENT_ID = re.compile(r"[A-Z]{2}[0-9]{8}")
OTHER_CYCLE = "PP1-2026-S1-FINAL"
SETTINGS = SolverSettings(time_limit_seconds=10)
BREAK = dt.timedelta(minutes=60)


def at(clock: dt.time, minutes: int = 0, day: dt.date = MONDAY) -> dt.datetime:
    return dt.datetime.combine(day, clock) + dt.timedelta(minutes=minutes)


def protected_session(start: dt.datetime, minutes: int, venues=("R1", "R2"), students=(A,), module="IE2010",
                      session_id="OTHER:IE2010") -> ProtectedSession:
    return ProtectedSession(session_id, module, start, start + dt.timedelta(minutes=minutes), tuple(venues), tuple(students))


def protected_timetable(*sessions: ProtectedSession, timetable_id=7, status="APPROVED") -> ProtectedTimetable:
    day = min(s.start.date() for s in sessions)
    return ProtectedTimetable(timetable_id, OTHER_CYCLE, "Semester 1 final", status, 1, 3, day, day, sessions)


def schedule(*sessions: ProtectedSession) -> ProtectedSchedule:
    return ProtectedSchedule((protected_timetable(*sessions),))


async def solve(**case):
    protected = case.pop("protected", None)
    snapshot, _, _ = await make_case(**case)
    snapshot = dataclasses.replace(snapshot, protected=protected)
    return snapshot, generate_timetable(snapshot, SETTINGS)


def starts(result) -> dict[str, dt.datetime]:
    return {s.module_code: s.start for s in result.timetable.sessions}


# --- Rooms: two cycles on the same dates and rooms -------------------------------------------------------------


async def test_a_new_timetable_avoids_rooms_held_by_a_protected_timetable_on_the_same_dates():
    snapshot, result = await solve(protected=schedule(protected_session(at(MORNING), 60, students=())))  # R1 and R2
    assert result.status in (SolveStatus.OPTIMAL, SolveStatus.FEASIBLE)
    assert starts(result)["IT1001"] == at(AFTERNOON)  # the only start whose rooms are free
    assert validate_timetable(snapshot, result.timetable) == ()


async def test_without_protected_timetables_the_solver_and_the_input_identity_are_unchanged():
    snapshot, free = await solve()
    for none in (None, ProtectedSchedule()):
        other, result = await solve(protected=none)
        assert other.fingerprint == snapshot.fingerprint  # protected timetables are not part of the input identity
        assert starts(result) == starts(free)
        assert validate_timetable(other, result.timetable) == ()


async def test_different_start_times_with_overlapping_durations_still_clash():
    # A 180 minute exam at 08:30 ends 11:30; the protected exam runs 10:00-12:00, which is no permitted start.
    snapshot, result = await solve(durations=(180,), protected=schedule(protected_session(at(MORNING, 90), 120, students=())))
    assert starts(result)["IT1001"] == at(AFTERNOON)
    clash = dataclasses.replace(result.timetable, sessions=(dataclasses.replace(
        result.timetable.sessions[0], start=at(MORNING), end=at(MORNING, 180)),))
    assert {v.constraint for v in validate_timetable(snapshot, clash)} == {"X1"}


async def test_touching_intervals_do_not_clash():
    snapshot, result = await solve(protected=schedule(protected_session(at(MORNING, -60), 60, students=())))  # ends at 08:30
    assert starts(result)["IT1001"] == at(MORNING)
    assert validate_timetable(snapshot, result.timetable) == ()


async def test_no_clash_free_timetable_is_reported_not_ignored():
    snapshot, result = await solve(protected=schedule(protected_session(at(MORNING), 540, students=())))  # both rooms all day
    assert result.status == SolveStatus.INFEASIBLE and result.timetable is None
    summary = protected_summary(snapshot, result.status, False, ())
    assert summary["student_conflict_status"] == "blocked"
    assert summary["blocking_issues"][0].startswith("No timetable satisfies H1-H11 together with the 1 protected")
    assert "not ignored" in summary["blocking_issues"][0]


async def test_overlapping_protected_timetables_do_not_make_the_model_infeasible():
    first = protected_timetable(protected_session(at(MORNING), 60, venues=("R1",), students=()))
    second = protected_timetable(protected_session(at(MORNING, 30), 60, venues=("R1",), students=()), timetable_id=8)
    snapshot, result = await solve(protected=ProtectedSchedule((first, second)))  # R2 stays free
    assert result.status in (SolveStatus.OPTIMAL, SolveStatus.FEASIBLE)
    assert validate_timetable(snapshot, result.timetable) == ()


# --- Students: shared students, repeat students ----------------------------------------------------------------


async def test_a_student_with_a_protected_exam_keeps_the_break_across_cycles():
    # A sits another cycle's exam 08:30-10:30 at a venue this snapshot does not know: only the student clashes.
    held = schedule(protected_session(at(MORNING), 120, venues=("ELSEWHERE",), students=(A,)))
    snapshot, result = await solve(protected=held)
    assert starts(result)["IT1001"] == at(AFTERNOON)
    assert validate_timetable(snapshot, result.timetable) == ()


async def test_exactly_the_break_is_allowed_and_one_minute_less_is_a_clash():
    snapshot, _ = await solve()
    exam = ExamSession("TEST:IT1001", "IT1001", "TEST", at(MORNING), at(MORNING, 60),
                       (RoomAllocation("R1", (SeatAllocation(A, 1), SeatAllocation(B, 2))),))
    for gap, expected in ((60, set()), (59, {"X2"})):  # the protected exam starts `gap` minutes after the new one ends
        held = schedule(protected_session(at(MORNING, 60 + gap), 60, venues=("ELSEWHERE",), students=(A,)))
        draft = DraftTimetable("TEST", snapshot.fingerprint, (exam,))
        assert {v.constraint for v in validate_timetable(dataclasses.replace(snapshot, protected=held), draft)} == expected


async def test_a_repeat_student_is_protected_like_any_other_student():
    # IT1002 is a repeat module and A its repeat candidate; A also sits an exam of the other cycle at 13:30.
    held = schedule(protected_session(at(AFTERNOON), 120, venues=("ELSEWHERE",), students=(A,)))
    snapshot, result = await solve(candidates=((B,), (A,)), protected=held)
    assert {r.registration_type for r in snapshot.registrations if r.student_id == A} == {"repeat"}
    assert starts(result)["IT1002"] == at(MORNING)
    assert validate_timetable(snapshot, result.timetable) == ()


async def test_a_student_who_sits_a_split_module_is_also_protected():
    held = schedule(protected_session(at(AFTERNOON), 120, venues=("ELSEWHERE",), students=(A,)))
    snapshot, result = await solve(candidates=((A, B, C),), venue_types=("lecture_hall",), full_capacities=(4,),
                                   protected=held)  # one room of 2 seats: two sittings
    assert result.status in (SolveStatus.OPTIMAL, SolveStatus.FEASIBLE)
    assert len(result.timetable.sessions) == 2
    [session] = [s for s in result.timetable.sessions if A in {seat.student_id for r in s.rooms for seat in r.seats}]
    assert session.start == at(MORNING)
    assert validate_timetable(snapshot, result.timetable) == ()


async def test_clash_messages_and_the_summary_never_contain_student_ids():
    snapshot, _ = await solve()
    held = schedule(protected_session(at(MORNING), 60, venues=("R1",), students=(A, B)))
    exam = ExamSession("TEST:IT1001", "IT1001", "TEST", at(MORNING), at(MORNING, 60),
                       (RoomAllocation("R1", (SeatAllocation(A, 1), SeatAllocation(B, 2))),))
    found = cross_timetable_violations(held, (exam,), minimum_break_minutes=60)
    assert {v.constraint for v in found} == {"X1", "X2"}
    assert "2 student(s)" in next(v.message for v in found if v.constraint == "X2")
    summary = protected_summary(dataclasses.replace(snapshot, protected=held), SolveStatus.MODEL_INVALID, False, found)
    assert not STUDENT_ID.search(" ".join(v.message for v in found) + repr(summary)) and summary["shared_students"] == 2
    assert (summary["venue_conflicts"], summary["student_conflicts"], summary["student_conflict_status"]) == (1, 1, "blocked")


# --- What counts as protected ---------------------------------------------------------------------------------


def saved(period, venue="R1", students=(A,), day=MONDAY, calendar=(MONDAY, MONDAY), clock=MORNING) -> NewTimetable:
    session = {"session_id": f"{period}:IE2010", "module_code": "IE2010", "starts_at": at(clock, day=day),
               "ends_at": at(clock, 60, day=day)}
    return protected_record(period, [session], [{"session_id": session["session_id"], "venue_id": venue}],
                        [{"session_id": session["session_id"], "venue_id": venue, "seat_number": n, "student_id": s}
                         for n, s in enumerate(students, 1)], calendar)


def protected_record(period, sessions, rooms, seats, calendar):
    """Complete synthetic stored records: LIST/detail must see the same shape as database rows.

    Minimal headers suffice for protected_schedule, but cannot represent saved API records.
    Keep those required fields in the fixture instead of adding fabricated production defaults.
    """
    modules = {s["module_code"] for s in sessions}
    counts = collections.Counter(s["module_code"] for s in sessions)
    numbered = collections.Counter()
    full_sessions = []
    for s in sessions:
        numbered[s["module_code"]] += 1
        n = sum(seat["session_id"] == s["session_id"] for seat in seats)
        full_sessions.append(dict(module_name="Synthetic protected examination", exam_type="written", candidate_scope="all",
            academic_year=1, semester=1, delivery_mode="regular", session_number=numbered[s["module_code"]],
            session_count=counts[s["module_code"]], duration_minutes=int((s["ends_at"] - s["starts_at"]).total_seconds() // 60),
            reading_time_minutes=0, candidates=n, normal_candidates=n, repeat_candidates=0, **s))
    full_rooms = []
    for room in rooms:
        n = sum((s["session_id"], s["venue_id"]) == (room["session_id"], room["venue_id"]) for s in seats)
        full_rooms.append(dict(venue_name=room["venue_id"], venue_type="lecture_hall", seats=n, exam_capacity=max(n, 1), **room))
    fingerprint = "0" * 64
    summary = dict(sessions=len(sessions), exam_days_used=len({s["starts_at"].date() for s in sessions}),
                   room_allocations=len(rooms), seat_allocations=len(seats),
                   multi_room_exams=sum(sum(r["session_id"] == s["session_id"] for r in rooms) > 1 for s in sessions),
                   multi_session_modules=sum(n > 1 for n in counts.values()))
    header = dict(period_code=period, period_name=period, created_by=INSTITUTION, exam_kind="FINAL", mode="feasibility",
                  selection="full_cycle", module_codes=None, solver_status="FEASIBLE", solver_version=None,
                  input_fingerprint=fingerprint, settings_version=0, settings_fingerprint=fingerprint,
                  seating_ruleset_version=0, capacity_fingerprint=fingerprint, validation_passed=True, summary=summary,
                  solver_run=dict(mode="feasibility", time_limit_seconds=10, num_search_workers=1, random_seed=0,
                                  wall_time_seconds=0, solver_version=None),
                  validation=dict(passed=True, rules_checked=[], violations=[]), workload=None, optimization=None,
                  excluded_registrations=dict(note="Synthetic fixture", total=0, normal=0, repeat=0, modules=[]), notices=[],
                  inputs=dict(period_code=period, period_name=period, exam_kind="FINAL", start_date=calendar[0].isoformat(),
                              end_date=calendar[1].isoformat(), exam_days=sorted({s["starts_at"].date().isoformat() for s in sessions}),
                              selection="full_cycle", modules=len(modules), repeat_only_modules=0,
                              students=len({s["student_id"] for s in seats}), registrations=len(seats), repeat_registrations=0,
                              venues=len({r["venue_id"] for r in rooms}), input_fingerprint=fingerprint,
                              capacity_ruleset_version=0, capacity_fingerprint=fingerprint,
                              settings_version=0, settings_fingerprint=fingerprint))
    return NewTimetable(header, full_sessions, full_rooms, [dict(registration_type="normal", **s) for s in seats])


STEPS = {"DRAFT": (), "APPROVED": ("APPROVED",), "PUBLISHED": ("APPROVED", "PUBLISHED"),
         "ARCHIVED": ("APPROVED", "PUBLISHED", "ARCHIVED")}


async def put(store: MemoryTimetableStore, period, status, institution=INSTITUTION, **kw) -> int:
    header = await store.save(institution, saved(period, **kw))
    current, revision = "DRAFT", 1
    for to in STEPS[status]:
        await store.change_status(institution, header["timetable_id"], from_status=current, to_status=to,
                                  expected_revision=revision, user_id=INSTITUTION, reason="test", details={})
        current, revision = to, revision + 1
    return header["timetable_id"]


async def read(store, window=(MONDAY, MONDAY), students=frozenset({A, B}), exclude="TEST", minimum_break_minutes=0):
    return await store.protected_schedule(INSTITUTION, exclude_period_code=exclude, start_date=window[0], end_date=window[1],
                                          student_ids=students, minimum_break_minutes=minimum_break_minutes)


async def test_only_approved_and_published_timetables_of_other_cycles_are_protected():
    store = MemoryTimetableStore()
    approved = await put(store, "CYCLE-A", "APPROVED")
    published = await put(store, "CYCLE-B", "PUBLISHED")
    await put(store, "CYCLE-C", "DRAFT")
    await put(store, "CYCLE-D", "ARCHIVED")
    await put(store, "TEST", "APPROVED")  # the same cycle holds alternative versions of itself: never a constraint
    found = await read(store)
    assert [(t.timetable_id, t.status) for t in found.timetables] == [(approved, "APPROVED"), (published, "PUBLISHED")]
    assert found.overlapping_drafts == 1  # reported, not blocking


async def test_drafts_never_block_and_other_institutions_are_invisible():
    store = MemoryTimetableStore()
    await put(store, "CYCLE-C", "DRAFT")
    await put(store, "CYCLE-X", "PUBLISHED", institution=uuid.uuid4())
    assert (await read(store)).timetables == ()


async def test_timetables_without_overlapping_dates_are_not_considered():
    store = MemoryTimetableStore()
    later = MONDAY + dt.timedelta(days=45)  # Semester 2 dates, say
    await put(store, "CYCLE-S1", "PUBLISHED", day=later, calendar=(later, later + dt.timedelta(days=12)))
    assert (await read(store)).timetables == ()
    assert (await read(store, window=(later, later))).timetables[0].period_code == "CYCLE-S1"
    snapshot, result = await solve(protected=await read(store))
    assert snapshot.protected.timetables == () and starts(result)["IT1001"] == at(MORNING)


async def test_only_sessions_inside_the_dates_and_only_candidate_students_are_kept():
    store = MemoryTimetableStore()
    await put(store, "CYCLE-S1", "APPROVED", students=(A, B, "IT39999999"), calendar=(MONDAY, MONDAY + dt.timedelta(days=5)))
    [kept] = (await read(store, students={A})).timetables[0].sessions
    assert kept.student_ids == (A,) and kept.venue_ids == ("R1",)  # a student outside this cycle is never loaded
    next_days = (MONDAY + dt.timedelta(days=1), MONDAY + dt.timedelta(days=2))
    assert (await read(store, window=next_days)).timetables[0].sessions == ()


# --- The database queries, compiled without a database -----------------------------------------------------------


class Recorder:
    """A session that records statements and answers them in order."""

    def __init__(self, *answers):
        self.statements, self.answers = [], list(answers)

    async def execute(self, statement, params=None):
        self.statements.append(statement)
        return self.answers.pop(0)


def rows(name, *values):
    row = collections.namedtuple(name, values[0].keys()) if values else None
    return [row(**v) for v in values]


async def test_the_database_store_reads_only_protected_rows_of_other_cycles_and_locks_the_institution():
    window = (MONDAY, MONDAY)
    header = dict(timetable_id=3, period_code=OTHER_CYCLE, period_name="S1", status="APPROVED", draft_number=2, revision=4,
                  inputs={"start_date": "2026-11-16", "end_date": "2026-11-16"})
    session = dict(timetable_id=3, session_id="S1:IE2010", module_code="IE2010", starts_at=at(MORNING), ends_at=at(MORNING, 60))
    recorder = Recorder(rows("H", header), rows("D", {"inputs": {"start_date": "2026-11-16", "end_date": "2026-11-16"}}),
                        rows("S", session), rows("R", dict(timetable_id=3, session_id="S1:IE2010", venue_id="R1")),
                        rows("T", dict(timetable_id=3, session_id="S1:IE2010", student_id=A)))
    found = await DatabaseTimetableStore(recorder).protected_schedule(
        INSTITUTION, exclude_period_code="TEST", start_date=window[0], end_date=window[1], student_ids={A})
    assert found.overlapping_drafts == 1 and found.timetables[0].sessions[0].student_ids == (A,)
    sql = [str(s.compile(dialect=postgresql.dialect())) for s in recorder.statements]
    assert len(sql) == 5 and all("FROM examinations." in q or "FROM exam_timetable" in q for q in sql)
    assert "status IN" in sql[0] and "period_code !=" in sql[0] and "institution_id =" in sql[0]
    assert "status =" in sql[1] and "period_code !=" in sql[1]
    assert "student_id IN" in sql[4]
    assert all(q.lstrip().startswith("SELECT") for q in sql)  # nothing is written

    lock = Recorder(None, None)
    await DatabaseTimetableStore(lock).lock_protected(INSTITUTION)
    assert "pg_advisory_xact_lock" in str(lock.statements[1]) and "lock_timeout" in str(lock.statements[0])


# --- Save Draft, Approve and Publish through the API ----------------------------------------------------------


class ProtectedHarness(Harness):
    """The generation harness whose snapshot loader reads protected timetables from the in-memory store."""

    def __init__(self, store: MemoryTimetableStore):
        super().__init__()
        self.store, self.aware = store, True

    def generator(self, lock) -> TimetableGenerator:
        from app.scheduling_settings.service import SchedulingSettingsService

        async def load_settings():
            return await SchedulingSettingsService(self.settings, INSTITUTION).active()

        async def load(period_code, policy, module_codes, calendar=None, seating_preferences=None):
            self.loads += 1

            async def protected(period, students):
                return await self.store.protected_schedule(
                    INSTITUTION, exclude_period_code=period.period_code, start_date=period.start_date,
                    end_date=period.end_date, student_ids=students, minimum_break_minutes=policy.minimum_break_minutes)

            return await load_snapshot(self.source, MemorySeatingStore(), period_code=period_code, policy=policy,
                                       module_codes=module_codes, from_cycle=True, calendar=calendar,
                                       seating_preferences=seating_preferences, protected=protected if self.aware else None)

        return TimetableGenerator(load, load_settings, lock)


@pytest.fixture
def tokens() -> TokenFactory:
    return TokenFactory()


@pytest.fixture
def identity() -> FakeIdentityService:
    return FakeIdentityService()


@pytest.fixture
def store() -> MemoryTimetableStore:
    return MemoryTimetableStore()


@pytest.fixture
def harness(store) -> ProtectedHarness:
    return ProtectedHarness(store)


@pytest.fixture
def admin(tokens, identity) -> dict[str, str]:
    return headers(tokens, identity, "EXAMINATION_ADMIN")


REASON = "Checked by the examination board"
SHARED_STUDENTS = sorted({r.student_id for r in DATASET.registrations if r.module_code in SHARED})
VENUES = [v.venue_id for v in DATASET.venues]


def run(coroutine):
    return asyncio.run(coroutine)


def stored(store, timetable_id):
    detail = run(store.get(INSTITUTION, timetable_id))
    seats = run(store.seats(INSTITUTION, timetable_id, session_id=None, limit=10**6, offset=0))[0]
    return detail, seats


def promote(store, timetable_id, status):
    revision = 1
    for current, to in zip(("DRAFT", "APPROVED", "PUBLISHED"), STEPS[status]):
        run(store.change_status(INSTITUTION, timetable_id, from_status=current, to_status=to, expected_revision=revision,
                                user_id=INSTITUTION, reason="test", details={}))
        revision += 1


def copy_as_other_cycle(store, timetable_id, status="APPROVED") -> int:
    """A saved timetable of the harness cycle, copied as an APPROVED / PUBLISHED timetable of another cycle."""
    detail, seats = stored(store, timetable_id)
    header = {**detail.header, "period_code": OTHER_CYCLE, "period_name": "Semester 1 final"}
    copied = run(store.save(INSTITUTION, NewTimetable(header, detail.sessions, detail.rooms, seats)))["timetable_id"]
    promote(store, copied, status)
    return copied


def everything_booked(store, status="APPROVED") -> int:
    """Another cycle that holds every venue all day on every weekday of the harness dates, for all shared students."""
    days = [dt.date(2026, 11, 16) + dt.timedelta(days=i) for i in range(12) if (dt.date(2026, 11, 16) + dt.timedelta(days=i)).weekday() < 5]
    sessions = [{"session_id": f"{OTHER_CYCLE}:{n}", "module_code": "IE2010", "starts_at": at(dt.time(8, 0), day=d),
                 "ends_at": at(dt.time(18, 0), day=d)} for n, d in enumerate(days)]
    rooms = [{"session_id": s["session_id"], "venue_id": v} for s in sessions for v in VENUES]
    seats = [{"session_id": s["session_id"], "venue_id": VENUES[0], "seat_number": n, "student_id": student}
             for s in sessions for n, student in enumerate(SHARED_STUDENTS, 1)]
    record = protected_record(OTHER_CYCLE, sessions, rooms, seats, (dt.date(2026, 11, 16), dt.date(2026, 11, 28)))
    timetable_id = run(store.save(INSTITUTION, record))["timetable_id"]
    promote(store, timetable_id, status)
    return timetable_id


def save(client, admin):
    return client.post(DRAFTS, headers=admin, json=body())


def change(client, admin, timetable, action):
    return client.post(f"{LIST}/{timetable['timetable_id']}/{action}", headers=admin,
                       json={"expected_revision": timetable["revision"], "reason": REASON})


def status_of(client, admin, timetable) -> str:
    return client.get(f"{LIST}/{timetable['timetable_id']}", headers=admin).json()["status"]


def test_a_second_cycle_on_the_same_dates_and_rooms_works_around_the_first(client, admin, store) -> None:
    first = save(client, admin).json()
    protected_id = copy_as_other_cycle(store, first["timetable_id"])
    before = copy.deepcopy(stored(store, protected_id))

    response = save(client, admin)
    assert response.status_code == 201, response.text
    second = response.json()
    assert before == stored(store, protected_id)  # the protected timetable is never altered

    moment = dt.datetime.fromisoformat
    for new in second["sessions"]:
        for old in first["sessions"]:
            if {r["venue_id"] for r in new["rooms"]} & {r["venue_id"] for r in old["rooms"]}:
                assert moment(new["end"]) <= moment(old["start"]) or moment(old["end"]) <= moment(new["start"])
    # The same 105 students sit modules of both cycles: nobody has overlapping exams or less than the break.
    old_seats, new_seats = {}, {}
    for seats, target in ((stored(store, first["timetable_id"])[1], old_seats), (stored(store, second["timetable_id"])[1], new_seats)):
        for seat in seats:
            target.setdefault(seat["session_id"], set()).add(seat["student_id"])
    for new in second["sessions"]:
        for old in first["sessions"]:
            if new_seats[new["session_id"]] & old_seats[old["session_id"]]:
                assert moment(new["start"]) >= moment(old["end"]) + BREAK or moment(new["end"]) + BREAK <= moment(old["start"])

    summary = second["inputs"]["cross_timetable"]
    [listed] = summary["protected_timetables"]
    assert (listed["timetable_id"], listed["status"], listed["period_code"]) == (protected_id, "APPROVED", OTHER_CYCLE)
    assert summary["student_conflict_status"] == "clear" and summary["shared_students"] == len(SHARED_STUDENTS)
    assert summary["occupied_slot_total"] == sum(len(s["rooms"]) for s in first["sessions"]) and summary["blocking_issues"] == []
    assert not STUDENT_ID.search(str(summary))
    assert client.get(f"{LIST}/{second['timetable_id']}", headers=admin).json()["inputs"]["cross_timetable"] == summary


def test_generate_reports_published_protected_timetables_and_saves_nothing(client, admin, store) -> None:
    copy_as_other_cycle(store, save(client, admin).json()["timetable_id"], status="PUBLISHED")
    total = client.get(LIST, headers=admin).json()["total"]
    response = client.post("/api/v1/timetables/generate", headers=admin, json=body())
    assert response.status_code == 200
    assert response.json()["inputs"]["cross_timetable"]["protected_timetables"][0]["status"] == "PUBLISHED"
    assert client.get(LIST, headers=admin).json()["total"] == total


def test_when_nothing_fits_around_the_protected_timetables_the_answer_says_so(client, admin, store) -> None:
    everything_booked(store)
    result = client.post("/api/v1/timetables/generate", headers=admin, json=body()).json()
    assert result["status"] == "INFEASIBLE" and result["generated"] is False
    cross = result["inputs"]["cross_timetable"]
    assert cross["student_conflict_status"] == "blocked" and "not ignored" in cross["blocking_issues"][0]
    refused = save(client, admin)
    assert refused.status_code == 422 and refused.json()["detail"][0]["rule"] == "NOT_SAVED"
    assert client.get(LIST, headers=admin).json()["total"] == 1  # only the protected timetable exists


def test_a_protected_timetable_that_appears_during_generation_stops_save_draft(client, admin, store, harness) -> None:
    everything_booked(store)  # approved while this generation was running: the generation did not see it
    harness.aware = False
    response = save(client, admin)
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail[0]["rule"] == "PROTECTED_CHANGED" and {p["rule"] for p in detail[1:]} == {"X1", "X2"}
    assert client.get(LIST, headers=admin).json()["total"] == 1  # nothing was saved
    assert not STUDENT_ID.search(str(detail))


def test_approval_is_refused_when_a_protected_timetable_now_clashes(client, admin, store) -> None:
    draft = save(client, admin).json()
    everything_booked(store)  # another cycle was approved after this draft was saved
    refused = change(client, admin, draft, "approve")
    assert refused.status_code == 409
    detail = refused.json()["detail"]
    assert detail[0]["rule"] == "STALE" and "another examination cycle" in detail[0]["message"]
    assert {"X1", "X2"} <= {p["rule"] for p in detail}
    assert status_of(client, admin, draft) == "DRAFT" and not STUDENT_ID.search(refused.text)


def test_publication_is_refused_when_a_protected_timetable_changed_after_approval(client, admin, store) -> None:
    approved = change(client, admin, save(client, admin).json(), "approve").json()["timetable"]
    everything_booked(store, status="PUBLISHED")
    refused = change(client, admin, approved, "publish")
    assert refused.status_code == 409 and {"X1", "X2"} <= {p["rule"] for p in refused.json()["detail"]}
    assert status_of(client, admin, approved) == "APPROVED"


def test_drafts_and_archived_timetables_of_other_cycles_do_not_block_approval(client, admin, store) -> None:
    draft = save(client, admin).json()
    everything_booked(store, status="DRAFT")
    archived = everything_booked(store, status="ARCHIVED")
    assert status_of(client, admin, {"timetable_id": archived}) == "ARCHIVED"
    assert change(client, admin, draft, "approve").status_code == 200  # alternatives and old versions are not constraints


def test_a_clash_free_protected_timetable_does_not_make_an_approval_stale(client, admin, store) -> None:
    draft = save(client, admin).json()
    run(put(store, OTHER_CYCLE, "APPROVED", venue="NOT-A-ROOM", students=("IT39999999",), day=dt.date(2026, 11, 17),
            calendar=(dt.date(2026, 11, 16), dt.date(2026, 11, 28))))
    approved = change(client, admin, draft, "approve")
    assert approved.status_code == 200, approved.text
    assert any("other cycles" in check for check in approved.json()["checks"])


@pytest.mark.parametrize("offset,clock,expected", [(-1, MORNING, AFTERNOON), (1, AFTERNOON, MORNING)])
async def test_student_break_is_protected_across_both_calendar_boundaries(offset, clock, expected):
    from app.scheduling.policy import SchedulingPolicy

    store = MemoryTimetableStore()
    day = MONDAY + dt.timedelta(days=offset)
    await put(store, OTHER_CYCLE, "APPROVED", students=(A,), venue="ELSEWHERE", day=day,
              calendar=(day, day), clock=clock)
    assert (await read(store)).timetables == ()  # the saved calendar itself does not overlap
    held = await read(store, minimum_break_minutes=1440)
    assert len(held.timetables) == 1
    snapshot, result = await solve(protected=held, policy=SchedulingPolicy(minimum_break_minutes=1440))
    assert result.timetable and starts(result)["IT1001"] == at(expected)
    assert validate_timetable(snapshot, result.timetable) == ()
    forbidden = MORNING if expected == AFTERNOON else AFTERNOON
    invalid = dataclasses.replace(result.timetable, sessions=(dataclasses.replace(
        result.timetable.sessions[0], start=at(forbidden), end=at(forbidden, 60)),))
    assert {v.constraint for v in validate_timetable(snapshot, invalid)} == {"X2"}


async def test_database_protected_query_covers_break_buffer_without_loading_other_students():
    day = MONDAY - dt.timedelta(days=1)
    header = dict(timetable_id=3, period_code=OTHER_CYCLE, period_name="S1", status="PUBLISHED", draft_number=1,
                  revision=3, inputs={"start_date": day.isoformat(), "end_date": day.isoformat()})
    session = dict(timetable_id=3, session_id="S1:IE2010", module_code="IE2010", starts_at=at(MORNING, day=day),
                   ends_at=at(MORNING, 60, day=day))
    recorder = Recorder(rows("H", header), [], rows("S", session), [],
                        rows("T", dict(timetable_id=3, session_id="S1:IE2010", student_id=A)))
    held = await DatabaseTimetableStore(recorder).protected_schedule(INSTITUTION, exclude_period_code="TEST",
        start_date=MONDAY, end_date=MONDAY, student_ids={A}, minimum_break_minutes=1440)
    assert held.timetables[0].sessions[0].student_ids == (A,)
    session_params = recorder.statements[2].compile(dialect=postgresql.dialect()).params
    assert dt.datetime.combine(MONDAY - dt.timedelta(days=1), dt.time()) in session_params.values()
    assert dt.datetime.combine(MONDAY + dt.timedelta(days=2), dt.time()) in session_params.values()
    seat_query = recorder.statements[4].compile(dialect=postgresql.dialect())
    assert "student_id IN" in str(seat_query)
    assert [A] in seat_query.params.values()


def test_save_approve_and_publish_share_a_consistent_protection_lock_order(client, admin, store, monkeypatch):
    events = []
    for method, label in (("lock_protected", "institution"), ("lock", "row"), ("lock_cycle", "cycle"),
                          ("protected_schedule", "protected_read"), ("save", "save"), ("commit", "commit")):
        original = getattr(store, method)
        async def tracked(*args, _original=original, _label=label, **kwargs):
            events.append(_label)
            return await _original(*args, **kwargs)
        monkeypatch.setattr(store, method, tracked)
    response = save(client, admin)
    assert response.status_code == 201, response.text
    draft = response.json()
    final_check = events.index("institution")
    assert events[final_check:] == ["institution", "protected_read", "save", "commit"]
    events.clear()
    approved = change(client, admin, draft, "approve")
    assert approved.status_code == 200, approved.text
    assert events[:3] == ["institution", "row", "protected_read"]
    events.clear()
    published = change(client, admin, approved.json()["timetable"], "publish")
    assert published.status_code == 200, published.text
    assert events[:4] == ["institution", "row", "protected_read", "cycle"]
