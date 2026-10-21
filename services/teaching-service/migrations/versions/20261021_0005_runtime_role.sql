-- Restricted Teaching runtime role, assumed by the shared migration login with SET ROLE.
-- Same attributes as teaching-runtime-role.review.sql; NOLOGIN, so no new credential exists.
-- Grants cover only the teaching schema; Identity/Academic/Resource records are untouched.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'schedence_teaching_runtime') THEN
        CREATE ROLE schedence_teaching_runtime
            NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT;
    END IF;
END $$;

GRANT USAGE ON SCHEMA teaching TO schedence_teaching_runtime;
GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA teaching TO schedence_teaching_runtime;
REVOKE ALL ON teaching.alembic_version FROM schedence_teaching_runtime;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA teaching TO schedence_teaching_runtime;

-- FOR UPDATE row locking; the immutable-history trigger still rejects actual updates.
GRANT UPDATE (series_reference) ON teaching.timetable_series TO schedence_teaching_runtime;
-- teaching-jobs-runtime-role.review.sql, 0003 and 0004 grant notes.
GRANT UPDATE ON teaching.generation_jobs, teaching.generation_job_attempts, teaching.form_template_drafts
    TO schedence_teaching_runtime;
GRANT UPDATE (first_opened_at) ON teaching.form_distribution_recipients TO schedence_teaching_runtime;

-- The runtime connects as the migration login and switches with SET ROLE; it never inherits that login's rights.
DO $$
BEGIN
    IF current_setting('server_version_num')::int >= 160000 THEN
        EXECUTE format('GRANT schedence_teaching_runtime TO %I WITH INHERIT FALSE, SET TRUE', current_user);
    ELSE
        EXECUTE format('GRANT schedence_teaching_runtime TO %I', current_user);
    END IF;
END $$;
