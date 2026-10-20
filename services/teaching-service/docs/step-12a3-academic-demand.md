# Step 12A.3: confirmed academic sources and continuous lecture demand

The user confirmed the existing supplied university academic records as
university-sourced information. This supersedes earlier source-verification
cautions. Staff IDs, names, departments, rank text, module metadata, preferences
and all teaching-team entries are retained. Original workbook status labels are
traceability data, not reasons to request verification again.

`WideImportConfig.source_confirmation` records the user's confirmation reference
and SHA-256 of each academic source workbook. The importer checks all three exact
files; a mismatch is an error rather than a silently inherited confirmation.
The local `department-aligned.mapping.json` now includes these hashes and the
explicit `ONE_CONTINUOUS_WEEKLY_LECTURE` interpretation. No source workbook was
edited. Generic imports without this metadata retain their existing behavior.
Preference candidates inherit confirmed source status without acquiring invented
week/stream scope, numerical day-part boundaries, penalty weights or hard blocks.

## Actual extraction

The unchanged Department_Aligned module workbook has one worksheet,
`Modules_183`, with 184 module rows despite its historical name. Reuse the existing
wide importer for module fields and staff links. Contact-hour columns are exactly:

| Source column | Retained session type | Interpretation |
| --- | --- | --- |
| `Lecture_Hours_Week` | LECTURE | 2 = one continuous 120-minute lecture; 1 = one 60-minute lecture; 0 = no lecture |
| `Tutorial_Hours_Week` | TUTORIAL | Retain weekly minutes; explicit duration/frequency required |
| `Workshop_Hours_Week` | WORKSHOP | Retain weekly minutes; explicit duration/frequency required |
| `Lab_Hours_Week` | LAB | Retain weekly minutes; explicit duration/frequency required |

There are **171 two-hour lecture rows, nine one-hour rows and four zero-hour
rows**. Positive non-lecture hours occur on 70 tutorial, 21 workshop and 106 lab
rows. Blank, formula, invalid or unsupported contact values are reported, never
treated as zero or replaced by a synthetic pattern. The derived total-contact
formula is preserved as source metadata and is not evaluated as scheduling input.

The catalog preserves original study year, semester, degree type and specialization
text. Comma-separated applicability lists expose their exact labels without
aliases, reordering the original text or merging modules. The eight labels are
COMPUTER_SCIENCE, CSNE, CYBER, DS, IM, ISE, IT and SE. Both distinct IT2100 module
rows and their teams remain present. There are 63 year/semester/specialization
scopes and 596 positive specialization/session-type draft rows. These drafts
are **not** 596 scheduled sessions: shared source rows appear under multiple
applicable specializations, and actual group demand is still required.

Source locations are diagnostic identities only. Offering IDs and stream values
remain blank in drafts. The report never invents institution or offering UUIDs.

## Reusable demand preparation

`pilot.academic.extract_academic_catalog()` creates source-bound module contact
records. `catalog.select(academic_year=..., semester=..., specialization=...)`
selects the actual applicable modules.

`build_specialization_teaching_input()` reuses existing ModuleOffering,
StudentGroup, SessionRequirement, TeachingTeamMember and TeachingTimetableInput.
It accepts externally supplied `OfferingSourceBinding` records, an exact
group-to-specialization map and explicit audiences by session type. Offerings can
retain a shared source specialization list or carry an explicitly supplied
single-specialization scope from that list. One shared offering ID is sufficient
for a shared row; the helper never manufactures separate IDs per specialization.
Readiness accepts group membership in the supplied offering applicability list.
This does not permit cross-specialization combination: existing combination
policy and shared-content/eligibility/population checks remain unchanged. The
current PP1 run disables shared lecture combining entirely.

For a supplied offering `SYN-O1` and group `SYN-G1` matching a two-hour lecture
row, the generated requirement is:

```json
{
  "requirement_id": "demand:SYN-O1:SYN-G1:LECTURE",
  "offering_id": "SYN-O1",
  "group_id": "SYN-G1",
  "session_type": "LECTURE",
  "duration_minutes": 120,
  "sessions_per_week": 1
}
```

The existing occurrence expander produces one `:1` occurrence. Requirement IDs
are deterministic planning identities, not authoritative database identifiers.
All source team entries are copied unchanged to the supplied offering. No
lecturer is selected during preparation.

Tutorial, workshop and lab patterns require explicit `NonLecturePattern`
contracts with a source reference. Duration times frequency must equal the
retained weekly contact minutes. A caller may explicitly select main groups for
lectures and child groups for labs; the helper does not infer this from size.
Including a parent and descendant in the same session audience is rejected to
prevent duplicate demand for that population. It also rejects duplicate source
scope bindings, changed academic fields, missing eligibility and incompatible
group periods/streams. Original groups and inputs remain unchanged.

## One global solve, then views

The generator returns **one** TeachingTimetableInput across all supplied
specializations. Existing integration/preparation and the existing global CP-SAT
model schedule that input together. Staff, venue and student conflicts continue
to be checked across streams and specializations. No engine constraints,
objectives or lexicographic stages changed. The staff input checker has a
default-false PP1-only unclassified-profile option described below.

Validated preview exports add specialization, lecturer, group and venue sections
to `timetable.html`, plus a `views.json` index referencing the same delivery IDs
as the CSV and solver result. A combined delivery remains one physical booking;
child group views inherit attendance at their ancestor's lecture. Views do not
reschedule, add workload or duplicate global bookings. Every export passes the
existing full independent validation gateway before any output is written.

## Current readiness and genuine remaining inputs

The original student workbook supplies all-specialization totals: 6,720 across
16 scopes. The subsequently supplied
`Schedence_Specialization_Student_Counts_Proportional_Editable.xlsx` supplies an
approved proportional breakdown for PP1 testing. This supersedes the earlier
specialization-allocation gap; it does not claim exact specialization enrollment.
The PP1 importer derives the group mapping automatically and reconciles every
cohort against the original workbook. It generates 140 main groups and 198 lab
subgroups. Parent and child counts are not added as separate populations.

The user clarified that there is no separate specialization-start policy file.
Use recorded module year/semester/specialization applicability directly. No
starting-year restriction is inferred or requested. The optional earlier
SpecializationStart contract remains backward compatible but is not a blocker.

PP1 uses local source-row/stream offering references, not invented database IDs.
Actual-campus publication/persistence remains separate and requires real
institution/offering identities, actual dated applicability/availability and
population evidence. No further academic source verification is requested.

## Commands and outputs

From the repository root using the existing virtual environment:

```powershell
.\.venv\Scripts\python.exe -B -m schedence_teaching.pilot.academic_cli --output services/teaching-service/local-data/pilot-previews/new-academic-report
.\.venv\Scripts\python.exe -B services/teaching-service/examples/authoritative-data-audit.py
.\.venv\Scripts\python.exe -B -m schedence_teaching.pilot --mode DEMO --time-limit 15 --output services/teaching-service/local-data/pilot-previews/new-synthetic-regression
.\.venv\Scripts\python.exe -B -m pytest services/teaching-service/tests -q
.\.venv\Scripts\python.exe -B -m pip check
```

The actual extraction is saved in ignored
`local-data/pilot-previews/step12a3-academic-inputs/`: `module-catalog.csv`,
`specialization-demand-drafts.csv`, and `academic-readiness.json`. Source extraction
can succeed while correctly reporting incomplete scheduling inputs.

The explicit historical pilot DEMO remains synthetic: its specialization
partition, calendars, lab pattern and other stated assumptions are fictional.
It now respects supplied positive lecture hours. Its new regression output is
under ignored `step12a3-synthetic-regression/` and is labelled SYNTHETIC_DEMO,
never a real multi-specialization timetable. It returned independently validated
FEASIBLE with 18 original occurrences, 16 physical deliveries and two combined
lectures. Source datasets, accepted assignments, database and solver code remain
unchanged.

## Approved proportional workbook integration and PP1 result

`pilot.allocations` reads `STUDENT_ALLOCATIONS`: caps B7/E7/H7, specialization
codes/names/weights A10:C17, original cohort references A21:D36, and the allocation
table A42:G169 below its row-41 headers. No source cells or workbooks are saved.
The importer recognizes only the inspected cumulative ROUND allocation formulas,
recomputes them using Decimal ROUND_HALF_UP, and checks any cached results.
Arbitrary formulas, stale caches, duplicate/scope mismatches, invalid weights,
and totals differing from the original workbook are rejected with source locations.
Literal allocation overrides are preserved if valid and reconciled. Empty formula
caches do not require inventing values: only the explicitly recognized formula
with its saved literal inputs is evaluated. Derived group-output cells are not
imported as independent academic records.

All **128 allocations reconcile across 16 cohorts**, preserving **6,720**.
The approved estimates produce **338 group records: 140 main and 198 lab**,
with maximum generated sizes 106 and 57 respectively (configured caps 120/60).
Both semesters, all four study years, both streams and all eight specializations
are included concurrently. All 184 source module rows and 1,288 team entries
are preserved; stream-scoped local references produce 368 offerings and 2,576
mapped candidate-team entries. There are **1,461** weekly occurrences:

| Type | Occurrences |
| --- | ---: |
| Lecture | 616 |
| Tutorial | 258 |
| Workshop | 95 |
| Lab | 492 |

`examples/pp1-test-calendar.json` is editable configuration for the user's
representative week 2026-10-19 through 2026-10-25: Monday-Friday WEEKDAY,
Saturday/Sunday WEEKEND, 08:30-17:30 each applicable day. The confirmed fixed
student lunch periods apply to both streams. Source lecture hours produce one
continuous weekly lecture. For this PP1 configuration, each other positive
contact-hour column is retained as one unsplit weekly block; no source hours
are reduced or changed, and no general university splitting rule is claimed.
Lecture/tutorial/workshop audiences are main groups, labs use their subgroups.
These audience/type-suitability settings are explicit editable PP1 configuration.
All inventory values remain intact. The 62-venue inventory has 60 bookable venues;
two unresolved component-room relationships retain their existing exclusion.
All bookable venues use the user-supplied PP1 teaching windows and native
capacity/type/no-double-booking checks. No holiday, shared lecture combining
or cross-specialization student-overlap optimization is introduced.

The **full global model returned UNKNOWN**, with no complete schedule, within
the configured 120-second search/build budget. Native total runtime was about
156 seconds: model construction itself is not interrupted by the search budget.
Existing lunch/preference model-size guards required explicit PP1 operational
limits of 500,000 variables and 1,000,000 constraints. These are technical
limits, not relaxed academic constraints. No requirements were truncated.

Independently, **41 leaf groups in 27 weekend scopes exceed available teaching
time**. Each weekend group has 16 hours after two one-hour lunches. The most
overloaded source group needs 28 hours (Year 4 Semester 1 IT). A native diagnostic
using its **16 original requirements** and unchanged calendar/lunch/resource
rules returned **INFEASIBLE**. Because this is a subset of the original global
request, it proves the complete request cannot fit either. This diagnostic does
not generate an independent specialization timetable or change the global
solver's recorded UNKNOWN status.

| Example weekend scope | Required hours | Available hours |
| --- | ---: | ---: |
| Year 1 Semester 1 COMPUTER_SCIENCE | 20 | 16 |
| Year 2 Semester 1 CSNE | 24 | 16 |
| Year 4 Semester 1 IT | 28 | 16 |

The result is **0 scheduled / 1,461 unscheduled**. Every occurrence is retained
in `unscheduled.csv` and `session-demand.csv`; `timetable.csv` contains a header
and no invented assignments. `timetable.html`, eight specialization HTML pages,
`views.json`, `solver-result.json`, `run-report.json`, configuration and the native
infeasibility witness are under ignored
`local-data/pilot-previews/step12a3-pp1-reviewed-results/`. Group views inherit
ancestor lecture attendance. Unscheduled lecturer views show candidate eligibility,
not assignment/workload. Venue views retain all inventory attributes. All outputs
are labelled **PP1_TEST_CALENDAR**. Failure-output integrity was independently
validated; no clash-free timetable or lunch-compliance claim is made for an
empty unsuccessful schedule. Original global native result fields were preserved
exactly when regenerating the report with the diagnostic witness.

### Staff policy boundaries

The PP1 pipeline reuses the existing workload policy, availability/cap checks,
optional protected-period preparation and soft preference contracts. Explicit
StaffSchedulingInput and protected-period overrides are retained and enforced.
No individual dated staff calendar or timed approved admin reservations were
provided in the workbooks. The default PP1 staff calendar therefore uses the
configured test windows, explicitly labelled as test availability. It does not
claim actual staff availability or completed administrative work. The 232 source
day-part availability assertions remain recorded but lack numerical day-part
boundaries; they are not silently turned into guessed hard blocks.

An explicit source-position mapping provides 105 workload profiles. Five
appointment-only titles remain unclassified; no underlying academic ranks are
invented. `StaffSchedulingInput.allow_unclassified_staff_for_testing` defaults
to false; PP1 opts in so their availability is still enforced and the workload
foundation reports them unassessed. Actual-campus readiness rejects this option.
The confirmed targets remain teaching targets rather than maximum-only caps,
and the separate Professor/HOD/Dean 240-minute administrative allocation remains
distinct from completion. No administrative calendar blocks or six teaching-day
requirements are invented. Existing appointment/rank policy logic is unchanged.

170 applicable source day/weekend preferences are soft criteria, with an explicit
equal PP1 test weight of one. The 514 clock-label preferences remain retained
without invented MORNING/AFTERNOON cutoffs. Test weights, dates and availability
are clearly distinguished from university-provided academic facts.

```powershell
.\.venv\Scripts\python.exe -B -m schedence_teaching.pilot.pp1_cli --config services/teaching-service/examples/pp1-test-calendar.json --output services/teaching-service/local-data/pilot-previews/new-pp1-run
```

Exit 0 means complete validated timetable; exit 1 means no complete timetable
and reports preserve all unscheduled demand; exit 2 indicates invalid input or
configuration. The configured full request is infeasible even if the main model
gets a longer search budget. A curriculum/attendance or calendar decision is
needed before a complete timetable can exist; the implementation does not make
that decision or alter accepted source data.

Additional implementation files are `pilot/allocations.py`, `pilot/pp1.py`,
`pilot/pp1_run.py`, `pilot/pp1_cli.py`, `examples/pp1-test-calendar.json` and
`tests/test_pp1_allocations.py`. The staff-input validation contract and campus
readiness check include the strict-by-default testing option. Core CP-SAT
constraints, objective logic, original tests, other services and Excel sources
remain unchanged.

Final PP1 verification: **2,156 tests passed**, comprising all 2,120 previous
tests plus 36 new allocation/PP1 tests; `pip check` passed. The existing
Starlette/httpx deprecation warning remains. Tests cover exact cohort
reconciliation and half-up formula semantics, invalid/stale/formula inputs,
automatic specialization grouping, continuous sessions, original staff/team
preservation, two-specialization global conflicts, strict production rejection
of the testing workload option, hard supplied staff blocks/caps, lunch scope,
corrupt-export rejection, complete unscheduled coverage and native infeasible
source-subset certificates. All source workbook hashes are unchanged.

## Files and verification

Created:

- `src/schedence_teaching/pilot/academic.py`
- `src/schedence_teaching/pilot/academic_cli.py`
- `src/schedence_teaching/schemas/specializations.py`
- `tests/test_academic_demand.py`
- This implementation report.

Modified:

- `excel_import/wide_config.py`, `wide_adapter.py`, `staging.py` and
  `preference_adapter.py`: optional checksum-bound confirmation and the explicit
  continuous-lecture rule; preserve generic-import compatibility.
- `integration/audit.py` and `integration/readiness.py`: report current source
  confirmation and accept exact specialization membership in shared offerings.
- `pilot/demo.py`, `pilot/preview.py` and `pilot/__init__.py`: source lecture
  duration handling, views from one validated result and public preparation exports.
- Service `README.md`, `data-collection/README.md`, `gap-report.csv` and
  `university-checklist.md`: supersede stale verification and lecture-hour gaps.
- Ignored `local-data/department-aligned.mapping.json`: confirmation checksums and
  lecture interpretation; no personal source records added to repository files.

The full Teaching Service suite passed **2,120 tests** (2,075 existing and 45 new)
with the existing Starlette/httpx deprecation warning. `pip check` passed.
New tests cover continuous 60/120-minute lectures and zero/missing/invalid hours;
exact specialization applicability and starting metadata; unchanged source teams
and bytes; duplicate-code/source-scope handling; explicit non-lecture patterns;
main/subgroup audiences; globally shared staff/venue conflicts; inherited group
views and single physical booking coverage; source confirmation without invented
preference scope or weights; and checksum mismatch rejection.

An initial full run had 2,110 passes and ten setup errors because one existing
10-second lunch fixture returned FEASIBLE rather than its required OPTIMAL result.
Its isolated module then passed all 102 tests, and the subsequent complete run
passed all 2,120. No solver limits, existing tests or assertions were changed.

Before/after hashes confirmed every original workbook byte-identical, every
existing solver file and test unchanged, and no original files missing. The
branch remains `feat/teaching-timetable`; the Teaching Service remains local and
uncommitted. There were no database operations, commits, pushes or branch changes.
