"""P3.2: combined optimization through the existing database-backed generation workflow.

Database-shaped synthetic owner rows only; nothing connects to Neon and no workbook is opened.
"""

from dataclasses import replace
from uuid import uuid4

import pytest

from schedence_teaching.excel_import import VenueImportResult
from schedence_teaching.excel_import.staging import (SourceLocation, SourceModuleRecord, SourceRecord, SourceTeamAssignment,
                                                     WideImportResult)
from schedence_teaching.persistence.contracts import ExternalReference, SourceDocument
from schedence_teaching.persistence.database_read import DatabaseEvidence, OWNER_TABLES
from schedence_teaching.persistence.policy import resolve_snapshot_policy
from schedence_teaching.persistence.pp1 import capture_pp1_snapshot
from schedence_teaching.pilot.academic import extract_academic_catalog
from schedence_teaching.pilot.allocations import SpecializationAllocations
from schedence_teaching.pilot.electives import ElectiveAllocationPolicy, prepare_enrolled_pp1
from schedence_teaching.pilot.pp1 import PP1Configuration, prepare_pp1
from schedence_teaching.pilot.students import CohortCount, GroupSizingPolicy
from schedence_teaching.schemas import StaffMember, TeachingRole, Venue
from schedence_teaching.solver import SolverConfig
from schedence_teaching.workload.teaching_presets import initial_university_assignment_policy

from test_institution_policy import revision
from test_lecturer_preference_settings import INSTITUTION_A, preference_row
from test_persistence_contracts import TENANT
from test_pp1_allocations import configuration
from test_teaching_read_api import api  # noqa: F401  (authenticated HTTP fixture)

REFERENCE = "SYNTHETIC:INSTITUTION-POLICY"  # The sealing revision's policy reference.
PEAK = "DELIVERY_COST_THEN_PEAK_OVERAGE_TOTAL_OVERAGE_FAIRNESS_PREFERENCES"
PLAN = ["MAX_STUDENT_DAILY_MINUTES", "MAX_STAFF_DAILY_MINUTES", "THIN_TEACHING_DAYS", "CONSECUTIVE_PENALTY",
        "PREFERENCE_PENALTY", "AGGREGATE_TARGET_DEVIATION"]
WEEKDAYS = ("MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY")
LECTURER_ONE_CELLS = {"Monday_Morning": "Not Available", "Monday_Afternoon": "Not Available", "Tuesday_Morning": "Preferred"}
LECTURER_TWO_CELLS = {"Friday_Afternoon": "Not Preferred"}
WEEKDAY_CLOSED = {f"{day}_{part}": "Not Available" for day in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")
                  for part in ("Morning", "Afternoon")}


def product_configuration(*, combined=True):
    """An institution's PP1 configuration: P2.5 settings and, optionally, P2.1-P2.4 plus a P3.1 plan.

    Weekend Monday stays the weekend stream's online-only candidate; weekday Wednesday becomes a
    normal physical candidate weekday (P2.4 decision 5).
    """
    from schedence_teaching.pilot.online_calendar import configure_online_days
    base = configure_online_days(configuration(), weekday_online_day="WEDNESDAY", weekend_online_day="MONDAY",
                                 weekend_online_window=dict(start_minute=510, end_minute=1080))
    raw = base.model_dump(round_trip=True)
    raw["lecturer_preference_settings"] = INSTITUTION_A
    raw["solver_config"]["max_time_seconds"] = 60.0
    if combined:
        for row in raw["solver_config"]["delivery_calendar"]["days"]:
            if (row["day"], row["stream"]) == ("WEDNESDAY", "WEEKDAY"):
                row.update(mode="PHYSICAL", allowed_session_types={"LECTURE", "LAB", "TUTORIAL", "WORKSHOP"})
        raw["solver_config"].update(
            batch_online_days=dict(policy_reference=REFERENCE, batch_key="ACADEMIC_YEAR_SEMESTER_STREAM",
                candidate_days={"WEEKDAY": set(WEEKDAYS), "WEEKEND": {"MONDAY"}}, selected_day_online_lectures_only=True,
                require_online_lecture_on_selected_day=True),
            teaching_day_compactness=dict(policy_reference=REFERENCE, preferred_minimum_daily_teaching_minutes=120,
                minimum_reference="SYNTHETIC:TEST-ONLY-120", placement="BEFORE_AGGREGATE_TARGET_DEVIATION"),
            daily_load_distribution=dict(policy_reference=REFERENCE, criteria=["MAX_STUDENT_DAILY_MINUTES", "MAX_STAFF_DAILY_MINUTES"],
                placement="BEFORE_AGGREGATE_TARGET_DEVIATION", relative_to_compactness="DAILY_LOAD_THEN_COMPACTNESS"),
            objective_stage_plan=dict(plan_reference=REFERENCE, order=PLAN))
    return PP1Configuration.model_validate(raw)


def academic_source(cells=None):
    """The existing PP1 elective source (Year 4 electives + Year 1), two more Year 1 modules and stored preference rows."""
    from schedence_teaching.pilot.electives import ModuleClassification
    academic = WideImportResult(source_confirmation_reference="synthetic:source", lecture_hours_interpretation="ONE_CONTINUOUS_WEEKLY_LECTURE")
    academic.staff_members = [StaffMember(staff_id=f"SYN-LEC-{n}", staff_name="Synthetic Name", position="Lecturer",
                                          staff_type="ACADEMIC") for n in (1, 2)]
    classifications = []
    for row, (year, lecture, tutorial, lab, kind) in enumerate(((4,2,0,2,"ELECTIVE"), (4,2,1,0,"ELECTIVE"), (4,2,0,0,"ELECTIVE"),
            (4,1,0,0,"COMPULSORY"), (4,0,0,0,"PROJECT"), (1,2,0,0,"COMPULSORY"), (1,2,1,0,"COMPULSORY"), (1,2,0,0,"COMPULSORY")), 2):
        location = SourceLocation("synthetic-academic.xlsx", "Modules_183", row)
        academic.source_modules.append(SourceModuleRecord(location, dict(Lecture_Hours_Week=lecture, Tutorial_Hours_Week=tutorial,
            Workshop_Hours_Week=0, Lab_Hours_Week=lab), contract_fields=dict(module_code=f"SYN{row}", module_name=f"Synthetic module {row}",
                academic_year=year, semester=1, specialization="IT", degree_type="Synthetic")))
        for staff in academic.staff_members:
            academic.source_assignments.append(SourceTeamAssignment(location, "LIC_Staff_ID", "LIC", staff.staff_id, "Synthetic",
                                                                    TeachingRole.LIC))
        if year == 4:
            classifications.append(ModuleClassification(source=location, specialization="IT", kind=kind,
                                                        reference="synthetic:explicit-classification"))
    academic.source_preferences = [
        replace(preference_row("SYN-LEC-1", cells or LECTURER_ONE_CELLS, row=5),
                location=SourceLocation("synthetic-preferences.xlsx", "Lecturer_Preferences", 5)),
        replace(preference_row("SYN-LEC-2", cells or LECTURER_TWO_CELLS, row=6),
                location=SourceLocation("synthetic-preferences.xlsx", "Lecturer_Preferences", 6))]
    return academic, classifications


def enrolled_batch(config, cells=None):
    from schedence_teaching.pilot.electives import generate_test_electives
    from schedence_teaching.pilot.research_requirements import ResearchRequirementInput, apply_research_requirements
    academic, classifications = academic_source(cells)
    allocations = SpecializationAllocations(sizing=GroupSizingPolicy(), specialization_names={"IT": "Synthetic IT"})
    for year, stream, count in ((4, "WEEKDAY", 131), (4, "WEEKEND", 131), (1, "WEEKDAY", 65)):
        allocations.cohorts.append(CohortCount(cohort_id=f"SYN-Y{year}-{stream}", academic_year=year, semester=1, stream=stream,
            student_count=count, specialization="IT", source=SourceLocation("synthetic-counts.xlsx", "allocations",
                                                                             len(allocations.cohorts) + 2)))
    venues = VenueImportResult()
    for identity, kind, size in (("SYN-HALL", "Lecture Hall", 120), ("SYN-LAB", "Computer Lab", 60), ("SYN-ROOM", "Lecture Hall", 120)):
        venues.venues.append(Venue(venue_id=identity, venue_name="Synthetic", venue_type=kind, capacity=size))
        venues.source_venues.append(SourceRecord(SourceLocation("synthetic-venues.xlsx", "venues", 2 + len(venues.venues)), {}))
    batch = prepare_pp1(allocations, academic, venues, config)
    policy = ElectiveAllocationPolicy(reference="synthetic:test-allocation", random_seed=17)
    enrolled = prepare_enrolled_pp1(batch, academic, classifications,
                                    generate_test_electives(batch, academic, classifications, policy), policy=policy)
    enrolled = apply_research_requirements(enrolled, batch, ResearchRequirementInput(policy_reference="synthetic:compulsory-research"))
    return enrolled, academic, policy, batch


def sealed_document(enrolled, policy, sources, *, combined=True, tenant=TENANT):
    """Capture through the existing PP1 adapter and seal at capture with the institution's approved revision."""
    from test_consecutive import scoring
    from test_fairness_solver import fairness, options as fairness_options

    def seal(document):
        raw = document.model_dump(round_trip=True)
        raw["contexts"]["staff_input"]["assignment_policy"] = initial_university_assignment_policy(policy_reference=REFERENCE,
            evidence_reference="SYNTHETIC:USER-POLICY", week_reference=document.calendar.week_reference,
            require_target_optimization=combined)
        if combined:
            week = document.calendar.week_reference
            raw["contexts"].update(fairness_policy=fairness(("SYN-LEC-1", "SYN-LEC-2"), week_reference=week),
                fairness_config=fairness_options(priority=PEAK), consecutive_policy=scoring(week_reference=week),
                consecutive_config=dict(priority="EXISTING_OBJECTIVES_THEN_CONSECUTIVE", priority_policy_reference=REFERENCE,
                                        formulation="TIME_INDEXED"))
        unsealed = type(document).model_validate(raw)
        return resolve_snapshot_policy(unsealed, revision(unsealed))

    return capture_pp1_snapshot(enrolled, institution_id=tenant, sources=sources, blocked_scopes=(),
        academic_term_reference="SYNTHETIC_PRODUCT_TERM", timezone_name="Asia/Colombo",
        registration_policy=policy.model_dump(mode="json"), seal=seal)


def database_evidence(document, academic, tenant=TENANT):
    """Owner rows shaped like the existing Neon tables, including academic.lecturer_preferences."""
    owners = {name: {table: [] for table in names} for name, names in OWNER_TABLES.items()}

    def add(schema, table, **values):
        row = dict(id=uuid4(), institution_id=tenant, **values)
        owners[schema][table].append(row)
        return row
    source_ids = {}
    for source in document.sources:
        owner = {"ACADEMIC_DATA_SERVICE": "academic", "RESOURCE_SERVICE": "resource"}.get(source.owner.value)
        if owner:
            source_ids[source.label] = add(owner, "source_datasets", filename=source.label, sha256=source.sha256,
                provenance_kind=source.provenance_kind, provenance=source.provenance)["id"]
    references = []
    for member in academic.staff_members:
        row = add("academic", "staff", staff_number=member.staff_id, full_name=member.staff_name)
        references.append(ExternalReference(owner="ACADEMIC_DATA_SERVICE", record_kind="STAFF", local_reference=member.staff_id,
            authoritative_id=row["id"], source_reference="synthetic staff"))
        add("academic", "staff_teaching_profiles", staff_number=member.staff_id, staff_type=member.staff_type.value,
            source_position=member.position, department_id=None, source_id=source_ids["synthetic-academic.xlsx"])
    for record in academic.source_preferences:  # Stored rows; the adapter never re-reads a workbook.
        add("academic", "lecturer_preferences", record_key=record.location.diagnostic_key,
            staff_number=record.source_values["Staff_ID"], source_id=source_ids["synthetic-preferences.xlsx"],
            worksheet=record.location.worksheet, source_row=record.location.row, source_values=dict(record.source_values))
    spec = add("academic", "specializations", code="IT", name="Synthetic IT", source_id=source_ids["synthetic-counts.xlsx"])
    module_ids = {}
    for contact in extract_academic_catalog(academic).modules:
        source, fields = contact.source, contact.source.contract_fields
        key = f"{source.location.workbook}:{source.location.worksheet}:{source.location.row}"
        row = add("academic", "modules", record_key=key, **{k: fields[k] for k in ("module_code", "module_name", "academic_year",
            "semester", "degree_type")}, **{f"{kind.value.lower()}_minutes": minutes for kind, minutes in contact.weekly_minutes.items()},
            source_id=source_ids[source.location.workbook], worksheet=source.location.worksheet, source_row=source.location.row,
            source_values=dict(source.source_values, Specialization=fields["specialization"]))
        module_ids[source.location] = row["id"]
        add("academic", "module_specializations", module_id=row["id"], specialization_id=spec["id"])
        references.append(ExternalReference(owner="ACADEMIC_DATA_SERVICE", record_kind="MODULE_SOURCE_ROW", local_reference=key,
            authoritative_id=row["id"], source_reference="synthetic module source row"))
    for index, assignment in enumerate(academic.source_assignments):
        add("academic", "module_teaching_teams", module_id=module_ids[assignment.location], staff_number=assignment.source_staff_id,
            role=assignment.role.value, source_slot=f"synthetic-{index}")
    return owners, references, add, spec


def product_evidence(*, combined=True, tenant=TENANT, cells=None):
    config = product_configuration(combined=combined)
    enrolled, academic, policy, original = enrolled_batch(config, cells)
    sources = tuple(SourceDocument(label=name, sha256=("a" if index % 2 else "c") * 64,
        owner="ACADEMIC_DATA_SERVICE" if name != "synthetic-venues.xlsx" else "RESOURCE_SERVICE",
        provenance_kind="UNIVERSITY_SOURCED" if name != "synthetic-counts.xlsx" else "APPROVED_PP1_ESTIMATE",
        provenance={"synthetic_fixture": True, "name": name})
        for index, name in enumerate(("synthetic-academic.xlsx", "synthetic-counts.xlsx", "synthetic-preferences.xlsx",
                                      "synthetic-venues.xlsx")))
    document = sealed_document(enrolled, policy, sources, combined=combined, tenant=tenant)
    owners, references, add, spec = database_evidence(document, academic, tenant)
    base = original  # The pre-enrolment plan, as stored in the planning-group tables.
    allocations = {}
    for cohort in base.plan.cohorts:
        total = add("academic", "cohort_totals", academic_year=cohort.academic_year, semester=cohort.semester,
            stream=cohort.stream.value, student_count=cohort.student_count, source_id=next(
                row["id"] for row in owners["academic"]["source_datasets"] if row["filename"] == "synthetic-counts.xlsx"))
        allocations[cohort.cohort_id] = add("academic", "pp1_specialization_allocations", cohort_total_id=total["id"],
            specialization_id=spec["id"], local_cohort_reference=cohort.cohort_id, student_count=cohort.student_count,
            source_id=total["source_id"], source_values=dict(cohort=cohort.model_dump(mode="json")))["id"]
    group_ids = {group.group_id: uuid4() for group in base.plan.groups}
    for group in base.plan.groups:
        owners["academic"]["pp1_student_groups"].append(dict(id=group_ids[group.group_id], institution_id=tenant,
            allocation_id=allocations[base.plan.cohort_by_group[group.group_id]], local_group_reference=group.group_id,
            plan_reference="SYNTHETIC_BASE_PLAN", parent_id=group_ids.get(group.parent_group_id), student_count=group.student_count,
            source_values=dict(group=group.model_dump(mode="json"))))
    venue_source = next(row["id"] for row in owners["resource"]["source_datasets"])
    for venue in document.contexts.venue_input.venues:
        row = add("resource", "venues", venue_code=venue.venue_id, **{k: getattr(venue, k) for k in
            ("venue_name", "venue_type", "capacity", "building", "floor", "lab_specialization")}, source_id=venue_source)
        references.append(ExternalReference(owner="RESOURCE_SERVICE", record_kind="VENUE", local_reference=venue.venue_id,
            authoritative_id=row["id"], source_reference="synthetic venue"))
    document = type(document).model_validate(document.model_dump(round_trip=True) | dict(external_references=references))
    identifier = uuid4()
    for table, records in (("snapshot_sources", document.sources), ("snapshot_scopes", document.scopes),
                           ("snapshot_issues", document.issues)):
        for row in records:
            add("teaching", table, snapshot_id=identifier, **row.model_dump(mode="python"))
    return DatabaseEvidence(tenant, identifier, document, owners["academic"], owners["resource"], owners["teaching"])


def generation_request(evidence, **changes):
    from schedence_teaching.generation.contracts import GenerationRequest
    document = evidence.document
    sources = {row.label: row.sha256 for row in document.sources}
    return GenerationRequest.model_validate(dict(institution_id=evidence.institution_id, snapshot_id=evidence.snapshot_id,
        semester=1, academic_years=[1], streams=["WEEKDAY"], specializations=["IT"], calendar_label=document.calendar_label,
        policy_version="HISTORICAL_SNAPSHOT", academic_term_reference=document.academic_term_reference,
        calendar_reference=sources["PP1:calendar-configuration"],
        scheduling_policy_reference=sources["PP1:synthetic-elective-registration-evidence"],
        max_time_seconds=60.0, job_timeout_seconds=120.0) | changes)


@pytest.fixture(scope="module")
def evidence():
    return product_evidence()


def test_stored_preference_rows_become_sealed_hard_blocks_and_soft_preferences(evidence):
    document = evidence.document
    assert document.resolved_policy_digest is not None and document.solver_config.objective_stage_plan is not None
    blocks = {(row.staff_id, block.day, block.start_minute, block.end_minute) for row in document.contexts.staff_input.availability
              for block in row.blocked_periods}
    assert blocks == {("SYN-LEC-1", "MONDAY", 510, 750), ("SYN-LEC-1", "MONDAY", 750, 1050)}
    preferences = {(row.staff_id, row.category.value, row.direction.value, row.penalty_weight)
                   for row in document.contexts.preference_input.preferences}
    assert ("SYN-LEC-1", "TIME_OF_DAY", "PREFER", 3) in preferences and ("SYN-LEC-2", "TIME_OF_DAY", "AVOID", 5) in preferences
    assert all(row["source_values"]["Staff_ID"] == row["staff_number"] for row in evidence.academic["lecturer_preferences"])


def generate(evidence, tmp_path, **changes):
    """The real generation path minus process spawning: database reconstruction, scoped seal, worker solve, gate."""
    from schedence_teaching.generation.inputs import prepare_database_work
    from schedence_teaching.generation.worker import execute_work
    from schedence_teaching.persistence.snapshots import prepare_attempt
    work = prepare_database_work(evidence, generation_request(evidence, **changes))
    outcome = execute_work(work, tmp_path)
    return work, outcome.result, prepare_attempt(work.document, outcome.result)


@pytest.fixture(scope="module")
def generated(evidence, tmp_path_factory):
    return generate(evidence, tmp_path_factory.mktemp("generation"))


def test_database_generation_runs_the_sealed_combined_plan_and_publishes_only_validated_results(generated):
    import json
    work, result, attempt = generated
    document = work.document
    assert document.resolved_policy_digest is not None  # Scoped snapshot re-bound to the parent's approved revision.
    assert document.solver_config.resolved_policy.revision == "v1"
    assert {row.academic_year for row in document.scopes if row.scope_status == "SUPPORTED"} == {1}
    assert result.status.value in {"OPTIMAL", "FEASIBLE"} and attempt.success
    assert attempt.scheduled_occurrences == attempt.expected_occurrences == len(result.schedule)
    report = result.objective_stage_report
    assert report.priority_order == ("DELIVERIES", "CONTACT_MINUTES", "MAXIMUM_OVER_TARGET_MINUTES", "TOTAL_OVER_TARGET_MINUTES",
                                     "MAXIMUM_TARGET_DEVIATION", *PLAN)
    assert (result.status.value == "OPTIMAL") == report.all_stages_proven_optimal
    validation = json.loads(attempt.validation_json)
    assert validation["assessment"] == "PASSED" and validation["resolved_policy_digest"] == document.resolved_policy_digest
    assert "batch_online_day_selection" in validation["independent_summary"]["checks"]
    stored = json.loads(attempt.result_json)  # Exactly what persistence stores as run metadata.
    assert stored["objective_stage_plan"]["priority_order"] == list(report.priority_order)
    assert stored["teaching_day_compactness"] and stored["daily_load_distribution"] and stored["batch_online_days"]


def test_stored_not_available_rows_are_never_booked_and_preferences_are_scored(generated):
    work, result, _ = generated
    assert not any(row.assigned_staff_id == "SYN-LEC-1" and row.day == "MONDAY" for row in result.schedule)
    blocked = {(row.staff_id, block.day) for row in work.document.contexts.staff_input.availability for block in row.blocked_periods}
    assert ("SYN-LEC-1", "MONDAY") in blocked
    assert result.preference_report is not None and result.preference_report.objective_status == "EVALUATED_COMPONENT"
    assert "staff_blocked_periods" in result.validation.checks


def test_batch_online_day_rules_hold_in_the_generated_timetable(generated):
    _, result, _ = generated
    ((year, semester, stream, day),) = result.batch_online_days
    assert (year, semester, stream) == (1, 1, "WEEKDAY") and day in WEEKDAYS
    for row in result.schedule:
        if row.day == day:
            assert row.session.session_type.value == "LECTURE" and row.delivery_mode.value == "ONLINE" and row.assigned_venue_id is None
        else:
            assert row.delivery_mode.value == "PHYSICAL" and row.assigned_venue_id is not None


class DatabaseInputs:
    """The existing input-provider contract over sanitized owner evidence (no live connection)."""
    def __init__(self, evidence):
        self.evidence = evidence

    async def prepare(self, request):
        import asyncio
        from schedence_teaching.generation.inputs import prepare_database_work
        from schedence_teaching.persistence.ports import PersistenceConflict
        if (request.institution_id, request.snapshot_id) != (self.evidence.institution_id, self.evidence.snapshot_id):
            raise PersistenceConflict("snapshot not found in this institution")  # What the RLS-scoped reader reports.
        return await asyncio.to_thread(prepare_database_work, self.evidence, request)


class InProcessExecutor:
    """The real worker body (execute_work) without process spawning."""
    def __init__(self, directory):
        self.directory, self.calls = directory, 0

    async def run(self, work, job_id):
        import asyncio
        from schedence_teaching.generation.worker import execute_work
        self.calls += 1
        target = self.directory / str(job_id)
        target.mkdir()
        return await asyncio.to_thread(execute_work, work, target)


def recording_sink():
    from schedence_teaching.generation.persistence import NoDraftPersistence

    class Sink(NoDraftPersistence):
        mode = "ISOLATED_POSTGRESQL"

        def __init__(self):
            self.saved = []

        async def save(self, work, result, job_id, actor):
            self.saved.append((work.document, result))
            return uuid4(), uuid4()
    return Sink()


def install(api, evidence, tmp_path):
    from schedence_teaching.generation.jobs import DevelopmentGenerationJobs
    sink, executor = recording_sink(), InProcessExecutor(tmp_path)
    api.runtime.generations = DevelopmentGenerationJobs(DatabaseInputs(evidence), executor, enabled=True, persistence=sink)
    return sink, executor


def submit(api, evidence, key="product-1", **changes):
    from test_generation_jobs import poll, post
    response = post(api, generation_request(evidence, **changes).model_dump(mode="json"), key=key)
    return response, (poll(api, response.json()["job_id"]) if response.status_code == 202 else None)


def test_authorized_api_generation_runs_the_combined_plan_and_publishes_a_validated_draft(api, evidence, tmp_path):
    sink, executor = install(api, evidence, tmp_path)
    response, job = submit(api, evidence)
    assert response.status_code == 202, response.text
    assert job["status"] == "COMPLETED" and job["solver_status"] == "OPTIMAL" and job["validation_status"] == "PASSED"
    assert job["scheduled_occurrences"] == job["expected_occurrences"] > 0
    optimization = job["optimization"]
    assert optimization["all_stages_proven_optimal"] and optimization["proven_stage_count"] == len(optimization["priority_order"]) == 11
    document, result = sink.saved[0]
    assert document.resolved_policy_digest is not None and len(result.schedule) == job["scheduled_occurrences"]
    assert not any(row.assigned_staff_id == "SYN-LEC-1" and row.day == "MONDAY" for row in result.schedule)


def test_cross_institution_generation_is_rejected_without_running_anything(api, evidence, tmp_path):
    from test_persistence_contracts import OTHER_TENANT
    sink, executor = install(api, evidence, tmp_path)
    api.profile["institutions"][0]["institution_id"] = str(OTHER_TENANT)  # Only the other institution is granted.
    response, _ = submit(api, evidence, key="cross-1")
    assert response.status_code == 403 and response.json()["error"]["code"] == "GENERATION_FORBIDDEN"
    # Granted institution, but the snapshot belongs to another tenant: the scoped reader finds nothing.
    response, _ = submit(api, evidence, key="cross-2", institution_id=OTHER_TENANT)
    assert response.status_code == 400 and response.json()["error"]["code"] == "INVALID_GENERATION_INPUTS"
    assert executor.calls == 0 and not sink.saved


def test_feasible_result_with_unfinished_stages_is_validated_and_reported_honestly(api, evidence, tmp_path, monkeypatch):
    from ortools.sat.python import cp_model
    native, calls = cp_model.CpSolver.solve, []

    def stop_at_consecutive(self, model, *args, **kwargs):
        status = native(self, model, *args, **kwargs)
        calls.append(status)
        return cp_model.FEASIBLE if len(calls) == 7 else status  # Peak, total, deviation, student, staff, thin, consecutive.

    monkeypatch.setattr(cp_model.CpSolver, "solve", stop_at_consecutive)
    sink, _ = install(api, evidence, tmp_path)
    _, job = submit(api, evidence, key="feasible-1")
    assert job["status"] == "COMPLETED" and job["solver_status"] == "FEASIBLE" and job["validation_status"] == "PASSED"
    optimization = job["optimization"]
    assert not optimization["all_stages_proven_optimal"] and optimization["proven_stage_count"] == 8
    assert [row["status"] for row in optimization["stages"]][-1] == "FEASIBLE" and len(optimization["stages"]) == 9
    assert len(sink.saved) == 1  # A validated FEASIBLE draft may be stored; it never claims optimality.


@pytest.mark.parametrize("outcome", ["INFEASIBLE", "UNKNOWN"])
def test_infeasible_and_unknown_outcomes_publish_nothing(api, tmp_path, monkeypatch, outcome):
    from ortools.sat.python import cp_model
    if outcome == "INFEASIBLE":
        # Stored rows leave only Tuesday morning (240 minutes) for 420 required minutes: hard blocks are never relaxed.
        evidence = product_evidence(cells={**WEEKDAY_CLOSED, "Tuesday_Morning": "Available"})
    else:
        evidence = product_evidence()
        monkeypatch.setattr(cp_model.CpSolver, "solve", lambda self, model, *a, **k: cp_model.UNKNOWN)
    sink, _ = install(api, evidence, tmp_path)
    _, job = submit(api, evidence, key=f"{outcome.lower()}-1")
    assert job["status"] == "FAILED" and job["solver_status"] == outcome and job["error"]["category"] == f"SOLVER_{outcome}"
    assert job.get("timetable_version_id") is None and not sink.saved and "optimization" not in job


def test_stored_rows_leaving_no_eligible_opportunity_are_refused_before_queueing(api, tmp_path):
    # Every weekday Not Available: input preparation fails closed; nothing is queued, solved or stored.
    evidence = product_evidence(cells=WEEKDAY_CLOSED)
    sink, executor = install(api, evidence, tmp_path)
    response, job = submit(api, evidence, key="all-blocked-1")
    assert response.status_code == 503 and response.json()["error"]["code"] == "INPUTS_UNAVAILABLE" and job is None
    assert executor.calls == 0 and not sink.saved


def test_the_publication_gate_rejects_a_tampered_combined_result(generated):
    from schedence_teaching.persistence.snapshots import prepare_attempt
    work, result, _ = generated
    report = result.objective_stage_report
    values = dict(report.stage_values)
    values["CONSECUTIVE_PENALTY"] -= 1
    tampered = replace(result, objective_stage_report=replace(report, stage_values=tuple(values.items())))
    with pytest.raises(ValueError):
        prepare_attempt(work.document, tampered)


def test_stored_version_returns_its_combined_stage_history_through_the_existing_validation_endpoint(api, generated):
    import json
    from schedence_teaching.persistence.policy import version_policy_metadata
    from schedence_teaching.persistence.retrieval import assemble_timetable
    from schedence_teaching.persistence.snapshots import content_digest
    from schedence_teaching.persistence.versioning import METADATA_KEY, delivery_digest
    from test_timetable_versioning import record_for
    work, result, attempt = generated
    document, version, run, report, bookings, coverage = record_for(work.document, result)
    metadata = dict(logical_status="VALIDATED", normalized_deliveries_sha256=delivery_digest(json.loads(attempt.deliveries_json)),
                    snapshot_sha256=content_digest(document), **version_policy_metadata(document))
    run.update(result_metadata=json.loads(attempt.result_json) | {METADATA_KEY: metadata}, snapshot_id=uuid4(),
               solver_status=result.status.value, runtime_seconds=result.runtime_seconds)  # As the repository writes it.
    report["evidence"] = {METADATA_KEY: metadata}
    version.update(id=uuid4(), series_id=uuid4(), version_number=1, academic_complete=False)
    api.records.append(assemble_timetable(document, version, run, report, bookings, coverage))
    response = api.client.get(f"/api/teaching/timetables/{version['id']}/validation", headers=api.headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "PASSED" and body["hard_violations"] == 0
    assert body["covered_occurrences"] == body["expected_occurrences"] == len(result.schedule)
    assert body["optimization"]["priority_order"] == list(result.objective_stage_report.priority_order)
    assert body["optimization"]["proven_stage_count"] == result.objective_stage_report.proven_stage_count
    sessions = api.client.get(f"/api/teaching/timetables/{version['id']}/sessions", headers=api.headers).json()
    assert sessions["total"] == len(result.schedule)


def test_legacy_jobs_and_versions_have_no_optimization_section(api):
    from test_generation_jobs import install as legacy_install, poll, post
    _, body, _ = legacy_install(api)
    job = poll(api, post(api, body, key="legacy-1").json()["job_id"])
    assert job["status"] == "COMPLETED" and "optimization" not in job
    first = api.records[0].version["id"]
    assert "optimization" not in api.client.get(f"/api/teaching/timetables/{first}/validation", headers=api.headers).json()


def test_scoped_rebinding_refuses_changed_rules_overrides_and_unsealed_parents(evidence):
    from schedence_teaching.persistence.policy import rebind_scoped_snapshot
    from test_fairness_solver import fairness
    parent = evidence.document
    unsealed = parent.model_dump(round_trip=True)
    unsealed["solver_config"].pop("resolved_policy", None)
    unsealed.pop("resolved_policy_digest", None)
    changed = dict(unsealed, contexts=dict(unsealed["contexts"],
        fairness_policy=fairness(("SYN-LEC-1",), week_reference=parent.calendar.week_reference)))
    with pytest.raises(ValueError, match="change sealed institution rules"):
        rebind_scoped_snapshot(type(parent).model_validate(changed), parent)
    with pytest.raises(ValueError, match="Only a sealed parent"):
        rebind_scoped_snapshot(type(parent).model_validate(unsealed), type(parent).model_validate(unsealed))
    same = rebind_scoped_snapshot(type(parent).model_validate(unsealed), parent)
    sealed, original = same.solver_config.resolved_policy, parent.solver_config.resolved_policy
    # Same approved revision and rules; the new seal records its own parent provenance, so its digest differs.
    assert (sealed.revision, sealed.policy_reference, sealed.approval_json, sealed.rank_appointment_rules_json) == (
        original.revision, original.policy_reference, original.approval_json, original.rank_appointment_rules_json)


def test_a_scope_drops_only_the_inapplicable_preference_stage(evidence):
    from schedence_teaching.generation.inputs import prepare_database_work
    cells = {**WEEKDAY_CLOSED, "Tuesday_Morning": "Available"}  # Only weekend criteria remain; none apply to Year 1 weekday.
    source = product_evidence(cells=cells)
    scoped = prepare_database_work(source, generation_request(source)).document
    assert scoped.contexts.preference_input is None
    assert [row.value for row in scoped.solver_config.objective_stage_plan.order] == [key for key in PLAN if key != "PREFERENCE_PENALTY"]
    assert scoped.resolved_policy_digest is not None


@pytest.mark.parametrize("formulation", ["SUCCESSOR_CIRCUIT", "TIME_INDEXED"])
def test_unavoidable_excess_continuous_teaching_is_proven_quickly(formulation):
    # Three separate 120-minute sessions against a 60-minute preferred maximum: 3 x 60 x 3 = 540 is forced.
    from schedence_teaching.preparation import prepare_timetable_input
    from schedence_teaching.schemas import TeachingTimetableInput
    from schedence_teaching.solver import ConsecutiveOptimizationConfig, SolverStatus, solve_timetable
    from test_consecutive import scoring
    from test_preferences import calendar
    from test_staff_solver import demand, windows
    payload = demand({"OFFER": [("STAFF", "LIC")]}, [("OFFER", "G", 3)]).input_snapshot.model_dump(round_trip=True)
    payload["session_requirements"][0]["duration_minutes"] = 120
    prepared = prepare_timetable_input(TeachingTimetableInput.model_validate(payload))
    config = windows(240, days=("D1", "D2", "D3"), limit=10)
    result = solve_timetable(prepared, config, consecutive_policy=scoring(), consecutive_calendar=calendar(config),
        consecutive_config=ConsecutiveOptimizationConfig.model_validate({"priority": "EXISTING_OBJECTIVES_THEN_CONSECUTIVE",
            "priority_policy_reference": "synthetic:excess-bound", "formulation": formulation}))
    assert result.status is SolverStatus.OPTIMAL and result.consecutive_report.total_weighted_penalty == 540
    assert result.runtime_seconds < 10
