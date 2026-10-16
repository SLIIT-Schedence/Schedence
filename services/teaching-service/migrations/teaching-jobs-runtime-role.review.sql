-- REVIEW ONLY: run only after owner approval, recovery verification and migration.
-- Existing role must already be NOSUPERUSER, NOBYPASSRLS and not a table owner.
-- These grants add no access to Identity/Academic/Resource master records.
GRANT SELECT, INSERT, UPDATE ON teaching.generation_jobs, teaching.generation_job_attempts
    TO schedence_teaching_runtime;
-- No DELETE, DDL, ownership, BYPASSRLS or history UPDATE permissions are added.
