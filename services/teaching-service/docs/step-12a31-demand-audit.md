# Step 12A.3.1: source demand audit and complete feasible scopes

All academic Excel inputs remain university-sourced, user-confirmed records.
Specialization counts are approved proportional PP1 test allocations. The
2026-10-19 to 2026-10-25 calendar remains `PP1_TEST_CALENDAR`, with both semesters
concurrent: Mon–Fri weekday / Sat–Sun weekend, 08:30–17:30. Fixed student lunch
removes one hour each day (Years 1/2 12:30–13:30; Years 3/4 13:30–14:30).

## Source reconciliation

`pilot.demand_audit.audit_demand` independently reconstructs expected audiences
and weekly contact minutes from all 184 source module rows. It checks exact
year/semester/specialization applicability, unchanged module fields, unique
source-row/stream bindings, omitted bindings, missing/duplicate requirements,
duration/frequency, and the exact expanded occurrence multiset. Conflicting
module codes are retained by source-row identity, including both IT2100 rows.

For each leaf group it follows only its ancestor chain, adds each original
occurrence once, and compares attendance against source contact-hour totals.
Parent lectures/tutorials/workshops and the leaf's own labs are counted; sibling
labs are excluded. Neither different IDs nor proportional counts are used as
proof of actual population disjointness. No source or expansion defect was found
in the current 1,461 occurrences, 128 academic scopes and 198 leaves.

### Year 4 Semester 1 IT Weekend

All rows below are in `Modules_183` in
`Schedence_Modules_Department_Aligned_Instructors_Unchanged.xlsx`.
These eight distinct rows explicitly include IT in their specialization values.
No elective selection, exemption or alternative-module policy is inferred.

| Source row | Module | Name | Lecture h | Tutorial h | Workshop h | Lab h |
|---|---|---|---:|---:|---:|---:|
| 72 | IE4060 | Robotics & Intelligent Systems | 2 | 0 | 0 | 2 |
| 119 | IT4020 | Modern Topics in IT | 2 | 1 | 0 | 0 |
| 121 | IT4030 | Internet of Things | 2 | 1 | 0 | 0 |
| 123 | IT4040 | Database Administration | 2 | 0 | 0 | 2 |
| 125 | IT4050 | Innovation Management and Entrepreneurship | 2 | 0 | 1 | 0 |
| 128 | IT4090 | Cloud Computing | 2 | 0 | 0 | 2 |
| 130 | IT4110 | Computer Systems and Network Administration | 2 | 0 | 0 | 2 |
| 178 | SE4050 | Deep Learning | 2 | 1 | 0 | 0 |
| **Total per leaf** | | | **16** | **3** | **1** | **8** |

Each leaf therefore attends **28 hours in 16 occurrences**: eight continuous
120-minute lectures, three 60-minute tutorials, one 60-minute workshop and four
120-minute labs. Each positive source column remains one unsplit weekly block
under the existing explicit PP1 non-lecture configuration. Zero hours generate
no session; missing values are never silently replaced with zero.

The approved IT cohort has 159 test students: two main groups of 80 and 79,
with four lab leaves of 40, 40, 40 and 39. There are 40 physical occurrences
across these main/subgroups, totaling 72 lecturer/venue contact hours. This is
distinct from the 28 hours attended by each leaf: a parent session is not counted
again for its sibling lab group when evaluating an individual student's week.
The target CSV gives the exact main/lab group and source row for each occurrence;
the leaf-contributions CSV gives every leaf's individual attendance coverage.

## Capacity finding and calendar alternatives

Available weekday attendance is 5 × (9−1) = **40 hours**; weekend attendance is
2 × (9−1) = **16 hours**. **41 leaves in 27 weekend scopes** exceed this necessary
time bound. Required attendance ranges from 17 to 28 hours among overloaded
scopes, so shortages range from 1 to 12 hours per leaf per week. The full baseline
request is **mathematically calendar-infeasible**, even with unlimited venues
and lecturers. More solver time cannot fix this shortage. The earlier full native
solver result remains UNKNOWN; this audit does not relabel it INFEASIBLE or
pretend to run the full global solver again.

`calendar-scenario-comparison.csv` lists every scope's baseline capacity and
minimum extra teaching hours. These are necessary lower bounds, not sufficient
resource-feasibility guarantees. No additional days/hours, semester separation,
sessions removed, new courses, or hard-constraint relaxations were applied.
No experimental calendar was tested. A future calendar experiment must be
explicitly labelled `TEST_SCENARIO` and independently validated.

The smallest justified next change is an explicit calendar decision providing
sufficient additional teaching time for affected cohorts—**at least 12 extra
hours/week for the worst case**—followed by renewed time/resource checks. No
importer correction or source-demand reduction is justified by this audit.

## Progressively coordinated complete previews

Each row below is its own single CP-SAT model. All original requirements for
every selected cohort survive unchanged; shared staff/venue constraints apply
globally within that model. These runs overlap in scope and must not be added
together or combined as separate timetables.

| Complete selected scope | Academic scopes | Scheduled | Unscheduled within scope | Status |
|---|---:|---:|---:|---|
| Year 1 / Semester 1 / IT / Weekday | 1 | 20 | 0 | OPTIMAL |
| Year 1 / Semester 1 / all eight specializations / Weekday | 8 | 106 | 0 | OPTIMAL |
| Year 1 / both concurrent semesters / all eight specializations / Weekday | 16 | 233 | 0 | OPTIMAL |
| Year 1 / both concurrent semesters and streams / all time-fitting scopes | 30 | 356 | 0 | OPTIMAL |

The fourth run explicitly excludes Year 1 Semester 1 Computer Science Weekend
(20h versus 16h) and Year 1 Semester 2 CSNE Weekend (19h versus 16h), as well as
all Years 2–4. They remain intact outside the requested scope. Its 1,105 outside
occurrences appear separately; no complete full-PP1 schedule is claimed. Its
1,666 summed cohort allocation entries are not a deduplicated student headcount.

Native eligibility, staff availability/caps, venue capacity/type/booking rules,
group hierarchy conflicts, fixed student lunch and applicable soft source
preferences are preserved. Each successful export reruns independent validation
for exact coverage, original metadata, durations, stream windows, group/staff/
venue clashes, eligibility, availability and the enabled policy reports. These
results use the existing PP1 test staff availability/profile limitations; they
are not publication-ready actual-campus schedules. No protected administrative
time slots or lecturer lunch rules are invented. Solver constraints/objectives,
source team assignments and university workbooks remain unchanged.

## Files and usage

New source files: `pilot/demand_audit.py`, `pilot/scopes.py`,
`pilot/demand_cli.py`, `pilot/weekly_view.py`. Updated `pilot/pp1_run.py` adds
scheduled JSON, a separate independent-validation report, explicit scope notices
and a standalone interactive weekly group/lecturer/venue view. New synthetic
tests are in `tests/test_pp1_demand_audit.py`. Service README and this report
document the audit. No dependencies were added.

```powershell
.\.venv\Scripts\python.exe -B -m schedence_teaching.pilot.demand_cli --output services/teaching-service/local-data/pilot-previews/new-demand-audit --seconds-per-scope 120
.\.venv\Scripts\python.exe -B -m pytest services/teaching-service/tests -q
.\.venv\Scripts\python.exe -m pip check
```

`--audit-only` skips all native solves. Output directories must be new and remain
under ignored `local-data`. Scope selection is also reusable through
`select_complete_scope(batch, {(year, semester, stream, specialization), ...})`;
unknown/empty keys or stale expansion snapshots are rejected. Search budgets
can change explicitly; teaching windows and source demand do not change.

Private artifacts are in `local-data/pilot-previews/step12a31-reviewed/`:

- `audit/module-demand-audit.csv`: all 1,461 requirement rows with source row,
  all four original contact-hour columns, generated duration/frequency, and group.
- `audit/year4-sem1-it-weekend.csv`: all 40 target-scope occurrences.
- `audit/leaf-attendance.csv`: all 198 leaves and source/actual attendance totals.
- `audit/scope-capacity.csv`: all 128 year/semester/stream/specialization scopes.
- `audit/weekend-overloads.csv`: all 41 overloaded leaves, sizes and shortages.
- `audit/leaf-session-contributions.csv`: contributing modules/sessions; join on
  leaf group ID to audit each overloaded leaf without including sibling labs.
- `audit/calendar-scenario-comparison.csv` and `audit/demand-audit.json`.
- `baseline-feasibility.json` and `baseline-unscheduled-requirements.csv`: the
  complete unscheduled baseline request, preserving all 1,461 occurrences.
- Four `01-...` through `04-...` scope directories: HTML, scheduled CSV/JSON,
  native solver JSON, independent validation, all demand, unscheduled demand,
  explicit outside-scope demand, configuration, specialization pages and views.
  `weekly.html` provides selectable group/lecturer/venue calendar views.

Only private ignored outputs contain source-derived allocations/bookings. Source
staff names are not needed in diagnostics; timetables use original staff IDs.

## Verification

The complete Teaching Service suite passed **2,182 tests**: all 2,156 previous
tests plus 26 new audit/scope tests. `pip check` reported no broken requirements.
The existing Starlette/httpx deprecation warning remains. New tests cover source
row/code conflicts, exact ancestor attendance without sibling counting, missing
or duplicate requirements, continuous-hour interpretation, corrupted expansion,
altered applicability, omitted bindings, lunch applicability, time shortages,
complete cohort selection, preserved native contexts, invalid search budgets,
joint staff/venue/group validation and exports.

Before/after checks found no missing original files, all seven source workbooks
byte-identical, and all existing solver files and tests unchanged. The branch
remains `feat/teaching-timetable`; all changes remain local and uncommitted. No
Neon operations, migrations, source-workbook changes, branch changes, commits
or pushes occurred.
