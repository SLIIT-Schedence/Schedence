"""Reuse an immutable reviewed registration snapshot for scenario comparisons."""

from collections import Counter
from dataclasses import dataclass
import csv
import json
from pathlib import Path

from .__main__ import LOCAL_DATA, load_reference_sources
from .allocations import import_specialization_allocations
from .elective_config import ElectiveRunConfiguration
from .elective_io import read_classifications, read_selections
from .electives import prepare_enrolled_pp1
from .pp1 import PP1Configuration, prepare_pp1
from .research_requirements import apply_research_requirements
from .scopes import select_complete_scope
from .students import import_student_counts


@dataclass(frozen=True)
class RegisteredSnapshot:
    enrolled: object
    blocked_scopes: tuple[dict, ...]
    original_cohorts: tuple
    reference: str


def load_registered_snapshot(directory):
    """Fail on source/demand drift instead of silently changing registrations.

    No regeneration of random registrations occurs. The source workbooks and
    earlier report directory are read only. Source IDs remain local PP1 IDs.
    """
    directory = Path(directory).resolve()
    if not directory.is_relative_to(LOCAL_DATA.resolve()):
        raise ValueError("Registration snapshots must remain in ignored local-data.")
    settings = ElectiveRunConfiguration.model_validate_json(
        (directory / "configuration.json").read_text(encoding="utf-8-sig"))
    configuration = PP1Configuration.model_validate_json(
        (directory / "hybrid-calendar.json").read_text(encoding="utf-8-sig"))
    original = import_student_counts(LOCAL_DATA / "Schedence_Student_Groups_One_Sheet_Editable.xlsx")
    allocations = import_specialization_allocations(
        LOCAL_DATA / "Schedence_Specialization_Student_Counts_Proportional_Editable.xlsx", original)
    academic, venues = load_reference_sources(LOCAL_DATA)
    base = prepare_pp1(allocations, academic, venues, configuration)
    summary = json.loads((directory / "run-summary.json").read_text(encoding="utf-8"))
    blocked = tuple(summary["blocked_scopes"])
    blocked_ids = {row["cohort_id"] for row in blocked}
    keys = {(row.academic_year, row.semester, row.stream.value, row.specialization)
            for row in base.plan.cohorts if row.cohort_id not in blocked_ids}
    supported = select_complete_scope(base, keys, retain_applicable_catalog=True)
    registrations = read_selections(directory / "enrollment/student-elective-allocations.csv",
                                    base.plan.cohorts, label=summary["allocation_label"])
    enrolled = prepare_enrolled_pp1(supported, academic,
        read_classifications(directory / "module-classifications.csv"), registrations,
        policy=settings.allocation_policy, allocation_label=summary["allocation_label"])
    enrolled = apply_research_requirements(enrolled, base, settings.research)
    with (directory / "04-all-supported-pp1/session-demand.csv").open(encoding="utf-8", newline="") as handle:
        saved = tuple(csv.DictReader(handle))
    expected = Counter((row["occurrence_id"], row["requirement_id"], row["offering_id"], row["group_id"],
                        row["session_type"], int(row["duration_minutes"])) for row in saved)
    actual = Counter((row.occurrence_id, row.requirement_id, row.offering_id, row.group_id,
                      row.session_type.value, row.duration_minutes) for row in enrolled.batch.prepared.sessions)
    if actual != expected:
        raise ValueError("Reviewed registration demand changed; no comparable scenario was generated.")
    if summary["year4_registered_students"] != len(registrations):
        raise ValueError("Reviewed registration count changed.")
    return RegisteredSnapshot(enrolled, blocked, tuple(base.plan.cohorts), directory.name)
