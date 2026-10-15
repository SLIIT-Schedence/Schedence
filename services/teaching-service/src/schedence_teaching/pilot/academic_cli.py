"""Extract actual university-sourced modules and specialization demand drafts."""

import argparse
import csv
from dataclasses import asdict
import json
from pathlib import Path

from schedence_teaching.excel_import import WideImportConfig, import_wide_excel

from .academic import CONTACT_HOUR_COLUMNS, extract_academic_catalog
from .__main__ import LOCAL_DATA, find_student_workbook
from .preview import _new_directory
from .students import generate_groups, import_student_counts


def write_academic_report(academic, student_counts, directory, *, specialization_starts=()):
    """Drafts have source-row identities, never invented offering IDs or counts."""
    catalog = extract_academic_catalog(academic, specialization_starts=specialization_starts)
    if not catalog.valid or not student_counts.valid:
        raise ValueError("Academic catalog and student counts must pass validation before export.")
    plan = generate_groups(student_counts.cohorts, student_counts.sizing)
    module_rows, drafts = [], []
    for module in catalog.modules:
        location = asdict(module.source.location)
        module_rows.append(dict(**location, **module.source.contract_fields,
            **{column: module.source.source_values[column] for column in CONTACT_HOUR_COLUMNS.values()},
            applicable_specializations=";".join(module.specializations)))
        for specialization in module.specializations:
            for kind, minutes in module.weekly_minutes.items():
                if minutes:
                    lecture = kind.value == "LECTURE"
                    drafts.append(dict(**location, source_identity=module.source.location.diagnostic_key,
                        offering_id="", group_id="", student_count="", stream="",
                        module_code=module.source.contract_fields["module_code"],
                        module_name=module.source.contract_fields["module_name"],
                        academic_year=module.source.contract_fields["academic_year"],
                        semester=module.source.contract_fields["semester"], specialization=specialization,
                        source_specialization=module.source.contract_fields["specialization"],
                        session_type=kind.value, weekly_contact_minutes=minutes,
                        duration_minutes=module.lecture_duration_minutes if lecture else "",
                        sessions_per_week=1 if lecture else "", status="DRAFT_REQUIRES_OFFERING_AND_GROUP_MAPPING"))
    missing = ["Actual offering IDs and stream applicability for each selected source row/specialization",
        "Student group-to-specialization mapping or specialization population counts; supplied cohort totals cover all specializations",
        "Explicit group audiences and non-lecture duration/frequency patterns",
        "Dated teaching week/windows, staff availability and venue calendars/blocks/room relationships",
        "Scheduling policy applicability/weights, existing institution/staff integration identifiers, and population evidence"]
    report = dict(status="SOURCE_EXTRACTION_COMPLETE_SCHEDULING_INPUTS_INCOMPLETE", ready_for_scheduling=False,
        academic_catalog=catalog.to_dict(), student_counts=student_counts.summary(), planned_groups=plan.summary(),
        source_staff_count=len(academic.staff_members), academic_staff_count=len(academic.academic_staff),
        instructor_count=len(academic.instructors), lecturer_preference_rows=len(academic.source_preferences),
        teaching_team_assignments=len(academic.source_assignments), specialization_contact_drafts=len(drafts),
        duplicate_module_codes_preserved=True, specialization_counts_invented=False,
        teaching_assignments_changed=False, source_workbooks_modified=False,
        specialization_start_policy_required=False, module_applicability_used_directly=True,
        module_weekly_minutes_are_not_student_weighted=True, missing=missing,
        scheduling_approach="One global solve over all supplied specialization demands; views filter that single independently validated result.")
    directory = _new_directory(directory)
    for name, rows in (("module-catalog.csv", module_rows), ("specialization-demand-drafts.csv", drafts)):
        with (directory / name).open("x", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n") if rows else None
            if writer:
                writer.writeheader()
                writer.writerows(rows)
    (directory / "academic-readiness.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-data", type=Path, default=LOCAL_DATA)
    parser.add_argument("--student-workbook", type=Path)
    parser.add_argument("--specialization-starts", type=Path, help="JSON list of supplied SpecializationStart contracts")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if not args.output.resolve().is_relative_to(LOCAL_DATA.resolve()):
            raise ValueError("Outputs must remain inside ignored local-data.")
        source = import_wide_excel(WideImportConfig.model_validate_json(
            (args.local_data / "department-aligned.mapping.json").read_text(encoding="utf-8")), base_dir=args.local_data)
        counts = import_student_counts(args.student_workbook or find_student_workbook(args.local_data))
        starts = json.loads(args.specialization_starts.read_text(encoding="utf-8-sig")) if args.specialization_starts else ()
        report = write_academic_report(source, counts, args.output, specialization_starts=starts)
        print(json.dumps(dict(status=report["status"], data_status=report["academic_catalog"]["data_status"],
            module_rows=report["academic_catalog"]["module_rows"],
            lecture_duration_counts=report["academic_catalog"]["lecture_duration_counts"],
            specialization_contact_drafts=report["specialization_contact_drafts"],
            source_student_total=report["student_counts"]["total_students"], missing=report["missing"], output=str(args.output)), indent=2))
        return 0
    except (OSError, ValueError, TypeError, KeyError):
        print(json.dumps(dict(status="ERROR", explanation="Check source imports, optional starting mappings and a new writable local output directory; no source workbook was modified.")))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
