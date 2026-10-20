"""Aggregate research evidence on in-memory snapshots. No database or persistence."""
import datetime as dt
import json
from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest

from app.api.analysis_schemas import EvaluationAnalysisOut
from app.generation.analysis import build_analysis
from app.scheduling.configuration import SchedulingConfiguration
from app.scheduling.evaluation import evaluate_timetable
from app.scheduling.optimization import OptimizationResult
from app.scheduling.service import generate_timetable
from app.scheduling.types import SolveResult, SolveStatus
from optimization_support import optimization_case, starts_on, timed_draft
from scheduling_support import MONDAY, make_case

A, B = "IT30000001", "IT30000002"


def outcome(snapshot, selected, baseline=None, configuration=None):
    configuration = configuration or SchedulingConfiguration(scheduling=snapshot.policy)
    measured = evaluate_timetable(snapshot, selected, configuration.optimization) if selected else None
    before = evaluate_timetable(snapshot, baseline, configuration.optimization) if baseline else None
    result = (OptimizationResult(SolveStatus.FEASIBLE, timetable=selected, baseline=baseline,
                                baseline_evaluation=before, selected_evaluation=measured)
              if baseline else SolveResult(SolveStatus.FEASIBLE, selected))
    return SimpleNamespace(snapshot=snapshot, configuration=configuration, timetable=selected, result=result, evaluation=measured)


async def case():
    snapshot = await optimization_case(3, difficulties=(5, 4, 1), candidates=((A, B), (A,), (A,)))
    snapshot = replace(snapshot, students=tuple(replace(s, academic_year=2, specialization="DS") if s.student_id == A else s
                                              for s in snapshot.students))
    starts = (starts_on(0)[0], dt.datetime.combine(MONDAY, dt.time(13, 30)), starts_on(1)[0])
    draft = timed_draft(snapshot, starts)
    configuration = SchedulingConfiguration(scheduling=snapshot.policy)
    configuration = replace(configuration, optimization=replace(configuration.optimization,
                            short_gap_minutes=2000, preparation_target_minutes=3000, difficult_target_minutes=5000))
    return snapshot, draft, configuration


async def test_exact_denominators_all_pairs_cohorts_repeats_and_privacy():
    snapshot, draft, configuration = await case()
    before = asdict(snapshot), asdict(draft)
    report = build_analysis(outcome(snapshot, draft, configuration=configuration))
    stats = report["selected"]["statistics"]
    assert stats["students"] == 2 and stats["registrations"] == 4
    assert stats["students_with_consecutive_exams"] == 1
    assert stats["consecutive_gap_count"] == 2
    assert stats["total_consecutive_gap_minutes"] == 1320
    assert stats["mean_gap_minutes"] == stats["median_gap_minutes"] == 660
    assert stats["minimum_gap_minutes"] == stats["lower_decile_gap_minutes"] == 240
    assert stats["short_consecutive_gaps"] == 2 and stats["short_gap_student_pairs"] == 3
    assert stats["all_exam_student_pairs"] == 3
    assert stats["difficult_exam_student_pairs"] == stats["difficult_pairs_below_target"] == 1
    assert stats["normal_registrations"] == stats["repeat_registrations"] == 2
    cohorts = report["selected"]["cohorts"]
    own = next(c for c in cohorts if (c["academic_year"], c["specialization"], c["registration_group"]) == (2, "DS", "includes_repeat"))
    assert own["statistics"]["registrations"] == 3 and own["statistics"]["students"] == 1
    regular = next(c for c in cohorts if (c["academic_year"], c["specialization"], c["registration_group"]) == (1, "IT", "normal_only"))
    assert regular["statistics"]["consecutive_gap_count"] == 0 and regular["statistics"]["mean_gap_minutes"] is None
    assert regular["statistics"]["mean_weighted_workload"] == 0
    pair = report["selected"]["difficult_pairs"][0]
    assert pair["shared_candidates"] == pair["repeat_candidates"] == 1 and pair["normal_only_candidates"] == 0
    assert pair["gap_minutes"] == 240 and pair["first"]["date"] == MONDAY
    serialized = EvaluationAnalysisOut.model_validate(report).model_dump_json()
    assert A not in serialized and B not in serialized and "student_id" not in serialized and "student_penalties" not in serialized
    assert (asdict(snapshot), asdict(draft)) == before


async def test_baseline_selected_same_inputs_configuration_and_independent_scores():
    snapshot, baseline, configuration = await case()
    selected = timed_draft(snapshot, starts_on(0, 2, 4))
    generated = outcome(snapshot, selected, baseline, configuration)
    report = build_analysis(generated)
    assert report["baseline"]["input_fingerprint"] == report["selected"]["input_fingerprint"] == snapshot.fingerprint
    assert report["baseline"]["configuration_fingerprint"] == report["selected"]["configuration_fingerprint"] == configuration.fingerprint
    for key, measured in (("baseline", generated.result.baseline_evaluation), ("selected", generated.evaluation)):
        stats = report[key]["statistics"]
        assert stats["mean_weighted_workload"] == measured.total_weighted_penalty
        assert report[key]["workload"]["total_weighted_penalty"] == measured.total_weighted_penalty
        assert len(report[key]["workload"]["penalties"]) == 5
        assert stats["difficult_pairs_below_target"] == measured.difficult_pairs_below_target
        assert stats["minimum_gap_minutes"] == measured.minimum_gap_minutes
        assert report[key]["validation"] == dict(passed=True, rules_checked=[f"H{i}" for i in range(1, 12)])
    assert report["selected"]["statistics"]["mean_gap_minutes"] > report["baseline"]["statistics"]["mean_gap_minutes"]


async def test_actual_split_sitting_drives_gap_not_first_module_session():
    snapshot, _, _ = await make_case(candidates=((A, B, "IT30000003", "IT30000004"), (A,)),
        policy=__import__("app.scheduling.policy", fromlist=["SchedulingPolicy"]).SchedulingPolicy(max_rooms_per_exam=1),
        venue_types=("lecture_hall",) * 3, full_capacities=(4, 4, 4), end_date=MONDAY + dt.timedelta(days=1))
    snapshot = replace(snapshot, modules=tuple(replace(m, difficulty=5) for m in snapshot.modules))
    draft = generate_timetable(snapshot).timetable
    report = build_analysis(outcome(snapshot, draft))
    actual = sorted((s for s in draft.sessions if any(seat.student_id == A for r in s.rooms for seat in r.seats)), key=lambda s: s.start)
    expected = int((actual[1].start - actual[0].end).total_seconds() // 60)
    assert report["selected"]["statistics"]["minimum_gap_minutes"] == expected
    pair = report["selected"]["difficult_pairs"][0]
    assert pair["gap_minutes"] == expected
    assert pair["first"]["session_number"] == actual[0].session_number
    assert pair["second"]["session_number"] == actual[1].session_number


async def test_gap_uses_end_including_reserved_reading_time():
    snapshot = await optimization_case(2, difficulties=(5, 5), readings=(15, 15))
    draft = timed_draft(snapshot, starts_on(0, 1))
    report = build_analysis(outcome(snapshot, draft))
    assert report["selected"]["statistics"]["mean_gap_minutes"] == 1440 - 75
    assert report["selected"]["difficult_pairs"][0]["gap_minutes"] == 1440 - 75


async def test_bounds_omit_expensive_analysis_and_truncate_details_without_changing_totals(monkeypatch):
    import app.generation.analysis as analysis
    snapshot, draft, configuration = await case()
    snapshot = replace(snapshot, modules=tuple(replace(m, difficulty=5) for m in snapshot.modules))
    draft = replace(draft, input_fingerprint=snapshot.fingerprint)
    generated = outcome(snapshot, draft, configuration=configuration)
    monkeypatch.setattr(analysis, "MAX_PAIR_ROWS", 1)
    monkeypatch.setattr(analysis, "MAX_COHORTS", 2)
    report = build_analysis(generated)
    assert len(report["selected"]["difficult_pairs"]) == 1
    assert report["selected"]["difficult_pair_rows_total"] == 3
    assert report["selected"]["difficult_pair_rows_truncated"]
    assert report["selected"]["statistics"]["difficult_exam_student_pairs"] == 3
    assert len(report["selected"]["cohorts"]) <= 2 and report["selected"]["cohorts_truncated"]
    monkeypatch.setattr(analysis, "MAX_STUDENT_PAIRS", 2)
    report = build_analysis(generated)
    assert report["selected"] is None and "limit" in report["selected_unavailable_reason"]


async def test_stale_or_mismatched_evaluation_fails_closed_and_single_exam_has_no_fake_gaps():
    snapshot, draft, configuration = await case()
    generated = outcome(snapshot, draft, configuration=configuration)
    generated.timetable = replace(draft, input_fingerprint="stale")
    assert build_analysis(generated)["selected"] is None
    generated.timetable = draft
    generated.evaluation = replace(generated.evaluation, configuration_fingerprint="stale")
    assert build_analysis(generated)["selected"] is None
    snapshot = await optimization_case(1, difficulties=(5,))
    report = build_analysis(outcome(snapshot, timed_draft(snapshot, starts_on(0))))
    stats = report["selected"]["statistics"]
    assert stats["mean_gap_minutes"] is None and stats["minimum_gap_minutes"] is None
    assert stats["consecutive_gap_count"] == stats["all_exam_student_pairs"] == 0
    assert report["baseline"] is None


# API fixture uses the same authenticated in-memory generator as existing tests; never Neon.
from test_generation_calendar import SELECTED, admin, harness, identity, tokens  # noqa: E402,F401
from test_timetable_generation import body, URL, client, headers  # noqa: E402,F401


def test_generate_opt_in_calendar_preferences_privacy_and_no_persistence(client, admin):
    legacy = client.post(URL, headers=admin, json=body()).json()
    assert legacy["analysis"] is None
    response = client.post(URL, headers=admin, json=body(mode="optimized", calendar=SELECTED,
        scheduling_preferences={"optimization": {"high_difficulty_minimum": 3}}, include_analysis=True))
    assert response.status_code == 200, response.text
    generated = response.json()
    analysis = generated["analysis"]
    assert analysis["policy"]["high_difficulty_minimum"] == 3
    assert analysis["input_fingerprint"] == generated["inputs"]["input_fingerprint"]
    assert analysis["configuration_fingerprint"] == generated["inputs"]["settings_fingerprint"]
    assert generated["inputs"]["start_date"] == SELECTED["start_date"]
    assert analysis["selected"]["statistics"]["short_gap_student_pairs"] == generated["workload"]["short_gap_student_pairs"]
    assert "student_id" not in json.dumps(analysis)
    assert generated["validation"]["passed"] is True
    assert "timetable_id" not in generated


def test_analysis_does_not_expand_authorization(client):
    assert client.post(URL, json=body(include_analysis=True)).status_code == 401


def test_analysis_is_not_available_to_academic_admins(client, tokens, identity):
    response = client.post(URL, headers=headers(tokens, identity, "ACADEMIC_ADMIN"), json=body(include_analysis=True))
    assert response.status_code == 403
