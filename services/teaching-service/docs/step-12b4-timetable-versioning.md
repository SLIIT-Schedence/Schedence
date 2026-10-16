# Step 12B.4: timetable persistence and version management

The application layer can save a complete independently validated solver result,
retrieve its timetable and views, and append immutable versions. Writes were
tested using synthetic records in an isolated local PostgreSQL 18.6 database.
Production access in this step was read-only: no new production version, source
import, schema change, grant or master-record change was performed.

## Operations and data retained

`persist_generation_result(factory, institution_id, series_id, snapshot_id,
result, expected_previous_version_id=..., logical_status="VALIDATED")` is an
explicit application operation. It does not load environment configuration,
generate a timetable, read Excel or publish anything. The caller supplies a
trusted institution identity and session factory. The existing shared db-core
unit of work commits everything together or rolls it back on any exception.

The repository first replays the selected immutable snapshot and runs the existing
independent validator through `prepare_attempt`. An incomplete result, invalid
assignment, unsuccessful solve, missing physical venue or status outside
DRAFT/VALIDATED cannot become a saved timetable. The existing `append_attempt`
continues to support failed-run diagnostics for earlier callers; the new safe
application operation accepts only successfully validated complete results.

| Retained information | Existing destination |
| --- | --- |
| Institution, academic term, study year/semester/stream/specialization | Tenant columns plus the selected `input_snapshots` and `snapshot_scopes` |
| University source checksums, estimated allocations, synthetic elective evidence, policy and unresolved requirements | Immutable snapshot sources/issues and source document; derived limitations also retained in new run/validation metadata |
| Calendar identity and rules | Snapshot UUID plus calendar/rules SHA-256 references; full configuration stays in the selected snapshot, without inventing a calendar master UUID |
| Solver status, runtime, objective values and all existing diagnostic reports | `generation_runs.result_metadata` and relational run columns |
| Independent validation and limitations | `validation_reports.evidence`, including the derived persistence metadata |
| Version number and predecessor | `timetable_versions` |
| One actual physical/online teaching booking, staff, venue and date/time | `deliveries` |
| Every covered original occurrence, requirement, offering and group | `delivery_coverage` |

The selected snapshot is the exact demand boundary. A result for a smaller scope
cannot be saved against a larger snapshot. Such a future scope needs its own
validated input snapshot first. No real offering IDs or missing academic
requirements are fabricated. Objective fields and ordered objective priorities
are preserved, not recalculated or relaxed by this layer.

## Versioning and idempotency

`SqlTeachingRepository.save_validated_result` locks the existing series row and
checks runs for that same institution, series and snapshot before appending.
It delegates actual inserts to the existing `append_attempt` implementation.

The save identity includes the semantic input snapshot, solver output and logical
draft/validation status. It excludes elapsed runtime and the attached untrusted
validator summary, which is recomputed before every save. It normalizes session
ordering and order-independent preference observation collections while retaining
all observations, penalties, objective values and **ordered** objective priorities.
Sources, registrations, rules and policy version remain part of snapshot identity.
Different input snapshots or genuinely different output/status produce a new
version rather than overwriting history.

- An identical save returns the same run/report/version IDs with `reused=True`.
  A retry does not replace the original runtime, validation or author evidence.
- A stale identical retry may reuse its original historical version even after a
  later version was appended. A genuinely different stale save is rejected.
- Concurrent identical writers serialize and reuse one version. Concurrent
  different writers using the same parent yield one success and one stale-parent
  conflict; the second can be reviewed and retried against the new parent.
- The database's unique series/version number, delivery reference and occurrence
  coverage constraints remain a second line of duplicate protection.
- Idempotency applies to cooperating repository/application writers. No new
  database fingerprint constraint or schema change was introduced.
- Historical rows without new metadata are compared using their original result
  documents and snapshot; no fingerprint is backfilled into immutable history.

## Retrieval

Within `async with timetable_reader(factory, institution_id) as repository`:

- `get_series(series_id)` returns the series and ordered version summaries.
- `list_versions(series_id)` retains the existing history interface.
- `get_latest_draft(series_id)` returns the newest stored draft or `None` for an
  existing empty series.
- `get_version(series_id, version_number)` retrieves a specific numbered version.
- `get_timetable(version_id)` retrieves the detached `TimetableRecord`.

The record exposes `deliveries`, `coverage`, original snapshot, run, version,
validation and derived academic limitations. It provides `lecturer_timetable`,
`venue_timetable`, `student_group_timetable`, `specialization_timetable` and
`coverage_report` operations. Student views include ancestor/subgroup attendance
and explicit roster intersections; they do not invent unknown overlap. A returned
booking retains its original covered groups, including when only some members of
the queried population attend it. Combined deliveries appear once per view,
with all original occurrence coverage attached.

Read-back checks expected occurrence identities, original requirement metadata,
counts and coverage uniqueness. New records also carry a normalized booking
digest and matching run/validation provenance. Corrupt or missing evidence fails
closed. Legacy records use their existing validation and exact original coverage;
the prior independent historical validation remains preserved.

## Lifecycle compatibility and deliberate limits

The requested public vocabulary is DRAFT, VALIDATED, PENDING_APPROVAL, APPROVED,
PUBLISHED and SUPERSEDED. Inspection found the deployed table's CHECK constraint
allows **only `DRAFT_VALIDATED`**, and its triggers prohibit UPDATE/DELETE.
Changing those constraints or adding transition storage would be a schema design
change requiring review, so no migration was prepared or applied here.

The new foundation stores logical DRAFT or VALIDATED in new run/validation
metadata while keeping the existing raw `DRAFT_VALIDATED` value. Legacy records
are read as logical VALIDATED drafts. Both new save options require successful
independent validation; logical DRAFT does not allow invalid assignments.
Choosing a different logical status appends a distinct immutable version.

`check_transition` is a pure preparatory guard for the intended forward chain.
It rejects skipped/reversed transitions, requires explicit authorization for
approval/publication/supersession, and prevents testing/experimental or academically
incomplete timetables entering the publication workflow. There is no operation
that writes these transitions, approves a timetable or publishes it.

All saved drafts conservatively retain `academic_complete=false`. Exact known
session coverage is not evidence of complete academic requirements. Research
mapping and blocked DS counts stay visible, and even a future otherwise complete
schedule still needs explicit academic-completion evidence and authorized review.

## Security and test isolation

The write application checks the existing restricted runtime privileges, forced
institution RLS, exact reviewed policy predicates, absence of elevated role
membership/BYPASSRLS/table ownership, and read/write transaction mode before any
inserts. Shared role/policy inspection is reused by the read-only input adapter.
Every repository query includes institution filters and tenant-scoped references.

The reader starts with database-enforced repeatable-read, read-only mode and
transaction-local institution context. It rejects elevated roles. Its underlying
connection cannot write even if a caller mistakenly invokes a repository write
method. Neither operation grants privileges or accesses Academic/Resource masters.
The legacy Academic staff table's lack of RLS is unchanged; this step never
queries that table or exposes a new unrestricted master-data interface.

API authentication is not implemented here. Future handlers must derive the
institution from authorized Identity claims; an arbitrary client-supplied tenant
UUID is not authorization. Database tenant context alone is not an API access
control layer.

The new write tests require `SCHEDENCE_TEACHING_ISOLATED_DB_TESTS=1` and a private
`TEACHING_ISOLATED_DATABASE_CONFIG` file. They reject any URL whose host is not
`127.0.0.1`, whose database is not `schedence_step12b4_test`, or whose port is
unspecified. Setup asserts the server is not Neon. No production `.env` fallback
is used. The isolated database recreates only the already deployed Teaching
revision and runtime grants, with synthetic institutions and inputs.

Docker Desktop was unavailable, so the test cluster used the Windows binary
archive linked by [EDB's PostgreSQL binaries page](https://www.enterprisedb.com/download-postgresql-binaries).
Its source and SHA-256 are retained privately. No system PostgreSQL installation
or Windows service was created. Runtime files, credentials, logs and database
files stay in ignored `local-data`; the temporary local server is stopped after
verification. No production write-capable test opt-in was enabled.

## Production preservation and historical policy

Read-only production verification retrieved both original drafts using the new
repository, all view types and coverage reports. Before/after fingerprints cover
all ten Teaching tables for the selected institution.

| Production entity | Preserved count |
| --- | ---: |
| Input snapshots | 2 |
| Timetable series / runs / reports / versions | 2 each |
| Deliveries | 1,401 |
| Original occurrence coverage | 1,401 |
| Semester 1 known occurrences | 697 |
| Semester 2 known occurrences | 704 |

Both historical draft save identities match the corresponding database-backed
previews after normalizing preference observation order. This was verified by
pure comparison, not by invoking a production save or acquiring write locks.

Semester 2's 14 IT4070 occurrences retain their earlier snapshot policy. The newer
candidate exclusion is not applied to these historical versions. A future
regeneration under that review must use a distinct reviewed input/policy snapshot.
Year 4 S1 DS remains blocked for 148 students; compulsory Research session mapping
remains pending. Both experimental semester timetables remain separate draft
alternatives, with no claim of simultaneous-semester compatibility.

## Files and verification

Created:

- `src/schedence_teaching/persistence/lifecycle.py`
- `src/schedence_teaching/persistence/versioning.py`
- `src/schedence_teaching/persistence/retrieval.py`
- `src/schedence_teaching/persistence/timetable_service.py`
- `tests/test_timetable_versioning.py`
- `tests/test_timetable_persistence_isolated.py`
- This report.

Modified: `persistence/repositories.py`, `persistence/ports.py`,
`persistence/database_read.py` and the Teaching Service `README.md`.
Models, migrations, solver modules and other services remain unchanged.

Private review artifacts are under
`local-data/persistence-preview/step12b4-reviewed/`: production before/after
counts/fingerprints, local test configuration/runtime, suite logs, preservation
evidence and verification helpers. No new timetable preview or production
version was needed for this step; previous Step 12B.3 outputs remain intact.

The focused suite covers first saves, identical retries, legacy retry recognition,
new policy versions, stale retries, concurrent identical/distinct saves, invalid
results, mid-coverage rollback, institution isolation, denied cross-tenant inserts,
immutability triggers, duplicate booking/coverage constraints, read-only write
rejection, elevated-role rejection, physical/online/hybrid and combined/mixed
round trips, group hierarchy/elective views and lifecycle publication gates.
Final verification:

| Check | Result | Runtime |
| --- | --- | ---: |
| Full Teaching suite | **2,573 passed, 9 skipped**, zero failures/errors | 249.26 s pytest; 250.28 s including wrapper startup |
| New focused suite | 57 passed, included above | 10.67 s |
| Isolated PostgreSQL write-capable integration tests | 20 passed, included above | Included above |
| Existing read-only Neon integration tests | 7 passed, included above | Included above |
| Relevant Academic suite | **33 passed** | 6.57 s pytest; 11.11 s including wrapper startup |
| `python -m pip check` | No broken requirements found | — |
| Production Teaching fingerprints and record counts | Before/after unchanged; no writes | — |
| Workspace preservation | 1,364 original files present; only four intended existing Teaching files modified | — |

The nine skipped tests are the earlier production write-capable tests. Their
opt-in remained disabled, including rollback-only production inserts. All new
write tests ran on localhost instead. The single full-suite warning is the
existing FastAPI/Starlette TestClient httpx deprecation; no dependency upgrade
was introduced.

Commands used from the repository root, with the local cluster running:

```powershell
$env:SCHEDENCE_TEACHING_ISOLATED_DB_TESTS = '1'
$env:TEACHING_ISOLATED_DATABASE_CONFIG = (Resolve-Path 'services/teaching-service/local-data/persistence-preview/step12b4-reviewed/isolated-database.json').Path
.\.venv\Scripts\python.exe -m pytest services/teaching-service/tests/test_timetable_versioning.py services/teaching-service/tests/test_timetable_persistence_isolated.py -q -x --tb=short
.\.venv\Scripts\python.exe services/teaching-service/local-data/persistence-preview/step12b4-reviewed/verify_step12b4.py
.\.venv\Scripts\python.exe services/teaching-service/local-data/persistence-preview/step12b4-reviewed/verify_production_readonly.py after
.\.venv\Scripts\python.exe -m pip check
```

The ignored full-suite helper explicitly disables production-write tests and
configures the isolated writer and separate read-only Neon tests. Academic tests
used the same virtual environment with `pytest.main(['tests','-q','--tb=short'])`
from the Academic Service root and its existing service/auth-core import paths.
Logs and count/runtime evidence are saved in the private review directory.

The temporary local server was stopped after testing, with its shutdown result
recorded in `isolated-postgres-shutdown.json`. Existing source workbooks (eight),
prior previews, solver code and all other services' files retain their original
checksums. No Git branch switch, commit or push occurred.

## Before REST APIs

- Review this write path and the exact intended production save before any live
  new draft/version creation. None was performed here.
- Decide approval ownership, authorization and an immutable lifecycle-event/version
  design before reviewing any schema changes for real status transitions.
- Define explicit academic-completion evidence and resolve Research/DS mapping gaps
  before considering publication. Experimental/test inputs remain non-published.
- Add authenticated, institution-authorized API handlers with bounded retrieval,
  transaction error mapping, optimistic parent/version handling and idempotent
  retry semantics; preserve this database-independent solver boundary.
- Review distinct input/policy versions before applying the updated IT4070 rule.
