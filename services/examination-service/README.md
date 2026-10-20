# Examination Service

Examination timetabling and cognitive load optimization for Schedence.

**Current phase: data, secured API, seating settings and optional offline workload optimization.** This service has a
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
| H10 | Each selected module has the fewest sittings its capacity allows (one when everyone fits; see "Multiple sessions"), at different non-overlapping times; each registered candidate gets exactly one room and logical seat in exactly one sitting |
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

## Offline Phase 2: estimated workload optimization

Phase 2 is **optional**. The existing `generate_timetable()` and CLI without
`--optimize` retain feasibility-only behavior. Both modes build the same H1-H11
constraints in `app/scheduling/model.py`; soft weights cannot relax them.
The configured minimum student break remains hard, including when it is zero.
There are no new APIs, database writes, workers, UI features or dependencies.

**Cognitive load here is an estimated workload score, not a scientifically
validated stress measurement, medical assessment or prediction of individual
wellbeing.** All defaults are approved prototype assumptions, not university
policy. Existing synthetic registrations and simulated blocks keep their labels.

From this service directory, with the existing `[solver]` extra installed:

```powershell
python -m app.scheduling --optimize
python -m app.scheduling --optimize --optimization-config data/optimization_policy.prototype.json --time-limit 60
python -m app.scheduling --optimize --minimum-break 90 --include-draft
python -m pytest tests/test_optimization_scoring.py tests/test_optimization_solver.py
```

The JSON configuration is read afresh on each run. It may contain a subset of
the settings shown in `data/optimization_policy.prototype.json`; omitted settings
use defaults. Unknown/duplicate keys, non-finite JSON constants, wrong types and
inconsistent settings are rejected with structured JSON errors. Settings use
integer minutes, days, coefficients and weights; booleans are not integers here.
The CLI never saves settings or results. A future Admin UI can serialize these
same fields through `OptimizationPolicy.from_mapping()` / `dataclasses.asdict()`;
authentication, settings endpoints and persistence are deliberately outside this
phase. Supplied settings are copied into an immutable policy for that run.

### Workload formula and normalization

For each module, estimated load points are:

```text
100 * (0.50 * difficulty/3 + 0.25 * credits/4
       + 0.25 * (duration_minutes + reading_time_minutes)/120)
```

Difficulty, credits and duration coefficients, their reference values, and
fixed-point precision are configurable. The coefficients are normalized by their
sum. Reference values stay fixed across datasets, rather than changing when a
module is added. Exact rational arithmetic is rounded half up once to
`load_scale` units per point (default 1000). Difficulty must be 1..5 and credits
must be positive signed 32-bit integers in optimization mode, matching the source
storage type; feasibility-only input rules are unchanged.

Each student is scored from their actual selected module registrations, including
repeats. Repeat status adds no multiplier. Unregistered students do not affect the
score. Identical registration patterns share model terms but retain their full
student multiplicity. For each student, each raw penalty is divided by a fixed
upper bound derived from those registrations and floored to `score_scale` units
(default 1000). A zero bound gives zero penalty. This rounding is part of the
versioned objective and can hide tiny improvements; increase precision if needed,
subject to safe integer bounds.

Reported `penalty_points` are the equal-student mean on a 0..100 scale.
`weighted_points = weight * penalty_points / 100`; their sum is
`total_weighted_penalty`. Default weights sum to 100; other non-negative integer
weights are supported, and zero disables that objective's contribution. Exact
integer `objective_units`, not rounded display values, determine improvement.
Participating students with one exam contribute zero penalties and remain in
the equal-student mean.

### Five soft constraints

Preparation gap `g` is measured in minutes from the earlier exam's complete end
to the later start, including reading time. Weekends and excluded dates count as
elapsed preparation time; this does not claim students have all that time free.

| Rule | Raw penalty and default settings |
| --- | --- |
| S1, weight 25 | For pairs with both difficulties >=4: `(4320 - clamp(g, 2880, 4320)) / 1440` |
| S2, weight 20 | For all registered pairs: `(2880 - clamp(g, 720, 2880)) / 2160` |
| S3, weight 30 | Maximum of the same-calendar-day indicator and `max(0, 720-g)/720`; never add both |
| S4, weight 15 | Peak count-adjusted demanding-exam concentration in a three-calendar-day window, as defined below |
| S5, weight 10 | Peak excess above two exams in any three-calendar-day window |

S1 and S2 use different missing-gap bands. Their penalties saturate below their
lower thresholds: the same missing minute is not charged in both bands. S3 covers
short gaps and the distinct calendar-day preference. A close difficult pair can
still receive multiple *different* layers of penalty; this is intentional. Larger
gaps beyond the targets earn no extra reward. Pair scores are normalized by the
student's total unordered exam-pair count, including non-difficult pairs for S1.
Pairs with another exam between them still count. Consecutive gap metrics are
reported separately.

For S4, let `n` be the student's exam count and `T` the sum of their module load
units. Define `delta[m] = n * load[m] - T`. This subtracts the expected load for
the number of exams in a window. Subtract the unavoidable single-exam excess
`B = max(0, max(delta))`, then take:

```text
raw S4 = max(0, max_over_windows(sum(delta[m] in window) - B))
S4 bound = sum(max(0, delta[m])) - B
```

An isolated difficult exam is therefore not penalized, and equal-load exams have
zero S4 penalty. S4 balances the mix of demanding and lighter exams; S5 measures
exam count. S5's bound is `max(0, n - preferred_window_exams)`. Both use a peak,
so a cluster is not charged repeatedly through overlapping windows. Calendar
windows include partial windows at period edges and count empty/excluded dates;
large configured windows are reduced to distinct intersections with the period.
The objectives remain correlated, and their individual results are always shown.

### Solver orchestration, validation and comparison

Python callers can use:

```python
from app.scheduling import OptimizationPolicy, SolverSettings, optimize_timetable

result = optimize_timetable(snapshot, SolverSettings(time_limit_seconds=60),
                            OptimizationPolicy(), baseline=existing_valid_draft)
```

Omit `baseline` to generate one first. The default baseline search receives 10%
of the shared solver budget (`baseline_budget_percent` is configurable).
Optimization receives the remaining budget after actual baseline solver time.
A baseline `UNKNOWN` does not imply infeasibility; the objective search can still
find the first valid draft. If that happens, the report has no baseline comparison
and does not claim a measured improvement. A supplied baseline must pass hard
validation against the exact snapshot before use.

The optimizer hints baseline starts, bounds the objective by the baseline score,
and validates each returned candidate with the existing independent H1-H11
validator. `evaluation.py` independently recomputes the integer objective without
OR-Tools or importing objective/model helpers. Mismatches or hard violations
reject the candidate. A candidate must strictly improve the integer score to
replace a valid baseline; ties, timeouts and invalid candidates preserve it.

`status` describes the selected result. `optimization_status` describes the
optimization attempt. A valid fallback is `FEASIBLE` when search was `UNKNOWN`
or its candidate was invalid; it is never falsely marked infeasible or optimal.
If an optimum equal to the baseline is proved, the original baseline is kept and
the overall status may be `OPTIMAL`. Diagnostics/violations describe failed
attempts separately; the CLI independently checks selected-draft validity.

The limit is a CP-SAT search limit shared by both stages, not an end-to-end wall
clock guarantee. Model construction, validation and scoring add overhead, and
solver termination can slightly exceed its requested limit. Results report both
solver and total elapsed seconds, settings, solver version, objective bound and
relative gap where available. Single-worker settings improve repeatability;
wall-clock cutoffs and hardware load can still change results.

The data snapshot fingerprint is unchanged by optimization. Results additionally
record a configuration fingerprint and scoring version. The independent report
includes S1-S5 scores, same-day pairs/student-days, short-gap pairs, difficult-pair
counts, minimum/median/lower-decile consecutive gaps, peak window count and load,
worst-student penalty and the mean for students with any selected repeat exam.

Optimization minimizes an equal-student **mean**, not the worst student's score.
Some individual penalties, minimum gaps, or student outcomes can worsen while
the weighted total improves. Hard minimum breaks and other H rules still hold.
No clinical benefit, improvement of every metric, or proof of global optimality
under a finite time limit is promised. Model size grows with shared module pairs,
student registration patterns, available starts and calendar windows; larger
datasets may need longer limits. Extreme fixed-point configurations that exceed
safe integer bounds produce structured errors, retaining a valid baseline.

### Optional baseline-relative fairness protection

Fairness is **off by default**. Enabling it adds the approved prototype quality
guards below while retaining the existing total weighted objective and all
S1-S5 formulas. H1-H11 are unchanged. These optional guards are not institutional
scheduling rules or clinically validated stress limits. They protect estimated
workload scores relative to a particular baseline.

```powershell
python -m app.scheduling --optimize --fairness
python -m app.scheduling --optimize --fairness --fairness-config data/fairness_policy.prototype.json
python -m app.scheduling --optimize --no-fairness
python -m pytest tests/test_fairness.py
```

`--fairness` / `--no-fairness` override the file's `enabled` value. Without either
flag, the JSON file determines enablement, or the default is off. Both options
and `--fairness-config` require `--optimize`. The fairness file is separate from
the existing optimization settings and is read afresh on each run. It supports:

| Field | Default | Meaning |
| --- | --- | --- |
| `enabled` | false | Apply optional fairness protection |
| `max_individual_increase_points` | 5.0 | Maximum increase above each student's baseline normalized score |
| `protect_worst_student` | true | When enabled, also guard the population's worst score |
| `max_worst_increase_points` | 0.0 | Maximum increase above the baseline population maximum |
| `automatic_relaxation` | false | Only false is supported; true is rejected explicitly |

Point allowances may be fractional, finite numbers in 0..100. Boolean settings
require actual booleans. Unknown/duplicate fields and non-finite JSON numbers are
rejected. `FairnessPolicy.from_mapping()` and `dataclasses.asdict()` provide the
same immutable, serializable contract for future Admin settings; no UI, API or
persistence is added here.

For student `s`, let `U[s]` be their existing integer weighted penalty, `S` the
fixed-point score scale and `W` the sum of S1-S5 weights. Define:

```text
F[s] = 100 * U[s] / (S * W)       # 0..100 normalized workload points
F[s] = 0 when all weights are zero

F_new[s] <= F_baseline[s] + max_individual_increase_points
max(F_new) <= max(F_baseline) + max_worst_increase_points
             # second guard applies only when protect_worst_student is true
```

This is an absolute point increase, not a percentage of the student's baseline.
Normalizing by `W` keeps the guards equivalent when all weights are scaled
together. It does not change the original objective or reported weighted penalty.
With the default weight sum of 100, normalized student points equal their
existing weighted points. A student starting at 3.700 may reach at most 8.700
under the individual default. With a baseline worst of 28.120, the default worst
guard allows no selected score above 28.120. Different valid baselines produce
different limits.

The model applies limits to each registration pattern's **per-student** objective,
without multiplying the limit by the number of students sharing that pattern.
Every participating student, including repeat students, receives the same
protection. The solver converts decimal point allowances to integer upper bounds
with exact rational arithmetic and rounds downward, never loosening a limit.
`fairness.py` independently compares the scorer's student penalties using exact
rational arithmetic rather than trusting the model's integer cap conversion.
Input and scoring fingerprints must match before any comparison is accepted.

`optimize_timetable(..., baseline=draft, fairness=FairnessPolicy(enabled=True))`
requires a validated baseline. If none is supplied, the existing baseline stage
tries to obtain one within its configured budget share. If it returns `UNKNOWN`
without a draft, protected optimization returns `UNKNOWN` without a timetable;
it does not spend the remaining budget on unprotected optimization. Supply a
valid baseline, increase `baseline_budget_percent`, or increase the shared time
limit for a later run. No limits are automatically relaxed.

Every candidate must pass H1-H11, the independent objective comparison and the
independent fairness checks. It must also strictly improve the original total
weighted penalty. Otherwise the exact valid baseline is returned, including its
rooms and seats. Timeouts preserve the baseline. An unfair candidate or a model
consistency error is reported through `optimization_status` and violations while
the selected fallback remains valid. The baseline itself satisfies all these
non-negative increase allowances, so a valid baseline keeps the protected model
feasible; the model does not require strict improvement as a feasibility condition.

Both modes report `fairness_comparison`: normalized baseline/selected worst
scores, maximum individual increase, students improved/worsened/unchanged, and
limit exceedance counts. When off, limits are **not** applied and
`limits_satisfied` is null; exceedance counts are informational. When on, accepted
results have `limits_satisfied: true`. `fairness_policy` and a separate fairness
configuration fingerprint record the effective settings. Internal independent
score reports contain immutable per-student penalty records. CLI summary output
omits those records unless `--include-draft` is explicitly requested.

For a controlled OFF/ON comparison, load one immutable snapshot and generate one
baseline. Pass that same baseline and soft-scoring settings to both modes, using
the same seed, workers and remaining search budget. Count the common baseline
search time against each scenario's shared 60-second budget; do not use the OFF
result as the ON baseline. Compare total and S1-S5 penalties, normalized worst
score, maximum individual increase, changed-student counts and hard violations.
Fairness may reduce total improvement or leave the baseline unchanged within the
time limit. Finite-time outcomes can still vary. The default permits small
individual increases up to five points, and does not protect each separate S1-S5
component from worsening. Smaller allowances can be configured without changing
algorithm code. Setting the individual allowance to zero prevents all individual
score increases. These remain workload estimates, not clinical stress measures.

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
│       ├── 20261015_0005_configurable_exam_seating.py
│       ├── 20261018_0006_scheduling_settings.py
│       ├── 20261019_0007_exam_period_kind.py
│       ├── 20261019_0008_module_exam_eligibility.py
│       └── 20261020_0009_exam_cycle_modules.py
├── app/
│   ├── main.py                    # FastAPI app (create_app); uvicorn app.main:app
│   ├── database.py                # engine + read-only sessions; writable only for settings changes
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
│   ├── generation/                # POST /timetables/generate: reuse the scheduler, validate, not saved
│   ├── scheduling_settings/       # admin scheduling settings (versioned, per institution)
│   │   ├── store.py               # SchedulingSettingsStore + DatabaseSchedulingSettingsStore
│   │   ├── memory.py              # in-memory store (tests)
│   │   └── service.py             # validation, versions, dry run, history, logging
│   ├── db/
│   │   ├── base.py                # Base with MetaData(schema="examinations")
│   │   ├── seed_models.py         # development-only seed_* tables
│   │   ├── seating_models.py      # exam_seating_rules + exam_seating_rule_history
│   │   └── scheduling_settings_models.py  # exam_scheduling_settings + history
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

## Exam scheduling settings

Examination Admins configure the scheduler through
`GET`/`PUT /api/v1/settings/scheduling` (`examinations:read` / `examinations:manage`).
The settings are one JSON document with four sections, each checked by the
existing policy class:

| Section | Class | Settings |
|---|---|---|
| `scheduling` | `SchedulingPolicy` | `permitted_start_times` (`"HH:MM"`), `minimum_break_minutes`, `max_rooms_per_exam` (`null` = no limit, per session), `max_sessions_per_exam` (1-10, default 3; documents saved without it use 3), `venue_compatibility` (every exam type), `module_venue_overrides` |
| `solver` | `SolverSettings` | `time_limit_seconds`, `random_seed`, `num_search_workers` |
| `optimization` | `OptimizationPolicy` | S1-S5 weights, gap thresholds, workload coefficients, scales, `baseline_budget_percent` |
| `fairness` | `FairnessPolicy` | `enabled`, `max_individual_increase_points`, `protect_worst_student`, `max_worst_increase_points` (`automatic_relaxation` must stay `false`) |

* **H1-H11 are always enforced.** No setting can switch off student clash
  prevention, room capacity or room conflict checks; unknown keys are rejected
  with `422`, and every response lists the mandatory rules. A minimum break of
  `0` allows back-to-back exams, never overlaps.
* `PUT` needs the **complete** document (copy `settings` from `GET`, edit,
  send back), a `reason` and `expected_version`, so a missing key can never
  silently reset a setting. All problems are listed at once (`rule`, `message`).
* Until an admin saves settings, `GET` shows the built-in defaults as
  `version: 0` (`source: "defaults"`); the first save sends `expected_version: 0`.
  Same versioning, row lock, single transaction, `dry_run` and `changed: false`
  behaviour as the seating settings.
* **Ownership:** settings are stored per institution: the one in which the
  admin's `examinations:manage` permission is checked (see
  [Institutions](#institutions-multi-institution-migration-0012)). Each
  institution starts with the defaults and never sees another's settings.
* `data/scheduling_settings.prototype.json` is the default document.
* **Server-side limits** (in `app/scheduling/policy.py`, so they apply to the
  API, saved settings, the command line and library calls alike):

  | Setting | Limit |
  |---|---|
  | `solver.time_limit_seconds` | at most `3600` (one hour per solver run) |
  | `solver.num_search_workers` | at most `16` |
  | `scheduling.minimum_break_minutes` | at most `1440` (one day) |

  Larger values are rejected (`422` from the API, `MODEL_INVALID` from the
  scheduler). The limits bound resources; they never relax a hard rule.
* **Invalid saved settings.** A saved document can become invalid, for example
  when a limit is tightened by a code update. It is never replaced by the
  defaults: `GET` answers `status: "invalid"` with the saved version, the
  document as stored and its `problems` (`rule`, `message`); the scheduler
  refuses it (`503`, and `--settings-file` with an exported invalid response
  fails). An admin repairs it with a normal `PUT` (complete valid document,
  current `expected_version`, `examinations:manage`) plus `replace_invalid=true`.
  The flag is required only then, and refused (`409`) otherwise. The history row
  has action `repair` and keeps the invalid document exactly (`old_settings`,
  `old_format`).
* `changed_fields` in the `PUT` response is stored with the history row, so
  both always agree. The first save is compared with the built-in defaults.

Tables (created by 0006, which inserts **no** rows): `exam_scheduling_settings`
(one row per `institution_id`; `version`; `settings` JSONB) and
`exam_scheduling_settings_history` (action `create`/`update`/`repair`, version,
changed fields, old and new document with their formats, `changed_by`,
`changed_at`, `reason`).

**Scheduler:** `python -m app.scheduling --settings-file saved.json [--optimize]`
runs with saved settings: the `settings` document or the whole `GET` response.
It cannot be combined with `--starts`, `--minimum-break`, `--time-limit`,
`--seed`, `--workers`, `--max-rooms`, `--compatibility`, `--module-venues`,
`--optimization-config`, `--fairness` or `--fairness-config`. Without it, the
command-line defaults work as before. In code:
`await SchedulingSettingsService(store, institution_id).configuration()`.

### Applying 0006 (later, after review)

Not applied to Neon yet. Until it is, both settings routes answer `503`
("scheduling settings are not available"); every other route keeps working.
Each step needs approval:

```powershell
cd services/examination-service
python -m app.seed.backup export --pooled                 # 1. backup at 0005 (seed + seating tables)
alembic -x pooled=true upgrade 0005:0006 --sql            # 2. preview the SQL; changes nothing
alembic -x pooled=true upgrade 0006                       # 3. one transaction, two new empty tables
alembic -x pooled=true current                            # 4. expect 0006
```

The backup tool does not export the scheduling settings tables yet.

**Rollback:** `alembic -x pooled=true downgrade 0005` drops the two tables. If
settings were saved, it stops and changes nothing; export the history first,
then add `-x drop_scheduling_settings=true`.

## Examination period kind (MID / FINAL, migration 0007)

Every examination period has an `exam_kind`: `MID` or `FINAL` (database check
`ck_seed_exam_periods_exam_kind_valid`). Migration 0007 adds the column with the
default `FINAL`, so every existing period becomes a final examination period. No
row is deleted. Old backups without the column also load as `FINAL`.

The kind is shown by `GET /api/v1/exam-periods` and in the `inputs` of a
generated timetable, and an unknown kind is rejected before solving. It does
**not** change scheduling yet: MID and FINAL periods use the same hard rules
H1-H11, S1-S5, fairness and seating rules.

Not applied to Neon yet. Each step needs approval:

```powershell
cd services/examination-service
python -m app.seed.backup export --pooled                 # 1. backup at 0006
alembic -x pooled=true upgrade 0006:0007 --sql            # 2. preview the SQL; changes nothing
alembic -x pooled=true upgrade 0007                       # 3. one transaction: new column + check
alembic -x pooled=true current                            # 4. expect 0007
```

**Rollback:** `alembic -x pooled=true downgrade 0006` drops the check and the
column. If a MID period exists, it stops and changes nothing; remove it first or
add `-x drop_exam_kind=true`.

## Module examination eligibility and Block Mode (migration 0008)

New `seed_modules` fields, shown by `GET /api/v1/modules`:

| Field | Default | Meaning |
| --- | --- | --- |
| `has_final_exam` | `true` | the module has a FINAL examination (`duration_minutes`) |
| `has_mid_exam` | `false` | the module has a MID examination |
| `mid_duration_minutes` | `null` | MID duration; required exactly when `has_mid_exam` |
| `delivery_mode` | `regular` | `regular` or `block` (Block Mode) |
| `block_category` | `null` | `pro_rata` or `full_module`; required exactly for block modules |

The defaults keep every existing module as before: FINAL only, regular. The
catalogue has no MID, Block Mode or pro-rata data, so **no real module is marked**.
`SeedAssumptions.synthetic_*` can mark modules for tests only (empty by default).

Scheduling: a MID period schedules only modules with a MID examination, using
`mid_duration_minutes`; a FINAL period only modules with a FINAL examination.
A selected module without an examination of the period's kind is rejected (422).
Block Mode is stored and shown only; it does not change scheduling yet. Reading
time is the same for MID and FINAL. H1-H11, S1-S5, fairness and seating are unchanged.

Not applied to Neon yet. Preview with `alembic -x pooled=true upgrade 0007:0008 --sql`.
**Rollback:** `downgrade 0007` stops if any module has MID, no-FINAL or Block Mode
settings, unless `-x drop_module_exam_settings=true` is added.

## Examination cycles (migration 0009)

An examination period is an **examination cycle**. `seed_exam_period_modules`
lists the modules examined in it, and who sits them (`candidate_scope`):

* `all`: every registration of the module (normal and repeat);
* `repeat_only`: only repeat registrations (a module re-examined for repeat
  candidates, e.g. a semester 1 module in a semester 2 cycle).

Rules (checked by seed validation and before solving, with clear 422 messages):
a mapped module needs an examination of the cycle's kind (MID/FINAL); a regular
module of another semester may only be `repeat_only` (Block Mode modules are not
tied to a semester); every mapped module needs candidates. The mapping is empty
by default: **no module is mapped until the cycle is configured**, and full-cycle
generation never takes the whole catalogue or every module of a semester.
`GET /api/v1/exam-periods/{period_code}/modules` shows the mapping.

Registrations of modules that are not in the cycle's mapping are **never added
automatically**. The generation response lists them as *potentially excluded*
(`excluded_registrations`: totals, normal/repeat counts per module, and the other
cycles that map the module; counts only, no student IDs) and adds a warning
notice. Seed validation warns about repeat registrations whose module is in no
cycle mapping.

SYNTHETIC test mapping (never official): `python -m app.seed --with-synthetic-cycle`
maps every synthetic module of the period's semester (`all`) and adds three real
catalogue modules of the other semester with synthetic repeat registrations
(`repeat_only`). It only inserts; reruns change nothing; existing rows are kept.

Not applied to Neon yet. **Rollback:** `downgrade 0008` stops while mappings exist,
unless `-x drop_exam_cycle_modules=true` is added. After 0009, `python -m app.seed`
needs the new table (run `alembic upgrade head` first).

## Multiple sessions (capacity-based)

At each permitted start, one sitting of a module can seat at most the effective
exam capacities of its compatible venues that **no venue block overlaps for the
whole exam** (duration plus reading time), using the configurable seating
percentage (default 50%, with per-venue overrides) and at most
`max_rooms_per_exam` rooms. Before solving, each module gets the **fewest**
sessions that could seat every candidate (`app/scheduling/sessions.py`):

* some start can seat everyone: **one session**, modelled exactly as before (no
  extra sessions);
* otherwise the fewest non-overlapping starts whose free seats together can seat
  everyone (an exact search), at most `max_sessions_per_exam` (default 3,
  configurable); more is refused before solving with a clear H10 message (422).

In the solver, the N sessions of a module are at different permitted times, in
order and never overlapping; each has its own rooms (several rooms per session
are allowed). Each candidate is assigned to exactly one session and one seat, and
their chosen session must not clash (including the minimum break) with any of
their other exams: normal, repeat or Block Mode. Venue blocks, capacity, duration
and reading time apply to every session. S1-S5 and fairness score each student on
the session they actually sit; the independent validator and evaluator read that
from the seats. The validator recomputes the required session count itself.

Session IDs: `PERIOD:MODULE` for one session (unchanged), `PERIOD:MODULE:S01`,
`:S02`, ... when split. The generation API returns `session_number`,
`session_count` and `session_label` (`"Session 01"`) per session, and
`summary.multi_session_modules`. Offline CLI: `--max-sessions`.

The count looks at each module on its own: rooms taken by other exams are left to
the solver, which reports INFEASIBLE if the sessions cannot all be placed. The
independent validator recomputes the count from the calendar and blocks itself.

## Timetable generation API (not saved yet)

`POST /api/v1/timetables/generate` (`examinations:generate`, only `EXAMINATION_ADMIN`)
runs the existing scheduler on database inputs and returns a validated
timetable. Nothing is saved or published. It reuses `load_database_snapshot`
(one `REPEATABLE READ`, `READ ONLY` transaction), the active scheduling
settings, `generate_timetable` / `optimize_timetable` and `validate_timetable`;
it adds no scheduling logic.

```json
POST /api/v1/timetables/generate
{
  "period_code": "PP1-2026-S2-FINAL",
  "mode": "feasibility",
  "time_limit_seconds": 30
}
```

* `module_codes` is **optional** (1-200, unique). Omitted (or `null`): the full
  examination cycle, i.e. every module in the period's cycle mapping, with only
  the cycle's registrations (`inputs.selection = "full_cycle"`). Listed: only
  those modules, which must all be in the cycle (`partial_cycle`). A period
  without a mapping refuses a full-cycle run (422) and accepts listed modules of
  its own semester only (`listed_modules`).
* `mode`: `feasibility` (H1-H11) or `optimized` (H1-H11, S1-S5 and the saved
  fairness settings). `time_limit_seconds` may only lower the saved limit.
* Settings: the active saved settings (or the defaults, version 0). Invalid
  saved settings or missing settings tables (0006 not applied) answer `503`;
  nothing is scheduled with them.
* Response: `status`, `generated` (true only with a validated timetable),
  `persisted: false`, `solver` (mode, limit, workers, seed, wall time,
  version), `inputs` (period, counts, input/capacity/settings fingerprints and
  `settings_version`), `validation` (`passed`, `rules_checked` H1-H11,
  `violations`), `summary`, `sessions` (date, start/end time, module, rooms with
  seat counts and exam capacity), `workload` (S1-S5 scores), `optimization`
  (baseline vs selected, gap, fairness; `optimized` only), `diagnostics`,
  `notices`. No per-student data.
* Answers: solver outcomes `200` (`OPTIMAL`, `FEASIBLE`; `INFEASIBLE`, `UNKNOWN`,
  `MODEL_INVALID` with `generated: false` and diagnostics), request or input
  problems `422` (`rule`, `message`), another generation running `429`
  (`Retry-After`), settings/database/solver unavailable `503`.
* Resources: all reads finish before solving; the solver runs in a worker
  thread (the server keeps answering); one generation at a time per process;
  at most 300 s per API run, 200 modules, 20,000 registrations, 500 venues,
  plus the solver limits (16 workers, one-day minimum break). A timetable that
  fails independent validation is never returned (`MODEL_INVALID`).
* Requires the `[solver]` extra (`ortools`) on the API server; without it `503`.

### Per-timetable scheduling preferences

Both `/api/v1/timetables/generate` and `/api/v1/timetables/drafts` accept an optional
`scheduling_preferences` object. For example:

```json
{
  "period_code": "PP1-2026-S2-FINAL",
  "mode": "optimized",
  "scheduling_preferences": {
    "scheduling": {"permitted_start_times": ["09:00", "13:30"], "minimum_break_minutes": 90, "max_rooms_per_exam": 4, "max_sessions_per_exam": 3},
    "solver": {"time_limit_seconds": 120, "num_search_workers": 1, "random_seed": 17},
    "optimization": {"s1_weight": 40, "preparation_target_minutes": 3000},
    "fairness": {"enabled": true, "max_individual_increase_points": 5},
    "seating": {"default_percentage": 60}
  }
}
```

Only the supported scheduling controls, solver controls, S1-S5 weights, workload
thresholds/reference values, baseline budget and fairness limits may be overridden.
Unknown fields, venue compatibility/module exceptions, precision scales and
automatic fairness relaxation are rejected. Cross-field validation uses the actual
institution defaults before loading solver inputs. API time limits remain at most
300 seconds and search workers at most 16. A separate `time_limit_seconds` may only
lower the effective timetable limit. H1-H11 remain mandatory.

Omitting preferences (or sending null) retains the legacy active-default behavior.
Sending `{}` opts into a frozen copy of the current scheduling defaults and default
seating percentage. Venue-specific seating rules remain authoritative; no settings
PUT or database write is made during generation. Preferences and `calendar` are
independent and can be used together.

For opted-in timetables, `inputs.scheduling_preferences` records the complete
effective settings, default seating and venue overrides, original capacity identity,
effective capacity identity, source seating ruleset version and a versioned SHA-256
fingerprint. Save Draft stores this in the existing immutable `inputs` JSONB column;
no migration or trigger change is required. `settings_version` records the source
defaults version, while `settings_fingerprint` identifies the effective configuration.

Approval/publication reconstruct fresh source inputs with the stored configuration
and selected calendar. Module, registration, venue, block, source seating/capacity,
content-integrity and revision checks still run. Changes to another timetable's
preferences never change defaults or stale this timetable. Regeneration preserves
the stored scheduling preferences, default seating and calendar while refreshing
source inputs and venue-specific rules. Legacy snapshots without preferences keep
their existing checks. Corrupt or unsupported snapshots fail closed.

## Saved draft timetables (migration 0010)

`POST /api/v1/timetables/drafts` takes the same body as `/generate`, generates,
validates the result again (H1-H11) and saves it as a new `DRAFT` (`201`).
Only `OPTIMAL`/`FEASIBLE` and validated timetables are saved; otherwise `422`
and nothing is stored. `/generate` is unchanged and still saves nothing.

* Tables: `exam_timetables` (cycle, MID/FINAL, `draft_number`, status, solver
  status and run, input fingerprint, settings version, seating rule version,
  validation, summaries), `exam_timetable_sessions`, `exam_timetable_rooms`,
  `exam_timetable_seats` (every candidate's logical seat).
* Every save gets the next `draft_number` of its institution and cycle (an
  advisory lock serialises saves); older drafts are never overwritten.
* Names are copied, not foreign keys to the temporary seed tables.
* Statuses `APPROVED`/`PUBLISHED`/`ARCHIVED`, `approved_*`/`published_*`
  columns, `revision`, and at most one `PUBLISHED` timetable per cycle are used
  by the workflow below (migration 0011).
* Reads (`examinations:read`): `GET /api/v1/timetables` (newest first; filters
  `period_code`, `exam_kind`, `status`; no sessions, no student IDs) and
  `GET /api/v1/timetables/{timetable_id}` (sessions, venues, seat counts).
* Seats (`examinations:generate` or `examinations:approve`):
  `GET /api/v1/timetables/{timetable_id}/seats` (paged; `session_id`).
* Every query is limited to the request's institution; another institution's
  timetable is `404`. Downgrade refuses to drop saved timetables unless
  `-x drop_saved_timetables=true`.

## Approval and publishing workflow (migration 0011)

DRAFT -> APPROVED -> PUBLISHED (-> ARCHIVED when replaced). Approve and publish
need `examinations:approve`; body `expected_revision`, `reason` (5-500 chars);
publish also takes `replace_published`.

* `POST /api/v1/timetables/{id}/approve`: DRAFT only. `POST .../publish`:
  APPROVED only (a DRAFT is refused). Wrong status, revision, or an already
  published cycle: `409` with `detail[0].rule` = `STATUS`, `REVISION`,
  `ALREADY_PUBLISHED`, `INTEGRITY` or `STALE`.
* Before each change, with the row locked: the stored sessions, rooms and seats
  must match `content_fingerprint` (taken at save); the active settings, seating
  capacities and a fresh input snapshot must equal those used at generation;
  and H1-H11 must pass on the current inputs. Otherwise `409 STALE` lists
  `STALE_SETTINGS`, `STALE_CAPACITIES`, `STALE_INPUTS` and H-rule problems, and
  nothing changes. Timetables saved before 0011 have no content fingerprint and
  must be regenerated.
* One published timetable per cycle: publishing another needs
  `replace_published=true`, which archives the old one in the same transaction.
* `POST /api/v1/timetables/{id}/regenerate` (`examinations:generate`): a new
  DRAFT with the same cycle, modules and mode (`mode`, `time_limit_seconds`
  optional), from the current inputs; `source_timetable_id` links it. The old
  timetable is kept unchanged.
* `GET /api/v1/timetables/{id}/history` (`examinations:read`): create, approve,
  publish and archive rows with who, when, why and what was checked.
* Database triggers enforce the rules too: content columns never change; only
  the transitions above, each with revision + 1; approval and publication
  details are fixed once set; sessions, rooms and seats are never updated and
  are locked once a timetable leaves DRAFT; only drafts may be deleted;
  history rows are never updated.
* No notifications are sent yet. Downgrade refuses to drop workflow history
  unless `-x drop_timetable_workflow=true`.

## Institutions (multi-institution, migration 0012)

The service has **no institution setting**. A System Admin creates institutions
in the Identity Service at any time, and each request's institution comes from
the caller:

1. Identity returns the caller's permissions per institution
   (`/me/permissions`).
2. The route's permission check (`app/auth/dependencies.py`) keeps the
   institutions where the caller holds the needed `examinations:` permission:
   * exactly one: that institution is used;
   * several: the caller must send `X-Institution-Id: <uuid>`, else `400`
     (the service never picks one);
   * none: `403`.
   `X-Institution-Id` is only a choice among the caller's own institutions:
   naming one where the caller lacks the permission gives `403`, and a value
   that is not a UUID gives `400`. The web app sends only the bearer token,
   which is enough for an admin of one institution.
3. Saved timetables (and their seats and history) and scheduling settings are
   stored per institution and read and written only in that institution.
   Another institution's timetable gives `404`, as an unknown one does.

**The imported dataset.** Modules (with their exam details), students,
registrations, venues, venue blocks, exam periods and seating rules are one
imported dataset with global keys (`module_code`, `period_code`, `venue_id`).
`examinations.exam_dataset_owner` (one row) names the one institution allowed
to read and change it, and to run the solver on it (generate, save a draft,
regenerate, approve, publish). Every other institution gets `403` ("This
institution has no examination dataset in this service") on those routes, and
so does everyone while the table is empty. Giving each institution its own
dataset would need institution keys on every seed table; that is not done yet.

**Existing SLIIT data.** Rows saved before 0012 carry the old
`SLIIT_INSTITUTION_ID` in `institution_id` (`exam_timetables`,
`exam_timetable_history`, `exam_scheduling_settings`,
`exam_scheduling_settings_history`). They are not changed. They stay readable by
that institution's Examination Admins when the Identity Service's institution ID
for SLIIT is the same UUID, which is what `SLIIT_INSTITUTION_ID` held. Migration
0012 makes that institution the dataset owner:

| Existing rows | Migration 0012 |
|---|---|
| one institution ID (the old `SLIIT_INSTITUTION_ID`) | it becomes the dataset owner |
| none (new database) | table left empty; run `python -m app.dataset_owner --institution-id <uuid>` |
| several | stops; run `alembic -x dataset_owner=<uuid> upgrade 0012` |

`python -m app.dataset_owner --institution-id <uuid> [--dry-run] [--replace]`
assigns the owner later. It never replaces an existing owner without
`--replace`. If the SLIIT institution was created in Identity with a new ID,
the old rows are not mapped automatically: they would need a reviewed data
update of the four tables above, plus `--replace`.

`SLIIT_INSTITUTION_ID` and `EXAMINATION_INSTITUTION_ID` are no longer read; an
old `.env` that still has them is fine.

### Applying 0012 (after review)

0012 only creates `exam_dataset_owner` and inserts at most one row; it changes
no existing row or table. On the shared Neon database, check first:

```powershell
cd services/examination-service
alembic upgrade 0011:0012 --sql   # review the SQL; nothing runs
alembic upgrade 0012
```

Downgrade (`alembic downgrade 0011`) drops the table. Without it, no
institution can read the dataset.

## API

Start the server from `services/examination-service` (Windows needs the
selector event loop for psycopg async):

```powershell
cd services/examination-service
uvicorn app.main:create_app --factory --port 8003 --loop asyncio:SelectorEventLoop
```

Interactive docs: <http://127.0.0.1:8003/docs> (hidden when `APP_ENV=production`).
The API uses the pooled `DATABASE_URL`, one engine per process, and opens every
transaction `READ ONLY`. The only exceptions: saved seating changes (`PUT`/`DELETE`
under `/api/v1/settings/exam-seating`) and saved scheduling settings
(`PUT /api/v1/settings/scheduling`), without `dry_run`, get a writable
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
   `examinations:read` in the request's institution: the caller's only
   institution with that permission, or the one named in `X-Institution-Id`
   (see [Institutions](#institutions-multi-institution-migration-0012)).
   Dataset routes also need that institution to own the dataset.
4. Seating and scheduling-settings changes (`PUT`/`DELETE /api/v1/settings/exam-seating/...`,
   `PUT /api/v1/settings/scheduling`), including
   dry runs, require `examinations:manage` in that institution. They do not also
   require `examinations:read`. Writes always ask Identity again (no cache).
5. `POST /api/v1/timetables/generate` requires `examinations:generate` in that
   institution (not also `read` or `manage`), and the dataset owner.

Only `EXAMINATION_ADMIN` receives these permissions in the Identity catalog.
Its existing `examinations:generate`, `examinations:approve`,
`academic:staff:read`, `academic:workload:read` and `venues:read` grants remain.
Other roles, including `SUPER_ADMIN`, have no examination override. Supporting
permissions alone do not grant access to this service. Student self-service
stays off until a secure user -> student ID mapping exists.

| Examination permission | Purpose |
|---|---|
| `examinations:read` | Current data/settings GET routes; future timetable views, report views, comparisons and exports |
| `examinations:manage` | Seating and scheduling-settings changes; future period, daily hours and draft edits |
| `examinations:generate` | `POST /api/v1/timetables/generate` (not saved) and `POST /api/v1/timetables/drafts` (saved as a draft); seat allocations of saved timetables |
| `examinations:approve` | Approve and publish saved timetables; their seat allocations |

Generation and approval alone do not grant reads or settings changes, and
generation does not need them.

**Rollout:** deploy these checks only after a separately approved Identity RBAC
data update has added `examinations:read` and `examinations:manage` and granted
them to `EXAMINATION_ADMIN`. The code change does not update Neon. Existing
admins without the new grants will receive `403` until that step is complete.
No database schema migration or Cognito change is needed.

`/health` and `/health/db` are public. Errors: `400` (permission in several
institutions without `X-Institution-Id`, or a malformed one), `401` (no or
invalid token), `403` (no permission, disabled or unknown account, or the
institution does not own the dataset), `503` (Identity Service unavailable).
Settings (repository-root `.env`): `AWS_REGION`, `COGNITO_USER_POOL_ID`,
`COGNITO_APP_CLIENT_ID`, optional `IDENTITY_SERVICE_URL`
(default `http://localhost:8001`) and `FRONTEND_URL` (CORS). No institution
variable is needed.

To call the API locally, run the Identity Service too
(`python scripts
un_services.py --only identity` from the repository root),
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
| `GET /api/v1/exam-periods` | periods with `exam_kind` (`MID`/`FINAL`), exclusions and exam days | |
| `GET /api/v1/exam-periods/{period_code}` | one period | |
| `GET /api/v1/exam-periods/{period_code}/modules` | the examination cycle mapping (`module_code`, `candidate_scope`) | |
| `GET /api/v1/settings/exam-seating` | default rule, overrides, totals, unavailable venues, recent history | `history_limit` (0-500, default 20) |
| `PUT /api/v1/settings/exam-seating/default` | change the default (`examinations:manage`) | body `percentage`, `reason`, `expected_version`; `dry_run` |
| `PUT /api/v1/settings/exam-seating/venues/{venue_id}` | create (`expected_version: null`) or change an override | body as above; `dry_run` |
| `DELETE /api/v1/settings/exam-seating/venues/{venue_id}` | remove an override | `expected_version`, `reason`, `dry_run` |
| `GET /api/v1/settings/scheduling` | scheduling settings (defaults until saved), `status` (`valid`/`invalid`) and `problems`, mandatory rules H1-H11, recent history | `history_limit` (0-500, default 20) |
| `POST /api/v1/timetables/generate` | generate and validate a timetable, not saved (`examinations:generate`) | body `period_code`, `module_codes` (required), `mode`, `time_limit_seconds` |
| `POST /api/v1/timetables/drafts` | generate, validate and save as a new draft (`examinations:generate`) | body as `/generate` |
| `GET /api/v1/timetables` | saved timetables, newest first, no student IDs (`examinations:read`) | `period_code`, `exam_kind`, `status`, `limit`, `offset` |
| `GET /api/v1/timetables/{timetable_id}` | one saved timetable with sessions and venues (`examinations:read`) | |
| `GET /api/v1/timetables/{timetable_id}/seats` | seat allocations with student IDs (`examinations:generate` or `examinations:approve`) | `session_id`, `limit`, `offset` |
| `POST /api/v1/timetables/{timetable_id}/approve` | DRAFT -> APPROVED after integrity, stale-input and H1-H11 checks (`examinations:approve`) | body `expected_revision`, `reason` |
| `POST /api/v1/timetables/{timetable_id}/publish` | APPROVED -> PUBLISHED, same checks (`examinations:approve`) | body `expected_revision`, `reason`, `replace_published` |
| `POST /api/v1/timetables/{timetable_id}/regenerate` | new DRAFT from the same request (`examinations:generate`) | body `mode`, `time_limit_seconds` (optional) |
| `GET /api/v1/timetables/{timetable_id}/history` | status history (`examinations:read`) | |
| `PUT /api/v1/settings/scheduling` | replace the settings (`examinations:manage`) | body `settings` (complete document), `reason`, `expected_version` (0 = first save); `dry_run`; `replace_invalid` (only to repair invalid saved settings) |

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
python -m pytest -m "not slow"   # quick run: skips the long full-dataset solver tests (~3 min)
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

### Optional preparation-gap evaluation (Generate only)

`POST /api/v1/timetables/generate` accepts `include_analysis: true` (default `false`).
The optional `analysis` response is computed in a worker from the frozen input snapshot and actual
assigned sittings, with the independent H1–H11 validator run again. It never changes assignments,
institution defaults or stored timetables. Draft saving, approval, publication and regeneration keep
their existing contracts; analysis is not persisted. No migration is required.

Baseline and selected summaries share input/effective-configuration fingerprints and include the
existing independent S1–S5 scores. The baseline is the optimizer's real feasibility solution; a Basic
run has no comparison baseline. Module difficulty and applied thresholds come from the frozen inputs.
The mean is the pooled sum of consecutive end-to-next-start gaps divided by their count. Median and
lower decile use that same population (lower-decile index `floor((count - 1) / 10)`). Single-exam
students contribute no gaps. All-pair short/difficult counts include nonconsecutive exams and are
student-pair counts, not distinct module-pair counts. End times include reserved reading time.

Cohorts use actual registered student year/specialization membership. Normal-only students have no
repeat registration; the disjoint repeat group includes students with any repeat registration and
considers all their scheduled exams. Only aggregate cohort statistics and shared-candidate counts
are returned; no student identifiers, names, seats or individual schedules are exposed.

Additional analysis is skipped above 100,000 student-exam pairs per timetable, with an explicit
unavailable reason. Responses return at most 128 cohort summaries and 100 difficult session-pair
groups per timetable (closest gaps, below-target pairs first). Truncation flags and total counts are
explicit; summaries remain exact within the analysis limit. Pair-detail tables are full-cycle data,
not cohort-filtered. Unsupported/missing cohorts are unavailable, not inferred from module mappings.
Soft targets are preferences, not guarantees or validated measurements of stress.

Focused local checks (no database): `python -m pytest -q tests/test_preparation_analysis.py`.
