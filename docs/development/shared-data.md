# Shared development data: audit and seed runbook

**Superseded deployment choice:** the user now requires the existing `neondb`,
with no new database/project/branch and no production changes before approval.
Use [the existing-database comparison and additive review plan](existing-neondb-comparison.md).
The older development-only seed loader below intentionally remains unable to
write to the protected existing database; do not weaken its guard or import the
pseudonymized seed into live canonical records.

Audited 2026-10-21. Existing service code, migrations, tenant IDs, permissions and
historical/generated rows were not changed. Database inspection used READ ONLY
transactions, catalog metadata and institution-scoped counts.

## Findings

| Component | Actual configuration/source | Sharing issue |
| --- | --- | --- |
| Academic Data | Unprefixed `DATABASE_URL` / `DIRECT_DATABASE_URL`, discovered from the nearest `.env` unless explicitly configured. Current root config connects to Neon `neondb`. | A teammate's working directory, service `.env` or process overrides can select another database. The root role is `neondb_owner`, unsuitable as a shared runtime credential. |
| Teaching | Prefixed `TEACHING_*`, `ACADEMIC_*`, `RESOURCE_*` settings. Current web URL is `127.0.0.1:8005`; the development launcher uses `DURABLE_ISOLATED`. | Academic/venue inputs come from Neon, but generated results and Forms use local PostgreSQL `127.0.0.1:52870/schedence_step12b4_test`. Teammates cannot share another computer's localhost. Database readers also validate project/branch/database identity, not just the URL. |
| Exam | Unprefixed DB settings, currently the same Neon database. `DatabaseSource` reads `examinations.seed_*`. | These are a separate imported catalog, not live `academic.*` / `resource.*`. Sharing the DB URL does not synchronize them. Dataset access is restricted by its singleton institution owner. |
| Staff Duty Allocation | Repository/branch is unavailable in this checkout, confirmed by requester. | Storage, auth, API and grants cannot be verified. No guessed schema or teammate edits are included. |

Current Neon identity: project `super-leaf-21551515`, branch
`br-dry-darkness-b549ci92`, database `neondb`. Treat it as protected; a file marked
`production` does not prove the Neon branch's business purpose. Do not repurpose it.

The exported catalogs confirm the mismatch: Academic has 184 module rows / 183
distinct codes; Exam has 207 codes. Only 179 codes match (4 Academic-only,
28 Exam-only). All 62 Resource venue codes match Exam venue IDs, but this does
not establish capacity/type equivalence or an ongoing synchronization mechanism.

Two **different active tenants** exist:

| Institution | Identity UUID | Staff / modules / departments | Neon Teaching versions |
| --- | --- | --- | --- |
| `SLIIT-MALABE` | `84651d6d-0c8a-470a-a798-5220666fd367` | 110 / 184 / 9 | 0 |
| `INST-44DC54C1` | `44dc54c1-35df-4e53-8fc0-95a7d911368b` | 110 / 184 / 9 | 2 |

Both have 110 teaching profiles, 16 cohort totals and 338 PP1 student groups.
These counts do not prove row content/identities are identical. Root
`SLIIT_INSTITUTION_ID` names the second tenant; isolated Teaching's allowlist and
Exam's actual dataset owner name the first. Current Exam auth resolves Identity
permissions and dataset ownership, not that legacy env value. Do not rename,
merge or reassign either tenant or historical records.

Permissions also differ. `schedence_teaching_runtime` has no Academic schema
usage or staff/module SELECT grants; Teaching opens separate Academic/Resource
connections. The importer roles have INSERT privileges, so they are not ideal
general-purpose reader roles. Imported Academic tables, Resource venues and
Teaching history enforce tenant RLS. Legacy Academic staff and Exam seed tables
do not have DB RLS: their APIs enforce tenant access. A broad direct SELECT grant
there would not reproduce the API's isolation.

Academic HTTP routes currently cover staff, administrative workload, positions
and appointments. There are no general module/department/enrolment catalog
routes registered. Resource has models/migrations, no HTTP runtime. Academic
cohort totals/groups are not an individual student/registration master. Exam
has exam-specific duration/eligibility/period data that Academic does not own.

API access requires the same Identity institution grant as the data. Academic
Admin and Examination Admin are different permission sets. Multi-institution
Exam callers must supply `X-Institution-Id`; the current web Exam client does
not set it. Staff `identity_user_id` is nullable; a lecturer record is not a
login account and an administrative appointment does not grant RBAC access.

## Reusable seed package

`scripts/dev_seed.py` has three commands:

- `export`: READ ONLY, REPEATABLE READ per owner connection; explicit tenant,
  explicit env files/keys, fixed table allowlist, exact owner migration revisions,
  and a frozen checksummed JSON bundle. Owners are read in separate transactions;
  this is not a cross-service atomic snapshot. Export while catalog imports are idle.
- `inspect`: offline checksums, row/column shapes, institution and reference
  validation. No database connection.
- `load`: **read-only plan by default**. `--apply` explicitly opts into one atomic,
  insert-only transaction. Existing equivalent data is skipped; differing data
  stops the transaction. No UPDATE, DELETE, TRUNCATE, DDL, grants, migration runs,
  RLS disabling, solver calls or generated/history imports.

Snapshots are restricted to git-ignored `scripts/dev-seed/*.json`. They contain
existing catalog data, not invented seed records. Lecturer names/numbers/UUIDs
and student identifiers are deterministically replaced with development values;
emails and Identity links are cleared; raw source values/provenance are removed.
Personal lecturer preference responses are excluded. Catalog IDs, department and
module relationships, capacities and aggregate counts remain sourced values.
This is pseudonymization, not a promise of anonymous or publicly distributable
data. Review institutional data policy before sharing the bundle.

The schema-shaped rows target only existing Academic inputs/staff, Resource
venues/sources, and Exam's existing `seed_*`/dataset-owner tables. Generated
Teaching results, Forms, saved Exam timetables, seating records, history and
authentication tables are excluded. Real database FK/CHECK/RLS constraints remain
enabled during load; metadata and migration versions must match exactly.
Source workbook payloads and preferences are intentionally removed, so this is
an integration catalog seed, **not a solver-ready authoritative input snapshot**.

Published, independently marked valid Exam sessions and their room allocations
are exported as a separate **read-only reference projection**, scoped to the
selected institution. They are never restored as generated/historical DB rows.
Zero published sessions is valid and is reported explicitly; no session is
invented. The Exam owner must confirm timezone semantics before Duty consumes
the source's local timestamps.

The private validated bundle `scripts/dev-seed/sliit-malabe-20261021-v1.json`
contains 110 pseudonymized lecturers, 9 departments, 184 module rows, 62 venues,
390 pseudonymized Exam students and 3,577 registrations. It has **zero** published
validated Exam sessions. Its SHA-256 is
`57366255aaac1f051675ff04b59ea6dcbd2cf60635d9c2bbfecb7b972a7c51b9`.
No database load has been applied.

Example read-only export (URLs stay in ignored env files; never paste credentials):

```powershell
$sourceEnv = 'services/teaching-service/local-data/persistence-preview/step12b2/resumed-production/restricted-runtime.env'
.venv/Scripts/python.exe scripts/dev_seed.py export `
  --institution-id 84651d6d-0c8a-470a-a798-5220666fd367 `
  --output scripts/dev-seed/sliit-malabe-20261021-v1.json `
  --academic-env-file $sourceEnv --academic-url-key ACADEMIC_DIRECT_DATABASE_URL `
  --resource-env-file $sourceEnv --resource-url-key RESOURCE_DIRECT_DATABASE_URL `
  --examinations-env-file .env --examinations-url-key DIRECT_DATABASE_URL `
  --metadata-env-file .env --metadata-url-key DIRECT_DATABASE_URL
.venv/Scripts/python.exe scripts/dev_seed.py inspect --bundle scripts/dev-seed/sliit-malabe-20261021-v1.json
```

Existing bundle filenames cannot be overwritten. Source access must be authorized;
an account with only the appropriate SELECT permissions is preferable to the
currently configured owner/importer credentials. The enforced read-only session
does not turn an otherwise privileged credential into a least-privilege account.

The current Academic/Resource importer roles cannot SELECT their `alembic_version`
tables. The optional, explicitly supplied metadata connection reads **only**
migration revisions and project/branch/database metadata; it must point to the
same source target. Source records still use their separate scoped reader
connections. No grants are changed to work around missing metadata permissions.

## Exact shared Neon development integration steps (not applied)

1. The database owner creates a **new schema-only development branch/project**,
   then a new `schedence_dev` database on that branch. All teammates use that
   **same** branch/database for shared live records. Branches diverge independently;
   one branch per developer does not share subsequent writes. Keep all current
   branches and historical records untouched. See [Neon branching](https://neon.com/docs/introduction/branching).
2. Each service owner applies their reviewed migrations to the new DB using a
   dedicated migrator, including Identity and Resource prerequisites. Current
   source revisions are Academic `0002_teaching_inputs`, Resource
   `0001_resource_inventory`, Exam `0012`; seed load requires these exact source
   revisions. Later revisions require a reviewed bundle version/adapter. Identity
   owner provisions the same canonical `SLIIT-MALABE` UUID in the new dev Identity
   database, plus development users and grants. The seed tool does not create
   accounts or copy production login mappings.
3. Create distinct restricted runtime, catalog-reader and temporary seed roles
   **with SQL**, not console-created elevated roles. Give each service writes only
   to its own schema. Seed role needs SELECT/INSERT on the allowlisted tables,
   sequence USAGE for venue blocks, SELECT on owner migration tables and the
   canonical Identity institution. Seed loaders use a transaction advisory lock
   and SERIALIZABLE isolation; UPDATE/DELETE privileges are not required.
   It must have neither SUPERUSER/BYPASSRLS nor `neon_superuser` membership. Never
   disable RLS. Prefer owner APIs for legacy non-RLS tables rather than granting
   cross-component unrestricted SELECT. See [Neon roles](https://neon.com/docs/manage/roles)
   and [PostgreSQL RLS](https://www.postgresql.org/docs/current/ddl-rowsecurity.html).
4. Copy `scripts/dev-seed/target.example.json` to ignored
   `scripts/dev-seed/team-target.json`; enter the new **actual** Neon project and
   branch IDs, retaining database `schedence_dev`. Put its restricted seed direct
   URL in an ignored explicit env file. Run the following **without `--apply`**
   first. It rejects source/protected branches, wrong identities, elevated roles,
   missing tenant identity, schema drift and conflicting existing data. For
   disposable local testing only, the target must be literal loopback and a new
   `schedence_seed_*` DB with null Neon metadata; the existing isolated Teaching
   database is deliberately rejected.

   ```powershell
   .venv/Scripts/python.exe scripts/dev_seed.py load `
     --bundle scripts/dev-seed/sliit-malabe-20261021-v1.json `
     --target scripts/dev-seed/team-target.json `
     --env-file scripts/dev-seed/team-dev.env --url-key DIRECT_DATABASE_URL
   ```

   The owner reviews the plan, then explicitly runs the same command with
   `--apply` **only on the newly approved development database**. No load has been
   applied during this audit. A repeated identical load must report unchanged.
5. Set `SCHEDENCE_ENV_FILE` explicitly for each launched service; files are not
   merged. Academic, Identity and Exam currently use unprefixed `DATABASE_URL` /
   `DIRECT_DATABASE_URL`; putting only `ACADEMIC_DATABASE_URL` in a file will not
   configure Academic's current container. Teaching uses `TEACHING_*` plus its
   explicit `ACADEMIC_*` / `RESOURCE_*` reader URLs and matching database target
   metadata. Use pooled runtime connections and direct migration/seed connections
   ([Neon pooling](https://neon.com/docs/connect/connection-pooling)). Set transaction-local
   `schedence.institution_id` on every transaction; pooled session settings do not
   persist. Retain schema-qualified queries.
6. Use one development Cognito/Identity environment and canonical institution
   grant across services. Link sanitized lecturers only to provisioned **dev**
   users through the owning service. Set web service URLs to reachable development
   APIs (or run each teammate's local APIs against the shared DB). A remote
   teammate's `localhost:8005` points to their own machine. Teaching's existing
   `DURABLE_ISOLATED` worker/Forms guards are local-only; moving those writes to
   Neon requires a Teaching-owner reviewed configuration/adapter change, not just
   a URL replacement. Current Neon Teaching schema has historical tables at
   `20261016_0001`, not the newer Forms/durable-generation tables.
7. Academic/Resource owners publish missing scoped read contracts, including
   stable IDs and pagination. Exam owner replaces its source adapter or performs
   a reviewed, versioned import using module/venue codes with ambiguity rejection;
   never assume Academic UUIDs equal Exam string keys or invent missing exam
   metadata/student registrations. Sharing seeds is an initial snapshot, **not
   ongoing synchronization**. Existing Exam seeds remain Exam-owned data.
8. Verify the actual `(project, branch, database)` tuple for every runtime,
   scoped row counts, stable references, same-institution API access, and denied
   cross-institution reads/writes. Verify generated-view storage separately; do
   not run generation as a data-sharing smoke test.

## Staff Duty handoff requirements

No Duty tables, routes or permissions were invented. The future owner must supply:

- Repository/branch, institution-scoped API contract, auth permission names and
  migration/schema ownership.
- Lecturer key mapping: sanitized dev `academic.staff.id`, `staff_number`,
  `staff_teaching_profiles.department_id`; logins require explicit dev linking.
- Department and venue catalogs; Resource venue UUID/code to Exam `venue_id`
  mapping must be checked for missing/ambiguous values, not guessed.
- Approved/published Exam session API/event contract: institution, timetable ID,
  revision, session ID, period/module code, start/end, timezone, candidate counts
  and room allocations. Consume only published validated versions; handle
  withdrawal/revision explicitly. The reference projection supports contract
  review but is not a live API.
- Authoritative invigilation eligibility, workload/availability constraints and
  required duty counts per room. These facts are not in the available catalog
  and are deliberately not fabricated.

## Verification

Focused offline checks: `.venv/Scripts/python.exe -m pytest scripts/tests/test_dev_seed.py -q`.
Result: **34 passed**, plus successful read-only sanitized export and offline
inspection of the real bundle. Python compilation and whitespace checks passed.
They cover checksum/tenant/ref rejection, deterministic sanitization, protected
target guards, role restrictions, read-only default planning, insert-only replay
and conflict refusal. A live import is deliberately not exercised on any existing
database. Run migration/constraint/role integration checks on a newly provisioned
development target before approving its first load.

Automatic approval review rejected a supplemental live type-coercion SELECT
because it would transmit the full pseudonymized seed back to the existing Neon
database as query parameters. That check did not execute. Verification remains
offline; only schema metadata was captured during the authorized source export.
