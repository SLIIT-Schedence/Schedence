# Step 12B.2: pre-import mapping and safety review

**Subsequent authorization:** the user has now explicitly approved additive
production integration for Academic-owned shared masters, Resource-owned venue
masters and Teaching persistence after safety checks. No additional generic
approval is required. Execution stopped at the user's mandatory recovery gate:
no usable backup/restore evidence or Neon management access is available. The
configured role has BYPASSRLS and dedicated runtime access is not configured.
The newly requested Year 4 module-classification workbook is also not present in
the inspected local-data tree. The current authorization/preflight record is
`local-data/persistence-preview/step12b2/approved-integration-preflight.json`.

The following documents the preceding mapping-only review and its narrower
Teaching-snapshot proposal; it is not the full newly approved shared-master
execution plan. **No migration, role creation, source-data insert or timetable
persistence was executed.** Original workbooks and existing database records
remain unchanged.

Read-only inspection of the existing shared Neon PostgreSQL 18.6 database found:

| Schema | Tables including Alembic | Application records excluding Alembic |
| --- | ---: | ---: |
| academic | 6 | 0 |
| identity | 8 | 73 |
| examinations | 11 | 2,316 |
| public | 0 | 0 |

The user confirmed the target branch label is production. The exact project,
branch, existing institution UUID, all Academic columns/types/defaults, keys,
indexes and source mappings are in the private review artifacts. No credentials,
personal staff records or Identity account records appear in the review report.

## Proposed import boundary

- **REUSE:** the existing institution reference and Academic service contracts.
  `academic.staff` is empty, so there are currently no authoritative staff UUID
  records to reuse. Staff IDs map to `staff_number`, not UUID `id`.
- **CREATE TABLE:** only the ten prepared Teaching history tables plus isolated
  `teaching.alembic_version`, with the already prepared RLS/immutability objects.
- **INSERT after approval and safety checks:** two immutable PP1 input snapshots,
  32 source-evidence rows, 128 scope rows and four pending-issue rows. Re-imports
  reuse equivalent snapshots instead of overwriting or duplicating history.
- **No initial INSERT:** solver runs, timetable versions, deliveries or coverage.
  Existing successful previews remain files; no approved/published result is
  manufactured from their JSON exports.
- **No master-data writes:** no change to Academic, Identity or Examination tables;
  no Teaching staff/module/venue master tables. Future authoritative imports or
  extensions require their owning services' reviewed mappings.

`examinations.seed_modules` is not a suitable catalog: its primary key is
`module_code`, which cannot preserve both IT2100 records (source rows 100 and 185).
Its 42 rows and related seed tables are examination-owned and lack institution
scope. The 62 examination seed venues are not a Resource Service master. Their
normal/exam capacities must not be overwritten by this import.

The existing Academic staff table also lacks `staff_type` and department fields.
Appointment-only source titles must not be fabricated as underlying academic
ranks; absent emails, login IDs, employment status evidence, organizational units
and appointment dates must not be invented. Existing university-source
confirmation is retained, without requesting repeat confirmation of that data.

## Prepared inputs and provenance

The existing importers and preserved registrations prepared these private inputs:

| Semester | Known occurrences | Staff input records | Venues | Groups | Supported cohorts | Blocked students |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 697 | 110 | 62 | 227 | 62 | 148 |
| 2 | 704 | 110 | 62 | 245 | 64 | 0 |

Source totals are 110 staff (90 academic / 20 instructors), 90 preference records,
184 module rows, 1,288 teaching-team entries, 16 original aggregate cohorts, and
128 proportional specialization allocations reconciled to every original total.
The sum of cohort counts is 6,720 across both semesters; it is not a claim about
distinct individuals across semesters. The original venue workbook is a backup
reference, not a second active 62-venue inventory.

Seven workbook checksums match the prior reviewed manifest. University source,
PP1 proportional estimates, synthetic elective selections and experimental
calendar provenance remain distinct. Both inputs retain
`PP1_EXPERIMENTAL_CALENDAR`; Semester 1 DS stays blocked and compulsory Year 4
Research stays pending session mapping. Unsupported/unscoped source preference
fields remain source evidence rather than invented hard availability constraints.

The pure capture adapter preserves existing lecturer eligibility, student
hierarchy and synthetic attendance rosters. The application layer verifies source
checksums, validates inputs, imports atomically, checks database read-back before
commit, and returns native scheduling inputs after closing the DB transaction.
The CP-SAT solver and its objectives/constraints are unchanged.

The repository serializes cooperating tenant imports with a transaction advisory
lock and compares semantic input digests. There is no new database semantic-content
unique index: direct writers must not bypass this boundary. Live idempotency and
read-back verification remain unperformed pending approval and safety readiness.

## Safety prerequisites and verification

Recovery protection is **not yet verified**. No backup evidence or management API
access was supplied. The available Neon integration is not confirmed connected.
The current shared/migration role has BYPASSRLS and cannot be the Teaching runtime
role. A separate service-specific role/grant proposal is prepared in
`migrations/teaching-runtime-role.review.sql`; it is not executed by Alembic and
contains no password. It starts NOLOGIN until secure credential provisioning.

Before any approved mutation: verify recovery for the exact production branch,
recheck schema/migration drift, provision a non-owner NOBYPASSRLS runtime principal
and inspect all intended SQL. Never compensate by disabling forced RLS or writing
through the owner role as application runtime.

Verification: **2,413 tests passed, 4 live-database tests skipped**; `pip check`
passed. The unchanged Starlette/httpx warning remains. Live tests require explicit
opt-in, target/institution settings, recovery evidence and restricted credentials;
their synthetic writes are designed to roll back. They were not enabled here.
Read-only live metadata inspection succeeded. Offline migration review found no
unrelated-schema changes. No live import, RLS write test or database read-back
success is claimed.

The detailed approval report is:

`local-data/persistence-preview/step12b2/mapping-and-safety-review.md`

It is accompanied by metadata JSON, source/count facts, reviewed offline SQL,
prepared private semester snapshots and preservation/verification evidence. The
directory is Git-ignored. All prior timetable outputs and source workbooks remain
unchanged. No commit, push or branch change was made.
