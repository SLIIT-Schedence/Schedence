# Step 12C.2.1: durable jobs, effective permissions and Swagger

The PostgreSQL queue is implemented and exercised against the dedicated isolated
database. Production execution remains **DISABLED**. No production migration,
Identity grant change or timetable write is performed by this step.

## Architecture and ownership

There is still no executable Celery/Redis/broker infrastructure or gateway in the
monorepo; those diagrams are conceptual. The queue uses PostgreSQL rows directly,
so acceptance and queue storage commit together without a broker/outbox gap.

| Table | Responsibility |
| --- | --- |
| `teaching.generation_jobs` | Request, idempotency hash, immutable accepted snapshot link, lifecycle, lease/deadline, result references |
| `teaching.generation_job_attempts` | Attempt identity, worker/fencing token, timestamps, outcome and private diagnostic report |

No master tables are copied or changed. Immutable `generation_runs` remains
terminal native solver history. Its existing ten-table ORM/security boundary is
unchanged; the queue has a separate runtime mapping and additional RLS checks.
Queue constraints are maintained in frozen migration SQL, and those two tables
are excluded from Alembic autogeneration to prevent unintended constraint/table
removal by the separate runtime mapping.

`source_snapshot_reference` retains the original request's UUID as a provenance
reference. It can name a read-only source database snapshot when the result
database is isolated. `work_snapshot_id` has a tenant-composite FK to the exact
accepted scoped input document in the result database. Derived documents retain
parent UUID/digest, source references, approved estimates, synthetic registration
provenance, experimental calendar and unresolved Research/DS mappings.

The solver still receives detached Python contracts. Existing CP-SAT constraints,
monolithic/coordinated strategies, attendance and independent validation are
unchanged. Native statuses remain distinct from job states.

## Acceptance, workers and recovery

The authenticated API first checks exact `teaching:generate` permission in the
trusted Identity institution grant. It validates/loads existing inputs, then
commits the accepted snapshot and PENDING job in one tenant-bound transaction.
Only after that commit does POST return 202 with Location.

Institution advisory transaction locks serialize quota and idempotency decisions
across connections and API processes. Institution/key uniqueness survives
restarts. Same key and canonical payload returns the original job, including a
completed/failed job. A different payload is 409. Another key for an identical
active payload is also 409. Pending and running jobs count toward the institution
limit (default 2). The database enforces active-payload uniqueness independently.

Workers claim with row locks/`SKIP LOCKED` and a tenant quota lock. The default
running limit is 1 per institution across worker processes. Each process has a
bounded worker count and rotates through its explicitly configured institutions;
workers never scan all tenants with BYPASSRLS.

A claim commits a random fencing token, attempt number, deadline and lease
(default 30 seconds). Heartbeats renew at one-third of the lease period, bounded
by the attempt's wall deadline. Expired/stale tokens cannot heartbeat or save.
Accepted immutable inputs are loaded from SQL and compared with the owner's
reconstructed inputs before solving. Changes fail rather than silently replacing
the accepted demand. The native solver runs in the existing disposable process;
attempt-specific directories avoid report collisions on retry.

Expired leases are reconciled transactionally on subsequent claims. Attempts are
retained, and safe infrastructure failures/restarts retry the same job with a new
token (default at most 2 attempts, configurable to 1–5). Backoff defaults to
`attempt_number × 1 second`. Timeouts, native UNKNOWN/INFEASIBLE/MODEL_INVALID,
input changes, validation failures and stale persistence parents are not
automatically retried. Failure DTOs expose safe categories; private reports retain
native results or structural failure diagnostics without credentials.

Graceful shutdown cancels/reaps computation and requeues interrupted owned work
within its attempt budget. Abrupt process loss leaves a lease that a replacement
worker expires/reclaims. No process-local registry is needed to retrieve status
after restart. `elapsed_seconds` measures wall time since the first attempt,
including retries; native solver runtime is reported separately.

Successful completion locks and verifies the current live lease. Existing
`persist_in_transaction` composes the reviewed Step 12B.4 save inside that same
transaction: independent validation, run/report/version/bookings/coverage, attempt
completion and job COMPLETED commit atomically. A failure after draft preparation
rolls everything back. Only exact complete validated coverage becomes a draft;
publication/approval is never automatic. Lease/deadline are checked again before
commit so delayed/stale workers cannot finalize.

## Prepared migration and production review plan

Prepared assets:

- `migrations/versions/20261016_0002_generation_jobs.py`
- `migrations/versions/20261016_0002_generation_jobs.sql`
- `migrations/teaching-jobs-runtime-role.review.sql`

Revision 0002 follows the existing 0001. It adds only the two Teaching tables,
indexes and forced institution RLS policies. Both USING and WITH CHECK use the
reviewed transaction-local institution predicate. Tenant-composite references
protect snapshots and result history. No history triggers/rows or owning-service
schemas are altered. The earlier job proposal is explicitly marked superseded.

The isolated tests apply these DDL assets only after verifying the exact
loopback test database and absence of a Neon project. They add SELECT/INSERT/UPDATE
for the existing restricted **local** runtime role on the two queue tables; no
DELETE, DDL, ownership or BYPASSRLS is granted.

Before any production deployment, review this exact SQL/grant diff, backup and
restore protection, current migration head and target branch; rehearse the full
upgrade on a disposable database; review restricted API/worker roles and RLS;
approve a deployment window and rollback/retention procedure. Downgrade of the
new queue intentionally refuses to delete accepted jobs/audit records. No
migration runs at application startup. Do not apply the superseded proposal.

Production execution is not exposed by the current runtime adapter: it accepts
only the dedicated loopback database for queue writes and completion. Removing
that guard requires separate owner/security review, authorized production
configuration, recovery rehearsals, private-report retention/encryption,
monitoring, database/time limits, clock synchronization and supervised process
resources (including cleanup of child processes after host/process failure).
Isolated restart tests establish durability of this implementation, not an
already-deployed production worker service.

## Real Academic Admin authorization

Read-only production inspection confirmed that Identity's actual
`role_permissions` maps **ACADEMIC_ADMIN** to `teaching:generate` and
`teaching:approve`. The inspected database had **zero active Academic Admin
assignments** (active local users with non-revoked assignments). No grant was
created/changed. A specific real account has not been identified or authenticated
as part of the automated fixture demonstrations.

| Operation | Existing effective permission |
| --- | --- |
| Admin timetable read | `teaching:timetables:read`, or compatibility `teaching:generate` / `teaching:approve` |
| Generation submission/polling | **`teaching:generate`**, explicitly in the selected Identity institution grant |

The extended read permissions are Teaching contracts; they are not automatically
seeded into Identity by this service. Lecturer/self and published-student reads
retain their existing additional owner-binding/lifecycle checks. HOD, Dean,
Professor, coordinator, JWT groups, arbitrary headers and an Academic Admin
display label grant nothing by themselves. Every API request re-resolves current
Identity permissions; tokens and user grants are not cached in job responses.

Identity owns user provisioning and system role assignment. A manager with
`identity:roles:assign` for the institution can use its existing
`PUT /api/v1/identity/users/{user_id}/roles`. This **replaces** the institution's
active role set, so an approved change must retain every intended existing role.
Identity prevents self-role modification and enforces privileged-role checks.
Its creation/provisioning workflow can also assign the selected existing role.
Those operations were inspected, not executed. Existing source `role_permissions`
is the authoritative grant path, never Cognito role labels or Academic appointments.

Safe current-account inspection:

1. Authenticate normally with the existing Cognito application.
2. In Identity Swagger (`http://localhost:8001/docs`), call
   `GET /api/v1/identity/me/permissions` with your access token. It returns your
   active account's institution roles/effective permissions without writing.
3. Alternatively run the following Teaching CLI. It verifies the shared Cognito
   token, calls the same Identity endpoint and prints only user UUID, institutions,
   roles, Teaching permissions and effective read mode/generation capability.
   The prompt hides the token, refuses an unsafe echo fallback, and never stores it.

```powershell
.\.venv\Scripts\python.exe -m schedence_teaching.api.inspect_permissions
```

Do not paste tokens into chat, command arguments, query strings or log files.
If `teaching:generate` is absent, request a separately approved Identity management
change. Do not seed or grant roles from Teaching to make the demo work.

## Startup and Swagger walkthrough

The monorepo launcher currently starts Identity on 8001 and Academic on 8002;
it does not include Teaching. The previous Teaching README's 8001 command conflicted
with Identity and is corrected to a separate local port, **8003**.

From repository root, with existing configuration in the ignored environment:

```powershell
.\.venv\Scripts\python.exe -m uvicorn schedence_teaching.main:app --app-dir services/teaching-service/src --host 127.0.0.1 --port 8003 --loop asyncio:SelectorEventLoop
```

Swagger: **http://localhost:8003/docs**. OpenAPI: **http://localhost:8003/openapi.json**.
The explicit selector loop supports psycopg async on Windows. Both
`/api/teaching` and `/api/v1/teaching` aliases appear in Swagger: five timetable
read operations and two generation operations under each prefix. There is one
shared HTTP bearer scheme; authorization is not persisted across page reloads.

1. Start Identity using its existing configuration on 8001, then Teaching using
   the command above. Keep production generation mode **DISABLED**.
2. Open Teaching Swagger and click **Authorize → BearerToken**.
3. Paste a valid **Cognito access token only**, without adding `Bearer ` to the
   value. Use an access token, not an ID token. Swagger adds the header. An access
   token can come from the existing application login or Identity's configured
   DevTools OAuth flow; no new login system/app client is introduced here.
4. Expand `GET /api/teaching/timetables`, choose **Try it out**, provide your
   authorized institution UUID and optional filters, then Execute. Expect 200
   only when Identity provides the required effective read permission. With
   default read-only configuration, the historical experimental drafts remain
   readable to authorized admins. A missing grant is 403, not an empty success.
5. For **isolated write-capable testing only**, configure the isolated deployment
   described below or run the private synthetic demonstration. Expand POST
   `/api/teaching/generations`, provide a stable `Idempotency-Key` header and
   a request built from the selected actual snapshot's period/source references.
   Omitted `specializations` selects all applicable scopes. No assignments are
   accepted. Default disabled execution returns 503 after successful authorization.
6. A successful durable acceptance returns 202 with `job_id`, PENDING state,
   `durability: POSTGRESQL_DURABLE` and Location. Poll the Location (or GET
   `/api/teaching/generations/{job_id}` with institution UUID) every few seconds.
   A repeated identical POST/key returns the same job after restart as well.
7. When status is COMPLETED, verify PASSED validation and equal expected/scheduled
   counts. Use `timetable_version_id` with GET
   `/api/teaching/timetables/{version_id}`, `/sessions`, `/validation` and `/versions`.
   Research/DS/testing/experimental limitations remain visible. These are drafts.
   For FAILED, inspect the safe error category; do not interpret UNKNOWN as proof
   of infeasibility or a partial result as a complete timetable.

Representative request, using real selected snapshot references:

```json
{
  "institution_id": "<authorized institution UUID>",
  "snapshot_id": "<source snapshot UUID>",
  "academic_term_reference": "<exact selected snapshot term>",
  "semester": 1,
  "academic_years": [4],
  "streams": ["WEEKDAY"],
  "specializations": ["IT"],
  "calendar_label": "PP1_EXPERIMENTAL_CALENDAR",
  "calendar_reference": "<PP1:calendar-configuration source sha256>",
  "policy_version": "HISTORICAL_SNAPSHOT",
  "scheduling_policy_reference": "<PP1:synthetic-elective-registration-evidence source sha256>",
  "strategy": "MONOLITHIC",
  "max_time_seconds": 30.0,
  "job_timeout_seconds": 300.0
}
```

Successful job response fields include:

```json
{
  "job_id": "<persisted job UUID>",
  "status": "COMPLETED",
  "durability": "POSTGRESQL_DURABLE",
  "attempt_number": 2,
  "solver_status": "OPTIMAL",
  "validation_status": "PASSED",
  "expected_occurrences": 2,
  "scheduled_occurrences": 2,
  "timetable_version_id": "<validated draft UUID>",
  "academic_complete": false
}
```

This is an abbreviated synthetic example, not a claim that the full university
scope contains two sessions. API responses also carry request/calendar, timing,
snapshot and unresolved-requirement metadata. Private attempt reports and lease
tokens are never exposed through Swagger/job responses.

## Isolated configuration and worker commands

`TEACHING_GENERATION_MODE=DURABLE_ISOLATED` requires:

- Explicit source target fields `TEACHING_GENERATION_DATABASE`,
  `TEACHING_GENERATION_PROJECT_ID`, `TEACHING_GENERATION_BRANCH_ID` and restricted
  owner-reader configuration, as in Step 12C.2. These connections remain READ ONLY.
- `TEACHING_GENERATION_JOB_DATABASE_CONFIG`: an ignored JSON configuration file
  with `runtime_url` pointing to the dedicated local test database. URLs are not
  printed or accepted in API request bodies.
- `TEACHING_GENERATION_INSTITUTIONS`: a JSON array of allowed institution UUIDs.
- `TEACHING_GENERATION_ARTIFACT_DIRECTORY`: a private Teaching local-data path.
- Existing maximum/preflight timeout and institution job-limit settings.

The isolated result store is used by timetable reads in this mode, so generated
test versions are never confused with historical Neon versions. No schemas are
created automatically; isolated DDL/restricted-role setup is a prerequisite.
The ordinary app lifecycle starts configured durable workers. A separate worker
process can also be started using the same explicit configuration:

```powershell
.\.venv\Scripts\python.exe -m schedence_teaching.generation.worker_service
```

For a completely synthetic write-capable walkthrough, the ignored
`local-data/generation-preview/step12c2_1-reviewed/run_durable_demo.py` seeds valid
SQL fixtures and runs actual localhost Uvicorn instances with signed test tokens
and mocked Identity responses. It stops the first worker while RUNNING, restarts
the service over the same PostgreSQL queue, polls the same job and retrieves its
validated draft. No real account or production job/version is written. The
separate abrupt-exit integration test exercises lease expiry after `os._exit`.

## Remaining deployment work

The current implementation is proven against isolated PostgreSQL; production
rollout remains gated on the migration/security/recovery plan above and explicit
approval. The real dashboard user also needs an effective Identity Teaching grant;
the catalogue alone does not assign it. Long-scope resource/lease tuning and
production monitoring/report retention need deployment rehearsal. Snapshot and
calendar selection metadata can later be exposed through an authorized dashboard
catalogue. Frontend submission/polling/read views can reuse these APIs. Manual
editing must remain a separately authorized, independently revalidated append-only
version operation. No editing, approval, publication or frontend is implemented.

## Verification results

| Check | Result |
| --- | --- |
| Complete Teaching suite | **2,709 passed, 9 skipped** |
| Identity | **76 passed** |
| Academic | **33 passed** |
| Shared authentication | **29 passed** |
| New durable PostgreSQL tests | **17 passed**, included in Teaching total |
| New Swagger/self-inspection tests | **8 passed**, included in Teaching total |
| Live localhost demonstration | **42 HTTP checks passed**, 14 schema operations verified |
| Documented ordinary startup | **8003/docs verified**, generation DISABLED |
| Dependencies | `pip check`: no broken requirements |

The complete Teaching run enabled isolated PostgreSQL tests and existing separate
read-only Neon checks. The nine earlier production-write tests stayed disabled.
Tests cover durable acceptance/idempotency across new repository instances,
simultaneous submissions and worker claims, quota locks, lease expiry/heartbeats,
stale-token fencing, infrastructure retries, deadline failure, corrupt/partial
results, atomic job/draft rollback, and retrieval through the existing APIs.
One test launches a separate process that commits a claim and calls `os._exit`;
a replacement reclaims it after expiration and saves exactly one draft. Another
checks graceful worker restart with the same persisted job ID. Existing read,
authentication, scheduling and persistence tests remain included.

The live Uvicorn demo used signed **test** tokens, mocked Identity grants and
synthetic isolated PostgreSQL input records. It stopped a RUNNING worker, restarted
the service, completed the same job on attempt 2, and retrieved one validated
2/2-occurrence draft with no hard violations. It did not authenticate a real
campus account or regenerate full university semester schedules. The original
production 697/704-occurrence drafts and historical IT4070 coverage were verified
unchanged by before/after fingerprints. All eight original Excel files and other
services were hash-checked unchanged, with no missing original workspace files.

The sole test warning is the existing Starlette/httpx TestClient deprecation;
no dependency modification was required. Private evidence/logs are ignored under
`local-data/generation-preview/step12c2_1-reviewed/`, including:

- `final-tests/teaching-test-results.json` and full test log
- Owning-service test logs and results
- `http-verification.json` and `completed-durable-job.json`
- `documented-startup-verification.json`
- `identity-permission-inspection.json` (read-only role metadata/aggregate counts)
- `production-before.json`, `production-after.json`
- `affected-files-and-preservation.json`, `final-verification.json`

Executed root-venv commands:

```powershell
.\.venv\Scripts\python.exe services/teaching-service/local-data/generation-preview/step12c2_1-reviewed/final-tests/verify_step12c2_1.py
.\.venv\Scripts\python.exe services/teaching-service/local-data/generation-preview/step12c2_1-reviewed/verify_owning_service.py identity
.\.venv\Scripts\python.exe services/teaching-service/local-data/generation-preview/step12c2_1-reviewed/verify_owning_service.py academic
.\.venv\Scripts\python.exe services/teaching-service/local-data/generation-preview/step12c2_1-reviewed/verify_owning_service.py auth-core
.\.venv\Scripts\python.exe services/teaching-service/local-data/generation-preview/step12c2_1-reviewed/run_durable_demo.py
.\.venv\Scripts\python.exe services/teaching-service/local-data/generation-preview/step12c2_1-reviewed/verify_startup.py
.\.venv\Scripts\python.exe -m pip check
```

These private runners opt into the existing safe-target database checks and never
embed credentials. Ordinary offline tests remain available through
`python -m pytest services/teaching-service/tests -q` with database tests skipped.

## Files created or modified

| Action | Teaching Service paths |
| --- | --- |
| Created | `generation/db_models.py`, `generation/postgres.py`, `generation/durable.py`, `generation/worker_service.py` under `src/schedence_teaching/` |
| Created | `src/schedence_teaching/api/inspect_permissions.py` |
| Created | `migrations/versions/20261016_0002_generation_jobs.py` and `.sql` |
| Created | `migrations/teaching-jobs-runtime-role.review.sql` |
| Created | `tests/test_durable_generation.py`, `tests/test_generation_swagger.py` |
| Created | This design/Swagger/review guide |
| Modified | `src/schedence_teaching/main.py`, `api/runtime.py`, `api/generations.py`, `generation/contracts.py`, `persistence/timetable_service.py` |
| Modified | `migrations/env.py`, `tests/test_persistence_migrations.py` |
| Modified | `README.md`, `docs/proposals/generation-jobs.review.sql` (superseded notice) |

All source paths in the last two rows of application code are relative to
`src/schedence_teaching/`. No other-service source files, source workbooks or
earlier timetable artifacts were modified. The branch remains
`feat/teaching-timetable`; nothing was committed or pushed.
