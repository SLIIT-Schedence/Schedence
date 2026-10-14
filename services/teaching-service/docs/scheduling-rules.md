# Experimental Scheduling Rules backend

Scheduling Rules map to the existing CP-SAT contracts. They do not create a new
solver, store a configuration, or change an institution's approved policy.

`GET /api/v1/teaching/generation-inputs?semester=1&academic_year=1&stream=WEEKDAY`
continues to assess the existing snapshot settings. Its `scheduling_rules` report
includes the actual connected objectives, protected constraints, full objective
order, and explicitly experimental defaults. Defaults are suggestions; GET never
applies them. `/api/teaching` supports the same routes.

To validate a complete selection, POST a SchedulingRules JSON body to the same
URL. This read-only POST requires the existing institution-scoped
`teaching:generate` permission and the development readiness opt-in. For example:

```json
{
  "lecturer_preferences": {
    "morning_start_minute": 510,
    "afternoon_start_minute": 750,
    "afternoon_end_minute": 1110,
    "preferred_day_part_weight": 1,
    "not_preferred_day_part_weight": 1,
    "preferred_free_day_weight": 1,
    "weekend_pattern_weight": 1
  },
  "workload_fairness": true,
  "balance_student_daily_load": true,
  "balance_staff_daily_load": true,
  "consecutive": {
    "maximum_preferred_continuous_minutes": 240,
    "minimize_idle_gaps": false,
    "apply_to_lecturers": true,
    "apply_to_student_groups": false,
    "penalty_weight": 1
  }
}
```

Omitted switches default to false; omitted optional sections default to null.
An empty object disables these soft objectives. Numeric values and booleans are
strict; unsupported fields fail validation. Invalid combinations return
`ready: false` with a Missing scheduling-rules check. Owner records still have to
match the original snapshot before any settings are applied. A successful check
does not prove schedule feasibility or store a snapshot.

Lecturer weights use existing preference scoring; Not Available periods remain
hard blocks. Existing blocks are preserved even when soft scoring is disabled.
Additional blocks use the supplied day-part boundaries. If a historical snapshot
has Not Available records without numerical boundaries, disabling preferences
is rejected until explicit boundaries are supplied. Empty or inapplicable soft
preferences do not create a preference objective. Lunch, eligibility, venue
constraints, attendance, availability, workload targets/caps, and protected work
periods retain the prepared snapshot's contracts.

Fairness uses each member's own positive teaching target. Daily load uses the
existing student/staff minimax criteria. Continuous-run and idle-gap costs use
the existing time-indexed consecutive formulation. Enabled quality objectives
follow the tested order, after the protected delivery/workload prefix:
student daily load, staff daily load, existing thin-day compactness, consecutive
penalty, preference penalty, aggregate target deviation. Unsupported conflicts
are reported without silently changing policy.

The existing `POST /generations` request now accepts an optional
`scheduling_rules` object with the same shape. It is bound after owner validation
and scope selection, and the worker reconstructs that exact detached document.
Original snapshot/calendar/policy references remain required. Omitting rules
preserves historical request digests and behavior. Explicit rules require
`MONOLITHIC`; sealed institution policies and confirmed hard minimum-break
policies cannot be experimentally replaced. The existing solver and independent
validators remain responsible for execution and assessment.

The Generate Timetable wizard now includes Scheduling Rules between Academic
inputs and Check readiness. It loads experimental defaults from GET readiness,
validates edits with the read-only POST, supports Reset, and carries the selection
in the wizard URL through navigation and reloads. Unsupported controls are
disabled. Lecturer Preference Forms are not integrated; scoring consumes only
the already stored backend records. Final submission rechecks readiness and
includes the exact selected rules with the existing stored snapshot references.

Readiness does not itself store a snapshot. A suitable existing stored snapshot
and enabled worker are required; sealed institution policies remain protected.
The optional [isolated UI preparation](isolated-ui-generation.md) exposes newly
validated stored inputs without fabricating timetable history or running CP-SAT.
Readiness alone does not submit a generation job.

Focused offline validation (the test fixtures block CP-SAT construction/solving):

```powershell
.venv/Scripts/python.exe -m pytest services/teaching-service/tests/test_scheduling_rules.py services/teaching-service/tests/test_generation_readiness.py -q -p no:cacheprovider
```

An end-to-end generation run remains untested in this change; run it only when
explicitly authorized against the intended environment.
