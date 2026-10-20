"""Step 7: end-to-end MID and FINAL examination-cycle generation through the API (SYNTHETIC data, no database).

One combined synthetic dataset:

* FINAL cycle (PP1-2026-S2-FINAL): the synthetic cycle of ``python -m app.seed
  --with-synthetic-cycle``: every semester 2 module of years 1-4 (``all``) and
  three semester 1 modules re-examined for repeat candidates (``repeat_only``),
  with the 27 simulated venue blocks.
* MID cycle (SYNTHETIC-2026-S2-MID): a few modules marked with SYNTHETIC MID
  durations (one also Block Mode, one repeat-only), one simulated venue block.

Slow (full-cycle optimization); excluded with ``-m "not slow"``.
"""

from __future__ import annotations

import asyncio
import dataclasses
import datetime as dt
from collections import Counter

import pytest

from app.seed.assumptions import SeedAssumptions
from app.seed.cycles import build_synthetic_cycle
from app.seed.generator import generate_dataset
from app.seed.records import ExamPeriodModule, VenueBlock
from app.seed.validation import validate_dataset
from app.sources import DatasetSource
from test_timetable_generation import URL, Harness, admin, identity, make_client, tokens  # noqa: F401

pytestmark = pytest.mark.slow

FINAL, MID = "PP1-2026-S2-FINAL", "SYNTHETIC-2026-S2-MID"
OK = ("OPTIMAL", "FEASIBLE")


def _combined():
    plain = build_synthetic_cycle(generate_dataset())
    repeat_only = sorted(c.module_code for c in plain.exam_period_modules if c.candidate_scope == "repeat_only")
    by_year = {}
    for m in sorted(plain.modules, key=lambda m: m.module_code):
        if m.module_code not in repeat_only:
            by_year.setdefault(m.academic_year, []).append(m.module_code)
    mid_codes = [codes[0] for _, codes in sorted(by_year.items())] + [repeat_only[0]]  # one per year + a repeat-only
    block = mid_codes[1]
    assumptions = SeedAssumptions(synthetic_mid_exams=tuple((code, 60) for code in mid_codes),
                                  synthetic_block_modules=((block, "pro_rata"),))
    data = build_synthetic_cycle(generate_dataset(assumptions), assumptions=assumptions)
    [final] = data.exam_periods
    mid = dataclasses.replace(final, period_code=MID, period_name="SYNTHETIC MID examinations", exam_kind="MID",
                              start_date=dt.date(2026, 10, 5), end_date=dt.date(2026, 10, 9))
    lab = max((v for v in data.venues if v.venue_type == "computer_lab"), key=lambda v: v.normal_capacity)
    mid_block = VenueBlock(lab.venue_id, dt.date(2026, 10, 5), dt.time(8, 0), dt.time(17, 30),
                           "simulated_maintenance", "SIMULATED maintenance (Step 7 verification)")
    members = tuple(ExamPeriodModule(MID, code, "repeat_only" if code in repeat_only else "all") for code in mid_codes)
    data = dataclasses.replace(data, exam_periods=(final, mid), venue_blocks=data.venue_blocks + (mid_block,),
                               exam_period_modules=data.exam_period_modules + members)
    return data, mid_codes, block, lab


DATA, MID_CODES, BLOCK, BLOCKED_LAB = _combined()
MODULES = {m.module_code: m for m in DATA.modules}


def _generate(tokens, identity, admin, period, *, percentage=None, **settings):
    harness = Harness(DatasetSource(DATA), seating_percentage=percentage)
    if settings:
        asyncio.run(harness.save(**settings))
    response = make_client(tokens, identity, harness).post(URL, headers=admin, json={
        "period_code": period, "mode": "optimized", "time_limit_seconds": 60})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] in OK and data["generated"], (data["status"], data["diagnostics"], data["violations"])
    return data


def _common_checks(data, period, percentage=50):
    mapped = {c.module_code: c.candidate_scope for c in DATA.exam_period_modules if c.period_code == period}
    # Independent validation of the returned timetable (H1-H11); never returned otherwise.
    assert data["validation"] == {"passed": True, "rules_checked": [f"H{i}" for i in range(1, 12)], "violations": []}
    assert data["inputs"]["selection"] == "full_cycle" and data["inputs"]["modules"] == len(mapped)
    assert {s["module_code"] for s in data["sessions"]} == set(mapped)  # automatic loading from the mapping only
    full = {v.venue_id: v.normal_capacity for v in DATA.venues}
    for s in data["sessions"]:
        m = MODULES[s["module_code"]]
        assert s["candidate_scope"] == mapped[m.module_code]
        assert (s["academic_year"], s["semester"], s["delivery_mode"]) == (m.academic_year, m.semester, m.delivery_mode)
        start, end = dt.datetime.fromisoformat(s["start"]), dt.datetime.fromisoformat(s["end"])
        assert end - start == dt.timedelta(minutes=s["duration_minutes"] + s["reading_time_minutes"])  # reading time
        assert s["normal_candidates"] + s["repeat_candidates"] == s["candidates"]
        for room in s["rooms"]:  # configurable seating percentage
            assert room["exam_capacity"] == full[room["venue_id"]] * percentage // 100 >= room["seats"]
    # Every included registration is seated exactly once (normal and repeat).
    seated = Counter()
    for s in data["sessions"]:
        seated[s["module_code"], "normal"] += s["normal_candidates"]
        seated[s["module_code"], "repeat"] += s["repeat_candidates"]
    expected = Counter((r.module_code, r.registration_type) for r in DATA.registrations
                       if r.module_code in mapped and (mapped[r.module_code] == "all" or r.registration_type == "repeat"))
    assert +seated == expected
    # S1-S5 workload scores and fairness protection on the selected timetable.
    assert [p["constraint"] for p in data["workload"]["penalties"]] == ["S1", "S2", "S3", "S4", "S5"]
    fairness = data["optimization"]["fairness"]
    assert fairness["protection_applied"] and fairness["limits_satisfied"]
    return mapped


def test_final_cycle_end_to_end(tokens, identity, admin) -> None:
    data = _generate(tokens, identity, admin, FINAL, fairness__enabled=True)
    mapped = _common_checks(data, FINAL)
    sessions = data["sessions"]
    assert {s["academic_year"] for s in sessions} == {1, 2, 3, 4} and {s["semester"] for s in sessions} == {1, 2}
    # A module shared by several specializations is examined once, for all of them.
    assert any(sum(link.module_code == s["module_code"] for link in DATA.module_specializations) > 1 for s in sessions)
    repeat_only = [s for s in sessions if s["candidate_scope"] == "repeat_only"]
    assert len({s["module_code"] for s in repeat_only}) == 3
    assert all(s["normal_candidates"] == 0 and s["repeat_candidates"] > 0 for s in repeat_only)
    assert any(s["delivery_mode"] == "block" for s in sessions)  # the SYNTHETIC Block Mode module
    assert data["inputs"]["repeat_registrations"] > 0
    # Venue availability: no room is used while one of the 27 simulated blocks covers it.
    for s in sessions:
        start, end = dt.datetime.fromisoformat(s["start"]), dt.datetime.fromisoformat(s["end"])
        for room in s["rooms"]:
            assert not any(b.venue_id == room["venue_id"] and dt.datetime.combine(b.block_date, b.start_time) < end
                           and start < dt.datetime.combine(b.block_date, b.end_time) for b in DATA.venue_blocks)
    assert data["excluded_registrations"]["total"] == 0  # every registered module is in the cycle
    _summary("FINAL", data, len(mapped))


def test_mid_cycle_end_to_end_with_sessions_blocks_and_warnings(tokens, identity, admin) -> None:
    data = _generate(tokens, identity, admin, MID, percentage=60, fairness__enabled=True,
                     scheduling__max_rooms_per_exam=1, scheduling__max_sessions_per_exam=6,
                     scheduling__minimum_break_minutes=90)
    _common_checks(data, MID, percentage=60)
    assert data["inputs"]["exam_kind"] == "MID"
    sessions = data["sessions"]
    assert all(s["duration_minutes"] == 60 for s in sessions)  # MID durations, not FINAL ones
    assert {s["academic_year"] for s in sessions} == {1, 2, 3, 4}
    assert any(s["candidate_scope"] == "repeat_only" for s in sessions)
    assert any(s["module_code"] == BLOCK and s["delivery_mode"] == "block" for s in sessions)
    # Capacity-based multiple sessions: numbered, one room each, at different times.
    split = {}
    for s in sessions:
        split.setdefault(s["module_code"], []).append(s)
    multi = {code: rows for code, rows in split.items() if len(rows) > 1}
    assert multi and data["summary"]["multi_session_modules"] == len(multi)
    for rows in multi.values():
        rows.sort(key=lambda s: s["start"])
        assert [s["session_label"] for s in rows] == [f"Session {i:02d}" for i in range(1, len(rows) + 1)]
        assert len({s["start"] for s in rows}) == len(rows) and all(len(s["rooms"]) == 1 for s in rows)
    # Venue availability: the blocked lab is never used on its blocked day.
    assert not any(room["venue_id"] == BLOCKED_LAB.venue_id and s["date"] == "2026-10-05"
                   for s in sessions for room in s["rooms"])
    # Excluded-registration warning: FINAL-only modules, labelled with the cycle that maps them.
    excluded = data["excluded_registrations"]
    assert excluded["total"] > 0 and any(n.startswith("Warning:") for n in data["notices"])
    assert all(row["other_cycles"] == [FINAL] for row in excluded["modules"])
    assert not set(MID_CODES) & {row["module_code"] for row in excluded["modules"]}
    _summary("MID", data, len(MID_CODES))


def _summary(kind, data, modules):
    opt, fair = data["optimization"], data["optimization"]["fairness"]
    print(f"\n{kind}: status={data['status']} modules={modules} sessions={len(data['sessions'])} "
          f"split_modules={data['summary']['multi_session_modules']} registrations={data['inputs']['registrations']} "
          f"repeat={data['inputs']['repeat_registrations']} improved={opt['improved']} "
          f"fairness_ok={fair['limits_satisfied']} excluded={data['excluded_registrations']['total']} "
          f"solve_s={data['solver']['wall_time_seconds']}")


def test_combined_synthetic_dataset_is_valid() -> None:
    report = validate_dataset(DATA)
    assert report.ok, report.errors
