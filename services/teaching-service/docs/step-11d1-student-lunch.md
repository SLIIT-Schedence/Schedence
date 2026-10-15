# Step 11D.1: confirmed fixed student lunch periods

Step 11D.2 now adds [optional native hard enforcement](step-11d2-student-lunch-enforcement.md).
The independent-only behavior documented here remains the default when
enforcement is disabled; an enabled result is separately labeled as enforced.

This correction replaces the previous general lunch-window proposal. It is
not flexible lunch-slot selection. The project owner confirms these campus
periods, each one continuous hour:

| StudentGroup.academic_year (study year) | Fixed lunch | Minutes from midnight |
| --- | --- | --- |
| 1 | 12:30–13:30 | [750, 810) |
| 2 | 12:30–13:30 | [750, 810) |
| 3 | 13:30–14:30 | [810, 870) |
| 4 | 13:30–14:30 | [810, 870) |

The calendar year (for example 2026) is never used as the study year. No
lecturer lunch period, campus opening hours, canteen capacity, crowd objective,
alternative lunch slots or weekday/weekend applicability is inferred.

## Contracts and explicit scope

Import from `schedence_teaching.lunch`:

- `StudentLunchPeriod`: existing strict `AcademicYear` and `TimeWindow` types;
  exactly one continuous 60-minute fixed window per record.
- `StudentLunchPolicy`: policy/week references, source provenance, explicit
  confirmation of the supplied periods, and exactly one period for each study
  year 1–4. Unknown fields (including flexible slots) are rejected.
- `confirmed_student_lunch_policy`: constructs the four confirmed values above.
  Callers supply their own policy/week references and provenance. It does not
  fabricate an authoritative policy identifier or institution ID.

`applicable_dates` and `applicable_streams` default to `None` (unresolved).
Setting either requires an `applicability_reference`. Explicit sets configure
the assessment; an empty set explicitly excludes all dates or streams. A
reference records the supplied configuration, not independent confirmation.
Period confirmation does not confirm a still-unknown stream/date scope.
The generic policy contract permits later explicitly versioned one-hour
periods; the confirmed factory retains the exact current supplied times.

Use the existing `PreferenceCalendar` to associate opaque scheduling-day IDs
with actual dates. Policy/calendar weeks must match, applicability dates must
lie within that week, and all enabled staff/work-period/preference/fairness/
consecutive calendars must agree. A date not configured for a group's stream
is not treated as a teaching or campus-attendance day for that group.

Example **synthetic applicability**, not a campus weekday/weekend declaration:

```python
from schedence_teaching.lunch import (
    assess_student_lunch, confirmed_student_lunch_policy,
)

policy = confirmed_student_lunch_policy(
    policy_reference="synthetic:fixed-student-lunch",
    week_reference=calendar.week_reference,
    provenance={"source": "project-owner-confirmed-periods", "reference": "year-specific-correction"},
    applicable_dates=set(calendar.day_dates.values()),
    applicable_streams={"WEEKDAY", "WEEKEND"},
    applicability_reference="synthetic:example-scope-only",
)
report = assess_student_lunch(
    prepared, solver_config, result, calendar, policy,
    **original_enabled_solver_inputs,
)
print(report.to_dict())
```

## Independent assessment only

`assess_student_lunch` first invokes the complete existing result-validation
gateway. It accepts only a complete valid FEASIBLE/OPTIMAL timetable and all
original enabled resource, combination, staff, work-period, preference,
fairness and consecutive inputs. Missing original contexts, corrupt coverage,
assignments/reports and unsuccessful results are rejected. No CP model is
constructed, no solver result is mutated and no sessions are rescheduled.

Every actual attended lecture, tutorial, workshop or lab is checked against
the group's fixed period. Intervals are half-open: ending exactly when lunch
starts, or starting exactly when lunch ends, is permitted. Every positive
overlap is retained with delivery identity, original occurrence coverage and
exact overlap minutes. Year 1/2 teaching until 12:30 is not rejected by the old
general-window assumption; Year 3/4 teaching at 14:00–14:30 is an overlap.

A subgroup attends ancestor teaching, including nested ancestors. Parents
do not inherit child labs, and siblings do not inherit each other's teaching.
Combined deliveries count once per attending group, with one unique physical
booking and preserved original occurrence coverage. Group/delivery violation
associations can exceed the number of violating physical deliveries; this
does not create extra lecturer/venue bookings or teaching workload.

Each group/date row reports one of:

| Status | Meaning |
| --- | --- |
| CLEAR | Scope explicitly applies and no scheduled attended session overlaps. |
| VIOLATED | Scope explicitly applies and attended teaching overlaps. |
| APPLICABILITY_UNKNOWN | Required date or stream scope is unresolved; observed overlaps remain visible, with no compliance claim. |
| NOT_APPLICABLE | Explicitly excluded date/stream, or that stream is not configured on the date. This does not establish attendance. |

Known violations make overall `compliant=false`; otherwise unresolved group
dates or no applicable group dates produce `null`. A complete applicable
assessment with no overlaps produces `true`, meaning **teaching-free during
the fixed period**, not proof that lunch was taken or campus attended. Even
a group with no teaching is not assigned a campus attendance day.

The immutable report includes canonical policy/calendar snapshots, physical
and original-occurrence counts, separate applicable/unknown/excluded counts,
observed overlaps, violation counts and diagnostics. Group details are opt-in.
`validate_student_lunch_report` independently regenerates the entire report
and rejects changed year/window/status/coverage/overlap/identity/provenance
or wrong numeric types. Independent-only reports with enforcement disabled state:

```text
assessment_mode = INDEPENDENT_ONLY
hard_lunch_constraints_enforced_by_solver = false
lecturer_lunch_assessed = false
lunch_consumption_verified = false
attendance_inferred = false
university_ready = false
```

## Corrected existing example

```powershell
.\.venv\Scripts\python.exe -B services/teaching-service/examples/end-to-end-scheduling-demo.py
.\.venv\Scripts\python.exe -B -m pytest services/teaching-service/tests -q
.\.venv\Scripts\python.exe -B -m pip check
```

The existing end-to-end example keeps all seven original requirements and
six physical bookings, with its previously implemented objective stages.
It now adds a separate lunch assessment using the confirmed periods and an
explicit **synthetic-only** date/stream configuration. The Year 2 child-group
lab on Tuesday 12:00–13:00 overlaps 12:30–13:30 by 30 minutes. Its native
OPTIMAL status describes existing solver constraints/objectives; the new lunch
assessment correctly reports noncompliance. No booking is silently shifted.

Historical timelines in earlier examples predate student lunch enforcement;
they are not demonstrations of lunch-compliant campus scheduling. No executable
test previously implemented a universal lunch-window rule; the incorrect
general proposal was documentation. It has been corrected in the README and
Step 11C audit while preserving all prior test files.

## Step 11D.2 boundary

Step 11D.2 should enforce the applicable fixed periods as **hard** CP-SAT
constraints over each selected physical delivery's true group attendance,
including descendants of direct groups. Requirements/combination alternatives
must not be removed to avoid lunch. Preserve all existing staff/venue/resource
protections and higher objective tiers, and independently validate lunch again
from the selected bookings. Unknown applicability must remain a readiness
blocker rather than becoming guessed weekday/weekend rules. Impossible lunch
restrictions must produce honest infeasibility, not silently moved lunch times.

No hard lunch constraints, new solver objective, lecturer lunch restriction,
data edit, database write, API, branch change, commit or push is part of Step
11D.1. Implementation stops for review before Step 11D.2.

Verification: **1,804 tests passed in 66.44 seconds**, preserving all 1,707
previous tests and adding 97 tests. Coverage includes every year and session
type, half-open boundaries and one-minute overlaps, the corrected afternoon
period for Years 3/4, nested attendance, combined/separate booking counts,
unknown/excluded weekday/weekend/date scope, complete existing optimization
contexts, corrupt inputs/reports, fresh imports and CP-model-free assessment.
`pip check` passed. The existing Starlette/httpx deprecation warning remains.
The updated standalone end-to-end CLI returned OPTIMAL for existing solver
constraints and lunch `compliant=false`, with one violating physical delivery
and preserved seven-occurrence coverage. File hashes confirm original tests,
solver code, workbooks and local import reports are unchanged; no files are
missing. `local-data` remains ignored by Git.
