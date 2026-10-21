-- REVIEW ONLY. Explicit approval required before running on Neon.
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
    IF NOT EXISTS(SELECT 1 FROM identity.institutions WHERE id='84651d6d-0c8a-470a-a798-5220666fd367'::uuid AND code='SLIIT-MALABE' AND status='ACTIVE') THEN
        RAISE EXCEPTION 'Canonical active institution is missing';
    END IF;
    IF to_regclass('teaching_shared.schema_migrations') IS NOT NULL THEN
        SELECT artifact_sha256,view_fingerprints INTO previous,fingerprints
        FROM teaching_shared.schema_migrations WHERE version=1;
        IF previous IS NOT NULL THEN
            IF previous<>'7ce157405fc1ab956b3767bd25245697bf0d273214c88c4da395ac1fb133225c' THEN RAISE EXCEPTION 'Migration artifact mismatch'; END IF;
            FOR item IN SELECT key,value FROM jsonb_each_text(fingerprints) LOOP
                IF to_regclass('teaching_shared.'||quote_ident(item.key)) IS NULL OR
                   encode(sha256(convert_to(pg_get_viewdef(to_regclass('teaching_shared.'||quote_ident(item.key)),true),'UTF8')),'hex')<>item.value THEN
                    RAISE EXCEPTION 'Installed view drift; no replacement permitted';
                END IF;
            END LOOP;
            RETURN; -- Identical re-run: no changes.
        END IF;
    END IF;

    IF to_regnamespace('teaching_shared') IS NOT NULL THEN
        RAISE EXCEPTION 'Unrecognized existing teaching_shared schema; no replacement permitted';
    END IF;

    IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname IN ('schedence_teaching_shared_reader','schedence_teaching_shared_owner')) THEN
        RAISE EXCEPTION 'Reserved new role already exists; ownership review required';
    END IF;
    EXECUTE 'CREATE ROLE schedence_teaching_shared_owner NOLOGIN NOINHERIT NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION';
    EXECUTE 'CREATE ROLE schedence_teaching_shared_reader NOLOGIN NOINHERIT NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION';
    EXECUTE 'GRANT schedence_teaching_shared_owner TO CURRENT_USER';
    EXECUTE 'CREATE SCHEMA teaching_shared AUTHORIZATION schedence_teaching_shared_owner';
    EXECUTE 'REVOKE ALL ON SCHEMA teaching_shared FROM PUBLIC';
    EXECUTE 'CREATE TABLE teaching_shared.schema_migrations(version integer PRIMARY KEY,artifact_sha256 text NOT NULL,view_fingerprints jsonb NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp())';
    EXECUTE 'ALTER TABLE teaching_shared.schema_migrations OWNER TO schedence_teaching_shared_owner';
    EXECUTE 'GRANT USAGE ON SCHEMA academic TO schedence_teaching_shared_owner';
    EXECUTE 'GRANT USAGE ON SCHEMA examinations TO schedence_teaching_shared_owner';
    EXECUTE 'GRANT USAGE ON SCHEMA resource TO schedence_teaching_shared_owner';
    EXECUTE 'GRANT SELECT (code,id,institution_id,is_active,name,unit_type) ON academic.administrative_position_types TO schedence_teaching_shared_owner';
    EXECUTE 'GRANT SELECT (admin_hours_per_week,cap_hours_per_week,combination_rule,effective_from,effective_until,id,position_type_id,version) ON academic.administrative_workload_policies TO schedence_teaching_shared_owner';
    EXECUTE 'GRANT SELECT (academic_year,cohort_reference,id,institution_id,semester,source_id,stream,student_count) ON academic.cohort_totals TO schedence_teaching_shared_owner';
    EXECUTE 'GRANT SELECT (id,institution_id,name,source_id) ON academic.departments TO schedence_teaching_shared_owner';
    EXECUTE 'GRANT SELECT (id,institution_id,module_id,specialization_id) ON academic.module_specializations TO schedence_teaching_shared_owner';
    EXECUTE 'GRANT SELECT (id,institution_id,module_id,role,source_slot,staff_number) ON academic.module_teaching_teams TO schedence_teaching_shared_owner';
    EXECUTE 'GRANT SELECT (academic_year,degree_type,id,institution_id,lab_minutes,lecture_minutes,module_code,module_name,semester,source_id,tutorial_minutes,workshop_minutes) ON academic.modules TO schedence_teaching_shared_owner';
    EXECUTE 'GRANT SELECT (id,institution_id,provenance_kind) ON academic.source_datasets TO schedence_teaching_shared_owner';
    EXECUTE 'GRANT SELECT (code,id,institution_id,name,source_id) ON academic.specializations TO schedence_teaching_shared_owner';
    EXECUTE 'GRANT SELECT (academic_rank,employment_status,full_name,id,institution_id,staff_number) ON academic.staff TO schedence_teaching_shared_owner';
    EXECUTE 'GRANT SELECT (admin_hours_override,effective_from,effective_until,ended_at,id,institution_id,position_type_id,staff_id,unit_code,unit_type) ON academic.staff_administrative_appointments TO schedence_teaching_shared_owner';
    EXECUTE 'GRANT SELECT (department_id,id,institution_id,staff_number,staff_type) ON academic.staff_teaching_profiles TO schedence_teaching_shared_owner';
    EXECUTE 'GRANT SELECT (institution_id,singleton) ON examinations.exam_dataset_owner TO schedence_teaching_shared_owner';
    EXECUTE 'GRANT SELECT (exam_capacity,seats,session_id,timetable_id,venue_id,venue_name,venue_type) ON examinations.exam_timetable_rooms TO schedence_teaching_shared_owner';
    EXECUTE 'GRANT SELECT (academic_year,candidates,duration_minutes,ends_at,module_code,module_name,semester,session_id,starts_at,timetable_id) ON examinations.exam_timetable_sessions TO schedence_teaching_shared_owner';
    EXECUTE 'GRANT SELECT (institution_id,period_code,revision,status,timetable_id,validation_passed) ON examinations.exam_timetables TO schedence_teaching_shared_owner';
    EXECUTE 'GRANT SELECT (academic_year_label,day_end_time,day_start_time,end_date,exam_kind,include_saturdays,include_sundays,period_code,period_name,semester,start_date) ON examinations.seed_exam_periods TO schedence_teaching_shared_owner';
    EXECUTE 'GRANT SELECT (building,capacity,floor,id,institution_id,lab_specialization,source_id,venue_code,venue_name,venue_type) ON resource.venues TO schedence_teaching_shared_owner';
    EXECUTE 'SET LOCAL ROLE schedence_teaching_shared_owner';
    EXECUTE 'CREATE VIEW teaching_shared.academic_staff WITH (security_barrier=true,security_invoker=false) AS SELECT t.id, t.institution_id, t.staff_number, t.full_name, t.academic_rank, t.employment_status FROM academic.staff t WHERE t.institution_id = ''84651d6d-0c8a-470a-a798-5220666fd367''::uuid AND NULLIF(current_setting(''schedence.institution_id'',true),'''')::uuid = ''84651d6d-0c8a-470a-a798-5220666fd367''::uuid AND t.employment_status=''ACTIVE''';
    EXECUTE 'REVOKE ALL ON teaching_shared.academic_staff FROM PUBLIC';
    EXECUTE 'GRANT SELECT ON teaching_shared.academic_staff TO schedence_teaching_shared_reader';
    EXECUTE 'CREATE VIEW teaching_shared.departments WITH (security_barrier=true,security_invoker=false) AS SELECT t.id, t.institution_id, t.name, t.source_id FROM academic.departments t WHERE t.institution_id = ''84651d6d-0c8a-470a-a798-5220666fd367''::uuid AND NULLIF(current_setting(''schedence.institution_id'',true),'''')::uuid = ''84651d6d-0c8a-470a-a798-5220666fd367''::uuid';
    EXECUTE 'REVOKE ALL ON teaching_shared.departments FROM PUBLIC';
    EXECUTE 'GRANT SELECT ON teaching_shared.departments TO schedence_teaching_shared_reader';
    EXECUTE 'CREATE VIEW teaching_shared.academic_positions WITH (security_barrier=true,security_invoker=false) AS SELECT t.id, t.institution_id, t.code, t.name, t.unit_type, t.is_active FROM academic.administrative_position_types t WHERE t.institution_id = ''84651d6d-0c8a-470a-a798-5220666fd367''::uuid AND NULLIF(current_setting(''schedence.institution_id'',true),'''')::uuid = ''84651d6d-0c8a-470a-a798-5220666fd367''::uuid AND t.is_active';
    EXECUTE 'REVOKE ALL ON teaching_shared.academic_positions FROM PUBLIC';
    EXECUTE 'GRANT SELECT ON teaching_shared.academic_positions TO schedence_teaching_shared_reader';
    EXECUTE 'CREATE VIEW teaching_shared.modules WITH (security_barrier=true,security_invoker=false) AS SELECT t.id, t.institution_id, t.module_code, t.module_name, t.academic_year, t.semester, t.degree_type, t.lecture_minutes, t.tutorial_minutes, t.workshop_minutes, t.lab_minutes, t.source_id FROM academic.modules t WHERE t.institution_id = ''84651d6d-0c8a-470a-a798-5220666fd367''::uuid AND NULLIF(current_setting(''schedence.institution_id'',true),'''')::uuid = ''84651d6d-0c8a-470a-a798-5220666fd367''::uuid AND NOT EXISTS(SELECT 1 FROM academic.modules other WHERE other.institution_id=t.institution_id AND other.module_code=t.module_code AND other.id<>t.id)';
    EXECUTE 'REVOKE ALL ON teaching_shared.modules FROM PUBLIC';
    EXECUTE 'GRANT SELECT ON teaching_shared.modules TO schedence_teaching_shared_reader';
    EXECUTE 'CREATE VIEW teaching_shared.specializations WITH (security_barrier=true,security_invoker=false) AS SELECT t.id, t.institution_id, t.code, t.name, t.source_id FROM academic.specializations t WHERE t.institution_id = ''84651d6d-0c8a-470a-a798-5220666fd367''::uuid AND NULLIF(current_setting(''schedence.institution_id'',true),'''')::uuid = ''84651d6d-0c8a-470a-a798-5220666fd367''::uuid';
    EXECUTE 'REVOKE ALL ON teaching_shared.specializations FROM PUBLIC';
    EXECUTE 'GRANT SELECT ON teaching_shared.specializations TO schedence_teaching_shared_reader';
    EXECUTE 'CREATE VIEW teaching_shared.module_specializations WITH (security_barrier=true,security_invoker=false) AS SELECT t.id, t.institution_id, t.module_id, t.specialization_id FROM academic.module_specializations t WHERE t.institution_id = ''84651d6d-0c8a-470a-a798-5220666fd367''::uuid AND NULLIF(current_setting(''schedence.institution_id'',true),'''')::uuid = ''84651d6d-0c8a-470a-a798-5220666fd367''::uuid';
    EXECUTE 'REVOKE ALL ON teaching_shared.module_specializations FROM PUBLIC';
    EXECUTE 'GRANT SELECT ON teaching_shared.module_specializations TO schedence_teaching_shared_reader';
    EXECUTE 'CREATE VIEW teaching_shared.venues WITH (security_barrier=true,security_invoker=false) AS SELECT t.id, t.institution_id, t.venue_code, t.venue_name, t.venue_type, t.capacity, t.building, t.floor, t.lab_specialization, t.source_id FROM resource.venues t WHERE t.institution_id = ''84651d6d-0c8a-470a-a798-5220666fd367''::uuid AND NULLIF(current_setting(''schedence.institution_id'',true),'''')::uuid = ''84651d6d-0c8a-470a-a798-5220666fd367''::uuid';
    EXECUTE 'REVOKE ALL ON teaching_shared.venues FROM PUBLIC';
    EXECUTE 'GRANT SELECT ON teaching_shared.venues TO schedence_teaching_shared_reader';
    EXECUTE 'CREATE VIEW teaching_shared.staff_teaching_profiles WITH (security_barrier=true,security_invoker=false) AS SELECT p.id,p.institution_id,s.id AS staff_id,p.staff_number,p.staff_type,p.department_id FROM academic.staff_teaching_profiles p JOIN academic.staff s ON s.institution_id=p.institution_id AND s.staff_number=p.staff_number WHERE p.institution_id = ''84651d6d-0c8a-470a-a798-5220666fd367''::uuid AND NULLIF(current_setting(''schedence.institution_id'',true),'''')::uuid = ''84651d6d-0c8a-470a-a798-5220666fd367''::uuid AND s.employment_status=''ACTIVE''';
    EXECUTE 'REVOKE ALL ON teaching_shared.staff_teaching_profiles FROM PUBLIC';
    EXECUTE 'GRANT SELECT ON teaching_shared.staff_teaching_profiles TO schedence_teaching_shared_reader';
    EXECUTE 'CREATE VIEW teaching_shared.teaching_assignments WITH (security_barrier=true,security_invoker=false) AS SELECT a.id,a.institution_id,a.module_id,s.id AS staff_id,a.staff_number,a.role,a.source_slot FROM academic.module_teaching_teams a JOIN academic.staff s ON s.institution_id=a.institution_id AND s.staff_number=a.staff_number WHERE a.institution_id = ''84651d6d-0c8a-470a-a798-5220666fd367''::uuid AND NULLIF(current_setting(''schedence.institution_id'',true),'''')::uuid = ''84651d6d-0c8a-470a-a798-5220666fd367''::uuid AND s.employment_status=''ACTIVE''';
    EXECUTE 'REVOKE ALL ON teaching_shared.teaching_assignments FROM PUBLIC';
    EXECUTE 'GRANT SELECT ON teaching_shared.teaching_assignments TO schedence_teaching_shared_reader';
    EXECUTE 'CREATE VIEW teaching_shared.cohort_totals WITH (security_barrier=true,security_invoker=false) AS SELECT c.id,c.institution_id,c.cohort_reference,c.academic_year,c.semester,c.stream,c.student_count,c.source_id,s.provenance_kind FROM academic.cohort_totals c JOIN academic.source_datasets s ON s.institution_id=c.institution_id AND s.id=c.source_id WHERE c.institution_id = ''84651d6d-0c8a-470a-a798-5220666fd367''::uuid AND NULLIF(current_setting(''schedence.institution_id'',true),'''')::uuid = ''84651d6d-0c8a-470a-a798-5220666fd367''::uuid AND s.provenance_kind=''UNIVERSITY_SOURCED''';
    EXECUTE 'REVOKE ALL ON teaching_shared.cohort_totals FROM PUBLIC';
    EXECUTE 'GRANT SELECT ON teaching_shared.cohort_totals TO schedence_teaching_shared_reader';
    EXECUTE 'CREATE VIEW teaching_shared.administrative_appointments WITH (security_barrier=true,security_invoker=false) AS SELECT t.id, t.institution_id, t.staff_id, t.position_type_id, t.unit_type, t.unit_code, t.effective_from, t.effective_until, t.admin_hours_override FROM academic.staff_administrative_appointments t WHERE t.institution_id = ''84651d6d-0c8a-470a-a798-5220666fd367''::uuid AND NULLIF(current_setting(''schedence.institution_id'',true),'''')::uuid = ''84651d6d-0c8a-470a-a798-5220666fd367''::uuid AND t.ended_at IS NULL AND t.effective_from<=CURRENT_DATE AND (t.effective_until IS NULL OR t.effective_until>=CURRENT_DATE)';
    EXECUTE 'REVOKE ALL ON teaching_shared.administrative_appointments FROM PUBLIC';
    EXECUTE 'GRANT SELECT ON teaching_shared.administrative_appointments TO schedence_teaching_shared_reader';
    EXECUTE 'CREATE VIEW teaching_shared.administrative_workload_policies WITH (security_barrier=true,security_invoker=false) AS SELECT w.id,p.institution_id,w.position_type_id,w.version,w.admin_hours_per_week,w.combination_rule,w.cap_hours_per_week,w.effective_from,w.effective_until FROM academic.administrative_workload_policies w JOIN academic.administrative_position_types p ON p.id=w.position_type_id WHERE p.institution_id = ''84651d6d-0c8a-470a-a798-5220666fd367''::uuid AND NULLIF(current_setting(''schedence.institution_id'',true),'''')::uuid = ''84651d6d-0c8a-470a-a798-5220666fd367''::uuid AND p.is_active AND w.effective_from<=CURRENT_DATE AND (w.effective_until IS NULL OR w.effective_until>=CURRENT_DATE)';
    EXECUTE 'REVOKE ALL ON teaching_shared.administrative_workload_policies FROM PUBLIC';
    EXECUTE 'GRANT SELECT ON teaching_shared.administrative_workload_policies TO schedence_teaching_shared_reader';
    EXECUTE 'CREATE VIEW teaching_shared.examination_periods WITH (security_barrier=true,security_invoker=false) AS SELECT o.institution_id,p.period_code,p.period_name,p.academic_year_label,p.semester,p.start_date,p.end_date,p.day_start_time,p.day_end_time,p.include_saturdays,p.include_sundays,p.exam_kind FROM examinations.seed_exam_periods p CROSS JOIN examinations.exam_dataset_owner o WHERE o.singleton AND o.institution_id = ''84651d6d-0c8a-470a-a798-5220666fd367''::uuid AND NULLIF(current_setting(''schedence.institution_id'',true),'''')::uuid = ''84651d6d-0c8a-470a-a798-5220666fd367''::uuid';
    EXECUTE 'REVOKE ALL ON teaching_shared.examination_periods FROM PUBLIC';
    EXECUTE 'GRANT SELECT ON teaching_shared.examination_periods TO schedence_teaching_shared_reader';
    EXECUTE 'CREATE VIEW teaching_shared.published_exam_sessions WITH (security_barrier=true,security_invoker=false) AS SELECT t.institution_id,t.timetable_id,t.revision,t.period_code,s.session_id,s.module_code,s.module_name,s.academic_year,s.semester,s.starts_at,s.ends_at,s.duration_minutes,s.candidates FROM examinations.exam_timetables t JOIN examinations.exam_timetable_sessions s ON s.timetable_id=t.timetable_id WHERE t.institution_id = ''84651d6d-0c8a-470a-a798-5220666fd367''::uuid AND NULLIF(current_setting(''schedence.institution_id'',true),'''')::uuid = ''84651d6d-0c8a-470a-a798-5220666fd367''::uuid AND t.status=''PUBLISHED'' AND t.validation_passed';
    EXECUTE 'REVOKE ALL ON teaching_shared.published_exam_sessions FROM PUBLIC';
    EXECUTE 'GRANT SELECT ON teaching_shared.published_exam_sessions TO schedence_teaching_shared_reader';
    EXECUTE 'CREATE VIEW teaching_shared.published_exam_rooms WITH (security_barrier=true,security_invoker=false) AS SELECT t.institution_id,t.timetable_id,t.revision,r.session_id,r.venue_id,r.venue_name,r.venue_type,r.seats,r.exam_capacity FROM examinations.exam_timetables t JOIN examinations.exam_timetable_rooms r ON r.timetable_id=t.timetable_id WHERE t.institution_id = ''84651d6d-0c8a-470a-a798-5220666fd367''::uuid AND NULLIF(current_setting(''schedence.institution_id'',true),'''')::uuid = ''84651d6d-0c8a-470a-a798-5220666fd367''::uuid AND t.status=''PUBLISHED'' AND t.validation_passed';
    EXECUTE 'REVOKE ALL ON teaching_shared.published_exam_rooms FROM PUBLIC';
    EXECUTE 'GRANT SELECT ON teaching_shared.published_exam_rooms TO schedence_teaching_shared_reader';
    EXECUTE 'CREATE VIEW teaching_shared.module_source_records WITH (security_barrier=true,security_invoker=false) AS SELECT t.id, t.institution_id, t.module_code, t.module_name, t.academic_year, t.semester, t.degree_type, t.lecture_minutes, t.tutorial_minutes, t.workshop_minutes, t.lab_minutes, t.source_id FROM academic.modules t WHERE t.institution_id = ''84651d6d-0c8a-470a-a798-5220666fd367''::uuid AND NULLIF(current_setting(''schedence.institution_id'',true),'''')::uuid = ''84651d6d-0c8a-470a-a798-5220666fd367''::uuid';
    EXECUTE 'REVOKE ALL ON teaching_shared.module_source_records FROM PUBLIC';
    EXECUTE 'GRANT SELECT ON teaching_shared.module_source_records TO schedence_teaching_shared_reader';
    EXECUTE 'CREATE VIEW teaching_shared.module_code_conflicts WITH (security_barrier=true,security_invoker=false) AS SELECT t.institution_id,t.module_code,count(*) AS source_record_count,array_agg(t.id ORDER BY t.id) AS source_record_ids FROM academic.modules t WHERE t.institution_id = ''84651d6d-0c8a-470a-a798-5220666fd367''::uuid AND NULLIF(current_setting(''schedence.institution_id'',true),'''')::uuid = ''84651d6d-0c8a-470a-a798-5220666fd367''::uuid GROUP BY t.institution_id,t.module_code HAVING count(*)>1';
    EXECUTE 'REVOKE ALL ON teaching_shared.module_code_conflicts FROM PUBLIC';
    EXECUTE 'GRANT SELECT ON teaching_shared.module_code_conflicts TO schedence_teaching_shared_reader';
    EXECUTE 'GRANT USAGE ON SCHEMA teaching_shared TO schedence_teaching_shared_reader';
    EXECUTE 'INSERT INTO teaching_shared.schema_migrations(version,artifact_sha256,view_fingerprints) SELECT 1,''7ce157405fc1ab956b3767bd25245697bf0d273214c88c4da395ac1fb133225c'',jsonb_object_agg(c.relname,encode(sha256(convert_to(pg_get_viewdef(c.oid,true),''UTF8'')),''hex'')) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=''teaching_shared'' AND c.relkind=''v'' AND c.relname IN (''academic_staff'',''departments'',''academic_positions'',''modules'',''specializations'',''module_specializations'',''venues'',''staff_teaching_profiles'',''teaching_assignments'',''cohort_totals'',''administrative_appointments'',''administrative_workload_policies'',''examination_periods'',''published_exam_sessions'',''published_exam_rooms'',''module_source_records'',''module_code_conflicts'')';
    EXECUTE 'RESET ROLE';
    EXECUTE 'REVOKE schedence_teaching_shared_owner FROM CURRENT_USER';
END
$migration$;
COMMIT;
