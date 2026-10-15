# Step 12A.3.5: complete supported semester timetables

The explicitly authorized **PP1_EXPERIMENTAL_CALENDAR** extends only the Weekend
online Monday window from 08:30–18:00 to **08:30–18:30**. This is an experiment,
not an approved university timetable. The original PP1 baseline and all earlier
681-session/693-session preview artifacts are retained unchanged.

## Mathematical diagnosis

Year 3 student lunch remains fixed at 13:30–14:30. Each affected student's
known demand contains ten continuous two-hour attendances, plus shorter work:

| Cohort | Students | Original bookings | Per-student two-hour attendances | Other attendance |
| --- | ---: | ---: | ---: | --- |
| Year 3 Semester 1 IT Weekend | 95 | 16 | Six lectures + four labs = 10 | Two one-hour workshops |
| Year 3 Semester 2 IM Weekend | 18 | 11 | Five lectures + five labs = 10 | One one-hour workshop |

IT's main group has two disjoint lab subgroups of 48 and 47. Each student attends
four labs; the eight actual subgroup lab bookings are not counted twice for one
student. IM has one 18-student lab subgroup. Every original booking is preserved.

| Teaching day | Uninterrupted segments | Maximum two-hour attendances |
| --- | --- | ---: |
| Baseline Monday online | 08:30–13:30 and 14:30–18:00 | 2 + 1 = 3 |
| Saturday physical | 08:30–13:30 and 14:30–17:30 | 2 + 1 = 3 |
| Sunday physical | 08:30–13:30 and 14:30–17:30 | 2 + 1 = 3 |
| Experimental Monday online | 08:30–13:30 and 14:30–18:30 | 2 + 2 = 4 |

The baseline supplies nine possible two-hour placements, despite sufficient raw
weekly hours. The experiment supplies ten. The additional usable lecture slot
is **16:30–18:30**, following 14:30–16:30 without overlapping lunch. No lecture
is split, no contact hours are removed, and labs stay physical.

`calendar-feasibility-comparison.csv` includes both calendars and every affected
leaf's attendance bound. Demand CSVs list source row, module, group, duration,
eligible delivery modes/days and fixed lunch. Placement CSVs enumerate individual
possible starts on the configured 30-minute grid. These are structural candidate
placements, not a claim that all candidate placements can be booked together.
Actual CP-SAT schedules and independent validation establish joint feasibility.

## Original source modules

All rows refer to `Modules_183` in the original Department_Aligned module workbook.
Each lecture/lab entry below is one continuous 120-minute attendance per week.

| IT Y3 S1 module | Source row | Lecture | Lab per student | Workshop |
| --- | ---: | ---: | ---: | ---: |
| IT3010 Network Design and Management | 102 | 120 min | 120 min | — |
| IT3020 Database Systems | 104 | 120 min | 120 min | — |
| IT3030 Programming Applications and Frameworks | 106 | 120 min | 120 min | — |
| IT3040 IT Project Management | 108 | 120 min | — | 60 min |
| IT3060 Human Computer Interaction | 111 | 120 min | 120 min | — |
| IT3090 Business Management for IT | 116 | 120 min | — | 60 min |

| IM Y3 S2 module | Source row | Lecture | Lab per student | Workshop |
| --- | ---: | ---: | ---: | ---: |
| IT3050 Employability Skills Development - Seminar (Non-GPA) | 109 | — | — | 60 min |
| SE3011 Technical Evolution of Multimedia | 147 | 120 min | 120 min | — |
| SE3041 Digital Video Production & Delivery | 155 | 120 min | 120 min | — |
| SE3071 Digital Image Processing | 161 | 120 min | 120 min | — |
| SE3081 Digital Marketing Strategies | 163 | 120 min | 120 min | — |
| SE3091 Games Technology | 165 | 120 min | 120 min | — |

## Results and scope labels

| Selected experimental scope | Result | Expected | Scheduled | Unscheduled |
| --- | --- | ---: | ---: | ---: |
| IT Y3 S1 Weekend | Native OPTIMAL | 16 | 16 | 0 |
| IM Y3 S2 Weekend | Native OPTIMAL | 11 | 11 | 0 |
| All supported Semester 1, both streams | Coordinated FEASIBLE | 697 | 697 | 0 |
| All supported Semester 2, both streams | Coordinated FEASIBLE | 704 | 704 | 0 |

Full semester generation uses the existing native CP-SAT cohort decomposition.
All prior lecturer and shared venue bookings are reserved in later subproblems.
The complete union is independently checked against the original full selected
semester input; local optimality is not presented as global objective optimality.
All 27 previously excluded known occurrences are now included.

Semester 1 contains all 62 supported cohorts (3,212 scoped students), and Semester
2 all 64 supported cohorts (3,360 scoped students). Counts across semesters are
not a deduplicated real-person headcount. The saved test registrations are reused
exactly: Year 4 students retain their two selected electives, including all
registered offerings and the original main/lab group caps and contact hours.

The outputs distinguish:

- `COMPLETE_SUPPORTED_SCOPE`: every known required occurrence in the selected
  supported semester is scheduled exactly once and independently validated.
- `EXPERIMENTAL_CALENDAR_RESULT`: uses `PP1_EXPERIMENTAL_CALENDAR`; not an
  approved university timetable or a change to the baseline.
- `BLOCKED_MISSING_ACADEMIC_MAPPING`: Year 4 Semester 1 DS remains excluded
  from supported input because no applicable source catalog rows exist. Its
  42 Weekday and 106 Weekend student allocations remain preserved.
- `RESEARCH_PENDING_SESSION_MAPPING`: Research remains compulsory for Year 4.
  No module code, frequency, duration, eligible staff or Research booking was
  invented. Missing session mapping does not mean zero academic work.

These are **separate semester alternatives on the same representative test week**.
They must not be combined as a concurrent clash-free timetable. The experiment
does not solve the original simultaneous-semester physical lab-capacity shortage.
No further calendar adjustment was needed for the supported separate semesters.

Recorded search/construction runtimes were 0.66 s and 0.38 s for the isolated
IT/IM native solves, and 122.10 s and 109.74 s for the coordinated semester
solves. Semester 1 has 161 online and 536 physical occurrences; Semester 2 has
132 online and 572 physical occurrences. Both affected cohorts use four Monday
online lectures, including one 16:30–18:30 lecture. These are actual validated
bookings, not just possible placements.

## Preserved constraints and test-availability provenance

Physical Weekend teaching remains Saturday/Sunday 08:30–17:30. All other stream
windows, dates, online/physical type rules, fixed lunch, source eligibility,
capacity/type restrictions, explicit teaching caps and blocked periods remain
unchanged. Online delivery uses no physical room; venue calendars are unchanged.
Six-day campus attendance is not treated as six mandatory teaching days.

The existing PP1 staff calendar is generated from test teaching windows, rather
than actual campus availability records. Its derived Monday window therefore
follows the experimental 18:30 finish, with explicit provenance
`PP1_TEST_WINDOWS_DERIVED_FROM_EXPERIMENTAL_CALENDAR`. Workload policies, caps,
blocked periods and dates remain identical. Explicitly supplied staff availability
overrides are never extended. A non-template staff calendar cannot be silently
overwritten; the experiment fails closed instead. This is not evidence of actual
individual lecturer availability or completion of administrative/research work.

Independent validation covers exact original occurrence metadata and coverage,
hierarchy and selected-elective membership clashes, staff/venue double booking,
duration/frequency, capacity/type, stream/mode and fixed student lunch. Every
scope additionally reads back its CSV and compares it against the original
prepared demand, with `session-coverage.csv` showing one scheduled occurrence
for each expected occurrence. No cross-specialization overlap optimization,
shared lecture combining, source-data changes or database integration was added.

## Files and reproduction

Created:

- `src/schedence_teaching/pilot/calendar_experiment.py`: isolated calendar
  transformation, placement diagnostics, exact coverage and progressive runs.
- `src/schedence_teaching/pilot/calendar_experiment_cli.py`: safe new-directory
  command using the reviewed registration snapshot.
- `examples/pp1-experimental-calendar.json`: only the authorized 18:30 scenario.
- `tests/test_calendar_experiment.py`: synthetic input, preservation, placement,
  availability, mode/lunch, label, corrupt coverage and export regressions.
- This report.

Modified: `pilot/pp1.py` (explicit experimental label), `pilot/pp1_run.py` and
`pilot/weekly_view.py` (labels and semester boundaries),
`pilot/semester_scenarios.py` (report labels and empty non-Year-4 elective report),
and the service README. No CP-SAT engine or hard-constraint code changed.

The first real cohort solved successfully but exposed an export defect: a
Year 3-only scope has no Year 4 elective rows, while the CSV writer required
nonempty rows. It now writes an explicit empty report, covered by a regression
test. Reporting also avoids claiming Year 4 academic completeness for a scope
that contains no Year 4 students. Initial/debug artifacts were retained; the
authoritative full outputs are in `reviewed-results/`.

```powershell
.\.venv\Scripts\python.exe -B -m schedence_teaching.pilot.calendar_experiment_cli --output services/teaching-service/local-data/pilot-previews/step12a35-experimental-calendar/reviewed-results
.\.venv\Scripts\python.exe -B -m pytest services/teaching-service/tests -q --tb=short
.\.venv\Scripts\python.exe -B -m pip check
```

Use a **new** output directory when reproducing; the command refuses overwrites.
All private files remain inside ignored `local-data/pilot-previews/`.

`step12a35-experimental-calendar/reviewed-results/index.html` links the four
results and their specialization, group, lecturer and venue views. Each result
includes `weekly.html`, scheduled CSV/JSON, coverage/unscheduled reports,
independent validation, exported-CSV revalidation, utilization, configuration and
scope/provenance summaries. Root files contain baseline/experimental calendars,
module demand, candidate placements, calendar comparison and remaining blockers.

No workbook, original source hour, previous successful artifact, database or
unrelated service was modified. No commit, push or branch switch was performed.

Final verification: **2,307 tests passed in 171.12 seconds**, preserving all
2,290 previous cases and adding 17 tests. `pip check` found no broken
requirements; one existing Starlette/httpx deprecation warning remains. All
four CSV exports passed read-back revalidation against original prepared demand.
Three HTML pages were rendered and visually checked; all 100 checked local
HTML links resolve. All seven source workbooks and every pre-existing private
artifact are byte-identical; none of the 905 files present at the start of this
step is missing. Detailed evidence is in `verification-summary.json` under
`reviewed-results/`.
