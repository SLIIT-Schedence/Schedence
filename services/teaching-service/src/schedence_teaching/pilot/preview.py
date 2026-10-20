"""Static previews contain independently validated physical deliveries only."""

import csv
from dataclasses import dataclass
from enum import StrEnum
from html import escape
import json
from pathlib import Path
from uuid import UUID

from schedence_teaching.integration import ReadinessDiagnostic, ReadinessReport, SchedulingSubmission, assess_readiness, run_scheduling
from schedence_teaching.lunch.applicability import attending_group_ids
from schedence_teaching.schemas import StudentGroup
from schedence_teaching.solver import SolverStatus
from schedence_teaching.solver.evaluation import validate_evaluation_result

from .students import PlannedGroups


class PilotMode(StrEnum):
    REAL_CAMPUS = "REAL_CAMPUS"
    DEMO = "DEMO"


@dataclass(frozen=True)
class PilotPreviewResult:
    readiness: ReadinessReport
    solver_status: str
    label: str | None
    paths: dict[str, str]
    result: object | None = None


def _time(minutes):
    return f"{minutes//60:02}:{minutes%60:02}"


def _new_directory(directory):
    directory = Path(directory)
    if directory.exists() and any(directory.iterdir()):
        raise FileExistsError("Use a new or empty preview directory; existing outputs are never overwritten.")
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _projection(preparation, result, label):
    offerings = {row.offering_id: row for row in preparation.prepared.input_snapshot.module_offerings}
    staff_types = {row.staff_id: row.staff_type.value for row in preparation.prepared.input_snapshot.staff_members}
    calendar = preparation.solver_inputs["student_lunch_calendar"]
    records = []
    for delivery in sorted(result.schedule, key=lambda row: (calendar.day_dates[row.day], row.start_minute, row.delivery_id)):
        covered = delivery.covered_sessions
        records.append(dict(output_label=label, delivery_id=delivery.delivery_id,
            module_codes=";".join(sorted({offerings[row.offering_id].module_code for row in covered})),
            module_names=";".join(sorted({offerings[row.offering_id].module_name for row in covered})),
            offering_ids=";".join(sorted({row.offering_id for row in covered})),
            specializations=";".join(sorted({preparation.solver_inputs["combination_input"].input_snapshot.group_specializations[identity]
                                           for identity in delivery.group_ids})),
            session_type=covered[0].session_type.value, stream=covered[0].stream.value,
            group_ids=";".join(delivery.group_ids),
            attending_group_ids=";".join(sorted(attending_group_ids(preparation.prepared, delivery.group_ids))),
            student_count=delivery.combined_student_count, lecturer_id=delivery.assigned_staff_id,
            staff_type=staff_types[delivery.assigned_staff_id],
            venue_id=delivery.assigned_venue_id, day=delivery.day, on_date=calendar.day_dates[delivery.day].isoformat(),
            start_time=_time(delivery.start_minute), end_time=_time(delivery.end_minute),
            start_minute=delivery.start_minute, end_minute=delivery.end_minute,
            is_combined=delivery.is_combined, occurrence_ids=";".join(row.occurrence_id for row in covered)))
    return records


def _view_index(records, preparation, result):
    """Views reference the same validated physical bookings; never solve per view."""
    deliveries = {row.delivery_id: row for row in result.schedule}
    group_specs = preparation.solver_inputs["combination_input"].input_snapshot.group_specializations
    offerings = {row.offering_id: row for row in preparation.prepared.input_snapshot.module_offerings}
    index = {kind: {} for kind in ("specialization", "lecturer", "group", "venue")}
    for row in records:
        delivery = deliveries[row["delivery_id"]]
        specs = {group_specs[identity] for identity in delivery.group_ids if identity in group_specs}
        if not specs:
            specs = {offerings[item.offering_id].specialization for item in delivery.covered_sessions}
        keys = dict(specialization=specs, lecturer={delivery.assigned_staff_id},
            group=attending_group_ids(preparation.prepared, delivery.group_ids), venue={delivery.assigned_venue_id})
        for kind, identities in keys.items():
            for identity in sorted(identities):
                index[kind].setdefault(identity, []).append(delivery.delivery_id)
    return {kind: dict(sorted(values.items())) for kind, values in index.items()}


def _views_html(records, views):
    by_id = {row["delivery_id"]: row for row in records}
    sections = []
    for kind, entries in views.items():
        blocks = []
        for identity, delivery_ids in entries.items():
            lines = []
            for delivery_id in delivery_ids:
                row = by_id[delivery_id]
                values = (row["on_date"], row["stream"], f'{row["start_time"]}-{row["end_time"]}', row["module_codes"],
                    row["session_type"], row["group_ids"], row["lecturer_id"], row["venue_id"], delivery_id)
                lines.append('<tr>' + ''.join(f'<td>{escape(str(value))}</td>' for value in values) + '</tr>')
            headers = ''.join(f'<th>{value}</th>' for value in ('Date', 'Stream', 'Time', 'Module', 'Type', 'Groups', 'Staff', 'Venue', 'Booking'))
            blocks.append(f'<details><summary>{escape(identity)} ({len(delivery_ids)} deliveries)</summary>'
                f'<div class="grid"><table><thead><tr>{headers}</tr></thead><tbody>{"".join(lines)}</tbody></table></div></details>')
        sections.append(f'<section id="view-{kind}"><h2>{kind.title()} views</h2>{"".join(blocks)}</section>')
    return '<p>These views reference one globally validated timetable. A shared delivery remains one booking across all views.</p>' + ''.join(sections)


def _html(records, preparation, label, plan, assumptions, views):
    calendar = preparation.solver_inputs["student_lunch_calendar"]
    lunch = preparation.solver_inputs["student_lunch_policy"]
    groups = {row.group_id: row for row in preparation.prepared.input_snapshot.student_groups}
    years_by_stream = {(row.academic_year, row.stream) for row in groups.values()}
    windows = {row.academic_year: row.window for row in lunch.year_periods}
    lunches = {day.day: sorted({(windows[year].start_minute, windows[year].end_minute, year)
        for year, stream in years_by_stream if calendar.day_dates[day.day] in lunch.applicable_dates
        and stream in lunch.applicable_streams and stream in day.windows}) for day in preparation.config.days}
    boundaries = {minute for row in records for minute in (row["start_minute"], row["end_minute"])}
    boundaries.update(minute for day in preparation.config.days for spans in day.windows.values()
                      for window in spans for minute in (window.start_minute, window.end_minute))
    boundaries.update(minute for periods in lunches.values() for start, end, _ in periods for minute in (start, end))
    times = sorted(boundaries)
    days = sorted(preparation.config.days, key=lambda row: calendar.day_dates[row.day])
    headings = "".join(f"<th>{escape(day.day.title())}<small>{calendar.day_dates[day.day].isoformat()}</small></th>" for day in days)
    lines = []
    for start, end in zip(times, times[1:]):
        cells = []
        for day in days:
            content = []
            for row in records:
                if row["day"] != day.day or not row["start_minute"] < end or not start < row["end_minute"]:
                    continue
                if row["start_minute"] != start:
                    content.append(f'<div class="continues">Continues: {escape(row["module_codes"])}</div>')
                    continue
                short_groups = ", ".join("G" + value.rsplit("-G", 1)[-1] if "-G" in value else value for value in row["group_ids"].split(";"))
                combined = '<b class="combined">COMBINED</b> ' if row["is_combined"] else ""
                content.append(f'<article class="session" title="{escape(row["group_ids"], quote=True)}">{combined}'
                    f'<strong>{escape(row["module_codes"])} · {escape(row["session_type"])}</strong>'
                    f'<span>{escape(row["module_names"])}</span><span>{escape(short_groups)} · {row["student_count"]} students</span>'
                    f'<span>{"Instructor" if row["staff_type"] == "INSTRUCTOR" else "Lecturer"} {escape(row["lecturer_id"])} · Venue {escape(row["venue_id"])}</span>'
                    f'<span>{row["start_time"]}–{row["end_time"]}</span><small>{escape(row["offering_ids"])}</small></article>')
            for first, last, year in lunches[day.day]:
                if first < end and start < last:
                    content.append(f'<div class="lunch">Year {year} student lunch {_time(first)}–{_time(last)}</div>')
            cells.append("<td>" + "".join(content) + "</td>")
        lines.append(f'<tr><th class="time">{_time(start)}–{_time(end)}</th>{"".join(cells)}</tr>')
    details = "".join(f"<li>{escape(item)}</li>" for item in assumptions)
    summary = plan.summary()
    scopes = "; ".join(f"Year {year} / Semester {semester} / {stream}" for year, semester, stream in sorted({
        (row.academic_year, row.semester, row.stream.value) for row in groups.values()}))
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Schedence pilot — {escape(label)}</title><style>
body{{font:15px/1.5 system-ui,sans-serif;margin:0;background:#f2f5f8;color:#192c3e}}main{{max-width:1500px;margin:auto;padding:28px}}
header{{background:#17324d;color:white;padding:24px;border-radius:12px}}h1{{margin:8px 0}}.label{{background:#ffd27b;color:#332500;padding:5px 12px;border-radius:5px;font-weight:800}}
.stats{{display:flex;gap:16px;flex-wrap:wrap;margin:20px 0}}.stats div{{background:white;padding:14px 24px;border-radius:8px}}.stats strong{{font-size:26px;display:block}}
.grid{{overflow:auto}}table{{width:100%;min-width:1000px;border-collapse:collapse;background:white}}th,td{{border:1px solid #ced8e1;padding:10px;vertical-align:top}}thead th{{background:#e4edf4}}small,span{{display:block}}small{{font-size:11px;overflow-wrap:anywhere}}.time{{width:105px;white-space:nowrap}}.session{{background:#e7f2ff;border-left:4px solid #2773ae;border-radius:4px;padding:10px;margin-bottom:8px}}.combined{{color:#16654a}}.lunch{{background:#fff0bc;padding:8px;border-radius:4px}}.continues{{font-size:12px;color:#52677a}}details{{background:white;margin-top:20px;padding:16px;border-radius:8px}}a{{color:#175b91}}
</style></head><body><main><header><b class="label">{escape(label)}</b><h1>Teaching timetable · pilot preview</h1>
<p>{calendar.week_start} to {calendar.week_end} · {escape(scopes)}<br>Source inputs and scheduling assumptions are distinguished below.</p></header>
<section class="stats"><div><strong>{summary["total_students"]}</strong>students — parents counted once</div><div><strong>{summary["main_group_count"]}</strong>main groups</div><div><strong>{summary["lab_subgroup_count"]}</strong>lab subgroups</div><div><strong>{len(records)}</strong>physical deliveries</div></section>
<p><a href="timetable.csv">CSV assignments</a> · <a href="solver-result.json">Solver result</a> · <a href="readiness-assumptions.json">Readiness and assumptions</a> · <a href="views.json">View index</a></p>
<nav><a href="#view-specialization">Specializations</a> · <a href="#view-lecturer">Lecturers</a> · <a href="#view-group">Groups</a> · <a href="#view-venue">Venues</a></nav>
<div class="grid"><table><thead><tr><th>Time</th>{headings}</tr></thead><tbody>{"".join(lines)}</tbody></table></div>
<p>Each card is one physical booking. Continuations are the same booking; a parent lecture includes its lab subgroups. Only configured teaching dates are shown. Student lunch does not imply lecturer lunch.</p>
{_views_html(records, views)}
<details open><summary>Source status and assumptions</summary><ul>{details}</ul></details></main></body></html>'''


def export_validated_preview(directory, preparation, result, *, label, plan, report, assumptions=()):
    if label not in {"SYNTHETIC_DEMO", "CAMPUS_INPUT_VALIDATED"}:
        raise ValueError("Use an explicit supported preview label.")
    synthetic = preparation.solver_inputs["student_lunch_calendar"].synthetic_data
    if synthetic != (label == "SYNTHETIC_DEMO"):
        raise ValueError("Preview label must agree with the original scheduling context.")
    if label == "CAMPUS_INPUT_VALIDATED" and report.get("readiness", {}).get("ready_for_actual_campus") is not True:
        raise ValueError("A campus preview requires the actual-campus readiness report.")
    # Recompute every original enabled context/report before exporting anything.
    validate_evaluation_result(preparation.prepared, preparation.config, result, **preparation.solver_inputs)
    records = _projection(preparation, result, label)
    views = _view_index(records, preparation, result)
    html = _html(records, preparation, label, plan, assumptions, views)
    directory = _new_directory(directory)
    with (directory / "timetable.csv").open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(records)
    (directory / "timetable.html").write_text(html, encoding="utf-8")
    (directory / "solver-result.json").write_text(json.dumps(dict(output_label=label, solver=result.to_dict()), indent=2), encoding="utf-8")
    (directory / "readiness-assumptions.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    (directory / "views.json").write_text(json.dumps(dict(output_label=label,
        single_global_timetable=True, physical_delivery_count=len(records), views=views), indent=2), encoding="utf-8")
    return {key: str(directory / name) for key, name in dict(html="timetable.html", csv="timetable.csv",
        solver_json="solver-result.json", readiness_json="readiness-assumptions.json", views_json="views.json").items()}


def run_pilot_preview(submission: SchedulingSubmission, plan: PlannedGroups, directory: Path, *,
        institution_id: UUID, mode: PilotMode, source_report: dict, assumptions=()) -> PilotPreviewResult:
    mode = PilotMode(mode)
    actual = mode is PilotMode.REAL_CAMPUS
    assessment = assess_readiness(submission, expected_institution_id=institution_id, actual_campus=actual)
    issues = list(assessment.report.diagnostics)
    try:
        supplied = submission.inputs["combination_input"]["teaching_input"]["student_groups"]
        groups = [StudentGroup.model_validate(row) for row in supplied]
        if {row.group_id: row for row in groups} != {row.group_id: row for row in plan.groups}:
            raise ValueError("Different group plan.")
        if actual and any(submission.inputs["combination_input"].get("group_specializations", {}).get(identity) != value
                          for identity, value in plan.specializations.items()):
            raise ValueError("Supplied specialization differs from the workbook-derived source plan.")
    except (ValueError, KeyError, TypeError):
        issues.append(ReadinessDiagnostic("UNVERIFIED", "student_plan_mapping", ("combination_input", "teaching_input", "student_groups"),
            "Supply reviewed group/source evidence matching this workbook-derived planning scope; aggregate counts alone do not approve groups or population relationships."))
    readiness = ReadinessReport(assessment.report.mode, tuple(issues))
    report = dict(requested_mode=mode.value, university_ready=False, source=source_report,
        pilot_group_plan=plan.summary(), groups=[row.model_dump(mode="json") for row in plan.groups],
        readiness=readiness.to_dict(), assumptions=list(assumptions), independently_validated=False,
        missing_real_inputs=["Authorized institution, term, actual week and operating windows", "Authoritative offering IDs and streams",
            "Confirmed specialization and group/partition/population applicability", "Group-specific session demand",
            "Staff ADS crosswalk, policy applicability, availability and caps", "Venue calendars, blocks and shared/combined-room relationships",
            "Approved preference/fairness/consecutive and administrative/research applicability", "Actual lunch date/stream applicability and dated source confirmations"]
            if not actual or not readiness.ready_for_generation else [])
    if not readiness.ready_for_generation:
        directory = _new_directory(directory)
        path = directory / "readiness-assumptions.json"
        path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        return PilotPreviewResult(readiness, "BLOCKED", None, {"readiness_json": str(path)})
    output = run_scheduling(submission, expected_institution_id=institution_id, actual_campus=actual)
    result = output.timetable
    if not output.independently_validated or result.status not in {SolverStatus.OPTIMAL, SolverStatus.FEASIBLE}:
        directory = _new_directory(directory)
        report.update(solver_status=result.status.value if result else "BLOCKED", failure_reason=result.failure_reason if result else None)
        path = directory / "readiness-assumptions.json"
        path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        return PilotPreviewResult(readiness, report["solver_status"], None, {"readiness_json": str(path)}, result)
    label = "CAMPUS_INPUT_VALIDATED" if actual else "SYNTHETIC_DEMO"
    report.update(output_label=label, solver_status=result.status.value, independently_validated=True,
        physical_deliveries=len(result.schedule), original_occurrences=result.validation.expected_occurrences,
        combined_deliveries=result.validation.combined_deliveries, lunch=result.student_lunch_report.to_dict(),
        planned_main_student_total=plan.summary()["total_students"])
    paths = export_validated_preview(directory, assessment.preparation, result, label=label, plan=plan, report=report, assumptions=assumptions)
    return PilotPreviewResult(readiness, result.status.value, label, paths, result)
