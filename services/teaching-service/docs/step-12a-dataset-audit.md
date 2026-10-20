# Step 12A: read-only development dataset audit

Re-run on 2026-10-15 with the existing read-only importers and checksum-bound
venue confirmation. No workbook, accepted assignment or previous report changed.
This is a local-file audit, not live database introspection or independent
verification of academic records. Private source data remains in ignored
`local-data/`; no personal records appear here.

| Source | Records / outcome |
| --- | --- |
| Staff master | 110: 90 academic lecturers and 20 development instructors |
| Module rows | 184 retained, including the intentional two-record IT2100 conflict |
| Teaching-team entries | 1,288 retained; all references valid (1,076 academic / 212 instructor) |
| Lecturer preferences | 90 original rows; 594 soft candidates (514 time-of-day / 80 working-day) |
| Corrected venue inventory | 62: 31 Lecture Hall, 10 Smart Classroom, 19 Computer Lab, 2 BYOD Lab |
| Source import errors | 0 academic, 0 venue, 0 preference-mapping errors |
| Duplicate/name/reference/team-repeat checks | All reported duplicate checks 0 |
| Venue location warnings | 0; campus confirmation still applies to corrected checksum |

## Warnings and conversion blockers

The original **eight academic warning entries** remain appropriate:

* One development-data warning: accepted development records are not an
  independently verified university roster.
* Two source-metadata warnings: retained metadata must not become guessed
  scheduling semantics.
* Two shared-module-code warnings: the IT2100 rows remain distinct.
* One conversion blocker: all 184 rows lack authoritative offering IDs and
  streams, also blocking 1,288 offering references in normalized team records.
* One unverified-source warning: academic source/department provenance still
  needs confirmation (50 owner mappings, 40 provisional, 20 demo placeholders).
* One demand warning: no group-specific session frequency/duration is inferred
  from weekly contact-hour cells.

The venue inventory is **campus-confirmed development input**, including all
provided capacities, types and corrected locations. Original workbook status
labels remain traceability values, not a denial of later campus confirmation.
Four venue warning entries remain: two unknown combined-room booking
relationships, one missing availability dataset and one missing blocked-period
dataset. Inventory confirmation does not supply booking calendars.

All 594 preference candidates remain unscoped, unweighted, without stream scope
and unconfirmed for scheduling; zero complete scoped preference contracts exist.
The adapter reports 90 ambiguous weekend patterns, 90 separate working-attendance
assertions, 232 unconfirmed availability assertions, 90 missing scope notices,
514 missing numeric day-part-boundary notices and 90 separate weekend-availability
assertions. These are distinct diagnostic categories and can refer to the same
source row; they are not extra staff or preference records. Existing Solver_Mapping
labels/costs do not establish approved per-violation optimization semantics.

`audit_development_datasets(..., include_row_diagnostics=True)` retains workbook,
worksheet, actual row and column where known for missing offering/stream and
preference issues, without publishing source cell values. No fabricated header
is attached to a missing authoritative field.

## Exact university inputs still required

1. Authorized institution UUID, staff-number ↔ ADS UUID crosswalk, and confirmed
   staff types/ranks/appointment applicability and any provisional departments.
2. Actual academic term reference, semester, academic-week dates/day mapping,
   explicitly supplied teaching windows and weekday/weekend applicability.
3. Stable authoritative offering IDs for each module record (including both
   IT2100 rows), confirmed stream and teaching-team offering references.
4. Student groups: IDs, study years, semesters, specializations, streams, counts,
   parent relationships, and referenced population/overlap/disjointness evidence
   across the requested group set, including repeat students/cross-stream cases.
5. Complete group-specific session requirements: stable IDs, offering/group
   references, type, duration and weekly frequency; confirmation of demand scope.
6. Explicit combination permissions, any shared-content equivalence and limits;
   source teams must establish common lecturer eligibility for each combination.
7. Dated staff availability, explicit blocks and any approved teaching caps.
8. Role-aware target classification and dated appointment/policy exports;
   applicable upstream multi-appointment aggregate resolutions. A daily ADS
   response alone must not be stretched into week-wide validity.
9. Administrative/research protection policies, approvals and applicable fixed
   or flexible reservations (or explicit confirmation that none applies).
   Completion/attendance evidence, if available, remains separate from allocation.
10. Venue calendar and confirmed blocked periods; explicit combined-room/shared
    resource relationships or declared exclusions; session-to-venue eligibility.
11. Confirmed preference interpretations, day-part numeric boundaries, dates,
    streams, weights, alternate-preference semantics and hard-availability approvals.
12. Approved fairness cohort/applicability/target-verification/priority policy and
    applicable consecutive-session scoring policies, weights and approvals.
13. Explicit dates and streams for confirmed fixed student lunch periods. Fixed
    times are already known; lecturer lunch and opening hours remain unspecified.
14. Source/export identifiers, confirmation references and validity intervals
    covering the requested week for every required record or collection.

Actual-campus generation remains blocked. The recommended next step is to agree
ADS/Resource export contracts and obtain these inputs, then run this readiness
gate against an authorized institution/week. Persistence, APIs and publication
should follow review, with fresh resource validation before booking.
