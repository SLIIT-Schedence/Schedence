# Step 12A.3.4: lab capacity and semester scenario review

The source workbooks, reviewed elective registrations, capacities, session
durations and original PP1 baseline are unchanged. Semester 1 and Semester 2
are evaluated as **TEST_SCENARIO alternatives**, not official semester dates.
Research remains compulsory pending session mapping; Semester 1 Year 4 DS
remains input-blocked with 42 Weekday and 106 Weekend students preserved.

## Results and exact scope boundaries

| Scope | Required known occurrences | Scheduled | Unscheduled inside scope | Result | Independent evidence |
| --- | ---: | ---: | ---: | --- | --- |
| A: full supported Semester 1 | 697 | 0 | 697 | Native UNKNOWN | Student packing proves infeasible |
| B: full Semester 2 | 704 | 0 | 704 | Native UNKNOWN | Student packing proves infeasible |
| C: simultaneous semesters, original stress-test scope | 1,401 | 0 | 1,401 | Native UNKNOWN | Lab and student packing prove infeasible |
| A: explicitly selected feasible-cohort subset | 681 | 681 | 0 | Coordinated FEASIBLE | Complete union independently passed |
| B: explicitly selected feasible-cohort subset | 693 | 693 | 0 | Coordinated FEASIBLE | Complete union independently passed |

The Semester 1 subset includes 61 cohorts and 3,117 scoped students. It excludes
only Year 3 Semester 1 IT Weekend: **95 students, 16 original occurrences**.
The original DS input block is separate. The Semester 2 subset includes 63
cohorts and 3,342 scoped students, excluding only Year 3 Semester 2 IM Weekend:
**18 students, 11 original occurrences**. All 27 excluded occurrences remain
unchanged in an explicit unscheduled/outside-scope CSV; no legitimate teaching
demand was deleted to claim full-semester success.

Each successful subset includes both streams, Years 1–4 and all eight
specializations represented in that selected semester. Every selected required
occurrence is scheduled. Its `known_session_scope_status` is `COMPLETE`, while
its teaching component is `AVAILABLE_TEACHING_COMPONENT_COMPLETE`. Research
prevents a Year 4 academic completeness claim. Semester 1 has 14 supported
Research-pending cohorts; Semester 2 has 16. Both blocked DS cohorts also retain
their compulsory Research obligations separately.

The two successful timetables are **alternatives**. Their booking lists must
not be concatenated onto the same actual dates. Official semester overlap is
not inferred. Any genuinely overlapping academic periods still require a joint
conflict-free run. The previous 282-session Year 4 result and all previous
outputs are preserved.

## Actual lab inventory and baseline capacity

All 62 source venue records and campus-confirmation metadata are preserved.
There are 19 suitable physical labs: **17 Computer Labs and two BYOD Labs**.
Ten have capacity 60; nine have capacity 30. The two unresolved room-booking
relationships belong to excluded venues; the current bookable inventory is 60.
All suitable labs are already considered by the native eligibility checks.

Labs remain continuous 120-minute occurrences. Main groups retain the maximum
120 and lab subgroups the maximum 60; each assigned venue must actually fit the
group. A nine-hour room day holds at most four two-hour bookings, even before
student and staff restrictions. Physical Weekday teaching has four days;
physical Weekend teaching has two. Online lectures occupy no physical venue.

| Full scenario | Weekday lab bookings / optimistic room slots | Weekend lab bookings / optimistic room slots | Size-sensitive lab shortage |
| --- | --- | --- | --- |
| A: Semester 1 | 137 / 304 | 92 / 152 | None found by necessary bounds |
| B: Semester 2 | 129 / 304 | 97 / 152 | None found by necessary bounds |
| C: simultaneous | 266 / 304 | 189 / 152 | Weekday >30 students: 205 / 160; Weekend >30: 99 / 80 |

The simultaneous stress test needs at least **45 additional two-hour Weekday
bookings for groups above 30 students**, and **37 additional Weekend lab
bookings overall**. These mean booking deficits, not missing physical rooms.
The Weekend >30-student bound separately shows 19 missing bookings; it overlaps
the 37-booking bound and must not be added to it. Passing a necessary room bound
is not proof of timetable feasibility.

The new reports show every lab occurrence, source row, group size, duration,
compatible venue IDs and source-eligible staff IDs. These are candidates, not
fixed lecturer assignments. All original eligibility, capacity, venue type,
availability, booked-space, lunch and staff constraints remain active.

## Why both complete semesters still fail

A newly added student packing diagnostic identifies a stronger necessary
condition than total weekly hours. Year 3 lunch is fixed at 13:30–14:30.
Saturday and Sunday therefore each have uninterrupted segments of five and
three hours. Monday online has segments of five and three-and-a-half hours.
Each day fits at most **three continuous two-hour sessions**: nine across the
three teaching days.

- IT Year 3 Semester 1 Weekend requires **ten two-hour attendances plus two
  one-hour workshops** per leaf student group: 22 hours. Source module rows
  are 102, 104, 106, 108, 111 and 116. The original modules are IT3010, IT3020,
  IT3030, IT3040, IT3060 and IT3090. Its two lab subgroups have 48 and 47 students.
- IM Year 3 Semester 2 Weekend requires **ten two-hour attendances plus one
  one-hour activity**: 21 hours. Its original source rows/modules are retained
  in `unscheduled-outside-feasible-previews.csv`.

Both totals fit the raw 24.5 available student hours, but ten continuous
two-hour sessions cannot occupy nine possible slots. The proof relaxes staff,
venue and shorter-session competition; it does not invent a new hard rule.
The IT cohort also returned native `INFEASIBLE` before any decomposition
bookings were reserved. Native full-model `UNKNOWN` remains distinct from
independently proven calendar infeasibility.

No importer, module-applicability or source contact-hour defect was found.
The earlier weekly-hour and lab-room bounds were necessary but insufficient
to expose this continuous-duration packing obstruction.

## Search and global conflict protection

Optional `SolverConfig.num_search_workers` and `lab_variables_first` affect
search and model-variable ordering only. Defaults remain one worker and the
previous ordering. No original CP-SAT constraints or objective tiers were
removed or relaxed. Both the ordinary and lexicographic solvers use the
configured worker count.

Monolithic attempts for A and B returned `UNKNOWN` after 240-second native
search budgets (about 256 seconds including construction). Coordinated whole
streams also found no complete result. The successful approach uses smaller
native CP-SAT cohort subproblems, placing constrained Weekend/physical demand
first. Every subsequent part reserves **all earlier lecturer and shared venue
bookings**, and debits explicit remaining teaching caps when present.
Reservations are algorithmic commitments, not new campus policy.

The complete union is checked against the **original unmodified full selected
scope**. It receives `FEASIBLE`, never global `OPTIMAL`; local native optimality
does not establish joint preference optimality. Source preferences and workload
assessments are recomputed for the global result, not summed from local reports.
Failed restricted parts produce `UNKNOWN` and no partial timetable, never a
false original-scope infeasibility claim.

This decomposition fails closed for joint protected-period, fairness,
consecutive or combination optimization and explicit student attendance shared
across partitions; those inputs must use the existing monolithic engine. No such
unsupported joint optimization was enabled for these PP1 inputs. No missing
administrative blocks, actual availability or completed work were invented.

Independent validation covers exact occurrence metadata/duration/frequency,
source eligibility, hierarchy and explicit elective membership, staff/venue
double booking, capacity/type, fixed lunch and online/physical rules. Both unions
have zero hard-constraint violations. No cross-specialization overlap or shared
lecture combining was introduced.

## Utilization and runtime

| Successful subset | Lab bookings | Online / physical occurrences | Room contact hours | Lecturer teaching hours | Used venues / staff | Search and construction runtime |
| --- | ---: | --- | ---: | ---: | --- | ---: |
| Semester 1 | 221 | 151 / 530 | 884 | 1,178 | 60 / 104 | 78.45 s |
| Semester 2 | 221 | 109 / 584 | 974 | 1,183 | 60 / 109 | 79.51 s |

Maximum individual raw-window utilization is 70.37% for a venue and 53.12% for
a lecturer in Semester 1; Semester 2 maxima are 74.07% and 45.31% respectively.
Each CSV contains per-resource detail. Availability denominators merge
overlapping windows and subtract explicit availability blocks; they are raw
configured time, not whole-session packing capacity or workload targets.
Teaching time is separate from administrative/research work and six-day campus
attendance. Unsuccessful runs report utilization as unknown, not zero.

## Minimum justified proposal, not applied

For the two student packing obstructions, the smallest increment on the current
30-minute scheduling grid that creates another two-hour slot is extending the
**Weekend online Monday end from 18:00 to 18:30**. Its afternoon segment would
then be four hours, allowing two two-hour lectures instead of one. Physical
teaching days/windows, student lunch and source session lengths would stay intact.

This is a **proposal for review only**. No timetable in this step uses extended
hours. Removing that necessary obstruction does not prove globally coordinated
feasibility; a newly approved scenario would require regeneration and independent
validation. It would not repair the simultaneous-semester physical lab deficits.
Do not add rooms or increase source capacities merely to make that stress test fit.

## Implementation and reproduction

New files under `src/schedence_teaching/pilot/`:

- `semester_inputs.py`: reads reviewed registrations and rejects demand drift.
- `semester_scenarios.py`, `semester_cli.py`: typed scenarios, safe private exports
  and honest complete-scope labels.
- `coordinated_streams.py`: shared-reservation native decomposition and validated
  global union, with explicit unsupported-mode guards.
- `lab_diagnostics.py`: source inventory, lab candidates and resource utilization.
- `student_packing.py`: hierarchy/explicit-attendance continuous-session bounds.

Modified: `solver/config.py`, `solver/engine.py`, `solver/lexicographic.py`
(optional search settings only); `pilot/pp1_run.py` (decomposition provenance);
`pilot/resource_capacity.py` (room-size boundary reporting); the service README.
Added three editable example JSON settings, `test_semester_scenarios.py`,
`test_student_packing.py` and a room-boundary regression test in
`test_resource_capacity.py`.

```powershell
# Full A/B/C attempts; exit 1 is expected when a complete result is unavailable.
.\.venv\Scripts\python.exe -B -m schedence_teaching.pilot.semester_cli --config services/teaching-service/examples/pp1-semester-cohort-search.json --output services/teaching-service/local-data/pilot-previews/new-full-semester-comparison

# Explicitly narrowed, independently validated feasible-cohort subsets.
.\.venv\Scripts\python.exe -B -m schedence_teaching.pilot.semester_cli --config services/teaching-service/examples/pp1-semester-feasible-scopes.json --output services/teaching-service/local-data/pilot-previews/new-feasible-cohorts

.\.venv\Scripts\python.exe -B -m pytest services/teaching-service/tests -q --tb=short
.\.venv\Scripts\python.exe -B -m pip check
```

Output directories must be new. Configurations and reviewed snapshot inputs are
editable without changing source Excel files. Backend exports use the existing
CSV/JSON/HTML tooling; no database connection or API is added.

## Private deliverables

All paths are under ignored `local-data/pilot-previews/`:

- `step12a34-review/index.html`: full A/B/C and subset comparison, lab inventory,
  all original outside demand, packing evidence, blocked counts, Research and
  the unapplied minimum-change proposal.
- `step12a34-feasible-cohorts/`: two successful subset previews; each contains
  interactive `weekly.html`, specialization pages, `timetable.csv`/`.json`,
  exact-demand/unscheduled CSVs, native stage records, original configuration,
  independent validation and lecturer/venue utilization.
- `step12a34-search-semester-1/` and `step12a34-search-semester-2/`: original
  unsuccessful monolithic attempts with all demand preserved as unscheduled.
- `step12a34-semester-scenarios/` and `step12a34-coordinated-cohorts/`: additional
  whole-stream/full-cohort attempts and combined stress-test comparisons.

Nothing was committed, pushed, written to Neon or changed outside this service.

Final verification: **2,290 tests passed in 135.74 seconds**, including all
2,256 previous cases and 34 added cases. `pip check` reported no broken
requirements. One existing Starlette/httpx deprecation warning remains.
Both successful weekly pages and the comparison page were rendered and visually
checked; all 80 checked local HTML links resolve. All seven source workbook
checksums and all pre-existing private report files are unchanged; none of the
561 files present at the start of this step is missing. The detailed evidence is
in `step12a34-review/preservation-verification.json` and
`verification-summary.json` under the private preview directory.
