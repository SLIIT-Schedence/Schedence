"""Bounded, read-only research analysis of frozen inputs and actual assigned sessions.

Never alters assignments, scoring formulas or persistence. Only aggregate data leaves this module.
Consecutive gaps are pooled across students; S1-S3 pair counts use ALL pairs per student.
"""
from __future__ import annotations

import re
from collections import defaultdict
from itertools import combinations
from typing import TYPE_CHECKING

from app.scheduling.optimization import OptimizationResult
from app.scheduling.optimization_policy import SCORING_VERSION
from app.scheduling.validation import validate_timetable

if TYPE_CHECKING:
    from app.generation.service import GenerationOutcome

MAX_STUDENT_PAIRS = 100_000
MAX_PAIR_ROWS = 100
MAX_COHORTS = 128


def _text(value):
    return re.sub(r"[A-Z]{2}[0-9]{8}", "[student identifier omitted]", value, flags=re.I)


def _statistics(members, values, objectives, scale):
    gaps = sorted(g for student in members for g in values[student]["gaps"])
    n = len(gaps)
    total = sum(gaps)
    count = lambda key: sum(values[student][key] for student in members)
    return dict(students=len(members), registrations=count("registrations"),
                normal_registrations=count("normal"), repeat_registrations=count("repeat"),
                students_with_consecutive_exams=sum(bool(values[s]["gaps"]) for s in members),
                consecutive_gap_count=n, total_consecutive_gap_minutes=total,
                mean_gap_minutes=total / n if n else None,
                median_gap_minutes=(gaps[n // 2] + gaps[(n - 1) // 2]) / 2 if n else None,
                minimum_gap_minutes=gaps[0] if n else None,
                lower_decile_gap_minutes=gaps[(n - 1) // 10] if n else None,
                short_consecutive_gaps=count("short_consecutive"), all_exam_student_pairs=count("pairs"),
                short_gap_student_pairs=count("short"), difficult_exam_student_pairs=count("difficult"),
                difficult_pairs_below_target=count("below"), same_day_student_pairs=count("same_day"),
                mean_weighted_workload=(sum(objectives[s] for s in members) / (len(members) * scale)
                                        if members and objectives is not None else None))


def _exam(session):
    return dict(module_code=_text(session.module_code), session_number=session.session_number,
                date=session.start.date(), start_time=session.start.time(), end_time=session.end.time())


def _run(snapshot, timetable, evaluation, configuration, registrations, cohort_keys, total_cohorts):
    if timetable.input_fingerprint != snapshot.fingerprint or validate_timetable(snapshot, timetable):
        return None, "Independent H1-H11 validation did not pass for these frozen inputs."
    policy = configuration.optimization
    modules = {m.module_code: m for m in snapshot.modules}
    # H10/H11 verify exactly one assigned sitting for every registered student/module.
    sitting = {(seat.student_id, session.module_code): session
               for session in timetable.sessions for room in session.rooms for seat in room.seats}
    values, pair_rows = {}, {}
    students = {s.student_id: s for s in snapshot.students}
    cohorts = {key: [] for key in cohort_keys}
    for student, rows in registrations.items():
        ordered = sorted((sitting[student, r.module_code] for r in rows), key=lambda s: (s.start, s.module_code))
        gaps = [int((b.start - a.end).total_seconds() // 60) for a, b in zip(ordered, ordered[1:])]
        repeat = any(r.registration_type == "repeat" for r in rows)
        own = dict(gaps=gaps, registrations=len(rows), normal=sum(r.registration_type == "normal" for r in rows),
                   repeat=sum(r.registration_type == "repeat" for r in rows),
                   short_consecutive=sum(g < policy.short_gap_minutes for g in gaps), pairs=0, short=0,
                   difficult=0, below=0, same_day=0)
        for a, b in combinations(ordered, 2):
            gap = int((b.start - a.end).total_seconds() // 60)
            high = min(modules[a.module_code].difficulty, modules[b.module_code].difficulty) >= policy.high_difficulty_minimum
            own["pairs"] += 1
            own["short"] += gap < policy.short_gap_minutes
            own["same_day"] += a.start.date() == b.start.date()
            own["difficult"] += high
            own["below"] += high and gap < policy.difficult_target_minutes
            if high:
                key = (a.session_id, b.session_id)
                if key not in pair_rows:
                    pair_rows[key] = dict(first=_exam(a), second=_exam(b), gap_minutes=gap,
                                          below_target=gap < policy.difficult_target_minutes,
                                          shared_candidates=0, normal_only_candidates=0, repeat_candidates=0)
                pair_rows[key]["shared_candidates"] += 1
                pair_rows[key]["repeat_candidates" if repeat else "normal_only_candidates"] += 1
        values[student] = own
        person = students[student]
        for key in _student_cohorts(person, repeat):
            if key in cohorts:
                cohorts[key].append(student)
    objectives = {s.student_id: s.objective_units for s in evaluation.student_penalties} if evaluation else None
    if objectives is not None and objectives.keys() != registrations.keys():
        return None, "Workload evaluation does not match the registered student population."
    statistics = _statistics(list(registrations), values, objectives, policy.score_scale)
    # Existing independent evaluator remains authoritative; mismatches fail closed.
    if evaluation and (evaluation.input_fingerprint != snapshot.fingerprint
                       or evaluation.configuration_fingerprint != policy.fingerprint
                       or statistics["minimum_gap_minutes"] != evaluation.minimum_gap_minutes
                       or statistics["median_gap_minutes"] != evaluation.median_gap_minutes
                       or statistics["lower_decile_gap_minutes"] != evaluation.lower_decile_gap_minutes
                       or statistics["same_day_student_pairs"] != evaluation.same_day_student_pairs
                       or statistics["mean_weighted_workload"] != evaluation.total_weighted_penalty
                       or statistics["short_gap_student_pairs"] != evaluation.short_gap_student_pairs
                       or statistics["difficult_pairs_below_target"] != evaluation.difficult_pairs_below_target):
        return None, "Analysis does not match the independent workload evaluation."
    pairs = sorted(pair_rows.values(), key=lambda p: (not p["below_target"], p["gap_minutes"],
                   p["first"]["module_code"], p["first"]["session_number"], p["second"]["module_code"], p["second"]["session_number"]))
    return dict(input_fingerprint=snapshot.fingerprint, configuration_fingerprint=configuration.fingerprint,
                validation=dict(passed=True, rules_checked=[f"H{i}" for i in range(1, 12)]), statistics=statistics,
                workload=(dict(objective_units=evaluation.objective_units, total_weighted_penalty=evaluation.total_weighted_penalty,
                               penalties=[dict(constraint=p.constraint, weight=p.weight, penalty_points=p.penalty_points,
                                               weighted_points=p.weighted_points) for p in evaluation.penalties]) if evaluation else None),
                cohorts=[dict(academic_year=y, specialization=_text(s) if s else None, registration_group=g,
                              statistics=_statistics(members, values, objectives, policy.score_scale))
                         for (y, s, g), members in cohorts.items() if members],
                cohorts_total=total_cohorts, cohorts_truncated=total_cohorts > MAX_COHORTS,
                difficult_pairs=pairs[:MAX_PAIR_ROWS], difficult_pair_rows_total=len(pairs),
                difficult_pair_rows_truncated=len(pairs) > MAX_PAIR_ROWS), None


def _student_cohorts(student, repeat):
    years = (None, student.academic_year) if 1 <= student.academic_year <= 4 else (None,)
    specializations = (None, student.specialization) if student.specialization.strip() else (None,)
    return {(y, s, group) for y in years for s in specializations
            for group in ("all", "includes_repeat" if repeat else "normal_only")}


def build_analysis(outcome: GenerationOutcome):
    snapshot, configuration = outcome.snapshot, outcome.configuration
    policy = configuration.optimization
    registrations = defaultdict(list)
    for row in snapshot.registrations:
        registrations[row.student_id].append(row)
    students = {s.student_id: s for s in snapshot.students}
    keys = {key for student, rows in registrations.items()
            for key in _student_cohorts(students[student], any(r.registration_type == "repeat" for r in rows))}
    # Prefer global, then single-dimension cohorts before intersections when the cap is reached.
    cohort_keys = sorted(keys, key=lambda k: (int(k[0] is not None) + int(k[1] is not None), k[0] or 0, k[1] or "", k[2]))[:MAX_COHORTS]
    common = dict(version="preparation-gap-v1", input_fingerprint=snapshot.fingerprint,
                  configuration_fingerprint=configuration.fingerprint,
                  policy=dict(high_difficulty_minimum=policy.high_difficulty_minimum, short_gap_minutes=policy.short_gap_minutes,
                              preparation_target_minutes=policy.preparation_target_minutes, difficult_target_minutes=policy.difficult_target_minutes,
                              scoring_version=SCORING_VERSION, scoring_fingerprint=policy.fingerprint),
                  modules=[dict(module_code=_text(m.module_code), module_name=_text(m.module_name), academic_year=m.academic_year,
                                semester=m.semester, specializations=[_text(s) for s in m.specializations],
                                difficulty=m.difficulty if 1 <= m.difficulty <= 5 else None,
                                difficult=m.difficulty >= policy.high_difficulty_minimum if 1 <= m.difficulty <= 5 else None)
                           for m in sorted(snapshot.modules, key=lambda m: m.module_code)],
                  limits=dict(max_student_pairs=MAX_STUDENT_PAIRS, max_pair_rows=MAX_PAIR_ROWS, max_cohorts=MAX_COHORTS),
                  cohort_membership_available=sum(1 <= students[s].academic_year <= 4 and bool(students[s].specialization.strip()) for s in registrations))
    baseline = outcome.result.baseline if isinstance(outcome.result, OptimizationResult) else None
    baseline_evaluation = outcome.result.baseline_evaluation if isinstance(outcome.result, OptimizationResult) else None
    reason = None
    if sum(len(rows) * (len(rows) - 1) // 2 for rows in registrations.values()) > MAX_STUDENT_PAIRS:
        reason = "Analysis omitted: this population exceeds the bounded student-pair analysis limit. Existing workload scores remain available."
    elif any(not 1 <= m.difficulty <= 5 for m in snapshot.modules):
        reason = "Analysis unavailable: some frozen module difficulty levels are outside 1-5."
    before, before_reason = (None, reason or "No feasibility baseline was returned for this run.")
    after, after_reason = (None, reason or "No independently validated selected timetable was returned.")
    if not reason and baseline is not None:
        before, before_reason = _run(snapshot, baseline, baseline_evaluation, configuration, registrations, cohort_keys, len(keys))
    if not reason and outcome.timetable is not None:
        if baseline is outcome.timetable and before is not None:
            after, after_reason = before, None
        else:
            after, after_reason = _run(snapshot, outcome.timetable, outcome.evaluation, configuration, registrations, cohort_keys, len(keys))
    return dict(**common, baseline=before, selected=after, baseline_unavailable_reason=before_reason,
                selected_unavailable_reason=after_reason)
