"""Corrected academic-track snapshots through the database adapter; database-shaped synthetic rows only."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from schedence_teaching.persistence.database_read import DatabaseEvidence
from schedence_teaching.persistence.generation_inputs import academic_track_cohorts, reconstruct_enrollment
from schedence_teaching.persistence.ports import PersistenceConflict
from schedence_teaching.pilot.academic_pathways import prepare_academic_grouping
from schedence_teaching.pilot.allocations import SpecializationAllocations
from schedence_teaching.pilot.students import GroupSizingPolicy

from test_academic_demand import source
from test_academic_pathways import inputs
from test_database_generation import database_fixture  # noqa: F401  (existing sanitized SQL-row fixture)
from test_elective_enrollment import elective_fixture  # noqa: F401

SHA = "b" * 64


def track_case():
    """Two source-identical populations pooled into one recorded track, as the corrected snapshots record it."""
    allocations, catalog, configuration = inputs()
    academic = source(lab=2)
    for module in academic.source_modules:
        module.contract_fields["specialization"] = "SYN-A, SYN-B"
    allocations = SpecializationAllocations(cohorts=allocations.cohorts, sizing=GroupSizingPolicy(), source_sha256=SHA)
    grouping = prepare_academic_grouping(allocations, catalog, configuration)
    cohorts = {row.cohort_id: row for row in allocations.cohorts}
    dataset = uuid4()
    evidence = SimpleNamespace(academic=dict(source_datasets=[dict(id=dataset, sha256=SHA)],
        pp1_specialization_allocations=[dict(source_id=dataset) for _ in cohorts]))
    groups = {}
    for group in grouping.plan.groups:
        groups.setdefault(grouping.plan.cohort_by_group[group.group_id], []).append(group)
    report = SimpleNamespace(provenance=dict(academic_grouping=grouping.report | dict(
        configuration=configuration.model_dump(mode="json"))))
    document = SimpleNamespace(sources=(SimpleNamespace(provenance={}), report),
        scopes=tuple(SimpleNamespace(scope_key=key, group_references=tuple(g.group_id for g in members)) for key, members in groups.items()),
        teaching_input=SimpleNamespace(student_groups=tuple(grouping.plan.groups)))
    return evidence, document, academic, cohorts, grouping


def derive(evidence, document, academic, cohorts):
    return academic_track_cohorts(evidence, document, academic, cohorts, GroupSizingPolicy())


def test_recorded_tracks_are_rederived_from_stored_per_specialization_allocations():
    evidence, document, academic, cohorts, grouping = track_case()
    tracks = derive(evidence, document, academic, cohorts)
    assert set(tracks) == {"PP1:ACADEMIC-TRACK:Y1S1:WEEKDAY:FOUNDATION", "PP1:ACADEMIC-TRACK:Y1S1:WEEKEND:FOUNDATION"}
    assert {key: row.student_count for key, row in tracks.items()} == {
        "PP1:ACADEMIC-TRACK:Y1S1:WEEKDAY:FOUNDATION": 250, "PP1:ACADEMIC-TRACK:Y1S1:WEEKEND:FOUNDATION": 55}
    assert all(row.specialization == "FOUNDATION" for row in tracks.values())
    assert set(cohorts) == {"SYN-WEEKDAY-SYN-A", "SYN-WEEKDAY-SYN-B", "SYN-WEEKEND-SYN-A", "SYN-WEEKEND-SYN-B"}  # Not mutated.


def test_snapshots_without_recorded_grouping_keep_historical_behaviour():
    evidence, document, academic, cohorts, _ = track_case()
    document.sources = document.sources[:1]
    assert derive(evidence, document, academic, cohorts) == {}


def test_a_changed_planning_group_is_rejected():
    evidence, document, academic, cohorts, _ = track_case()
    first = document.teaching_input.student_groups[0]
    document.teaching_input.student_groups = (first.model_copy(update={"student_count": first.student_count - 1}),
                                              *document.teaching_input.student_groups[1:])
    with pytest.raises(PersistenceConflict, match="planning group differs"):
        derive(evidence, document, academic, cohorts)


def test_changed_allocation_slices_are_rejected():
    evidence, document, academic, cohorts, _ = track_case()
    slices = document.sources[1].provenance["academic_grouping"]["group_allocation_slices"]
    key = next(iter(slices))
    slices[key] = tuple(dict(row, source_first_offset=row["source_first_offset"] + 1) for row in slices[key])
    with pytest.raises(PersistenceConflict, match="allocation slices differ"):
        derive(evidence, document, academic, cohorts)


def test_a_scope_with_missing_or_extra_groups_is_rejected():
    evidence, document, academic, cohorts, _ = track_case()
    scope = document.scopes[0]
    document.scopes = (SimpleNamespace(scope_key=scope.scope_key, group_references=scope.group_references[1:]), *document.scopes[1:])
    with pytest.raises(PersistenceConflict, match="scope groups differ"):
        derive(evidence, document, academic, cohorts)


def test_stored_allocations_must_match_the_recorded_rows_and_source():
    evidence, document, academic, cohorts, _ = track_case()
    changed = dict(cohorts)
    key = "SYN-WEEKDAY-SYN-A"
    changed[key] = cohorts[key].model_copy(update={"student_count": cohorts[key].student_count + 1})
    with pytest.raises(PersistenceConflict, match="per-specialization allocations"):
        derive(evidence, document, academic, changed)
    evidence.academic["source_datasets"][0]["sha256"] = "c" * 64
    with pytest.raises(PersistenceConflict, match="another allocation source"):
        derive(evidence, document, academic, cohorts)


def test_a_curriculum_difference_cannot_be_pooled():
    evidence, document, academic, cohorts, _ = track_case()
    academic.source_modules[0].contract_fields["specialization"] = "SYN-A"  # Stored rows no longer source-identical.
    with pytest.raises(ValueError, match="identical nonempty source-row curricula"):
        derive(evidence, document, academic, cohorts)


def test_two_recorded_groupings_are_ambiguous():
    evidence, document, academic, cohorts, _ = track_case()
    document.sources = (*document.sources, document.sources[1])
    with pytest.raises(PersistenceConflict, match="ambiguous"):
        derive(evidence, document, academic, cohorts)


def evidence_with(evidence, mutate):
    raw = evidence.document.model_dump(round_trip=True)
    mutate(raw["solver_config"])
    document = type(evidence.document).model_validate(raw)
    return DatabaseEvidence(evidence.institution_id, evidence.snapshot_id, document, evidence.academic, evidence.resource,
                            evidence.teaching)


def test_operational_search_settings_do_not_block_reconstruction(database_fixture):
    evidence, original = database_fixture
    changed = evidence_with(evidence, lambda config: config.update(max_time_seconds=360.0, num_search_workers=8, random_seed=7))
    assert reconstruct_enrollment(changed).batch.prepared == original.batch.prepared


def test_a_changed_sealed_rule_is_still_rejected(database_fixture):
    evidence, _ = database_fixture

    def shorten(config):
        window = next(iter(config["days"][0]["windows"].values()))[0]
        window["end_minute"] -= 30
    with pytest.raises(PersistenceConflict, match="rules differ"):
        reconstruct_enrollment(evidence_with(evidence, shorten))


BLOCKED = dict(cohort_id="SYN-Y4-WD-BLOCKED", academic_year=4, semester=1, stream="WEEKDAY", specialization="SYN-BLOCKED",
               student_count=42, status="BLOCKED_INPUT", reason="SYNTHETIC_MISSING_APPLICABLE_MODULE_MAPPINGS",
               student_counts_preserved=True)


def blocked_capture(elective_fixture, rows):
    from schedence_teaching.persistence.pp1 import capture_pp1_snapshot
    from test_persistence_contracts import TENANT
    from test_semester_scenarios import snapshot
    enrolled = snapshot(elective_fixture).enrolled
    policy = elective_fixture[3]
    return capture_pp1_snapshot(enrolled, institution_id=TENANT, sources=(), blocked_scopes=rows,
        academic_term_reference="SYNTHETIC_BLOCKED_SCOPE_REFERENCE", timezone_name="Asia/Colombo",
        registration_policy=policy.model_dump(mode="json"))


def test_blocked_scopes_are_handed_to_capture_as_their_recorded_rows(elective_fixture):
    from schedence_teaching.persistence.generation_inputs import blocked_scope_rows
    document = blocked_capture(elective_fixture, (BLOCKED,))
    blocked = [row for row in document.scopes if row.scope_status != "SUPPORTED"]
    rows = blocked_scope_rows(document, blocked)
    assert rows == (BLOCKED,)
    again = blocked_capture(elective_fixture, rows)  # Re-capture reproduces the same blocked scope and issues.
    assert again.scopes == document.scopes and again.issues == document.issues


def test_a_blocked_scope_without_its_matching_issue_is_rejected(elective_fixture):
    from schedence_teaching.persistence.generation_inputs import blocked_scope_rows
    document = blocked_capture(elective_fixture, (BLOCKED,))
    blocked = [row for row in document.scopes if row.scope_status != "SUPPORTED"]
    changed = [row.model_copy(update={"student_count": 41}) for row in blocked]
    with pytest.raises(PersistenceConflict, match="no matching recorded academic-mapping issue"):
        blocked_scope_rows(document, changed)
    stripped = document.model_copy(update={"issues": tuple(row for row in document.issues
                                                           if row.issue_code != "BLOCKED_MISSING_ACADEMIC_MAPPING")})
    with pytest.raises(PersistenceConflict, match="no matching recorded academic-mapping issue"):
        blocked_scope_rows(stripped, blocked)
