"""Generate private PP1 previews from PostgreSQL; production stays read-only.

Explicit invocation only. Historical cached JSON is used for comparison, never
as a replacement for the freshly loaded database generation input.
"""

import argparse
from dataclasses import replace
from html import escape
import json
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from schedence_teaching.pilot.coordinated_streams import solve_coordinated_cohorts
from schedence_teaching.pilot.lab_diagnostics import export_lab_diagnostics
from schedence_teaching.pilot.pp1_run import _page
from schedence_teaching.pilot.semester_scenarios import export_scenario
from schedence_teaching.schemas.models import InputContract, Semester
from schedence_teaching.schemas import Stream
from schedence_teaching.solver import SolverStatus, solve_timetable

from .database_read import DatabaseTarget, load_database_evidence
from .generation_inputs import DatabaseGenerationRequest, build_database_generation, compare_snapshot_inputs
from .historical_generation import booking_differences, historical_draft
from .snapshots import restore_snapshot, snapshot_payload

SERVICE_ROOT = Path(__file__).resolve().parents[3]
LOCAL_DATA = SERVICE_ROOT / "local-data"


class DatabasePreviewConfiguration(InputContract):
    institution_id: UUID
    snapshots: dict[Semester, UUID]
    calendar_label: Literal["PP1_TEST_CALENDAR", "PP1_EXPERIMENTAL_CALENDAR"]
    policy_version: Literal["HISTORICAL_SNAPSHOT"]
    seconds_per_cohort: Annotated[float, Field(gt=0, allow_inf_nan=False)] = 30.0

    @model_validator(mode="after")
    def both_alternatives(self):
        if self.institution_id.int == 0 or set(self.snapshots) != {1, 2} or len(set(self.snapshots.values())) != 2:
            raise ValueError("select two distinct semester snapshots and an existing institution")
        return self


def write_json(path, value):
    with Path(path).open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False, default=str)


def generate_scope(loaded, name, directory, *, coordinated):
    enrolled = loaded.enrolled
    batch = enrolled.batch
    only_year4 = all(row.academic_year == 4 for row in batch.plan.cohorts)
    batch = replace(batch, metadata=dict(batch.metadata, scenario_name=name, scenario_label="DATABASE_BACKED_PREVIEW",
        semesters=[loaded.request.semester], blocked_scopes=list(loaded.blocked_scopes),
        blocked_student_count=sum(row.get("student_count", 0) or 0 for row in loaded.blocked_scopes),
        excluded_proven_calendar_infeasible_cohorts=[], supported_semester_scope=coordinated,
        output_component="YEAR4_ELECTIVE_COMPONENT" if only_year4 else "ALL_SUPPORTED_KNOWN_TEACHING_COMPONENTS",
        database_input_provenance=loaded.provenance, no_excel_files_read=True,
        solver_result_origin="COORDINATED_DECOMPOSITION" if coordinated else "MONOLITHIC_NATIVE_CP_SAT"))
    enrolled = replace(enrolled, batch=batch)
    diagnostics = directory / f"{name}-diagnostics"
    diagnostics.mkdir()
    capacity = export_lab_diagnostics(batch, diagnostics)
    if coordinated:
        result, stages = solve_coordinated_cohorts(enrolled, seconds_per_part=loaded.request.max_time_seconds)
    else:
        result = solve_timetable(batch.prepared, batch.config, **batch.solver_inputs)
        stages = None
    summary = export_scenario(enrolled, result, directory / name, capacity)
    successful = result.status in {SolverStatus.FEASIBLE, SolverStatus.OPTIMAL}
    require_count = len(batch.prepared.sessions)
    if successful and (not summary["independently_validated"] or summary["scheduled_occurrences"] != require_count):
        raise ValueError("a successful database preview must independently cover every selected occurrence")
    summary.update(database_input_provenance=loaded.provenance, database_writes_performed=False,
        scope_coverage="ALL_SELECTED_KNOWN_OCCURRENCES_VALIDATED" if successful else "NOT_COMPLETE",
        academic_complete=successful and not loaded.blocked_scopes and not enrolled.research_pending_cohort_ids,
        blocked_academic_mapping_scopes=list(loaded.blocked_scopes),
        research_status="RESEARCH_PENDING_SESSION_MAPPING" if enrolled.research_pending_cohort_ids else "NOT_APPLICABLE_TO_SELECTED_SCOPE",
        policy_differences=loaded.equivalence["policy_differences"], coordinated_stages=stages,
        separate_semester_alternatives=True, concurrent_semester_validation_performed=False)
    # export_scenario already wrote its report. Keep the enriched DB result in
    # another file rather than overwriting an existing exporter artifact.
    write_json(directory / name / "database-generation-report.json", summary)
    write_json(directory / name / "input-equivalence.json", loaded.equivalence)
    return enrolled, result, summary


def preview_index(directory, summaries):
    body = ('<p class="notice">Read-only database-backed previews. The explicitly selected test calendar '
        'and historical synthetic elective policy are testing inputs. Semesters are separate alternatives. '
        'Compulsory Research and missing DS mappings remain unresolved.</p><table><tr><th>Scope</th>'
        '<th>Solver</th><th>Scheduled / required</th><th>Academic complete</th><th>Views</th></tr>')
    for row in summaries:
        name = escape(row["scenario"], quote=True)
        body += (f'<tr><td>{name}</td><td>{escape(row["solver_status"])}</td><td>{row["scheduled_occurrences"]} / '
            f'{row["required_occurrences"]}</td><td>{row["academic_complete"]}</td><td>'
            f'<a href="{name}/weekly.html">Weekly views</a> | <a href="{name}/timetable.html">Report</a> | '
            f'<a href="{name}/timetable.csv">CSV</a> | <a href="{name}/solver-result.json">JSON</a></td></tr>')
    body += '</table>'
    # This index is the current progress view inside a new run directory.
    (directory / "index.html").write_text(_page("Step 12B.3 database-backed previews", body,
        output_label=summaries[0]["output_label"]), encoding="utf-8")


async def run_database_preview(engines, settings, target, directory, earlier_directory):
    directory = Path(directory).resolve()
    if not directory.is_relative_to(LOCAL_DATA.resolve()) or directory.exists():
        raise ValueError("use a new preview directory inside ignored local-data")
    settings = DatabasePreviewConfiguration.model_validate(settings.model_dump())
    directory.mkdir(parents=True)
    write_json(directory / "request.json", settings.model_dump(mode="json"))
    evidence_by_semester, inputs, historical, summaries = {}, {}, {}, []
    for semester in (1, 2):
        print(json.dumps(dict(stage="READ_ONLY_DATABASE_INPUT", semester=semester)), flush=True)
        evidence = await load_database_evidence(engines, settings.institution_id, settings.snapshots[semester], target)
        evidence_by_semester[semester] = evidence
        request = DatabaseGenerationRequest(institution_id=settings.institution_id, snapshot_id=settings.snapshots[semester],
            semester=semester, calendar_label=settings.calendar_label, policy_version=settings.policy_version,
            max_time_seconds=settings.seconds_per_cohort)
        loaded = build_database_generation(evidence, request)
        earlier = restore_snapshot(json.loads((Path(earlier_directory) / f"semester-{semester}.snapshot.json").read_text(encoding="utf-8")))
        equivalence = compare_snapshot_inputs(evidence.document, earlier)
        loaded.equivalence["earlier_excel_based_snapshot"] = equivalence
        inputs[semester] = loaded
        write_json(directory / f"semester-{semester}.database-snapshot.json", snapshot_payload(evidence.document))
        write_json(directory / f"semester-{semester}.input-equivalence.json", loaded.equivalence)
        historical[semester], audit = historical_draft(evidence, reconstruct_full_enrollment(loaded, evidence))
        write_json(directory / f"semester-{semester}.historical-validation.json", audit)
        print(json.dumps(dict(stage="DATABASE_INPUT_VALIDATED", semester=semester,
            expected=len(loaded.enrolled.batch.prepared.sessions), historical_validation=audit["status"])), flush=True)
    # A supported Year 4 IT weekday cohort exercises actual elective attendance
    # before attempting the complete coordinated semester alternatives.
    small_request = DatabaseGenerationRequest.model_validate(inputs[1].request.model_dump() | dict(academic_years=frozenset({4}),
        streams=frozenset({Stream.WEEKDAY}), specializations=frozenset({"IT"})))
    small = build_database_generation(evidence_by_semester[1], small_request)
    _, small_result, summary = generate_scope(small, "small-year4-semester1-it-weekday", directory, coordinated=False)
    summaries.append(summary)
    preview_index(directory, summaries)
    if small_result.status in {SolverStatus.FEASIBLE, SolverStatus.OPTIMAL}:
        for semester in (1, 2):
            print(json.dumps(dict(stage="SOLVING_FULL_SUPPORTED_SEMESTER", semester=semester)), flush=True)
            _, result, summary = generate_scope(inputs[semester], f"semester-{semester}", directory, coordinated=True)
            summary["historical_booking_comparison"] = booking_differences(historical[semester], result)
            write_json(directory / f"semester-{semester}" / "historical-comparison.json", summary["historical_booking_comparison"])
            summaries.append(summary)
            preview_index(directory, summaries)
            print(json.dumps({key: summary[key] for key in ("scenario", "solver_status", "required_occurrences",
                "scheduled_occurrences", "unscheduled_occurrences", "runtime_seconds")}), flush=True)
    # Fresh restricted reads, after the solver and exports have finished.
    comparisons = []
    for semester in (1, 2):
        after = await load_database_evidence(engines, settings.institution_id, settings.snapshots[semester], target)
        unchanged = evidence_by_semester[semester].fingerprint() == after.fingerprint()
        if not unchanged:
            raise ValueError("owner data or historical timetable records changed during the preview")
        comparisons.append(dict(semester=semester, before_sha256=evidence_by_semester[semester].fingerprint(),
            after_sha256=after.fingerprint(), all_owner_and_historical_rows_unchanged=True))
    write_json(directory / "database-preservation.json", comparisons)
    write_json(directory / "run-summary.json", summaries)
    write_json(directory / "remaining-blockers.json", dict(
        academic_mapping=[row.model_dump(mode="json") for e in evidence_by_semester.values()
            for row in e.document.scopes if row.scope_status != "SUPPORTED"],
        compulsory_research_status="RESEARCH_PENDING_SESSION_MAPPING", research_is_not_zero_academic_work=True,
        policy_version="HISTORICAL_SNAPSHOT", current_review_not_silently_applied=True,
        authoritative_offering_ids_not_fabricated=True, no_production_versions_created=True))
    return summaries


def reconstruct_full_enrollment(loaded, evidence):
    # History validation uses the full original snapshot, not a narrowed request
    # or a search-budget projection. No Excel or database access occurs here.
    from .generation_inputs import reconstruct_enrollment
    return reconstruct_enrollment(evidence)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--credentials-file", type=Path, required=True)
    parser.add_argument("--earlier-snapshots", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--branch-id", required=True)
    args = parser.parse_args(argv)
    from schedence_db import create_db_engine, load_settings
    from schedence_db.runtime import run
    async def execute():
        engines = {schema: create_db_engine(load_settings(env_file=args.credentials_file,
            env_prefix=f"{schema.upper()}_", db_echo=False, db_connect_timeout=30), direct=True, hide_parameters=True)
            for schema in ("teaching", "academic", "resource")}
        try:
            return await run_database_preview(engines, DatabasePreviewConfiguration.model_validate_json(args.request.read_text(encoding="utf-8")),
                DatabaseTarget(args.database, args.project_id, args.branch_id), args.output, args.earlier_snapshots)
        finally:
            for engine in engines.values():
                await engine.dispose()
    try:
        rows = run(execute())
        return 0 if len(rows) == 3 and all(row["independently_validated"] for row in rows) else 2
    except Exception as error:
        from .ports import PersistenceConflict
        print(json.dumps(dict(status="PREVIEW_FAILED", safe_error_type=type(error).__name__,
            explanation=str(error) if isinstance(error, PersistenceConflict) else None,
            sqlstate=getattr(getattr(error, "orig", None), "sqlstate", None), database_writes_performed=False)), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
