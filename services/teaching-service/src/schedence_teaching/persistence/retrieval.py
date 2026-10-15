"""Detached timetable retrieval and views; one row remains one actual booking."""

from collections import Counter, defaultdict
from dataclasses import dataclass

from schedence_teaching.preparation import build_group_hierarchy, prepare_timetable_input

from .lifecycle import academic_limitations, draft_status
from .ports import PersistenceConflict
from .versioning import METADATA_KEY, delivery_digest
from .snapshots import content_digest


@dataclass(frozen=True)
class TimetableRecord:
    document: object
    version: dict
    run: dict
    validation: dict
    deliveries: tuple[dict, ...]
    coverage: tuple[dict, ...]
    limitations: dict
    logical_status: str

    def lecturer_timetable(self, staff_id):
        return tuple(row for row in self.deliveries if row["staff_reference"] == staff_id)

    def venue_timetable(self, venue_id):
        if venue_id is None:
            raise ValueError("venue view requires an explicit physical venue reference")
        return tuple(row for row in self.deliveries if row["venue_reference"] == venue_id)

    def specialization_timetable(self, specialization):
        return tuple(row for row in self.deliveries
            if any(item["specialization"] == specialization for item in row["coverage"]))

    def student_group_timetable(self, group_id):
        hierarchy = build_group_hierarchy(self.document.teaching_input.student_groups)
        if group_id not in hierarchy.parents:
            raise PersistenceConflict("student group not found in this timetable scope")
        rosters = {row.group_id: set(row.student_ids) for row in self.document.solver_config.attendance_memberships}
        selected = {candidate for candidate in hierarchy.parents if hierarchy.conflicts(group_id, candidate)
            or bool(rosters.get(group_id, set()) & rosters.get(candidate, set()))}
        return tuple(row for row in self.deliveries
            if any(item["group_reference"] in selected for item in row["coverage"]))

    def coverage_report(self):
        return dict(expected_original_occurrences=self.run["expected_occurrences"],
            covered_original_occurrences=len(self.coverage), actual_deliveries=len(self.deliveries),
            unscheduled_occurrences=self.run["expected_occurrences"]-len(self.coverage),
            hard_violations=self.validation["hard_violations"], independently_validated=self.validation["passed"],
            academic_complete=False, limitations=self.limitations,
            session_types=dict(Counter(row["session_type"] for row in self.coverage)))


def assemble_timetable(document, version, run, report, deliveries, coverage):
    if not (run["solver_success"] and version["solver_success"] and version["validation_passed"] and report["passed"]
            and report["hard_violations"] == 0 and version["lifecycle_status"] == "DRAFT_VALIDATED"):
        raise PersistenceConflict("stored timetable lacks successful independent validation")
    expected = {row.occurrence_id: row for row in prepare_timetable_input(document.teaching_input).sessions}
    covered = {row["occurrence_reference"] for row in coverage}
    if len(covered) != len(coverage) or covered != expected.keys() or not all(
        count == len(expected) for count in (run["expected_occurrences"], run["scheduled_occurrences"], report["expected_occurrences"], report["checked_occurrences"])):
        raise PersistenceConflict("stored original occurrence coverage is incomplete or duplicated")
    grouped = defaultdict(list)
    groups = {row.group_id: row for row in document.teaching_input.student_groups}
    for item in coverage:
        session = expected[item["occurrence_reference"]]
        group = groups[session.group_id]
        fields = dict(requirement_reference=session.requirement_id, offering_reference=session.offering_id,
            group_reference=session.group_id, session_type=session.session_type.value, duration_minutes=session.duration_minutes,
            academic_year=group.academic_year, semester=group.semester, stream=group.stream.value,
            specialization=document.group_specializations[group.group_id])
        if any(item[key] != value for key, value in fields.items()):
            raise PersistenceConflict("stored occurrence metadata differs from its validated snapshot")
        grouped[item["delivery_id"]].append(fields | dict(occurrence_reference=session.occurrence_id))
    bookings = []
    for row in deliveries:
        items = grouped.pop(row["id"], [])
        if not items:
            raise PersistenceConflict("stored delivery has no original occurrence coverage")
        booking = {key: value for key, value in row.items() if key not in {"id", "institution_id", "version_id", "created_at"}}
        booking["coverage"] = items
        bookings.append(booking)
    if grouped:
        raise PersistenceConflict("stored coverage references a missing delivery")
    metadata = run["result_metadata"].get(METADATA_KEY, {})
    if metadata and metadata.get("normalized_deliveries_sha256") != delivery_digest(bookings):
        raise PersistenceConflict("stored delivery digest differs from its validated write plan")
    if metadata and report["evidence"].get(METADATA_KEY) != metadata:
        raise PersistenceConflict("stored run and validation provenance differ")
    if "snapshot_sha256" in metadata and metadata["snapshot_sha256"] != content_digest(document):
        raise PersistenceConflict("stored validation refers to a different input snapshot")
    from .policy import verify_version_policy_metadata
    try:
        verify_version_policy_metadata(document, metadata)
    except ValueError as error:
        raise PersistenceConflict(str(error)) from None
    logical_status = draft_status(metadata.get("logical_status", "VALIDATED")).value
    return TimetableRecord(document, version, run, report, tuple(bookings), tuple(coverage),
        academic_limitations(document), logical_status)
