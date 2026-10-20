"""Explicit synthetic inputs and bounded instrumentation for the Step 11C audit.

No local workbooks, database, environment variables or real staff are read.
Benchmark instrumentation is single-threaded and scoped to this example run.
"""

from collections import Counter
from datetime import date
from time import perf_counter
from unittest.mock import patch

from ortools.sat.python import cp_model

from schedence_teaching.consecutive import ConsecutivePolicy
from schedence_teaching.fairness import FairnessPolicy
from schedence_teaching.preferences import (
    LecturerPreferenceInput, PreferenceCalendar, PreferenceScoringPolicy, prepare_lecturer_preferences,
)
from schedence_teaching.preparation import prepare_group_combinations
from schedence_teaching.schemas import GroupCombinationInput
from schedence_teaching.solver import (
    ConsecutiveOptimizationConfig, FairnessOptimizationConfig, PreferenceOptimizationConfig,
    SolverConfig, SolverStatus, StaffSchedulingInput, VenueSchedulingInput, WorkPeriodSolverConfig,
    solve_timetable, validate_consecutive_result,
)
from schedence_teaching.workload import (
    Weekday, WeeklyWorkloadInput, WorkPeriodInput, confirmed_campus_policy, prepare_work_periods,
)


FIRST, LAST, WEEK = date(2026, 10, 19), date(2026, 10, 25), "SYNTHETIC-AUDIT-WEEK"
DATES = {"MONDAY": FIRST, "TUESDAY": date(2026, 10, 20), "SATURDAY": date(2026, 10, 24)}
PROVENANCE = dict(source="synthetic-integration-fixture", reference="synthetic:example-only")


def window(start, end):
    return dict(start_minute=start, end_minute=end)


def authorization(staff_id, policy_reference):
    return dict(staff_id=staff_id, policy_reference=policy_reference, status="APPROVED",
        approval_reference="synthetic:approved-for-example-only", provenance=PROVENANCE,
        effective_from=FIRST, effective_until=LAST)


def build_case(*, copies=1, layout="mixed", administrative_mode="fixed", time_limit=5.0):
    """Replicate independent synthetic cohorts; preserve shared lexicographic tiers.

    The large hall is available for zero/one/two lecture periods according to
    layout. All individual requirements remain, with optional combination only.
    Group sizes are supplied example values, not university partition rules.
    """
    if type(copies) is not int or not 1 <= copies <= 3:
        raise ValueError("Use one to three bounded synthetic copies.")
    if layout not in {"combined", "mixed", "separate"} or administrative_mode not in {"fixed", "flexible"}:
        raise ValueError("Unknown synthetic fixture layout or administrative mode.")
    started = perf_counter()
    people, offerings, teams, groups, requirements, evidence, profiles = [], [], [], [], [], [], []
    specializations, rooms, venue_calendars, venue_rules, availability, cohorts = {}, [], [], [], [], []
    periods = dict(week_reference=WEEK, week_start=FIRST, week_end=LAST,
        administrative_periods=[], flexible_policies=[], flexible_requests=[], research_policies=[], research_reservations=[])
    policy = confirmed_campus_policy()
    for index in range(1, copies+1):
        prefix = f"SYN-{index}"
        professor, doctor, lecturer, instructor = [f"{prefix}-{suffix}" for suffix in ("P", "D", "L", "I")]
        for identity, band, kind in ((professor, "PROFESSOR", "ACADEMIC"), (doctor, "DR", "ACADEMIC"),
                (lecturer, "LECTURER", "ACADEMIC"), (instructor, "INSTRUCTOR", "INSTRUCTOR")):
            people.append(dict(staff_id=identity, staff_name=f"Synthetic {identity}", position=band, staff_type=kind))
            profiles.append(dict(staff_id=identity, workload_band=band, evidence_reference="synthetic:classification"))
            availability.append(dict(staff_id=identity, allowed_teaching_days=set(DATES),
                available_windows={"MONDAY": [window(540, 900)], "TUESDAY": [window(540, 780)], "SATURDAY": [window(540, 780)]},
                blocked_periods=[dict(day="MONDAY", **window(870, 900))], effective_from=FIRST, effective_until=LAST,
                daily_teaching_max_minutes=180, weekly_teaching_max_minutes=300))
        cohorts.extend([dict(group_id=f"{prefix}-ACADEMIC-COHORT", staff_ids=[professor, doctor, lecturer],
            reason="Explicit synthetic cohort with different teaching targets"),
            dict(group_id=f"{prefix}-INSTRUCTOR-COHORT", staff_ids=[instructor], reason="Separate synthetic instructor cohort")])
        shared, lab, security = [f"{prefix}-{suffix}" for suffix in ("PROGRAMMING", "PROGRAMMING-LAB", "SECURITY")]
        for identity, stream, specialization, pool in (
                (shared, "WEEKDAY", "Computing", [professor, doctor]),
                (lab, "WEEKDAY", "Computing", [lecturer, instructor]),
                (security, "WEEKEND", "Security", [doctor, lecturer])):
            offerings.append(dict(offering_id=identity, module_code=identity, module_name=f"Synthetic {identity}",
                academic_year=2, semester=1, specialization=specialization, degree_type="Synthetic degree", stream=stream))
            teams.extend(dict(offering_id=identity, staff_id=staff_id, role="TEAM_MEMBER") for staff_id in pool)
        first, second, child1, child2, weekend = [f"{prefix}-{suffix}" for suffix in ("G1", "G2", "G1-LAB", "G2-LAB", "SEC")]
        for identity, parent, size, stream, specialization in (
                (first, None, 20, "WEEKDAY", "Computing"), (second, None, 20, "WEEKDAY", "Computing"),
                (child1, first, 10, "WEEKDAY", "Computing"), (child2, second, 10, "WEEKDAY", "Computing"),
                (weekend, None, 15, "WEEKEND", "Security")):
            groups.append(dict(group_id=identity, parent_group_id=parent, student_count=size, academic_year=2, semester=1, stream=stream))
            specializations[identity] = specialization
        evidence.append(dict(group_ids=[first, second], relationship="DISJOINT", evidence_reference="synthetic:confirmed-cohort-partition"))
        for identity, offering, group, kind, duration, frequency in (
                (f"{prefix}-LEC1", shared, first, "LECTURE", 45, 2),
                (f"{prefix}-LEC2", shared, second, "LECTURE", 45, 2),
                (f"{prefix}-LAB1", lab, child1, "LAB", 60, 1),
                (f"{prefix}-LAB2", lab, child2, "LAB", 60, 1),
                (f"{prefix}-SEC", security, weekend, "LECTURE", 60, 1)):
            requirements.append(dict(requirement_id=identity, offering_id=offering, group_id=group,
                session_type=kind, duration_minutes=duration, sessions_per_week=frequency))
            venue_rules.append(dict(requirement_id=identity, eligible_venue_ids={f"{prefix}-LABROOM"} if kind == "LAB"
                else {f"{prefix}-HALL", f"{prefix}-CLASSROOM"}, allowed_venue_types={"Computer Lab"} if kind == "LAB" else {"Lecture Hall", "Smart Classroom"}))
        for suffix, capacity, kind in (("HALL", 50, "Lecture Hall"), ("CLASSROOM", 25, "Smart Classroom"), ("LABROOM", 15, "Computer Lab")):
            identity = f"{prefix}-{suffix}"
            rooms.append(dict(venue_id=identity, venue_name=f"Synthetic {identity}", venue_type=kind,
                capacity=capacity, building="Synthetic inventory only", floor=1, lab_specialization="Computing" if suffix == "LABROOM" else None))
            hall_end = {"combined": 630, "mixed": 585, "separate": 540}[layout]
            venue_calendars.append(dict(venue_id=identity,
                available_windows={"MONDAY": ([window(540, hall_end)] if hall_end > 540 else []) if suffix == "HALL" else [window(540, 900)],
                    "TUESDAY": [] if suffix == "HALL" else [window(540, 780)], "SATURDAY": [] if suffix == "HALL" else [window(540, 780)]},
                blocked_periods=[dict(day="MONDAY", **window(840, 900))] if suffix == "LABROOM" else []))
        admin_auth = authorization(professor, policy.policy_reference)
        if administrative_mode == "fixed":
            periods["administrative_periods"].append(dict(**admin_auth, reservation_id=f"{prefix}-ADMIN", on_date=FIRST,
                window=window(600, 840), planned_duration_minutes=240, protection="HARD"))
        else:
            flexible_auth = authorization(professor, "synthetic:flexible-admin")
            periods["flexible_policies"].append(dict(**flexible_auth, administrative_policy_reference=policy.policy_reference,
                flexible_scheduling_permitted=True, allow_hard_protection=True, reservation_required=True))
            periods["flexible_requests"].append(dict(**flexible_auth, request_id=f"{prefix}-ADMIN", planned_duration_minutes=240,
                protection="HARD", allow_split=False, eligible_windows=[dict(on_date=FIRST, **window(600, 840))]))
        research_auth = authorization(doctor, "synthetic:research-policy")
        periods["research_policies"].append(dict(**research_auth, allow_full_day=True, allow_partial_day=True, allow_hard_protection=True))
        periods["research_reservations"].append(dict(**research_auth, reservation_id=f"{prefix}-RESEARCH", on_date=DATES["TUESDAY"],
            full_day=False, windows=[window(540, 660)], protection="HARD"))
    combinations = prepare_group_combinations(GroupCombinationInput(teaching_input=dict(staff_members=people,
        module_offerings=offerings, teaching_team_members=teams, student_groups=groups, session_requirements=requirements),
        group_specializations=specializations, policy=dict(allow_combination=True, max_groups=2), population_evidence=evidence))
    prepared = combinations.prepared
    workload = WeeklyWorkloadInput(week_reference=WEEK, policy=policy, profiles=profiles)
    staff = StaffSchedulingInput(workload_input=workload, day_dates=DATES, policy_effective_from=FIRST,
        policy_effective_until=LAST, availability=availability)
    periods = WorkPeriodInput.model_validate(periods)
    work_periods = prepare_work_periods(prepared.input_snapshot, workload, periods)
    config = SolverConfig(days=[dict(day=day, windows={"WEEKEND" if day == "SATURDAY" else "WEEKDAY":
        [window(540, 900 if day == "MONDAY" else 780)]}) for day in DATES],
        granularity_minutes=15, max_time_seconds=time_limit, random_seed=7)
    calendar = PreferenceCalendar(week_reference=WEEK, week_start=FIRST, week_end=LAST, day_dates=DATES, synthetic_data=True)
    preferences = prepare_lecturer_preferences(prepared.input_snapshot, LecturerPreferenceInput(preferences=[
        dict(preference_id=f"PREF-{row['staff_id']}", staff_id=row["staff_id"], category="TEACHING_WINDOW", direction="AVOID",
            days={"MONDAY"}, scope=dict(week_reference=WEEK), time_window=window(780, 900),
            confirmation_status="CONFIRMED", penalty_weight=3, weight_reference="synthetic:example-weights", provenance=PROVENANCE)
        for row in people if row["staff_type"] == "ACADEMIC"]))
    inputs = dict(combination_input=combinations, venue_input=VenueSchedulingInput(venues=rooms,
        unresolved_venue_ids=set(), calendars=venue_calendars, requirements=venue_rules), staff_input=staff,
        work_periods=work_periods, work_period_config=WorkPeriodSolverConfig(day_weekdays={day: Weekday(day) for day in DATES},
            day_streams={day.day: set(day.windows) for day in config.days}),
        preference_input=preferences, preference_calendar=calendar,
        preference_policy=PreferenceScoringPolicy(policy_reference="synthetic:preference-policy", weight_mode="CONFIRMED_WEIGHTS"),
        preference_config=PreferenceOptimizationConfig(priority_policy_reference="synthetic:existing-priority",
            calendar_semantics_reference="synthetic:calendar", day_streams={day.day: set(day.windows) for day in config.days},
            unscoped_preferences_apply_to_all_streams=True),
        fairness_policy=FairnessPolicy(policy_reference="synthetic:fairness", week_reference=WEEK,
            provenance=PROVENANCE, comparison_scope="EXPLICIT_GROUPS", comparison_groups=cohorts),
        fairness_calendar=calendar, fairness_config=FairnessOptimizationConfig(priority="EXISTING_OBJECTIVES_THEN_FAIRNESS",
            priority_policy_reference="synthetic:fairness-priority", cohort_rule="INPUT_ELIGIBILITY_AND_FIXED_CALENDAR",
            deviation_reference="TEACHING_TARGET"), consecutive_calendar=calendar,
        consecutive_policy=ConsecutivePolicy(policy_reference="synthetic:consecutive", week_reference=WEEK, provenance=PROVENANCE,
            effective_from=FIRST, effective_until=LAST, score_lecturers=True, score_student_groups=True,
            soft_criteria={"BACK_TO_BACK", "SHORT_BREAK", "EXCESS_CONTINUOUS_MINUTES"}, preferred_minimum_break_minutes=15,
            maximum_preferred_continuous_minutes=90, back_to_back_penalty=5, short_break_minute_penalty=2,
            excess_continuous_minute_penalty=3, idle_gap_minute_penalty=1, idle_gap_policy_reference="synthetic:compactness"),
        consecutive_config=ConsecutiveOptimizationConfig(priority="EXISTING_OBJECTIVES_THEN_CONSECUTIVE",
            priority_policy_reference="synthetic:consecutive-last"))
    return dict(prepared=prepared, config=config, inputs=inputs, preparation_seconds=perf_counter()-started,
        copies=copies, layout=layout, administrative_mode=administrative_mode)


def run_case(case):
    """Measure actual native calls; do not infer completion from status alone.

    Native instrumentation is scoped with patch and restored even on failure.
    Python RSS/native memory cannot be isolated reliably here, so no memory
    estimate is fabricated. Preparation/build/validation timings are distinct.
    """
    native = cp_model.CpSolver.solve
    stages = []
    started = perf_counter()
    first_native_start = None

    def measured(solver, model, *args, **kwargs):
        nonlocal first_native_start
        entry = perf_counter()
        if first_native_start is None:
            first_native_start = entry
        variables, constraints = len(model.proto.variables), len(model.proto.constraints)
        status = native(solver, model, *args, **kwargs)
        stages.append(dict(variables=variables, constraints=constraints, native_seconds=perf_counter()-entry,
            status=solver.status_name(status)))
        return status

    with patch.object(cp_model.CpSolver, "solve", measured):
        result = solve_timetable(case["prepared"], case["config"], **case["inputs"])
    total_seconds = perf_counter()-started
    validation_start = perf_counter()
    summary = validate_consecutive_result(case["prepared"], case["config"], result, **case["inputs"])
    validation_seconds = perf_counter()-validation_start
    complete = result.status in {SolverStatus.OPTIMAL, SolverStatus.FEASIBLE}
    if complete and (summary is None or summary != result.validation):
        raise AssertionError("Successful timetable did not independently validate.")
    if not complete and (summary is not None or result.schedule or result.administrative_schedule):
        raise AssertionError("Unsuccessful solve exposed a partial timetable.")
    stats = dict(synthetic=True, university_ready=False, layout=case["layout"],
        staff_count=len(case["prepared"].input_snapshot.staff_members),
        academic_lecturer_count=sum(row.staff_type.value == "ACADEMIC" for row in case["prepared"].input_snapshot.staff_members),
        instructor_count=sum(row.staff_type.value == "INSTRUCTOR" for row in case["prepared"].input_snapshot.staff_members),
        student_group_count=len(case["prepared"].input_snapshot.student_groups),
        original_occurrences=len(case["prepared"].sessions),
        combined_occurrence_alternatives=sum(len(row.occurrences) for row in case["inputs"]["combination_input"].candidates),
        combination_candidate_count=len(case["inputs"]["combination_input"].candidates),
        total_delivery_alternatives=len(case["prepared"].sessions)+sum(len(row.occurrences) for row in case["inputs"]["combination_input"].candidates),
        venue_count=len(case["inputs"]["venue_input"].venues),
        maximum_model_variables=max((row["variables"] for row in stages), default=None),
        maximum_model_constraints=max((row["constraints"] for row in stages), default=None),
        fixture_preparation_seconds=round(case["preparation_seconds"], 6),
        model_preparation_seconds=round(first_native_start-started, 6) if first_native_start is not None else None,
        native_solve_seconds=round(sum(row["native_seconds"] for row in stages), 6),
        solve_and_internal_reporting_seconds=round(total_seconds, 6),
        independent_validation_seconds=round(validation_seconds, 6),
        memory_bytes=None, memory_measurement="Not measured: process RSS includes shared Python/native allocations; no isolated peak established.",
        status=result.status.value, complete_timetable=complete, independently_validated=complete,
        failure_result_validated=not complete, native_stages=stages,
        selected_deliveries=len(result.schedule), status_is_not_a_policy_fulfillment_claim=True)
    return result, stats


def summarize(result, case):
    """Only synthetic identifiers are emitted; never print real dataset records."""
    complete = result.status in {SolverStatus.OPTIMAL, SolverStatus.FEASIBLE}
    if not complete:
        return dict(status=result.status.value, complete_timetable=False, failure_reason=result.failure_reason,
            schedule=[], university_ready=False)
    report = result.consecutive_report
    metrics = report.assessment.to_dict()
    return dict(status=result.status.value, complete_timetable=True, synthetic=True, university_ready=False,
        occurrence_coverage=dict(Counter(key for row in result.schedule for key in (item.occurrence_id for item in row.covered_sessions))),
        schedule=[dict(day=row.day, on_date=DATES[row.day].isoformat(), start=row.to_dict()["start_time"],
            end=row.to_dict()["end_time"], staff=row.assigned_staff_id, venue=row.assigned_venue_id,
            groups=list(row.group_ids), combined=row.is_combined, students=row.combined_student_count,
            occurrence_ids=[item.occurrence_id for item in row.covered_sessions])
            for row in sorted(result.schedule, key=lambda row: (DATES[row.day], row.start_minute, row.delivery_id))],
        delivery_count=len(result.schedule), combined_deliveries=result.validation.combined_deliveries,
        separate_deliveries=result.validation.separate_deliveries, contact_minutes=result.validation.lecturer_contact_minutes,
        original_group_contact_minutes=sum(row.duration_minutes for row in case["prepared"].sessions),
        preference_penalty=result.preference_report.score.total_weighted_penalty,
        maximum_normalized_target_deviation=str(result.fairness_report.maximum_normalized_target_deviation),
        aggregate_normalized_target_deviation=str(result.fairness_report.aggregate_normalized_target_deviation),
        consecutive_penalty=report.total_weighted_penalty, objective_order=list(report.priority_order),
        consecutive_metrics={key: metrics[key] for key in ("lecturer_summary", "student_group_summary")},
        teaching_distribution=[dict(kind=row.kind, entity_id=row.entity_id, teaching_days=row.teaching_day_count,
            daily_span_minutes=row.total_daily_span_minutes, idle_minutes=row.total_daily_idle_minutes)
            for row in report.teaching_distributions],
        stages=[vars(row) for row in report.stages], independent_checks=list(result.validation.checks),
        protected_intervals=[row.to_dict() for row in case["inputs"]["work_periods"].protected_intervals],
        flexible_administrative_bookings=[row.to_dict() for row in result.administrative_schedule],
        workload=[row.to_dict() for row in result.work_period_report.staff],
        administrative_completion_inferred=False, attendance_inferred=False)
