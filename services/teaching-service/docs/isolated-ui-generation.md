# Isolated UI generation preparation

An unsolved, validated input version can be exposed to the wizard without creating
a timetable version or generation run. This remains a development capability;
the existing CP-SAT solver and its independent validators are unchanged.

`generation/prepared_inputs.py` binds a private `PreparedInputManifest` to two new
immutable snapshots in the dedicated loopback PostgreSQL database: the verified
source document and the scoped document with selected Scheduling Rules. The
manifest records the revision, request, UUIDs and semantic digests. Preparation
checks the normalized source children and both stored digests, reloads Academic
and Resource evidence using database-enforced READ ONLY transactions, and reuses
`prepare_database_work`. The exact saved request must reconstruct the saved
validated document. Other explicitly selected supported rules are revalidated;
their responses do not claim to reference the previously saved derivation.

The readiness response now optionally includes `stored_input` metadata and
`snapshot_stored: true`. This identifies an existing input version; GET/POST
readiness never stores anything. The frontend can construct the existing
generation request from that source snapshot, scope, term and source references
without relying on an already-generated timetable. Missing rules, a mismatched
scope, stale owner records, or a disabled worker still block Generate.

The runtime requires all of these explicit settings:

```dotenv
APP_ENV=development
TEACHING_READ_STORE=DATABASE
TEACHING_GENERATION_MODE=DURABLE_ISOLATED
TEACHING_GENERATION_READINESS=DEVELOPMENT
TEACHING_DATABASE_ENV_FILE=<restricted owner-reader credentials file>
TEACHING_GENERATION_DATABASE=<explicit read-only source database>
TEACHING_GENERATION_PROJECT_ID=<explicit source project>
TEACHING_GENERATION_BRANCH_ID=<explicit source branch>
TEACHING_GENERATION_JOB_DATABASE_CONFIG=<private isolated runtime_url configuration>
TEACHING_GENERATION_PREPARED_MANIFEST=<private prepared input manifest>
TEACHING_GENERATION_INSTITUTIONS=["<manifest institution UUID>"]
TEACHING_GENERATION_ARTIFACT_DIRECTORY=<private Teaching local-data directory>
```

Production/staging, non-isolated persistence, other institutions in the worker
allowlist, or a remote snapshot repository are rejected. Existing loopback-only
database identity, restricted-role and forced-RLS checks remain active. Source
Teaching history is not used for writes; Academic and Resource stay read-only.
No migrations run automatically. Never start the worker over an unchecked queue:
inspect active jobs for the allowed institution first. Submitting a generation
request starts execution; preparation and readiness must not submit one.

For the current SLIIT-MALABE preparation, ignored private artifacts are under
`local-data/ui-generation-preparation/`. `prepared-input-v1.json` records the
default rules and Year 1 / Semester 1 / Weekday request. The reviewed isolated
database listens on 127.0.0.1:52870; the separate Teaching API listens on
127.0.0.1:8005. `runtime-readiness-report.json` records successful no-solve
preparation and compatibility for 106 sessions and 30 student groups. Identity
permissions are unchanged and still checked normally by the API/UI.

The API launcher is `local-data/ui-generation-preparation/serve_isolated.py`.
It loads only its isolated overrides; shared credentials are not copied into
that file. Restart it after backend changes, with the Windows selector event
loop retained. The frontend's ignored `.env.local` now points Teaching requests
at port 8005. The old URL and service PID are recorded in that private directory.
Stop only the recorded isolated API process and the dedicated PostgreSQL cluster
when this development setup is no longer needed. Do not alter historical jobs
or use the source Neon database as a fallback.

Focused tests use synthetic owner rows and block CP-SAT:

```powershell
.venv/Scripts/python.exe -m pytest services/teaching-service/tests/test_prepared_generation_inputs.py services/teaching-service/tests/test_generation_readiness.py services/teaching-service/tests/test_scheduling_rules.py -q -p no:cacheprovider
```

No solver feasibility/quality claim is made until a separately authorized run.
