# Examination Service

Examination timetabling and cognitive load optimization for Schedence.

**Current phase: data and read-only API.** This service has a synthetic,
reproducible dataset, the tables to store it, and a read-only FastAPI backend
to read it. It has no scheduler or UI yet.

## Layout

```text
services/examination-service/
├── pyproject.toml                 # dependencies + pytest config
├── alembic.ini                    # Alembic config (no URL; read from .env via db-core)
├── alembic/
│   ├── env.py                     # only touches the "examinations" schema
│   └── versions/
│       ├── 20261012_0001_create_seed_tables.py
│       └── 20261012_0002_real_module_catalog.py
├── app/
│   ├── main.py                    # FastAPI app (create_app); uvicorn app.main:app
│   ├── database.py                # engine + read-only request sessions (via db-core)
│   ├── api/                       # routes, response schemas, input rules, safe errors
│   ├── sources/                   # read-only data layer shared with the future scheduler
│   │   ├── base.py                # ExamDataSource interface
│   │   ├── types.py               # Module, Student, Registration, Venue, ... records
│   │   ├── database.py            # DatabaseSource: reads the seed_* tables
│   │   └── memory.py              # DatasetSource: same answers from the generated dataset
│   ├── db/
│   │   ├── base.py                # Base with MetaData(schema="examinations")
│   │   └── seed_models.py         # development-only seed_* tables
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

**Examination capacity rule:** only half of a venue can be used in an exam:
`exam_capacity = floor(full_capacity x 0.5)` for every venue type (240 -> 120,
60 -> 30, 30 -> 15). Both values are stored (`normal_capacity` = full capacity,
never reduced). Migration 0004 enforces it in the database, and the scheduler
must never seat more students than `exam_capacity`. One exam may use several
compatible rooms; `max_rooms_per_exam` in the assumptions can limit that
(no limit by default).

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
The 50% exam seating rule is the examination policy above, not an assumption.
Student IDs use fictional intakes 30-33 so they cannot match real students.

## Setup on Windows PowerShell

Run from the repository root:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ./packages/db-core
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
python -m app.seed.backup export --pooled             # all seed tables -> backups/ (git-ignored)
python -m app.seed.backup verify backups\seed_backup_...  # checksums, no database
python -m app.seed.replace --backup backups\seed_backup_... --pooled --dry-run   # full rehearsal, rolled back
python -m app.seed.replace --backup backups\seed_backup_... --pooled             # delete old + seed new, one transaction
python -m app.seed.backup restore backups\seed_backup_... --pooled --dry-run     # re-insert missing rows from a backup
```

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

## Read-only API

Start the server from `services/examination-service` (Windows needs the
selector event loop for psycopg async):

```powershell
cd services/examination-service
uvicorn app.main:app --port 8003 --loop asyncio:SelectorEventLoop
```

Interactive docs: <http://127.0.0.1:8003/docs>. The API uses the pooled
`DATABASE_URL`, one engine per process, and opens every transaction
`READ ONLY`, so it cannot change the database.

| Method and path | Purpose | Filters / paging |
|---|---|---|
| `GET /health` | process is running (no database) | |
| `GET /health/db` | `SELECT 1`; `503` with a safe reason if unreachable | |
| `GET /api/v1/modules` | modules with specializations and candidate counts | `academic_year`, `specialization`, `exam_type` |
| `GET /api/v1/modules/{module_code}` | one module | |
| `GET /api/v1/students` | students, paginated | `academic_year`, `specialization`, `limit` (1-500, default 50), `offset` |
| `GET /api/v1/students/{student_id}` | one student with their registrations | |
| `GET /api/v1/registrations` | registrations, paginated | `student_id`, `module_code`, `registration_type`, `limit`, `offset` |
| `GET /api/v1/venues` | venues with exam capacity | `venue_type` |
| `GET /api/v1/venue-blocks` | times a venue is unavailable | `venue_id`, `source`, `date_from`, `date_to` |
| `GET /api/v1/exam-periods` | periods with exclusions and exam days | |
| `GET /api/v1/exam-periods/{period_code}` | one period | |

Paginated responses look like `{"items": [...], "total": 390, "limit": 50, "offset": 0}`.
Invalid input answers `422`, unknown IDs `404`, and database problems `503`
with a generic message (details go to the server log without credentials).

## Tests

```powershell
cd services/examination-service
python -m pytest            # unit tests; no database or credentials needed
python -m pytest --live tests/test_live_api.py   # read-only checks against the real database
python -m pytest --live     # all live tests; the seed test writes inside a transaction, then ROLLS BACK
```
