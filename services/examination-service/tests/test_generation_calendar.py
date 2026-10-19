"""Per-generation exam calendar: generate, save, approve and regenerate with admin-selected dates.

The saved period (PP1-2026-S2-FINAL, 16-28 Nov 2026) is never changed. A selected calendar replaces its
dates, hours, weekends and excluded dates inside the generation snapshot only, so CP-SAT, the independent
H1-H11 validator, venue blocks and the input fingerprint all use exactly that calendar. Saved drafts keep
it, and approval re-checks against it, never against the saved period's dates.

In-memory stores and the real CP-SAT solver, through the harness of test_timetable_generation.
"""

from __future__ import annotations

import dataclasses
import datetime as dt

import pytest

pytest.importorskip("ortools")

from app.exam_periods.rules import ExamCalendar, apply_calendar, calendar_document, saved_calendar  # noqa: E402
from app.generation.service import TimetableGenerator  # noqa: E402
from app.saved_timetables.memory import MemoryTimetableStore  # noqa: E402
from app.scheduling.inputs import SnapshotError, load_snapshot  # noqa: E402
from app.seating.memory import MemorySeatingStore  # noqa: E402
from app.scheduling.policy import SchedulingPolicy  # noqa: E402
from auth_support import FakeIdentityService  # noqa: E402
from schedence_auth.testing import TokenFactory  # noqa: E402
from test_saved_timetables import DRAFTS, LIST, client  # noqa: E402, F401  (fixture)
from test_timetable_generation import PERIOD, SOURCE, URL, Harness, body, headers  # noqa: E402

REASON = "Checked by the examination board"
# Two weeks after the saved period; Thursday 3 December is excluded.
SELECTED = {
    "start_date": "2026-11-30", "end_date": "2026-12-11", "day_start_time": "09:00", "day_end_time": "17:00",
    "include_saturdays": False, "include_sundays": False,
    "exclusions": [{"excluded_date": "2026-12-03", "reason": "University holiday"}],
}
ALLOWED = {dt.date(2026, 11, 30) + dt.timedelta(days=i) for i in range(12)} - {dt.date(2026, 12, 3)}
ALLOWED = {day for day in ALLOWED if day.weekday() < 5}


class CalendarHarness(Harness):
    """The generation harness with a loader that accepts the selected calendar, like the database loader."""

    def generator(self, lock) -> TimetableGenerator:
        async def load_settings():
            from app.scheduling_settings.service import SchedulingSettingsService
            from auth_support import INSTITUTION
            return await SchedulingSettingsService(self.settings, INSTITUTION).active()

        async def load(period_code, policy, module_codes, calendar=None):
            self.loads += 1
            return await load_snapshot(self.source, MemorySeatingStore(), period_code=period_code, policy=policy,
                                       module_codes=module_codes, from_cycle=True, calendar=calendar)

        return TimetableGenerator(load, load_settings, lock)


@pytest.fixture
def tokens() -> TokenFactory:
    return TokenFactory()


@pytest.fixture
def identity() -> FakeIdentityService:
    return FakeIdentityService()


@pytest.fixture
def harness() -> CalendarHarness:
    # Permitted starts 08:30/13:30: 08:30 is before 09:00, so only 13:30 starts remain inside the selected hours.
    return CalendarHarness()


@pytest.fixture
def store() -> MemoryTimetableStore:
    return MemoryTimetableStore()


@pytest.fixture
def admin(tokens, identity) -> dict[str, str]:
    return headers(tokens, identity, "EXAMINATION_ADMIN")


def _dates(sessions) -> set[dt.date]:
    return {dt.date.fromisoformat(s["date"]) for s in sessions}


def test_generation_uses_exactly_the_selected_calendar(client, admin) -> None:
    before = dataclasses.asdict(SOURCE._periods[0])
    response = client.post(URL, headers=admin, json=body(calendar=SELECTED))
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["generated"] and data["validation"]["passed"] is True
    inputs = data["inputs"]
    assert (inputs["calendar_source"], inputs["start_date"], inputs["end_date"]) == ("selected", "2026-11-30", "2026-12-11")
    assert (inputs["day_start_time"], inputs["day_end_time"]) == ("09:00:00", "17:00:00")
    assert inputs["exclusions"] == [{"excluded_date": "2026-12-03", "reason": "University holiday"}]
    assert {dt.date.fromisoformat(d) for d in inputs["exam_days"]} == ALLOWED
    assert _dates(data["sessions"]) <= ALLOWED
    assert all(s["start_time"] >= "09:00:00" and s["end_time"] <= "17:00:00" for s in data["sessions"])
    assert dataclasses.asdict(SOURCE._periods[0]) == before  # the saved period is unchanged


def test_without_a_calendar_the_saved_period_is_used_as_before(client, admin) -> None:
    data = client.post(URL, headers=admin, json=body()).json()
    assert data["inputs"]["calendar_source"] == "period"
    assert (data["inputs"]["start_date"], data["inputs"]["end_date"]) == ("2026-11-16", "2026-11-28")
    assert _dates(data["sessions"]) <= {dt.date.fromisoformat(d) for d in data["inputs"]["exam_days"]}


@pytest.mark.parametrize("changes, rule", [
    ({"end_date": "2026-11-29"}, "H6"),  # before the start
    ({"start_date": "2027-08-02", "end_date": "2027-12-20"}, "H6"),  # longer than 120 days and outside 2026/2027
    ({"day_start_time": "17:00", "day_end_time": "09:00"}, "H7"),
    ({"exclusions": [{"excluded_date": "2026-12-20", "reason": "Outside the range"}]}, "H8"),
    ({"start_date": "2026-12-05", "end_date": "2026-12-06", "exclusions": []}, "H8"),  # a weekend only
])
def test_an_invalid_calendar_is_refused_before_solving(client, admin, harness, changes, rule) -> None:
    response = client.post(URL, headers=admin, json=body(calendar={**SELECTED, **changes}))
    assert response.status_code == 422, response.text
    assert rule in {p["rule"] for p in response.json()["detail"]}
    assert "Selected calendar" in response.text


@pytest.mark.parametrize("calendar", [
    {**SELECTED, "unknown": 1}, {**SELECTED, "include_saturdays": "no"}, {k: v for k, v in SELECTED.items() if k != "exclusions"},
    {**SELECTED, "start_date": "30/11/2026"},
])
def test_malformed_calendars_are_rejected(client, admin, harness, calendar) -> None:
    assert client.post(URL, headers=admin, json=body(calendar=calendar)).status_code == 422
    assert harness.loads == 0


def test_a_saved_draft_keeps_its_calendar_and_is_approved_against_it(client, admin, store) -> None:
    saved = client.post(DRAFTS, headers=admin, json=body(calendar=SELECTED))
    assert saved.status_code == 201, saved.text
    draft = saved.json()
    assert draft["inputs"]["calendar_source"] == "selected" and draft["inputs"]["start_date"] == "2026-11-30"
    assert {dt.date.fromisoformat(s["date"]) for s in draft["sessions"]} <= ALLOWED
    # Approval rebuilds the inputs with the stored calendar: not stale, although the saved period is in November.
    approved = client.post(f"{LIST}/{draft['timetable_id']}/approve", headers=admin,
                           json={"expected_revision": 1, "reason": REASON})
    assert approved.status_code == 200, approved.text
    published = client.post(f"{LIST}/{draft['timetable_id']}/publish", headers=admin,
                            json={"expected_revision": 2, "reason": REASON})
    assert published.status_code == 200, published.text
    # Regenerating keeps the source timetable's calendar.
    again = client.post(f"{LIST}/{draft['timetable_id']}/regenerate", headers=admin, json={})
    assert again.status_code == 201, again.text
    assert again.json()["inputs"]["start_date"] == "2026-11-30" and again.json()["inputs"]["calendar_source"] == "selected"


def test_a_period_calendar_draft_still_approves_against_the_period(client, admin) -> None:
    draft = client.post(DRAFTS, headers=admin, json=body()).json()
    assert draft["inputs"]["calendar_source"] == "period"
    approved = client.post(f"{LIST}/{draft['timetable_id']}/approve", headers=admin, json={"expected_revision": 1, "reason": REASON})
    assert approved.status_code == 200, approved.text


def test_an_unreadable_stored_calendar_blocks_approval(client, admin, store) -> None:
    draft = client.post(DRAFTS, headers=admin, json=body(calendar=SELECTED)).json()
    for state in (store._committed, store._work):  # corrupt the stored calendar behind the API
        for _, _, header in state.timetables:
            if header["timetable_id"] == draft["timetable_id"]:
                header["inputs"] = {**header["inputs"], "start_date": "not a date"}
    refused = client.post(f"{LIST}/{draft['timetable_id']}/approve", headers=admin, json={"expected_revision": 1, "reason": REASON})
    assert refused.status_code == 409 and "STALE" in refused.text


# --- Rules -------------------------------------------------------------------------------------------------------

def _calendar(**changes) -> ExamCalendar:
    values = dict(start_date=dt.date(2026, 11, 30), end_date=dt.date(2026, 12, 11), day_start_time=dt.time(9),
                  day_end_time=dt.time(17), include_saturdays=False, include_sundays=False,
                  exclusions=((dt.date(2026, 12, 3), "University holiday"),))
    return ExamCalendar(**{**values, **changes})


def test_stored_calendars_round_trip_and_older_timetables_use_the_period() -> None:
    calendar = _calendar()
    stored = {"calendar_source": "selected", **calendar_document(calendar)}
    assert saved_calendar(stored) == calendar
    assert saved_calendar(None) is None
    assert saved_calendar({"start_date": "2026-11-16"}) is None  # saved before calendars were recorded
    assert saved_calendar({**stored, "calendar_source": "period"}) is None
    for broken in ({**stored, "end_date": "x"}, {**stored, "include_sundays": "no"}, {"calendar_source": "selected"},
                   {**stored, "calendar_source": "custom"}):
        with pytest.raises(ValueError):
            saved_calendar(broken)


async def test_the_snapshot_period_and_fingerprint_follow_the_calendar() -> None:
    period = SOURCE._periods[0]
    selected, problems = apply_calendar(period, _calendar())
    assert problems == [] and set(selected.exam_days) == ALLOWED
    assert (selected.period_code, selected.semester, selected.exam_kind) == (period.period_code, period.semester, period.exam_kind)
    load = dict(period_code=PERIOD, policy=SchedulingPolicy(), module_codes=("IT1010", "IT1030"), from_cycle=True)
    plain = await load_snapshot(SOURCE, MemorySeatingStore(), **load)
    chosen = await load_snapshot(SOURCE, MemorySeatingStore(), **load, calendar=_calendar())
    assert chosen.period.start_date == dt.date(2026, 11, 30) and plain.period.start_date == dt.date(2026, 11, 16)
    assert chosen.fingerprint != plain.fingerprint
    assert all(block.block_date >= dt.date(2026, 11, 30) for block in chosen.blocks)  # blocks of the selected dates
    with pytest.raises(SnapshotError):
        await load_snapshot(SOURCE, MemorySeatingStore(), **load, calendar=_calendar(end_date=dt.date(2026, 11, 1)))
