"""Synthetic explicit classification, reproducible enrollment and delivery tests."""

from dataclasses import replace
import csv
import json

import pytest

from schedence_teaching.excel_import import VenueImportResult
from schedence_teaching.excel_import.staging import SourceLocation, SourceModuleRecord, SourceRecord, SourceTeamAssignment, WideImportResult
from schedence_teaching.pilot.allocations import SpecializationAllocations
from schedence_teaching.pilot.electives import (ElectiveAllocationPolicy, ElectiveInputError, ElectiveSelection, ModuleClassification,
    assess_elective_catalog, classification_template, generate_test_electives, prepare_enrolled_pp1, validate_elective_selections, select_enrolled_scope)
from schedence_teaching.pilot.elective_validation import validate_elective_result
from schedence_teaching.pilot.elective_io import (enrolled_demand_report, export_enrollment, read_classifications,
    read_selections, write_classifications, write_selections)
from schedence_teaching.pilot.online_calendar import configure_online_days
from schedence_teaching.pilot.pp1 import prepare_pp1
from schedence_teaching.pilot.pp1_run import export_pp1
from schedence_teaching.pilot.scopes import select_complete_scope
from schedence_teaching.pilot.students import CohortCount, GroupSizingPolicy
from schedence_teaching.schemas import StaffMember, TeachingRole, Venue
from schedence_teaching.solver import SolverInputError, TimetableValidationError, solve_timetable
from schedence_teaching.solver.attendance_inputs import DeliveryMode
from schedence_teaching.solver.evaluation import validate_evaluation_result

from test_pp1_allocations import configuration


@pytest.fixture
def elective_fixture():
    academic = WideImportResult(source_confirmation_reference="synthetic:source", lecture_hours_interpretation="ONE_CONTINUOUS_WEEKLY_LECTURE")
    academic.staff_members = [StaffMember(staff_id=f"SYN-LEC-{n}", staff_name="Synthetic", position="Lecturer", staff_type="ACADEMIC") for n in (1, 2)]
    classifications = []
    # Three electives, a compulsory module, a zero-contact project, and a Year 1 module.
    for row, (year, lecture, tutorial, lab, kind) in enumerate(((4,2,0,2,"ELECTIVE"), (4,2,1,0,"ELECTIVE"),
            (4,2,0,0,"ELECTIVE"), (4,1,0,0,"COMPULSORY"), (4,0,0,0,"PROJECT"), (1,2,0,0,"COMPULSORY")), 2):
        location = SourceLocation("synthetic-academic.xlsx", "Modules_183", row)
        academic.source_modules.append(SourceModuleRecord(location, dict(Lecture_Hours_Week=lecture, Tutorial_Hours_Week=tutorial,
            Workshop_Hours_Week=0, Lab_Hours_Week=lab), contract_fields=dict(module_code=f"SYN{row}", module_name=f"Synthetic module {row}",
                academic_year=year, semester=1, specialization="IT", degree_type="Synthetic")))
        for staff in academic.staff_members:
            academic.source_assignments.append(SourceTeamAssignment(location,"LIC_Staff_ID","LIC",staff.staff_id,"Synthetic",TeachingRole.LIC))
        if year == 4:
            classifications.append(ModuleClassification(source=location,specialization="IT",kind=kind,reference="synthetic:explicit-classification"))
    allocations = SpecializationAllocations(sizing=GroupSizingPolicy(), specialization_names={"IT":"Synthetic IT"})
    for year, stream, count in ((4,"WEEKDAY",131),(4,"WEEKEND",131),(1,"WEEKDAY",65)):
        allocations.cohorts.append(CohortCount(cohort_id=f"SYN-Y{year}-{stream}",academic_year=year,semester=1,stream=stream,
            student_count=count,specialization="IT",source=SourceLocation("synthetic-counts.xlsx","allocations",len(allocations.cohorts)+2)))
    venues = VenueImportResult()
    for identity, kind, size in (("SYN-HALL","Lecture Hall",120),("SYN-LAB","Computer Lab",60)):
        venues.venues.append(Venue(venue_id=identity,venue_name="Synthetic",venue_type=kind,capacity=size))
        venues.source_venues.append(SourceRecord(SourceLocation("synthetic-venues.xlsx","venues",2+len(venues.venues)),{}))
    config = configure_online_days(configuration(),weekday_online_day="WEDNESDAY",weekend_online_day="MONDAY",
        weekend_online_window=dict(start_minute=510,end_minute=1080))
    config.solver_config.max_time_seconds = 10.0
    batch = prepare_pp1(allocations,academic,venues,config)
    policy = ElectiveAllocationPolicy(reference="synthetic:test-allocation",random_seed=17)
    return batch, academic, classifications, policy


def enrolled_fixture(data):
    batch, academic, classes, policy = data
    selections = generate_test_electives(batch,academic,classes,policy)
    return prepare_enrolled_pp1(batch,academic,classes,selections,policy=policy)


def test_exactly_two_distinct_reproducible_electives_preserve_all_cohort_counts(elective_fixture):
    base,source,classes,policy = elective_fixture
    first = generate_test_electives(base,source,classes,policy)
    assert len(first) == 262 and len({row.student_id for row in first}) == 262
    assert all(len(set(row.offering_ids)) == 2 and row.student_id.startswith("PP1_TEST_STUDENT:") for row in first)
    assert first == generate_test_electives(base,source,list(reversed(classes)),policy)
    assert first != generate_test_electives(base,source,classes,policy.model_copy(update=dict(random_seed=18)))
    _,assessed = validate_elective_selections(base,source,classes,first)
    assert all(sum(row.cohort_id==key for row in first)==data["cohort"].student_count for key,data in assessed["cohorts"].items())


@pytest.mark.parametrize("identities", [("one",), ("one","one"), ("one","two","three")])
def test_elective_contract_rejects_wrong_selection_count_or_duplicates(identities):
    with pytest.raises(ValueError):ElectiveSelection(cohort_id="SYN",student_id="PP1_TEST_STUDENT:1",offering_ids=identities)


@pytest.mark.parametrize("change,code", [("missing","COHORT_STUDENT_COUNT_MISMATCH"),
    ("duplicate","DUPLICATE_STUDENT_REGISTRATION"),("ineligible","INELIGIBLE_ELECTIVE_SELECTION")])
def test_invalid_registration_references_and_counts_are_rejected(elective_fixture,change,code):
    base,source,classes,policy=elective_fixture
    selections=list(generate_test_electives(base,source,classes,policy))
    if change=="missing":selections.pop()
    if change=="duplicate":selections.append(selections[0])
    if change=="ineligible":selections[0]=selections[0].model_copy(update=dict(offering_ids=("unknown",selections[0].offering_ids[1])))
    with pytest.raises(ElectiveInputError) as error:validate_elective_selections(base,source,classes,selections)
    assert code in {row["code"] for row in error.value.diagnostics}


def test_unknown_classifications_never_become_electives_automatically(elective_fixture):
    base,source,_,policy=elective_fixture
    template=classification_template(source)
    assert all(row.kind.value=="UNKNOWN" for row in template)
    assessment=assess_elective_catalog(base,source,template)
    assert not assessment["valid"]
    with pytest.raises(ElectiveInputError):generate_test_electives(base,source,template,policy)


def test_compulsory_and_zero_contact_project_preserved_outside_two_elective_selections(elective_fixture):
    enrolled=enrolled_fixture(elective_fixture)
    report=enrolled_demand_report(enrolled)
    assert all(row["selected_elective_count"]==2 and 6<=row["corrected_attendance_hours"]<=8 for row in report)
    assert all(row["original_catalog_application_hours"]==10 for row in report)
    assert all(row["shortfall_hours"]==row["physical_shortfall_hours"]==0 for row in report)
    assert any(row["classification"]=="PROJECT" for row in enrolled.catalog_assessment["catalog"])
    source_rows={enrolled.batch.source_offerings[key]["row"] for values in enrolled.attendance_by_group.values() for key in values}
    assert 6 in source_rows  # Explicit project enrollment survives even with zero hours.


def test_attendance_groups_preserve_exact_memberships_caps_and_unique_enrollment_totals(elective_fixture):
    enrolled=enrolled_fixture(elective_fixture)
    groups={row.group_id:row for row in enrolled.batch.plan.groups}
    rosters={row.group_id:set(row.student_ids) for row in enrolled.group_memberships}
    assert all(len(rosters[row.group_id])==row.student_count for row in groups.values() if row.academic_year==4)
    for group in groups.values():
        assert group.student_count<= (60 if group.parent_group_id else 120)
        if group.parent_group_id:
            assert rosters[group.group_id]<=rosters[group.parent_group_id] if group.academic_year==4 else True
    assert enrolled.batch.plan.summary()["total_students"]==327
    assert enrolled.batch.plan.summary()["main_student_sum_is_attendance_enrollments_not_unique_students"]
    assert len(enrolled.batch.config.attendance_memberships)==len(rosters)


def test_non_year4_requirements_and_teams_are_preserved(elective_fixture):
    base,*_=elective_fixture
    before=base.prepared.input_snapshot.model_dump()
    enrolled=enrolled_fixture(elective_fixture)
    ids={row.group_id for row in base.plan.groups if row.academic_year==1}
    assert tuple(row for row in base.prepared.sessions if row.group_id in ids)==tuple(row for row in enrolled.batch.prepared.sessions if row.group_id in ids)
    assert base.prepared.input_snapshot.model_dump()==before
    old={(row.offering_id,row.staff_id,row.role) for row in base.prepared.input_snapshot.teaching_team_members}
    assert all((row.offering_id,row.staff_id,row.role) in old for row in enrolled.batch.prepared.input_snapshot.teaching_team_members)


def test_editable_csv_roundtrip_and_private_exports(elective_fixture,tmp_path):
    base,source,classes,policy=elective_fixture
    selections=generate_test_electives(base,source,classes,policy)
    write_classifications(tmp_path/"classes.csv",classes)
    assert read_classifications(tmp_path/"classes.csv")==tuple(classes)
    write_selections(tmp_path/"selections.csv",selections,base.plan.cohorts,policy.output_label)
    assert read_selections(tmp_path/"selections.csv",base.plan.cohorts,label=policy.output_label)==selections
    report=export_enrollment(enrolled_fixture(elective_fixture),tmp_path/"export")
    assert report["distinct_students"]==262 and report["elective_enrollment_entries"]==524
    with (tmp_path/"export/corrected-weekend-capacity.csv").open(encoding="utf-8",newline="") as handle:
        assert all(row["stream"]=="WEEKEND" for row in csv.DictReader(handle))
    with pytest.raises(FileExistsError):export_enrollment(enrolled_fixture(elective_fixture),tmp_path/"export")


def test_editable_pair_restrictions_are_enforced(elective_fixture):
    base,source,classes,policy=elective_fixture
    assessed=assess_elective_catalog(base,source,classes)
    restricted={key:(tuple(sorted(data["electives"])[:2]),) for key,data in assessed["cohorts"].items()}
    policy=policy.model_copy(update=dict(allowed_pairs_by_cohort=restricted))
    selections=generate_test_electives(base,source,classes,policy)
    assert all(tuple(sorted(row.offering_ids))==restricted[row.cohort_id][0] for row in selections)


def test_scope_projection_filters_rosters_and_preserves_unique_cohort_counts(elective_fixture):
    enrolled=enrolled_fixture(elective_fixture)
    scoped=select_complete_scope(enrolled.batch,{(4,1,"WEEKEND","IT")})
    assert scoped.plan.summary()["total_students"]==131
    assert all(row.group_id in scoped.group_specializations for row in scoped.config.attendance_memberships)
    assert scoped.config.delivery_calendar==enrolled.batch.config.delivery_calendar


def test_weekend_online_day_extends_only_online_window_and_weekday_uses_existing_window(elective_fixture):
    base,*_=elective_fixture
    rules=base.config.delivery_calendar.days
    assert [(r.day,r.stream.value) for r in rules if r.mode==DeliveryMode.ONLINE]==[("MONDAY","WEEKEND"),("WEDNESDAY","WEEKDAY")]
    monday=next(row for row in base.config.days if row.day=="MONDAY")
    assert monday.windows["WEEKEND"][0].end_minute==1080
    assert monday.windows["WEEKDAY"][0].end_minute==1050
    assert all(r.allowed_session_types=={"LECTURE"} for r in rules if r.mode==DeliveryMode.ONLINE)


@pytest.mark.parametrize("day", ["SATURDAY","UNKNOWN"])
def test_invalid_online_calendar_days_rejected(day):
    with pytest.raises(ValueError):configure_online_days(configuration(),weekday_online_day=day,weekend_online_day="MONDAY",weekend_online_window=dict(start_minute=510,end_minute=1080))


def test_joint_enrolled_timetable_has_complete_coverage_and_no_individual_clashes(elective_fixture,tmp_path):
    enrolled=enrolled_fixture(elective_fixture)
    batch=enrolled.batch
    result=solve_timetable(batch.prepared,batch.config,**batch.solver_inputs)
    assert result.status.value in {"OPTIMAL","FEASIBLE"}
    validate_evaluation_result(batch.prepared,batch.config,result,**batch.solver_inputs)
    assert len(result.schedule)==len(batch.prepared.sessions)
    for row in result.schedule:
        assert row.assigned_venue_id is None if row.delivery_mode==DeliveryMode.ONLINE else row.assigned_venue_id is not None
    report=export_pp1(batch,result,tmp_path/"timetable")
    assert report["complete_timetable_generated"] and report["unscheduled_sessions"]==0
    assert "explicit_student_membership_clashes" in result.validation.checks
    assert not result.combination_enabled


def test_roster_size_corruption_rejected_before_solving(elective_fixture):
    batch=enrolled_fixture(elective_fixture).batch
    config=batch.config.model_copy(deep=True)
    config.attendance_memberships[0].student_ids=config.attendance_memberships[0].student_ids[:-1]
    with pytest.raises(SolverInputError,match="complete and match"):
        solve_timetable(batch.prepared,config,**batch.solver_inputs)


def test_explicit_source_elective_labels_only_seed_classification(elective_fixture):
    _,source,_,_=elective_fixture
    source.source_modules[0].contract_fields["module_name"]="Synthetic (Elective)"
    template=classification_template(source)
    assert [row.kind.value for row in template].count("ELECTIVE")==1
    assert [row.kind.value for row in template].count("UNKNOWN")==4


@pytest.mark.parametrize("damage",["duplicate","unknown-row","wrong-specialization"])
def test_classification_references_are_validated(elective_fixture,damage):
    base,source,classes,_=elective_fixture
    classes=list(classes)
    if damage=="duplicate":classes.append(classes[0])
    if damage=="unknown-row":classes[0]=classes[0].model_copy(update=dict(source=SourceLocation("synthetic-academic.xlsx","Modules_183",999)))
    if damage=="wrong-specialization":classes[0]=classes[0].model_copy(update=dict(specialization="UNKNOWN"))
    with pytest.raises(ValueError):assess_elective_catalog(base,source,classes)


def test_scope_selection_preserves_zero_contact_project_catalog_and_assignments(elective_fixture):
    base,source,classes,policy=elective_fixture
    scoped=select_complete_scope(base,{(4,1,"WEEKEND","IT")},retain_applicable_catalog=True)
    assessed=assess_elective_catalog(scoped,source,classes)
    assert any(row[1].value=="PROJECT" for data in assessed["cohorts"].values() for row in data["applicable"])
    selections=generate_test_electives(scoped,source,classes,policy)
    enrolled=prepare_enrolled_pp1(scoped,source,classes,selections,policy=policy)
    assert enrolled.batch.plan.summary()["total_students"]==131
    assert any(row["classification"]=="PROJECT" for row in enrolled.catalog_assessment["catalog"])


def test_no_lab_groups_are_invented_for_modules_without_lab_demand(elective_fixture):
    enrolled=enrolled_fixture(elective_fixture)
    for group in enrolled.batch.plan.groups:
        if group.academic_year!=4 or group.parent_group_id is None:continue
        identity=next(iter(enrolled.attendance_by_group[group.group_id]))
        assert enrolled.batch.source_offerings[identity]["row"]==2


def test_full_registration_validation_and_filtered_scope_validation(elective_fixture):
    enrolled=enrolled_fixture(elective_fixture)
    scoped=select_enrolled_scope(enrolled,{(4,1,"WEEKEND","IT")},max_time_seconds=10.0)
    result=solve_timetable(scoped.batch.prepared,scoped.batch.config,**scoped.batch.solver_inputs)
    report=validate_elective_result(scoped,result)
    assert report["students_checked"]==131 and report["student_clashes"]==0
    assert scoped.batch.plan.summary()["total_students"]==131
    corrupt_config=scoped.batch.config.model_copy(deep=True)
    corrupt_config.attendance_memberships=corrupt_config.attendance_memberships[:-1]
    altered=replace(scoped,batch=replace(scoped.batch,config=corrupt_config))
    with pytest.raises(ValueError,match="original enrollment"):
        validate_elective_result(altered,result)


@pytest.mark.parametrize("damage",["scope","label","extra-values"])
def test_editable_registration_csv_rejects_scope_and_provenance_changes(elective_fixture,tmp_path,damage):
    base,source,classes,policy=elective_fixture
    path=tmp_path/"registrations.csv"
    selections=generate_test_electives(base,source,classes,policy)
    write_selections(path,selections,base.plan.cohorts,policy.output_label)
    with path.open(encoding="utf-8",newline="") as handle:rows=list(csv.reader(handle))
    if damage=="scope":rows[1][4]="WEEKEND" if rows[1][4]=="WEEKDAY" else "WEEKDAY"
    if damage=="label":rows[1][0]="SUPPLIED_ELECTIVE_REGISTRATIONS"
    if damage=="extra-values":rows[1].append("unexpected")
    with path.open("w",encoding="utf-8",newline="") as handle:csv.writer(handle).writerows(rows)
    with pytest.raises(ValueError):read_selections(path,base.plan.cohorts,label=policy.output_label)
