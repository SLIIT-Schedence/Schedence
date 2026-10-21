-- Emergency access deactivation only; DO NOT execute during verification.
-- Preserves every original and inserted record. No DROP/DELETE or policy changes.
BEGIN;
DO $guard$ BEGIN
IF current_database()<>'neondb' OR current_setting('neon.project_id',true) IS DISTINCT FROM 'super-leaf-21551515'
OR current_setting('neon.branch_id',true) IS DISTINCT FROM 'br-dry-darkness-b549ci92' THEN
RAISE EXCEPTION 'Wrong target'; END IF;
IF NOT EXISTS(SELECT 1 FROM teaching_shared.integration_migrations WHERE version=1 AND artifact_sha256='5496e2988afc1d47eb5b62b870ab4f53288e487aa830dfdc860fd35435cf7ed8') THEN
RAISE EXCEPTION 'Unrecognized deployment'; END IF;
END $guard$;
REVOKE schedence_teaching_shared_reader FROM schedence_teaching_runtime;
REVOKE USAGE ON SCHEMA teaching_shared FROM schedence_teaching_shared_reader;
REVOKE SELECT ON teaching_shared.academic_staff,teaching_shared.departments,teaching_shared.academic_positions,teaching_shared.modules,teaching_shared.specializations,teaching_shared.module_specializations,teaching_shared.venues,teaching_shared.staff_teaching_profiles,teaching_shared.teaching_assignments,teaching_shared.cohort_totals,teaching_shared.administrative_appointments,teaching_shared.administrative_workload_policies,teaching_shared.examination_periods,teaching_shared.published_exam_sessions,teaching_shared.published_exam_rooms,teaching_shared.module_code_conflicts,teaching_shared.reviewed_module_crosswalk FROM schedence_teaching_shared_reader;
REVOKE SELECT,INSERT,UPDATE ON teaching.form_template_drafts FROM schedence_teaching_runtime;
REVOKE SELECT,INSERT ON teaching.form_distributions,teaching.form_distribution_recipients FROM schedence_teaching_runtime;
REVOKE UPDATE(first_opened_at) ON teaching.form_distribution_recipients FROM schedence_teaching_runtime;
COMMIT;
