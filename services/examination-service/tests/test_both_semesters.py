"""Isolated two-semester API tests: real CP-SAT, test tokens, synthetic inputs and transactional memory store."""
import asyncio
import copy
import dataclasses
import datetime as dt

import pytest

from app.generation.service import GenerationRequest
from app.saved_timetables import ListFilter
from app.scheduling.types import SolveResult, SolveStatus
from app.seed.records import Dataset, ExamPeriod, ExamPeriodModule, Module, ModuleSpecialization, Registration, Student, Venue
from app.sources import DatasetSource
from auth_support import INSTITUTION
from test_cross_timetable import ProtectedHarness
from test_saved_timetables import admin, client, identity, store, tokens  # noqa: F401
from test_timetable_generation import headers

S1, S2 = "LOCAL-S1-FINAL", "LOCAL-S2-FINAL"
GENERATE, SAVE = "/api/v1/timetables/generate/both-semesters", "/api/v1/timetables/drafts/both-semesters"
DAY = dt.date(2026, 11, 16)
A = "IT30000001"


def dataset():
    return Dataset(
        modules=(Module("IT2001", "Semester one", 2, 1, 3, 4, "written", 60, False, 0),
                 Module("IT1001", "Repeat in semester two", 1, 2, 3, 4, "written", 60, False, 0)),
        module_specializations=tuple(ModuleSpecialization(c, "IT") for c in ("IT2001", "IT1001")),
        students=(Student(A, 2, "IT"),),
        registrations=(Registration(A, "IT2001", "normal"), Registration(A, "IT1001", "repeat")),
        venues=(Venue("R1", "Room one", "lecture_hall", "R", 1, 4, 2),), venue_blocks=(),
        exam_periods=tuple(ExamPeriod(code, f"Semester {s}", "2026/2027", s, DAY, DAY,
            dt.time(8, 30), dt.time(17, 30), False, False) for code, s in ((S1, 1), (S2, 2))),
        exam_period_exclusions=(), exam_period_modules=(ExamPeriodModule(S1, "IT2001"), ExamPeriodModule(S2, "IT1001")))


@pytest.fixture
def harness(store):
    h = ProtectedHarness(store)
    h.source = DatasetSource(dataset())
    return h


def body(**changes):
    return {"cycles": [{"period_code": S1}, {"period_code": S2}], "mode": "feasibility", "time_limit_seconds": 5, **changes}


def count(store):
    return asyncio.run(store.list(INSTITUTION, ListFilter(), limit=20, offset=0))[1]


@pytest.mark.parametrize("mode", ["feasibility", "optimized"])
def test_both_cycles_reuse_real_solver_and_actual_repeat_assignments(client, admin, store, mode):
    response = client.post(GENERATE, headers=admin, json=body(mode=mode, include_analysis=True))
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["generated"] and data["pair_validation"] == {"passed": True, "violations": []}
    assert data["sequential_order"] == [S1, S2] and data["shared_candidates"] == 1
    first, second = data["results"]
    assert first["sessions"][0]["semester"] == 1 and second["sessions"][0]["semester"] == 2
    assert second["sessions"][0]["repeat_candidates"] == 1
    assert first["sessions"][0]["rooms"][0]["venue_id"] == second["sessions"][0]["rooms"][0]["venue_id"]
    assert first["sessions"][0]["start_time"] != second["sessions"][0]["start_time"]
    assert all(r["validation"]["passed"] for r in data["results"])
    assert first["inputs"]["scheduling_preferences"] == second["inputs"]["scheduling_preferences"]
    assert all(r["inputs"]["cross_timetable"]["protected_timetables"] == [] for r in data["results"])
    assert all(r["analysis"] is not None for r in data["results"])
    assert A not in response.text and "TEMPORARY_BATCH_PEER" not in response.text
    assert count(store) == 0


def test_separate_selected_calendars_and_shared_preferences_are_used_and_saved(client, admin, store):
    calendar = dict(start_date="2026-11-17", end_date="2026-11-17", day_start_time="08:30", day_end_time="17:30",
                    include_saturdays=False, include_sundays=False, exclusions=[])
    b = body(cycles=[{"period_code": S2, "calendar": calendar}, {"period_code": S1}],
             scheduling_preferences={"scheduling": {"minimum_break_minutes": 120}, "seating": {"default_percentage": 75}})
    saved = client.post(SAVE, headers=admin, json=b)
    assert saved.status_code == 201, saved.text
    first, second = saved.json()["timetables"]
    assert [first["period_code"], second["period_code"]] == [S1, S2]
    assert first["sessions"][0]["date"] == "2026-11-16" and second["sessions"][0]["date"] == "2026-11-17"
    assert first["inputs"]["batch"]["batch_id"] == second["inputs"]["batch"]["batch_id"]
    assert first["inputs"]["scheduling_preferences"] == second["inputs"]["scheduling_preferences"]
    assert first["status"] == second["status"] == "DRAFT" and count(store) == 2 and store.commits == 1
    # Each is approved and published separately through the existing workflow.
    for item in (first, second):
        base = f"/api/v1/timetables/{item['timetable_id']}"
        approved = client.post(base + "/approve", headers=admin, json={"expected_revision": 1, "reason": "Isolated test approval"})
        assert approved.status_code == 200, approved.text
        published = client.post(base + "/publish", headers=admin, json={"expected_revision": 2, "reason": "Isolated test publication"})
        assert published.status_code == 200, published.text


@pytest.mark.parametrize("failed_call, expected_results", [(1, 1), (2, 2)])
def test_partial_solver_failures_never_save_one_semester(client, admin, store, monkeypatch, failed_call, expected_results):
    import app.generation.service as service
    original = service.generate_timetable
    calls = 0

    def fail(snapshot, settings):
        nonlocal calls
        calls += 1
        return SolveResult(SolveStatus.INFEASIBLE) if calls == failed_call else original(snapshot, settings)

    monkeypatch.setattr(service, "generate_timetable", fail)
    preview = client.post(GENERATE, headers=admin, json=body())
    assert preview.status_code == 200, preview.text
    data = preview.json()
    assert not data["generated"] and data["pair_validation"]["passed"] is None and len(data["results"]) == expected_results
    assert data["skipped_period_codes"] == ([S2] if failed_call == 1 else [])
    calls = 0
    refused = client.post(SAVE, headers=admin, json=body())
    assert refused.status_code == 422 and count(store) == 0 and store.commits == 0


@pytest.mark.parametrize("failure", ["second_insert", "commit"])
def test_atomic_rollback_leaves_no_drafts_or_history(client, admin, store, monkeypatch, failure):
    original = store.save
    calls = 0

    async def fail(institution, timetable):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("Isolated second insert failure")
        return await original(institution, timetable)

    if failure == "second_insert":
        monkeypatch.setattr(store, "save", fail)
    else:
        store.fail_on = "commit"
    response = client.post(SAVE, headers=admin, json=body())
    assert response.status_code == 500 and count(store) == 0
    assert store._work.history == [] and store.commits == 0


def test_stale_inputs_are_revalidated_before_either_insert(client, admin, store, harness, monkeypatch):
    original = harness.generator

    def generator(lock):
        g = original(lock)
        generate = g.generate_both

        async def changed(requests):
            result = await generate(requests)
            harness.source._modules[0] = dataclasses.replace(harness.source._modules[0], module_name="Changed after solve")
            return result

        g.generate_both = changed
        return g

    monkeypatch.setattr(harness, "generator", generator)
    response = client.post(SAVE, headers=admin, json=body())
    assert response.status_code == 409, response.text
    assert "STALE_INPUTS" in response.text and count(store) == 0


@pytest.mark.parametrize("changes", [
    {"cycles": [{"period_code": S1}, {"period_code": S1}]}, {"cycles": [{"period_code": S1}]},
    {"scheduling_preferences": {"H1": False}}, {"time_limit_seconds": 301}, {"module_codes": ["IT2001"]},
])
def test_invalid_batch_inputs_are_rejected_without_writes(client, admin, store, changes):
    assert client.post(SAVE, headers=admin, json=body(**changes)).status_code == 422
    assert count(store) == 0


def test_authentication_and_permission_are_still_required(client, tokens, identity, store):
    assert client.post(GENERATE, json=body()).status_code == 401
    for route in (GENERATE, SAVE):
        response = client.post(route, headers=headers(tokens, identity, "STUDENT"), json=body())
        assert response.status_code == 403
    assert count(store) == 0


async def test_both_generation_keeps_existing_single_generation_busy_guard(harness):
    from app.generation.service import GenerationBusyError
    lock = asyncio.Lock()
    generator = harness.generator(lock)
    async with lock:
        with pytest.raises(GenerationBusyError):
            await generator.generate_both((GenerationRequest(S1, None), GenerationRequest(S2, None)))


def test_repeat_students_are_protected_even_with_another_free_room(client, admin, harness):
    original = dataset()
    harness.source = DatasetSource(dataclasses.replace(original,
        venues=(*original.venues, Venue("R2", "Room two", "lecture_hall", "R", 1, 4, 2))))
    response = client.post(GENERATE, headers=admin, json=body())
    assert response.status_code == 200, response.text
    first, second = [r["sessions"][0] for r in response.json()["results"]]
    start = lambda s: dt.datetime.fromisoformat(s["start"])
    assert abs((start(first) - start(second)).total_seconds()) >= 120 * 60


def test_existing_published_examinations_stay_protected_and_unchanged(client, admin, harness, store):
    from test_cross_timetable import put
    asyncio.run(put(store, "OTHER-PROTECTED", "PUBLISHED"))
    before = copy.deepcopy(store._committed)
    response = client.post(GENERATE, headers=admin, json=body())
    assert response.status_code == 200, response.text
    # With one room and only two starts, three sessions cannot fit. The protected slot is never dropped.
    assert not response.json()["generated"] and count(store) == 1
    assert store._committed == before
    assert all(r["inputs"]["cross_timetable"]["protected_timetables"][0]["period_code"] == "OTHER-PROTECTED"
               for r in response.json()["results"])


def test_wrong_academic_year_is_rejected_before_solving(client, admin, harness, store):
    original = dataset()
    harness.source = DatasetSource(dataclasses.replace(original, exam_periods=(original.exam_periods[0],
        dataclasses.replace(original.exam_periods[1], academic_year_label="2027/2028"))))
    response = client.post(SAVE, headers=admin, json=body())
    assert response.status_code == 422 and "BOTH_SEMESTERS" in response.text and count(store) == 0


async def test_peer_constraints_use_the_actual_split_session_for_each_student(harness):
    from app.exam_periods.rules import ExamCalendar
    from app.generation.both_semesters import peer_schedule, pair_violations
    original = dataset()
    extra = ("IT30000002", "IT30000003")
    harness.source = DatasetSource(dataclasses.replace(original,
        students=(*original.students, *(Student(s, 2, "IT") for s in extra)),
        registrations=(*original.registrations, *(Registration(s, "IT2001", "normal") for s in extra))))
    calendar = ExamCalendar(DAY, DAY + dt.timedelta(days=1), dt.time(8, 30), dt.time(17, 30), False, False, ())
    generator = harness.generator(asyncio.Lock())
    batch = await generator.generate_both(tuple(GenerationRequest(code, None, calendar=calendar, time_limit_seconds=5) for code in (S1, S2)))
    assert batch.generated and pair_violations(batch.outcomes) == ()
    first = batch.outcomes[0]
    assert len(first.timetable.sessions) >= 2
    assert sum(A in session.student_ids for _, session in peer_schedule(first).sessions) == 1


# Exactly what the Both Semesters page sends in Optimized mode: the recommended profile (4 workers, 120 s) inside the
# shared scheduling_preferences, with no top-level time_limit_seconds. Institution defaults here are the built-in 1 worker / 60 s.
BROWSER_OPTIMIZED_PREFERENCES = {
    "scheduling": {"permitted_start_times": ["08:30", "13:30"], "minimum_break_minutes": 60, "max_rooms_per_exam": None, "max_sessions_per_exam": 3},
    "solver": {"time_limit_seconds": 120, "random_seed": 20261019, "num_search_workers": 4},
    "optimization": {"s1_weight": 25, "s2_weight": 20, "s3_weight": 30, "s4_weight": 15, "s5_weight": 10, "short_gap_minutes": 720,
                     "preparation_target_minutes": 2880, "difficult_target_minutes": 4320, "high_difficulty_minimum": 4, "window_days": 3,
                     "preferred_window_exams": 2, "difficulty_weight": 50, "credits_weight": 25, "duration_weight": 25,
                     "reference_difficulty": 3, "reference_credits": 4, "reference_duration_minutes": 120, "baseline_budget_percent": 10},
    "fairness": {"enabled": False, "protect_worst_student": True, "max_individual_increase_points": 5, "max_worst_increase_points": 0},
    "seating": {"default_percentage": 50},
}


def test_optimized_both_semesters_gives_each_semester_four_workers_and_a_120_second_budget(client, admin, store, monkeypatch):
    from ortools.sat.python import cp_model

    solves = []

    class Spy(cp_model.CpSolver):
        def solve(self, model, *args, **kwargs):
            solves.append((self.parameters.num_search_workers, self.parameters.max_time_in_seconds))
            return super().solve(model, *args, **kwargs)

    monkeypatch.setattr(cp_model, "CpSolver", Spy)
    payload = {"cycles": [{"period_code": S1}, {"period_code": S2}], "mode": "optimized", "scheduling_preferences": BROWSER_OPTIMIZED_PREFERENCES}
    response = client.post(GENERATE, headers=admin, json=payload)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["generated"] and len(data["results"]) == 2
    # What the service reports for each semester ...
    assert [(r["solver"]["mode"], r["solver"]["num_search_workers"], r["solver"]["time_limit_seconds"]) for r in data["results"]] \
        == [("optimized", 4, 120.0)] * 2
    assert all(r["inputs"]["scheduling_preferences"]["settings"]["solver"]["num_search_workers"] == 4 for r in data["results"])
    # ... and what CP-SAT really received: every solve of both semesters uses 4 workers, each semester starts with its
    # own baseline of 10 % of 120 s, and none may run longer than the 120 s budget.
    assert solves and all(workers == 4 for workers, _ in solves)
    assert [limit for _, limit in solves if limit == pytest.approx(12.0)] == [pytest.approx(12.0)] * 2
    assert all(0 < limit <= 120 for _, limit in solves)
    assert count(store) == 0


def test_a_request_time_limit_can_only_lower_the_shared_120_second_profile(client, admin):
    base = {"cycles": [{"period_code": S1}, {"period_code": S2}], "mode": "feasibility", "scheduling_preferences": BROWSER_OPTIMIZED_PREFERENCES}
    lowered = client.post(GENERATE, headers=admin, json={**base, "time_limit_seconds": 30})
    assert lowered.status_code == 200, lowered.text
    assert [r["solver"]["time_limit_seconds"] for r in lowered.json()["results"]] == [30.0, 30.0]
    assert client.post(GENERATE, headers=admin, json={**base, "time_limit_seconds": 121}).status_code == 422
