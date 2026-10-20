"""Validated PP1 outputs include every demand, including honest failed runs."""

from collections import Counter, defaultdict
import csv
from html import escape
import json
from pathlib import Path
from urllib.parse import quote

from schedence_teaching.preparation import prepare_timetable_input
from schedence_teaching.preferences import prepare_lecturer_preferences
from schedence_teaching.schemas import TeachingTimetableInput
from schedence_teaching.solver import SolverStatus, solve_timetable
from schedence_teaching.solver.evaluation import validate_evaluation_result
from schedence_teaching.solver.lunch_validation import validate_student_lunch_result

from .pp1 import attendance_capacity_diagnostics
from .preview import _new_directory


def _records(batch, result):
    offerings = {row.offering_id: row for row in batch.prepared.input_snapshot.module_offerings}
    groups = {row.group_id: row for row in batch.plan.groups}
    assigned = {row.session.occurrence_id: row for row in result.schedule}
    rows = []
    for session in batch.prepared.sessions:
        booking = assigned.get(session.occurrence_id)
        offering = offerings[session.offering_id]
        source = batch.source_offerings[session.offering_id]
        rows.append(dict(output_label=batch.configuration.output_label, status="SCHEDULED" if booking else "UNSCHEDULED",
            **({"delivery_mode": booking.delivery_mode.value if booking else "UNSCHEDULED"} if batch.config.delivery_calendar is not None else {}),
            occurrence_id=session.occurrence_id, requirement_id=session.requirement_id,
            offering_id=session.offering_id, offering_id_status=source["identifier_status"],
            source_workbook=source["workbook"], source_worksheet=source["worksheet"], source_row=source["row"],
            module_code=offering.module_code, module_name=offering.module_name,
            specialization=batch.group_specializations[session.group_id], source_specialization=offering.specialization,
            academic_year=offering.academic_year, semester=offering.semester, stream=session.stream.value,
            group_id=session.group_id, student_count=groups[session.group_id].student_count,
            session_type=session.session_type.value, duration_minutes=session.duration_minutes,
            lecturer_id=booking.assigned_staff_id if booking else "", venue_id=booking.assigned_venue_id if booking else "",
            day=booking.day if booking else "", on_date=batch.configuration.calendar.day_dates[booking.day].isoformat() if booking else "",
            start_time=f"{booking.start_minute//60:02}:{booking.start_minute%60:02}" if booking else "",
            end_time=f"{booking.end_minute//60:02}:{booking.end_minute%60:02}" if booking else ""))
    return rows


def _views(batch, rows):
    index = {kind: {} for kind in ("specialization", "group", "lecturer", "venue")}
    for specialization in sorted(set(batch.group_specializations.values())):
        index["specialization"][specialization] = []
    for group in batch.plan.groups:
        index["group"][group.group_id] = []
    for staff in batch.prepared.input_snapshot.staff_members:
        index["lecturer"][staff.staff_id] = []
    for venue in batch.solver_inputs["venue_input"].venues:
        index["venue"][venue.venue_id] = []
    pools = defaultdict(set)
    for member in batch.prepared.input_snapshot.teaching_team_members:
        pools[member.offering_id].add(member.staff_id)
    for row in rows:
        identity = row["occurrence_id"]
        index["specialization"][row["specialization"]].append(identity)
        attended = {row["group_id"], *batch.prepared.hierarchy.descendants(row["group_id"])}
        for group in attended:
            index["group"][group].append(identity)
        # Unscheduled staff rows are candidate eligibility, never assignments.
        staff_ids = {row["lecturer_id"]} if row["status"] == "SCHEDULED" else pools[row["offering_id"]]
        for staff in staff_ids:
            index["lecturer"][staff].append(identity)
        if row["venue_id"]:
            index["venue"][row["venue_id"]].append(identity)
    return index


def _table(rows):
    columns = ("status", "module_code", "module_name", "academic_year", "semester", "stream", "group_id", "session_type",
               "duration_minutes", "on_date", "start_time", "end_time", "lecturer_id", "venue_id")
    if any("delivery_mode" in row for row in rows):
        columns = (*columns, "delivery_mode")
    header = ''.join(f'<th>{escape(key.replace("_", " "))}</th>' for key in columns)
    body = ''.join('<tr>' + ''.join(f'<td>{escape(str(row[key]))}</td>' for key in columns) + '</tr>' for row in rows)
    return f'<div class="scroll"><table><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table></div>'


def _page(title, body, *, output_label="PP1_TEST_CALENDAR"):
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title><style>body{{font:14px/1.5 system-ui,sans-serif;background:#f3f6f9;color:#17324d;margin:24px}}
main{{max-width:1550px;margin:auto}}header{{background:#17324d;color:white;padding:24px;border-radius:10px}}h1{{margin:8px 0}}
.label{{background:#ffd27b;color:#332500;padding:5px 10px;font-weight:800}}.notice{{background:#fff0bc;padding:16px}}
.scroll{{overflow:auto;margin:16px 0}}table{{border-collapse:collapse;background:white;width:100%}}th,td{{border:1px solid #cbd5df;padding:8px;text-align:left;vertical-align:top}}
th{{background:#e5edf4}}details{{background:white;margin:10px 0;padding:12px}}a{{color:#175b91}}code{{overflow-wrap:anywhere}}</style></head>
<body><main><header><span class="label">{escape(output_label)}</span><h1>{escape(title)}</h1></header>{body}</main></body></html>'''


def prove_attendance_infeasibility(batch, *, max_time_seconds=15.0):
    """A source subset proves infeasibility; it never generates separate timetables.

    Every witness constraint already exists in the global request. Removing other
    requirements cannot make this subset harder, so INFEASIBLE certifies that the
    original complete request cannot fit either. UNKNOWN is never called proof.
    """
    diagnostics = attendance_capacity_diagnostics(batch)
    if not diagnostics:
        return None
    conflict = max(diagnostics, key=lambda row: row["shortfall_minutes"])
    audiences, current = set(), conflict["group_id"]
    while current is not None:
        audiences.add(current)
        current = batch.prepared.hierarchy.parents[current]
    raw = batch.prepared.input_snapshot.model_dump()
    raw["session_requirements"] = [row for row in raw["session_requirements"] if row["group_id"] in audiences]
    teaching = TeachingTimetableInput.model_validate(raw)
    prepared = prepare_timetable_input(teaching)
    requirement_ids = {row.requirement_id for row in teaching.session_requirements}
    contexts = dict(batch.solver_inputs)
    venues = contexts["venue_input"].model_copy(deep=True)
    venues.requirements = [row for row in venues.requirements if row.requirement_id in requirement_ids]
    contexts["venue_input"] = venues
    if "preference_input" in contexts:
        contexts["preference_input"] = prepare_lecturer_preferences(teaching, contexts["preference_input"].input_snapshot)
    if "work_periods" in contexts:
        from schedence_teaching.workload import prepare_work_periods
        contexts["work_periods"] = prepare_work_periods(teaching, contexts["staff_input"].workload_input,
            contexts["work_periods"].input_snapshot)
    config = batch.config.model_copy(update=dict(max_time_seconds=float(max_time_seconds)))
    witness = solve_timetable(prepared, config, **contexts)
    if witness.status in {SolverStatus.OPTIMAL, SolverStatus.FEASIBLE}:
        raise ValueError("A validated attendance-capacity violation cannot have a feasible source subset.")
    validate_student_lunch_result(prepared, config, witness, **contexts)
    return dict(purpose="INFEASIBILITY_DIAGNOSTIC_ONLY_NOT_A_SEPARATE_TIMETABLE", group_capacity=conflict,
        source_requirement_ids=sorted(requirement_ids), source_occurrences=len(prepared.sessions),
        solver_status=witness.status.value, source_subset_validated=True,
        fixed_teaching_windows_unchanged=True, result=witness.to_dict())


def export_pp1(batch, result, directory, *, witness=None):
    """Reject corrupt/partial solver outputs before creating ANY report files."""
    success = result.status in {SolverStatus.OPTIMAL, SolverStatus.FEASIBLE}
    if result.combination_enabled or any(hasattr(row, "covered_sessions") for row in result.schedule):
        raise ValueError("PP1 excludes shared/combined lecture deliveries.")
    if success:
        validate_evaluation_result(batch.prepared, batch.config, result, **batch.solver_inputs)
    else:
        validate_student_lunch_result(batch.prepared, batch.config, result, **batch.solver_inputs)
    rows = _records(batch, result)
    views = _views(batch, rows)
    scheduled = [row for row in rows if row["status"] == "SCHEDULED"]
    unscheduled = [row for row in rows if row["status"] == "UNSCHEDULED"]
    diagnostics = attendance_capacity_diagnostics(batch)
    if success and diagnostics:
        raise ValueError("A complete timetable cannot violate the independent weekly attendance bound.")
    research_pending = bool(batch.metadata.get("research_pending_cohort_count"))
    completion = ("ELECTIVE_COMPONENT_COMPLETE" if batch.metadata.get("output_component")=="YEAR4_ELECTIVE_COMPONENT" else
        "AVAILABLE_TEACHING_COMPONENT_COMPLETE") if success and research_pending else "COMPLETE_FOR_SUPPLIED_SESSION_INPUT" if success else "NO_COMPLETE_KNOWN_SESSION_TIMETABLE"
    report = dict(batch.metadata, solver_status=result.status.value, failure_reason=result.failure_reason,
        scheduled_sessions=len(scheduled), unscheduled_sessions=len(unscheduled),
        complete_timetable_generated=success and not research_pending, complete_input_component_generated=success,
        completion_status=completion, complete_year4_academic_timetable_generated=success and not research_pending
            and any(row.academic_year==4 for row in batch.plan.cohorts),
        source_offerings=batch.source_offerings,
        independently_validated=success, failure_output_integrity_validated=not success,
        clash_validation="PASSED" if success else "NOT_APPLICABLE_NO_TIMETABLE",
        student_capacity_failures=diagnostics, source_workbooks_modified=False,
        independent_attendance_capacity_status="INFEASIBLE" if diagnostics else "NO_WEEKLY_CAPACITY_VIOLATION_FOUND",
        diagnostic_witness_status=witness["solver_status"] if witness else None,
        unscheduled_counts_by_type=dict(Counter(row["session_type"] for row in unscheduled)),
        actual_campus_publication_ready=False)
    directory = _new_directory(directory)
    fieldnames = list(rows[0])
    for filename, records in (("timetable.csv", scheduled), ("unscheduled.csv", unscheduled), ("session-demand.csv", rows)):
        with (directory / filename).open("x", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
            writer.writeheader()
            writer.writerows(records)
    (directory / "solver-result.json").write_text(json.dumps(dict(output_label=batch.configuration.output_label, solver=result.to_dict()), indent=2), encoding="utf-8")
    (directory / "timetable.json").write_text(json.dumps(dict(output_label=batch.configuration.output_label,completion_status=completion,
        compulsory_research_pending=research_pending,scheduled=scheduled), indent=2), encoding="utf-8")
    (directory / "independent-validation.json").write_text(json.dumps(dict(
        status="PASSED" if success else "NOT_APPLICABLE_NO_TIMETABLE", complete_coverage=success,
        checked_occurrences=len(scheduled), constraints=result.to_dict()["constraint_validation"],
        student_lunch_checked=success and result.student_lunch_enabled,
        staff_policy_checked=success and result.staff_policy_enabled,
        venue_constraints_checked=success and result.venue_enabled,
        preference_report_recomputed=success and result.preference_optimization_enabled), indent=2), encoding="utf-8")
    (directory / "run-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (directory / "views.json").write_text(json.dumps(dict(output_label=batch.configuration.output_label,
        single_global_run=batch.metadata.get("solver_result_origin")!="COORDINATED_DECOMPOSITION",
        globally_coordinated=True,
        staff_unscheduled_rows_are_candidate_eligibility_only=True, views=views), indent=2), encoding="utf-8")
    (directory / "configuration.json").write_text(batch.configuration.model_dump_json(indent=2), encoding="utf-8")
    if witness:
        (directory / "infeasibility-witness.json").write_text(json.dumps(witness, indent=2), encoding="utf-8")
    by_id = {row["occurrence_id"]: row for row in rows}
    specs_dir = directory / "specializations"
    specs_dir.mkdir()
    links = []
    for code, identities in views["specialization"].items():
        filename = quote(code, safe="") + ".html"
        title = f'{code}: {result.status.value}'
        body = f'<p><a href="../timetable.html">Global run report</a></p><p>{len(identities)} source session occurrences. No separate specialization solve was performed.</p>'
        if research_pending:
            body += '<p class="notice">Elective/known teaching component only. Compulsory Year 4 Research remains COMPULSORY_RESEARCH_PENDING_SESSION_MAPPING; missing session details do not mean zero academic work.</p>'
        body += _table([by_id[identity] for identity in identities])
        (specs_dir / filename).write_text(_page(title, body, output_label=batch.configuration.output_label), encoding="utf-8")
        links.append(f'<li><a href="specializations/{escape(filename, quote=True)}">{escape(code)}</a>: {len(identities)} occurrences</li>')
    explanation = ("Complete independently validated known teaching component; compulsory Research session mapping remains unresolved." if success and research_pending else
        "Complete independently validated timetable." if success else
        "No complete timetable exists in this solver result. All demand is preserved below as unscheduled; no partial bookings or clash-free claim is emitted.")
    body = f'<p class="notice"><strong>Solver status: {result.status.value}</strong>. {explanation}</p>'
    if batch.configuration.output_label=="PP1_EXPERIMENTAL_CALENDAR":
        body += '<p class="notice"><strong>EXPERIMENTAL_CALENDAR_RESULT.</strong> This uses an experimental PP1 calendar, not an approved university timetable. Separate semester results must not be combined on overlapping actual dates.</p>'
        if success and batch.metadata.get("supported_semester_scope"):
            body += '<p><strong>COMPLETE_SUPPORTED_SCOPE:</strong> All known teaching occurrences for the selected supported semester were scheduled and independently validated. This does not resolve missing academic mappings or compulsory Research session mapping.</p>'
    body += f'<p><strong>Completion status: {escape(completion)}</strong></p>'
    if batch.metadata.get("solver_result_origin")=="COORDINATED_DECOMPOSITION":
        body += '<p class="notice">Coordinated native CP-SAT subsolves reserved earlier staff and venue bookings. The complete union was independently validated against the original scope. FEASIBLE does not claim joint objective optimality.</p>'
    if research_pending:
        body += '<p class="notice"><strong>COMPULSORY_RESEARCH_PENDING_SESSION_MAPPING.</strong> This is not a complete Year 4 academic timetable. No Research module code, duration, lecturer or supervision booking was invented; Research academic work is not assumed zero.</p>'
    if "selected_complete_scopes" in batch.metadata:
        body += f'<p class="notice"><strong>Selected scope only:</strong> {len(batch.metadata["selected_complete_scopes"])} complete academic scopes. '
        outside = batch.metadata.get("out_of_scope_occurrences")
        body += (f'{outside} original global occurrences are outside this request. ' if outside is not None else
            'Other cohorts are outside this request; their corrected enrollment demand is not counted here. ')
        body += 'All requirements within the selected scope are retained. Separate preview runs cannot be combined into one timetable.</p>'
        body += '<details><summary>Exact selected academic scopes</summary><ul>' + ''.join(
            f'<li>Year {row["academic_year"]} / Semester {row["semester"]} / {escape(row["stream"])} / {escape(row["specialization"])}</li>'
            for row in batch.metadata["selected_complete_scopes"]) + '</ul></details>'
    if diagnostics:
        body += '<p class="notice"><strong>Independent weekly attendance bound: INFEASIBLE.</strong> Original session demand exceeds available teaching time for at least one group.</p>'
    if witness:
        body += f'<p>Native source-subset diagnostic: <strong>{escape(witness["solver_status"])}</strong> '
        body += '<a href="infeasibility-witness.json">Original-requirement witness</a>. This is not a separately generated timetable.</p>'
    body += f'<p>{len(scheduled)} scheduled / {len(unscheduled)} unscheduled occurrences. {batch.plan.summary()["total_students"]} source students; proportional specialization counts are approved PP1 estimates.</p>'
    body += '<p><a href="timetable.csv">Scheduled CSV</a> · <a href="unscheduled.csv">Unscheduled CSV</a> · <a href="solver-result.json">Solver JSON</a> · <a href="run-report.json">Run report</a> · <a href="views.json">View index</a> · <a href="configuration.json">Editable run configuration</a></p>'
    body += '<h2>Specialization views</h2><ul>' + ''.join(links) + '</ul>'
    if diagnostics:
        scopes = Counter((row["academic_year"], row["semester"], row["specialization"], row["required_minutes"], row["available_minutes"]) for row in diagnostics)
        body += '<h2>Independent student time-capacity findings</h2><table><tr><th>Year</th><th>Semester</th><th>Specialization</th><th>Required hours</th><th>Available hours</th><th>Affected leaf groups</th></tr>'
        body += ''.join(f'<tr><td>{year}</td><td>{semester}</td><td>{escape(spec)}</td><td>{required/60:g}</td><td>{available/60:g}</td><td>{count}</td></tr>'
                        for (year, semester, spec, required, available), count in sorted(scopes.items())) + '</table>'
    if scheduled:
        from .weekly_view import weekly_html
        (directory / "weekly.html").write_text(weekly_html(batch, scheduled, views), encoding="utf-8")
        body += '<p><a href="weekly.html"><strong>Open interactive weekly group / lecturer / venue timetable</strong></a></p>'
        body += '<h2>Global coordinated timetable</h2>' + _table(scheduled)
    for kind in ("group", "lecturer"):
        body += f'<h2>{kind.title()} views</h2>'
        if kind == "lecturer":
            body += '<p>Unscheduled rows show source team eligibility only. They are not lecturer assignments or workload.</p>'
        for identity, occurrences in views[kind].items():
            body += f'<details><summary>{escape(identity)} ({len(occurrences)} occurrences)</summary>' + _table([by_id[item] for item in occurrences]) + '</details>'
    body += '<h2>Venue views</h2>'
    for venue in batch.solver_inputs["venue_input"].venues:
        occurrences = views["venue"][venue.venue_id]
        body += f'<details><summary>{escape(venue.venue_id)}: {escape(venue.venue_type.value)}, capacity {venue.capacity}, {len(occurrences)} bookings</summary><p>{escape(str(venue.building))} / floor {escape(str(venue.floor))}</p>'
        if venue.venue_id in batch.solver_inputs["venue_input"].unresolved_venue_ids:
            body += '<p>Excluded: component-room booking relationship remains unspecified.</p>'
        body += _table([by_id[item] for item in occurrences]) + '</details>'
    body += '<h2>Source and testing boundaries</h2><ul>' + ''.join(f'<li>{escape(item)}</li>' for item in report["limitations"]) + '</ul>'
    (directory / "timetable.html").write_text(_page("Coordinated PP1 timetable run", body, output_label=batch.configuration.output_label), encoding="utf-8")
    return report


def run_pp1(batch, directory):
    result = solve_timetable(batch.prepared, batch.config, **batch.solver_inputs)
    witness = prove_attendance_infeasibility(batch) if result.status not in {SolverStatus.OPTIMAL, SolverStatus.FEASIBLE} else None
    report = export_pp1(batch, result, directory, witness=witness)
    return result, report
