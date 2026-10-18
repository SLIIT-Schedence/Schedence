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
`DELIVERY_COST_THEN_FAIRNESS_THEN_PREFERENCES` remains available without the
additional overage stage.
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
