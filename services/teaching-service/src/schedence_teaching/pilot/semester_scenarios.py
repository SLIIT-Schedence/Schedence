"""Complete semester alternatives, each one globally coordinated CP-SAT model."""

from dataclasses import replace
import csv
from html import escape
import json
from typing import Annotated, Literal

from pydantic import Field, model_validator

from schedence_teaching.schemas.models import InputContract, RequiredText, Semester
from schedence_teaching.solver import SolverConfig, SolverStatus, solve_timetable

from .demand_audit import _csv
from .elective_io import enrolled_demand_report
from .electives import select_enrolled_scope
from .elective_validation import validate_elective_result
from .lab_diagnostics import export_lab_diagnostics, resource_utilization
from .pp1_run import _page, export_pp1
from .student_packing import student_packing_audit


class SemesterScenario(InputContract):
    name: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")]
    semesters: frozenset[Semester] = Field(min_length=1)
    exclude_proven_calendar_infeasible: bool = Field(default=False, strict=True)


class SemesterScenarioSettings(InputContract):
    reference: RequiredText
    num_search_workers: Annotated[int, Field(strict=True, ge=1, le=32)] = 1
    lab_variables_first: Annotated[bool, Field(strict=True)] = False
    strategy: Literal["MONOLITHIC", "COORDINATED_STREAMS", "COORDINATED_COHORTS"] = "MONOLITHIC"
    max_time_seconds: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    stress_test_seconds: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    scenarios: tuple[SemesterScenario, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_names(self):
        if len({row.name for row in self.scenarios}) != len(self.scenarios):
            raise ValueError("Each scenario output directory must be unique.")
        return self


def select_scenario(snapshot, scenario, settings):
    excluded = (set(student_packing_audit(snapshot.enrolled.batch)["blocked_cohort_ids"])
                if scenario.exclude_proven_calendar_infeasible else set())
    keys = {(row.academic_year, row.semester, row.stream.value, row.specialization)
            for row in snapshot.enrolled.batch.plan.cohorts if row.semester in scenario.semesters and row.cohort_id not in excluded}
    available = {row.semester for row in snapshot.enrolled.batch.plan.cohorts}
    if scenario.semesters - available:
        raise ValueError("A selected semester has no supported original cohort.")
    budget = settings.stress_test_seconds if len(scenario.semesters) > 1 else settings.max_time_seconds
    enrolled = select_enrolled_scope(snapshot.enrolled, keys, max_time_seconds=budget)
    batch = enrolled.batch
    config = SolverConfig.model_validate(dict(batch.config.model_dump(),
        num_search_workers=settings.num_search_workers, lab_variables_first=settings.lab_variables_first))
    configuration = batch.configuration.model_copy(update=dict(solver_config=config))
    blocked = [row for row in snapshot.blocked_scopes if row["semester"] in scenario.semesters]
    metadata = dict(batch.metadata, scenario_label="TEST_SCENARIO", scenario_name=scenario.name,
        scenario_reference=settings.reference, semesters=sorted(scenario.semesters),
        semester_overlap_assumption="ORIGINAL_SIMULTANEOUS_PP1_STRESS_TEST" if len(scenario.semesters)>1 else "SELECTED_SEMESTER_ONLY_NOT_OFFICIAL_TERM_DATES",
        source_registration_snapshot=snapshot.reference, original_baseline_unchanged=True,
        output_component="ALL_SUPPORTED_KNOWN_TEACHING_COMPONENTS", blocked_scopes=blocked,
        blocked_student_count=sum(row["student_count"] for row in blocked),
        excluded_proven_calendar_infeasible_cohorts=sorted(row.cohort_id for row in snapshot.enrolled.batch.plan.cohorts
            if row.semester in scenario.semesters and row.cohort_id in excluded),
        full_source_scope_included=not blocked and not any(row.cohort_id in excluded
            for row in snapshot.enrolled.batch.plan.cohorts if row.semester in scenario.semesters),
        solver_config=config.model_dump(mode="json"))
    return replace(enrolled, batch=replace(batch, config=config, configuration=configuration, metadata=metadata))


def export_scenario(enrolled, result, directory, capacity):
    """Only complete independently validated schedules receive COMPLETE labels."""
    success = result.status in {SolverStatus.OPTIMAL, SolverStatus.FEASIBLE}
    validation = validate_elective_result(enrolled, result) if success else None
    report = export_pp1(enrolled.batch, result, directory)
    utilization = resource_utilization(enrolled.batch, result)
    _csv(directory / "venue-utilization.csv", utilization["venues"])
    _csv(directory / "lecturer-utilization.csv", utilization["lecturers"])
    attendance = enrolled_demand_report(enrolled)
    if attendance:
        _csv(directory / "student-attendance-demand.csv", attendance)
    else:
        # A Year 1–3-only diagnostic scope has no elective registrations.
        # Preserve an explicit empty report; do not invent student selections.
        with (directory / "student-attendance-demand.csv").open("x",encoding="utf-8",newline="") as handle:
            csv.writer(handle).writerow(("data_status","cohort_id","student_id","selected_elective_count","corrected_attendance_hours"))
    (directory / "resource-utilization.json").write_text(json.dumps(utilization, indent=2), encoding="utf-8")
    if validation:
        (directory / "elective-independent-validation.json").write_text(json.dumps(validation, indent=2), encoding="utf-8")
    summary = dict(scenario=enrolled.batch.metadata["scenario_name"], scenario_label=enrolled.batch.metadata.get("scenario_label","TEST_SCENARIO"),
        output_label=enrolled.batch.configuration.output_label,
        semesters=enrolled.batch.metadata["semesters"], solver_status=result.status.value,
        runtime_seconds=result.runtime_seconds, required_occurrences=len(enrolled.batch.prepared.sessions),
        scheduled_occurrences=len(result.schedule), unscheduled_occurrences=report["unscheduled_sessions"],
        known_session_scope_status="COMPLETE" if success else "NOT_COMPLETE",
        completion_status=report["completion_status"], independently_validated=bool(validation),
        hard_constraint_violations=0 if validation else None,
        research_pending_cohorts=len(enrolled.research_pending_cohort_ids),
        year4_academic_timetable_complete=not enrolled.research_pending_cohort_ids and success
            and any(row.academic_year==4 for row in enrolled.batch.plan.cohorts),
        blocked_scopes=enrolled.batch.metadata["blocked_scopes"],
        excluded_proven_calendar_infeasible_cohorts=enrolled.batch.metadata["excluded_proven_calendar_infeasible_cohorts"],
        independent_lab_capacity_status=capacity["status"],
        lab_bookings_required=sum(row.session_type.value=="LAB" for row in enrolled.batch.prepared.sessions),
        lab_bounds=[row for row in capacity["rows"] if row["minimum_students"] in {1,31}],
        all_thresholds_report="../" + enrolled.batch.metadata["scenario_name"] + "-diagnostics/physical-lab-capacity.csv",
        separate_scenarios_must_not_be_concatenated=True)
    (directory / "scenario-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def run_semester_scenarios(snapshot, settings, directory):
    if directory.exists():
        raise FileExistsError("Use a new output directory; earlier work is preserved.")
    directory.mkdir(parents=True)
    (directory / "scenario-settings.json").write_text(settings.model_dump_json(indent=2), encoding="utf-8")
    (directory / "blocked-scopes.json").write_text(json.dumps(snapshot.blocked_scopes, indent=2), encoding="utf-8")
    summaries = []
    for scenario in settings.scenarios:
        enrolled = select_scenario(snapshot, scenario, settings)
        diagnostics = directory / (scenario.name + "-diagnostics")
        diagnostics.mkdir()
        capacity = export_lab_diagnostics(enrolled.batch, diagnostics)
        packing = student_packing_audit(enrolled.batch)
        (diagnostics / "student-packing.json").write_text(json.dumps(packing, indent=2), encoding="utf-8")
        _csv(diagnostics / "student-packing.csv", packing["rows"])
        if scenario.exclude_proven_calendar_infeasible:
            excluded = set(enrolled.batch.metadata["excluded_proven_calendar_infeasible_cohorts"])
            outside = [row.to_dict() for row in snapshot.enrolled.batch.prepared.sessions
                       if snapshot.enrolled.batch.plan.cohort_by_group[row.group_id] in excluded]
            (diagnostics / "excluded-original-demand.json").write_text(json.dumps(dict(
                reason="PROVEN_STUDENT_CONTINUOUS_SESSION_PACKING_SHORTAGE", scope_was_explicitly_narrowed=True,
                excluded_cohort_ids=sorted(excluded), unchanged_original_occurrences=outside), indent=2), encoding="utf-8")
            from types import SimpleNamespace
            from .pp1_run import _records
            outside_rows = [row for row in _records(snapshot.enrolled.batch, SimpleNamespace(schedule=()))
                if snapshot.enrolled.batch.plan.cohort_by_group[row["group_id"]] in excluded]
            if outside_rows:
                _csv(diagnostics / "excluded-original-demand.csv", outside_rows)
        print(json.dumps(dict(stage="SOLVING", scenario=scenario.name,
            required=len(enrolled.batch.prepared.sessions), independent_lab_bound=capacity["status"])), flush=True)
        stages = None
        if settings.strategy=="COORDINATED_COHORTS" and len(scenario.semesters)==1:
            from .coordinated_streams import solve_coordinated_cohorts
            result, stages = solve_coordinated_cohorts(enrolled, seconds_per_part=settings.max_time_seconds)
        elif settings.strategy=="COORDINATED_STREAMS" and len(scenario.semesters)==1:
            from .coordinated_streams import solve_coordinated_streams
            result, stages = solve_coordinated_streams(enrolled, seconds_per_part=settings.max_time_seconds)
        else:
            result = solve_timetable(enrolled.batch.prepared, enrolled.batch.config, **enrolled.batch.solver_inputs)
        origin = "COORDINATED_DECOMPOSITION" if stages is not None else "MONOLITHIC_NATIVE_CP_SAT"
        enrolled = replace(enrolled, batch=replace(enrolled.batch, metadata=dict(enrolled.batch.metadata,
            solver_result_origin=origin, global_objective_optimality_claimed=False)))
        summary = export_scenario(enrolled, result, directory / scenario.name, capacity)
        summary.update(result_origin=origin, decomposition_stages=stages)
        summary["independent_student_packing_status"] = packing["status"]
        (directory / scenario.name / "scenario-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        summaries.append(summary)
        (directory / "scenario-comparison.json").write_text(json.dumps(summaries, indent=2), encoding="utf-8")
        index_html(directory, summaries)
        print(json.dumps({key:summary[key] for key in ("scenario","solver_status","required_occurrences","scheduled_occurrences","unscheduled_occurrences","runtime_seconds")}), flush=True)
    return summaries


def index_html(directory, summaries):
    body = '<p class="notice"><strong>TEST_SCENARIO alternatives, not official semester dates.</strong> Each scenario is globally coordinated. Decomposition carries earlier resource bookings forward and validates the complete union; it never claims global optimality. Do not combine independent scenario bookings on overlapping actual dates. The original combined-semester PP1 baseline is unchanged. Research remains compulsory and pending session mapping.</p>'
    body += '<table><tr><th>Scenario</th><th>Result status</th><th>Scheduled / required</th><th>Known session scope</th><th>Lab bound</th><th>Reports</th></tr>'
    for row in summaries:
        name = escape(row["scenario"], quote=True)
        body += f'<tr><td><a href="{name}/timetable.html">{name}</a></td><td>{escape(row["solver_status"])}</td><td>{row["scheduled_occurrences"]} / {row["required_occurrences"]}</td><td>{escape(row["known_session_scope_status"])}</td><td>{escape(row["independent_lab_capacity_status"])}</td><td><a href="{name}/scenario-summary.json">Summary</a> / <a href="{name}-diagnostics/lab-demand.csv">Lab demand</a> / <a href="{name}-diagnostics/physical-lab-capacity.csv">Capacity bounds</a> / <a href="{name}/resource-utilization.json">Utilization</a></td></tr>'
    body += '</table><p>Room capacity bounds are optimistic necessary conditions, not proof of feasibility when they pass. Shortage bookings are not missing rooms; size-threshold bounds overlap and must not be summed.</p>'
    (directory / "index.html").write_text(_page("Step 12A.3.4: semester scenario comparison", body), encoding="utf-8")
