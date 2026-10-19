"""Explicit 18:30 online-only experiment; reviewed baseline remains immutable."""

from collections import Counter
from dataclasses import replace
from html import escape
import json
from types import SimpleNamespace
from typing import Annotated, Literal

from pydantic import Field

from schedence_teaching.schemas import Stream
from schedence_teaching.schemas.models import InputContract, RequiredText
from schedence_teaching.solver import SolverConfig, SolverStatus, solve_timetable
from schedence_teaching.solver.attendance_inputs import permitted_day, mode_on_day

from .coordinated_streams import solve_coordinated_cohorts
from .demand_audit import _csv
from .electives import select_enrolled_scope
from .lab_diagnostics import export_lab_diagnostics
from .pp1_run import _page, _records
from .semester_scenarios import SemesterScenario, SemesterScenarioSettings, select_scenario, export_scenario
from .student_packing import student_packing_audit

EXPERIMENTAL = "PP1_EXPERIMENTAL_CALENDAR"
COHORTS = ("PP1:Y3S1-WE:IT", "PP1:Y3S2-WE:IM")


class CalendarExperiment(InputContract):
    reference: RequiredText
    output_label: Literal["PP1_EXPERIMENTAL_CALENDAR"]
    online_day: RequiredText
    baseline_end_minute: Literal[1080]
    experimental_end_minute: Literal[1110]
    seconds_per_cohort: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    num_search_workers: Annotated[int, Field(strict=True, ge=1, le=32)] = 1
    lab_variables_first: Annotated[bool, Field(strict=True)] = True


def extend_weekend_online_calendar(snapshot, settings):
    """Only the Weekend Monday window is extended; source demand is reused.

    Generated PP1 staff windows follow the test calendar. Explicitly supplied
    staff availability, caps and blocks are preserved, never extended by fiat.
    Physical venue calendars are untouched: online delivery uses no venue.
    """
    enrolled, batch = snapshot.enrolled, snapshot.enrolled.batch
    configuration = batch.configuration.model_copy(deep=True)
    if configuration.output_label != "PP1_TEST_CALENDAR":
        raise ValueError("Apply the experiment to the original PP1 test baseline only.")
    if configuration.calendar.day_dates[settings.online_day].weekday()!=0:
        raise ValueError("The experimental online day must be Monday in the supplied calendar.")
    rule = next((row for row in batch.config.delivery_calendar.days
                 if row.day==settings.online_day and row.stream==Stream.WEEKEND), None) if batch.config.delivery_calendar else None
    if rule is None or rule.mode.value!="ONLINE" or {row.value for row in rule.allowed_session_types}!={"LECTURE"}:
        raise ValueError("The existing Weekend Monday must already be lecture-only online delivery.")
    old_windows = {day.day:[window for spans in day.windows.values() for window in spans] for day in batch.config.days}
    raw = batch.config.model_dump()
    day = next(row for row in raw["days"] if row["day"]==settings.online_day)
    spans = day["windows"][Stream.WEEKEND]
    if len(spans)!=1 or spans[0]!={"start_minute":510,"end_minute":settings.baseline_end_minute}:
        raise ValueError("Expected the reviewed 08:30–18:00 Weekend online baseline, not an existing override.")
    spans[0]["end_minute"] = settings.experimental_end_minute
    raw.update(num_search_workers=settings.num_search_workers, lab_variables_first=settings.lab_variables_first)
    config = SolverConfig.model_validate(raw)
    contexts = dict(batch.solver_inputs)
    staff = contexts["staff_input"].model_copy(deep=True)
    derived = configuration.staff_input is None
    if derived:
        new_windows = {day.day:[window for spans in day.windows.values() for window in spans] for day in config.days}
        for person in staff.availability:
            if person.available_windows!=old_windows or person.allowed_teaching_days!=set(old_windows):
                raise ValueError("Staff availability differs from the generated PP1 template; supply it explicitly rather than extending it.")
            person.available_windows = {day:list(spans) for day, spans in new_windows.items()}
        contexts["staff_input"] = type(staff).model_validate(staff.model_dump())
    configuration.output_label = EXPERIMENTAL
    configuration.configuration_reference = settings.reference
    configuration.solver_config = config
    configuration = type(configuration).model_validate(configuration.model_dump())
    metadata = dict(batch.metadata, output_label=EXPERIMENTAL, calendar_result_status="EXPERIMENTAL_CALENDAR_RESULT",
        experimental_calendar_reference=settings.reference, baseline_calendar_unchanged=True,
        experimental_change=dict(stream="WEEKEND",day=settings.online_day,start_minute=510,
            old_end_minute=settings.baseline_end_minute,new_end_minute=settings.experimental_end_minute),
        staff_availability_scope="PP1_TEST_WINDOWS_DERIVED_FROM_EXPERIMENTAL_CALENDAR" if derived else "SUPPLIED_OVERRIDE_UNCHANGED",
        no_actual_campus_staff_availability_inferred=True, solver_config=config.model_dump(mode="json"))
    return replace(snapshot, enrolled=replace(enrolled,batch=replace(batch,config=config,configuration=configuration,
        solver_inputs=contexts,metadata=metadata)))


def _settings(settings):
    return SemesterScenarioSettings(reference=settings.reference,num_search_workers=settings.num_search_workers,
        lab_variables_first=settings.lab_variables_first,strategy="COORDINATED_COHORTS",
        max_time_seconds=settings.seconds_per_cohort,stress_test_seconds=settings.seconds_per_cohort,
        scenarios=[dict(name="semester-1",semesters=[1]),dict(name="semester-2",semesters=[2])])


def _select(snapshot, settings, semester, *, cohort_id=None):
    enrolled = select_scenario(snapshot,SemesterScenario(name=f"semester-{semester}",semesters=[semester]),_settings(settings))
    if cohort_id is not None:
        cohort = next((row for row in enrolled.batch.plan.cohorts if row.cohort_id==cohort_id),None)
        if cohort is None:raise ValueError("The requested diagnostic cohort is missing; no substitute was selected.")
        enrolled = select_enrolled_scope(enrolled,{(cohort.academic_year,cohort.semester,cohort.stream.value,cohort.specialization)},max_time_seconds=settings.seconds_per_cohort)
    return replace(enrolled,batch=replace(enrolled.batch,metadata=dict(enrolled.batch.metadata,
        scenario_name=f"cohort-year3-semester-{semester}" if cohort_id else f"semester-{semester}",
        scenario_label="EXPERIMENTAL_CALENDAR_RESULT",supported_semester_scope=cohort_id is None,
        separate_semester_alternatives_not_jointly_conflict_free=True)))


def placement_diagnostics(enrolled):
    """Individual possible starts, before simultaneous resource/attendance checks."""
    batch = enrolled.batch
    if batch.config.batch_online_days is not None:
        raise ValueError('Calendar placement diagnostics do not yet support batch-specific online days.')
    groups = {row.group_id:row for row in batch.plan.groups}
    policy = batch.solver_inputs["student_lunch_policy"]
    if policy.applicable_dates is None or policy.applicable_streams is None:
        raise ValueError("Placement diagnostics require explicit lunch applicability.")
    lunches = {row.academic_year:row.window for row in policy.year_periods}
    placements, demand = [], []
    original = {row["occurrence_id"]:row for row in _records(batch,SimpleNamespace(schedule=()))}
    for session in batch.prepared.sessions:
        group = groups[session.group_id]
        eligible_days, modes = set(), set()
        for day in batch.config.days:
            if session.stream not in day.windows or not permitted_day(batch.config,session,day.day):continue
            mode = mode_on_day(batch.config,day.day,session.stream).value
            lunch = lunches[group.academic_year]
            applicable = batch.configuration.calendar.day_dates[day.day] in policy.applicable_dates and group.stream in policy.applicable_streams
            for span in day.windows[session.stream]:
                for start in range(span.start_minute,span.end_minute-session.duration_minutes+1,batch.config.granularity_minutes):
                    end = start+session.duration_minutes
                    if applicable and start<lunch.end_minute and lunch.start_minute<end:continue
                    eligible_days.add(day.day)
                    modes.add(mode)
                    placements.append(dict(output_label=batch.configuration.output_label,occurrence_id=session.occurrence_id,
                        group_id=group.group_id,session_type=session.session_type.value,day=day.day,delivery_mode=mode,
                        start_minute=start,end_minute=end,start_time=f"{start//60:02}:{start%60:02}",end_time=f"{end//60:02}:{end%60:02}",
                        semantics="INDIVIDUAL_CALENDAR_PLACEMENT_NOT_A_SIMULTANEOUS_BOOKING"))
        demand.append(dict(original[session.occurrence_id],eligible_days=";".join(sorted(eligible_days)),eligible_modes=";".join(sorted(modes)),
            fixed_lunch=f"{lunches[group.academic_year].start_minute//60:02}:{lunches[group.academic_year].start_minute%60:02}–{lunches[group.academic_year].end_minute//60:02}:{lunches[group.academic_year].end_minute%60:02}"))
    return dict(demand=demand,placements=placements)


def coverage_report(enrolled, result):
    expected = {row.occurrence_id:row for row in enrolled.batch.prepared.sessions}
    actual = Counter(row.session.occurrence_id for row in result.schedule)
    if actual.keys()-expected.keys() or any(count!=1 for count in actual.values()):
        raise ValueError("Unexpected or duplicated scheduled occurrence; completeness cannot be claimed.")
    for row in result.schedule:
        if row.session!=expected[row.session.occurrence_id]:raise ValueError("Scheduled occurrence metadata changed.")
    return [dict(occurrence_id=identity,requirement_id=row.requirement_id,group_id=row.group_id,
        duration_minutes=row.duration_minutes,expected_count=1,scheduled_count=actual[identity],
        coverage="EXACTLY_ONCE" if actual[identity]==1 else "UNSCHEDULED") for identity,row in expected.items()]


def run_experiment(baseline, settings, directory):
    """New output root only; both original and experimental calendars are saved."""
    experiment = extend_weekend_online_calendar(baseline,settings)
    directory.mkdir(parents=True,exist_ok=False)
    (directory/"experiment-settings.json").write_text(settings.model_dump_json(indent=2),encoding="utf-8")
    (directory/"baseline-calendar.json").write_text(baseline.enrolled.batch.configuration.model_dump_json(indent=2),encoding="utf-8")
    (directory/"experimental-calendar.json").write_text(experiment.enrolled.batch.configuration.model_dump_json(indent=2),encoding="utf-8")
    comparison = []
    for semester,identity in enumerate(COHORTS,1):
        for label,snapshot in (("BASELINE",baseline),(EXPERIMENTAL,experiment)):
            enrolled = _select(snapshot,settings,semester,cohort_id=identity)
            diagnostic = placement_diagnostics(enrolled)
            _csv(directory/f"{label.lower()}-cohort-{semester}-demand.csv",diagnostic["demand"])
            _csv(directory/f"{label.lower()}-cohort-{semester}-placements.csv",diagnostic["placements"])
            packing = student_packing_audit(enrolled.batch)
            comparison.extend(dict(calendar=label,**row) for row in packing["rows"])
    _csv(directory/"calendar-feasibility-comparison.csv",comparison)
    (directory/"calendar-feasibility-comparison.json").write_text(json.dumps(comparison,indent=2),encoding="utf-8")
    summaries = []
    stages = [(1,COHORTS[0]),(2,COHORTS[1]),(1,None),(2,None)]
    for semester,identity in stages:
        enrolled = _select(experiment,settings,semester,cohort_id=identity)
        batch = enrolled.batch
        name = batch.metadata["scenario_name"]
        diagnostic_dir = directory/(name+"-diagnostics")
        diagnostic_dir.mkdir()
        capacity = export_lab_diagnostics(batch,diagnostic_dir)
        print(json.dumps(dict(stage=name,required=len(batch.prepared.sessions),output_label=EXPERIMENTAL)),flush=True)
        if identity:
            result = solve_timetable(batch.prepared,batch.config,**batch.solver_inputs)
            origin, subsolves = "MONOLITHIC_NATIVE_CP_SAT", None
        else:
            result, subsolves = solve_coordinated_cohorts(enrolled,seconds_per_part=settings.seconds_per_cohort)
            origin = "COORDINATED_DECOMPOSITION"
        enrolled = replace(enrolled,batch=replace(batch,metadata=dict(batch.metadata,solver_result_origin=origin)))
        summary = export_scenario(enrolled,result,directory/name,capacity)
        coverage = coverage_report(enrolled,result)
        _csv(directory/name/"session-coverage.csv",coverage)
        passed = summary["independently_validated"] and all(row["scheduled_count"]==1 for row in coverage)
        summary.update(calendar_result_status="EXPERIMENTAL_CALENDAR_RESULT",result_origin=origin,decomposition_stages=subsolves,
            supported_scope_status="COMPLETE_SUPPORTED_SCOPE" if passed and identity is None else "COMPLETE_SELECTED_COHORT" if passed else "NOT_COMPLETE",
            research_status="RESEARCH_PENDING_SESSION_MAPPING" if enrolled.research_pending_cohort_ids else "NOT_APPLICABLE_TO_SELECTED_COHORT",
            full_year4_academic_timetable_complete=False,
            academic_mapping_blocks=[dict(row,status="BLOCKED_MISSING_ACADEMIC_MAPPING") for row in baseline.blocked_scopes if row["semester"]==semester],
            separate_semester_results_are_not_jointly_conflict_free=True)
        (directory/name/"scenario-summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
        summaries.append(summary)
        (directory/"run-summary.json").write_text(json.dumps(summaries,indent=2),encoding="utf-8")
        experiment_index(directory,summaries)
        print(json.dumps({key:summary[key] for key in ("scenario","solver_status","required_occurrences","scheduled_occurrences","supported_scope_status")}),flush=True)
    blockers = dict(academic_mapping=[dict(row,status="BLOCKED_MISSING_ACADEMIC_MAPPING") for row in baseline.blocked_scopes],
        research_status="RESEARCH_PENDING_SESSION_MAPPING",research_session_mapping_is_not_zero_academic_work=True,
        baseline_unchanged=True,experimental_calendar_is_not_approved_university_timetable=True,
        semesters_must_not_be_concatenated_on_overlapping_dates=True)
    (directory/"remaining-blockers.json").write_text(json.dumps(blockers,indent=2),encoding="utf-8")
    return summaries


def experiment_index(directory,summaries):
    body = '<p class="notice"><strong>EXPERIMENTAL_CALENDAR_RESULT.</strong> Weekend online Monday ends at 18:30 in this experiment only. The original 18:00 baseline and prior timetables are unchanged. This is not an approved university timetable. Separate semester bookings must not be combined on overlapping dates.</p>'
    body += '<table><tr><th>Scope</th><th>Result</th><th>Scheduled / required</th><th>Coverage status</th><th>Views</th></tr>'
    for row in summaries:
        name = escape(row["scenario"],quote=True)
        body += f'<tr><td>{name}</td><td>{escape(row["solver_status"])}</td><td>{row["scheduled_occurrences"]} / {row["required_occurrences"]}</td><td>{escape(row["supported_scope_status"])}</td><td><a href="{name}/timetable.html">Report / specialization views</a> / <a href="{name}/timetable.csv">CSV</a> / <a href="{name}/timetable.json">JSON</a>'
        if row["scheduled_occurrences"]:body+=f' / <a href="{name}/weekly.html">Weekly group / lecturer / venue views</a>'
        body += '</td></tr>'
    body += '</table><p><a href="calendar-feasibility-comparison.csv">Calendar feasibility comparison</a> / <a href="run-summary.json">Detailed results and validation</a></p>'
    body += '<p>COMPLETE_SUPPORTED_SCOPE refers only to the supplied known sessions. Year 4 Semester 1 DS remains BLOCKED_MISSING_ACADEMIC_MAPPING. Compulsory Research remains RESEARCH_PENDING_SESSION_MAPPING; this is not a complete Year 4 academic timetable. Coordinated FEASIBLE results do not claim global objective optimality.</p>'
    (directory/"index.html").write_text(_page("Step 12A.3.5: experimental supported semester timetables",body,output_label=EXPERIMENTAL),encoding="utf-8")
