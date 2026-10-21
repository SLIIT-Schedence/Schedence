# Portable Teaching runtime and schema reconciliation

The normal Teaching API reads its own restricted database URLs from the shared
`.env`. Owner reads for readiness and sending use GET-only HTTP with the current
verified caller's bearer token. No private owner credentials file, Neon target
pins or `pilot-previews/` input files participate in these paths. The settings
default to enabled in all APP_ENV values; execution of CP-SAT remains disabled.

Readiness, form sending and legacy worker configuration failures are isolated
from timetable reads and draft Forms. Exceptions are logged by category, without
secrets. Identity permissions are still fetched per request, and authorized
institution selection happens before any upstream owner call. New HTTP clients
reject redirects, partial exports, foreign-tenant data and ambiguous identities.
Lecturers use an owner `/me` link endpoint instead of the administrative roster.

The Python 3.11 nested f-string failure in `generation/readiness.py` is fixed by
computing `blocked_labels` first. All Teaching source files pass Python 3.11
grammar parsing. The installed test interpreter is Python 3.14; a native 3.11
interpreter is not installed here, so an actual 3.11 runtime smoke test remains
for CI/team verification.

## Current live integration blockers

The Academic teaching-input export and recipient roster/self-link endpoints
do not exist in this checkout. Resource has no HTTP runtime. Exact proposed
routes and payload requirements are documented in
[owner-api-integration.md](../../services/teaching-service/docs/owner-api-integration.md).
Those service owners need to implement/authorize them; their code has not been
modified here.

The canonical tenant also has no approved Teaching input snapshot. Readiness
reads and validates shared Teaching snapshots rather than local workbooks; an
absent snapshot is a MISSING check, not a fabricated READY result. The earlier
experimental PP1 inputs remain excluded and have not been reimported. An
approved input/calendar is required through the reviewed preparation workflow.

Consequently, startup/timetable availability and mocked authenticated readiness
integration can be verified now. A live web-app READY result cannot be claimed
until the missing owner APIs and approved inputs are supplied. No credentials
or authentication bypass is used to conceal these blockers.

## Neon schema reconciliation

Target is the existing `neondb`, project `super-leaf-21551515`, branch
`br-dry-darkness-b549ci92`. No database/Neon branch is created. Only Teaching's
schema, runtime grants and migration-version metadata are in scope.

Fresh backup `scripts/neon-review/backup-20261021T071800Z/neondb.dump`:
1,308,909 bytes, SHA-256
`f7a2acc52cccd99a854de71bb0e15e402279fbf525e258db252e244a9f71705f`.
All 106 tables and 21,697 rows restored with matching row hashes in the existing
local maintenance database, then rolled back. The isolated Teaching database
and other services were not changed.

`scripts/reconcile_teaching_schema.py` first compares the three existing Forms
tables, indexes, constraints, policies, RLS flags, immutable triggers and complete
function definitions with frozen revisions 0003/0004 in a transactional restore.
Function-body line endings are normalized between Windows and PostgreSQL;
SQL logic is compared in full. The initial live attempt stopped and rolled back
on that CRLF-only discrepancy before any DDL or marker change. The corrected
comparison passed the repeated local dry-run.

The reviewed apply executes pending 0002 queue DDL, preserves matching existing
0003/0004 Forms objects, applies restricted grants and advances
`teaching.alembic_version` from `20261016_0001` to `20261021_0004`. This explicitly
authorized metadata update replaces the earlier selective-deployment marker;
it never updates academic, Examination, historical or Forms application rows.
The new queue tables remain empty; no job is created or solver run.

Private restore SQL, full shape comparisons and apply reports are under ignored
`scripts/neon-review/teaching-schema-reconciliation-20261021/`. They must not
be committed. Normal `alembic upgrade head` sees the reconciled current head and
must not attempt to recreate the adopted Forms tables.

Apply result: **PASS**, two empty queue tables added, three Forms tables adopted,
one Alembic metadata row advanced, zero application rows changed, and all 105
protected non-marker tables retained their complete row hashes.

## Verification

- 206 focused Python tests passed, including authenticated timetable/draft reads
  while readiness and sending configuration fail; no solver/model construction.
- Fresh-clone launcher smoke passed on an unused temporary port with the private
  file paths, owner database URLs and Neon pins removed from the shared env copy.
  Preflight reported `20261021_0004`; Teaching started and `/health` returned 200.
  Only that test instance was stopped; existing services were not restarted.
- The real restricted shared-env runtime read canonical timetables (zero) and
  Forms (four) through the existing RLS-checked facades. Readiness and sending
  HTTP adapters configured successfully without private files; no worker started.
- Authenticated HTTP behavior was verified with the existing JWT/Identity test
  harness. No live user session was bypassed or claimed as an end-to-end READY
  result; the owner APIs and approved canonical input are still missing.
- Python 3.11 grammar, secret scan and `git diff --check` passed. No credentials,
  backup archives or private migration payloads are included in the commits.
