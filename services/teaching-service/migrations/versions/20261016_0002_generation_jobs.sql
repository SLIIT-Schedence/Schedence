-- Additive orchestration only. PRODUCTION REVIEW REQUIRED; no startup migration.
-- Existing immutable history tables/triggers and owning-service data are untouched.
CREATE TABLE teaching.generation_jobs (
 id uuid PRIMARY KEY,
 institution_id uuid NOT NULL,
 requested_by_identity_user_id uuid NOT NULL,
 source_snapshot_reference uuid NOT NULL,
 work_snapshot_id uuid NOT NULL,
 work_sha256 varchar(64) NOT NULL CHECK (work_sha256 ~ '^[0-9a-f]{64}$'),
 idempotency_sha256 varchar(64) NOT NULL CHECK (idempotency_sha256 ~ '^[0-9a-f]{64}$'),
 request_sha256 varchar(64) NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
 request_payload jsonb NOT NULL CHECK (jsonb_typeof(request_payload)='object'),
 summary jsonb NOT NULL CHECK (jsonb_typeof(summary)='object'),
 state varchar(16) NOT NULL CHECK (state IN ('PENDING','RUNNING','COMPLETED','FAILED')),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 started_at timestamptz,
 finished_at timestamptz,
 next_attempt_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 lease_token uuid,
 lease_expires_at timestamptz,
 last_heartbeat_at timestamptz,
 deadline_at timestamptz,
 attempt_number integer NOT NULL DEFAULT 0 CHECK (attempt_number>=0),
 max_attempts integer NOT NULL CHECK (max_attempts BETWEEN 1 AND 5),
 timeout_seconds double precision NOT NULL CHECK (timeout_seconds>0 AND timeout_seconds<=3600),
 solver_status varchar(16) CHECK (solver_status IN ('OPTIMAL','FEASIBLE','UNKNOWN','INFEASIBLE','MODEL_INVALID')),
 solver_runtime_seconds double precision CHECK (solver_runtime_seconds>=0 AND solver_runtime_seconds<'Infinity'::float8),
 expected_occurrences integer NOT NULL CHECK (expected_occurrences>0),
 scheduled_occurrences integer NOT NULL DEFAULT 0 CHECK (scheduled_occurrences>=0),
 validation_status varchar(16) NOT NULL DEFAULT 'NOT_ASSESSED' CHECK (validation_status IN ('NOT_ASSESSED','PASSED','FAILED')),
 result_run_id uuid,
 timetable_version_id uuid,
 safe_error_category varchar(64),
 safe_error_message varchar(300),
 retry_with_new_key boolean NOT NULL DEFAULT false,
 UNIQUE (institution_id,id),
 UNIQUE (institution_id,idempotency_sha256),
 FOREIGN KEY (institution_id,work_snapshot_id) REFERENCES teaching.input_snapshots(institution_id,id),
 FOREIGN KEY (institution_id,result_run_id) REFERENCES teaching.generation_runs(institution_id,id),
 FOREIGN KEY (institution_id,timetable_version_id) REFERENCES teaching.timetable_versions(institution_id,id),
 CHECK (attempt_number<=max_attempts),
 CHECK ((state IN ('COMPLETED','FAILED'))=(finished_at IS NOT NULL)),
 CHECK ((state='RUNNING')=(lease_token IS NOT NULL AND lease_expires_at IS NOT NULL AND deadline_at IS NOT NULL)),
 CHECK (state<>'RUNNING' OR started_at IS NOT NULL),
 CHECK (state<>'COMPLETED' OR (solver_status IS NOT NULL AND solver_status IN ('OPTIMAL','FEASIBLE') AND validation_status='PASSED' AND scheduled_occurrences=expected_occurrences AND result_run_id IS NOT NULL AND timetable_version_id IS NOT NULL)),
 CHECK ((state='COMPLETED')=(timetable_version_id IS NOT NULL)),
 CHECK (started_at IS NULL OR started_at>=created_at),
 CHECK (finished_at IS NULL OR finished_at>=created_at)
);
CREATE UNIQUE INDEX uq_generation_jobs_active_payload ON teaching.generation_jobs(institution_id,request_sha256) WHERE state IN ('PENDING','RUNNING');
CREATE INDEX ix_generation_jobs_claim ON teaching.generation_jobs(institution_id,state,next_attempt_at,created_at);
CREATE TABLE teaching.generation_job_attempts (
 id uuid PRIMARY KEY,
 institution_id uuid NOT NULL,
 job_id uuid NOT NULL,
 attempt_number integer NOT NULL CHECK (attempt_number>0),
 lease_token uuid NOT NULL,
 worker_id uuid NOT NULL,
 started_at timestamptz NOT NULL,
 finished_at timestamptz,
 outcome varchar(32) NOT NULL,
 error_category varchar(64),
 report jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(report)='object'),
 UNIQUE (institution_id,job_id,attempt_number),
 UNIQUE (institution_id,lease_token),
 FOREIGN KEY (institution_id,job_id) REFERENCES teaching.generation_jobs(institution_id,id),
 CHECK ((outcome='RUNNING')=(finished_at IS NULL)),
 CHECK (finished_at IS NULL OR finished_at>=started_at)
);
ALTER TABLE teaching.generation_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE teaching.generation_jobs FORCE ROW LEVEL SECURITY;
CREATE POLICY institution_isolation ON teaching.generation_jobs USING (institution_id=NULLIF(current_setting('schedence.institution_id',true),'')::uuid) WITH CHECK (institution_id=NULLIF(current_setting('schedence.institution_id',true),'')::uuid);
ALTER TABLE teaching.generation_job_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE teaching.generation_job_attempts FORCE ROW LEVEL SECURITY;
CREATE POLICY institution_isolation ON teaching.generation_job_attempts USING (institution_id=NULLIF(current_setting('schedence.institution_id',true),'')::uuid) WITH CHECK (institution_id=NULLIF(current_setting('schedence.institution_id',true),'')::uuid);
