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
        FROM teaching_shared.schema_migrations WHERE version=2;
        IF previous IS NOT NULL THEN
            IF previous<>'67f64aefee298ac80200ad164b209f7dd2b1c6fdac880e1043aac255367e8f73' THEN RAISE EXCEPTION 'Migration artifact mismatch'; END IF;
            FOR item IN SELECT key,value FROM jsonb_each_text(fingerprints) LOOP
                IF to_regclass('teaching_shared.'||quote_ident(item.key)) IS NULL OR
                   encode(sha256(convert_to(pg_get_viewdef(to_regclass('teaching_shared.'||quote_ident(item.key)),true),'UTF8')),'hex')<>item.value THEN
                    RAISE EXCEPTION 'Installed view drift; no replacement permitted';
                END IF;
            END LOOP;
            RETURN; -- Identical re-run: no changes.
        END IF;
    END IF;

    IF to_regclass('teaching_shared.schema_migrations') IS NULL THEN
        RAISE EXCEPTION 'Apply reviewed version 1 first';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM teaching_shared.schema_migrations WHERE version=1 AND artifact_sha256='7ce157405fc1ab956b3767bd25245697bf0d273214c88c4da395ac1fb133225c') THEN
        RAISE EXCEPTION 'Unrecognized prerequisite migration';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='schedence_teaching_shared_owner' AND NOT rolcanlogin AND NOT rolsuper
        AND NOT rolbypassrls AND NOT rolcreatedb AND NOT rolcreaterole AND NOT rolreplication) THEN
        RAISE EXCEPTION 'Shared view owner is not restricted';
    END IF;

    IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname IN ('schedence_teaching_shared_draft_reader')) THEN
        RAISE EXCEPTION 'Reserved new role already exists; ownership review required';
    END IF;
    EXECUTE 'CREATE ROLE schedence_teaching_shared_draft_reader NOLOGIN NOINHERIT NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION';
    EXECUTE 'GRANT schedence_teaching_shared_owner TO CURRENT_USER';
    EXECUTE 'GRANT USAGE ON SCHEMA academic TO schedence_teaching_shared_owner';
    EXECUTE 'GRANT USAGE ON SCHEMA teaching TO schedence_teaching_shared_owner';
    EXECUTE 'GRANT SELECT (cohort_total_id,id,institution_id,specialization_id) ON academic.pp1_specialization_allocations TO schedence_teaching_shared_owner';
    EXECUTE 'GRANT SELECT (allocation_id,id,institution_id,local_group_reference,parent_id,plan_reference,student_count) ON academic.pp1_student_groups TO schedence_teaching_shared_owner';
    EXECUTE 'GRANT SELECT (delivery_mode,end_minute,id,institution_id,staff_reference,start_minute,student_count,teaching_date,venue_reference,version_id) ON teaching.deliveries TO schedence_teaching_shared_owner';
    EXECUTE 'GRANT SELECT (academic_term_reference,calendar_label,content_sha256,created_at,expected_occurrences,id,institution_id,payload,schema_version,timezone_name) ON teaching.input_snapshots TO schedence_teaching_shared_owner';
    EXECUTE 'GRANT SELECT (academic_complete,id,institution_id,lifecycle_status,solver_success,validation_passed,version_number) ON teaching.timetable_versions TO schedence_teaching_shared_owner';
    EXECUTE 'SET LOCAL ROLE schedence_teaching_shared_owner';
    EXECUTE 'CREATE VIEW teaching_shared.experimental_student_groups WITH (security_barrier=true,security_invoker=false) AS SELECT g.id,g.institution_id,g.allocation_id,g.plan_reference,g.local_group_reference,g.parent_id,g.student_count,a.cohort_total_id,a.specialization_id,''PP1_EXPERIMENTAL''::text AS data_classification FROM academic.pp1_student_groups g JOIN academic.pp1_specialization_allocations a ON a.institution_id=g.institution_id AND a.id=g.allocation_id WHERE g.institution_id = ''84651d6d-0c8a-470a-a798-5220666fd367''::uuid AND NULLIF(current_setting(''schedence.institution_id'',true),'''')::uuid = ''84651d6d-0c8a-470a-a798-5220666fd367''::uuid';
    EXECUTE 'REVOKE ALL ON teaching_shared.experimental_student_groups FROM PUBLIC';
    EXECUTE 'GRANT SELECT ON teaching_shared.experimental_student_groups TO schedence_teaching_shared_draft_reader';
    EXECUTE 'CREATE VIEW teaching_shared.scheduling_inputs WITH (security_barrier=true,security_invoker=false) AS SELECT t.id, t.institution_id, t.schema_version, t.content_sha256, t.academic_term_reference, t.timezone_name, t.calendar_label, t.expected_occurrences, t.created_at FROM teaching.input_snapshots t WHERE t.institution_id = ''84651d6d-0c8a-470a-a798-5220666fd367''::uuid AND NULLIF(current_setting(''schedence.institution_id'',true),'''')::uuid = ''84651d6d-0c8a-470a-a798-5220666fd367''::uuid';
    EXECUTE 'REVOKE ALL ON teaching_shared.scheduling_inputs FROM PUBLIC';
    EXECUTE 'GRANT SELECT ON teaching_shared.scheduling_inputs TO schedence_teaching_shared_draft_reader';
    EXECUTE 'CREATE VIEW teaching_shared.validated_draft_sessions WITH (security_barrier=true,security_invoker=false) AS SELECT d.id,d.institution_id,d.version_id,v.version_number,v.lifecycle_status,v.academic_complete,d.teaching_date,d.start_minute,d.end_minute,d.delivery_mode,d.staff_reference,d.venue_reference,d.student_count FROM teaching.deliveries d JOIN teaching.timetable_versions v ON v.institution_id=d.institution_id AND v.id=d.version_id WHERE d.institution_id = ''84651d6d-0c8a-470a-a798-5220666fd367''::uuid AND NULLIF(current_setting(''schedence.institution_id'',true),'''')::uuid = ''84651d6d-0c8a-470a-a798-5220666fd367''::uuid AND v.lifecycle_status=''DRAFT_VALIDATED'' AND v.validation_passed AND v.solver_success';
    EXECUTE 'REVOKE ALL ON teaching_shared.validated_draft_sessions FROM PUBLIC';
    EXECUTE 'GRANT SELECT ON teaching_shared.validated_draft_sessions TO schedence_teaching_shared_draft_reader';
    EXECUTE 'CREATE VIEW teaching_shared.staff_workload WITH (security_barrier=true,security_invoker=false) AS SELECT d.institution_id,d.version_id,d.staff_reference,d.teaching_date,sum(d.end_minute-d.start_minute)::bigint AS assigned_draft_minutes,sum(d.end_minute-d.start_minute)/60.0 AS assigned_draft_hours FROM teaching.deliveries d JOIN teaching.timetable_versions v ON v.institution_id=d.institution_id AND v.id=d.version_id WHERE d.institution_id = ''84651d6d-0c8a-470a-a798-5220666fd367''::uuid AND NULLIF(current_setting(''schedence.institution_id'',true),'''')::uuid = ''84651d6d-0c8a-470a-a798-5220666fd367''::uuid AND v.lifecycle_status=''DRAFT_VALIDATED'' AND v.validation_passed AND v.solver_success GROUP BY d.institution_id,d.version_id,d.staff_reference,d.teaching_date';
    EXECUTE 'REVOKE ALL ON teaching_shared.staff_workload FROM PUBLIC';
    EXECUTE 'GRANT SELECT ON teaching_shared.staff_workload TO schedence_teaching_shared_draft_reader';
    EXECUTE 'CREATE VIEW teaching_shared.snapshot_planning_groups WITH (security_barrier=true,security_invoker=false) AS SELECT s.institution_id,s.id AS snapshot_id,g->>''group_id'' AS group_reference,(g->>''academic_year'')::integer AS academic_year,(g->>''semester'')::integer AS semester,g->>''stream'' AS stream,(g->>''student_count'')::integer AS student_count,g->>''parent_group_id'' AS parent_group_reference,s.calendar_label FROM teaching.input_snapshots s CROSS JOIN LATERAL jsonb_array_elements((s.payload->>''document_json'')::jsonb#>''{teaching_input,student_groups}'') g WHERE s.institution_id = ''84651d6d-0c8a-470a-a798-5220666fd367''::uuid AND NULLIF(current_setting(''schedence.institution_id'',true),'''')::uuid = ''84651d6d-0c8a-470a-a798-5220666fd367''::uuid';
    EXECUTE 'REVOKE ALL ON teaching_shared.snapshot_planning_groups FROM PUBLIC';
    EXECUTE 'GRANT SELECT ON teaching_shared.snapshot_planning_groups TO schedence_teaching_shared_draft_reader';
    EXECUTE 'GRANT USAGE ON SCHEMA teaching_shared TO schedence_teaching_shared_draft_reader';
    EXECUTE 'INSERT INTO teaching_shared.schema_migrations(version,artifact_sha256,view_fingerprints) SELECT 2,''67f64aefee298ac80200ad164b209f7dd2b1c6fdac880e1043aac255367e8f73'',jsonb_object_agg(c.relname,encode(sha256(convert_to(pg_get_viewdef(c.oid,true),''UTF8'')),''hex'')) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=''teaching_shared'' AND c.relkind=''v'' AND c.relname IN (''experimental_student_groups'',''scheduling_inputs'',''validated_draft_sessions'',''staff_workload'',''snapshot_planning_groups'')';
    EXECUTE 'RESET ROLE';
    EXECUTE 'REVOKE schedence_teaching_shared_owner FROM CURRENT_USER';
END
$migration$;
COMMIT;
