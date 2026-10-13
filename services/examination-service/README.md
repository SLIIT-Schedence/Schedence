# Examination Service

Examination timetabling and cognitive load optimization for Schedence.

**Current phase: development data only.** This service has a synthetic,
reproducible dataset and the tables to store it. It has no API, scheduler or
UI yet.

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
│   ├── db/
│   │   ├── base.py                # Base with MetaData(schema="examinations")
│   │   └── seed_models.py         # development-only seed_* tables
│   └── seed/
│       ├── assumptions.py         # every configurable number (NOT real policy)
│       ├── catalog.py             # loads data/module_catalog.csv + synthetic venues
│       ├── catalog_import.py      # cleans the Excel module catalogue into data/
│       ├── xlsx.py                # minimal read-only .xlsx reader
│       ├── records.py             # immutable dataclasses for the dataset
│       ├── generator.py           # deterministic dataset generator
│       ├── validation.py          # checks run before any write
│       ├── seeder.py              # insert/update by natural key; never deletes
│       ├── backup.py              # export / verify / restore seed tables
│       ├── replace.py             # atomic replacement of old seed data
│       └── __main__.py            # CLI: python -m app.seed
├── data/                          # cleaned catalogue + cleaning audit files
└── tests/                         # unit tests + one opt-in live test
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

## Seed tables (schema `examinations`)

| Table | Key | Will later come from |
|---|---|---|
| `seed_modules` | `module_code` | Academic Data Service: module offerings |
| `seed_module_specializations` | `(module_code, specialization)` | Academic Data Service: programme structure |
| `seed_students` | `student_id` (synthetic, e.g. `IT30315600`) | Academic Data Service: students |
| `seed_registrations` | `(student_id, module_code)` | Academic Data Service: registrations |
| `seed_venues` | `venue_id` | Resource Service: venues |
| `seed_venue_blocks` | `block_id`; unique `(venue_id, block_date, start_time)` | Resource Service: availability |
| `seed_exam_periods` | `period_code` | Examination Service (stays), from the institutional calendar |
| `seed_exam_period_exclusions` | `(period_code, excluded_date)` | same as above |

Candidate counts per module are **never stored**. They are always counted
from `seed_registrations`. `seed_modules.specialization` is a legacy column
of the original SYN rows (NULL for new rows); it will be dropped once those
rows are removed.

**Replacing the seed tables later:** the scheduler will read input through
small source interfaces (for example `RegistrationSource` and `VenueSource`).
The first versions will read these `seed_*` tables; later versions will call
the Academic Data and Resource service APIs instead.

## Dataset (fixed random seed `20261019`)

| Item | Count |
|---|---|
| Modules: real Semester 2 modules for IT, SE, DS, CYBER | 42 |
| Module-specialization links (all programmes kept) | 91 |
| Students (synthetic IDs, 16 cohorts of 17-35) | 390 |
| Registrations | 1,700 (63 repeat) |
| Venues (auditorium, 2 exam halls, 2 lecture halls, 4 computer labs) | 9 |
| Venue blocks (22 simulated teaching, 5 maintenance) | 27 |
| Exam period: 2026-11-16 to 2026-11-28, weekdays 08:30-17:30, 1 excluded day | 9 exam days |

**Assumptions, not real policy** (all in [app/seed/assumptions.py](app/seed/assumptions.py)):
cohort sizes; 15% repeat students; 10 minutes when Extra_Reading_Time = Yes;
written exams use halls, computer-based exams use labs, hybrid exams may use
any venue unless restricted (`hybrid_venue_types`, `hybrid_lab_required_modules`);
exam seating 50% of normal capacity for auditorium/lecture halls, 100% for exam
halls, working machines for labs; weekends off; one simulated non-exam day.
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

Revisions: `0001` creates the seed tables; `0002` adds the real-catalogue support (non-destructive).

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

## Tests

```powershell
cd services/examination-service
python -m pytest            # unit tests; no database or credentials needed
python -m pytest --live     # also seeds twice inside a transaction, then ROLLS BACK
```
