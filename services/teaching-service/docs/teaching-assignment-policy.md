# Corrected teaching assignment policy

The old module-member pool was not a session-type permission. The new
`TeachingAssignmentPolicy` is explicit, versioned project policy, independent
of Excel source values, Identity software permissions and campus attendance.

The initial university's explicitly selected rank table is Instructor: LAB; Assistant Lecturer,
Lecturer and Senior Lecturer: LECTURE/TUTORIAL/WORKSHOP; Dr., Assistant Professor
and Professor: LECTURE. HOD/Dean appointments restrict delivery to LECTURE.
The generic contract requires explicit `rank_permissions`, `appointment_permissions`
and `institution_teaching_day_limit`; it supplies no university defaults.
`initial_university_assignment_policy` is an optional, explicit preset outside
the generic solver. The resolved snapshot carries policy provenance. Optional
dated, staff-wide or offering-specific source permissions may further restrict
it; they cannot grant module membership or bypass rank restrictions/availability.
Exact source-position-to-band mappings already supplied in the PP1 snapshot are
preserved. Names and arbitrary title prefixes never supply a missing rank.

Candidate generation intersects module membership, resolved rank/appointment,
session permission and applicable dates. Staff availability/protected periods,
venues, lunch, roster/hierarchy coverage and all other existing constraints
remain in force. Combined alternatives must satisfy the policy for every covered
requirement. Independent validation checks source records and actual bookings,
without consulting CP-SAT start domains or modeled assignment choices.

The initial university preset supplies `institution_teaching_day_limit=6`.
The generic contract accepts a configured limit from one to seven; no six-day
constant is embedded in CP-SAT. Staff, explicit availability and appointment limits can
restrict it further. Dates are unioned across streams and delivery modes; a
Professor attendance exemption does not authorize seven teaching days. No six-day
teaching minimum is inferred from campus attendance. New day indicators link to
actual selected deliveries, so combined coverage does not multiply days/minutes.

## Targets, caps and optimization

An instructor may teach lab occurrences for multiple eligible modules, including
four different modules. Assignment variables belong to actual deliveries, not a
single exclusive module per instructor. Repeated weekly sessions and multiple
groups may therefore create several lab deliveries for one module. The approved
initial university policy still permits instructors to teach LAB only and gives
a soft weekly teaching target of 1,200 minutes. Actual assigned durations determine
workload; module counts never determine teaching hours.

Workload JSON and summary CSV expose separate distinct module-offering, lab-offering
and lab-delivery counts. Offering IDs are used because module codes are not unique;
this is not a claim that separate offerings always represent different authoritative
catalog modules. Counts for a filtered view are separate from full-version counts.
Combined coverage is deduplicated for counts and actual delivery minutes.

Four is not a maximum: there is currently no module-count cap contract or solver
constraint. A future confirmed maximum must be explicitly configured in the
institution policy snapshot with its module identity/counting basis and validated
independently. Neither an instructor's module-team membership nor permission to
teach labs overrides availability, non-overlap, explicit time caps or teaching-day
limits. This update does not add an institution-wide module cap.

Existing separate teaching targets and HOD/Dean override rules are unchanged.
The strict policy requires explicit target optimization unless deliberately
configured for a labelled feasibility diagnostic. The existing exact fairness
optimizer models `workload`, target-normalized `utilization` and absolute target
deviation variables. Minimizing the maximum deviation and then aggregate
deviation penalizes over-allocation and imbalance without imposing a target cap.
The explicit `DELIVERY_COST_THEN_OVERAGE_FAIRNESS_PREFERENCES` priority retains
delivery cost priorities, then minimizes total teaching-target overage, maximum
normalized deviation, aggregate normalized deviation and existing soft preferences.
`DELIVERY_COST_THEN_PEAK_OVERAGE_TOTAL_OVERAGE_FAIRNESS_PREFERENCES` adds one
earlier stage: it first minimizes the largest individual overage,
`max_i max(0, load_i - target_i)` in integer minutes, and then the total overage
under that proven peak. Constant delivery stages are recorded without a native
solve, and each proven stage seeds the next stage's nonbinding hints. Both
overage stages are soft objectives, not hard minute caps.
`DELIVERY_COST_THEN_FAIRNESS_THEN_PREFERENCES` remains available without the
additional overage stage.

### Opt-in teaching-day compactness (P2.1)

`SolverConfig.teaching_day_compactness` adds one staged criterion,
`THIN_TEACHING_DAYS`. It minimizes the number of staff-days whose assigned
teaching minutes are greater than zero but strictly below an explicitly supplied
`preferred_minimum_daily_teaching_minutes`. Days without teaching are never
penalized.

- **No default minimum.** There is no default value. The 90 minutes used in
  tests is a labelled synthetic example, not campus policy.
- **Reuses existing variables.** It uses the per-delivery staff-day and
  active-date variables that already enforce the teaching-day limit. A combined
  delivery counts once.
- **Placement.** The stage never reorders existing criteria:
  - `AFTER_WORKLOAD_STAGES` runs after every fairness stage and before
    preferences.
  - `BEFORE_AGGREGATE_TARGET_DEVIATION` runs after the proven peak overage,
    total overage and maximum-deviation stages. Aggregate deviation is then
    optimized only among compact schedules. This is a deliberate research
    trade-off.
- **Earlier optima kept.** Earlier proven optima are fixed before this stage
  runs.
- **No daily cap.** No hard daily cap is added. Compacting can raise daily
  minutes and continuous teaching, so the result reports the daily-minute
  distribution and maximum.
- **Not yet combinable.** It cannot yet be combined with consecutive or
  preference-only optimization.

### Opt-in daily workload distribution (P2.2)

`SolverConfig.daily_load_distribution` adds pure minimax stages in an explicit
order (`criteria`). There are no thresholds, targets or hard daily caps.

- **`MAX_STUDENT_DAILY_MINUTES`.** For every lowest-level student group and
  configured date, the load is the minutes of every delivery it attends: its
  own and every ancestor's. Each delivery counts once per group, even when it
  names several groups.
  - Roster overlaps between unrelated groups still prevent clashes. They are not
    merged into one student's daily load.
- **`MAX_STAFF_DAILY_MINUTES`.** It reuses the P2.1 staff-day minutes, which come
  from the existing teaching-day variables.
- **Redundant links.** These links are valid and do not change feasibility. They
  give CP-SAT usable bounds:
  - each delivery lies on exactly one stream day;
  - each group's or staff member's daily loads sum to its weekly minutes.
- **Placement.** Placement uses the same two anchors as P2.1. When both share an
  anchor, `relative_to_compactness` must state their order; it is never
  inferred.
- **Conflict with compactness.** Spreading load evenly and avoiding thin days
  conflict, so the stated order decides the outcome.
- **Reported status.** If any P2.1/P2.2 stage stops FEASIBLE after proven
  fairness stages, the result is FEASIBLE. The fairness report keeps its own
  proof flag.

### Scalable consecutive-teaching formulation (P2.3)

`ConsecutiveOptimizationConfig.formulation` selects one of two exact
formulations of the existing consecutive-teaching objective. The policy contract
and its criteria are unchanged.

- **`SUCCESSOR_CIRCUIT`** is the default and the original model, and it is left
  out of the serialized config. It builds one chronological circuit per
  attendee, with a successor arc for every candidate pair. Its node and arc
  preflight limits still apply.
- **`TIME_INDEXED`** builds per-date occupancy on the granularity grid, from
  shared "delivery starts at tick v" literals:
  - **Back-to-back:** a same-stream end and start at the same boundary.
  - **Idle slot:** a free slot whose previous and next occupied slots are the
    same stream.
  - **Short break:** the gap after an end, weighted over the next start, with
    `Σ_k w_k [gap ≤ k slots]`.
  - **Runs:** contiguous same-stream occupancy.
  - **Hard break:** a minimum break that is explicitly enabled forbids a
    same-stream start within that many slots.

  Its size is linear in candidate start times, and it builds no successor arcs.
  Every delivery stays in the model, including online lectures, which keep their
  configured stream and date. Both formulations reproduce the Step 11A sequence
  metrics exactly, and the independent validator checks every solve.
- **Same semantics.** Mode does not change scoring. Cross-date neighbours and
  stream changes never form a scored gap, as before. No lecturer lunch rule is
  added.
- **Size trade-off.** The grid is larger than the circuit for small attendees,
  and smaller and faster for large ones.
- **Not yet combined.** Consecutive optimization is still rejected together with
  P2.1/P2.2 quality stages until their combined ordering is approved.

### Lecturer preferences from stored records (P2.5)

`PP1Configuration.lecturer_preference_settings` (`LecturerPreferenceSettings`) is
one institution's versioned interpretation of its stored
`academic.lecturer_preferences` rows. It is omitted from the saved configuration
when unset, and the legacy behaviour is then unchanged.

- **Settings:** morning and afternoon boundaries, a weight for each preference
  kind (0 disables a kind), `not_available_handling`, the applicable streams,
  and the weekend-pattern translations.
- **Category handling.** It reuses the existing adapter and the existing
  `TIME_OF_DAY`, `WORKING_DAY` and `WEEKEND_DAY` preference objective:
  - **Preferred** becomes a weighted soft PREFER. On that day, bookings outside
    the preferred parts cost the weight.
  - **Available** is neutral and adds no term.
  - **Not Preferred** becomes a weighted soft AVOID; the period stays usable.
  - **Not Available** becomes a hard staff blocked period for that day part on
    every configured date of that weekday. It is enforced in the lecturer's
    start domain and rechecked by the independent validator
    (`staff_blocked_periods`).
  - **Preferred_Free_Day** becomes a weighted working-day AVOID, and
    **Weekend_Work_Pattern** a weighted weekend PREFER.
  - **Can_Work_Both_Weekend_Days** and **Expected_Working_Days** are only
    cross-checked for conflicts.
- **When nothing applies.** Soft preference optimization is attached only when
  the solver's own checker finds an applicable criterion. Hard blocks always
  apply.
- **Clear failures, nothing relaxed.** An occurrence with no eligible lecturer
  available fails before solving and names the occurrence. Preparation metadata
  lists the same `NO_AVAILABLE_ELIGIBLE_LECTURER` diagnostics.
- **Database generation.** Generation rebuilds the soft preferences, scoring
  policy and hard blocks from the current Neon rows, and requires an exact match
  with the sealed snapshot.

**How "Preferred" is scored on the same day (unchanged).** All Preferred parts
of one staff member's day form one alternative group. A booking on that weekday
is satisfied if it fits inside any preferred part, and costs the weight
otherwise. With Monday morning and Monday afternoon both preferred, any booking
within those windows is satisfied; they are never competing obligations. Days
with no Preferred part are never penalized, so Available is neutral.
Not Preferred parts are separate AVOID units, each charged when a booking
overlaps them. Each booking is charged per violated unit.

### Combined objective stage plan (P3.1)

`SolverConfig.objective_stage_plan` (`ObjectiveStagePlan`) is opt-in and sealed
with the institution policy; its `plan_reference` must match the revision. It
runs every enabled objective in one deterministic lexicographic solve.

- **Protected prefix.** The existing delivery and Priority 1 order is kept
  exactly, up to the last protected criterion (deliveries, contact minutes, peak
  and total overage, maximum deviation).
- **Explicit order for everything else.** Every other enabled criterion is
  listed once in `order`:
  - the maximum student and staff daily minutes, and thin teaching days;
  - the consecutive and preference penalties;
  - aggregate deviation.

  A plan is rejected if it:
  - omits or adds a criterion;
  - reorders the configured daily-load criteria;
  - contradicts a P2.1/P2.2 placement or `relative_to_compactness`;
  - uses a priority mode that places preferences inside the protected prefix.
- **Validation by projection.** Fairness and consecutive reports and validators
  use the plan projected onto their own criteria; their arithmetic and checks
  are unchanged.
- **Reporting.** The result carries an `objective_stage_report` with:
  - the exact order and each stage's status and value;
  - the proven-stage count and the model size.

  An unfinished later stage leaves earlier proofs intact, and the overall result
  is OPTIMAL only when every planned stage is proven.
- **Independent validation.** `validate_combined_result` reruns the existing
  validators, which cover hard constraints, batch online days, staff blocks,
  fairness, consecutive scoring and preferences. It also recomputes P2.1/P2.2
  and every stage value from the final schedule.
- **Without a plan,** behaviour and restrictions are unchanged.

### Combined plans in database-backed generation (P3.2)

The existing `POST /generations` workflow carries a sealed institution
configuration from the database to the CP-SAT solve. The P2.5 settings live in
`PP1Configuration`; P2.1–P2.4 and the P3.1 plan live in `solver_config`. No new
endpoint, connection layer or table is involved.

The path runs: owner-row reader → `reconstruct_enrollment` (which verifies P2.5
against the stored `academic.lecturer_preferences` rows) → scoped snapshot →
isolated worker → `prepare_attempt` → persistence → read APIs.

- **Sealing at capture.** `capture_pp1_snapshot(..., seal=...)` seals a
  newly captured snapshot with the institution revision, so policies that
  require a seal can be captured at all.
- **Scoped snapshots are re-sealed.** A scoped generation snapshot is re-sealed
  with the parent's own approved revision (`rebind_scoped_snapshot`). Every
  rule, policy context and assignment policy must be identical. Two scope-driven
  differences are allowed, and nothing else:
  - attendance and venue scope;
  - the existing drop of preference contexts, together with exactly the
    `PREFERENCE_PENALTY` stage, when no preference criterion applies in the
    scope.

  Parents with staff policy overrides are refused until a scoped revision is
  separately approved.
- **The publication gate.** `prepare_attempt` uses `validate_combined_result`
  whenever a plan is present. FEASIBLE results may be stored as validated drafts
  and always report their unfinished stages. INFEASIBLE and UNKNOWN results are
  never stored.
- **Reporting.** Development jobs expose an `optimization` stage summary. The
  existing validation endpoint returns the stored stage history from the run
  metadata.
- **Excess-continuous lower bound.** Consecutive scoring adds the valid
  redundant bound `excess(run) >= sum(max(d_i - M, 0))` in both formulations.
  It changes no optimal value and lets CP-SAT prove optimality where the plain
  relaxation reported a bound of 0.
Other fairness priorities retain their original order. Maximum teaching minutes
remain separate authoritative hard constraints.

`prepare_policy_preview` starts from the corrected 631-occurrence foundation
snapshot, retains every requirement, contact hour and attendance membership, and
uses one global model across Years 1–4 and both streams. None of the 525 other-year
bookings are frozen. Optional compatible old `ScheduledSession` values may be
passed as nonbinding `schedule_hints`; those hints add no constraint, can be
abandoned by CP-SAT, and are never treated as assignment evidence.

The separate `hypothetical_target_caps=True` diagnostic uses copied inputs and
labels the experiment in provenance. It never updates a campus policy or stored
version. The corrected scope has 422 lab hours and 20 instructors with a combined
400-hour weekly teaching target. Lab-only instructor eligibility therefore proves
that the hypothetical target-as-cap scenario cannot cover all demand: at least
22 hours above instructor targets are unavoidable without an approved change in
academic requirements or eligible staffing. This does not justify inventing caps
or permitting academic staff to teach labs contrary to the confirmed policy.

## Historical versions and reporting

New optional policy/day-limit fields are omitted when absent during serialization,
preserving historical snapshot JSON and hashes. Old schedules remain reproducible
under their original policy. This is not current-policy approval: workload
responses now distinguish `validation_basis=STORED_VERSION_POLICY` from
`current_teaching_policy`. They identify saved database versions, the corrected
foundation local preview and new policy replacement previews separately.

The new local preview requires an explicit resolved assignment policy.
Existing API generation retains its explicit `HISTORICAL_SNAPSHOT` reproduction
semantics; this change does not upgrade historical API inputs automatically.
Selecting current institution-policy snapshots for those APIs requires a separate
reviewed integration. Production execution remains disabled by existing gates.
No production input or timetable version is rewritten.

The five missing underlying ranks remain a real data gap: LEC001 (Dean),
LEC002 (Head - CSE), LEC003 (Head - CSSE), LEC004 (Head - IT) and LEC005
(Associate Dean). The authoritative database rank fields are null, and the
restricted reader cannot access appointment history. No title parsing, arbitrary
rank substitution, appointment dates or database grants have been introduced.
These staff are excluded from new assignment candidates until resolved.

Local outputs, historical audits, solver statuses, hypothetical-cap evidence,
independent validation and comparisons are under the ignored
`local-data/teaching-policy-correction/`. Historical 697-delivery database records
and the earlier 631-delivery local preview remain intact. Source Excel data,
calendars, CP-SAT hard constraints unrelated to this policy, and other services
are not modified.

## Bounded feasibility diagnostic

The continuation preserved both earlier optimized UNKNOWN attempts, including
the 600-second attempt, and ran a separate feasibility-only snapshot. It disabled
soft target and preference optimization, retaining all 631 original occurrences,
eligibility, availability, explicit caps, teaching-day limits, student hierarchy,
venue restrictions, lunch and teaching calendars. The 384 compatible historical
bookings were hints only.

Stage 1 returned **UNKNOWN**, with no incumbent: 0/631 scheduled. Native solve
time was 371.595 seconds (383.897 seconds including preparation); the configured
budget was 360 seconds. The model had 50,432 variables and 118,418 constraints.
This is not an infeasibility proof and independent validation is NOT_ASSESSED.
Workload optimization and replacement timetable/PDF generation were not started.
Every occurrence has a candidate; all 211 lab occurrences have just two eligible
instructors each. These narrow resource domains and the large joint placement
model warrant further search/model diagnostics; no conflicting hard-constraint
set has been proved for the actual soft-target scenario.

The separate hypothetical-cap certificate solves a lab-assignment relaxation
without calendars or venues. Its INFEASIBLE result proves only that treating
all instructor targets as hard caps cannot accommodate 422 lab hours within
400 hours. It does not prove the actual soft-target scenario infeasible.

Existing campus workload and lunch factories and the PP1 adapter remain explicit
initial-university presets. They are not generic solver constants. The existing
study-year, semester and rank enum vocabulary remains an upstream input-contract
scope limitation; this correction does not redesign those contracts.
