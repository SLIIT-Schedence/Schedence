-- REVIEW ONLY: access rollback for NEW shared reader groups, not source data.
-- Run only after explicit approval and after checking these are our installed objects.
-- No DROP, DELETE, UPDATE, owner-table grant revocation, or role deletion.
BEGIN;
SET LOCAL lock_timeout='3s';
DO $deactivate$
DECLARE reader_name text;
BEGIN
    IF current_database()<>'neondb'
       OR current_setting('neon.project_id',true) IS DISTINCT FROM 'super-leaf-21551515'
       OR current_setting('neon.branch_id',true) IS DISTINCT FROM 'br-dry-darkness-b549ci92'
       OR to_regclass('teaching_shared.schema_migrations') IS NULL THEN
        RAISE EXCEPTION 'Unrecognized installed shared schema/target';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_namespace n JOIN pg_roles r ON r.oid=n.nspowner
                  WHERE n.nspname='teaching_shared' AND r.rolname='schedence_teaching_shared_owner') THEN
        RAISE EXCEPTION 'Unexpected schema owner';
    END IF;
    FOREACH reader_name IN ARRAY ARRAY['schedence_teaching_shared_reader','schedence_teaching_shared_draft_reader'] LOOP
        IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname=reader_name) THEN
            EXECUTE format('REVOKE USAGE ON SCHEMA teaching_shared FROM %I',reader_name);
            EXECUTE format('REVOKE SELECT ON ALL TABLES IN SCHEMA teaching_shared FROM %I',reader_name);
        END IF;
    END LOOP;
END
$deactivate$;
COMMIT;
