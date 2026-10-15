"""Audit baseline PP1 demand and solve progressively larger COMPLETE scopes."""

import argparse
import json
from math import isfinite

from .__main__ import LOCAL_DATA, SERVICE_ROOT, load_reference_sources
from .allocations import import_specialization_allocations
from .demand_audit import audit_demand, export_demand_audit, _csv
from .pp1 import PP1Configuration, prepare_pp1
from .pp1_run import run_pp1, _records
from .scopes import select_complete_scope
from .students import import_student_counts


def main(argv=None):
    from pathlib import Path
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=SERVICE_ROOT / "examples" / "pp1-test-calendar.json")
    parser.add_argument("--seconds-per-scope", type=float, default=120.0)
    parser.add_argument("--audit-only", action="store_true")
    args = parser.parse_args(argv)
    if not isfinite(args.seconds_per_scope) or args.seconds_per_scope <= 0:
        parser.error("Scope search budget must be a finite positive number of seconds.")
    if not args.output.resolve().is_relative_to(LOCAL_DATA.resolve()):
        parser.error("Private outputs must stay inside ignored Teaching Service local-data.")
    if args.output.exists():
        parser.error("Choose a new output directory; previous outputs are preserved.")
    original = import_student_counts(LOCAL_DATA / "Schedence_Student_Groups_One_Sheet_Editable.xlsx")
    allocations = import_specialization_allocations(LOCAL_DATA / "Schedence_Specialization_Student_Counts_Proportional_Editable.xlsx", original)
    academic, venues = load_reference_sources(LOCAL_DATA)
    configuration = PP1Configuration.model_validate_json(args.config.read_text(encoding="utf-8-sig"))
    batch = prepare_pp1(allocations, academic, venues, configuration)
    audit = audit_demand(batch, academic)
    export_demand_audit(audit, args.output / "audit")
    target = [row for row in audit["module_demand"] if (row["academic_year"], row["semester"], row["stream"], row["specialization"]) == (4, 1, "WEEKEND", "IT")]
    if target:
        _csv(args.output / "audit" / "year4-sem1-it-weekend.csv", target)
    summary = {key: audit[key] for key in ("source_reconciliation_passed", "issues", "occurrence_count", "leaf_count", "overloaded_leaf_count", "overloaded_scope_count")}
    summary["scope_runs"] = []
    summary["academic_scope_count"] = len(audit["scope_capacity"])
    from types import SimpleNamespace
    baseline_rows = _records(batch, SimpleNamespace(schedule=()))
    _csv(args.output / "baseline-unscheduled-requirements.csv", baseline_rows)
    (args.output / "baseline-feasibility.json").write_text(json.dumps(dict(
        native_global_solver_status="NOT_RUN_IN_THIS_AUDIT", source_reconciliation_passed=audit["source_reconciliation_passed"],
        independent_calendar_status="PROVEN_INFEASIBLE" if audit["source_reconciliation_passed"] and audit["overloaded_leaf_count"] else "NOT_PROVEN",
        original_occurrences=len(batch.prepared.sessions), baseline_scheduled=0,
        overloaded_leaf_count=audit["overloaded_leaf_count"],
        explanation="An individual leaf must attend all own and ancestor teaching. Demand exceeding the union of available teaching minutes after fixed lunch proves the complete request infeasible, regardless of lecturer/venue supply."), indent=2), encoding="utf-8")
    if audit["source_reconciliation_passed"] and not args.audit_only:
        known = {(row["academic_year"], row["semester"], row["stream"], row["specialization"]) for row in audit["scope_capacity"]}
        scopes = [dict(name="01-year1-sem1-it-weekday", keys={(1, 1, "WEEKDAY", "IT")}),
            dict(name="02-year1-sem1-all-specializations-weekday", keys={key for key in known if key[:3] == (1, 1, "WEEKDAY")}),
            dict(name="03-year1-both-semesters-all-specializations-weekday", keys={key for key in known if key[0] == 1 and key[2] == "WEEKDAY"}),
            dict(name="04-year1-both-streams-time-fitting-scopes", keys={
                (row["academic_year"], row["semester"], row["stream"], row["specialization"])
                for row in audit["scope_capacity"] if row["academic_year"] == 1 and not row["minimum_additional_teaching_hours"]})]
        for stage in scopes:
            selected = select_complete_scope(batch, stage["keys"], max_time_seconds=args.seconds_per_scope)
            print(json.dumps(dict(stage=stage["name"], complete_occurrences=len(selected.prepared.sessions)), indent=2), flush=True)
            result, report = run_pp1(selected, args.output / stage["name"])
            selected_ids = {row.occurrence_id for row in selected.prepared.sessions}
            outside = [dict(row, status="UNSCHEDULED_OUTSIDE_SELECTED_SCOPE") for row in baseline_rows if row["occurrence_id"] not in selected_ids]
            if outside:
                _csv(args.output / stage["name"] / "outside-selected-scope.csv", outside)
            row = dict(name=stage["name"], solver_status=result.status.value, scheduled=report["scheduled_sessions"],
                unscheduled=report["unscheduled_sessions"], validation=report["clash_validation"],
                selected_complete_scopes=report["selected_complete_scopes"],
                out_of_scope_occurrences=report["out_of_scope_occurrences"])
            summary["scope_runs"].append(row)
            (args.output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
            print(json.dumps(row, indent=2), flush=True)
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0 if audit["source_reconciliation_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
