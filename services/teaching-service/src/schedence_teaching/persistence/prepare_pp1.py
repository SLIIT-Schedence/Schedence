"""Prepare private PP1 database input documents; this command never connects."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
from uuid import UUID

from schedence_teaching.pilot.__main__ import LOCAL_DATA, SERVICE_ROOT
from schedence_teaching.pilot.calendar_experiment import CalendarExperiment, extend_weekend_online_calendar, _select
from schedence_teaching.pilot.elective_config import ElectiveRunConfiguration
from schedence_teaching.pilot.semester_inputs import load_registered_snapshot

from .application import verify_source_checksums
from .contracts import SourceDocument
from .pp1 import capture_pp1_snapshot
from .snapshots import content_digest, replay_snapshot, snapshot_payload

WORKBOOKS = {
    "Schedence_Staff_Master_Department_Aligned.xlsx": ("ACADEMIC_DATA_SERVICE", "UNIVERSITY_SOURCED", "UNIVERSITY_SOURCE"),
    "Schedence_Lecturer_Preferences_Department_Aligned.xlsx": ("ACADEMIC_DATA_SERVICE", "UNIVERSITY_SOURCED", "UNIVERSITY_SOURCE"),
    "Schedence_Modules_Department_Aligned_Instructors_Unchanged.xlsx": ("ACADEMIC_DATA_SERVICE", "UNIVERSITY_SOURCED", "UNIVERSITY_SOURCE"),
    "Schedence_Student_Groups_One_Sheet_Editable.xlsx": ("ACADEMIC_DATA_SERVICE", "UNIVERSITY_SOURCED", "UNIVERSITY_SOURCE"),
    "Schedence_Specialization_Student_Counts_Proportional_Editable.xlsx": ("ACADEMIC_DATA_SERVICE", "APPROVED_PP1_ESTIMATE", "PP1_TEST_ALLOCATION"),
    "Schedence_Actual_Venue_List_Campus_Confirmed_Locations.xlsx": ("RESOURCE_SERVICE", "UNIVERSITY_SOURCED", "UNIVERSITY_SOURCE"),
    "Schedence_Actual_Venue_List_Sample_Capacities.xlsx": ("RESOURCE_SERVICE", "UNIVERSITY_SOURCED", "UNIVERSITY_SOURCE"),
}


def prepare_documents(*, institution_id, registration_directory, reviewed_directory, settings_path, timezone_name):
    previous = json.loads((reviewed_directory / "verification-summary.json").read_text(encoding="utf-8-sig"))
    expected = {row["name"]: row["sha256"] for row in previous["source_workbooks"]}
    if set(expected) != set(WORKBOOKS):
        raise ValueError("reviewed manifest must identify the seven existing workbooks exactly")
    verify_source_checksums(LOCAL_DATA, expected)
    settings = CalendarExperiment.model_validate_json(settings_path.read_text(encoding="utf-8-sig"))
    baseline = load_registered_snapshot(registration_directory)
    registered = extend_weekend_online_calendar(baseline, settings)
    registration_config = ElectiveRunConfiguration.model_validate_json(
        (registration_directory / "configuration.json").read_text(encoding="utf-8-sig"))
    sources = []
    for name, (owner, kind, category) in WORKBOOKS.items():
        provenance = dict(dataset_category=category, source_file=name,
            source_values_unchanged=True, verification_basis="PREVIOUSLY_ACCEPTED_USER_SUPPLIED_UNIVERSITY_AND_PP1_INPUTS",
            active_input="Sample_Capacities" not in name)
        if "Sample_Capacities" in name:
            provenance["source_role"] = "PRE_LOCATION_CORRECTION_BACKUP_ONLY_NOT_SECOND_ACTIVE_VENUE_INVENTORY"
        if category == "PP1_TEST_ALLOCATION":
            provenance.update(individual_student_enrollment_verified=False,
                original_aggregate_cohort_totals_preserved=True)
        if "Campus_Confirmed_Locations" in name:
            provenance["campus_confirmation"] = json.loads((LOCAL_DATA / "venues.campus-confirmation.json").read_text(encoding="utf-8"))
            provenance["location_corrections"] = json.loads((LOCAL_DATA / "venues.location-updates.json").read_text(encoding="utf-8"))
        sources.append(SourceDocument(label=name, sha256=expected[name], owner=owner,
                                      provenance_kind=kind, provenance=provenance))
    # Also bind the exact configurations/registrations used by the existing loader.
    evidence_paths = [registration_directory / name for name in (
        "configuration.json", "hybrid-calendar.json", "module-classifications.csv",
        "enrollment/student-elective-allocations.csv", "run-summary.json")]
    evidence_paths.append(settings_path)
    for index, path in enumerate(evidence_paths):
        sources.append(SourceDocument(label=f"PP1:reviewed-artifact:{index}:{path.name}",
            sha256=sha256(path.read_bytes()).hexdigest(), owner="TEACHING_SERVICE",
            provenance_kind="SERVICE_EXPORT", provenance={"artifact_name":path.name,
                "registration_snapshot_reference":baseline.reference}))
    documents = []
    for semester in (1, 2):
        enrolled = _select(registered, settings, semester)
        documents.append(capture_pp1_snapshot(enrolled, institution_id=institution_id, sources=tuple(sources),
            blocked_scopes=registered.blocked_scopes, timezone_name=timezone_name,
            academic_term_reference=f"PP1_TEST_REFERENCE_WEEK:{enrolled.batch.configuration.calendar.week_reference}:SEMESTER_{semester}",
            registration_policy=registration_config.allocation_policy.model_dump(mode="json")))
    verify_source_checksums(LOCAL_DATA, expected)
    return tuple(documents), expected


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--institution-id", type=UUID, required=True)
    parser.add_argument("--timezone", required=True)
    parser.add_argument("--registration-directory", type=Path, default=LOCAL_DATA / "pilot-previews/step12a33-resumed-reviewed")
    parser.add_argument("--reviewed-directory", type=Path, default=LOCAL_DATA / "pilot-previews/step12a35-experimental-calendar/reviewed-results")
    parser.add_argument("--settings", type=Path, default=SERVICE_ROOT / "examples/pp1-experimental-calendar.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    output = args.output.resolve()
    if not output.is_relative_to(LOCAL_DATA.resolve()) or output.exists():
        parser.error("use a new output directory inside ignored local-data")
    try:
        documents, checksums = prepare_documents(institution_id=args.institution_id,
            registration_directory=args.registration_directory, reviewed_directory=args.reviewed_directory,
            settings_path=args.settings, timezone_name=args.timezone)
        output.mkdir(parents=True)
        reports = []
        for index, document in enumerate(documents, 1):
            # Exact text envelope is private: it includes original staff/preference
            # input and synthetic enrollment evidence, never output to the terminal.
            with (output / f"semester-{index}.snapshot.json").open("x",encoding="utf-8") as handle:
                json.dump(snapshot_payload(document), handle, ensure_ascii=False)
            prepared = replay_snapshot(document)[1]
            reports.append(dict(semester=index, preparation_status="VALIDATED_NOT_IMPORTED",
                expected_occurrences=len(prepared.sessions), staff_records=len(document.teaching_input.staff_members),
                offering_records=len(document.teaching_input.module_offerings),
                teaching_team_entries=len(document.teaching_input.teaching_team_members),
                student_groups=len(document.teaching_input.student_groups),
                session_requirements=len(document.teaching_input.session_requirements),
                venues=len(document.contexts.venue_input.venues),
                supported_cohorts=sum(row.scope_status=="SUPPORTED" for row in document.scopes),
                blocked_students=sum(row.student_count or 0 for row in document.scopes if row.scope_status!="SUPPORTED"),
                pending_issue_codes=sorted({row.issue_code for row in document.issues}),
                semantic_input_sha256=content_digest(document), calendar_label=document.calendar_label))
        audit=dict(database_writes_performed=False, source_workbooks_verified=len(checksums),
            source_checksums=checksums, snapshots=reports, timetable_results_modified=False,
            semesters_are_separate_alternatives=True)
        with (output / "preparation-audit.json").open("x",encoding="utf-8") as handle: json.dump(audit,handle,indent=2)
        print(json.dumps(dict(status="PREPARED_NOT_IMPORTED",source_workbooks_verified=len(checksums),snapshots=reports),indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(json.dumps(dict(status="PREPARATION_FAILED",safe_error_type=type(error).__name__,database_writes_performed=False)))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
