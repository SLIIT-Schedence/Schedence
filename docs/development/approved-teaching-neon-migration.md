# Approved additive Teaching / Forms deployment

Applied on 2026-10-21 to the existing `neondb`, project
`super-leaf-21551515`, branch `br-dry-darkness-b549ci92`. No database or
Neon branch was created. Canonical tenant:
`84651d6d-0c8a-470a-a798-5220666fd367`.

The metadata-only [deployment manifest](../../database/teaching_shared/approved-deployment.v1.json)
records the installed contracts and existing module IDs. Earlier `v001` / `v002`
SQL and comparison reports are historical **unapplied review drafts**, not the
installed deployment. Do not execute them against this installation.

## Exact changes

| Item | Result |
| --- | ---: |
| Reviewed local candidates | 335 |
| Draft form rows inserted | 4 |
| Experimental Teaching rows excluded with their snapshot/history dependencies | 331 |
| Additional local draft outside the reviewed 335, excluded | 1 |
| New Forms tables, in existing Teaching schema | 3 |
| New migration ledger table / row | 1 / 1 |
| Read-only shared views | 17 |
| Academic / Resource / Examination master rows inserted or changed | 0 |
| Pre-existing tables / rows verified unchanged by complete row hashes | 102 / 21,692 |
| Total tables / rows after deployment | 106 / 21,697 |

The Forms tables are `teaching.form_template_drafts`, `form_distributions` and
`form_distribution_recipients`. Existing Teaching APIs hard-code this schema;
using it preserves store compatibility without editing application or teammate
code. Sending tables contain zero rows. All three tables have forced tenant
RLS. The application receives only the required Forms privileges, with no
DELETE grant. Distribution definitions remain immutable.

Both excluded snapshots contain a synthetic PP1 test calendar and experimental
planning groups. All 331 dependent Teaching rows remain exclusively local and
unchanged. The generation-job tables were not installed in Neon: their excluded
data has no eligible snapshot dependency. No snapshots were rewritten or their
checksums changed to evade the exclusion.

The 91 existing Teaching module identities are reused in the shared crosswalk,
plus the separately verified semester-2 Statistics record:
`956bed92-6cc5-4b16-9e22-58c1a92b66ac` (`IT2100`). Thus the shared module view has
92 distinct existing IDs/codes. The other `IT2100` seminar is untouched and
excluded from this contract. `IT3060` has a NULL Exam mapping; the two reviewed
capitalization-only differences retain their original stored names.

The shared contracts also reuse the existing 110 active staff, 9 departments,
and 62 venues. They do not insert copies. The 28 Exam-only codes and unresolved
module identities outside this contract are not automatically merged or imported.

## Safety evidence

Fresh full archive, private restore SQL, payload-bearing migration SQL and reports
are in ignored `scripts/neon-review/`. Never commit those files.

- Archive: `backup-20261021T060734Z/neondb.dump`, 1,211,580 bytes.
- SHA-256: `ea8815420906469921ac2258ed2afe6bdc12fa8adef99d2fff9f50686f3fb8f3`.
- Verified manifest: `backup-20261021T060734Z/verified-manifest.json`.
- The dump succeeded; the separate comparison connection initially expired
  while idle. Verification then independently compared the completed archive
  against fresh live hashes, restored all 102 tables in the existing empty local
  `postgres` database, matched every row hash, and rolled back. No new database
  was needed and `schedence_step12b4_test` was not changed.
- Exact additive DDL/inserts were tested twice in that transactional restore.
  Foreign keys/check constraints, normal RLS enforcement, canonical/old/unset
  tenant visibility, private Forms exclusion and repeat idempotence passed.
- Live schema/column grant authority was checked before writes. The real apply
  ran in one transaction and rechecked every pre-existing row hash before commit
  and after commit.
- Artifact SHA-256:
  `5496e2988afc1d47eb5b62b870ab4f53288e487aa830dfdc860fd35435cf7ed8`.
- Reports: `approved-additive-migration-20261021/{dry-run,applied,service-access}.json`.

No existing records, old-institution data, Examination table definitions or
source RLS policies were changed. New view-owner permissions are limited to
the source columns used by the approved contracts. No existing Alembic version
row was updated; the separate integration ledger records this selective
deployment. **Do not blindly run the normal Teaching Alembic chain**: generation
jobs remain deferred and the Forms tables already exist. A future normal
deployment needs an explicitly reviewed reconciliation of that chain.

## Shared access and service configuration

Views use security barriers, explicit canonical-tenant predicates and a restricted
NOLOGIN / NOBYPASSRLS owner. Existing source RLS continues to apply. Reader role
`schedence_teaching_shared_reader` has SELECT only on the 17 approved views,
not on the private Forms tables, preference responses, login records or source
payloads. Experimental group/draft-timetable views were not installed.

The existing restricted `schedence_teaching_runtime` login is a member of that
reader role. A transaction can use:

```sql
BEGIN READ ONLY;
SET LOCAL ROLE schedence_teaching_shared_reader;
SET LOCAL schedence.institution_id = '84651d6d-0c8a-470a-a798-5220666fd367';
SELECT * FROM teaching_shared.reviewed_module_crosswalk;
SELECT * FROM teaching_shared.modules;
ROLLBACK;
```

Teaching's existing Academic/Resource reader configuration already points to
this Neon database and its development institution allowlist is canonical.
The read-only verification calls the real `load_owner_rows` Academic Inputs
adapter and `SqlFormTemplateStore`, not a mock or a solver. No service restart,
generation configuration change or authenticated browser action is part of
this migration. The port-8005 generation persistence remains isolated/local;
migrating Forms into Neon does not silently redirect that development server's
Forms store away from its local override.

Other component owners should choose their restricted service login, authorize
only the needed shared-reader membership, and use tenant-bound read-only
transactions. Do not distribute the database-owner credentials or weaken RLS.
No teammate configuration has been edited. Examination retains its existing
207 module codes and canonical dataset binding. Staff Duty's repository/login
is unavailable; its consumer integration and authorization require its owner.
Published-exam views deliberately exclude merely approved/unpublished sessions.

## Rollback readiness

Before commit, any verification failure rolls back the whole additive
transaction. After commit, the guarded
[access-deactivation script](../../database/teaching_shared/deactivate-approved-access.sql)
can remove only the newly granted application/shared access while retaining
every original and inserted record. It has **not** been executed. No destructive
rollback or restore into Neon is authorized. The full verified pre-change
archive and the local source rows remain available.

Focused Python tests: 71 passed; compile and `git diff --check` passed. No
CP-SAT, production generation, commits or pushes ran.
