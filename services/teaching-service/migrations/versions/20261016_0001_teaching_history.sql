CREATE TABLE teaching.input_snapshots (
	schema_version INTEGER NOT NULL, 
	content_sha256 VARCHAR(64) NOT NULL, 
	academic_term_reference TEXT NOT NULL, 
	timezone_name VARCHAR(100) NOT NULL, 
	calendar_label VARCHAR(40) NOT NULL, 
	expected_occurrences INTEGER NOT NULL, 
	payload JSONB NOT NULL, 
	id UUID NOT NULL, 
	institution_id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_input_snapshots PRIMARY KEY (id), 
	CONSTRAINT uq_snapshots_tenant_id UNIQUE (institution_id, id), 
	CONSTRAINT uq_snapshots_expected UNIQUE (institution_id, id, expected_occurrences), 
	CONSTRAINT ck_input_snapshots_schema_version CHECK (schema_version = 1), 
	CONSTRAINT ck_input_snapshots_sha256 CHECK (content_sha256 ~ '^[0-9a-f]{64}$'), 
	CONSTRAINT ck_input_snapshots_payload_object CHECK (jsonb_typeof(payload) = 'object'), 
	CONSTRAINT ck_input_snapshots_expected_count CHECK (expected_occurrences >= 0), 
	CONSTRAINT ck_input_snapshots_calendar_label CHECK (calendar_label IN ('PP1_TEST_CALENDAR','PP1_EXPERIMENTAL_CALENDAR','SUPPLIED_CALENDAR'))
);

CREATE INDEX ix_snapshots_tenant_digest ON teaching.input_snapshots (institution_id, content_sha256);

CREATE TABLE teaching.timetable_series (
	series_reference TEXT NOT NULL, 
	id UUID NOT NULL, 
	institution_id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_timetable_series PRIMARY KEY (id), 
	CONSTRAINT uq_series_tenant_id UNIQUE (institution_id, id), 
	CONSTRAINT uq_timetable_series_institution_id_series_reference UNIQUE (institution_id, series_reference)
);

CREATE TABLE teaching.generation_runs (
	series_id UUID NOT NULL, 
	snapshot_id UUID NOT NULL, 
	solver_status VARCHAR(20) NOT NULL, 
	solver_success BOOLEAN NOT NULL, 
	expected_occurrences INTEGER NOT NULL, 
	scheduled_occurrences INTEGER NOT NULL, 
	runtime_seconds FLOAT NOT NULL, 
	result_metadata JSONB NOT NULL, 
	id UUID NOT NULL, 
	institution_id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_generation_runs PRIMARY KEY (id), 
	CONSTRAINT fk_generation_runs_institution_id_series_id_timetable_series FOREIGN KEY(institution_id, series_id) REFERENCES teaching.timetable_series (institution_id, id) ON DELETE RESTRICT, 
	CONSTRAINT fk_generation_runs_institution_id_snapshot_id_expected__f61f FOREIGN KEY(institution_id, snapshot_id, expected_occurrences) REFERENCES teaching.input_snapshots (institution_id, id, expected_occurrences) ON DELETE RESTRICT, 
	CONSTRAINT uq_runs_tenant_id UNIQUE (institution_id, id), 
	CONSTRAINT uq_runs_series_id UNIQUE (institution_id, series_id, id), 
	CONSTRAINT uq_runs_success UNIQUE (institution_id, id, solver_success), 
	CONSTRAINT uq_runs_expected UNIQUE (institution_id, id, expected_occurrences), 
	CONSTRAINT ck_generation_runs_solver_status CHECK (solver_status IN ('OPTIMAL','FEASIBLE','UNKNOWN','INFEASIBLE','MODEL_INVALID')), 
	CONSTRAINT ck_generation_runs_success_matches_status CHECK (solver_success = (solver_status IN ('OPTIMAL','FEASIBLE'))), 
	CONSTRAINT ck_generation_runs_counts CHECK (expected_occurrences >= 0 AND scheduled_occurrences >= 0 AND scheduled_occurrences <= expected_occurrences), 
	CONSTRAINT ck_generation_runs_complete_or_empty CHECK ((solver_success AND scheduled_occurrences = expected_occurrences) OR (NOT solver_success AND scheduled_occurrences = 0)), 
	CONSTRAINT ck_generation_runs_runtime CHECK (runtime_seconds >= 0 AND runtime_seconds < 'Infinity'::float8), 
	CONSTRAINT ck_generation_runs_metadata_object CHECK (jsonb_typeof(result_metadata) = 'object')
);

CREATE INDEX ix_runs_tenant_series_created ON teaching.generation_runs (institution_id, series_id, created_at);

CREATE TABLE teaching.snapshot_scopes (
	snapshot_id UUID NOT NULL, 
	scope_key TEXT NOT NULL, 
	academic_year INTEGER NOT NULL, 
	semester INTEGER NOT NULL, 
	stream VARCHAR(10) NOT NULL, 
	specialization TEXT NOT NULL, 
	student_count INTEGER, 
	scope_status VARCHAR(40) NOT NULL, 
	group_references JSONB NOT NULL, 
	id UUID NOT NULL, 
	institution_id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_snapshot_scopes PRIMARY KEY (id), 
	CONSTRAINT fk_snapshot_scopes_institution_id_snapshot_id_input_snapshots FOREIGN KEY(institution_id, snapshot_id) REFERENCES teaching.input_snapshots (institution_id, id) ON DELETE RESTRICT, 
	CONSTRAINT uq_snapshot_scopes_institution_id_snapshot_id_scope_key UNIQUE (institution_id, snapshot_id, scope_key), 
	CONSTRAINT ck_snapshot_scopes_study_period CHECK (academic_year BETWEEN 1 AND 4 AND semester BETWEEN 1 AND 2), 
	CONSTRAINT ck_snapshot_scopes_stream CHECK (stream IN ('WEEKDAY','WEEKEND')), 
	CONSTRAINT ck_snapshot_scopes_student_count CHECK (student_count IS NULL OR student_count > 0), 
	CONSTRAINT ck_snapshot_scopes_scope_status CHECK (scope_status IN ('SUPPORTED','BLOCKED_MISSING_ACADEMIC_MAPPING')), 
	CONSTRAINT ck_snapshot_scopes_group_array CHECK (jsonb_typeof(group_references) = 'array')
);

CREATE INDEX ix_scopes_tenant_period ON teaching.snapshot_scopes (institution_id, semester, academic_year, stream, specialization);

CREATE TABLE teaching.snapshot_sources (
	snapshot_id UUID NOT NULL, 
	label TEXT NOT NULL, 
	sha256 VARCHAR(64) NOT NULL, 
	provenance_kind VARCHAR(40) NOT NULL, 
	owner VARCHAR(40) NOT NULL, 
	provenance JSONB NOT NULL, 
	id UUID NOT NULL, 
	institution_id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_snapshot_sources PRIMARY KEY (id), 
	CONSTRAINT fk_snapshot_sources_institution_id_snapshot_id_input_snapshots FOREIGN KEY(institution_id, snapshot_id) REFERENCES teaching.input_snapshots (institution_id, id) ON DELETE RESTRICT, 
	CONSTRAINT uq_snapshot_sources_institution_id_snapshot_id_label UNIQUE (institution_id, snapshot_id, label), 
	CONSTRAINT ck_snapshot_sources_sha256 CHECK (sha256 ~ '^[0-9a-f]{64}$'), 
	CONSTRAINT ck_snapshot_sources_provenance_kind CHECK (provenance_kind IN ('UNIVERSITY_SOURCED','APPROVED_PP1_ESTIMATE','SYNTHETIC_PP1','EXPERIMENTAL_CONFIGURATION','USER_CONFIRMED_CORRECTION','SERVICE_EXPORT')), 
	CONSTRAINT ck_snapshot_sources_provenance_object CHECK (jsonb_typeof(provenance) = 'object')
);

CREATE TABLE teaching.snapshot_issues (
	snapshot_id UUID NOT NULL, 
	scope_key TEXT, 
	issue_code TEXT NOT NULL, 
	severity VARCHAR(10) NOT NULL, 
	details JSONB NOT NULL, 
	id UUID NOT NULL, 
	institution_id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_snapshot_issues PRIMARY KEY (id), 
	CONSTRAINT fk_snapshot_issues_institution_id_snapshot_id_input_snapshots FOREIGN KEY(institution_id, snapshot_id) REFERENCES teaching.input_snapshots (institution_id, id) ON DELETE RESTRICT, 
	CONSTRAINT fk_snapshot_issues_institution_id_snapshot_id_scope_key_9181 FOREIGN KEY(institution_id, snapshot_id, scope_key) REFERENCES teaching.snapshot_scopes (institution_id, snapshot_id, scope_key) ON DELETE RESTRICT, 
	CONSTRAINT ck_snapshot_issues_severity CHECK (severity IN ('INFO','WARNING','BLOCKER')), 
	CONSTRAINT ck_snapshot_issues_details_object CHECK (jsonb_typeof(details) = 'object')
);

CREATE TABLE teaching.validation_reports (
	run_id UUID NOT NULL, 
	expected_occurrences INTEGER NOT NULL, 
	checked_occurrences INTEGER NOT NULL, 
	hard_violations INTEGER, 
	passed BOOLEAN NOT NULL, 
	evidence JSONB NOT NULL, 
	id UUID NOT NULL, 
	institution_id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_validation_reports PRIMARY KEY (id), 
	CONSTRAINT fk_validation_reports_institution_id_run_id_expected_oc_90e6 FOREIGN KEY(institution_id, run_id, expected_occurrences) REFERENCES teaching.generation_runs (institution_id, id, expected_occurrences) ON DELETE RESTRICT, 
	CONSTRAINT uq_reports_run_passed UNIQUE (institution_id, run_id, id, passed), 
	CONSTRAINT ck_validation_reports_counts CHECK (expected_occurrences >= 0 AND checked_occurrences BETWEEN 0 AND expected_occurrences AND hard_violations >= 0), 
	CONSTRAINT ck_validation_reports_passed_complete CHECK (NOT passed OR (hard_violations IS NOT NULL AND hard_violations = 0 AND checked_occurrences = expected_occurrences)), 
	CONSTRAINT ck_validation_reports_evidence_object CHECK (jsonb_typeof(evidence) = 'object')
);

CREATE TABLE teaching.timetable_versions (
	series_id UUID NOT NULL, 
	run_id UUID NOT NULL, 
	validation_report_id UUID NOT NULL, 
	validation_passed BOOLEAN NOT NULL, 
	solver_success BOOLEAN NOT NULL, 
	version_number INTEGER NOT NULL, 
	previous_version_id UUID, 
	lifecycle_status VARCHAR(30) NOT NULL, 
	scope_completion VARCHAR(40) NOT NULL, 
	academic_complete BOOLEAN NOT NULL, 
	created_by_identity_user_id UUID, 
	id UUID NOT NULL, 
	institution_id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_timetable_versions PRIMARY KEY (id), 
	CONSTRAINT fk_timetable_versions_institution_id_series_id_run_id_g_5ea6 FOREIGN KEY(institution_id, series_id, run_id) REFERENCES teaching.generation_runs (institution_id, series_id, id) ON DELETE RESTRICT, 
	CONSTRAINT fk_timetable_versions_institution_id_run_id_solver_succ_fa99 FOREIGN KEY(institution_id, run_id, solver_success) REFERENCES teaching.generation_runs (institution_id, id, solver_success) ON DELETE RESTRICT, 
	CONSTRAINT fk_timetable_versions_institution_id_run_id_validation__fa0d FOREIGN KEY(institution_id, run_id, validation_report_id, validation_passed) REFERENCES teaching.validation_reports (institution_id, run_id, id, passed) ON DELETE RESTRICT, 
	CONSTRAINT fk_timetable_versions_institution_id_series_id_previous_1c0a FOREIGN KEY(institution_id, series_id, previous_version_id) REFERENCES teaching.timetable_versions (institution_id, series_id, id) ON DELETE RESTRICT, 
	CONSTRAINT uq_timetable_versions_institution_id_series_id_version_number UNIQUE (institution_id, series_id, version_number), 
	CONSTRAINT uq_versions_series_id UNIQUE (institution_id, series_id, id), 
	CONSTRAINT uq_versions_tenant_id UNIQUE (institution_id, id), 
	CONSTRAINT uq_timetable_versions_institution_id_run_id UNIQUE (institution_id, run_id), 
	CONSTRAINT ck_timetable_versions_version_chain CHECK (version_number > 0 AND ((version_number = 1 AND previous_version_id IS NULL) OR (version_number > 1 AND previous_version_id IS NOT NULL))), 
	CONSTRAINT ck_timetable_versions_validated_success CHECK (solver_success AND validation_passed), 
	CONSTRAINT ck_timetable_versions_draft_only CHECK (lifecycle_status = 'DRAFT_VALIDATED'), 
	CONSTRAINT ck_timetable_versions_known_scope_complete CHECK (scope_completion = 'COMPLETE_SUPPORTED_SCOPE')
);

CREATE TABLE teaching.deliveries (
	version_id UUID NOT NULL, 
	delivery_reference TEXT NOT NULL, 
	teaching_date DATE NOT NULL, 
	start_minute INTEGER NOT NULL, 
	end_minute INTEGER NOT NULL, 
	delivery_mode VARCHAR(10) NOT NULL, 
	staff_reference TEXT NOT NULL, 
	venue_reference TEXT, 
	student_count INTEGER NOT NULL, 
	id UUID NOT NULL, 
	institution_id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_deliveries PRIMARY KEY (id), 
	CONSTRAINT fk_deliveries_institution_id_version_id_timetable_versions FOREIGN KEY(institution_id, version_id) REFERENCES teaching.timetable_versions (institution_id, id) ON DELETE RESTRICT, 
	CONSTRAINT uq_deliveries_version_id UNIQUE (institution_id, version_id, id), 
	CONSTRAINT uq_deliveries_institution_id_version_id_delivery_reference UNIQUE (institution_id, version_id, delivery_reference), 
	CONSTRAINT ck_deliveries_time_range CHECK (start_minute >= 0 AND end_minute <= 1440 AND end_minute > start_minute), 
	CONSTRAINT ck_deliveries_student_count CHECK (student_count > 0), 
	CONSTRAINT ck_deliveries_venue_mode CHECK ((delivery_mode = 'ONLINE' AND venue_reference IS NULL) OR (delivery_mode = 'PHYSICAL' AND venue_reference IS NOT NULL))
);

CREATE INDEX ix_deliveries_staff_date ON teaching.deliveries (institution_id, staff_reference, teaching_date);

CREATE INDEX ix_deliveries_venue_date ON teaching.deliveries (institution_id, venue_reference, teaching_date);

CREATE TABLE teaching.delivery_coverage (
	institution_id UUID NOT NULL, 
	version_id UUID NOT NULL, 
	occurrence_reference TEXT NOT NULL, 
	delivery_id UUID NOT NULL, 
	requirement_reference TEXT NOT NULL, 
	offering_reference TEXT NOT NULL, 
	group_reference TEXT NOT NULL, 
	session_type VARCHAR(12) NOT NULL, 
	duration_minutes INTEGER NOT NULL, 
	academic_year INTEGER NOT NULL, 
	semester INTEGER NOT NULL, 
	stream VARCHAR(10) NOT NULL, 
	specialization TEXT NOT NULL, 
	CONSTRAINT pk_delivery_coverage PRIMARY KEY (institution_id, version_id, occurrence_reference), 
	CONSTRAINT fk_delivery_coverage_institution_id_version_id_delivery_cc66 FOREIGN KEY(institution_id, version_id, delivery_id) REFERENCES teaching.deliveries (institution_id, version_id, id) ON DELETE RESTRICT, 
	CONSTRAINT ck_delivery_coverage_duration CHECK (duration_minutes > 0), 
	CONSTRAINT ck_delivery_coverage_session_type CHECK (session_type IN ('LECTURE','TUTORIAL','WORKSHOP','LAB')), 
	CONSTRAINT ck_delivery_coverage_study_period CHECK (academic_year BETWEEN 1 AND 4 AND semester BETWEEN 1 AND 2), 
	CONSTRAINT ck_delivery_coverage_stream CHECK (stream IN ('WEEKDAY','WEEKEND'))
);

CREATE INDEX ix_coverage_group ON teaching.delivery_coverage (institution_id, version_id, group_reference);
