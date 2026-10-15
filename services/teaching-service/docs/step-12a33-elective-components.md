# Step 12A.3.3: elective registration and hybrid timetable results

These are private PP1 previews using university-sourced module, staff and venue
inputs, approved proportional student allocations, reproducible **test** elective
registrations and the explicitly approved **PP1_TEST_CALENDAR**. They are not a
complete Year 4 academic timetable or actual university student registrations.

## Preserved work and resumed implementation

The interrupted implementation already included optional online/physical solver
contracts, explicit student attendance evidence, elective registration validation,
source-module attendance filtering, hybrid result validation and 50 synthetic
tests. That work was retained, including the previous private output directories.

The resumed work added configurable Research obligations, a typed run settings
file, deterministic balanced test registrations, progressive coordinated runs,
completion labels, per-student contact-hour reconciliation and independent
physical laboratory capacity bounds. No original Excel workbook was changed.
No files present at resumption were removed.

New implementation files:

- `pilot/research_requirements.py`: compulsory obligations and future explicit
  session mappings using existing offering, group and requirement contracts.
- `pilot/elective_config.py`: editable typed settings and blocked scopes.
- `pilot/elective_run.py`: one canonical registration snapshot, progressive scope
  selection, independent validation and export orchestration.
- `pilot/resource_capacity.py`: optimistic size-sensitive whole-session room
  capacity bounds, separate from CP-SAT outcomes.
- `examples/pp1-elective-test-settings.json`: approved test days/windows, seed,
  allocation strategy, Research state, blocked DS scopes and search budgets.
- `tests/test_research_electives.py` and `tests/test_resource_capacity.py`.
- This document.

Modified files after resumption: `pilot/electives.py`, `elective_cli.py`,
`elective_io.py`, `pp1_run.py`, `weekly_view.py` and the service README. The
optional solver/attendance implementation from before interruption was preserved.
All module paths above are relative to `src/schedence_teaching/` unless prefixed
with `examples/`, `tests/` or `docs/`.

## Registrations and corrected demand

Each of the 1,532 supported Year 4 test students has exactly two distinct
electives: **3,064 selections**. The accepted Year 4 module applicability catalog
provides the PP1 candidate pool under the user's clarified elective rule. All
54 Year 4 source rows are preserved; there are 72 source-row/specialization
classifications and 144 cohort/offering mappings across the two streams. Every
elective with registered students retains its source teaching requirements.
Explicit compulsory/project classification overrides remain supported.

`BALANCED_TEST_PAIRS`, seed 7, shuffles each cohort's eligible catalog
deterministically, pairs adjacent entries (wrapping an odd final entry) and
assigns those pairs round-robin. This distributes simulated registrations across
the available courses; it does not establish a university restriction on course
pairings. The prior random eligible-pair strategy remains available. Supplied
registrations can replace test selections through the CSV interface.

Attendance groups are course-specific, at most 120 students per main group and
60 per lab subgroup. Complete synthetic membership evidence identifies students
appearing in more than one course group. Distinct group IDs alone never establish
disjointness. The native solver and independent validator check actual student
intersection, ancestor attendance, staff and venue clashes. One occurrence is
one booking; exact required coverage is checked without counting ancestor labs
twice. No shared lecture combining or cross-specialization shared-population
optimization was enabled.

The old 28-hour IT Y4 S1 figure was the sum of attendance at all eight applicable
electives. It is not the demand of an individual student registered for two.
The Weekday preview now has **6–8 elective contact hours per student** and the
Weekend preview **7 hours**. All eight modules still run; their total contact
hours, lecturer eligibility and source row identities are unchanged. Two lecture
hours remain one continuous 120-minute session. Source-row offering references
remain local planning references, never claimed as authoritative database IDs.

Non-Year-4 requirements retain their original occurrence identities and demand.
The supported known input contains **1,401 occurrences**, replacing the earlier
1,461-occurrence catalog-wide attendance expansion through registered attendance,
not by reducing source contact hours.

## Research and blocked cohorts

All Year 4 cohorts retain a compulsory obligation labelled
`COMPULSORY_RESEARCH_PENDING_SESSION_MAPPING`. There are 32 such obligations:
30 supported elective cohorts and the two blocked DS cohorts. Unknown Research
hours are `null`, not zero. Neither an allocation nor a missing session record is
evidence that Research work has been completed.

`ResearchRequirementInput` accepts explicit future `ResearchSessionMapping`
contracts with a supplied offering, eligible staff, applicable cohort IDs and
positive duration/frequency/type/audience patterns. These reuse the preparation,
attendance, lunch, venue and solver contracts. Complete mapping must be declared
explicitly; incomplete Research does not block elective generation. No real
Research course, code, lecturer, duration or supervision session was invented.

Year 4 Semester 1 DS remains `BLOCKED_INPUT`: 42 Weekday and 106 Weekend
students, **148 in total**, with no applicable source module mapping. Their
counts are preserved in `blocked-scopes.csv` and `original-cohort-counts.json`.
Across all years the input has 128 cohort entries: 126 supported and two blocked.
The summed scoped counts reconcile as **6,720 = 6,572 + 148**. These totals span
concurrent semesters and are not a deduplicated real-person headcount.

## Calendar and hard constraints

The representative week remains 2026-10-19 through 2026-10-25, with both
semesters active together:

| Stream | Physical teaching | Online lecture opportunity |
| --- | --- | --- |
| Weekday | Mon/Tue/Thu/Fri, 08:30–17:30 | Wednesday, 08:30–18:00 |
| Weekend | Saturday/Sunday, 08:30–17:30 | Monday, 08:30–18:00 |

Days, windows and search limits are editable settings. Online delivery is
lecture-only; labs, tutorials and workshops remain physical. Online lectures
consume lecturer and student time but no physical venue. No minimum online
teaching quota is inferred. Lunch remains 12:30–13:30 for Years 1–2 and
13:30–14:30 for Years 3–4, applying to both streams. No lecturer lunch rule was
introduced.

Existing eligibility, availability, explicit teaching caps, supplied protected
periods, venue capacity/type/bookings and lunch checks are retained. The prior
PP1 limitations remain visible: source weekday/weekend preferences are soft;
source time labels do not establish invented clock cutoffs; unprovided campus
availability/protected periods are not fabricated. Workload fulfillment is not
claimed merely because a feasible teaching schedule exists. The six-day campus
attendance policy is not interpreted as six compulsory teaching days.

## Actual timetable runs

| Scope | Native status | Required | Scheduled | Unscheduled | Independent validation |
| --- | --- | ---: | ---: | ---: | --- |
| IT Y4 S1 Weekday | OPTIMAL | 16 | 16 | 0 | Passed |
| IT Y4 S1 Weekend | OPTIMAL | 16 | 16 | 0 | Passed |
| All supported Year 4, 30 cohorts | OPTIMAL | 282 | 282 | 0 | Passed |
| All supported PP1, 126 cohorts | UNKNOWN | 1,401 | 0 | 1,401 | No complete result to validate |

The Year 4 joint run coordinates all eight specializations, both semesters and
both streams in one model: **38 online and 244 physical sessions**, with zero
student, lecturer or venue clashes. Independent checks include all 1,532 test
students and exact 282-occurrence coverage. Its native run took about 66 seconds.
Successful scopes are labelled **ELECTIVE_COMPONENT_COMPLETE**; complete Year 4
academic timetable flags remain false because Research mapping is unresolved.
The two IT previews are separate alternatives; do not concatenate their bookings
with the coordinated Year 4 run.

The larger run used a 180-second native search limit and about 205 seconds total
including model construction. It returned `UNKNOWN`; no assignment was exported
as a successful full timetable. Independent capacity evidence below establishes
infeasibility under the current resource/calendar assumptions without changing
that recorded native status.

## Independent physical lab capacity proof

The actual inventory has 19 suitable labs: ten 60-seat rooms and nine 30-seat
rooms (17 Computer Labs plus two BYOD rooms). All current lab occurrences last
120 continuous minutes. A nine-hour physical day fits at most four such bookings
per room. This is an optimistic bound: staff, competing teaching and further
room restrictions can only reduce capacity.

| Stream and demand | Required lab bookings | Optimistic available slots | Shortage |
| --- | ---: | ---: | ---: |
| Weekday groups above 30 students | 205 | 10 rooms × 4 days × 4 = 160 | 45 bookings / 90 room-hours |
| Weekend all lab groups | 189 | 19 rooms × 2 days × 4 = 152 | 37 bookings / 74 room-hours |
| Weekend groups above 30 students | 99 | 10 rooms × 2 days × 4 = 80 | 19 bookings / 38 room-hours |

These bounds overlap: **do not add the Weekend shortages**. Room-hours are not
individual student attendance hours. The audit was attached only after matching
all 1,401 saved demand occurrences and their requirement, offering, group, type
and duration against the reconstructed canonical registration input.

`test-calendar-capacity-comparison.csv` reports upper bounds for +0/+60/+120/
+180/+240 physical minutes per day, labelled
`TEST_SCENARIO_UPPER_BOUND_ONLY_NOT_A_TIMETABLE`. No extended calendar was applied
to the actual timetables. With the fixed lunch periods, +60 minutes still permits
only four two-hour bookings. At +120 minutes the Weekday large-lab bound still
fails (205 needed, 200 slots). Passing any bound does not prove a feasible
timetable. These comparisons are diagnostics, not recommended campus hours.

The smallest justified next action is to resolve confirmed physical laboratory
capacity or explicitly approve a calendar/grouping scenario addressing these
booking deficits. Increasing search time alone cannot overcome the proven lab
shortage. Do not change room capacities or remove legitimate courses to fit.

## Reproduction, tests and private outputs

From the repository root, using the existing virtual environment:

```powershell
.\.venv\Scripts\python.exe -B -m schedence_teaching.pilot.elective_cli --output services/teaching-service/local-data/pilot-previews/step12a33-resumed-reviewed
.\.venv\Scripts\python.exe -B -m pytest services/teaching-service/tests -q --tb=short
.\.venv\Scripts\python.exe -B -m pip check
```

The recorded generation command exits 1 because the larger global scope has no
complete result. CLI output directories must be new; use a different directory
for reproduction. `--run-config` selects editable settings, `--classifications`
applies explicit source-row overrides, and `--registrations` accepts supplied or
explicitly labelled test registrations. No database writes occur.

Synthetic tests cover exact two-elective validation, preserved course demand,
deterministic registrations, group membership overlap, online/physical bookings,
staff/venue/lunch conflicts, invalid or corrupted results, pending and explicit
Research mappings, contact-hour reconciliation and conservative room-capacity
proofs. The complete suite passed **2,256 tests in 135.56 seconds**; `pip check`
reported no broken requirements. One existing Starlette/httpx deprecation
warning remains. The 74 tests added across this step comprise 50 preserved
pre-interruption cases and 24 resumed Research/capacity cases.

All exports stay in Git-ignored
`local-data/pilot-previews/step12a33-resumed-reviewed/`:

- `index.html`: master result and diagnostics links.
- `03-all-supported-year4/weekly.html`: coordinated student-group/lecturer/venue
  weekly views; `timetable.html` links all specialization pages.
- Each scope: scheduled `timetable.csv`/`timetable.json`, original demand,
  unscheduled report, configuration, native result and independent validation.
- Successful scopes: `elective-independent-validation.json` checks full student
  registrations and native constraints independently.
- `enrollment/`: editable registrations, retained catalog, exact attendance
  mappings and per-student contact-hour reconciliation.
- `blocked-scopes.csv`, `research-obligations.csv`, `original-cohort-counts.json`.
- `physical-lab-capacity.csv`/`.json` and calendar bound comparisons.

Source Excel checksums remain unchanged. Personal input and timetable details are
kept in ignored local exports. Nothing was committed, pushed or written to Neon.
