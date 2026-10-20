"""Generate an explicitly labelled local pilot; actual-campus mode is the default."""

import argparse
import json
from pathlib import Path
from uuid import UUID

from schedence_teaching.excel_import import WideImportConfig, VenueImportConfig, import_wide_excel, import_venue_excel
from schedence_teaching.integration import SchedulingSubmission

from .demo import DEMO_ASSUMPTIONS, DEMO_INSTITUTION, build_demo_submission
from .preview import PilotMode, _new_directory, run_pilot_preview
from .students import generate_groups, import_student_counts


SERVICE_ROOT = Path(__file__).resolve().parents[3]
LOCAL_DATA = SERVICE_ROOT / "local-data"
STUDENT_FILE = "Schedence_Student_Groups_One_Sheet_Editable.xlsx"


def find_student_workbook(local_data):
    expected = local_data / STUDENT_FILE
    if expected.is_file():
        return expected
    matches = list(local_data.rglob(STUDENT_FILE))
    if len(matches) != 1:
        raise ValueError("Supply --student-workbook when the expected file is missing or ambiguous.")
    return matches[0]


def load_reference_sources(local_data):
    academic = import_wide_excel(WideImportConfig.model_validate_json(
        (local_data / "department-aligned.mapping.json").read_text(encoding="utf-8")), base_dir=local_data)
    venues = import_venue_excel(VenueImportConfig.model_validate_json(
        (local_data / "venues.mapping.json").read_text(encoding="utf-8")), base_dir=local_data)
    if not academic.valid or not venues.valid:
        raise ValueError("Reference sources must pass the existing academic/venue import checks.")
    return academic, venues


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=[mode.value for mode in PilotMode], default="REAL_CAMPUS")
    parser.add_argument("--local-data", type=Path, default=LOCAL_DATA)
    parser.add_argument("--student-workbook", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--base-submission", type=Path)
    parser.add_argument("--institution-id", type=UUID)
    parser.add_argument("--time-limit", type=float, default=15.0)
    args = parser.parse_args(argv)
    try:
        mode = PilotMode(args.mode)
        directory = args.output or LOCAL_DATA / "pilot-previews" / f"step12a2-{mode.value.lower()}"
        if not directory.resolve().is_relative_to(LOCAL_DATA.resolve()):
            raise ValueError("Preview outputs must remain inside the Git-ignored Teaching Service local-data folder.")
        source_path = args.student_workbook or find_student_workbook(args.local_data)
        source = import_student_counts(source_path)
        if not source.valid:
            print(json.dumps(dict(status="INVALID_STUDENT_INPUT", source=source.summary()), indent=2))
            return 2
        all_groups = generate_groups(source.cohorts, source.sizing)
        selected = [row for row in source.cohorts if (row.academic_year, row.semester, row.stream.value) == (1, 1, "WEEKDAY")]
        if not selected:
            raise ValueError("The first pilot requires the Year 1 semester 1 weekday cohort.")
        plan = generate_groups(selected, source.sizing)
        source_report = dict(workbook=source_path.name, imported=source.summary(), all_groups=all_groups.summary())
        if mode is PilotMode.DEMO:
            academic, venues = load_reference_sources(args.local_data)
            submission = build_demo_submission(plan, academic, venues, time_limit=args.time_limit)
            institution = DEMO_INSTITUTION
            assumptions = DEMO_ASSUMPTIONS
        elif args.base_submission is None or args.institution_id is None:
            directory = _new_directory(directory)
            report = dict(requested_mode="REAL_CAMPUS", status="BLOCKED", ready_for_generation=False,
                ready_for_actual_campus=False, university_ready=False, source=source_report, pilot_group_plan=plan.summary(),
                missing=["Existing authorized institution UUID", "Reviewed complete Step 12A SchedulingSubmission with matching group plan, real specialization/population evidence, offerings, demand, calendars and approvals"],
                explanation="No real timetable or synthetic fallback was produced. Actual inputs must pass the existing Step 12A readiness gate.")
            path = directory / "readiness-assumptions.json"
            path.write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(json.dumps(dict(status="BLOCKED", pilot=plan.summary(), readiness_json=str(path)), indent=2))
            return 1
        else:
            submission = SchedulingSubmission.model_validate_json(args.base_submission.read_text(encoding="utf-8-sig"))
            institution = args.institution_id
            assumptions = ("CAMPUS_INPUT_VALIDATED means the supplied contracts and evidence passed checks; it is not independent official verification, booking or publication approval.",)
        output = run_pilot_preview(submission, plan, directory, institution_id=institution, mode=mode,
            source_report=source_report, assumptions=assumptions)
        print(json.dumps(dict(mode=mode.value, output_label=output.label, solver_status=output.solver_status,
            source_total_students=sum(row.student_count for row in source.cohorts), all_groups=all_groups.summary(),
            pilot=plan.summary(), independently_validated=output.result is not None and output.result.validation is not None,
            physical_deliveries=len(output.result.schedule) if output.result else 0,
            combined_deliveries=output.result.validation.combined_deliveries if output.result and output.result.validation else 0,
            paths=output.paths), indent=2))
        return 0 if output.label else 1
    except (OSError, ValueError, KeyError, TypeError):
        print(json.dumps(dict(status="ERROR", explanation="Pilot command failed: check student layout, reference imports, reviewed input contracts and a new writable local output directory. No original dataset was modified.")))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
