# Step 11C: integration audit and readiness review

Audit date: 2026-10-15. All people, groups, offerings, calendars, approvals and
weights in the executable examples are explicitly **synthetic**. Local Excel
sources were inspected through existing read-only importers; none was changed.
The result is an integration demonstration, not a campus timetable or evidence
that all university workload/attendance requirements have been fulfilled.

## Implementation review

| Component | Audited behavior and integration boundary |
| --- | --- |
| Contracts and preparation | Existing Pydantic contracts, required IDs, study years 1–4, semesters 1–2, explicit streams/durations, references and hierarchy are reused. Source occurrences remain intact. No academic master database is created. |
| Eligibility | Candidate pools come only from supplied offering teaching teams. LIC/Co-LIC must be academic; instructors retain their type and need no lecturer preference record. Roles are eligibility, not confirmed session assignments. Session-specific rank/qualification restrictions require additional explicit authoritative rules. |
| Groups and combinations | Same content, specialization, period, frequency, duration and common staff eligibility plus positive population evidence permit optional combinations. Unknown overlap is not disjointness. Exact coverage chooses one fulfillment per original occurrence. |
| Hierarchy | A child attends ancestor teaching; parents do not acquire every child lab and siblings do not acquire each other's attendance. Physical combined deliveries are deduplicated per attendee. Unsupplied cross-group/repeat-student overlap remains unknown. |
| Venues | Explicit type/capacity/eligibility, calendars, blocks and shared physical resource relationships are hard constraints. Inventory confirmation does not establish availability. Unresolved combined spaces cannot silently become independent bookable rooms. |
| Staff calendars/caps | Explicit dated availability, blocked periods and supplied daily/weekly teaching caps apply across the single global resource model. They remain hard constraints under every objective. |
| Workload | Physical teaching minutes are counted once, separately from recognized administrative/other work. Targets are explicitly reported and normalized for fairness; they are not silently maximum-only limits. Target equality/minimum fulfillment is not an additional hard solver constraint. |
| Administrative/research protection | Approved applicable fixed periods and approved required flexible reservations retain hard protection. Reservations allocate time, not completed-work credit or evidence of attendance. Pending/proposed periods are not promoted to hard blocks. |
| Preferences | Confirmed, applicable explicit synthetic weights are used; instructor records are not fabricated. Avoided periods stay soft unless separately supplied as hard availability. |
| Fairness | Explicit role-aware cohorts and separate teaching targets feed exact rational maximum and aggregate target-deviation tiers. A valid optimum may still leave staff below target if supplied demand is insufficient. |
| Consecutive teaching | Selected physical deliveries, chronological successors, same actual date/stream gaps and maximal runs reproduce Step 11A scoring. Lecturer and group costs are independent, with descendant attendance preserved. Soft breaks remain soft; hard rules need explicit confirmation and enablement. |
| Priorities/status | Existing D/T/preference/maximum-fairness/aggregate-fairness/consecutive order is retained. Only optimal stages are fixed. A later timeout can retain a complete validated incumbent as FEASIBLE; unproven tiers are never reported optimal. |
| Validation | The full evaluation gateway dispatches all original component validators and independently regenerates metrics from bookings. Lower-level raw-schedule validators alone do not establish every enabled policy/report check. Consumers must retain every original input context. |

No new scheduling objective, constraint policy, solver refactor or API was
introduced. The only production correction is stale `SolverResult` limitation
text: fairness is implemented as an optional mode; travel is out of research
scope; hard student lunch enforcement remains unimplemented (Step 11D.1 now
provides independent assessment). The obsolete fairness-specific
string replacement was removed. Solver constraints and objectives are unchanged.

Genuine boundaries are retained: one responsible staff member per physical
delivery (no simultaneous team teaching), no inferred student membership,
explicit upstream appointment/policy resolution, no persistent bookings, and
no automatic promotion of an accepted development dataset to authoritative
academic records. Flexible reservation integration is exercised separately
with the same complete objective configuration.

## Runnable synthetic academic week

From the repository root:

```powershell
.\.venv\Scripts\python.exe -B services/teaching-service/examples/end-to-end-scheduling-demo.py
.\.venv\Scripts\python.exe -B services/teaching-service/examples/scheduling-benchmark.py
.\.venv\Scripts\python.exe -B -m pytest services/teaching-service/tests -q
.\.venv\Scripts\python.exe -B -m pip check
```

`examples/integration_fixture.py` builds the inputs with the existing contracts.
The small case contains three offerings, a Professor/Dr./Lecturer/instructor,
two Computing main groups with explicitly disjoint populations and one supplied
lab child each, and a Security weekend group. Two weekly 45-minute lectures
per main group, two 60-minute labs and one 60-minute Security lecture give
seven original occurrences. Supplied sizes are 20/20, 10/10 and 15; no complete
partition of parent groups into labs is claimed. Three distinct independently
bookable synthetic rooms have capacities 50/25/15 and different venue types.

Dates are Monday 2026-10-19, Tuesday 2026-10-20 and Saturday 2026-10-24 within
one academic week. Weekday and weekend streams have explicit windows; resource
calendars are never inferred from a day name. The large hall has only one
45-minute window, deliberately forcing mixed lecture delivery. Staff blocks,
venue blocks, teaching caps, a Professor's approved Monday 10:00–14:00
administrative reservation and a Dr.'s approved Tuesday 09:00–11:00 research
protection are explicit synthetic evidence.

The native small result proved every stage optimal:

| Date | Time | Staff | Room | Attending group(s) | Delivery |
| --- | --- | --- | --- | --- | --- |
| Monday | 09:00–09:45 | SYN-1-P | HALL | G1 + G2 | Combined lecture, 40 students |
| Monday | 10:00–11:00 | SYN-1-I | LABROOM | G1-LAB | Lab, 10 students |
| Tuesday | 09:00–09:45 | SYN-1-P | CLASSROOM | G1 | Separate lecture, 20 students |
| Tuesday | 11:00–11:45 | SYN-1-D | CLASSROOM | G2 | Separate lecture, 20 students |
| Tuesday | 12:00–13:00 | SYN-1-I | LABROOM | G2-LAB | Lab, 10 students |
| Saturday | 09:00–10:00 | SYN-1-L | CLASSROOM | SEC | Separate lecture, 15 students |

All room/group names in this table have the `SYN-1-` prefix in the actual
output. Equivalent booking/occurrence orders may differ. Independent coverage
is exactly seven original occurrences, through six physical deliveries (one
combined, five separate), with **315 physical teaching minutes** versus 360
original group-demand minutes. Every original occurrence is covered once.

Preference penalty is zero. Maximum normalized teaching-target deviation is
`15/16`, aggregate deviation `559/160`; exact scaled fairness objectives are
13,500 and 50,310. Configured consecutive penalty is 30, caused by the two
explicitly scored 15-minute child-group gaps. Both lecturer and group metrics
are recomputed independently. All six objective stages are OPTIMAL.

The Professor has a 360-minute teaching target and a separate 240-minute
administrative allocation. The 240-minute reservation is reported fully
reserved, with **zero verified completed administrative minutes** and completion
NOT_VERIFIED. All staff in this deliberately partial-demand example remain
BELOW_TARGET. Regular academic attendance remains NOT_VERIFIED; the Professor
is exempt from compulsory campus-day attendance. Teaching days do not establish
campus attendance, and an OPTIMAL scheduling status does not establish workload
fulfillment, lunch provision, approval for publication or university readiness.

## Independent checks and negative integration tests

Tests jointly exercise all enabled contexts, immutable source snapshots,
combined/separate/mixed delivery, lab types/capacities, hierarchy attendance,
hard protection, staff caps, preference scoring, exact fairness and consecutive
costs. They independently inspect physical minutes, capacity and clashes as
well as calling the full validation gateway. A guard test prevents the
independent validator from constructing a CP model.

Targeted failure cases include unavailable/undersized venues, no eligible
teaching pool, insufficient combined staff caps, conflicting staff availability,
contradictory population proof, overlapping approved admin/research protection,
missing profiles/weights/approvals/venue blocking declarations, and impossible
explicit hard breaks. Unknown population evidence disables combining while
preserving separate demand. A completely unavailable fairness comparison cohort
is rejected as invalid policy input; it is not redefined or silently dropped.

Native UNKNOWN/MODEL_INVALID without an incumbent emits no schedule, validation
summary, objective or assessment reports. INFEASIBLE is also empty. Controlled
FEASIBLE and later UNKNOWN cases retain a complete independently validated
incumbent, without an optimality claim. Evaluation of unsuccessful outputs is
rejected. Corruption tests cover coverage, capacity metadata, staff IDs,
stream/day placement, admin/preference/fairness/consecutive reports, objective
history, mode flags and validation summaries. Calendar mismatches for each
objective component are rejected.

## Bounded benchmark observations

One worker, seed 7, same machine, no memory profiler or exhaustive scaling run.
The medium case is three independent copies of the complete small scenario;
it has sparse cross-cohort interactions and is **not** a dense campus stress
test. Combined occurrence alternatives are optional physical candidates, in
addition to the separate original alternatives.

| Measurement | Small | Medium |
| --- | ---: | ---: |
| Staff (academics + instructors) | 4 (3 + 1) | 12 (9 + 3) |
| Student groups, including children | 5 | 15 |
| Original occurrences | 7 | 21 |
| Combination patterns / occurrence alternatives | 1 / 2 | 3 / 6 |
| Total physical delivery alternatives | 9 | 27 |
| Venues | 3 | 9 |
| Maximum variables observed at native solve | 1,666 | 4,990 |
| Maximum constraints observed at native solve | 3,750 | 11,233 |
| Source/contract preparation | 0.011 s | 0.005 s |
| Checked model preparation | 0.086 s | 0.163 s |
| Total native solving | 2.114 s | 7.981 s |
| Solve call including internal reporting | 2.400 s | 8.569 s |
| Additional independent validation | 0.079 s | 0.175 s |
| Shared native search budget | 5 s | 8 s |
| Result | OPTIMAL | FEASIBLE |
| Selected physical deliveries | 6 | 18 |

The medium run proved delivery count, contact minutes, preference penalty and
maximum fairness deviation optimal, then returned UNKNOWN in aggregate
fairness. It retained the prior complete incumbent with overall FEASIBLE;
consecutive optimization was not reached. The model contains that objective,
but this run does not demonstrate its optimization. Per-stage results are
emitted by the benchmark; status, runtime and number of reached stages depend
on machine load. There is no guarantee of reaching all tiers within a fixed
budget. Model construction, contract preparation and independent validation
are measured outside the native search budget. Internal result validation
adds work beyond the reported native solve time.

Memory is explicitly null: isolated native/Python peak memory was not measured
reliably. No RSS estimate is mislabeled as model memory. Instrumentation patches
the native solve method only inside a single-threaded example context and
restores it afterward; no production solver instrumentation was added.
Existing bounded circuit/node/integer/model-size guards remain. These modest,
sparse examples do not establish real-campus performance or memory safety.

## Real-data readiness matrix

Fresh read-only imports of the local mappings reproduce the existing saved
report aggregate counts and warnings. The corrected venue workbook checksum
matches its separate confirmation metadata. No source report was overwritten.
The matrix contains only aggregate data and no private staff/preference rows.

| Input | Classification | Available evidence and remaining requirement |
| --- | --- | --- |
| Venue identity/type/capacity/location | Confirmed and usable for development inventory | 62 campus-confirmed development venues: 31 lecture halls, 10 smart classrooms, 19 computer labs, 2 BYOD labs. Zero missing locations. Original status labels and corrected-copy provenance remain intact. Confirmation covers inventory, not calendars. |
| Weekly teaching policy | Confirmed configurable policy; applicability incomplete | Instructor 20h, Assistant Lecturer 18h, Lecturer 16h, Senior Lecturer 12h, Dr. 8h, Assistant Professor/Professor 6h, HOD/Dean 4h. Requires authoritative staff-to-policy classification, dated scope and upstream appointment resolution. HOD/Dean are overrides, not rank replacements. |
| Administrative allocation | Confirmed configurable policy; reservations missing | Professor/HOD/Dean have a separate 4h weekly allocation, without extending the rule to Assistant Professors. No real dated reservations or completion evidence supplied. |
| Campus attendance | Confirmed distinction; evidence incomplete | Regular academics generally require six campus working days; Professor/HOD/Dean exemption does not remove teaching targets. Assistant Professor is not automatically exempt. Attendance evidence and applicable calendars are missing; instructor attendance policy is unspecified. |
| Staff identities/classifications/departments | Available but incomplete; requires university confirmation | 110 accepted development rows: 90 academics and 20 explicitly demo instructors. IDs are preserved; duplicate and ID/name-conflict checks report zero. 50 department affiliations use owner-supplied mapping, 40 are provisional, 20 are demo placeholders. These are not independently verified official records or authoritative dated policy profiles. |
| Teaching teams/eligibility | Available development candidates; requires confirmation | 1,288 assignment entries across 184 source module rows; all reference supplied staff. 1,076 academic and 212 instructor entries. Candidate eligibility is not an assigned physical session or independently verified qualification. Existing assignments are preserved. |
| Offering IDs | Missing | All 184 module rows lack authoritative offering IDs. Source-row identities are diagnostics only. Two distinct IT2100 records remain separate. |
| Academic year/semester/specialization | Available but incomplete | Source fields validate under supplied 1–4/1–2 mappings. They do not identify a unique offering or establish student membership. |
| Weekday/weekend offering stream | Missing | All 184 rows lack authoritative stream values; none is guessed from preferences or row order. |
| Student groups and hierarchy/specialization | Missing | No real group IDs, parent relationships or group-specific specialization dataset. |
| Student counts/population evidence | Missing | No authoritative counts, complete rosters, disjointness proof or cross-module/repeat-student overlap dataset. Distinct IDs alone cannot justify combined capacity. |
| Weekly session demand | Available hours, missing complete contracts | Source hours/formulas are retained, but group-specific requirement IDs, session types, duration, frequency and offering references are unavailable. Hours are not automatically divided into invented sessions. |
| Venue availability/blocked periods | Missing | Inventory has neither explicit operating/available windows nor blocking data. Missing input is not an empty block list. Resource Service remains the future authority. |
| Combined-room physical relationships | Unresolved; requires campus/resource confirmation | Two listed combined rooms lack constituent-room/mutually exclusive booking relationships. Do not treat them as independent spaces or invent component mappings. |
| Session-to-venue eligibility | Missing authoritative mapping | Lab specialization, equipment/features and allowed/excluded rooms require explicit policies. Type and capacity alone cannot prove every room suitable. |
| Staff teaching availability and caps | Missing | Preference workbook statements are not hard calendars. Explicit daily/weekly caps are separate from teaching targets and need their own authority, if applicable. |
| Admin/research timing | Missing real applicable inputs | Contracts and hard protection work synthetically. Need dated approvals, policies, actual windows or authorized flexible ranges, appointment applicability and upstream resolution where needed. |
| Lecturer preferences | Available but incomplete; requires confirmation | 90 source rows produce 594 candidates (514 time-of-day, 80 working-day), zero scoped contracts. All 594 lack explicit weights, stream and confirmation; numerical day-part boundaries are missing. 232 availability assertions require separate hard-availability confirmation. Instructors need no fabricated preference rows. |
| Preference template costs | Available but unapproved | Solver_Mapping contains source costs 0/2/6 and a hard-unavailability label. They are retained, not adopted as per-violation weights or hard blocks. Confirm numeric semantics, dates, streams, preferred alternatives and weekend interpretations. |
| Fairness cohorts/normalization/priorities | Requires university/research approval | Teaching-target normalization works with exact rational arithmetic, but real explicit cohorts, verified targets, exclusions, weights, priorities and applicability are not supplied. Synthetic approval strings do not establish real approval. |
| Consecutive breaks | Missing confirmed preferences/policy | No real preferred break duration, continuous limit, weights, date/scope applicability or confirmed hard minimum has been supplied. No compulsory duration is invented. Idle compactness is opt-in only. |
| Academic-week calendars | Missing authoritative scheduling horizon | Named solver days require explicit actual dates and stream windows, consistent across staff/work-period/preference/fairness/consecutive contracts. |

The teaching report still has **eight warning entries**: development provenance
(1), retained staff/preference metadata (2), the two deliberately shared IT2100
module-code rows (2), missing authoritative offering/stream conversion (1),
unverified staff/department provenance (1), and session demand not inferred
from hours (1). These are not eight new data errors. It reports zero errors
and zero duplicate/ID-name/repeated-team conflicts.

Venue warnings remain **four entries**: two unresolved combined-room
relationships, one missing availability declaration and one missing blocked-
period declaration. The ten former missing Building/Floor warnings remain
resolved. The richer preference mapping has separate outstanding diagnostics
for scope, day-part boundaries, availability confirmation and ambiguous
weekend work/attendance semantics; they are not resolved by parsing cells.

## Remaining requirements and recommended order

1. Obtain versioned authoritative offering/stream, group/membership and explicit
   session-demand contracts; verify development roster classifications and
   proposed eligibility before any real-campus generation. Do not create a
   competing academic master inside Teaching Service.
2. Confirm resource calendars, blocks, physical combined-room footprints,
   session-room eligibility, staff calendars/caps and approved dated
   admin/research protection. Generated drafts will later require atomic
   Resource Service booking/revalidation; an in-memory solve is no reservation.
3. Apply the corrected confirmed **fixed** student lunch periods: study years
   1/2 **12:30–13:30**, study years 3/4 **13:30–14:30**, each one continuous hour.
   These supersede the earlier general-window proposal. Step 11D.1 now provides
   independent contracts/assessment; CP-SAT enforcement belongs to Step 11D.2.
   Applicable teaching dates and weekday/weekend scope still need explicit
   configuration. Use validated StudentGroup study years and actual combined/
   ancestor attendance. No lecturer lunch rule, canteen capacity, crowd-balancing
   slots or campus opening hours are inferred. The historical Year 2 lab in
   the timetable above (Tuesday 12:00–13:00) overlaps lunch by 30 minutes;
   the updated runnable demo reports this rather than claiming lunch compliance.
4. Approve preference semantics/weights, fairness cohorts and objective
   priorities, and consecutive-break policies. Decide how complete-demand
   target shortfalls are handled before making a policy-fulfillment claim;
   current reporting deliberately permits a feasible partial teaching week.
   Preserve teaching/admin/recognized-work separation and independent campus
   attendance evidence.
5. Benchmark denser shared-resource synthetic cases only when input size and
   policy scope are known. Measure reliable isolated memory and consider
   operational search budgets, cancellation and review of FEASIBLE results.
   Existing model guards are technical limits, not campus capacity proof.

Updated next implementation step after Step 11D.1: enforce the applicable
fixed student lunch periods as hard constraints in Step 11D.2, once the
date/stream applicability is supplied. In parallel, obtain the
authoritative input handoff identified above. Databases and scheduling APIs
should not be used to conceal these readiness blockers. Travel-distance,
building/floor movement and walking-time optimization remain outside scope.

Verification: **1,707 tests passed in 59.30 seconds**, preserving all 1,659
previous tests and adding 48 integration/negative/benchmark-contract cases.
`pip check` reported no broken requirements. The existing Starlette/httpx
deprecation warning remains (one warning). The standalone end-to-end CLI
returned OPTIMAL and passed independent validation; bounded small/medium
benchmarks ran successfully with the statuses and limits reported above.
File hashes checked against the pre-step snapshot show no missing files,
unchanged original tests and local datasets/reports, and only the documented
production limitation-text change. `local-data` remains ignored by Git.

Step 11C stops at review. No source Excel/import-report edits, database writes/migrations,
other-service changes, API creation, branch changes, commits or pushes.
