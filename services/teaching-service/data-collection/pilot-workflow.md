# One-week pilot readiness workflow

This is a local collection and validation workflow. It adds no API, database
persistence, source corrections or solver changes. It never schedules during
CSV validation. It reuses the Step 12A contracts and `assess_readiness()`.

## Minimum complete authoritative set

For one authorized institution and one actual academic week, the current
full-policy integration profile requires:

1. Existing institution UUID; explicit academic term/semester and complete pilot
   scope; actual week/day/date/stream windows and grid configuration. No default
   campus opening hours. Technical base `mode=ACTUAL_CAMPUS`, calendar
   `synthetic_data=false`, `demand_complete=true` must be authorized explicitly.
2. All staff, offerings, groups and requirements needed for that scope, with
   existing eligibility and stable source IDs. At least one referenced teaching
   requirement, staff member, offering and group are needed. Group specialization
   is explicit; counts/duration/frequency are positive. Multiple unrelated groups
   require confirmed known population relationships; no roster is required when
   aggregate evidence suffices. Explicit DISALLOW permits a separate-only pilot.
3. Explicit ADS UUID crosswalk, staff institution mapping, workload classification,
   targets/caps and availability for included staff; dated appointments/upstream
   resolutions where applicable. No rank/login-role inference.
4. Included venue inventory with intact verification/source provenance, complete
   bookable-venue calendars/blocks, per-requirement eligibility and declared
   combined/shared relationships or retained unresolved exclusions.
5. All existing staff protection, preference, fairness, consecutive and lunch
   contracts/configuration, with approvals and exact date/stream applicability.
   Empty records are permitted only where explicitly attested and semantically
   valid. The Step 12A profile does not silently disable an unknown policy.
6. Authoritative source/export/approval references and full-week source validity
   for required values. Source confirmation is a claim supplied by the authorized
   owner, not independent verification or authentication by this script.

The pilot may cover a deliberately approved smaller academic scope; completeness
means complete for that declared scope. The technical team must obtain an
appropriately scoped authoritative export and preserve its metadata. Do not
silently trim academic eligibility, truncate the 62-venue inventory while
retaining its count/checksum confirmation, or fabricate a smaller source snapshot.
Retain original datasets unchanged; collect scope approval rather than asking
the university to retype known module/staff/venue attributes.

## 1. Prepare private review files

From the repository root:

```powershell
.\.venv\Scripts\python.exe -B -m schedence_teaching.collection create services/teaching-service/local-data/pilot-review --local-data services/teaching-service/local-data
```

Use a new directory. Existing nonempty directories are refused. This command
reuses the current wide/venue/preference importers. It prefills 184 module
references, 110 staff/crosswalk references, 110 availability reference rows,
62 venue reference rows and 594 preference references. It never fills unknown
IDs, streams, dates, student populations, hours, weights or confirmations.
The two IT2100 rows retain different source worksheet/row locations. Existing
source metadata, workbooks, capacities, assignments and confirmation checksums
remain unchanged. Private prefill output is restricted to the chosen local-data
folder by the CLI; that folder must remain Git-ignored.

Generic blank templates are already in `templates/`. The command without
`--local-data` creates another blank pack, useful for approved normalized ADS
exports that do not use the current workbook layout.

## 2. Obtain university completion and approval

Share the checklist, field-level gap report, dictionary and private review files.
Select and explicitly approve pilot scope. Duplicate availability/calendar
reference rows so there is one row per included entity/configured day. Keep
closed days explicit with NONE; do not confuse blank and NONE. Source approvals
must cover all returned rows. Keep completed data outside Git.

Policy Reference identifies the reviewed underlying contract or approval. It
does not itself supply missing weights, cohorts, time slots or appointments.
Supplementary preference/day-part/cohort CSVs provide those supported numeric/
membership details without retyping preference statements. The remaining
technical policy JSON comes from the existing service contracts/authorized
exports, not a synthetic fixture. No university user is asked to fabricate JSON
or implementation-specific research settings.

## 3. Assemble reviewed technical context

The technical team supplies `base-submission.json`, an existing
`SchedulingSubmission` shape: `{ "inputs": { ... }, "evidence": [ ... ] }`.
Use Step 12A `NormalizedSchedulingInput` and [its complete mapping specification](../docs/step-12a-authoritative-inputs.md).
This release deliberately does **not** provide a filled real-campus JSON example:
institution, week, groups and approved settings are not yet known.

Reuse existing imported staff/venue records, `venues_from_import`, confirmed
teaching/admin targets and fixed lunch periods. Supply authorized institution,
actual week/day mapping/stream windows, staff institution mapping, team
references or source-row mapping, session-to-venue eligibility, reviewed policy
contracts/configs, provenance and any approved caps. Reuse retained preference
expressions in `preference_input.preferences` so approval CSVs can reference them;
missing scope/weight fields may then be completed by those rows. Do not create
preferences for instructors or silently omit unapproved records. Approved
deferment/absence is explicit, not inferred from missing data.

CSV-managed collections may initially be empty; validation replaces those
collections in a **copy**. Existing base files are never written. Preserved
staff/venue attributes, non-CSV policy parameters, caps and source metadata are
not guessed. Technical base gaps remain visible through the existing gate.

Where `--local-data` is supplied, offering rows are matched by exact original
workbook/worksheet/row. Source module fields must match; accepted team entries
for each selected row are rebound to its supplied offering ID without changing
staff IDs or roles. A duplicate source-row mapping is rejected. Module codes
are never identity keys. Original source applicability must be confirmed for
the pilot; this code does not independently verify university eligibility.

## 4. Validate; no solver or database call

```powershell
.\.venv\Scripts\python.exe -B -m schedence_teaching.collection validate services/teaching-service/local-data/pilot-review --base-submission services/teaching-service/local-data/base-submission.json --institution-id <existing-institution-uuid> --local-data services/teaching-service/local-data
```

Replace the UUID placeholder with the authorized existing institution ID. The
CLI defaults to an actual-campus request; it never flips mode or substitutes a
fixture. Exit **0** means input readiness, **1** means blocked readiness, **2**
means the command/base/source could not be read or was invalid. Output contains
status counts and safe row-level diagnostics: filename, CSV worksheet, physical
row, readable column and structural contract path. Invalid source values are
not printed. Empty/unknown/ambiguous data is not silently discarded to produce
a ready submission.

`validate_collection_pack()` returns a submission **only when all collection
checks and Step 12A checks pass**. It withholds partial submissions on any failure.
Missing/incorrect headers, missing fields, invalid enums/counts, hierarchy,
references, unverified evidence, calendar gaps, policy conflicts and lunch scope
all block readiness. Confirmation strings do not authenticate users or sources;
the future service boundary must perform authorization separately.

Passing CSV validation is not a solved timetable, policy fulfillment, resource
reservation or publication approval. After review, the returned complete
submission can be passed to existing `integration.run_scheduling()` with the
same trusted institution and actual-campus mode. This step does not run it for
real data, create an API or persist results.

## 5. Synthetic verification and remaining blockers

```powershell
.\.venv\Scripts\python.exe -B services/teaching-service/examples/pilot-readiness-demo.py
.\.venv\Scripts\python.exe -B -m pytest services/teaching-service/tests -q
.\.venv\Scripts\python.exe -B -m pip check
```

The synthetic demo creates a temporary completed pack, explicitly calls
`actual_campus=False` and validates seven original occurrences, eligible
combination preparation and all existing policy contexts without CP execution.
It is never placed in the authoritative templates or selected as a fallback.
CLI fixture validation requires explicit `--synthetic` and matching base flags.

Actual pilot generation remains blocked by institution/week authorization,
184 offering IDs/streams (or an approved complete selected scope), groups and
aggregate population evidence, session demand, staff crosswalk/classification/
availability, venue calendars/blocks/relationship resolution or exclusions,
approved preference/fairness/protection/consecutive settings, lunch date/stream
scope and dated source confirmations. Fixed target hours, lunch times, venue
locations/capacities/types and existing assignments are not missing inputs.
