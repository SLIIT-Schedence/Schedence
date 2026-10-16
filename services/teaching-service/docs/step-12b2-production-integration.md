# Step 12B.2: approved production integration

The approved import uses the existing `neondb` database, Neon project
`<NEON_PROJECT_ID>`, production branch `<NEON_BRANCH_ID>`. It does
not create a replacement database, change Identity or Examination data, or
publish a university timetable. The CP-SAT engine remains independent of SQL.

## Recovery evidence and restricted access

The user-supplied Console screenshot shows a manual production snapshot created
at **2026-10-16 02:52:10 UTC**, expiring **never**. This is user-provided visual
evidence, matched to the user-confirmed target and live SQL project/branch IDs.
No independent Neon API snapshot lookup or restore drill was performed. The
named PNG is absent locally; the attached conversation image was inspected.
The separately displayed six-hour history window is not the manual snapshot's
expiration.

Neon's documented non-destructive recovery option restores the snapshot into a
**new preview branch**, without finalizing the restore or moving production
computes. See [Neon's snapshot restore reference](https://api-docs.neon.tech/reference/restoresnapshot).
No restore, branch replacement or recovery operation was attempted.

Three dedicated SQL login roles were created: `schedence_teaching_runtime`,
`schedence_academic_excel_importer` and `schedence_resource_excel_importer`.
All have NOSUPERUSER, NOBYPASSRLS, NOCREATEROLE, NOCREATEDB, NOREPLICATION and
NOINHERIT. They have no membership in privileged roles and no write privileges
in another owner's schema. The Academic importer also has narrowly required
SELECT/INSERT on the existing `academic.staff` table; its pre-existing RLS
configuration was not changed. This is an operator import role, not a new
end-user Academic API.

The migration operator can SET ROLE into these new roles, with INHERIT FALSE;
none of the service roles can assume the operator. Every new domain table has
forced RLS and a USING/WITH CHECK predicate based on transaction-local
`schedence.institution_id`. New table privileges were revoked from PUBLIC.
Teaching receives SELECT/INSERT on its ten domain tables and only the column
UPDATE privilege on `timetable_series.series_reference` needed for row locking.
The immutability trigger rejects actual updates, including that column.

The existing institution UUID was reused from the consistently populated
Identity institution references. No new institution or Identity user was
fabricated. Tenant context must come from trusted application authorization;
runtime credentials are not intended for direct client SQL access.

Credentials exist only in ignored
`local-data/persistence-preview/step12b2/resumed-production/restricted-runtime.env`.
This file contains separate prefixed URLs and production/echo settings; it must
never be copied to Git or diagnostic reports. The shared root `.env` is unchanged.
Future runtime composition can explicitly use
`load_settings(env_file=private_path, env_prefix="TEACHING_")` with db-core's
session factory and the existing Teaching unit of work. The health application
does not connect to PostgreSQL implicitly and no new API was introduced.

## Additive migrations and imported records

| Schema | Revision | Change |
| --- | --- | --- |
| academic | `0002_teaching_inputs` after `0001_academic_initial` | 12 new owner tables; existing staff and administrative table definitions unchanged |
| resource | `0001_resource_inventory` | New Resource schema, source evidence and venue inventory |
| teaching | `20261016_0001` | Prepared ten-table immutable history revision, now applied |

The three migrations and grants ran in one transaction. The successful data
import ran in another transaction, switching explicitly between restricted
owner roles. The first attempted import stopped at a missing operator SET ROLE
permission and rolled back before source records were inserted. The delegation
was then corrected without granting elevated privileges to service roles.

| Table | Imported rows |
| --- | ---: |
| academic.staff | 110 (90 academic staff, 20 instructors) |
| academic.source_datasets | 6 |
| academic.departments | 9 |
| academic.staff_teaching_profiles | 110 |
| academic.specializations | 8 |
| academic.modules | 184 |
| academic.module_specializations | 304 |
| academic.module_teaching_teams | 1,288 |
| academic.lecturer_preferences | 90 |
| academic.cohort_totals | 16 |
| academic.pp1_specialization_allocations | 128 |
| academic.pp1_student_groups | 338 |
| academic.module_classification_reviews | 54 |
| resource.source_datasets | 2 |
| resource.venues | 62 |
| teaching.input_snapshots | 2 |
| teaching.snapshot_sources | 38 |
| teaching.snapshot_scopes | 128 |
| teaching.snapshot_issues | 4 |
| teaching.timetable_series | 2 |
| teaching.generation_runs | 2 |
| teaching.validation_reports | 2 |
| teaching.timetable_versions | 2 |
| teaching.deliveries | 1,401 |
| teaching.delivery_coverage | 1,401 |

Each owning schema also has its own migration-version table. Original staff
numbers and venue codes are unchanged; UUIDs identify newly issued owner records,
not replacements for university identifiers. Academic modules retain source-row
identity and both conflicting IT2100 codes. Source-row IDs and local PP1 offering
references are never presented as supplied authoritative academic offering IDs.

The original 16 cohort totals sum to 6,720 cohort memberships across the two
semester alternatives, not 6,720 verified distinct individual students. All
128 proportional allocations reconcile exactly. The 338 Academic PP1 groups
are the base proportional planning hierarchy. The snapshots separately retain
the original elective-attendance plans, their populations, individual synthetic
selections and exact coverage; these distinct plans are not merged by group ID.

New staff reuse the existing Academic Service operational `ACTIVE` default;
this is not a university-supplied employment-status claim. Unknown email and
Identity references remain null. Administrative appointment titles do not
invent underlying ranks, dated appointments, administrative completion evidence
or calendar reservations. Source position, department, preference and module
values remain traceable to their original workbook rows and checksums.

The corrected campus-confirmed venue workbook supplies the single 62-row
inventory. The earlier workbook is retained only as source backup evidence,
not imported as another inventory. Original status labels and subsequent
campus confirmation/location-correction metadata coexist. Venue inventory does
not imply an official availability calendar or combined-room booking policy.

## Timetable history and policy versions

The existing Semester 1 **697/697** and Semester 2 **704/704** FEASIBLE previews
were reconstructed from preserved exports and independently validated before
storage. No new CP-SAT optimization was represented as having run. Normalized
deliveries, original occurrence coverage, run metadata and validation evidence
are compared during read-back. Each version is `DRAFT_VALIDATED`,
`COMPLETE_SUPPORTED_SCOPE`, and explicitly **not academically complete**.

Both retain `PP1_EXPERIMENTAL_CALENDAR`, including Weekend Monday online ending
at 18:30. They are separate semester alternatives and are not a validated
concurrent combined-semester timetable. Year 4 Semester 1 DS remains blocked
with its **148 allocated students preserved**. Research remains compulsory and
`RESEARCH_PENDING_SESSION_MAPPING`; no zero-hour Research session is invented.

The newly supplied `Year4_Classification` workbook contains 54 rows whose module
facts match all Year 4 source rows. All academic classifications are
`UNCLASSIFIED`; 53 test-candidate flags are YES and one is NO. The NO flag applies
to IT4070, source row 127. The preserved earlier PP1 registration policy includes
301 synthetic selections for that module, represented by 14 Semester 2
occurrences. The new review and the historical registration policy are retained
as separate versions. The stored previews are not claimed to have been
regenerated against the new candidate pool. Applying that new pool requires a
separate registration/demand regeneration, not deletion from historical history.

## Verification and operational artifacts

Private evidence is under
`local-data/persistence-preview/step12b2/resumed-production/`:

- `recovery-evidence.json`: exact evidence basis and unperformed recovery checks.
- `approved-mapping-execution-plan.json`: per-owner source counts and operations.
- `*.reviewed-upgrade.sql`: rendered migration SQL, with no credentials.
- `reviewed-source-checksums.json`, `source-validation.json`: eight unchanged workbooks and source reconciliation.
- `initial-import-results.json`: first successful inserts and within-transaction repeat comparisons.
- `import-results.json`, `idempotency-verification.json`: independent repeated import reusing every identifier, with no new rows or versions.
- `before-database-digests.json`, `after-database-digests.json`, `existing-record-preservation.json`: aggregate counts and hashes protecting other services' records.
- `postcommit-readback.json`: verification through a fresh restricted connection and read-only transaction.
- `workspace-preservation.json`: all 1,180 baseline files present; prior data artifacts and solver code unchanged.
- `files-changed.json`: exact new/modified code and documentation paths, excluding ignored operational data.
- `full-verification.json` and `full-verification.txt`: full Teaching suite including opted-in live RLS tests.
- `academic-tests.txt`: all 33 Academic Service tests, using fakes and offline migration rendering.

The operator artifact `integrate.py` records explicit prepare, role, migration,
import and finish phases. It is target-bound and deliberately refuses to rotate
existing roles or rerun initial migrations blindly. Imports are append-only:
identical identities are reused and conflicting values stop the transaction.
`verify.py full` loads only the ignored dedicated credentials and explicitly
enables rollback-only live tests. Do not run any phase casually against another
database or treat this operator artifact as an unauthenticated API.

### Code and documentation changes

- Academic: new `app/models/teaching_inputs.py` and frozen Python/SQL revision
  `alembic/versions/20261016_0002_teaching_inputs`; model registration, injected
  migration-connection support, migration tests and the service README updated.
- Resource: new minimal `schedence_resource` inventory models, package metadata,
  README, Alembic configuration/environment and frozen
  `20261016_0001_venue_inventory` Python/SQL revision. No Resource API was added.
- Teaching: new `persistence/master_import.py`, `master_plan.py` and
  `source_review.py`; injected migration-connection support and README updated.
  New tests cover source review, append/reuse/conflict behavior, owner migration
  boundaries and live restricted-role isolation. Existing live tests use shared
  db-core's Windows-compatible event-loop runner.
- Private operator scripts, reports, exact snapshots and credentials remain
  inside ignored `local-data/persistence-preview/step12b2/resumed-production`.

The prior prepared Teaching revision and existing Academic initial revision are
unchanged. No original CP-SAT, preparation, preference, workload, lunch,
combination or fairness implementation was changed.

### Final test results

The complete Teaching Service suite passed **2,457 tests**, including all nine
opted-in live PostgreSQL checks, with zero skips/failures. All **33 Academic
Service tests** passed. `python -m pip check` reported no broken requirements.
The existing Starlette/httpx deprecation warning remains unrelated to this work.

An earlier final verification run lost a database connection in one live test;
its 2,456 other tests passed. A read-back also encountered a connection drop.
Fresh read-back and the complete suite were rerun successfully without changing
permissions, bypassing security checks or relaxing constraints. The failed-run
logs are retained privately as `initial-full-verification.*`; no ambiguous write
was retried automatically. All synthetic live-test records roll back.

No original workbook or previous timetable export was modified. No database
record was deleted; no Identity, Examination or administrative record was
overwritten. No Git commit, push or branch switch was performed.
