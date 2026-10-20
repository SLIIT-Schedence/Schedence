# University timetable team: pilot data request

Purpose: prepare **one actual academic week for one institution** using approved
staff, academic and resource inputs. The development datasets contain 110 staff,
184 module rows, 1,288 team entries, 90 lecturer preference rows and 62 venues.
We will reuse these values; we are requesting missing links, scope and approvals.
The supplied academic records are confirmed by the user as university-sourced.
Reuse their academic fields and teaching assignments without requesting source
verification again. The venue inventory and listed campus policy values are
also confirmed; missing dated scheduling scope remains a separate requirement.

Please use the private prefilled review copy and return it through the agreed
authorized channel. Do not place completed files in Git. All examples below are
**synthetic format examples**, not real records or proposed university values.

## 1. Academic offerings and pilot scope — mandatory

**Owner:** academic registry/program teams through Academic Data Service (ADS).
Supply institution/term/week references to the technical coordinator. In
`offerings.csv`, add the **actual Offering ID and stream** for selected pilot
rows; keep existing module code/name/year/semester/specialization/degree and
source workbook/worksheet/row values. Link the retained teaching team to the
supplied offering identity; do not reassign or replace its members.

**Why:** module code alone is not unique. The two IT2100 records must receive
distinct offering identities. **Validation:** stable unique offering IDs; study
years 1–4; semesters 1–2; WEEKDAY/WEEKEND; unchanged source references and valid
staff links. Synthetic format: `SYN-OFFER-01 | SYN-MOD-01 | 2 | 1 | SYN-COMPUTING | WEEKDAY`.

## 2. Student groups and population evidence — mandatory

**Owner:** student registry/program administration through ADS.
Complete group ID, specialization, study year, semester, stream, positive count
and parent ID (blank for a main group). Supply verified aggregate population
relationships in `population-evidence.csv`. One referenced DISJOINT partition
may identify many groups; OVERLAPPING/UNKNOWN identifies two. Cover unrelated
groups across streams too; different IDs/streams do not establish non-overlap.

**Why:** prevent student clashes and add counts safely when combining lectures.
**Validation:** existing parents; no cycles; matching parent period/stream;
compatible offering/group scope; known referenced population relationships.
Synthetic formats: `SYN-G1 | SYN-COMPUTING | 2 | 1 | WEEKDAY | 120 | [blank parent]`;
`SYN-G1;SYN-G2 | DISJOINT | synthetic:registry-partition`.
**Do not send student names, emails or rosters when aggregate proof suffices.**

## 3. Teaching session demand — mandatory

**Owner:** module coordinators/program teams through ADS.
Supply stable requirement ID, offering ID, group ID, session type, sessions per
week and duration minutes. Supply content equivalence and combination permission
only where confirmed. No need to enumerate every possible group pairing.

**Confirmed lecture rule:** two weekly lecture hours mean one continuous
120-minute lecture; one means one 60-minute lecture. Do not request that rule
again or split those lectures. Tutorial/workshop/lab patterns require separate
explicit duration/frequency input, retaining their source weekly totals.
**Validation:** positive integers; known IDs; matching scope;
combination requires compatible lecture content/duration/frequency, same
specialization, disjoint evidence and a common eligible staff member. Original
requirements remain present. Synthetic format:
`SYN-REQ-01 | SYN-OFFER-01 | SYN-G1 | LECTURE | 2 | 60`.

## 4. Staff references, availability and appointments — mandatory

**Owner:** authorized academic administration/HR through ADS.
Confirm preserved Staff ID → ADS staff UUID, workload band and any applicable
HOD/DEAN appointment. Reuse existing names and ranks; do not recollect them.
Supply one staff/day availability row for each configured pilot day: actual
week reference, day ID, teaching allowed, windows, blocks and source approval.
Send dated appointment/policy and aggregate resolution exports to the technical
coordinator when applicable. A daily ADS result does not authorize a whole week.

**Why:** eligibility, availability and administrative overrides must refer to the
same staff member and institution. **Validation:** exact IDs; explicit dated
calendars; NONE for confirmed no blocks; no inferred caps or availability.
Synthetic format: `SYN-STAFF-01 | SYN-WEEK | MONDAY | TRUE | 09:00-11:00 | NONE`.
Daily/weekly teaching caps are **optional**, supplied only when explicitly approved.

## 5. Venue calendars and booking relationships — mandatory where bookable

**Owner:** campus resource/venue office through Resource Service.
Supply Venue ID, actual week/day, available windows, confirmed blocks, and any
explicit combined-room components/shared-resource IDs. Reuse all confirmed
venue names, capacities, types and locations; no need to send them again.
For the two unresolved combined-room cases, provide relationships or retain
their existing exclusion. Do not infer room components from names.

**Why:** inventory confirmation is not permission to book. **Validation:** known
IDs; complete day coverage; numeric non-overlapping time semantics; explicit
relationships; no fabricated open hours or unavailability. Synthetic format:
`SYN-ROOM-01 | SYN-WEEK | MONDAY | 09:00-12:00 | NONE`.
Travel distances, walking times and floor travel are **not requested**.

## 6. Policy applicability and approvals — mandatory declarations

**Owner:** university timetable policy authority; academic/HR administration for
staff policies; Resource office for venue relationships; Teaching team for
reviewed research configuration linked to those approvals.

Complete all eight policy confirmation rows with the exact policy reference,
decision, validity and approval/source. The technical coordinator supplies a
readable policy export showing the current parameters and priority order for
review; the university is not asked to invent code or reproduce JSON schemas.

* **Preferences:** approve the applicable retained preference IDs/week/stream,
  numeric weights and weight reference using `preference-approvals.csv`.
  Where morning/afternoon labels apply, supply numeric boundaries in
  `time-of-day-windows.csv`. Confirm weekend interpretation separately. Instructors
  need no lecturer preference records. An approved empty/deferred pilot preference
  set is possible; unknown or unapproved preferences cannot silently become empty.
* **Fairness:** approve explicit cohort IDs/staff IDs/reasons using
  `fairness-cohorts.csv`, or the reviewed existing eligibility-based scope.
  Confirm teaching-target verification and priority references. Optional staff/
  cohort weighting or extra metrics may be deferred, without removing required
  full-profile policy context.
* **Administration/research:** supply applicability, approval and explicit dated
  reservations/eligible windows where required, using existing ADS exports.
  Explicitly confirm no applicable protection if that is the policy. Do not
  invent time slots or count reservations as completed administrative work.
* **Consecutive teaching:** approve configured soft thresholds/weights; any hard
  minimum break requires separate explicit campus confirmation.
* **Lunch:** confirm applicable actual dates and WEEKDAY/WEEKEND streams. Do not
  resubmit or change the already confirmed fixed year-specific lunch times.

**Validation:** complete existing policy contracts, matching week, institution,
references, numeric parameters and explicit approvals; ambiguous decisions fail.
Synthetic weight: `SYN-PREF-01 | SYN-STAFF-01 | SYN-WEEK | WEEKDAY | 3 | synthetic:weights`.
Synthetic cohort: `SYN-COHORT | SYN-STAFF-01;SYN-STAFF-02 | synthetic comparable pool | synthetic:fairness`.
Synthetic lunch scope: `student_lunch | APPLY | synthetic:lunch | 2026-10-19 | WEEKDAY`.

## Already confirmed values — do not collect again

| Rank or appointment | Weekly teaching | Separate administrative allocation |
| --- | ---: | ---: |
| Instructor | 20 h | No additional allocation inferred |
| Assistant Lecturer | 18 h | No additional allocation inferred |
| Lecturer | 16 h | No additional allocation inferred |
| Senior Lecturer | 12 h | No additional allocation inferred |
| Dr. | 8 h | No additional allocation inferred |
| Assistant Professor | 6 h | No Professor allocation inferred |
| Professor | 6 h | 4 h |
| HOD appointment | 4 h | 4 h |
| Dean appointment | 4 h | 4 h |

HOD/Dean override policy is separate from academic rank. Regular academic staff
generally have six campus working days; Professor/HOD/Dean are exempt, Assistant
Professors are not automatically exempt. Campus attendance does not require
teaching on every day. Teaching targets are not maximum-only caps; completed
recognized work, administrative allocations and attendance remain distinct.

Fixed student lunch: years 1/2 **12:30–13:30**, years 3/4 **13:30–14:30**,
continuous one-hour periods. No automatic lecturer lunch rule is assumed.

Optional for this generation pilot: verified departments where still provisional,
individual completed-work/attendance evidence, extra fairness weights/metrics and
individual student rosters where aggregate evidence is sufficient. Optional
does not mean missing evidence can be declared confirmed zero.

## Return checklist

- [ ] Selected institution, academic term/week and complete pilot demand scope approved.
- [ ] Offering IDs/streams linked to exact module rows; both IT2100 records retained.
- [ ] Groups, hierarchy, counts and aggregate population evidence approved.
- [ ] Explicit session requirements and retained teaching-team applicability approved.
- [ ] Staff crosswalk/classification and dated calendars complete.
- [ ] Venue calendars and unresolved room relationships/exclusions reviewed.
- [ ] Preference/fairness/protection/consecutive/lunch applicability reviewed.
- [ ] Every returned data row has source, confirmation and full-week validity.
- [ ] Technical readiness report has no missing/ambiguous/unverified/invalid inputs.

Passing readiness authorizes neither publication nor resource booking; those
remain separate later workflows.
