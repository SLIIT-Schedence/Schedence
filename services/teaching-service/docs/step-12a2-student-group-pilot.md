# Step 12A.2: editable student counts and first timetable preview

The editable workbook is a user-provided, supervisor-accepted **development**
input, not independent verification of university populations or a master group
registry. No source workbook is modified. CP-SAT constraints/objectives, earlier
contracts, APIs and database state are unchanged.

## Actual inspected workbook

`local-data/Schedence_Student_Groups_One_Sheet_Editable.xlsx` contains one sheet,
`STUDENT_GROUPS`, 416 populated/layout rows and 18 columns. It was initially
located in the private collection subfolder and was subsequently available at
the requested local-data root. Its content checksum remained unchanged.

The explicit importer reads these original labels:

| Header at row 9 | Destination |
| --- | --- |
| Cohort ID | Preserved aggregate cohort reference |
| Study year | Existing strict study year 1–4 |
| Semester | Existing semester 1–2; Regular Intake → 1 and June Intake → 2 are explicitly supported |
| Stream | Existing WEEKDAY/WEEKEND enum |
| Specialization | Retain only when supplied; currently blank in all 16 rows |
| Students | Positive literal student count |
| Data source | Retained source label |

Literal cohort rows are 10–25. B4 `Main target` = 120, D4 `Lab cap` = 60, and H4
`Large hall` = 240 supply configurable product sizing/planning inputs. The
importer verifies the sizing labels and rejects formulas/errors in editable
input fields. It records source worksheet/row and SHA-256 and checks that the
workbook did not change during import.

The workbook's derived Main groups/Lab groups/Readiness formulas, effective
main-cap formula, total formulas and generated group section from row 32 are
**not** treated as authoritative group records or evaluated using stale caches.
The backend recomputes from literal counts and explicit caps. Small-hall or
large-hall planning labels never override a selected venue's actual capacity.
The workbook's 24 reserved group slots do not truncate backend generation.

## Import and planned group results

| Study year | Semester | Weekday | Weekend |
| --- | --- | ---: | ---: |
| 1 | 1 | 720 | 120 |
| 1 | 2 | 720 | 120 |
| 2 | 1 | 600 | 240 |
| 2 | 2 | 600 | 240 |
| 3 | 1 | 480 | 360 |
| 3 | 2 | 240 | 600 |
| 4 | 1 | 240 | 600 |
| 4 | 2 | 240 | 600 |

All 16 scopes are present: **6,720 students, 56 main groups, 112 lab subgroups,
168 group records**. Summing main groups gives 6,720; summing their subdivisions
also gives 6,720. These are the same populations, not 13,440 separate students.

`generate_groups()` reuses `StudentGroup` and existing hierarchy validation.
For total N and cap C it makes ceil(N/C) groups and balances counts with quotient
and remainder. It applies the same procedure independently to each main group's
labs. For 260 at current caps: main sizes **87, 87, 86**, labs **44/43, 44/43,
43/43**. Other configured caps can produce one or more than two lab subgroups;
no university-wide subgroup partition rule is invented. A technical record
limit fails explicitly rather than truncating or allocating huge partitions.

IDs are deterministic **product planning identities**, prefixed with PLAN and
scoped by study year, semester, stream, encoded source cohort and group index.
They are not authoritative database identifiers or individual student IDs.
Source specialization propagates only when supplied; no actual specialization,
roster or cross-group disjointness is inferred from aggregate counts.

## First pilot and explicit DEMO assumptions

The selected Year 1 / Semester 1 / WEEKDAY cohort has **720 students**, six main
groups of **120** and twelve labs of **60**. Its actual specialization is unknown.

`build_demo_submission()` deliberately constructs a separate SYNTHETIC profile:

* Two fictional specializations, three main groups each; never a silent
  assignment of all six groups to one actual specialization.
* Two explicitly synthetic offering IDs. Existing module codes/names and every
  original candidate staff ID/role for those source rows are retained. Selection
  requires an exact supported source rank title; unknown appointment titles are
  skipped rather than converted into invented academic ranks.
* One **synthetic 60-minute lecture per main group** and one **synthetic
  90-minute lab per subgroup**, once weekly. These are not real module hours.
* Explicit fictional aggregate disjointness assertions, consistent with the
  simulated hierarchy. They are never claimed as actual individual proof.
* Synthetic week **2026-10-19 through 2026-10-25**, with configured teaching
  Monday/Tuesday/Wednesday **09:00–16:00**. No actual opening hours are inferred.
* One retained academic plus another retained eligible member per offering has
  simulated availability; every other original team entry remains present with
  explicit empty demo availability. Staff blocks, venue blocks and daily/weekly
  caps are also explicitly simulated. Accepted teaching-team assignments are
  unchanged; actual lecturer availability is unknown.
* One explicitly simulated soft preference supplies the existing full-profile
  preference mode. Actual preference records, admin/research reservations and
  completed work are not invented. Separate confirmed teaching/admin target
  values remain intact; this pilot does not demonstrate target fulfillment or
  campus attendance. Research/fairness/consecutive applicability is fictional.
* All 62 venue inventory records and checksum-bound confirmation remain intact.
  A small explicit subset of existing halls/labs is eligible in this demo.
  Actual capacities/types are unchanged; unresolved combined rooms stay excluded.
* Optional combinations require the same fictional specialization/content,
  compatible requirements, retained common staff eligibility and explicitly
  fictional disjointness. A 240-student combination must fit an actual suitable
  venue. The 240 planning bound does not permit use of a smaller room.
* Year 1 lunch **12:30–13:30** is explicitly applicable to the three synthetic
  dates and WEEKDAY stream and enforced by the existing hard lunch mode.
  Lecturer lunch is not inferred.

The local assumption report enumerates these choices and missing actual inputs.
All demonstration institution/ADS UUID values are explicitly synthetic. No real
offerings, staffing records, calendars, populations or database rows are created.

The bounded run returned **FEASIBLE**, independently validated: **18 original
occurrences, 16 physical deliveries, two combined lectures**, with exact-once
coverage, capacity/eligibility/calendar/block/clash/protection checks and zero
student lunch violations. FEASIBLE does not claim proof of every objective tier.
A first three-second attempt returned UNKNOWN and emitted only a failure report;
no partial timetable was exported. Native algorithms were not changed.

## Outputs and commands

All preview outputs are inside ignored `local-data/pilot-previews/`. The successful
preview contains `timetable.html`, `timetable.csv`, `solver-result.json` and
`readiness-assumptions.json`, all marked **SYNTHETIC_DEMO**. HTML is a standalone,
read-only weekly table with configured dates across columns, time periods,
modules, direct groups, lecturer/instructor IDs, venues, combined cards and lunch.
Continuations are the same physical booking. CSV contains one row per physical
delivery and retains exact offering/occurrence references plus direct and
hierarchy-expanded attendee group IDs. Staff type distinguishes instructors
from academic staff. No frontend application or server is implemented.

The successful current output directory is
`local-data/pilot-previews/step12a2-preview/`; its HTML was visually checked in a
headless browser. A separate `step12a2-real-readiness/` contains only the blocked
actual-campus report. Earlier bounded-run diagnostic folders are retained.

Every export calls the existing independent full evaluation gateway before
writing any assignments. Corrupted/partial schedules and incompatible labels
are rejected. Existing nonempty output folders are refused. CLI output is
restricted to the Teaching Service's ignored local-data directory.

From the repository root (choose a new output directory when rerunning):

```powershell
.\.venv\Scripts\python.exe -B services/teaching-service/examples/student-group-pilot.py --mode DEMO --output services/teaching-service/local-data/pilot-previews/my-demo
.\.venv\Scripts\python.exe -B services/teaching-service/examples/student-group-pilot.py --mode REAL_CAMPUS --output services/teaching-service/local-data/pilot-previews/real-readiness
.\.venv\Scripts\python.exe -B -m pytest services/teaching-service/tests -q
.\.venv\Scripts\python.exe -B -m pip check
```

The Python API also permits an explicit `GroupSizingPolicy`; the CLI reads the
editable workbook caps. Neither production grouping nor solver code fixes the
total at 6,720. An edited positive count is accepted and regrouped exactly.

## REAL_CAMPUS readiness and remaining inputs

REAL_CAMPUS is the CLI default. It requires `--institution-id` and
`--base-submission` pointing to a reviewed Step 12A `SchedulingSubmission`.
Missing caller context returns only a blocked readiness report without an
invented institution UUID. Supplied context goes through the unchanged strict
Step 12A gate, including source ownership/verification and all policy checks.
Its reviewed groups must exactly match the selected workbook-derived plan;
any supplied source specialization must also agree. The tool does not silently
insert generated groups under an old blanket confirmation or create population
proofs. No synthetic fallback is selected.

Only a complete feasible independently validated actual-mode result is labelled
**CAMPUS_INPUT_VALIDATED**. That means supplied contracts/evidence passed checks;
it does not claim independent official verification, publication or booking.
Blocked/unsuccessful requests emit no timetable HTML/CSV. Actual data still needs:

1. Authorized institution, calendar term, actual week/date/window/stream scope.
2. Actual offering IDs/streams and eligible content-to-group mappings.
3. Group specialization and approved planning/partition/population relationships;
   counts alone do not supply these. Generated IDs need upstream applicability.
4. Actual group-specific session durations/frequencies and complete demand scope.
5. ADS staff UUID crosswalk/classification/appointment applicability and real
   availability, blocks and any explicit caps.
6. Venue availability/blocks and explicit shared/combined-room relationships or
   retained exclusions; inventory confirmation alone is insufficient.
7. Applicable preference weights/boundaries, fairness/consecutive approvals and
   approved admin/research protection, with dated source evidence.
8. Explicit real lunch dates and weekday/weekend applicability.

The new counts partially close the older collection gap report; they do not
resolve its authoritative group/population, offering or calendar blockers.

Verification: **2,075 tests passed** (all 2,040 previous tests plus 35 new);
`pip check` reported no broken requirements. The existing Starlette/httpx
TestClient deprecation warning remains. The runnable demo independently
validated the feasible native timetable and its fixed student lunch rules.
No source workbook content, existing solver code, previous tests, accepted
assignments or database records was changed.
