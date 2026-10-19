"""Capture existing PP1 imports and registrations without regenerating demand."""

import json
from uuid import UUID

from schedence_teaching.integration.contracts import SourceOwner
from schedence_teaching.solver import CombinationSolverConfig

from .contracts import IssueDocument, ScopeDocument, SchedulingContexts, SnapshotDocument, SourceDocument
from .snapshots import canonical_json, content_digest, replay_snapshot


def capture_pp1_snapshot(enrolled, *, institution_id: UUID, sources: tuple[SourceDocument, ...],
                         blocked_scopes: tuple[dict, ...], academic_term_reference: str,
                         timezone_name: str, registration_policy: dict, seal=None) -> SnapshotDocument:
    """Preserve source-local IDs and pending requirements; never issue master IDs."""
    batch = enrolled.batch
    if enrolled.allocation_label != "PP1_TEST_ELECTIVE_ALLOCATION":
        raise ValueError("this PP1 adapter must not relabel supplied university individual registrations as synthetic")
    if batch.config.attendance_memberships != enrolled.group_memberships:
        raise ValueError("PP1 scheduling rosters differ from the preserved registration memberships")
    inputs = batch.solver_inputs
    raw = {key: value for key, value in inputs.items() if key in SchedulingContexts.model_fields
           and key not in {"combination_input", "preference_input"}}
    if inputs.get("combination_input") is not None:
        raw["combination_input"] = inputs["combination_input"].input_snapshot
        raw.setdefault("combination_config", CombinationSolverConfig())
    if inputs.get("preference_input") is not None:
        raw["preference_input"] = inputs["preference_input"].input_snapshot
    if inputs.get("work_periods") is not None:
        raw["work_period_input"] = inputs["work_periods"].input_snapshot
    groups = batch.prepared.input_snapshot.student_groups
    group_ids = {row.group_id for row in groups}
    scopes = []
    for cohort in batch.plan.cohorts:
        references = tuple(row.group_id for row in groups if batch.plan.cohort_by_group[row.group_id] == cohort.cohort_id)
        if not references:
            raise ValueError("supported cohort has no original groups; no empty scope was fabricated")
        scopes.append(ScopeDocument(scope_key=cohort.cohort_id, academic_year=cohort.academic_year,
            semester=cohort.semester, stream=cohort.stream, specialization=cohort.specialization,
            student_count=cohort.student_count, scope_status="SUPPORTED", group_references=references))
    issues = []
    included_semesters = {row.semester for row in batch.plan.cohorts}
    selected_blocks = [row for row in blocked_scopes if row["semester"] in included_semesters]
    for row in selected_blocks:
        scopes.append(ScopeDocument(scope_key=row["cohort_id"], academic_year=row["academic_year"],
            semester=row["semester"], stream=row["stream"], specialization=row["specialization"],
            student_count=row["student_count"], scope_status="BLOCKED_MISSING_ACADEMIC_MAPPING"))
        issues.append(IssueDocument(issue_code="BLOCKED_MISSING_ACADEMIC_MAPPING", severity="BLOCKER",
            scope_key=row["cohort_id"], details=json.loads(canonical_json(row))))
    if enrolled.research_pending_cohort_ids or any(row["academic_year"] == 4 for row in selected_blocks):
        issues.append(IssueDocument(issue_code="RESEARCH_PENDING_SESSION_MAPPING", severity="BLOCKER",
            details={"academic_requirement": "COMPULSORY_RESEARCH_PENDING_SESSION_MAPPING",
                "compulsory": True, "missing_session_mapping_does_not_mean_zero_work": True,
                "supported_pending_cohort_ids": sorted(enrolled.research_pending_cohort_ids),
                "blocked_year4_cohort_ids": [row["cohort_id"] for row in selected_blocks if row["academic_year"] == 4]}))
    registration_evidence = dict(
        dataset_category="PP1_SYNTHETIC_ELECTIVES", university_individual_registrations=False,
        allocation_label=enrolled.allocation_label, policy=registration_policy,
        selections=[row.model_dump(mode="json") for row in enrolled.selections],
        classifications=[row.model_dump(mode="json") for row in enrolled.classifications])
    calendar_evidence = dict(dataset_category=batch.configuration.output_label,
        separate_semester_alternatives_not_jointly_conflict_free=True,
        university_calendar_approved=False, configuration=batch.configuration.model_dump(mode="json"))
    metadata_evidence = json.loads(canonical_json(dict(metadata=batch.metadata,
        source_offering_bindings=batch.source_offerings,
        local_references_are_not_authoritative_master_ids=True,
        offering_module_code_is_not_unique=True)))
    additional_sources = tuple(SourceDocument(label=label, sha256=content_digest(evidence),
        owner=SourceOwner.TEACHING, provenance_kind=kind, provenance=evidence) for label, kind, evidence in (
            ("PP1:synthetic-elective-registration-evidence", "SYNTHETIC_PP1", registration_evidence),
            ("PP1:calendar-configuration", "EXPERIMENTAL_CONFIGURATION" if
                batch.configuration.output_label == "PP1_EXPERIMENTAL_CALENDAR" else "SERVICE_EXPORT", calendar_evidence),
            ("PP1:preparation-source-bindings", "SERVICE_EXPORT", metadata_evidence)))
    document = SnapshotDocument(institution_id=institution_id, academic_term_reference=academic_term_reference,
        timezone_name=timezone_name, calendar_label=batch.configuration.output_label,
        calendar=batch.configuration.calendar, teaching_input=batch.prepared.input_snapshot,
        solver_config=batch.config, contexts=raw,
        group_specializations={key: value for key, value in batch.group_specializations.items() if key in group_ids},
        scopes=tuple(scopes), sources=sources + additional_sources, issues=tuple(issues))
    # Existing independent input checks, no CP-SAT execution or database access. A sealed
    # institution policy (for example batch online days) is validated by its sealing step,
    # which replays the bound document; nothing is solved or written here.
    return seal(document) if seal is not None else replay_snapshot(document)[0]
