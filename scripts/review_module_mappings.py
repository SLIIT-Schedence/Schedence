"""Read-only evidence for four explicitly reviewed module identities. No apply mode."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from dev_seed import connect, database_identity, read_url
from plan_existing_neon import CURRENT, NEON, PRIVATE, tenant
from compare_academic_records import records

CODES = ("IT3060", "IE3030", "IT3061", "IT2100")


def collect(conn):
    if database_identity(conn) != NEON:
        raise ValueError("Wrong existing Neon target")
    tenant(conn,CURRENT)
    owner=conn.execute("SELECT institution_id::text FROM examinations.exam_dataset_owner").fetchall()
    if owner != [(CURRENT,)]:
        raise ValueError("Examination dataset is not owned by the canonical institution")
    academic=records(conn,"""
        SELECT m.id::text,m.institution_id::text,m.module_code,m.module_name,m.academic_year,m.semester,m.degree_type,
               m.lecture_minutes,m.tutorial_minutes,m.workshop_minutes,m.lab_minutes,
               m.source_id::text,m.source_row,m.worksheet,m.record_key,
               s.filename,s.sha256 AS source_sha256,s.provenance_kind,
               coalesce((SELECT jsonb_object_agg(k,v) FROM jsonb_each(m.source_values) kv(k,v)
                         WHERE k ILIKE '%%credit%%' OR k IN ('Data_Status','Module_Code','Module_Name','Degree_Type','Academic_Year','Semester','Specialization')),'{}'::jsonb) AS safe_source_fields,
               coalesce((SELECT jsonb_agg(jsonb_build_object('id',p.id,'code',p.code,'name',p.name) ORDER BY p.code)
                         FROM academic.module_specializations x JOIN academic.specializations p
                         ON p.institution_id=x.institution_id AND p.id=x.specialization_id
                         WHERE x.institution_id=m.institution_id AND x.module_id=m.id),'[]'::jsonb) AS programs,
               (SELECT count(*) FROM academic.module_teaching_teams a WHERE a.institution_id=m.institution_id AND a.module_id=m.id) AS teaching_team_rows
        FROM academic.modules m JOIN academic.source_datasets s ON s.institution_id=m.institution_id AND s.id=m.source_id
        WHERE m.institution_id=%s AND m.module_code=ANY(%s) ORDER BY m.module_code,m.source_row
        """,(CURRENT,list(CODES)))
    exams=records(conn,"""
        SELECT m.module_code AS id,m.module_code,m.module_name,m.academic_year,m.semester,m.credits,m.exam_type,
               m.duration_minutes,m.has_final_exam,m.has_mid_exam,m.mid_duration_minutes,m.delivery_mode,
               coalesce((SELECT jsonb_agg(x.specialization ORDER BY x.specialization)
                         FROM examinations.seed_module_specializations x WHERE x.module_code=m.module_code),'[]'::jsonb) AS programs,
               (SELECT count(*) FROM examinations.seed_registrations r WHERE r.module_code=m.module_code) AS registration_count,
               coalesce((SELECT jsonb_agg(jsonb_build_object('period_code',p.period_code,'candidate_scope',p.candidate_scope) ORDER BY p.period_code)
                         FROM examinations.seed_exam_period_modules p WHERE p.module_code=m.module_code),'[]'::jsonb) AS period_references
        FROM examinations.seed_modules m WHERE m.module_code=ANY(%s) ORDER BY m.module_code
        """,(list(CODES),))
    saved=records(conn,"""
        SELECT s.module_code,s.module_name,s.academic_year,s.semester,t.status,count(*) AS session_count
        FROM examinations.exam_timetable_sessions s JOIN examinations.exam_timetables t USING(timetable_id)
        WHERE t.institution_id=%s AND s.module_code=ANY(%s)
        GROUP BY s.module_code,s.module_name,s.academic_year,s.semester,t.status ORDER BY s.module_code,t.status
        """,(CURRENT,list(CODES)))
    credit_columns=records(conn,"SELECT column_name,data_type FROM information_schema.columns "
        "WHERE table_schema='academic' AND table_name='modules' AND column_name ILIKE '%%credit%%' ORDER BY ordinal_position")
    exam_fks=records(conn,"""
        SELECT conrelid::regclass::text AS child,conname,pg_get_constraintdef(oid) AS definition
        FROM pg_constraint WHERE contype='f' AND confrelid='examinations.seed_modules'::regclass ORDER BY conname
        """)
    local_root=Path(__file__).resolve().parents[1]/"services/teaching-service/local-data"
    import hashlib
    for row in academic:
        file=(local_root/row["filename"]).resolve()
        row["local_source_available"]=file.is_relative_to(local_root.resolve()) and file.is_file()
        row["local_source_sha256_matches"]=None
        if row["local_source_available"]:
            with file.open("rb") as stream:
                row["local_source_sha256_matches"]=hashlib.file_digest(stream,"sha256").hexdigest()==row["source_sha256"]
    return {"created_utc":datetime.now(timezone.utc).isoformat(),"mode":"READ_ONLY",
            "institution_id":CURRENT,"neon_target":NEON,"exam_dataset_owner":CURRENT,
            "academic":academic,"examination":exams,"saved_exam_references":saved,
            "academic_credit_columns":credit_columns,"exam_module_foreign_keys":exam_fks}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",required=True)
    args=parser.parse_args()
    output=Path(args.output).resolve()
    if not output.is_relative_to(PRIVATE.resolve()) or output.exists():
        parser.error("A new private scripts/neon-review/ evidence path is required")
    root=Path(__file__).resolve().parents[1]
    with connect(read_url(root/".env","DIRECT_DATABASE_URL")) as conn:
        report=collect(conn)
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open("x",encoding="utf-8") as stream:json.dump(report,stream,indent=2,ensure_ascii=False)
    print(json.dumps(report,indent=2,ensure_ascii=False))


if __name__=="__main__":
    main()
