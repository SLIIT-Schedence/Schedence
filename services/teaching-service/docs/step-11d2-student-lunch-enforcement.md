# Step 11D.2: hard fixed student lunch enforcement

This step enables the existing Step 11D.1 policy inside CP-SAT. It does not
add a lunch objective, move lunch times or introduce lecturer lunch rules.

| StudentGroup.academic_year (study year) | Fixed period |
| --- | --- |
| 1 | 12:30–13:30, [750, 810) |
| 2 | 12:30–13:30, [750, 810) |
| 3 | 13:30–14:30, [810, 870) |
| 4 | 13:30–14:30, [810, 870) |

## Explicit enablement and input validation

Existing callers remain disabled by default with unchanged assignment/objective
behavior and serialized output. Pass all three lunch arguments to enable:

```python
from schedence_teaching.lunch import confirmed_student_lunch_policy
from schedence_teaching.solver import StudentLunchEnforcementConfig, solve_timetable

lunch = {
    "student_lunch_policy": confirmed_student_lunch_policy(
        policy_reference="synthetic:fixed-lunch",
        week_reference=calendar.week_reference,
        provenance={"source": "project-owner-confirmed-periods", "reference": "year-specific-correction"},
        applicable_dates=set(calendar.day_dates.values()),
        applicable_streams={"WEEKDAY", "WEEKEND"},
        applicability_reference="synthetic:example-scope-only",
    ),
    "student_lunch_calendar": calendar,  # Existing PreferenceCalendar contract.
    "student_lunch_config": StudentLunchEnforcementConfig(
        applicability_confirmation="CONFIRMED",
        applicability_confirmation_reference="synthetic:example-confirmation-only",
    ),
}
result = solve_timetable(prepared, solver_config, **original_inputs, **lunch)
```

This example confirms only **synthetic** date/stream applicability. Real campus
weekday/weekend applicability remains outstanding. Reference strings record
supplied confirmation, not independent proof of university approval. No
institution/academic identifiers are generated. The new config contains only
enablement, confirmation and technical limits; all periods/scope remain in
the existing Step 11D.1 policy.

Missing policy/calendar/config, unknown date or stream scope, unconfirmed
applicability, inconsistent academic weeks/calendars, invalid group references
or study years, and changed fixed periods are rejected before native solving.
Enforcement checks the current periods against the existing confirmed factory;
a generic future one-hour policy cannot silently override that confirmation.
Explicit empty/excluded scopes remain exclusions. If no group dates apply,
the report has compliance null rather than a verified pass.

## CP-SAT constraints and actual attendance

For each possible physical delivery, consider its direct groups plus all
descendants that attend ancestor teaching. Use every attending group's
validated study year, stream and actual calendar date. Union distinct lunch
windows; never assume the first participant's window covers everyone. Parents
do not acquire all child labs and siblings remain independent. Existing
combination eligibility still requires a common study year/content/period and
disjointness evidence; cross-year combinations are not newly authorized.

Teaching `[s,s+d)` overlaps lunch `[a,b)` exactly when `s+d>a` and `s<b`.
For a grid of `g` minutes, the forbidden local start ticks are:

```text
floor((a-d)/g) + 1  through  floor((b-1)/g)
```

The model clips these bounds to the day, applies the existing day stride,
and subtracts them from the original start domain. Ending exactly at lunch
start or beginning exactly at lunch end is legal. A coarser grid changes the
available teaching starts, never the actual lunch period. Every session type
is included, and any positive overlap, including one minute, is forbidden.

At most one domain constraint is added per modeled delivery, conditional on
its existing presence literal when optional. There are **zero new lunch
variables, intervals, resource bookings or staff blocks**. Inactive alternatives
do not acquire a lunch restriction or consume resources. An empty allowed
domain prevents selecting that delivery; existing exact coverage must choose
another valid alternative or native CP-SAT returns INFEASIBLE. Requirements
cannot be dropped, shortened or credited twice.

Assessment and enforcement reuse shared scope/attendance helpers. Native tick
arithmetic remains separate from Step 11D.1's direct minute-overlap check.
Lunch does not establish campus attendance or teaching/admin/research credit.
No lunch booking is created on a date without teaching.

All original hard restrictions remain: hierarchy/population conflicts, staff
eligibility/availability/caps, approved fixed/flexible admin and research
protection, venue calendars/capacity/shared resources, and explicit hard
consecutive breaks. Fairness cohorts/targets keep their original input-derived
definition. Lunch may change the best feasible objective values but cannot
relax a hard rule. Delivery/contact/preference/fairness/consecutive tiers and
their proof/timeout behavior are unchanged; lunch is not a new objective tier.

## Independent validation and results

`validate_student_lunch_result` validates every original enabled context and
mode flag. Its private projection removes only the lunch wrapper, preserving
statuses, bookings, objective proofs and earlier reports for original validators.
It then calls Step 11D.1 assessment to regenerate actual group attendance and
overlaps. Applicable violations or unresolved scope are rejected. Complete
canonical report comparison includes policy/calendar/confirmation snapshots,
years, groups, windows, compliance and exact numeric types.

The evaluation gateway and delivery/staff/work-period/preference/fairness/
consecutive result validators dispatch lunch checks first. Workload, preference,
fairness, consecutive and lunch post-solve assessments preserve the original
lunch context; omitting it is an error. Raw-schedule validators alone do not
establish every enabled policy. Tests prevent CP-model construction during
validation and detect a deliberately removed native lunch constraint.

Enabled successful output adds `student_lunch_enabled` and `student_lunch`:
policy reference, enforced years, dates/streams, evaluated groups, fixed windows,
confirmation metadata, compliance, violations and applicability diagnostics.
The nested independent assessment accurately labels validation of an enforced
result. Disabled Step 11D.1 assessment keeps its INDEPENDENT_ONLY/false flags;
post-hoc assessment against a different policy cannot claim solver enforcement.
All outputs retain `university_ready=false`.

UNKNOWN/INFEASIBLE/MODEL_INVALID without an incumbent returns no timetable,
admin bookings, objectives or partial compliance report. Later timeouts can
retain an independently validated complete FEASIBLE incumbent without an
optimality claim. Fabricated lunch bookings cannot pass original booking checks.

## Native before/after demonstration

```powershell
.\.venv\Scripts\python.exe -B services/teaching-service/examples/student-lunch-enforcement-demo.py
.\.venv\Scripts\python.exe -B -m pytest services/teaching-service/tests -q
.\.venv\Scripts\python.exe -B -m pip check
```

The new demo uses the unchanged historical `integration_fixture.py`: identical
requirements, population proof, calendars, venues and existing optimization
features. Only explicit, confirmed **synthetic** lunch applicability is added.
CP-SAT produced this enabled timetable (room/group IDs have prefix `SYN-1-`):

| Date | Time | Staff | Room | Group(s) |
| --- | --- | --- | --- | --- |
| Monday 2026-10-19 | 09:00–09:45 | SYN-1-P | HALL | G1 + G2 combined lecture |
| Monday | 10:00–11:00 | SYN-1-I | LABROOM | G1-LAB |
| Tuesday 2026-10-20 | 09:00–09:45 | SYN-1-P | CLASSROOM | G2 |
| Tuesday | 10:00–11:00 | SYN-1-I | LABROOM | G2-LAB |
| Tuesday | 11:00–11:45 | SYN-1-D | CLASSROOM | G1 |
| Saturday 2026-10-24 | 09:00–10:00 | SYN-1-L | CLASSROOM | SEC |

Previously G2-LAB was Tuesday 12:00–13:00, overlapping Year 2 lunch by 30
minutes. Native scheduling now selects 10:00–11:00 and reorders the separate
lectures; there is no post-processing. Independent lunch compliance changes
false → true. All seven originals are covered once through six bookings, one
combined and five separate, totaling 315 teaching minutes. Preference penalty
remains zero; maximum/aggregate normalized target deviation remain `15/16` and
`559/160`; consecutive cost remains 30. Every objective stage is OPTIMAL with
the same vector `(6, 315, 0, 13500, 50310, 30)`. The Professor's separate
240-minute admin reservation remains reserved with zero completed-work credit.
Equivalent native schedules can differ; no real personal data is used.

An isolated Year 2 60-minute lab restricted to 12:00–13:00 is INFEASIBLE.
Extending explicit availability to 15:00 allows 13:30–14:30. Approved admin/
research protection or staff/venue availability removing that remaining time
returns INFEASIBLE rather than weakening another hard restriction.

## Bounded performance observation

One worker, seed 7, shared five-second native search budget per run; four
staff, five groups, seven originals, nine physical alternatives (including
two combined), three venues. One measured run, not a scalability guarantee:

| Measurement | Disabled | Enabled |
| --- | ---: | ---: |
| Status | OPTIMAL | OPTIMAL |
| Variables observed at native solve | 1,666 | 1,666 |
| Maximum constraints observed | 3,750 | 3,759 |
| Checked model preparation | 0.058 s | 0.060 s |
| Total native solving | 2.000 s | 1.934 s |
| Solve call including internal reporting | 2.235 s | 2.423 s |
| Additional independent validation | 0.081 s | 0.187 s |

The native-time difference does not establish a speed improvement. Independent
reporting/validation adds overhead. Isolated memory remains unmeasured. The
domain approach adds no variables and nine constraints here. Technical guards
default to 20,000 unique delivery/date/window rules and 200,000 model variables/
constraints; existing resource/model/integer guards still apply. These sparse
synthetic cases do not establish full-campus performance.

## Files and remaining campus inputs

Created `lunch/applicability.py`, `solver/lunch_inputs.py`,
`solver/lunch_constraints.py`, `solver/lunch_results.py`,
`solver/lunch_validation.py`, `tests/test_student_lunch_solver.py`, the new
demo and this report. Modified files (relative to `src/schedence_teaching`
unless otherwise indicated):

- `solver/engine.py`, `solver/results.py`, `solver/__init__.py`.
- `solver/evaluation.py`, `solver/delivery_validation.py`,
  `solver/staff_validation.py`, `solver/work_period_validation.py`,
  `solver/preference_validation.py`, `solver/fairness_validation.py`,
  `solver/consecutive_validation.py`.
- `workload/assessment.py`, `preferences/scoring.py`, `fairness/assessment.py`,
  `consecutive/assessment.py`.
- `lunch/assessment.py`, `lunch/results.py`.
- Service `README.md` and `docs/step-11d1-student-lunch.md`.

Eight files created, eighteen modified; no original test file or historical
fixture was modified. Hash comparison found no missing files or altered
source workbooks/local import reports; `local-data` remains ignored by Git.

Real scheduling still requires approved weekday/weekend/date applicability,
authoritative offerings/streams/groups/study years, counts/population evidence
and explicit weekly session demand; staff/resource calendars and combined-room
relationships; approved dated admin/research and preference/fairness/consecutive
policies. Confirmed lunch times do not fill these gaps. No lecturer lunch or
canteen rules are inferred. Campus attendance/completed work remain separate
evidence requirements. Travel optimization remains outside scope.

Verification: **1,906 tests passed in 71.34 seconds**, preserving all 1,804
previous tests and adding 102 tests. `pip check` passed. The existing
Starlette/httpx deprecation warning remains. Tests include all years/types,
boundaries/one-minute overlap, grid alignment, date/stream confirmation, group
hierarchy, combined/mixed/separate delivery, differing windows, original hard
protections/objectives, unknown/infeasible/feasible statuses, all assessment
gateways, corruption and CP-model-free independent validation. The standalone
before/after demo and bounded native comparison passed.

Stop after Step 11D.2 for review. No source Excel edits, database operations,
API/endpoints, other-service changes, branch changes, commits or pushes.
