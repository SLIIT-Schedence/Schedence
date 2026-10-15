"""Import approved PP1 allocations and run the entire configured demand once."""

import argparse
import json
from pathlib import Path

from schedence_teaching.solver import SolverInputError

from .__main__ import LOCAL_DATA, SERVICE_ROOT, load_reference_sources
from .allocations import import_specialization_allocations
from .pp1 import PP1Configuration, prepare_pp1
from .pp1_run import run_pp1
from .students import import_student_counts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-data", type=Path, default=LOCAL_DATA)
    parser.add_argument("--config", type=Path, default=SERVICE_ROOT / "examples" / "pp1-test-calendar.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if not args.output.resolve().is_relative_to(LOCAL_DATA.resolve()):
            raise ValueError("Outputs must stay inside ignored Teaching Service local-data.")
        if args.output.exists() and any(args.output.iterdir()):
            raise FileExistsError("Use a new output directory; existing outputs are preserved.")
        original = import_student_counts(args.local_data / "Schedence_Student_Groups_One_Sheet_Editable.xlsx")
        allocations = import_specialization_allocations(args.local_data / "Schedence_Specialization_Student_Counts_Proportional_Editable.xlsx", original)
        if not allocations.valid:
            print(json.dumps(allocations.summary(), indent=2))
            return 2
        academic, venues = load_reference_sources(args.local_data)
        config = PP1Configuration.model_validate_json(args.config.read_text(encoding="utf-8-sig"))
        batch = prepare_pp1(allocations, academic, venues, config)
        result, report = run_pp1(batch, args.output)
        print(json.dumps({key: report[key] for key in ("output_label", "solver_status", "scheduled_sessions", "unscheduled_sessions",
            "complete_timetable_generated", "clash_validation", "independent_attendance_capacity_status", "diagnostic_witness_status",
            "source_module_rows", "scoped_local_offering_references", "session_type_counts")}
            | dict(groups=batch.plan.summary(), time_capacity_failure_count=len(report["student_capacity_failures"]), output=str(args.output)), indent=2))
        return 0 if report["complete_timetable_generated"] else 1
    except SolverInputError as error:
        print(json.dumps(dict(status="INVALID_SCHEDULING_CONFIGURATION", explanation=str(error))))
        return 2
    except (OSError, ValueError, KeyError, TypeError) as error:
        # Do not echo raw Pydantic inputs or personal source values.
        print(json.dumps(dict(status="ERROR", explanation="PP1 input/preparation/export failed. Check the source imports, explicit configuration and a new writable output directory. Source workbooks were not modified.", error_type=type(error).__name__)))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
