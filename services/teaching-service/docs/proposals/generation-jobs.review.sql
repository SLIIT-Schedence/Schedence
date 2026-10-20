-- SUPERSEDED by migrations/versions/20261016_0002_generation_jobs.{py,sql}.
-- Preserve this earlier proposal for review history; DO NOT execute it.
-- REVIEW PROPOSAL ONLY. Not an Alembic revision; do not apply in Step 12C.2.
-- Mutable orchestration belongs here, not in immutable generation_runs.
-- Requires owner/security review, durable worker implementation and tested recovery.
CREATE TABLE teaching.generation_jobs (
    id uuid PRIMARY KEY,
    institution_id uuid NOT NULL,
    requested_by_identity_user_id uuid NOT NULL,
    input_snapshot_id uuid NOT NULL,
    idempotency_key_sha256 varchar(64) NOT NULL CHECK (idempotency_key_sha256 ~ '^[0-9a-f]{64}$'),
    request_sha256 varchar(64) NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
    request_payload jsonb NOT NULL CHECK (jsonb_typeof(request_payload)='object'),
    state varchar(16) NOT NULL CHECK (state IN ('PENDING','RUNNING','COMPLETED','FAILED')),
    created_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    finished_at timestamptz,
    lease_expires_at timestamptz,
    lease_token uuid,
    attempt_number integer NOT NULL DEFAULT 0 CHECK (attempt_number >= 0),
    solver_status varchar(16) CHECK (solver_status IN ('OPTIMAL','FEASIBLE','INFEASIBLE','UNKNOWN','MODEL_INVALID')),
    result_run_id uuid,
    timetable_version_id uuid,
    safe_error_category varchar(64),
    UNIQUE (institution_id,id),
    UNIQUE (institution_id,idempotency_key_sha256),
    FOREIGN KEY (institution_id,input_snapshot_id) REFERENCES teaching.input_snapshots(institution_id,id),
    FOREIGN KEY (institution_id,result_run_id) REFERENCES teaching.generation_runs(institution_id,id),
    FOREIGN KEY (institution_id,timetable_version_id) REFERENCES teaching.timetable_versions(institution_id,id),
    CHECK ((state IN ('COMPLETED','FAILED')) = (finished_at IS NOT NULL)),
    CHECK (state <> 'RUNNING' OR (started_at IS NOT NULL AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)),
    CHECK (state <> 'COMPLETED' OR (solver_status IS NOT NULL AND solver_status IN ('OPTIMAL','FEASIBLE') AND result_run_id IS NOT NULL AND timetable_version_id IS NOT NULL)),
    CHECK (started_at IS NULL OR started_at >= created_at),
    CHECK (finished_at IS NULL OR finished_at >= created_at)
);
CREATE UNIQUE INDEX uq_generation_jobs_active_payload
    ON teaching.generation_jobs(institution_id,request_sha256) WHERE state IN ('PENDING','RUNNING');
CREATE INDEX ix_generation_jobs_claim ON teaching.generation_jobs(institution_id,state,created_at);
ALTER TABLE teaching.generation_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE teaching.generation_jobs FORCE ROW LEVEL SECURITY;
CREATE POLICY institution_isolation ON teaching.generation_jobs
    USING (institution_id = NULLIF(current_setting('schedence.institution_id',true),'')::uuid)
    WITH CHECK (institution_id = NULLIF(current_setting('schedence.institution_id',true),'')::uuid);
-- No grants/role changes are applied by this proposal. Future API/worker roles
-- must remain NOSUPERUSER/NOBYPASSRLS, use tenant-bound transactions and receive
-- only separately reviewed orchestration-column updates (not history updates).
-- Claim with FOR UPDATE SKIP LOCKED, a per-tenant advisory lock for quotas, and
-- fencing by lease_token + attempt_number for heartbeats/finalization.
-- The job row can serve as a PostgreSQL queue. If a broker is selected instead,
-- add a transactional outbox: never independently commit DB acceptance/broker send.
-- Finalize result_run/version refs and COMPLETED in the same transaction as the
-- existing reviewed draft save. Do not apply append-only triggers to this table.
