"""Build a local reviewed ID crosswalk from read-only, source-verified evidence.

No database connection or apply mode. Exceptions are scoped to exact existing
IDs and identities; other name/code differences remain unresolved.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from plan_existing_neon import CURRENT, NEON, PRIVATE

IDS = {
    "IT3060": "9d1bf5bd-dd24-4e16-95f4-91988437f8f7",
    "IE3030": "358eb637-c63d-40fb-83a6-958565563c46",
    "IT3061": "ba735af7-648e-4951-94ac-fa68ed69008d",
    "IT2100": "956bed92-6cc5-4b16-9e22-58c1a92b66ac",
}
SEMINAR_ID = "45ca97cb-bf9b-4b01-8ec5-77808a8aeed9"


def credits(row):
    value = row["safe_source_fields"].get("Module_Credits")
    if type(value) is not int or value <= 0:
        raise ValueError("Verified source credits are missing/invalid")
    return value


def programs(row):
    return sorted(p["code"] for p in row["programs"])


def valid_source(row):
    return (row["institution_id"] == CURRENT and row["provenance_kind"] == "UNIVERSITY_SOURCED"
            and row["local_source_sha256_matches"] is True
            and row["original_workbook_row_check"]["verified"] is True
            and row["safe_source_fields"]["Module_Code"] == row["module_code"]
            and row["safe_source_fields"]["Module_Name"] == row["module_name"]
            and bool(programs(row)))


def build_review(evidence, preflight):
    if (evidence["institution_id"] != CURRENT or evidence["neon_target"] != NEON
            or evidence["exam_dataset_owner"] != CURRENT or preflight["canonical_institution"] != CURRENT):
        raise ValueError("Evidence does not belong to the canonical existing database")
    academic = {row["id"]: row for row in evidence["academic"]}
    exams = {row["module_code"]: row for row in evidence["examination"]}
    if len(academic) != len(evidence["academic"]) or len(exams) != len(evidence["examination"]):
        raise ValueError("Duplicate evidence identities require review")
    if not (set(IDS.values()) | {SEMINAR_ID}).issubset(academic):
        raise ValueError("A reviewed existing Academic ID is missing")
    mappings = []
    for code, identifier in IDS.items():
        row = academic[identifier]
        if row["module_code"] != code or not valid_source(row):
            raise ValueError("Academic source identity failed verification: " + code)
        exam = exams.get(code)
        item = {"academic_id": identifier, "academic_code": code, "academic_name": row["module_name"],
                "institution_id": CURRENT, "academic_year": row["academic_year"], "semester": row["semester"],
                "degree_type": row["degree_type"], "credits": credits(row), "programs": programs(row),
                "source_id": row["source_id"], "source_row": row["source_row"], "source_sha256": row["source_sha256"],
                "exam_id": None, "exam_code": None, "exam_name": None}
        if code == "IT3060":
            if exam is not None:
                raise ValueError("New Exam reference for Academic-only module requires review")
            if (row["module_name"], row["academic_year"], row["semester"], credits(row), programs(row)) != (
                    "Human Computer Interaction", 3, 1, 3, ["DS", "IT"]):
                raise ValueError("Academic-only identity changed")
            item["decision"] = "VERIFIED_ACADEMIC_ONLY"
        else:
            if exam is None or exam["id"] != code or exam["module_code"] != code:
                raise ValueError("Exact existing Exam code missing")
            if (row["academic_year"], row["semester"], credits(row), programs(row)) != (
                    exam["academic_year"], exam["semester"], exam["credits"], sorted(exam["programs"])):
                raise ValueError("Academic identity/credits/program differs: " + code)
            if code in {"IE3030", "IT3061"}:
                if row["module_name"].casefold() != exam["module_name"].casefold():
                    raise ValueError("Difference is not capitalization-only: " + code)
                item["decision"] = "VERIFIED_CAPITALIZATION_ONLY_EQUIVALENCE"
            else:
                if row["module_name"] != "Probability and Statistics" or row["module_name"] != exam["module_name"]:
                    raise ValueError("IT2100 exact named identity does not match")
                alternatives = [r for r in academic.values() if r["module_code"] == code and valid_source(r)
                                and (r["module_name"],r["academic_year"],r["semester"],credits(r),programs(r)) ==
                                (exam["module_name"],exam["academic_year"],exam["semester"],exam["credits"],sorted(exam["programs"]))]
                if len(alternatives) != 1 or alternatives[0]["id"] != identifier:
                    raise ValueError("IT2100 is not uniquely resolved by actual details")
                item["decision"] = "VERIFIED_EXACT_IDENTITY_DESPITE_CODE_COLLISION"
            item.update(exam_id=exam["id"], exam_code=exam["module_code"], exam_name=exam["module_name"])
        mappings.append(item)
    seminar = academic[SEMINAR_ID]
    if not valid_source(seminar) or seminar["module_code"] != "IT2100":
        raise ValueError("Held duplicate source changed")
    baseline = preflight["teaching_to_exam_modules"]
    local = preflight["local_to_neon"]["modules"]
    if local["missing"] or local["conflicting"] or local["ambiguous"] or baseline["ambiguous"]:
        raise ValueError("Unresolved source-to-owner mapping")
    by_code = {row["academic_code"]: row for row in mappings}
    unresolved = []
    equivalent = academic_only = 0
    for detail in baseline["details"]:
        mapped = by_code.get(detail["key"])
        if (mapped and detail["status"] == "missing" and mapped["decision"] == "VERIFIED_ACADEMIC_ONLY"):
            academic_only += 1
        elif (mapped and detail["status"] == "conflicting"
              and detail["differing_fields"] == ["name_hash"]
              and mapped["decision"] == "VERIFIED_CAPITALIZATION_ONLY_EQUIVALENCE"):
            equivalent += 1
        else:
            unresolved.append(detail)
    count = baseline["reusable"] + equivalent + academic_only
    passed = not unresolved and count == baseline["source_distinct_records"]
    result = {"format": "schedence-reviewed-module-mappings/v1", "institution_id": CURRENT,
              "target": NEON, "mappings": mappings,
              "held_records": [{"academic_id": SEMINAR_ID, "module_code": "IT2100", "name": seminar["module_name"],
                                "credits": credits(seminar), "programs": programs(seminar),
                                "decision": "NO_EXAM_MAPPING_DO_NOT_MERGE_OR_RECODE",
                                "unresolved": "Correct official code, if any, needs curriculum-owner confirmation"}],
              "module_gate": {"scope": "Previously inspected 91 local Teaching module definitions; not all future modules",
                              "status": "PASS" if passed else "BLOCKED", "exact_exam_matches": baseline["reusable"],
                              "reviewed_case_equivalents": equivalent, "academic_only": academic_only,
                              "verified_total": count, "source_total": baseline["source_distinct_records"],
                              "unresolved_current_scope": unresolved},
              "migration_gate": "NOT_PASSED: backups, target schema/permissions, experimental-data exclusions and a fresh full dry-run remain",
              "database_changes": 0,
              "other_unresolved": "IT3080/IT4100/IE4080 discrepancies and 28 Exam-only codes remain outside this reviewed local scope; do not auto-import or merge"}
    result["sha256"] = hashlib.sha256(json.dumps(result,sort_keys=True).encode()).hexdigest()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence",required=True)
    parser.add_argument("--preflight",required=True)
    parser.add_argument("--output",required=True)
    args = parser.parse_args()
    result = build_review(json.loads(Path(args.evidence).read_text(encoding="utf-8")),
                          json.loads(Path(args.preflight).read_text(encoding="utf-8")))
    output = Path(args.output).resolve()
    expected = Path(__file__).resolve().parents[1]/"database/teaching_shared/reviewed_module_mappings.v1.json"
    if output != expected.resolve() or output.exists():
        parser.error("Use the new versioned reviewed crosswalk path; existing evidence cannot be overwritten")
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open("x",encoding="utf-8") as stream:json.dump(result,stream,indent=2,ensure_ascii=False)
    print(json.dumps(result["module_gate"],indent=2))


if __name__ == "__main__":
    main()
