# Examination Service

Examination timetabling and cognitive load optimization for Schedence.

**Current phase: data, secured API, seating settings and an offline draft scheduler.** This service has a
synthetic, reproducible dataset, the tables to store it, and a FastAPI backend
for examination administrators (Cognito login + Identity Service permissions).
The data routes are read-only; the only writes are the configurable exam
seating rules. Phase 1 CP-SAT generation runs offline on synthetic data; there
are no timetable-generation APIs, background workers, saved results or scheduler UI.

## Offline Phase 1 examination scheduler

The offline runner uses `DatasetSource(generate_dataset())`, `MemorySeatingStore`
and the existing frozen capacity snapshot. It does not read `.env`, connect to
Neon, contact Cognito, or write files. Students and registrations are synthetic;
venue teaching/maintenance blocks are labelled simulations, not institutional
bookings. Module and venue catalogue data remain the existing prototype inputs.

With the repository virtual environment active:

```powershell
# Optional solver dependency, also declared in pyproject.toml's [solver] extra:
python -m pip install ortools==9.15.6755
cd services/examination-service
python -m app.scheduling
python -m app.scheduling --include-draft
python -m app.scheduling --starts 09:00 14:00 --minimum-break 30 --time-limit 20
python -m app.scheduling --module IT1010 --module IT1100 --seating-percentage 40
python -m app.scheduling --module IT1010 --module-venues IT1010=computer_lab --max-rooms 6
python -m pytest tests/test_scheduling_inputs.py tests/test_scheduling_constraints.py tests/test_scheduling_validation.py tests/test_scheduling_integration.py tests/test_scheduling_hardening.py
```

Without `--include-draft`, stdout contains status, settings, policy, fingerprints,
diagnostics and summary counts. With it, stdout also contains every session,
room and numbered logical seat. Exit codes: 0 = independently validated draft,
1 = invalid inputs/no draft, 2 = command-line usage or missing dependency.
All responses use standard JSON on stdout, including usage errors, missing-solver
errors, and `--help` (a JSON object containing the help text). Solver time limits
must be finite and positive; NaN and Infinity are rejected before scheduling.
Error responses contain a status and structured violations or an error code/message.

### Approved configurable prototype policy

These are development settings approved for this prototype, not university policy:

| Setting | Initial value | Configuration |
| --- | --- | --- |
| Start times | 08:30, 13:30 | `--starts` / `SchedulingPolicy.permitted_start_times` |
| Student break | 60 minutes after the preceding exam, including reading time | `--minimum-break` |
| Written rooms | lecture hall, smart classroom | `--compatibility written=lecture_hall,smart_classroom` |
| Computer-based rooms | computer lab | `--compatibility computer_based=computer_lab` |
| Hybrid rooms | all four existing venue types | `--compatibility hybrid=...` |
| Per-module room types | inherit the exam-type rule | `--module-venues MODULE=type,type` |
| Maximum rooms | unlimited; split rooms share one session/time | `--max-rooms` |
| Seating | existing snapshot rules; CLI starts with an in-memory 50% default | `--seating-percentage` |
| Solver time | 60 seconds of CP-SAT search | `--time-limit` |
| Random seed / workers | 20261019 / 1 | `--seed`, `--workers` |

The student break is a **hard constraint**, with a configurable default of 60
minutes. Change it for the next run without editing source code:

```powershell
python -m app.scheduling --minimum-break 0
python -m app.scheduling --minimum-break 30
python -m app.scheduling --minimum-break 60
python -m app.scheduling --minimum-break 90
```

Each invocation uses its supplied value in a new frozen input snapshot. Values
are per run and are not saved; omitting the option uses the 60-minute default.
Python callers can supply `SchedulingPolicy(minimum_break_minutes=30)` when
loading the next snapshot. An existing snapshot keeps its original policy.
Breaks start after the full exam duration plus reading time. With a zero-minute
break, one exam may start exactly when the previous exam ends; overlapping exams
for the same student are always forbidden, including repeat examinations.

All dates and times use the source period's local calendar without timezone
conversion. Start times and period/block hours must have whole-minute precision.
Use one worker and the pinned solver for reproducible settings; a wall-clock
cutoff or multiple workers can still change search outcomes.

This is a feasibility-only model with no objective. A maximum of one exam per
student per day remains a future soft preference: multiple daily exams are
allowed when the hard constraints and minimum breaks permit them. No cognitive
load or other soft-constraint optimization is implemented. `OPTIMAL` for this
model means the satisfaction problem was solved; it is not a timetable-quality score.

### Hard constraints and validation

| Rule | Enforcement |
| --- | --- |
| H1 | Student no-overlap intervals include the configurable break; repeat registrations are included |
| H2 | Per-room candidate counts and logical seat numbers obey effective frozen capacity; zero-seat venues cannot be selected |
| H3 | Room types obey exam-type rules and optional module overrides |
| H4 | All source blocks are respected; overlapping blocks are merged for modelling |
| H5 | Optional room intervals cannot overlap another exam or a fixed block |
| H6 | Every exam belongs to the selected period and its date range |
| H7 | Starts obey policy and the full sitting fits within daily examination hours |
| H8 | Weekends and excluded dates obey the selected period's calendar |
| H9 | Every room and student interval reserves duration plus reading time |
| H10 | Each selected module has one sitting; each registered candidate gets exactly one room and logical seat |
| H11 | Input/output references, counts, session IDs, rooms and logical seats are checked for consistency |

Logical seats are numbered within each room/session; these are not physical seat
maps. All students taking a module sit at the same time across its allocated
rooms. Touching room intervals are allowed; student breaks are checked separately.
Room sharing by different exams at the same time is not supported.

`load_snapshot(source, store, period_code=..., policy=..., module_codes=...)`
freezes all inputs and checks relationships. `module_codes=None` selects all
offered modules; selected modules need at least one registration. Select codes
explicitly when an input source contains several offerings/periods: current
registrations have no period foreign key.

For a future database-backed caller, `load_database_snapshot(engine, ...)` reads
through `DatabaseSource` and `DatabaseSeatingStore` in one existing
`snapshot_session` (REPEATABLE READ, READ ONLY), then rolls back/closes before
solving. The CLI does not expose this adapter. Other source adapters must provide
a coherent source view. Fingerprints cover all selected data and policy, excluding
the capacity snapshot clock; seating ruleset version is retained.

`generate_timetable(snapshot, SolverSettings(...))` imports OR-Tools only when
needed. Existing FastAPI startup and the independent validator do not require it.
The solver returns `FEASIBLE`, `OPTIMAL`, `INFEASIBLE`, `UNKNOWN`, or `MODEL_INVALID`.
`UNKNOWN` means no solution or proof was obtained within search limits, not that
the schedule is impossible. Invalid inputs are reported as `MODEL_INVALID`.
Infeasible results currently give a general diagnostic, not a minimal conflict set.

`validate_timetable(snapshot, draft)` recomputes H1-H11 from the output and raw
snapshot, without CP-SAT or model helpers. Every successful solver result goes
through it; rejected output is withheld and reported as `MODEL_INVALID`.
Session, room and seat collections accept ordered iterables, including lists
and generators, and are materialized once into tuples during construction.
Text, mappings, unordered sets and records of the wrong type are rejected.
Caller-owned lists cannot change an already constructed result. Malformed field
types are checked before hashing/sorting identifiers or comparing times.
Construction errors expose `SchedulingValidationError.violations`; source-loader
errors expose `SnapshotError.violations`. Both contain constraint/message records.

Implementation lives in `app/scheduling/`: `policy.py`, `types.py`, `inputs.py`,
`availability.py`, `cp_sat.py`, `validation.py`, `service.py`, and `__main__.py`.
No result storage, publishing/booking guarantee, invigilator allocation or real
institutional policy is provided in Phase 1.

## Layout

```text
services/examination-service/
├── pyproject.toml                 # dependencies + pytest config
├── alembic.ini                    # Alembic config (no URL; read from .env via db-core)
├── alembic/
│   ├── env.py                     # only touches the "examinations" schema
│   └── versions/
│       ├── 20261012_0001_create_seed_tables.py
│       ├── 20261012_0002_real_module_catalog.py
│       ├── 20261014_0003_campus_venues_expand.py
│       ├── 20261014_0004_campus_venues_contract.py
│       └── 20261015_0005_configurable_exam_seating.py
├── app/
│   ├── main.py                    # FastAPI app (create_app); uvicorn app.main:app
│   ├── database.py                # engine + read-only sessions; writable only for seating changes
│   ├── auth/                      # Cognito token check (auth-core) + Identity Service permissions
│   ├── api/                       # routes, response schemas, input rules, safe errors
│   ├── sources/                   # read-only data layer shared with the future scheduler
│   │   ├── base.py                # ExamDataSource interface
│   │   ├── types.py               # Module, Student, Registration, Venue, ... records
│   │   ├── database.py            # DatabaseSource: reads the seed_* tables
│   │   └── memory.py              # DatasetSource: same answers from the generated dataset
│   ├── seating/                   # configurable exam seating capacity
│   │   ├── rules.py               # floor(full x percentage / 100), policy (default + overrides)
│   │   ├── store.py               # SeatingRuleStore + DatabaseSeatingStore
│   │   ├── memory.py              # in-memory store (tests)
│   │   ├── service.py             # validation, versions, dry run, history, logging
│   │   └── snapshot.py            # frozen capacity snapshot for the future CP-SAT scheduler
│   ├── db/
│   │   ├── base.py                # Base with MetaData(schema="examinations")
│   │   ├── seed_models.py         # development-only seed_* tables
│   │   └── seating_models.py      # exam_seating_rules + exam_seating_rule_history
│   └── seed/
│       ├── assumptions.py         # every configurable number (NOT real policy)
│       ├── catalog.py             # loads data/module_catalog.csv + data/venue_catalog.csv
│       ├── catalog_import.py      # cleans the Excel module catalogue into data/
│       ├── venue_import.py        # cleans the campus venue list into data/
│       ├── xlsx.py                # minimal read-only .xlsx reader
│       ├── records.py             # immutable dataclasses for the dataset
│       ├── generator.py           # deterministic dataset generator
│       ├── validation.py          # checks run before any write
│       ├── seeder.py              # insert/update by natural key; never deletes
│       ├── backup.py              # export / verify / restore seed tables
│       ├── replace.py             # atomic replacement of old seed data
│       └── __main__.py            # CLI: python -m app.seed
├── data/                          # cleaned catalogue + cleaning audit files
└── tests/                         # unit + API tests; opt-in live tests (--live)
```

## Module catalogue (real data)

Modules come from the team's exam dataset (`SLIIT_Final_Exam_Dataset_Semester_No_Unique_ID.xlsx`,
sheet "Lecturer Exam Data"). The Main Admin dataset
(`Schedence_Unique_Modules_Year_and_Specialization.xlsx`) is used only as read-only evidence.

```powershell
python -m app.seed.catalog_import --exam-file PATH\to\exam.xlsx --admin-file PATH\to\admin.xlsx
```

| File in `data/` | Content |
|---|---|
| `module_catalog.csv` | one clean row per module code (no lecturer names) |
| `catalog_corrections.csv` | every changed value with its original (IT3060 -> IT3041, spelling and Unicode fixes) |
| `removed_duplicates.csv` | every dropped duplicate record, the kept record, the basis, and an `uncertain` flag |
| `module_review.csv` | records still worth checking (year/semester or name differs from Main Admin) |
| `cleaning_report.md` | generated summary of the run |
| `module_review_decisions.csv` | **input**: `module_code,academic_year,semester,basis,note` picks one record per duplicate code |

Duplicate codes are never resolved silently. One is resolved only by a recorded
decision, or when exactly one record matches the Main Admin year/semester;
otherwise it is left out of the catalogue. All 26 duplicate codes currently
have decisions: `team_decision` (Record B, the 9 PP1 codes) and `code_year_rule`
(the other 17: keep the record whose year matches the code's year digit). Both
are research-prototype choices, marked `uncertain`, not official curriculum
verification. Rerun the import after editing the decisions file.

## Venue catalogue (campus venues)

Venues come from the campus venue list supplied by the team (tab-separated:
code, name, type, full capacity, building, floor). The raw file is **not**
committed; only the cleaned result is.

```powershell
python -m app.seed.venue_import --source PATH	oenue.txt
```

| File in `data/` | Content |
|---|---|
| `venue_catalog.csv` | one row per venue code: 62 venues (31 lecture halls, 10 smart classrooms, 19 computer labs, 2 BYOD labs) |
| `venue_cleaning_report.md` | generated summary: corrections, removed duplicates, rejected rows, capacity by type |

Codes and names are kept exactly as supplied (`F1301+F1302`, `CyberSecLab`,
`Robotics Lab`, ...). Only clear formatting problems are corrected and logged
(two rows had no name column, so the code is used as the name). Exact
duplicate lines are dropped; two different records with one code are never
merged: the import fails and writes nothing.

**Examination capacity rule:** only part of a venue can be used in an exam.
The share is configurable (see "Exam seating settings" below); it starts at 50%:

```text
effective exam capacity = floor(full capacity x percentage / 100)   (whole numbers only)
```

240 -> 120, 61 -> 30, 99 seats at 33% -> 32. A venue whose effective capacity
is 0 is **unavailable for examinations**. `normal_capacity` (full capacity) is
never reduced. The stored seed column `seed_venues.exam_capacity` stays
(always floor(full x 0.5)) for compatibility only; the API and the scheduler use
the effective capacity. The scheduler must never seat more students than the
effective capacity. One exam may use several compatible rooms;
`max_rooms_per_exam` in the assumptions can limit that (no limit by default).

## Seed tables (schema `examinations`)

| Table | Key | Will later come from |
|---|---|---|
| `seed_modules` | `module_code` | Academic Data Service: module offerings |
| `seed_module_specializations` | `(module_code, specialization)` | Academic Data Service: programme structure |
| `seed_students` | `student_id` (synthetic, e.g. `IT30315600`) | Academic Data Service: students |
| `seed_registrations` | `(student_id, module_code)` | Academic Data Service: registrations |
| `seed_venues` | `venue_id` | Resource Service: venues |
| `seed_venue_blocks` | `block_id`; unique `(source, venue_id, block_date, start_time)` and `(source, source_ref)` | Resource Service / Teaching Timetable: availability |
| `seed_exam_periods` | `period_code` | Examination Service (stays), from the institutional calendar |
| `seed_exam_period_exclusions` | `(period_code, excluded_date)` | same as above |

Candidate counts per module are **never stored**. They are always counted
from `seed_registrations`. `seed_modules.specialization` is a legacy column
of the original SYN rows (NULL for new rows); it will be dropped once those
rows are removed.

**Replacing the seed tables later:** the API and the scheduler read input only
through `app.sources.ExamDataSource`. `DatabaseSource` reads these `seed_*`
tables today; a later source can call the Academic Data and Resource service
APIs instead, without changing the callers.

## Dataset (fixed random seed `20261019`)

| Item | Count |
|---|---|
| Modules: real Semester 2 modules for IT, SE, DS, CYBER | 42 |
| Module-specialization links (all programmes kept) | 91 |
| Students (synthetic IDs, 16 cohorts of 17-35) | 390 |
| Registrations | 1,700 (63 repeat) |
| Venues: campus list (31 lecture halls, 10 smart classrooms, 19 computer labs, 2 BYOD labs) | 62 |
| Venue blocks, all labelled SIMULATED (22 teaching, 5 maintenance) | 27 |
| Exam period: 2026-11-16 to 2026-11-28, weekdays 08:30-17:30, 1 excluded day | 9 exam days |

**Assumptions, not real policy** (all in [app/seed/assumptions.py](app/seed/assumptions.py)):
cohort sizes; 15% repeat students; 10 minutes when Extra_Reading_Time = Yes;
written exams use lecture halls and smart classrooms, computer-based exams use
computer labs only (not BYOD labs), hybrid exams may use any venue unless
restricted (`hybrid_venue_types`, `hybrid_lab_required_modules`); simulated
teaching and maintenance blocks; weekends off; one simulated non-exam day.
The exam seating percentage is a setting (initially 50%), not an assumption.
Student IDs use fictional intakes 30-33 so they cannot match real students.

## Setup on Windows PowerShell

Run from the repository root:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ./packages/db-core -e ./packages/auth-core
python -m pip install -e "./services/examination-service[dev]"
if (-not (Test-Path .env)) { Copy-Item .env.example .env }   # then set DATABASE_URL
python -m schedence_db.check_connection
```

## One-time: create the schema (database owner, after review)

Alembic stores its version table inside `examinations`, so the schema must
exist first. Neither Alembic nor the seed script creates it. Run this once
in the Neon SQL Editor:

```sql
CREATE SCHEMA IF NOT EXISTS examinations;
```

## Migrations

| Revision | What it does |
|---|---|
| `0001` | creates the seed tables |
| `0002` | real module catalogue support (non-destructive) |
| `0003` | campus venues, **expand**: adds `building`, `floor`, `source_ref`; allows the new venue types and `simulated_*` block sources. The old rows stay valid. |
| `0004` | campus venues, **contract**: `building`/`floor` NOT NULL, only campus venue types, `exam_capacity = normal_capacity / 2`. Run it only **after** the venue replacement. |
| `0005` | configurable seating: drops the fixed 50% check, creates `exam_seating_rules` + `exam_seating_rule_history`, inserts the 50% default. Changes no existing row. **Not applied to Neon yet** (see "Applying 0005"). |

Alembic runs all pending revisions in **one** transaction. Running
`upgrade head` before the venue replacement therefore fails at 0004 and
changes nothing; upgrade to `0003` first (see "Venue replacement").

```powershell
cd services/examination-service
alembic upgrade head --sql      # preview the SQL; changes nothing
alembic upgrade head            # create the seed tables
alembic current                 # show the applied revision
```

## Seeding

```powershell
cd services/examination-service
python -m app.seed --validate-only   # generate + validate; no database
python -m app.seed --dry-run         # compare with the database; writes nothing
python -m app.seed                   # insert / update; safe to rerun
```

Both tools use `DIRECT_DATABASE_URL` by default. While no real direct URL is
configured, add a flag to use the pooled `DATABASE_URL` instead:
`alembic -x pooled=true upgrade head` and `python -m app.seed --pooled`.

The seed script validates the data first, inserts missing rows, updates
changed rows, and leaves identical rows alone. It **never deletes**. Rows in
the database that are not in the dataset are only reported as `extra`.

## Backup and atomic replacement

```powershell
python -m app.seed.backup export --pooled             # seed tables (+ seating tables from 0005) -> backups/
python -m app.seed.backup verify backups\seed_backup_...  # checksums, no database
python -m app.seed.replace --backup backups\seed_backup_... --pooled --dry-run   # full rehearsal, rolled back
python -m app.seed.replace --backup backups\seed_backup_... --pooled             # delete old + seed new, one transaction
python -m app.seed.backup restore backups\seed_backup_... --pooled --dry-run     # re-insert missing rows from a backup
```

Backups work at 0004 and at 0005: tables that do not exist yet are left out, and
the manifest records the Alembic revision. The seating tables keep their IDs.
`restore` brings them back only when no change can be lost:

* backup taken at 0004: the seating rules in the database are not touched;
* database with only the 50% default written by 0005: replaced by the backup;
* otherwise every database row must already be in the backup, unchanged, and
  only missing rows are added. If the database has a newer change, the whole
  restore stops and changes nothing.

A 0005 backup into a 0004 database needs `--skip-seating` (seed tables only).

`replace` deletes only rows that are in the verified backup and not in the new
dataset, then seeds, then checks final counts, leftovers and foreign keys. Any
failure rolls back the whole transaction.

### Venue replacement (9 fictional venues -> 62 campus venues)

Each step needs approval; nothing else in the database changes. Modules,
students, registrations and the exam period are identical before and after.

```powershell
cd services/examination-service
python -m app.seed.backup export --pooled                      # 1. backup (2,261 rows), checksums verified
alembic -x pooled=true upgrade 0003 --sql                      # 2. preview
alembic -x pooled=true upgrade 0003                            # 3. expand schema
python -m app.seed.replace --backup backups\<step-1> --pooled --dry-run   # 4. rehearsal, rolled back
python -m app.seed.replace --backup backups\<step-1> --pooled             # 5. one transaction
alembic -x pooled=true upgrade 0004                            # 6. contract schema
python -m pytest --live tests/test_live_api.py                 # 7. read-only check: database == dataset
```

Expected after step 5: 9 venues and 27 old blocks deleted; 62 venues and 27
simulated blocks inserted; every other table unchanged; 2,314 rows in total.

**Rollback (reverse order):** take a new backup; `alembic downgrade 0003`;
check out the previous code (9 fictional venues) and run `replace` with the
new backup (deletes the campus venues and blocks, re-inserts the old ones);
then `alembic downgrade 0002`. 0003's downgrade is only valid once no campus
venue or simulated block remains.

### Real availability from the Teaching Timetable (later)

The scheduler reads every unavailable period through
`ExamDataSource.list_venue_blocks()`. Real teaching sessions will be stored
with `source = "teaching_timetable"` and `source_ref` = the session's booking
ID; `(source, source_ref)` is unique, so a repeated sync updates a booking
instead of duplicating it. A sync never touches `simulated_*` rows; once real
bookings exist, the simulated teaching blocks are switched off.

## Exam seating settings

Tables (schema `examinations`, owned by this service, created by 0005):

| Table | Content |
|---|---|
| `exam_seating_rules` | one `default` rule + at most one `venue` override per venue; `percentage` 1-100; `version` |
| `exam_seating_rule_history` | append-only: action (`create`/`update`/`delete`), old and new percentage, version, `changed_by` (Identity Service `user_id`; NULL = migration), `changed_at`, `reason` |

* A venue uses its override if it has one, otherwise the default.
* Every change needs the version you last read (`expected_version`). If someone
  changed the rule in the meantime the answer is `409` and nothing is saved.
  The rule row is locked (`SELECT ... FOR UPDATE`, waits at most 5 s) and the
  update also checks the version. A re-created override never reuses an old
  version number.
* The rule and its history row are written in **one** transaction: if anything
  fails, both are rolled back.
* `dry_run=true` returns the same preview (affected venues, capacity before and
  after, venues that become unavailable) in a `READ ONLY` transaction; nothing is saved.
* Saved changes are logged (`app.seating.service`: action, percentages, version,
  user ID; never the token or the reason text).
* Setting the same value again answers `changed: false` and writes nothing.

**For the future CP-SAT scheduler:** read capacities once per run with
`app.seating.load_capacity_snapshot()` inside `snapshot_session(engine)` (one
`REPEATABLE READ`, `READ ONLY` transaction). The snapshot is frozen; store its
`ruleset_version` and `fingerprint` with the run. Never use
`Venue.exam_capacity` (the legacy seed value) for scheduling.

### Applying 0005 (later, after review)

Each step needs approval. Only the two new tables and one check change.

```powershell
cd services/examination-service
python -m app.seed.backup export --pooled                 # 1. backup at 0004: 2,314 seed rows, no seating tables
alembic -x pooled=true upgrade 0004:0005 --sql            # 2. preview the SQL; changes nothing
alembic -x pooled=true upgrade 0005                       # 3. one transaction
alembic -x pooled=true current                            # 4. expect 0005
python -m pytest --live tests/test_live_api.py            # 5. read-only check: seed data == dataset
```

Deploy the new API code together with (not before) 0005: until 0005 exists,
`GET /api/v1/venues` and the settings routes answer `503` ("seating rules are
not configured"); the other routes keep working.

After step 3, take a new backup: it then includes the seating tables.

**Rollback:** `alembic -x pooled=true downgrade 0004` drops the two tables and
restores the 50% check (the seed column was never changed, so it holds again).
If any user has changed a rule, the downgrade stops and changes nothing, so
the history is not lost by accident. Export it first (`python -m app.seed.backup export --pooled`), then run `alembic -x pooled=true -x drop_seating_history=true downgrade 0004`.

## API

Start the server from `services/examination-service` (Windows needs the
selector event loop for psycopg async):

```powershell
cd services/examination-service
uvicorn app.main:create_app --factory --port 8003 --loop asyncio:SelectorEventLoop
```

Interactive docs: <http://127.0.0.1:8003/docs> (hidden when `APP_ENV=production`).
The API uses the pooled `DATABASE_URL`, one engine per process, and opens every
transaction `READ ONLY`. The only exception: saved seating changes (`PUT`/`DELETE`
under `/api/v1/settings/exam-seating`, without `dry_run`) get a writable
session that commits explicitly.

### Security (RBAC)

Reuses the team's authentication; there is no separate login system.

1. The caller sends a Cognito **access token** (`Authorization: Bearer ...`).
   auth-core verifies it locally (signature, issuer, expiry, `token_use`, app client).
2. Permissions come from the Identity Service: `GET /api/v1/identity/me/permissions`
   with the caller's own token (same contract as the Academic Data Service). The
   answer must be about the same user. Reads may reuse it for 15 s; if the Identity
   Service is down the request is refused (`503`, fail closed). Role or permission
   headers from the browser are ignored.
3. Every `/api/v1` route requires authentication. Examination GET routes require
   `examinations:read` in the institution `SLIIT_INSTITUTION_ID`.
4. Seating changes (`PUT`/`DELETE /api/v1/settings/exam-seating/...`), including
   dry runs, require `examinations:manage` in that institution. They do not also
   require `examinations:read`. Writes always ask Identity again (no cache).

Only `EXAMINATION_ADMIN` receives these permissions in the Identity catalog.
Its existing `examinations:generate`, `examinations:approve`,
`academic:staff:read`, `academic:workload:read` and `venues:read` grants remain.
Other roles, including `SUPER_ADMIN`, have no examination override. Supporting
permissions alone do not grant access to this service. Student self-service
stays off until a secure user -> student ID mapping exists.

| Examination permission | Purpose |
|---|---|
| `examinations:read` | Current data/settings GET routes; future timetable views, report views, comparisons and exports |
| `examinations:manage` | Current seating PUT/DELETE routes; future period, daily hours, scheduling policy configuration and draft edits |
| `examinations:generate` | Future CP-SAT generation, cognitive load optimization and validation runs |
| `examinations:approve` | Future approval and publication |

Generation and approval alone no longer grant reads or seating changes. No
scheduling routes are added by this RBAC change. Existing API paths, request
and response formats, seating history and version checks are preserved.

**Rollout:** deploy these checks only after a separately approved Identity RBAC
data update has added `examinations:read` and `examinations:manage` and granted
them to `EXAMINATION_ADMIN`. The code change does not update Neon. Existing
admins without the new grants will receive `403` until that step is complete.
No database schema migration or Cognito change is needed.

`/health` and `/health/db` are public. Errors: `401` (no or invalid token),
`403` (no permission, disabled or unknown account), `503` (Identity Service
unavailable). Settings (repository-root `.env`): `AWS_REGION`, `COGNITO_USER_POOL_ID`,
`COGNITO_APP_CLIENT_ID`, `SLIIT_INSTITUTION_ID`, optional `IDENTITY_SERVICE_URL`
(default `http://localhost:8001`) and `FRONTEND_URL` (CORS).

To call the API locally, run the Identity Service too
(`python scriptsun_services.py --only identity` from the repository root),
sign in as an examination admin, and paste the **access token** into Swagger's
"Authorize" button.

| Method and path | Purpose | Filters / paging |
|---|---|---|
| `GET /health` | process is running (no database) | |
| `GET /health/db` | `SELECT 1`; `503` with a safe reason if unreachable | |
| `GET /api/v1/modules` | modules with specializations and candidate counts | `academic_year`, `specialization`, `exam_type` |
| `GET /api/v1/modules/{module_code}` | one module | |
| `GET /api/v1/students` | students, paginated | `academic_year`, `specialization`, `limit` (1-500, default 50), `offset` |
| `GET /api/v1/students/{student_id}` | one student with their registrations | |
| `GET /api/v1/registrations` | registrations, paginated | `student_id`, `module_code`, `registration_type`, `limit`, `offset` |
| `GET /api/v1/venues` | venues with `exam_seating_percentage`, `exam_seating_rule` (`default`/`venue`), effective `exam_capacity`, `available_for_exams`, legacy `seed_exam_capacity` | `venue_type` |
| `GET /api/v1/venue-blocks` | times a venue is unavailable | `venue_id`, `source`, `date_from`, `date_to` |
| `GET /api/v1/exam-periods` | periods with exclusions and exam days | |
| `GET /api/v1/exam-periods/{period_code}` | one period | |
| `GET /api/v1/settings/exam-seating` | default rule, overrides, totals, unavailable venues, recent history | `history_limit` (0-500, default 20) |
| `PUT /api/v1/settings/exam-seating/default` | change the default (`examinations:manage`) | body `percentage`, `reason`, `expected_version`; `dry_run` |
| `PUT /api/v1/settings/exam-seating/venues/{venue_id}` | create (`expected_version: null`) or change an override | body as above; `dry_run` |
| `DELETE /api/v1/settings/exam-seating/venues/{venue_id}` | remove an override | `expected_version`, `reason`, `dry_run` |

Settings input rules: `percentage` is a whole number 1-100 (`50.5`, `"50"` and
`true` are rejected); `reason` is 5-500 characters without control characters
or leading/trailing spaces; unknown fields are rejected. Example:

```text
PUT /api/v1/settings/exam-seating/default?dry_run=true
{"percentage": 60, "reason": "Exam board decision 2026-10", "expected_version": 1}
```

Paginated responses look like `{"items": [...], "total": 390, "limit": 50, "offset": 0}`.
Invalid input answers `422`, unknown IDs `404`, version conflicts `409`, and
database problems `503` with a generic message (details go to the server log
without credentials).

## Tests

The access tests cover all six role labels and all 16 combinations of the four
examination permissions on every protected route. Role labels do not bypass
permission checks. Seating writes are checked with and without `dry_run`.
Tests also cover institution boundaries, revoked permissions, invalid tokens,
Identity failures, unchanged responses and the complete protected route list.
RBAC/API tests use fake Identity responses and in-memory data, without AWS or Neon.

```powershell
cd services/examination-service
python -m pytest            # unit tests; no database or credentials needed
python -m pytest --live tests/test_live_api.py   # read-only checks against the real database
python -m pytest --live     # all live tests; the seed test writes inside a transaction, then ROLLS BACK
```

`tests/test_seating_postgres.py` runs migrations 0001-0005, the downgrade and
the seating API against a throw-away **local** PostgreSQL (never Neon; only
localhost URLs are accepted). It creates and drops its own `exam_seating_*`
databases:

```powershell
$env:SCHEDENCE_TEST_POSTGRES_URL = "postgresql://postgres@127.0.0.1:5432/postgres"
python -m pytest tests/test_seating_postgres.py
```
