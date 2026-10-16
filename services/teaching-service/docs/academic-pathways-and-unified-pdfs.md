# Academic pathways and unified main-group timetable previews

The earlier PP1 builder routed each provisional specialization allocation as a
separate teaching cohort. This conserved student totals, but made six identical
Year 1 Semester 1 curricula into six separately named tracks. The earlier PDF
script then exported main groups and individual subgroups on separate pages.

The correction separates teaching-track configuration from proportional test
allocations and official enrollment policy. No university workbook or historical
PostgreSQL snapshot/version is changed. Legacy preparation without the new
configuration remains available for exact historical replay.

## Source audit and approved Year 1 Semester 1 mapping

`Modules_183` source rows remain the module identity. Module codes, including the
known IT2100 conflict, are never assumed unique. First exclusive module
appearances match the previously reported stages: Computer Science Y1 S1, CSNE
Y1 S2, Cyber/ISE Y2 S1, IT/SE/DS/IM Y3 S1. Those observations are not official
specialization enrollment-start rules.

The user approved this source-preserving local preview mapping:

| Teaching track | Original provisional populations | Source rows | Weekday / Weekend students |
| --- | --- | --- | --- |
| Common Foundation | IT, SE, Cyber, DS, IM, ISE | 84, 86, 88, 90 | 635 / 106 |
| Foundation - CSNE curriculum variant | CSNE | 84, 86, 90 | 42 / 7 |
| Computer Science | Computer Science | 2, 4, 6, 8, 134, 136 | 43 / 7 |

Common Foundation includes IT1020, IT1040, IT1060 and IT1090. The CSNE variant
excludes IT1060 because row 88 does not include CSNE. No missing common module is
invented. Computer Science includes IE1004, IE1014, IE1024, IE1044, SE1022 and
SE1042, using their exact recorded contact hours and staff eligibility.

All 16 original year/semester/stream totals reconcile. The corrected period has
exactly 720 Weekday and 120 Weekend students. The allocation workbook remains an
approved estimate, not evidence of actual specialization enrollment. Other
periods are audited but retain their existing grouping until configured.

## Contracts and preparation

`pilot.academic_pathways` provides:

- `AcademicTrackRule` and `AcademicPathwayConfiguration`: explicit period,
  track label, source populations, interpretation and evidence reference.
- `audit_module_pathways`: curriculum equivalence and first exclusive module
  appearances without inferring enrollment starts.
- `prepare_academic_grouping`: validates source-row-identical curricula, then
  reuses `CohortCount`, `StudentGroup`, `GroupSizingPolicy` and `generate_groups`.
  Pooling different curricula fails. Both intersection and union shortcuts would
  change per-student demand and are prohibited.

Use `prepare_pp1(..., academic_pathways=configuration)` to opt into reviewed
teaching tracks. The original function signature remains backward compatible.
`build_specialization_teaching_input` accepts an optional exact
`group_source_specializations` mapping and independently checks curriculum
equivalence. Source `ModuleOffering.specialization` values are retained; a
display track label never replaces source applicability. An unconfigured,
known single-population scope with no catalog rows remains available to the
existing blocked-scope diagnostics, including Year 4 Semester 1 DS.

The public example `examples/academic-pathways.example.json` is the approved
Year 1 preview mapping, not a university enrollment-start policy. Future academic
pathway changes require explicit period rules and source applicability checks.

Group limits remain main 120 and subgroup 60; larger configured caps fail. New
IDs are deterministic local planning identities. Original allocation strata and
source locations remain in anonymous offset/count slices. Parent slices equal
the union of their children. These slices preserve reproducible PP1 count
partitions; they do not fabricate individual student IDs or claim verified real
student membership. The legacy internal `.Lxx` group suffix and sizing-field
names are retained for compatibility; presentation uses generic subgroups G1.1,
G1.2, which can attend any permitted small-group activity.

## One logical timetable per main group

`pilot.group_timetables` provides:

- `main_group_timetables`: the main group's own sessions and all descendant
  subgroup sessions, indexed once by actual booking identity.
- `subgroup_timetable`: optional individual filtering with ancestor sessions and
  without sibling sessions.
- `parallel_booking_lanes`: simultaneous sibling bookings occupy separate blocks
  in the same day/time area. Overlap clusters are independent for each day.

`pilot.pdf_export.export_main_group_pdf` revalidates the supplied native result
before creating files. It produces A3 landscape main-group timetables with fixed
9.2-point session text, complete module names, lecturer names/IDs, venue IDs,
attendance labels, actual times, delivery modes and year-specific lunch. It never
runs the solver. Existing paths are rejected, and optional `pdf` dependencies
are separate from the service's ordinary runtime requirements.

If parallel blocks cannot fit, the same main-group timetable continues in day
panels. Separate subgroup pages are not the default. The corrected campus preview
fits one physical page per main group, with no continuation pages: Weekday 8,
Weekend 3, Combined 11. Every actual delivery appears exactly once in its main
group view. Repeated views across the three separate export files are not extra
bookings.

## Local scheduling and independent reconciliation

Only the affected Year 1 teaching was regenerated. Existing 525 Semester 1
bookings for Years 2-4 were reserved as lecturer/venue commitments. Existing
CP-SAT constraints/objectives were not changed, and shared lecture combination
remains disabled. Source contact hours, suitability/capacity, lunch, staff
availability, preferences and Year 4 synthetic registrations remain intact.

| Measure | Earlier draft | Corrected local preview |
| --- | ---: | ---: |
| Year 1 main groups | 19 | 11 |
| Year 1 subgroups | 27 | 18 |
| Year 1 group records | 46 | 29 |
| Year 1 Weekday sessions | 106 | 78 |
| Year 1 Weekend sessions | 66 | 28 |
| Year 1 expected / scheduled | 172 / 172 | 106 / 106 |
| Semester 1 expected / scheduled | 697 / 697 | 631 / 631 |

The corrected Year 1 native solve was OPTIMAL within the reserved-booking
problem. The independently validated full Semester 1 union is FEASIBLE; it is
not a new claim of globally optimal Semester 1 scheduling. Every corrected
known occurrence is covered, with zero hard-constraint violations. The original
697/697 historical draft remains unchanged.

An independent per-leaf audit compares every source row/contact type against
the exact generated weekly minutes and occurrence count. It also reconciles
every original anonymous allocation partition to the new subgroups. Weighted
student contact time remains 610,200 Weekday and 101,640 Weekend minutes; no
curriculum hours were reduced, and two lecture hours remain one continuous
120-minute occurrence. The delivery reduction comes from academic regrouping.

Local output directory:

`local-data/academic-grouping-correction/validated-preview/`

It contains the three requested corrected PDFs, Year 1 HTML/CSV/JSON exports,
full corrected Semester 1 snapshot/result and validation, per-leaf contact-hour
CSV, allocation-partition correspondence CSV, policy/source audit and PDF
coverage manifest. These files and their personal staff content remain ignored
by Git. Prepared snapshots are review artifacts, not inserted database records.

The experimental Monday online end time remains 18:30. Semester 2 was neither
regenerated nor claimed concurrent with this preview. Year 4 Semester 1 DS remains
blocked for missing catalog mapping, and compulsory Research remains pending
session mapping. Production adoption of a corrected snapshot/version requires a
separate review; no production record, API or migration is changed here.

## Verification

Synthetic tests cover source-identical pooling, differing curricula with
duplicate module codes, omitted/duplicate rules, exact totals and slices,
confirmed group caps, nested groups, parallel sibling sessions, optional subgroup
filters, corrupt/partial results, existing-file protection and PDF content.
The complete Teaching suite includes isolated PostgreSQL writes and separate
read-only Neon checks; production-write tests remain disabled. Local evidence is
stored in `local-data/academic-grouping-correction/`.
