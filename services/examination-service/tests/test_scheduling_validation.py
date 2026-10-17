"""Independent validation rejects damaged drafts, not just valid solver output."""

from __future__ import annotations

import datetime as dt
from dataclasses import replace

import pytest

from app.scheduling import SchedulingPolicy, validate_timetable
from app.scheduling.types import DraftTimetable, ExamSession, RoomAllocation, SeatAllocation
from app.sources.types import VenueBlock
from scheduling_support import MONDAY, make_case


async def valid_draft():
    snapshot, _, _ = await make_case(candidates=(("IT30000001",), ("IT30000001",)),
                                      venue_types=("lecture_hall", "computer_lab"))
    sessions = tuple(ExamSession(
        f"TEST:{m.module_code}", m.module_code, "TEST", dt.datetime.combine(MONDAY, clock),
        dt.datetime.combine(MONDAY, clock) + dt.timedelta(minutes=60),
        (RoomAllocation("R1", (SeatAllocation("IT30000001", 1),)),),
    ) for m, clock in zip(snapshot.modules, (dt.time(8, 30), dt.time(13, 30))))
    draft = DraftTimetable("TEST", snapshot.fingerprint, sessions)
    assert validate_timetable(snapshot, draft) == ()
    return snapshot, draft


def change_first(draft, **changes):
    return replace(draft, sessions=(replace(draft.sessions[0], **changes), *draft.sessions[1:]))


def rules(snapshot, draft):
    return {v.constraint for v in validate_timetable(snapshot, draft)}


async def test_validator_h1_student_clash_and_break_are_independent_of_room_choice():
    snapshot, draft = await valid_draft()
    # Different room, same student: room non-overlap alone is not sufficient.
    first = draft.sessions[0]
    second = replace(draft.sessions[1], start=first.start, end=first.end,
                     rooms=(replace(first.rooms[0], venue_id="R2"),))
    assert "H1" in rules(snapshot, replace(draft, sessions=(first, second)))
    second = replace(second, start=first.end, end=first.end + dt.timedelta(minutes=60))
    assert "H1" in rules(snapshot, replace(draft, sessions=(first, second)))


@pytest.mark.parametrize("break_minutes", [0, 30, 60, 90])
async def test_validator_h1_accepts_exact_break_and_rejects_one_minute_short(break_minutes):
    first_start = dt.datetime.combine(MONDAY, dt.time(8, 30))
    duration = dt.timedelta(minutes=70)  # 60 minutes of exam + 10 reading minutes
    second_start = first_start + duration + dt.timedelta(minutes=break_minutes)
    short_start = second_start - dt.timedelta(minutes=1)
    policy = SchedulingPolicy(
        permitted_start_times=(first_start.time(), short_start.time(), second_start.time()),
        minimum_break_minutes=break_minutes,
    )
    snapshot, _, _ = await make_case(candidates=(("IT30000001",), ("IT30000001",)),
                                      readings=(10, 10), policy=policy)
    sessions = tuple(
        ExamSession(f"TEST:{m.module_code}", m.module_code, "TEST", start, start + duration,
                    (RoomAllocation(room, (SeatAllocation("IT30000001", 1),)),))
        for m, start, room in zip(snapshot.modules, (first_start, second_start), ("R1", "R2"))
    )
    draft = DraftTimetable("TEST", snapshot.fingerprint, sessions)
    assert validate_timetable(snapshot, draft) == ()
    shortened = replace(sessions[1], start=short_start, end=short_start + duration)
    # Both starts obey policy and use different rooms: this must fail specifically
    # for the student's break, including a one-minute overlap when break == 0.
    assert rules(snapshot, replace(draft, sessions=(sessions[0], shortened))) == {"H1"}


@pytest.mark.parametrize("number", [0, -1, 3, True, 1.5])
async def test_validator_h2_rejects_seats_outside_effective_capacity(number):
    snapshot, draft = await valid_draft()
    room = RoomAllocation("R1", (SeatAllocation("IT30000001", number),))
    assert "H2" in rules(snapshot, change_first(draft, rooms=(room,)))


async def test_validator_h2_rejects_overfull_room():
    snapshot, draft = await valid_draft()
    seats = tuple(SeatAllocation("IT30000001", n) for n in (1, 2, 3))
    assert "H2" in rules(snapshot, change_first(draft, rooms=(RoomAllocation("R1", seats),)))


async def test_validator_h3_rejects_incompatible_room():
    snapshot, draft = await valid_draft()
    room = replace(draft.sessions[0].rooms[0], venue_id="R2")
    assert "H3" in rules(snapshot, change_first(draft, rooms=(room,)))


async def test_validator_h4_rechecks_raw_blocks():
    snapshot, draft = await valid_draft()
    block = VenueBlock("R1", MONDAY, dt.time(9), dt.time(10), "simulated_maintenance", "SIMULATED test", None)
    changed = replace(snapshot, blocks=(block,))
    assert "H4" in rules(changed, replace(draft, input_fingerprint=changed.fingerprint))


async def test_validator_h5_rejects_room_overlap():
    snapshot, draft = await valid_draft()
    second = replace(draft.sessions[1], start=draft.sessions[0].start, end=draft.sessions[0].end)
    assert "H5" in rules(snapshot, replace(draft, sessions=(draft.sessions[0], second)))


async def test_validator_h6_rejects_dates_outside_period():
    snapshot, draft = await valid_draft()
    session = draft.sessions[0]
    bad = change_first(draft, start=session.start - dt.timedelta(days=1), end=session.end - dt.timedelta(days=1))
    assert "H6" in rules(snapshot, bad)


@pytest.mark.parametrize("clock", [dt.time(8), dt.time(9), dt.time(17, 30)])
async def test_validator_h7_rejects_hours_and_unpermitted_starts(clock):
    snapshot, draft = await valid_draft()
    start = dt.datetime.combine(MONDAY, clock)
    assert "H7" in rules(snapshot, change_first(draft, start=start, end=start + dt.timedelta(hours=1)))


@pytest.mark.parametrize("day", [dt.date(2026, 11, 14), dt.date(2026, 11, 15), MONDAY])
async def test_validator_h8_recomputes_weekends_and_exclusions(day):
    snapshot, _, _ = await make_case(start_date=dt.date(2026, 11, 14), end_date=dt.date(2026, 11, 17), excluded=(MONDAY,))
    start = dt.datetime.combine(day, dt.time(8, 30))
    session = ExamSession("S1", "IT1001", "TEST", start, start + dt.timedelta(hours=1),
                          (RoomAllocation("R1", (SeatAllocation("IT30000001", 1), SeatAllocation("IT30000002", 2))),))
    assert "H8" in rules(snapshot, DraftTimetable("TEST", snapshot.fingerprint, (session,)))


async def test_validator_h9_rejects_missing_reading_time():
    snapshot, draft = await valid_draft()
    module = replace(snapshot.modules[0], has_extra_reading_time=True, reading_time_minutes=10)
    changed = replace(snapshot, modules=(module, snapshot.modules[1]))
    assert "H9" in rules(changed, replace(draft, input_fingerprint=changed.fingerprint))


@pytest.mark.parametrize("mode", ["missing_module", "duplicate_module", "missing_seat", "duplicate_student", "unregistered_student"])
async def test_validator_h10_exactly_one_sitting_and_seat(mode):
    snapshot, draft = await valid_draft()
    if mode == "missing_module":
        bad = replace(draft, sessions=draft.sessions[1:])
    elif mode == "duplicate_module":
        bad = replace(draft, sessions=(*draft.sessions, replace(draft.sessions[0], session_id="EXTRA")))
    else:
        seats = {
            "missing_seat": (),
            "duplicate_student": (SeatAllocation("IT30000001", 1), SeatAllocation("IT30000001", 2)),
            "unregistered_student": (SeatAllocation("IT30000999", 1),),
        }[mode]
        bad = change_first(draft, rooms=(RoomAllocation("R1", seats),))
    assert "H10" in rules(snapshot, bad)


@pytest.mark.parametrize("mode", ["period", "fingerprint", "session_period", "module", "venue", "student", "session_id", "room", "seat"])
async def test_validator_h11_invalid_relationships(mode):
    snapshot, draft = await valid_draft()
    room = draft.sessions[0].rooms[0]
    if mode in ("period", "fingerprint"):
        bad = replace(draft, **{"period_code" if mode == "period" else "input_fingerprint": "WRONG"})
    elif mode == "session_period":
        bad = change_first(draft, period_code="WRONG")
    elif mode == "module":
        bad = change_first(draft, module_code="UNKNOWN")
    elif mode == "venue":
        bad = change_first(draft, rooms=(replace(room, venue_id="UNKNOWN"),))
    elif mode == "student":
        bad = change_first(draft, rooms=(replace(room, seats=(SeatAllocation("UNKNOWN", 1),)),))
    elif mode == "session_id":
        bad = change_first(draft, session_id=draft.sessions[1].session_id)
    elif mode == "room":
        bad = change_first(draft, rooms=(room, room))
    else:
        bad = change_first(draft, rooms=(replace(room, seats=(room.seats[0], room.seats[0])),))
    assert "H11" in rules(snapshot, bad)


async def test_validator_checks_room_limit_and_rejects_aware_or_subminute_times():
    snapshot, draft = await valid_draft()
    for start in (draft.sessions[0].start.replace(tzinfo=dt.UTC), draft.sessions[0].start.replace(second=1)):
        assert "H11" in rules(snapshot, change_first(draft, start=start))
    changed = replace(snapshot, policy=SchedulingPolicy(max_rooms_per_exam=1))
    room = draft.sessions[0].rooms[0]
    bad = change_first(replace(draft, input_fingerprint=changed.fingerprint), rooms=(room, replace(room, venue_id="R2")))
    assert "H11" in rules(changed, bad)
