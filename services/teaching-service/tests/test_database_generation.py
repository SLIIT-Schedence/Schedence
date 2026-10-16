"""Sanitized SQL-row fixtures; generation must never reopen a workbook."""

from copy import deepcopy
from dataclasses import replace
from datetime import date
import json
from uuid import uuid4

import pytest

from schedence_teaching.persistence.contracts import ExternalReference, SourceDocument
from schedence_teaching.persistence.database_read import DatabaseEvidence, OWNER_TABLES, check_read_security, verify_snapshot_children
from schedence_teaching.persistence.generation_inputs import (DatabaseGenerationRequest, build_database_generation,
    compare_snapshot_inputs, reconstruct_enrollment)
from schedence_teaching.persistence.historical_generation import booking_differences, historical_draft
from schedence_teaching.persistence.pp1 import capture_pp1_snapshot
from schedence_teaching.persistence.ports import PersistenceConflict
from schedence_teaching.persistence.snapshots import prepare_attempt
from schedence_teaching.pilot.academic import extract_academic_catalog
from schedence_teaching.pilot.elective_validation import validate_elective_result
from schedence_teaching.pilot.electives import select_enrolled_scope
from schedence_teaching.solver import solve_timetable
from test_elective_enrollment import elective_fixture
from test_semester_scenarios import snapshot
from test_persistence_contracts import TENANT


@pytest.fixture
def database_fixture(elective_fixture):
    original = snapshot(elective_fixture).enrolled
    base, academic, _, policy = elective_fixture
    owners = {name: {table: [] for table in names} for name, names in OWNER_TABLES.items()}
    def add(schema, table, **values):
        row = dict(id=uuid4(), institution_id=TENANT, **values)
        owners[schema][table].append(row)
        return row
    sources, source_ids = [], {}
    for owner, name, kind in (("academic", "synthetic-academic.xlsx", "UNIVERSITY_SOURCED"),
        ("academic", "synthetic-counts.xlsx", "APPROVED_PP1_ESTIMATE"),
        ("resource", "synthetic-venues.xlsx", "UNIVERSITY_SOURCED")):
        document = SourceDocument(label=name, sha256="a"*64, owner=owner.upper()+"_DATA_SERVICE" if owner=="academic" else "RESOURCE_SERVICE",
            provenance_kind=kind, provenance={"synthetic_fixture": True})
        sources.append(document)
        source_ids[name] = add(owner,"source_datasets",filename=name,sha256=document.sha256,
            provenance_kind=kind,provenance=document.provenance)["id"]
    document = capture_pp1_snapshot(original, institution_id=TENANT, sources=tuple(sources), blocked_scopes=(),
        academic_term_reference="SYNTHETIC_DATABASE_REFERENCE",timezone_name="Asia/Colombo",registration_policy=policy.model_dump(mode="json"))
    references = []
    for member in academic.staff_members:
        row = add("academic","staff",staff_number=member.staff_id,full_name=member.staff_name)
        references.append(ExternalReference(owner="ACADEMIC_DATA_SERVICE",record_kind="STAFF",local_reference=member.staff_id,
            authoritative_id=row["id"],source_reference="synthetic staff"))
        add("academic","staff_teaching_profiles",staff_number=member.staff_id,staff_type=member.staff_type.value,
            source_position=member.position,department_id=None,source_id=source_ids["synthetic-academic.xlsx"])
    spec = add("academic","specializations",code="IT",name="Synthetic IT",source_id=source_ids["synthetic-counts.xlsx"])
    module_ids = {}
    for contact in extract_academic_catalog(academic).modules:
        source, fields = contact.source, contact.source.contract_fields
        key=f"{source.location.workbook}:{source.location.worksheet}:{source.location.row}"
        row=add("academic","modules",record_key=key,**{k:fields[k] for k in ("module_code","module_name","academic_year","semester","degree_type")},
            **{f"{kind.value.lower()}_minutes":minutes for kind,minutes in contact.weekly_minutes.items()},
            source_id=source_ids[source.location.workbook],worksheet=source.location.worksheet,source_row=source.location.row,
            source_values=dict(source.source_values,Specialization=fields["specialization"]))
        module_ids[source.location]=row["id"]
        add("academic","module_specializations",module_id=row["id"],specialization_id=spec["id"])
        references.append(ExternalReference(owner="ACADEMIC_DATA_SERVICE",record_kind="MODULE_SOURCE_ROW",local_reference=key,
            authoritative_id=row["id"],source_reference="synthetic module source row"))
    for index,assignment in enumerate(academic.source_assignments):
        add("academic","module_teaching_teams",module_id=module_ids[assignment.location],staff_number=assignment.source_staff_id,
            role=assignment.role.value,source_slot=f"synthetic-{index}")
    allocations = {}
    for cohort in base.plan.cohorts:
        total=add("academic","cohort_totals",academic_year=cohort.academic_year,semester=cohort.semester,
            stream=cohort.stream.value,student_count=cohort.student_count,source_id=source_ids["synthetic-counts.xlsx"])
        allocation=add("academic","pp1_specialization_allocations",cohort_total_id=total["id"],specialization_id=spec["id"],
            local_cohort_reference=cohort.cohort_id,student_count=cohort.student_count,
            source_id=source_ids["synthetic-counts.xlsx"],source_values=dict(cohort=cohort.model_dump(mode="json")))
        allocations[cohort.cohort_id]=allocation["id"]
    group_ids={group.group_id:uuid4() for group in base.plan.groups}
    for group in base.plan.groups:
        owners["academic"]["pp1_student_groups"].append(dict(id=group_ids[group.group_id],institution_id=TENANT,
            allocation_id=allocations[base.plan.cohort_by_group[group.group_id]],local_group_reference=group.group_id,
            plan_reference="SYNTHETIC_BASE_PLAN",parent_id=group_ids.get(group.parent_group_id),student_count=group.student_count,
            source_values=dict(group=group.model_dump(mode="json"))))
    for venue in document.contexts.venue_input.venues:
        row=add("resource","venues",venue_code=venue.venue_id,**{k:getattr(venue,k) for k in
            ("venue_name","venue_type","capacity","building","floor","lab_specialization")},source_id=source_ids["synthetic-venues.xlsx"])
        references.append(ExternalReference(owner="RESOURCE_SERVICE",record_kind="VENUE",local_reference=venue.venue_id,
            authoritative_id=row["id"],source_reference="synthetic venue"))
    document=type(document).model_validate(document.model_dump()|dict(external_references=references))
    identifier=uuid4()
    for table,records in (("snapshot_sources",document.sources),("snapshot_scopes",document.scopes),("snapshot_issues",document.issues)):
        for row in records:add("teaching",table,snapshot_id=identifier,**row.model_dump(mode="python"))
    return DatabaseEvidence(TENANT,identifier,document,owners["academic"],owners["resource"],owners["teaching"]),original


def request(evidence, **overrides):
    return DatabaseGenerationRequest(institution_id=TENANT,snapshot_id=evidence.snapshot_id,semester=1,
        calendar_label=evidence.document.calendar_label,policy_version="HISTORICAL_SNAPSHOT",**overrides)


def test_database_rows_reconstruct_exact_original_inputs_and_electives_without_excel(database_fixture, monkeypatch):
    evidence,original=database_fixture
    monkeypatch.setattr("openpyxl.load_workbook",lambda *a,**k:pytest.fail("generation reopened Excel"))
    reconstructed=reconstruct_enrollment(evidence)
    assert reconstructed.batch.prepared==original.batch.prepared
    assert reconstructed.batch.config==original.batch.config
    assert reconstructed.selections==original.selections
    assert reconstructed.group_memberships==original.group_memberships
    assert reconstructed.research_pending_cohort_ids==original.research_pending_cohort_ids
    assert all(len(set(row.offering_ids))==2 for row in reconstructed.selections)
    verify_snapshot_children(evidence)
    assert compare_snapshot_inputs(evidence.document,evidence.document)["status"]=="PASSED"


def test_database_backed_scope_solves_and_independent_validation_passes(database_fixture):
    evidence,_=database_fixture
    loaded=build_database_generation(evidence,request(evidence,academic_years=[1],streams=["WEEKDAY"],specializations=["IT"]))
    batch=loaded.enrolled.batch
    result=solve_timetable(batch.prepared,batch.config,**batch.solver_inputs)
    assert validate_elective_result(loaded.enrolled,result)["status"]=="PASSED"
    assert len(result.schedule)==len(batch.prepared.sessions)
    assert loaded.provenance["no_excel_files_read"] and loaded.provenance["database_read_only"]


@pytest.mark.parametrize("table,field,value", [
    ("staff","full_name","Changed synthetic name"), ("staff_teaching_profiles","staff_type","INSTRUCTOR"),
    ("staff_teaching_profiles","source_position","Professor"), ("modules","lecture_minutes",999),
    ("modules","module_code","CHANGED"), ("modules","source_id",None),
    ("module_teaching_teams","staff_number","MISSING"), ("module_teaching_teams","role","TEAM_MEMBER"),
    ("cohort_totals","student_count",999), ("pp1_specialization_allocations","student_count",999),
    ("pp1_student_groups","student_count",999), ("pp1_student_groups","parent_id",uuid4()),
    ("source_datasets","sha256","b"*64), ("source_datasets","institution_id",uuid4()),
])
def test_inconsistent_master_records_are_rejected(database_fixture,table,field,value):
    evidence=deepcopy(database_fixture[0])
    evidence.academic[table][0][field]=value
    with pytest.raises((PersistenceConflict,ValueError,KeyError)):reconstruct_enrollment(evidence)


@pytest.mark.parametrize("table",["staff","staff_teaching_profiles","modules","module_specializations","module_teaching_teams",
    "pp1_specialization_allocations","pp1_student_groups"])
def test_missing_owner_mappings_are_rejected(database_fixture,table):
    evidence=deepcopy(database_fixture[0])
    evidence.academic[table].pop(0)
    with pytest.raises((PersistenceConflict,ValueError,KeyError)):reconstruct_enrollment(evidence)


def test_missing_authoritative_reference_is_never_fabricated(database_fixture):
    evidence,_=database_fixture
    changed=evidence.document.model_copy(deep=True)
    changed.external_references=()
    with pytest.raises(PersistenceConflict,match="mapping is missing"):
        reconstruct_enrollment(replace(evidence,document=changed))


@pytest.mark.parametrize("field,value",[("institution_id",uuid4()),("snapshot_id",uuid4()),("calendar_label","PP1_EXPERIMENTAL_CALENDAR"),
    ("semester",2),("specializations",["NOT_IN_CATALOG"])])
def test_unavailable_or_wrong_scope_fails_closed(database_fixture,field,value):
    evidence,_=database_fixture
    raw=request(evidence).model_dump()|{field:value}
    if field=="calendar_label" and value==evidence.document.calendar_label:raw[field]="PP1_TEST_CALENDAR"
    with pytest.raises(PersistenceConflict):build_database_generation(evidence,DatabaseGenerationRequest.model_validate(raw))


def test_updated_policy_requires_review_instead_of_silently_changing_historical_data(database_fixture):
    evidence,_=database_fixture
    with pytest.raises(ValueError):DatabaseGenerationRequest.model_validate(request(evidence).model_dump()|dict(policy_version="CURRENT_REVIEW"))
    require_module=evidence.academic["modules"][0]
    evidence.academic["module_classification_reviews"].append(dict(module_id=require_module["id"],pp1_test_candidate=False,institution_id=TENANT))
    loaded=build_database_generation(evidence,request(evidence))
    assert loaded.equivalence["policy_differences"]
    assert loaded.enrolled.selections==database_fixture[1].selections


@pytest.mark.parametrize("table",["snapshot_sources","snapshot_scopes","snapshot_issues"])
def test_normalized_snapshot_metadata_must_match_immutable_document(database_fixture,table):
    evidence=deepcopy(database_fixture[0])
    evidence.teaching[table].pop(0)
    with pytest.raises(PersistenceConflict,match="normalized snapshot metadata"):verify_snapshot_children(evidence)


def security_fixture(schema="teaching"):
    expression="(institution_id = (NULLIF(current_setting('schedence.institution_id'::text, true), ''::text))::uuid)"
    role=dict(transaction_read_only="on",rolsuper=False,rolbypassrls=False,rolcreaterole=False,rolcreatedb=False,rolreplication=False,elevated_membership=False)
    tables=[dict(relname=name,can_select=True,owns_table=False,relrowsecurity=True,relforcerowsecurity=True,
        policy_count=1,using_expression=expression,check_expression=expression) for name in OWNER_TABLES[schema]]
    return role,tables


@pytest.mark.parametrize("field,value",[("transaction_read_only","off"),("rolsuper",True),("rolbypassrls",True),
    ("rolcreaterole",True),("rolcreatedb",True),("rolreplication",True),("elevated_membership",True)])
def test_readonly_reader_rejects_insecure_connections(field,value):
    role,tables=security_fixture();role[field]=value
    with pytest.raises(PersistenceConflict):check_read_security(role,tables,"teaching")


@pytest.mark.parametrize("field,value",[("can_select",False),("owns_table",True),("relrowsecurity",False),
    ("relforcerowsecurity",False),("policy_count",2),("using_expression","true"),("check_expression","true")])
def test_reader_rejects_missing_access_or_weakened_rls(field,value):
    role,tables=security_fixture();tables[0][field]=value
    with pytest.raises(PersistenceConflict):check_read_security(role,tables,"teaching")


def test_legacy_staff_security_is_reported_without_modifying_it():
    role,tables=security_fixture("academic")
    tables[0].update(relrowsecurity=False,relforcerowsecurity=False,policy_count=0,using_expression=None,check_expression=None)
    check_read_security(role,tables,"academic")


def test_equivalence_detects_changed_source_contact_demand(database_fixture):
    evidence,_=database_fixture
    changed=evidence.document.model_copy(deep=True)
    changed.teaching_input.session_requirements[0].duration_minutes+=30
    with pytest.raises(PersistenceConflict):compare_snapshot_inputs(evidence.document,changed)


@pytest.fixture
def archived_database_fixture(database_fixture):
    evidence, original=database_fixture
    selected=select_enrolled_scope(original,{(1,1,"WEEKDAY","IT")})
    registration=next(row.provenance for row in evidence.document.sources if row.label=="PP1:synthetic-elective-registration-evidence")
    document=capture_pp1_snapshot(selected,institution_id=TENANT,
        sources=tuple(row for row in evidence.document.sources if not row.label.startswith("PP1:")),
        blocked_scopes=(),academic_term_reference="SYNTHETIC_SMALL_ARCHIVE",timezone_name="Asia/Colombo",
        registration_policy=registration["policy"])
    document=type(document).model_validate(document.model_dump()|dict(external_references=evidence.document.external_references))
    evidence=replace(evidence,document=document,snapshot_id=uuid4())
    reconstructed=reconstruct_enrollment(evidence)
    result=solve_timetable(reconstructed.batch.prepared,reconstructed.batch.config,**reconstructed.batch.solver_inputs)
    plan=prepare_attempt(document,result)
    version,run_id,report_id=uuid4(),uuid4(),uuid4()
    common=dict(institution_id=TENANT)
    evidence.teaching["generation_runs"].append(dict(common,id=run_id,snapshot_id=evidence.snapshot_id,
        solver_status=result.status.value,runtime_seconds=result.runtime_seconds))
    evidence.teaching["validation_reports"].append(dict(common,id=report_id,run_id=run_id,passed=True,hard_violations=0))
    evidence.teaching["timetable_versions"].append(dict(common,id=version,run_id=run_id,validation_report_id=report_id,
        validation_passed=True,solver_success=True))
    for row in json.loads(plan.deliveries_json):
        coverage=row.pop("coverage");identifier=uuid4()
        row["teaching_date"]=date.fromisoformat(row["teaching_date"])
        evidence.teaching["deliveries"].append(dict(common,id=identifier,version_id=version,**row))
        for item in coverage:evidence.teaching["delivery_coverage"].append(dict(common,delivery_id=identifier,version_id=version,**item))
    return evidence,reconstructed,result


def test_no_year4_snapshot_needs_no_invented_elective_registrations(archived_database_fixture):
    evidence,enrolled,_=archived_database_fixture
    assert not enrolled.selections and not enrolled.research_pending_cohort_ids
    assert all(row.academic_year==1 for row in evidence.document.scopes)


def test_source_versions_are_resolved_by_checksum_not_arbitrary_filename_order(database_fixture):
    evidence,_=database_fixture
    current=dict(evidence.academic["source_datasets"][0],id=uuid4(),sha256="f"*64)
    evidence.academic["source_datasets"].append(current)
    assert reconstruct_enrollment(evidence).selections==database_fixture[1].selections


def test_historical_database_deliveries_are_independently_validated_without_mutation(archived_database_fixture):
    evidence,enrolled,original=archived_database_fixture
    before=evidence.fingerprint()
    result,audit=historical_draft(evidence,enrolled)
    assert audit["status"]=="PASSED" and audit["hard_violations"]==0
    assert evidence.fingerprint()==before
    assert booking_differences(original,result)["changed_booking_occurrences"]==0


@pytest.mark.parametrize("damage",["missing_coverage","duplicate_coverage","unknown_occurrence","wrong_contact_minutes",
    "wrong_offering","wrong_date","wrong_student_count","invalid_staff","missing_validation"])
def test_corrupt_historical_timetables_fail_independent_validation(archived_database_fixture,damage):
    evidence,enrolled,_=archived_database_fixture
    if damage=="missing_coverage":evidence.teaching["delivery_coverage"].pop()
    elif damage=="duplicate_coverage":evidence.teaching["delivery_coverage"].append(dict(evidence.teaching["delivery_coverage"][0]))
    elif damage=="unknown_occurrence":evidence.teaching["delivery_coverage"][0]["occurrence_reference"]="MISSING"
    elif damage=="wrong_contact_minutes":evidence.teaching["delivery_coverage"][0]["duration_minutes"]+=1
    elif damage=="wrong_offering":evidence.teaching["delivery_coverage"][0]["offering_reference"]="MISSING"
    elif damage=="wrong_date":evidence.teaching["deliveries"][0]["teaching_date"]=date(2000,1,1)
    elif damage=="wrong_student_count":evidence.teaching["deliveries"][0]["student_count"]+=1
    elif damage=="invalid_staff":evidence.teaching["deliveries"][0]["staff_reference"]="MISSING"
    elif damage=="missing_validation":evidence.teaching["validation_reports"].clear()
    with pytest.raises((ValueError,PersistenceConflict)):historical_draft(evidence,enrolled)
