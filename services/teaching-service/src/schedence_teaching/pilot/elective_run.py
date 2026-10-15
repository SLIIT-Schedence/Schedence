"""One canonical registration snapshot, progressive joint models and honest scope labels."""

from dataclasses import replace
import csv
from html import escape
import json
from pathlib import Path

from schedence_teaching.solver import SolverStatus, solve_timetable

from .demand_audit import _csv
from .elective_io import export_enrollment, enrolled_demand_report, write_classifications
from .electives import assess_elective_catalog, generate_test_electives, prepare_enrolled_pp1, select_enrolled_scope
from .elective_validation import validate_elective_result
from .pp1_run import export_pp1, _page
from .research_requirements import PENDING_RESEARCH, apply_research_requirements
from .resource_capacity import export_resource_capacity
from .scopes import select_complete_scope


def _scope(cohort):
    return cohort.academic_year,cohort.semester,cohort.stream.value,cohort.specialization


def run_elective_previews(base, academic, classifications, policy, settings, directory, *, selections=None,
                          allocation_label="PP1_TEST_ELECTIVE_ALLOCATION"):
    """Blocked cohorts retain counts; missing Research never blocks electives."""
    directory = Path(directory)
    if directory.exists():
        raise FileExistsError("Use a new output directory; all earlier work and reports are retained.")
    assessment = assess_elective_catalog(base,academic,classifications)
    cohorts = {row.cohort_id:row for row in base.plan.cohorts}
    known_keys = {_scope(row) for row in cohorts.values()}
    if {row.key for row in settings.blocked_scopes}-known_keys:
        raise ValueError("Configured blocked scope has no original cohort.")
    if set(policy.allowed_pairs_by_cohort)-assessment["cohorts"].keys():
        raise ValueError("Pair policy references an unknown Year 4 cohort.")
    blocked = []
    for identity,cohort in cohorts.items():
        explicit = [row.reason for row in settings.blocked_scopes if row.key==_scope(cohort)]
        issues = [row["code"] for row in assessment["diagnostics"] if row.get("cohort_id")==identity]
        if explicit or issues:
            blocked.append(dict(cohort_id=identity,academic_year=cohort.academic_year,semester=cohort.semester,
                stream=cohort.stream.value,specialization=cohort.specialization,student_count=cohort.student_count,
                status="BLOCKED_INPUT",reason=";".join(sorted(set(explicit+issues))),student_counts_preserved=True))
    blocked_ids = {row["cohort_id"] for row in blocked}
    supported_ids = cohorts.keys()-blocked_ids
    supported_keys = {_scope(cohorts[identity]) for identity in supported_ids}
    supported_base = select_complete_scope(base,supported_keys,max_time_seconds=settings.global_solve_seconds,retain_applicable_catalog=True)
    active_policy = policy.model_copy(update=dict(allowed_pairs_by_cohort={key:value for key,value in policy.allowed_pairs_by_cohort.items() if key in supported_ids}))
    registrations = (tuple(row for row in selections if row.cohort_id in supported_ids) if selections is not None else
        generate_test_electives(supported_base,academic,classifications,active_policy))
    enrolled = prepare_enrolled_pp1(supported_base,academic,classifications,registrations,policy=active_policy,allocation_label=allocation_label)
    research = settings.research.model_copy(update=dict(complete_cohort_ids=settings.research.complete_cohort_ids & supported_ids,
        session_mappings=tuple(row.model_copy(update=dict(cohort_ids=tuple(identity for identity in row.cohort_ids if identity in supported_ids)))
            for row in settings.research.session_mappings if set(row.cohort_ids)&supported_ids)))
    # Unknown Research references are errors, distinct from intentionally blocked DS cohorts.
    if ({identity for row in settings.research.session_mappings for identity in row.cohort_ids} | settings.research.complete_cohort_ids)-cohorts.keys():
        raise ValueError("Research configuration references an unknown original cohort.")
    enrolled = apply_research_requirements(enrolled,base,research)
    metadata = dict(enrolled.batch.metadata,registered_population_scope="SUPPORTED_COHORTS_ONLY_BLOCKED_COUNTS_RETAINED_SEPARATELY",
        known_supported_global_occurrences=len(enrolled.batch.prepared.sessions),blocked_cohort_count=len(blocked),
        full_source_cohort_scope_included=not blocked,allocation_selection_strategy=policy.selection_strategy,
        research_mapping_gap_does_not_block_elective_teaching=True)
    enrolled = replace(enrolled,batch=replace(enrolled.batch,metadata=metadata))
    directory.mkdir(parents=True)
    write_classifications(directory/"module-classifications.csv",classifications)
    _csv(directory/"module-classification-review.csv",assessment["catalog"])
    with (directory/"blocked-scopes.csv").open("x",encoding="utf-8",newline="") as handle:
        writer=csv.DictWriter(handle,fieldnames=("cohort_id","academic_year","semester","stream","specialization","student_count","status","reason","student_counts_preserved"),lineterminator="\n")
        writer.writeheader()
        writer.writerows(blocked)
    (directory/"configuration.json").write_text(settings.model_dump_json(indent=2),encoding="utf-8")
    (directory/"hybrid-calendar.json").write_text(base.configuration.model_dump_json(indent=2),encoding="utf-8")
    (directory/"original-cohort-counts.json").write_text(json.dumps([row.model_dump(mode="json") for row in base.plan.cohorts],indent=2),encoding="utf-8")
    export_enrollment(enrolled,directory/"enrollment")
    resource_bound=export_resource_capacity(enrolled.batch,directory)
    obligations = list(enrolled.batch.metadata["research_obligations"])
    for row in blocked:
        if row["academic_year"]==4:
            obligations.append(dict(cohort_id=row["cohort_id"],academic_year=4,semester=row["semester"],stream=row["stream"],
                specialization=row["specialization"],student_count=row["student_count"],status=PENDING_RESEARCH,
                compulsory=True,academic_work_hours=None,academic_work_completed=False,weekly_session_minutes_known=None))
    _csv(directory/"research-obligations.csv",obligations)
    summary = dict(output_label="PP1_TEST_CALENDAR",allocation_label=allocation_label,
        allocation_strategy=policy.selection_strategy,allocation_seed=policy.random_seed,
        original_catalog_application_occurrences=len(base.prepared.sessions),
        known_supported_occurrences=len(enrolled.batch.prepared.sessions),
        original_cohort_count=len(cohorts),supported_cohort_count=len(supported_ids),blocked_scopes=blocked,
        original_cohort_student_sum=sum(row.student_count for row in cohorts.values()),
        supported_cohort_student_sum=sum(cohorts[identity].student_count for identity in supported_ids),
        blocked_cohort_student_sum=sum(row["student_count"] for row in blocked),
        year4_registered_students=len(registrations),research_pending_cohort_count=sum(row["status"]==PENDING_RESEARCH for row in obligations),
        independent_physical_lab_capacity_status=resource_bound["status"],
        research_academic_work_is_not_assumed_zero=True,full_global_academic_timetable_generated=False,runs=[])
    stages = [("01-year4-sem1-it-weekday",{(4,1,"WEEKDAY","IT")},settings.scope_solve_seconds),
              ("02-year4-sem1-it-weekend",{(4,1,"WEEKEND","IT")},settings.scope_solve_seconds),
              ("03-all-supported-year4",{key for key in supported_keys if key[0]==4},settings.scope_solve_seconds),
              ("04-all-supported-pp1",supported_keys,settings.global_solve_seconds)]
    for name,keys,budget in stages:
        unavailable = keys-supported_keys
        if unavailable:
            summary["runs"].append(dict(name=name,solver_status=None,completion_status="BLOCKED_INPUT",scheduled=None,unscheduled=None))
            continue
        selected = select_enrolled_scope(enrolled,keys,max_time_seconds=budget)
        only_year4 = all(row.academic_year==4 for row in selected.batch.plan.cohorts)
        selected = replace(selected,batch=replace(selected.batch,metadata=dict(selected.batch.metadata,
            output_component="YEAR4_ELECTIVE_COMPONENT" if only_year4 else "ALL_SUPPORTED_KNOWN_TEACHING_COMPONENTS",
            research_pending_cohort_count=len(selected.research_pending_cohort_ids),
            research_obligations=[row for row in obligations if row["cohort_id"] in {cohort.cohort_id for cohort in selected.batch.plan.cohorts}])))
        print(json.dumps(dict(stage=name,required_occurrences=len(selected.batch.prepared.sessions),selected_cohorts=len(selected.batch.plan.cohorts))),flush=True)
        result = solve_timetable(selected.batch.prepared,selected.batch.config,**selected.batch.solver_inputs)
        validation = validate_elective_result(selected,result) if result.status in {SolverStatus.OPTIMAL,SolverStatus.FEASIBLE} else None
        report = export_pp1(selected.batch,result,directory/name)
        if validation:
            (directory/name/"elective-independent-validation.json").write_text(json.dumps(validation,indent=2),encoding="utf-8")
        attendance = enrolled_demand_report(selected)
        _csv(directory/name/"student-attendance-demand.csv",attendance)
        row = dict(name=name,solver_status=result.status.value,completion_status=report["completion_status"],
            required_occurrences=len(selected.batch.prepared.sessions),scheduled=report["scheduled_sessions"],unscheduled=report["unscheduled_sessions"],
            selected_cohorts=len(selected.batch.plan.cohorts),independently_validated=bool(validation),
            research_pending_cohorts=len(selected.research_pending_cohort_ids),
            elective_hours_min=min(item["elective_contact_hours"] for item in attendance),
            elective_hours_max=max(item["elective_contact_hours"] for item in attendance),
            runtime_seconds=result.runtime_seconds)
        summary["runs"].append(row)
        summary["all_supported_known_sessions_scheduled"] = bool(name=="04-all-supported-pp1" and validation)
        (directory/"run-summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
        print(json.dumps(row),flush=True)
    _index(directory,summary)
    return summary


def _index(directory,summary):
    config=json.loads((directory/"configuration.json").read_text(encoding="utf-8"))
    def window(key):
        row=config[key]
        return f'{row["start_minute"]//60:02}:{row["start_minute"]%60:02}–{row["end_minute"]//60:02}:{row["end_minute"]%60:02}'
    body = '<p class="notice"><strong>Compulsory Research remains pending session mapping.</strong> Missing sessions do not imply zero academic work. No complete Year 4 academic or full university timetable is claimed.</p>'
    if summary.get("independent_physical_lab_capacity_status")=="PROVEN_INFEASIBLE":
        body += '<p class="notice"><strong>Independent physical lab-capacity bound: PROVEN_INFEASIBLE.</strong> This is separate from the native solver status. <a href="physical-lab-capacity.csv">Size/whole-session capacity audit</a> / <a href="test-calendar-capacity-comparison.csv">TEST_SCENARIO calendar bounds (not timetables)</a>.</p>'
    body += f'<p>Registration profile: {escape(summary["allocation_strategy"])}; seed {summary["allocation_seed"]}. PP1 test choices are not actual university registrations.</p>'
    body += f'<p>Weekday online: {escape(config["weekday_online_day"])} {window("weekday_online_window")}; Weekend online: {escape(config["weekend_online_day"])} {window("weekend_online_window")} (editable PP1 settings); physical weekend remains Saturday/Sunday.</p>'
    body += '<table><tr><th>Scope</th><th>Native status</th><th>Completion</th><th>Scheduled</th><th>Unscheduled known sessions</th></tr>'
    for row in summary["runs"]:
        name=escape(row["name"])
        body += f'<tr><td><a href="{name}/timetable.html">{name}</a></td><td>{escape(str(row["solver_status"]))}</td><td>{escape(row["completion_status"])}</td><td>{row["scheduled"]}</td><td>{row["unscheduled"]}</td></tr>'
    body += '</table><p><a href="blocked-scopes.csv">Blocked cohort counts</a> / <a href="research-obligations.csv">Compulsory Research obligations</a> / <a href="enrollment/student-elective-allocations.csv">Editable test registrations</a> / <a href="enrollment/student-attendance-demand.csv">Corrected per-student demand</a> / <a href="configuration.json">Editable settings</a></p>'
    body += f'<p>{summary["original_cohort_student_sum"]} original scoped student-count entries = {summary["supported_cohort_student_sum"]} supported + {summary["blocked_cohort_student_sum"]} blocked. Cohort counts across semesters are not a deduplicated real student headcount.</p>'
    (directory/"index.html").write_text(_page("Step 12A.3.3: elective-component timetable results",body),encoding="utf-8")
