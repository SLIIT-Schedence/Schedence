"""Evidence guards for the narrow, read-only reviewed module crosswalk."""
import copy
import json
import sys
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"scripts"))
from reviewed_module_mappings import build_review, CURRENT, NEON, SEMINAR_ID


@pytest.fixture
def inputs():
    review=json.loads((ROOT/"database/teaching_shared/reviewed_module_mappings.v1.json").read_text(encoding="utf-8"))
    academic=[]
    exams=[]
    for entry in review["mappings"]:
        row={"id":entry["academic_id"],"institution_id":CURRENT,"module_code":entry["academic_code"],
             "module_name":entry["academic_name"],"academic_year":entry["academic_year"],"semester":entry["semester"],
             "degree_type":entry["degree_type"],"programs":[{"code":p} for p in entry["programs"]],
             "safe_source_fields":{"Module_Credits":entry["credits"],"Module_Code":entry["academic_code"],"Module_Name":entry["academic_name"]},
             "source_id":entry["source_id"],"source_row":entry["source_row"],"source_sha256":entry["source_sha256"],
             "provenance_kind":"UNIVERSITY_SOURCED","local_source_sha256_matches":True,
             "original_workbook_row_check":{"verified":True}}
        academic.append(row)
        if entry["exam_code"]:
            exams.append({"id":entry["exam_id"],"module_code":entry["exam_code"],"module_name":entry["exam_name"],
                          "academic_year":entry["academic_year"],"semester":entry["semester"],
                          "credits":entry["credits"],"programs":entry["programs"]})
    held=copy.deepcopy(academic[-1])
    held.update(id=SEMINAR_ID,module_code="IT2100",module_name="Employability Skills Development - Seminar (Non-GPA)")
    held["safe_source_fields"]={"Module_Code":"IT2100","Module_Name":held["module_name"],"Module_Credits":1}
    held["programs"]=[{"code":p} for p in ["CYBER","DS","IM","ISE","IT","SE"]]
    academic.append(held)
    evidence={"institution_id":CURRENT,"neon_target":NEON,"exam_dataset_owner":CURRENT,"academic":academic,"examination":exams}
    preflight={"canonical_institution":CURRENT,"local_to_neon":{"modules":{"missing":0,"conflicting":0,"ambiguous":0}},
               "teaching_to_exam_modules":{"reusable":88,"source_distinct_records":91,"ambiguous":0,"details":[
                   {"key":"IE3030","status":"conflicting","differing_fields":["name_hash"]},
                   {"key":"IT3060","status":"missing","differing_fields":[]},
                   {"key":"IT3061","status":"conflicting","differing_fields":["name_hash"]}]}}
    return evidence,preflight


def test_review_resolves_only_current_scope_without_mutating_source(inputs):
    evidence,preflight=inputs
    before=copy.deepcopy(inputs)
    result=build_review(evidence,preflight)
    assert inputs==before
    assert result["module_gate"]["status"]=="PASS"
    assert result["module_gate"]["verified_total"]==91
    assert result["migration_gate"].startswith("NOT_PASSED")
    assert result["database_changes"]==0


def test_academic_only_record_has_no_invented_exam_identifier(inputs):
    result=build_review(*inputs)
    row=next(r for r in result["mappings"] if r["academic_code"]=="IT3060")
    assert row["exam_id"] is None and row["exam_code"] is None
    assert row["academic_id"]=="9d1bf5bd-dd24-4e16-95f4-91988437f8f7"


def test_it2100_selects_statistics_id_not_seminar(inputs):
    result=build_review(*inputs)
    row=next(r for r in result["mappings"] if r["exam_code"]=="IT2100")
    assert row["academic_id"]=="956bed92-6cc5-4b16-9e22-58c1a92b66ac"
    assert row["credits"]==3 and row["programs"]==["CSNE"]
    assert result["held_records"][0]["academic_id"]==SEMINAR_ID


@pytest.mark.parametrize("field,value",[("credits",1),("programs",["IT"]),("academic_year",4),("semester",1)])
def test_same_code_and_name_do_not_override_identity_disagreement(inputs,field,value):
    evidence,preflight=inputs
    exam=next(e for e in evidence["examination"] if e["module_code"]=="IT2100")
    exam[field]=value
    with pytest.raises(ValueError,match="identity/credits/program"):
        build_review(evidence,preflight)


def test_name_typo_is_not_accepted_as_capitalization(inputs):
    evidence,preflight=inputs
    next(e for e in evidence["examination"] if e["module_code"]=="IE3030")["module_name"]="Wide Area Network Design"
    with pytest.raises(ValueError,match="not capitalization-only"):
        build_review(evidence,preflight)


def test_source_checksum_and_original_row_must_both_verify(inputs):
    evidence,preflight=inputs
    evidence["academic"][0]["local_source_sha256_matches"]=False
    with pytest.raises(ValueError,match="source identity"):
        build_review(evidence,preflight)


def test_original_credit_row_mismatch_cannot_be_hidden_by_matching_file_hash(inputs):
    evidence,preflight=inputs
    evidence["academic"][0]["original_workbook_row_check"]["verified"]=False
    with pytest.raises(ValueError,match="source identity"):
        build_review(evidence,preflight)


def test_duplicate_evidence_id_cannot_overwrite_first_candidate(inputs):
    evidence,preflight=inputs
    evidence["academic"].append(copy.deepcopy(evidence["academic"][0]))
    with pytest.raises(ValueError,match="Duplicate evidence"):
        build_review(evidence,preflight)


def test_old_tenant_cannot_supply_same_named_course(inputs):
    evidence,preflight=inputs
    evidence["academic"][0]["institution_id"]="44dc54c1-35df-4e53-8fc0-95a7d911368b"
    with pytest.raises(ValueError,match="source identity"):
        build_review(evidence,preflight)


def test_unreviewed_name_difference_stays_blocked(inputs):
    evidence,preflight=inputs
    preflight["teaching_to_exam_modules"]["details"].append({"key":"IT3080","status":"conflicting","differing_fields":["name_hash"]})
    preflight["teaching_to_exam_modules"]["source_distinct_records"]+=1
    result=build_review(evidence,preflight)
    assert result["module_gate"]["status"]=="BLOCKED"


def test_new_exam_record_does_not_silently_change_academic_only_decision(inputs):
    evidence,preflight=inputs
    evidence["examination"].append({"id":"IT3060","module_code":"IT3060"})
    with pytest.raises(ValueError,match="New Exam reference"):
        build_review(evidence,preflight)


def test_missing_pinned_id_cannot_be_replaced_by_another_uuid(inputs):
    evidence,preflight=inputs
    evidence["academic"][0]["id"]="11111111-1111-1111-1111-111111111111"
    with pytest.raises(ValueError,match="existing Academic ID is missing"):
        build_review(evidence,preflight)
