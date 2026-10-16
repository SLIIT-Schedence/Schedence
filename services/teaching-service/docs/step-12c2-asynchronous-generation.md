# Step 12C.2: asynchronous Academic Admin generation

Teaching now accepts privileged generation requests and exposes job status. The
existing solver, hard constraints, independent validation and immutable draft
persistence remain the execution boundaries. No production migration or write is
enabled by this step.

## HTTP and authorization

| Method | Canonical route | Purpose |
| --- | --- | --- |
| POST | `/api/v1/teaching/generations` | Validate inputs and accept an actual background task |
| GET | `/api/v1/teaching/generations/{job_id}` | Institution-scoped status and safe result metadata |

Both also have `/api/teaching` aliases. Existing timetable GET routes and health
are preserved. There is no cancellation, listing, editing, approval or publication
API. Cancellation is not advertised as a user capability.

Both routes reuse shared Cognito verification and fresh Identity
`/api/v1/identity/me/permissions` grants. **Explicit `teaching:generate`** is
required for the selected institution. It already belongs to Identity's
`ACADEMIC_ADMIN` catalogue; no owner-service changes or seeds were needed.
Read/approve permissions, JWT roles and academic appointments are insufficient.
GET can infer the institution only when exactly one current generation grant
exists; otherwise pass `?institution_id=<authorized UUID>`. Another institution's
job is not found, even if its ID is known.

An illustrative request (replace placeholders with actual selected snapshot
metadata; neither hash is a newly invented policy ID):

```http
POST /api/teaching/generations
Authorization: Bearer <Cognito access token>
Idempotency-Key: semester1-it-weekday-preview-1
Content-Type: application/json

{
  "institution_id": "<authorized institution UUID>",
  "snapshot_id": "<existing input snapshot UUID>",
  "academic_term_reference": "<exact snapshot academic term>",
  "semester": 1,
  "academic_years": [4],
  "streams": ["WEEKDAY"],
  "specializations": ["IT"],
  "calendar_label": "PP1_EXPERIMENTAL_CALENDAR",
  "calendar_reference": "<64-hex PP1:calendar-configuration source sha256>",
  "policy_version": "HISTORICAL_SNAPSHOT",
  "scheduling_policy_reference": "<64-hex PP1:synthetic-elective-registration-evidence source sha256>",
  "strategy": "MONOLITHIC",
  "max_time_seconds": 30.0,
  "job_timeout_seconds": 300.0
}
```

Omitting `specializations` selects all supported/blocked applicable mappings;
omitting study years/streams selects all years/both streams. Semester is singular:
these remain separate semester alternatives, never a concurrency claim. The
existing read DTO's source-labelled `policy_sources` provides source hashes;
`calendar.sha256` hashes the calendar itself and is **not** the configuration
source reference. The academic term reference is checked against the stored
snapshot; this step does not create a calendar/policy catalogue API.

When enabled and preparation succeeds, POST returns **202**, `Location`,
`Retry-After: 2`, and a real job ID. When disabled it returns **503** rather than a
pretend acceptance. Other statuses include 400 for missing/invalid idempotency
keys, reference mismatch or incompatible source scope; 401 invalid authentication;
403 permission/institution denial; 404 missing scoped job; 409 conflicting
idempotency payload, duplicate active solve, resource limit or stale draft parent;
422 malformed typed requests. Failed input loading returns safe 503 and reserves
no key. Input validation does not accept arbitrary assignments or solver internals.

Job DTOs contain PENDING/RUNNING/COMPLETED/FAILED, UTC timestamps, elapsed and
native solver runtime, snapshot/scope/configuration references, separate native
solver status, expected/scheduled coverage, validation, optional draft version and
result-snapshot IDs, unresolved issue codes and blocked student totals. They
contain no staff rosters, solver JSON, exception text, logs or filesystem paths.

## Development execution, isolation and limits

The README's Celery/Redis/gateway diagram is conceptual. No executable queue,
broker or worker integration exists in the monorepo. Therefore
`DevelopmentGenerationJobs` is explicitly **DEVELOPMENT_NON_DURABLE**. It runs in
one API process/event loop, with a bounded in-memory registry and bounded worker
concurrency. It must not be deployed with multiple API workers or replicas.

Production defaults to `TEACHING_GENERATION_MODE=DISABLED`. Opt-in private preview
configuration requires all of:

```text
TEACHING_GENERATION_MODE=DEVELOPMENT
TEACHING_GENERATION_ARTIFACT_DIRECTORY=<new directory under Teaching local-data>
TEACHING_GENERATION_DATABASE=<explicit expected database>
TEACHING_GENERATION_PROJECT_ID=<explicit expected project>
TEACHING_GENERATION_BRANCH_ID=<explicit expected branch>
TEACHING_DATABASE_ENV_FILE=<ignored restricted owner credentials file>
```

Other limits: `TEACHING_GENERATION_MAX_TIMEOUT_SECONDS` defaults to 600;
`TEACHING_GENERATION_PER_INSTITUTION_LIMIT` defaults to 2. Registry capacity is
1,000 and native worker concurrency 1 by default in the injectable abstraction.
No broker dependency is added. Each worker is a spawned disposable OS process;
only detached input data is transferred, never live database sessions or
credentials. The wall deadline covers child startup, reconstruction, native solve
and child validation. Native `max_time_seconds` is per subproblem for coordinated
cohort solves. Parent validation and optional transactional persistence are
separate completion stages. A timed-out process is terminated and joined, not
left consuming CPU in a background thread.

Input preparation uses restricted owner connections with READ ONLY transactions
and RLS, closes them before solving, and moves CPU preparation off the HTTP loop.
Preflight timeout is 90 seconds, configurable with
`TEACHING_GENERATION_PREFLIGHT_TIMEOUT_SECONDS` (maximum 180). Remote owner
validation can require many round trips; a timeout never claims acceptance.
A per-registry lock serializes submissions and
preparation, while health/polling continue. Native solving never blocks the HTTP
request. Pending queue time is separate from running elapsed time.

Idempotency is scoped by institution and key. Canonical payload hashing handles
unordered scope sets consistently. Concurrent same-key/same-payload requests
reuse one job, including completed/failed results; different payload is 409.
An identical active payload under another key is also 409. Per-institution
limits include pending and running jobs. Registry exhaustion rejects requests
without evicting old keys. Retry a failed job only with a new key; no automatic
retry of expensive or unvalidated results occurs.

Graceful shutdown rejects submissions, terminates active computation, marks
pending/running jobs FAILED/WORKER_INTERRUPTED, and lets an already-started save
transaction finish. **Abrupt process/host loss loses the registry, keys and job
status.** Existing private files are diagnostics, not resumable queue state.
After restart lost IDs are 404; cross-restart idempotency is not claimed. The
development worker must not be used where durable acceptance is required.

## Generation and draft boundary

1. Authorize using the caller's fresh trusted Identity grant.
2. Verify exact snapshot institution, academic term, source-hashed calendar and
   historical policy selection; build inputs through existing owner validation.
3. Preserve an exact scoped snapshot and its original parent UUID/digest,
   request digest, source checksums and authoritative owner links. Scoped results
   are never saved against an unrelated full-semester demand snapshot.
4. Run the existing monolithic or coordinated-cohort solver. The latter keeps
   global staff/venue reservations and original union validation; unsupported
   joint-feature inputs fail rather than relax their constraints.
5. Reuse elective validation and `prepare_attempt` independent validation.
   Recompute validation in the parent before any persistence call. Missing,
   repeated, corrupt or incomplete coverage cannot create a validated draft.
6. UNKNOWN/INFEASIBLE/MODEL_INVALID remain native solver statuses on a FAILED job;
   no version is saved. Private input/result/validation/stage/log diagnostics stay
   under ignored local-data. Worker errors and timeouts expose safe categories.
7. Preview persistence is DISABLED. A valid preview is COMPLETED with counts and
   no database version. The isolated adapter permits only the existing dedicated
   `127.0.0.1:<port>/schedence_step12b4_test` database, rejects URL overrides, and
   verifies there is no Neon project at execution. **No production write adapter
   or production write-enabling switch is supplied.**
8. Isolated saves call Step 12B.4 `persist_generation_result`, retaining forced
   tenant RLS, complete validation, transactional run/report/version/bookings,
   idempotent saves and immutable history. Optional `timetable_series_id` plus
   `expected_previous_version_id` supports appending a new validated draft.
   Without a series, the isolated adapter creates a distinct development series.

Year 4 exact two-elective registrations, compulsory Research pending mapping,
blocked DS allocations, original contact hours, availability/protection,
hierarchy/attendance, hybrid modes, venue constraints and lunch remain unchanged.
The experimental Monday 18:30 configuration retains its source provenance.
Academic completeness is never inferred from known-session coverage. Current
IT4070 classification reviews do not rewrite historical snapshot policy or
historical drafts. Source Excel is not read by this API pipeline.

## Production deployment review still required

`generation_runs` is append-only terminal solver history, with native statuses;
it cannot safely represent mutable job lifecycle. The separate
[review-only job schema](proposals/generation-jobs.review.sql) proposes forced
tenant RLS, unique institution/idempotency hashes, active-payload uniqueness,
lease/fencing fields and tenant-bound snapshot/result foreign keys. It is not an
Alembic revision and is not executed by the application or tests.

Before production execution: select and implement a durable queue (PostgreSQL
claim/lease or broker plus transactional outbox); atomically enforce institution
quotas across replicas; persist acceptance before 202; implement heartbeat,
abandoned-job reconciliation, fenced retries and atomic result/job finalization;
review role grants and apply the approved migration with recovery protection;
provide private encrypted report storage/retention, operational monitoring,
bounded worker resources and production write authorization. Keep Cognito and
fresh Identity checks at the API boundary; internal workers must not infer system
privileges from academic ranks. Re-test RLS, concurrency and crash recovery before
turning on production writes.

Manual editing should later be a separate authorized operation that appends a
version and revalidates all hard constraints. A frontend can submit with a stable
key, follow Location, poll with backoff, and use existing read views only after a
version ID exists. Neither future feature is implemented here.

## Verification

| Check | Result |
| --- | --- |
| Complete Teaching suite | **2,684 passed, 9 skipped** |
| Identity suite | **76 passed** |
| Academic suite | **33 passed** |
| Shared auth suite | **29 passed** |
| New isolated generation/PostgreSQL tests | **3 passed**, included in Teaching total |
| Localhost Uvicorn HTTP assertions | **19 passed**, plus successful polling |
| Dependency integrity | `pip check`: no broken requirements |

The full Teaching run enabled isolated PostgreSQL and separate read-only Neon
checks. The nine earlier production-write tests remained disabled. The new tests
cover real spawned native solving, cross-process evidence checksums, process
termination, registry concurrency/shutdown, authorization, stale/idempotent
requests, unsafe write targets, independent validation and immutable SQL history.
The existing Starlette/httpx TestClient deprecation warning remains; no dependency
change was needed.

The real localhost demonstration used **locally signed authentication fixtures
and mocked trusted Identity responses**, not real Cognito users. Restricted live
owner connections loaded database inputs. A Year 4 Semester 1 IT Weekday preview
scheduled **16/16**, OPTIMAL, independently validated, with persistence DISABLED.
This is an elective-component preview; compulsory Research remains unresolved.
Before/after fingerprints show that production's 697/704 historical drafts,
1,401 deliveries/coverage entries and 14 Semester 2 IT4070 occurrences were
unchanged. No new production job, snapshot or timetable version was written.

Recorded execution commands (root virtual environment):

```powershell
.\.venv\Scripts\python.exe services/teaching-service/local-data/generation-preview/step12c2-reviewed/verified-tests/verify_step12c2.py
.\.venv\Scripts\python.exe services/teaching-service/local-data/generation-preview/step12c2-reviewed/verify_owning_service.py identity
.\.venv\Scripts\python.exe services/teaching-service/local-data/generation-preview/step12c2-reviewed/verify_owning_service.py academic
.\.venv\Scripts\python.exe services/teaching-service/local-data/generation-preview/step12c2-reviewed/verify_owning_service.py auth-core
.\.venv\Scripts\python.exe services/teaching-service/local-data/generation-preview/step12c2-reviewed/localhost-final/run_demo.py
.\.venv\Scripts\python.exe -m pip check
```

These private runners/logs remain Git-ignored, contain no inline credentials,
and do not replace the repository tests. For ordinary offline unit verification
use `python -m pytest services/teaching-service/tests -q`; database integrations
require their existing explicit safe-target opt-ins. Windows entry-point scripts
that spawn solver processes must use an `if __name__ == "__main__"` guard.

Private evidence is under `local-data/generation-preview/step12c2-reviewed/`:
`verified-tests/teaching-test-results.json`, owning-service logs,
`localhost-final/http-verification.json`, the job's solver/validation reports,
production preservation hashes, and affected-file/workspace preservation reports.
All eight original Excel files and other-service files were hash-checked unchanged;
no original workspace file was removed. Prior outputs were preserved.
