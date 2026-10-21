"""Generate review-only SQL for teaching_shared; never connects or applies SQL."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

CURRENT = "84651d6d-0c8a-470a-a798-5220666fd367"
OWNER = "schedence_teaching_shared_owner"
READER = "schedence_teaching_shared_reader"
DRAFT_READER = "schedence_teaching_shared_draft_reader"
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "database/teaching_shared"
GATE = f"NULLIF(current_setting('schedence.institution_id',true),'')::uuid = '{CURRENT}'::uuid"


def scoped(alias):
    return f"{alias}.institution_id = '{CURRENT}'::uuid AND {GATE}"


def simple(table, columns, *, where=""):
    fields = columns.split()
    query = f"SELECT {', '.join('t.'+c for c in fields)} FROM {table} t WHERE {scoped('t')}"
    return {"query": query + (" AND " + where if where else ""), "sources": {table: fields}}


CORE = {
    "academic_staff": simple("academic.staff", "id institution_id staff_number full_name academic_rank employment_status", where="t.employment_status='ACTIVE'"),
    "departments": simple("academic.departments", "id institution_id name source_id"),
    "academic_positions": simple("academic.administrative_position_types", "id institution_id code name unit_type is_active", where="t.is_active"),
    "modules": simple("academic.modules", "id institution_id module_code module_name academic_year semester degree_type lecture_minutes tutorial_minutes workshop_minutes lab_minutes source_id"),
    "specializations": simple("academic.specializations", "id institution_id code name source_id"),
    "module_specializations": simple("academic.module_specializations", "id institution_id module_id specialization_id"),
    "venues": simple("resource.venues", "id institution_id venue_code venue_name venue_type capacity building floor lab_specialization source_id"),
}
CORE["staff_teaching_profiles"] = {
    "query": f"SELECT p.id,p.institution_id,s.id AS staff_id,p.staff_number,p.staff_type,p.department_id "
             f"FROM academic.staff_teaching_profiles p JOIN academic.staff s "
             f"ON s.institution_id=p.institution_id AND s.staff_number=p.staff_number "
             f"WHERE {scoped('p')} AND s.employment_status='ACTIVE'",
    "sources": {"academic.staff_teaching_profiles": "id institution_id staff_number staff_type department_id".split(),
                "academic.staff": "id institution_id staff_number employment_status".split()},
}
CORE["teaching_assignments"] = {
    "query": f"SELECT a.id,a.institution_id,a.module_id,s.id AS staff_id,a.staff_number,a.role,a.source_slot "
             f"FROM academic.module_teaching_teams a JOIN academic.staff s "
             f"ON s.institution_id=a.institution_id AND s.staff_number=a.staff_number "
             f"WHERE {scoped('a')} AND s.employment_status='ACTIVE'",
    "sources": {"academic.module_teaching_teams": "id institution_id module_id staff_number role source_slot".split(),
                "academic.staff": "id institution_id staff_number employment_status".split()},
}
CORE["cohort_totals"] = {
    "query": f"SELECT c.id,c.institution_id,c.cohort_reference,c.academic_year,c.semester,c.stream,c.student_count,"
             f"c.source_id,s.provenance_kind FROM academic.cohort_totals c JOIN academic.source_datasets s "
             f"ON s.institution_id=c.institution_id AND s.id=c.source_id WHERE {scoped('c')} "
             f"AND s.provenance_kind='UNIVERSITY_SOURCED'",
    "sources": {"academic.cohort_totals": "id institution_id cohort_reference academic_year semester stream student_count source_id".split(),
                "academic.source_datasets": "id institution_id provenance_kind".split()},
}
CORE["administrative_appointments"] = simple(
    "academic.staff_administrative_appointments",
    "id institution_id staff_id position_type_id unit_type unit_code effective_from effective_until admin_hours_override",
    where="t.ended_at IS NULL AND t.effective_from<=CURRENT_DATE AND (t.effective_until IS NULL OR t.effective_until>=CURRENT_DATE)")
CORE["administrative_appointments"]["sources"]["academic.staff_administrative_appointments"].append("ended_at")
CORE["administrative_workload_policies"] = {
    "query": f"SELECT w.id,p.institution_id,w.position_type_id,w.version,w.admin_hours_per_week,w.combination_rule,"
             f"w.cap_hours_per_week,w.effective_from,w.effective_until FROM academic.administrative_workload_policies w "
             f"JOIN academic.administrative_position_types p ON p.id=w.position_type_id WHERE {scoped('p')} "
             f"AND p.is_active AND w.effective_from<=CURRENT_DATE AND (w.effective_until IS NULL OR w.effective_until>=CURRENT_DATE)",
    "sources": {"academic.administrative_workload_policies": "id position_type_id version admin_hours_per_week combination_rule cap_hours_per_week effective_from effective_until".split(),
                "academic.administrative_position_types": "id institution_id is_active".split()},
}
CORE["examination_periods"] = {
    "query": f"SELECT o.institution_id,p.period_code,p.period_name,p.academic_year_label,p.semester,p.start_date,p.end_date,"
             f"p.day_start_time,p.day_end_time,p.include_saturdays,p.include_sundays,p.exam_kind "
             f"FROM examinations.seed_exam_periods p CROSS JOIN examinations.exam_dataset_owner o "
             f"WHERE o.singleton AND {scoped('o')}",
    "sources": {"examinations.seed_exam_periods": "period_code period_name academic_year_label semester start_date end_date day_start_time day_end_time include_saturdays include_sundays exam_kind".split(),
                "examinations.exam_dataset_owner": "singleton institution_id".split()},
}
CORE["published_exam_sessions"] = {
    "query": f"SELECT t.institution_id,t.timetable_id,t.revision,t.period_code,s.session_id,s.module_code,s.module_name,"
             f"s.academic_year,s.semester,s.starts_at,s.ends_at,s.duration_minutes,s.candidates "
             f"FROM examinations.exam_timetables t JOIN examinations.exam_timetable_sessions s "
             f"ON s.timetable_id=t.timetable_id WHERE {scoped('t')} AND t.status='PUBLISHED' AND t.validation_passed",
    "sources": {"examinations.exam_timetables": "institution_id timetable_id revision period_code status validation_passed".split(),
                "examinations.exam_timetable_sessions": "timetable_id session_id module_code module_name academic_year semester starts_at ends_at duration_minutes candidates".split()},
}
CORE["published_exam_rooms"] = {
    "query": f"SELECT t.institution_id,t.timetable_id,t.revision,r.session_id,r.venue_id,r.venue_name,r.venue_type,r.seats,r.exam_capacity "
             f"FROM examinations.exam_timetables t JOIN examinations.exam_timetable_rooms r "
             f"ON r.timetable_id=t.timetable_id WHERE {scoped('t')} AND t.status='PUBLISHED' AND t.validation_passed",
    "sources": {"examinations.exam_timetables": "institution_id timetable_id revision status validation_passed".split(),
                "examinations.exam_timetable_rooms": "timetable_id session_id venue_id venue_name venue_type seats exam_capacity".split()},
}

# Existing duplicate codes are preserved for review, never merged or selected
# arbitrarily. The default modules contract has one row per unambiguous code.
CORE["module_source_records"] = {"query": CORE["modules"]["query"],
                                 "sources": CORE["modules"]["sources"]}
CORE["modules"] = {"sources": CORE["modules"]["sources"],
    "query": CORE["modules"]["query"] +
    " AND NOT EXISTS(SELECT 1 FROM academic.modules other WHERE other.institution_id=t.institution_id "
    "AND other.module_code=t.module_code AND other.id<>t.id)"}
CORE["module_code_conflicts"] = {
    "query": f"SELECT t.institution_id,t.module_code,count(*) AS source_record_count,array_agg(t.id ORDER BY t.id) AS source_record_ids "
             f"FROM academic.modules t WHERE {scoped('t')} GROUP BY t.institution_id,t.module_code HAVING count(*)>1",
    "sources": {"academic.modules": "id institution_id module_code".split()},
}

# Deliberately separate grants: these are experimental estimates and validated
# drafts, never an official published timetable or university student master.
DRAFTS = {
    "experimental_student_groups": {
        "query": f"SELECT g.id,g.institution_id,g.allocation_id,g.plan_reference,g.local_group_reference,g.parent_id,g.student_count,"
                 f"a.cohort_total_id,a.specialization_id,'PP1_EXPERIMENTAL'::text AS data_classification "
                 f"FROM academic.pp1_student_groups g JOIN academic.pp1_specialization_allocations a "
                 f"ON a.institution_id=g.institution_id AND a.id=g.allocation_id WHERE {scoped('g')}",
        "sources": {"academic.pp1_student_groups": "id institution_id allocation_id plan_reference local_group_reference parent_id student_count".split(),
                    "academic.pp1_specialization_allocations": "id institution_id cohort_total_id specialization_id".split()},
    },
    "scheduling_inputs": simple("teaching.input_snapshots", "id institution_id schema_version content_sha256 academic_term_reference timezone_name calendar_label expected_occurrences created_at"),
    "validated_draft_sessions": {
        "query": f"SELECT d.id,d.institution_id,d.version_id,v.version_number,v.lifecycle_status,v.academic_complete,"
                 f"d.teaching_date,d.start_minute,d.end_minute,d.delivery_mode,d.staff_reference,d.venue_reference,d.student_count "
                 f"FROM teaching.deliveries d JOIN teaching.timetable_versions v "
                 f"ON v.institution_id=d.institution_id AND v.id=d.version_id WHERE {scoped('d')} "
                 f"AND v.lifecycle_status='DRAFT_VALIDATED' AND v.validation_passed AND v.solver_success",
        "sources": {"teaching.deliveries": "id institution_id version_id teaching_date start_minute end_minute delivery_mode staff_reference venue_reference student_count".split(),
                    "teaching.timetable_versions": "id institution_id version_number lifecycle_status academic_complete validation_passed solver_success".split()},
    },
    "staff_workload": {
        "query": f"SELECT d.institution_id,d.version_id,d.staff_reference,d.teaching_date,"
                 f"sum(d.end_minute-d.start_minute)::bigint AS assigned_draft_minutes,"
                 f"sum(d.end_minute-d.start_minute)/60.0 AS assigned_draft_hours "
                 f"FROM teaching.deliveries d JOIN teaching.timetable_versions v "
                 f"ON v.institution_id=d.institution_id AND v.id=d.version_id WHERE {scoped('d')} "
                 f"AND v.lifecycle_status='DRAFT_VALIDATED' AND v.validation_passed AND v.solver_success "
                 f"GROUP BY d.institution_id,d.version_id,d.staff_reference,d.teaching_date",
        "sources": {"teaching.deliveries": "institution_id version_id staff_reference teaching_date start_minute end_minute".split(),
                    "teaching.timetable_versions": "id institution_id lifecycle_status validation_passed solver_success".split()},
    },
}
DRAFTS["snapshot_planning_groups"] = {
    "query": f"SELECT s.institution_id,s.id AS snapshot_id,g->>'group_id' AS group_reference,"
             f"(g->>'academic_year')::integer AS academic_year,(g->>'semester')::integer AS semester,"
             f"g->>'stream' AS stream,(g->>'student_count')::integer AS student_count,"
             f"g->>'parent_group_id' AS parent_group_reference,s.calendar_label "
             f"FROM teaching.input_snapshots s CROSS JOIN LATERAL jsonb_array_elements("
             f"(s.payload->>'document_json')::jsonb#>'{{teaching_input,student_groups}}') g WHERE {scoped('s')}",
    "sources": {"teaching.input_snapshots": "id institution_id payload calendar_label".split()},
}


def quote(value):
    return "'" + value.replace("'", "''") + "'"


def artifact(views):
    return hashlib.sha256(json.dumps(views,sort_keys=True).encode()).hexdigest()


def migration(version, views):
    """A single transactional DO block: verified repeats return without writes."""
    role = READER if version == 1 else DRAFT_READER
    signature = artifact(views)
    ddl = []
    if version == 1:
        ddl += [f"CREATE ROLE {OWNER} NOLOGIN NOINHERIT NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION",
                f"CREATE ROLE {role} NOLOGIN NOINHERIT NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION",
                f"GRANT {OWNER} TO CURRENT_USER",
                f"CREATE SCHEMA teaching_shared AUTHORIZATION {OWNER}",
                "REVOKE ALL ON SCHEMA teaching_shared FROM PUBLIC",
                "CREATE TABLE teaching_shared.schema_migrations(version integer PRIMARY KEY,artifact_sha256 text NOT NULL,"
                "view_fingerprints jsonb NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp())",
                f"ALTER TABLE teaching_shared.schema_migrations OWNER TO {OWNER}"]
    else:
        ddl += [f"CREATE ROLE {role} NOLOGIN NOINHERIT NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION",
                f"GRANT {OWNER} TO CURRENT_USER"]
    sources = {}
    for spec in views.values():
        for table, columns in spec["sources"].items():
            sources.setdefault(table,set()).update(columns)
    for schema in sorted({table.split('.')[0] for table in sources}):
        ddl.append(f"GRANT USAGE ON SCHEMA {schema} TO {OWNER}")
    for table, columns in sorted(sources.items()):
        ddl.append(f"GRANT SELECT ({','.join(sorted(columns))}) ON {table} TO {OWNER}")
    ddl += [f"SET LOCAL ROLE {OWNER}"]
    for view, spec in views.items():
        ddl += [f"CREATE VIEW teaching_shared.{view} WITH (security_barrier=true,security_invoker=false) AS {spec['query']}",
                f"REVOKE ALL ON teaching_shared.{view} FROM PUBLIC",
                f"GRANT SELECT ON teaching_shared.{view} TO {role}"]
    ddl.append(f"GRANT USAGE ON SCHEMA teaching_shared TO {role}")
    viewlist = ",".join(quote(v) for v in views)
    ddl += [f"INSERT INTO teaching_shared.schema_migrations(version,artifact_sha256,view_fingerprints) "
            f"SELECT {version},{quote(signature)},jsonb_object_agg(c.relname,encode(sha256(convert_to(pg_get_viewdef(c.oid,true),'UTF8')),'hex')) "
            f"FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='teaching_shared' "
            f"AND c.relkind='v' AND c.relname IN ({viewlist})",
            "RESET ROLE", f"REVOKE {OWNER} FROM CURRENT_USER"]
    commands = "\n".join("    EXECUTE " + quote(command) + ";" for command in ddl)
    prerequisite = "" if version == 1 else f"""
    IF to_regclass('teaching_shared.schema_migrations') IS NULL THEN
        RAISE EXCEPTION 'Apply reviewed version 1 first';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM teaching_shared.schema_migrations WHERE version=1 AND artifact_sha256='{artifact(CORE)}') THEN
        RAISE EXCEPTION 'Unrecognized prerequisite migration';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='{OWNER}' AND NOT rolcanlogin AND NOT rolsuper
        AND NOT rolbypassrls AND NOT rolcreatedb AND NOT rolcreaterole AND NOT rolreplication) THEN
        RAISE EXCEPTION 'Shared view owner is not restricted';
    END IF;
"""
    fresh_guard = """
    IF to_regnamespace('teaching_shared') IS NOT NULL THEN
        RAISE EXCEPTION 'Unrecognized existing teaching_shared schema; no replacement permitted';
    END IF;
""" if version == 1 else ""
    return f"""-- REVIEW ONLY. Explicit approval required before running on Neon.
-- Existing source rows, constraints, policies and Alembic markers are never changed.
-- New operational ledger rows: exactly one for this version; academic rows inserted: zero.
BEGIN;
SET LOCAL search_path=pg_catalog;
SET LOCAL lock_timeout='3s';
SET LOCAL statement_timeout='30s';
SELECT pg_advisory_xact_lock(hashtext('schedence-teaching-shared-review'));
DO $migration$
DECLARE previous text; fingerprints jsonb; item record;
BEGIN
    IF current_database()<>'neondb'
       OR current_setting('neon.project_id',true) IS DISTINCT FROM 'super-leaf-21551515'
       OR current_setting('neon.branch_id',true) IS DISTINCT FROM 'br-dry-darkness-b549ci92' THEN
        RAISE EXCEPTION 'Wrong existing Neon target';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM identity.institutions WHERE id='{CURRENT}'::uuid AND code='SLIIT-MALABE' AND status='ACTIVE') THEN
        RAISE EXCEPTION 'Canonical active institution is missing';
    END IF;
    IF to_regclass('teaching_shared.schema_migrations') IS NOT NULL THEN
        SELECT artifact_sha256,view_fingerprints INTO previous,fingerprints
        FROM teaching_shared.schema_migrations WHERE version={version};
        IF previous IS NOT NULL THEN
            IF previous<>{quote(signature)} THEN RAISE EXCEPTION 'Migration artifact mismatch'; END IF;
            FOR item IN SELECT key,value FROM jsonb_each_text(fingerprints) LOOP
                IF to_regclass('teaching_shared.'||quote_ident(item.key)) IS NULL OR
                   encode(sha256(convert_to(pg_get_viewdef(to_regclass('teaching_shared.'||quote_ident(item.key)),true),'UTF8')),'hex')<>item.value THEN
                    RAISE EXCEPTION 'Installed view drift; no replacement permitted';
                END IF;
            END LOOP;
            RETURN; -- Identical re-run: no changes.
        END IF;
    END IF;
{prerequisite}{fresh_guard}
    IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname IN ({quote(role)}{','+quote(OWNER) if version == 1 else ''})) THEN
        RAISE EXCEPTION 'Reserved new role already exists; ownership review required';
    END IF;
{commands}
END
$migration$;
COMMIT;
"""


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    for version, title, views in ((1,"authoritative_catalog",CORE),(2,"experimental_and_draft_catalog",DRAFTS)):
        (OUT/f"v{version:03d}_{title}.sql").write_text(migration(version,views),encoding="utf-8")
    manifest={"institution_id":CURRENT,"versions":{
        "1":{"artifact_sha256":artifact(CORE),"views":CORE,"reader_role":READER},
        "2":{"artifact_sha256":artifact(DRAFTS),"views":DRAFTS,"reader_role":DRAFT_READER}}}
    (OUT/"view-contracts.json").write_text(json.dumps(manifest,indent=2)+"\n",encoding="utf-8")
    print(f"Prepared {len(CORE)} authoritative views and {len(DRAFTS)} explicitly labelled draft/experimental views. No database connection.")


if __name__=="__main__":
    main()
