-- REVIEW ONLY. Not executed by Alembic, the app, or Step 12B.2 preparation.
-- Apply only after target/recovery verification and role-owner approval.
-- No password is generated or stored here. NOLOGIN prevents accidental use
-- until a dedicated login is securely provisioned through ignored configuration.
CREATE ROLE schedence_teaching_runtime
    NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT;

GRANT USAGE ON SCHEMA teaching TO schedence_teaching_runtime;
GRANT SELECT, INSERT ON TABLE
    teaching.input_snapshots,
    teaching.snapshot_sources,
    teaching.snapshot_scopes,
    teaching.snapshot_issues,
    teaching.timetable_series,
    teaching.generation_runs,
    teaching.validation_reports,
    teaching.timetable_versions,
    teaching.deliveries,
    teaching.delivery_coverage
TO schedence_teaching_runtime;

-- PostgreSQL requires UPDATE privilege on at least one column for FOR UPDATE.
-- The immutable-history trigger still rejects actual updates.
GRANT UPDATE (series_reference) ON teaching.timetable_series TO schedence_teaching_runtime;
