# Existing neondb: comparison and proposed additive integration

> Historical comparison. The approved eligible additive subset has now been
> deployed; see [the exact deployment results](approved-teaching-neon-migration.md).
> Statements below about pending migration describe the earlier review stage.

## Current module review: mapping gate passes for the inspected scope

The four requested identities have now been resolved using read-only Neon data,
original workbook checksums/rows, program links, actual credits and Exam references.
See [the exact reviewed ID mapping and evidence](reviewed-module-mappings.md).
For the current 91 local module definitions: 88 exact matches, 2 verified
capitalization-only equivalents and 1 valid Academic-only module. IT2100's
Statistics record is uniquely identified by its existing UUID; the different
seminar record remains unchanged and unmapped to Exam. No Neon changes ran.

The module gate passes for that explicit crosswalk; the full migration still
requires backups, storage/permission review, experimental-data handling and a
fresh complete dry-run. The following section records the earlier preflight
that prompted this now-completed identity review.

## Previous deployment preflight (superseded for these four mappings)

The latest conditional authorization requires Examination module codes to be
the reference and explicitly requires stopping on unresolved mappings. A fresh
read-only preflight matched **88 of 91** local Teaching module definitions
exactly to that reference. No Neon changes were applied.

| Teaching code | Existing catalog values | Required decision |
| --- | --- | --- |
| `IT3060` | Academic: `Human Computer Interaction`, Year 3 Semester 1; no exact Exam code | Confirm a Teaching-only exception or an owner-approved reference mapping. Do not invent an Exam row or guess another code. |
| `IE3030` | Academic: `Wide area Networks`; Exam: `Wide Area Networks` | Confirm equivalence of the capitalization-only difference; preserve both stored names. |
| `IT3061` | Academic: `Massive Data Processing and cloud Computing`; Exam: `Massive Data Processing and Cloud Computing` | Confirm equivalence of the capitalization-only difference; preserve both stored names. |
| `IT2100` | Two existing Academic rows: `Employability Skills Development - Seminar (Non-GPA)` and `Probability and Statistics`, both Year 2 Semester 2. Exam uses `Probability and Statistics`. | Confirm which existing Academic ID belongs in the cross-service contract and how the other existing record is excluded. Do not merge, rename or reassign either row. |

The broader 28 Exam-only codes and five name discrepancies remain recorded below.
The other three name discrepancies are outside the current 91 local module
definitions; they were not silently resolved. All 335 local Teaching/Forms
candidates remain held, including their immutable relationships. The 99
unapproved derived planning groups are not eligible for new master-data inserts.

The earlier generated SQL is a **review draft**, based on the Academic catalog;
it is not approved for execution under the newer Exam-reference requirement.
Resolve this preflight before revising/applying its module contract. No fresh
backup, Neon schema/data migration, reader grants, service configuration switch
or service restart was performed after this gate failed. A fresh verified backup
of both databases is still mandatory before any eventual approved writes.

Evidence: `scripts/neon-review/exam-reference-preflight-20261021-v1.json` and
`scripts/neon-review/examination-module-review-20261021-v1.csv` (private, ignored).

**Applied counts: 0 inserted, 0 updated/deleted/reassigned, 0 schema or permission
changes.** Verified reusable owners remain 110 staff, 9 departments and 62 venues;
none were copied or replaced. Existing configuration and all old-institution and
teammate data remain untouched.

**Review only, 2026-10-21. No Neon DDL/DML has run.** This plan supersedes the
earlier suggestion to create a development branch/database. Use only the
existing project `super-leaf-21551515`, branch `br-dry-darkness-b549ci92`, database
`neondb`. Canonical institution: `84651d6d-0c8a-470a-a798-5220666fd367`.

All other institutions, existing records, source ACLs and application settings
remain unchanged during this audit. The proposed shared roles/grants below also
require explicit approval before execution.

## Comparison results

The read-only audit inspected 102 Neon tables (21,692 visible rows) and all 15
isolated Teaching tables. Full row hashes and primary/unique keys were compared
for canonical Teaching data. Catalog names were compared using server-side
hashes, not exported personal details. Counts are snapshots of separate
read-only transactions, not a claim that writes by other users were frozen.

| Canonical data | Existing Neon rows | Distinct local input records | Reusable existing records | Missing | Attribute conflicts / ambiguity |
| --- | ---: | ---: | ---: | ---: | ---: |
| Modules | 184 (183 codes) | 91 module definitions | 91 | 0 | 0 |
| Academic staff | 110 | 110 | 110 | 0 | 0 |
| Departments | 9 | 9 | 9 | 0 | 0 |
| Resource venues | 62 | 62 | 62 | 0 | 0 |
| PP1 planning groups | 338 | 227 | 128 | 99 | 0 |

**The 99 unmatched group references are derived experimental planning groups,
not missing university master records.** They must not be inserted into
`academic.pp1_student_groups` or promoted to official groups. Their existing
snapshot IDs/references/provenance can be preserved in Teaching-specific history
and exposed through an explicitly labelled, restricted planning view.

All recorded canonical owner-ID references in local snapshots resolve in Neon:
184 module source IDs, 110 staff IDs and 62 venue IDs. The snapshots already bind
to existing canonical owners. Do not create replacement IDs or substitute the
old institution's counterparts. Staff numbers, module source-row IDs and venue
codes have different meanings; source module IDs are not official offering IDs.

### Examination comparison

Exam owns 207 module codes in `examinations.seed_modules`. Compared with Academic:

- 174 records match module code, exact name, year and semester.
- 5 existing codes have differing names: `IT3080`, `IT4100`, `IE3030`, `IE4080`,
  `IT3061`. This includes presentation differences; no automatic normalization or
  merge was performed. Owners must review them.
- 28 Exam codes are absent from Academic. They already exist in Exam and are
  **not** proposed Academic inserts. Academic lacks the complete owner-confirmed
  contact-hour/program facts needed to create those rows safely.
- Academic-only codes: `IT3050`, `IT3060`, `IT3110`, `IT4070`. Do not seed them into
  Exam without owner-provided examination metadata/eligibility.
- Academic already contains **two `IT2100` source rows**. Do not delete, merge or
  add a third. Source IDs distinguish these records. Default shared `modules`
  excludes this ambiguous code pending review; `module_source_records` preserves
  both existing IDs and `module_code_conflicts` makes the issue visible.
- All 62 Exam venue codes, names and normal capacities match Resource records.
  Preserve Resource UUIDs and Exam string venue IDs as separate namespaces.
  Exam seating capacity remains governed by Exam's own rules.

These comparisons do not turn aggregate groups into individual students or
registrations. Exam's candidate records and scheduling metadata remain Exam-owned.

## Exact local Teaching candidates, not approved inserts

The following **335 rows** are absent by primary key from Neon under the
canonical institution. There are zero primary-key/content collisions and zero
detected existing unique-key conflicts. Existing ten history-table column
shapes match. These are candidates for a later reviewed transfer, not a command
to insert them now.

| Destination owner table | Missing rows |
| --- | ---: |
| `teaching.input_snapshots` | 2 |
| `teaching.snapshot_sources` | 36 |
| `teaching.snapshot_scopes` | 72 |
| `teaching.snapshot_issues` | 3 |
| `teaching.timetable_series` | 1 |
| `teaching.generation_runs` | 1 |
| `teaching.validation_reports` | 1 |
| `teaching.timetable_versions` | 1 |
| `teaching.deliveries` | 106 |
| `teaching.delivery_coverage` | 106 |
| `teaching.generation_jobs` | 1 |
| `teaching.generation_job_attempts` | 1 |
| `teaching.form_template_drafts` | 4 |
| `teaching.form_distributions` / `form_distribution_recipients` | 0 / 0 |

The local job is COMPLETED; no PENDING/RUNNING jobs were observed. Template
creator/editor, requester and version creator IDs resolve to existing Identity
users. All 28 distinct scheduled staff references resolve to canonical staff.
No selected recipient/account rows exist yet. These checks supplement real
source FK constraints; they are not a substitute for a transactional import
preflight on the approved destination schema.

The additional local dependency check examined 19 validated FK constraints:
zero missing parent rows and zero missing job source-snapshot references.

Scheduling rules already live in immutable snapshot payload/configuration;
there is no missing standalone Scheduling Rules table to invent. Preserve the
original JSON text, hashes, dates, rules and provenance without rewriting IDs.

### Transfer blockers / mandatory gates

1. Neon lacks five tables: queue jobs/attempts, form drafts, distributions and
   recipients. Their approved Teaching migrations are local only. Do not apply
   Alembic automatically: it updates the existing `teaching.alembic_version` row,
   which is outside this plan's strict no-existing-row-update authorization.
   Deployment tracking for those new tables requires a separately approved plan.
2. Forms sending and durable generation currently enforce isolated loopback
   persistence. Copying rows or changing a URL will not enable them on Neon.
   This plan keeps the existing runtime, Forms behavior and solver unchanged.
3. The 99 derived group references and the existing `IT2100` ambiguity require
   explicit provenance/consumer handling; never resolve them by inserting
   duplicate master records or reassigning tenant IDs.
4. Obtain a fresh, verified backup of **both current databases** before any
   approved changes. No live constraint/restore/import test has been performed.
5. Rerun comparison immediately before approval/execution. Abort on a changed
   source hash, different target identity, new active jobs, missing parent,
   unmatched Identity principal or any ID/unique-key/content conflict.

An eventual importer must insert parent-first, compare all PK and natural keys,
skip byte-equivalent rows and abort on differing existing records. Never use
`ON CONFLICT DO UPDATE`, blind `DO NOTHING`, ID regeneration, tenant rewrites,
bulk reseeding, or restore a whole dump over the current database. No apply
importer is enabled in this review package while the gates above remain open.

## Proposed teaching_shared schema

SQL artifacts are in `database/teaching_shared/`, generated reproducibly by
`scripts/teaching_shared_plan.py`. **Do not execute them before approval.**

- Version 1: **17 views**, reusing authoritative existing rows. Staff, departments,
  positions, profiles, modules, specialization links, module team eligibility,
  university-sourced cohort totals, venues, current administrative appointments
  and policy inputs, Exam periods and published validated Exam sessions/rooms.
  Source records/conflicting codes have explicit review views.
- Version 2: **5 separately permissioned views** for PP1 groups, snapshot metadata,
  validated draft sessions, draft assigned hours and snapshot planning groups.
  No official timetable, target workload, actual availability or registration
  master is inferred from these experimental/draft sources.
- One new operational `teaching_shared.schema_migrations` ledger. Each applied
  version inserts **one new ledger row**; it creates no academic master rows.
  Identical repeats verify stored view fingerprints and return without writes;
  drift or an unrecognized schema aborts, never replaces existing views.
- No availability/preference response view: existing workbook responses are raw
  private data, and Forms has no approved-response/submission workflow yet.
  No form-link, token, raw question/response, email or Identity account view.
- No new academic/staff/module/group/venue tables. Missing Teaching-specific
  storage stays a separate blocked phase; it is not duplicated into a shared
  master schema just to bypass ownership or migration tracking.

Current-source SELECT-body validation passed for all 22 proposed views. Canonical
counts include staff 110, departments 9, unambiguous modules 182, module source
rows 184, venues 62, teaching team rows 1,288, cohort totals 16, specializations 8
and Exam periods 2. There are no published validated Exam sessions, current
administrative appointments/policies or canonical Neon draft sessions. All 22
queries return zero rows for old/unset tenant contexts.

This validation ran **only SELECT queries**, not CREATE VIEW or role grants.
Actual installed view-owner/RLS/reader privilege checks remain pending approval.

## Read-only access design

The new NOLOGIN view-owner role has no SUPERUSER, BYPASSRLS, role-creation or DB
creation rights. It receives only required schema USAGE and source column SELECT;
it owns no source tables. The ordinary reader receives only shared-schema USAGE
and explicit view SELECT, without source-table privileges or owner membership.
Experimental/draft views use a separate opt-in reader group. No existing runtime
or user is automatically granted membership.

Views use `security_barrier=true`, an explicit canonical-institution filter and
transaction-local `schedence.institution_id` checks. Definer ownership is confined
to the restricted NOLOGIN role; `security_invoker=true` would require base-table
grants to readers, which would be unsafe for legacy non-RLS staff tables. Existing
RLS is neither disabled nor replaced. See PostgreSQL's [view security rules](https://www.postgresql.org/docs/current/sql-createview.html)
and [RLS behavior](https://www.postgresql.org/docs/current/ddl-rowsecurity.html).

After approved installation, the operator may grant the ordinary group to a
reviewed restricted service login. Never distribute the existing owner URL.
Use a pooled URL to the **same existing `neondb`**, with TLS, via ignored service
configuration; use the direct endpoint only for approved administrative work.
Do not change production application settings as part of this plan.

```sql
-- Operator only, after approval; substitute an actual reviewed restricted login.
-- GRANT schedence_teaching_shared_reader TO reviewed_service_login;
BEGIN READ ONLY;
SELECT set_config('schedence.institution_id',
  '84651d6d-0c8a-470a-a798-5220666fd367', true);
SELECT id, staff_number, full_name FROM teaching_shared.academic_staff;
SELECT id, module_code, module_name FROM teaching_shared.modules;
SELECT module_code, source_record_ids FROM teaching_shared.module_code_conflicts;
SELECT id, venue_code, capacity FROM teaching_shared.venues;
SELECT * FROM teaching_shared.examination_periods;
COMMIT;
```

IDs from `module_source_records` retain source-row semantics. Exam clients should
join exact codes only after checking `module_code_conflicts` and the five metadata
conflicts above; never silently normalize names or generate a new ID. Module
teams mean eligibility, not scheduled sessions. Staff Duty must provide its own
repository, scoped auth/permission contract and invigilation requirements; none
are fabricated here. Local timestamps in published Exam sessions require the
Exam owner's confirmed timezone semantics.

## Protection, backups and rollback

The old institution `44dc54c1-35df-4e53-8fc0-95a7d911368b` remains intact. Neon
contains 5,698 directly tenant-scoped rows for it, including 2 timetable versions
and 1,401 deliveries plus coverage, snapshots, input catalogs, Identity role
grants, provisioning and audits. Its registry row, historical files and root
`.env` reference remain unchanged. All teammate schemas, including `m3_demo`,
are untouched. No deletion, retirement or cross-tenant reassignment is proposed.

Existing backup `neondb-20261020T072635Z.dump` was checked locally: its SHA-256
and size match the manifest, and `pg_restore --list` reads 101 table-data entries.
The manifest records an earlier successful isolated restore with matching counts.
This is **not a fresh backup of today's 102-table Neon state or local Forms**.
No restore was run against either existing database. Full restore verification
would need a separately approved disposable environment; never restore over
`neondb` or the preserved isolated database. PostgreSQL documents [consistent
logical exports and restore considerations](https://www.postgresql.org/docs/current/app-pgdump.html).

Rollback for approved shared views is access deactivation: revoke only the new
reader groups' schema/view grants; retain all data and objects. Do not DROP
CASCADE, delete imported rows or restore production wholesale. Any eventual
Teaching insert phase remains retained history; its consumer exposure can be
disabled independently. Exact access deactivation SQL is supplied separately.

Before/after an approved change, compare existing table counts and row checksums,
source FK/CHECK/RLS/policy definitions, role privileges and migration markers;
expected differences are only the approved new schema/roles/grants/ledger rows
and explicitly approved canonical Teaching inserts. Recheck existing modules,
Exam data, old-tenant history and teammate schemas in particular.

## Verification and evidence

Focused tests cover code/name conflicts, ambiguous IDs, protected existing rows,
canonical view guards, sensitive-column exclusions, read-only reader grants,
duplicate-code quarantine and deterministic SQL generation. No CP-SAT runs,
service restarts, production setting changes, commits or pushes occurred.

Private ignored evidence:

- `scripts/neon-review/existing-neondb-audit-20261021-v1.json`: inventory, FK/index/
  trigger/policy metadata and hashed canonical insert candidates.
- `scripts/neon-review/academic-comparison-20261021-v2.json`: master/Exam comparisons,
  including staff department/type/position, venue attributes and group parents.
- `scripts/neon-review/proposed-view-readonly-validation-20261021-v1.json`: SELECT
  projection counts and canonical/old/unset context results.

**Approval boundary:** approve a specific shared-view SQL version separately
from any missing Teaching storage/import phase. Neither has been applied.
