"""Read-only comparison of canonical Neon masters, Exam inputs and local snapshot references.

No DDL/DML or source-record exports. Names are compared as server-side hashes.
Only counts, module codes and reference identifiers enter the private report.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from sqlalchemy.engine import make_url

from dev_seed import connect, read_url, database_identity
from plan_existing_neon import CURRENT, NEON, LOCAL, PRIVATE, tenant


def grouped(rows, key):
    result=defaultdict(list)
    for row in rows:
        result[row[key]].append(row)
    return result


def compare_records(source, existing, *, key, fields):
    """A code/number is only a candidate key. Attribute conflicts never auto-merge."""
    by_key=grouped(existing,key)
    result={"existing_rows":len(existing),"source_distinct_records":len(source),
            "reusable":0,"missing":0,"conflicting":0,"ambiguous":0,"details":[]}
    for row in source:
        candidates=by_key.get(row[key],[])
        matches=[candidate for candidate in candidates if all(candidate.get(f)==row.get(f) for f in fields)]
        if len(matches)==1:
            status="reusable"
        elif len(matches)>1:
            status="ambiguous"
        elif candidates:
            status="conflicting"
        else:
            status="missing"
        result[status]+=1
        if status!="reusable":
            result["details"].append({"key":row[key],"status":status,
                "candidate_ids":[r.get("id") for r in candidates],
                "differing_fields":sorted({f for c in candidates for f in fields if c.get(f)!=row.get(f)})})
    return result


def records(conn, query, params=()):
    cursor=conn.execute(query,params)
    names=[column.name for column in cursor.description]
    return [dict(zip(names,row)) for row in cursor.fetchall()]


def named_hash(expression):
    # No lossy normalization: presentation differences require manual review too.
    return f"encode(sha256(convert_to(({expression})::text,'UTF8')),'hex')"


def snapshot_rows(conn, path, projection):
    return records(conn,
        "WITH docs AS (SELECT (payload->>'document_json')::jsonb AS d "
        "FROM teaching.input_snapshots WHERE institution_id=%s) "
        "SELECT DISTINCT " + projection + " FROM docs "
        "CROSS JOIN LATERAL jsonb_array_elements(coalesce(d#>'{"+path+"}','[]'::jsonb)) e "
        "WHERE d->>'institution_id'=%s",(CURRENT,CURRENT))


def run(neon_url,local_url):
    with connect(neon_url) as neon,connect(local_url) as local:
        if database_identity(neon)!=NEON or database_identity(local)!=LOCAL:
            raise ValueError("Unexpected database identity")
        tenant(neon,CURRENT);tenant(local,CURRENT)
        mods=records(neon,"SELECT id::text,module_code,"+named_hash("module_name")+
            " AS name_hash,academic_year,semester,degree_type FROM academic.modules WHERE institution_id=%s",(CURRENT,))
        exams=records(neon,"SELECT module_code AS id,module_code,"+named_hash("module_name")+
            " AS name_hash,academic_year,semester FROM examinations.seed_modules "
            "WHERE EXISTS(SELECT 1 FROM examinations.exam_dataset_owner WHERE institution_id=%s)",(CURRENT,))
        localmods=snapshot_rows(local,"teaching_input,module_offerings",
            "e->>'module_code' AS module_code,"+named_hash("e->>'module_name'")+
            " AS name_hash,(e->>'academic_year')::int AS academic_year,(e->>'semester')::int AS semester,e->>'degree_type' AS degree_type")
        module_result=compare_records(localmods,mods,key="module_code",fields=("name_hash","academic_year","semester","degree_type"))
        exam_result=compare_records(exams,mods,key="module_code",fields=("name_hash","academic_year","semester"))
        # Examination codes are the reference namespace; never seed or rename
        # them from Academic-only/local offerings. Report unresolved mappings.
        teaching_exam_result=compare_records(localmods,exams,key="module_code",fields=("name_hash","academic_year","semester"))
        academic_by_code=grouped(mods,"module_code")
        duplicate_codes={code:[r["id"] for r in rows] for code,rows in academic_by_code.items() if len(rows)>1}
        staff=records(neon,"SELECT s.id::text,s.staff_number,"+named_hash("s.full_name")+
            " AS name_hash,p.staff_type,"+named_hash("p.source_position")+" AS position_hash,"+
            named_hash("d.name")+" AS department_hash FROM academic.staff s "
            "LEFT JOIN academic.staff_teaching_profiles p ON p.institution_id=s.institution_id AND p.staff_number=s.staff_number "
            "LEFT JOIN academic.departments d ON d.institution_id=p.institution_id AND d.id=p.department_id "
            "WHERE s.institution_id=%s",(CURRENT,))
        localstaff=snapshot_rows(local,"teaching_input,staff_members","e->>'staff_id' AS staff_number,"+
            named_hash("e->>'staff_name'")+" AS name_hash,e->>'staff_type' AS staff_type,"+
            named_hash("e->>'position'")+" AS position_hash,"+named_hash("e->>'department'")+" AS department_hash")
        staff_result=compare_records(localstaff,staff,key="staff_number",fields=("name_hash","staff_type","position_hash","department_hash"))
        # Department names are only used for comparison; no source names are exported.
        departments=records(neon,"SELECT id::text,"+named_hash("name")+
            " AS name_hash FROM academic.departments WHERE institution_id=%s",(CURRENT,))
        localdeps=snapshot_rows(local,"teaching_input,staff_members",named_hash("e->>'department'")+" AS name_hash")
        localdeps=[r for r in localdeps if r["name_hash"] is not None]
        dep_result=compare_records(localdeps,departments,key="name_hash",fields=())
        venues=records(neon,"SELECT id::text,venue_code,capacity,venue_type,building,floor,lab_specialization,"+named_hash("venue_name")+
            " AS name_hash FROM resource.venues WHERE institution_id=%s",(CURRENT,))
        localvenues=snapshot_rows(local,"contexts,venue_input,venues","e->>'venue_id' AS venue_code,"+
            named_hash("e->>'venue_name'")+" AS name_hash,(e->>'capacity')::int AS capacity,"
            "e->>'venue_type' AS venue_type,e->>'building' AS building,e->'floor' AS floor,e->>'lab_specialization' AS lab_specialization")
        venue_result=compare_records(localvenues,venues,key="venue_code",fields=("name_hash","capacity","venue_type","building","floor","lab_specialization"))
        examvenues=records(neon,"SELECT venue_id AS venue_code,normal_capacity AS capacity,"+named_hash("venue_name")+
            " AS name_hash FROM examinations.seed_venues WHERE EXISTS(SELECT 1 FROM examinations.exam_dataset_owner WHERE institution_id=%s)",(CURRENT,))
        exam_venue_result=compare_records(examvenues,venues,key="venue_code",fields=("name_hash","capacity"))
        groups=records(neon,"SELECT g.id::text,g.local_group_reference AS group_id,g.student_count,p.local_group_reference AS parent_group_id,"+
            "c.academic_year,c.semester,c.stream FROM academic.pp1_student_groups g "
            "JOIN academic.pp1_specialization_allocations a ON a.institution_id=g.institution_id AND a.id=g.allocation_id "
            "JOIN academic.cohort_totals c ON c.institution_id=a.institution_id AND c.id=a.cohort_total_id "
            "LEFT JOIN academic.pp1_student_groups p ON p.institution_id=g.institution_id AND p.id=g.parent_id "
            "WHERE g.institution_id=%s",(CURRENT,))
        localgroups=snapshot_rows(local,"teaching_input,student_groups","e->>'group_id' AS group_id,"
            "(e->>'student_count')::int AS student_count,(e->>'academic_year')::int AS academic_year,"
            "(e->>'semester')::int AS semester,e->>'stream' AS stream,e->>'parent_group_id' AS parent_group_id")
        group_result=compare_records(localgroups,groups,key="group_id",fields=("student_count","academic_year","semester","stream","parent_group_id"))
        # Validate recorded authoritative IDs rather than guessing code/UUID equivalence.
        references=snapshot_rows(local,"external_references",
            "e->>'record_kind' AS kind,e->>'authoritative_id' AS id")
        targets={"STAFF":{r["id"] for r in staff},"MODULE_SOURCE_ROW":{r["id"] for r in mods},"VENUE":{r["id"] for r in venues}}
        refs={}
        for kind,rows in grouped(references,"kind").items():
            refs[kind]={"distinct_references":len(rows),"missing_ids":sum(r["id"] not in targets.get(kind,set()) for r in rows)}
        report={"mode":"READ_ONLY","canonical_institution":CURRENT,
            "local_to_neon":{"modules":module_result,"staff":staff_result,"departments":dep_result,"venues":venue_result,"planning_groups":group_result},
            "exam_to_academic_modules":exam_result,"exam_to_resource_venues":exam_venue_result,
            "teaching_to_exam_modules":teaching_exam_result,
            "existing_module_duplicate_codes":duplicate_codes,"authoritative_id_references":refs,
            "academic_only_module_codes":sorted(set(academic_by_code)-{r["module_code"] for r in exams}),
            "exam_only_module_codes":sorted({r["module_code"] for r in exams}-set(academic_by_code)),
            "scope_note":"Planning groups are experimental derived references, not university master groups. Missing derived groups are held, never automatically inserted."}
        # Staff/department identifiers are opaque-hashed before saving any conflicts.
        import hashlib
        for key in ("staff","departments"):
            for detail in report["local_to_neon"][key]["details"]:
                detail["key"]=hashlib.sha256(detail["key"].encode()).hexdigest()
        return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",required=True)
    args=parser.parse_args()
    output=Path(args.output).resolve()
    if not output.is_relative_to(PRIVATE.resolve()) or output.exists():
        parser.error("A new ignored scripts/neon-review/ report path is required")
    root=Path(__file__).resolve().parents[1]
    cfg=json.loads((root/"services/teaching-service/local-data/persistence-preview/step12b4-reviewed/isolated-database.json").read_text())
    local=make_url(cfg["admin_url"])
    if (local.host,local.port,local.database)!=("127.0.0.1",52870,LOCAL["database"]):
        parser.error("Wrong isolated source")
    report=run(read_url(root/".env","DIRECT_DATABASE_URL"),local)
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open("x",encoding="utf-8") as f:json.dump(report,f,indent=2)
    print(json.dumps({"local_to_neon":{k:{kk:vv for kk,vv in v.items() if kk!="details"} for k,v in report["local_to_neon"].items()},
        "exam_to_academic_modules":{k:v for k,v in report["exam_to_academic_modules"].items() if k!="details"},
        "exam_to_resource_venues":{k:v for k,v in report["exam_to_resource_venues"].items() if k!="details"},
        "teaching_to_exam_modules":{k:v for k,v in report["teaching_to_exam_modules"].items() if k!="details"},
        "duplicate_module_codes":list(report["existing_module_duplicate_codes"]),"authoritative_id_references":report["authoritative_id_references"]},indent=2))


if __name__=="__main__":
    main()
