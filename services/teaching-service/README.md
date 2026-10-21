# Teaching Timetable Service

## Portable API startup and readiness

Use the shared repository `.env` and `python scripts\run_services.py --only teaching`.
It needs Cognito/Identity settings and Teaching's **restricted**
`TEACHING_DATABASE_URL` / `TEACHING_DIRECT_DATABASE_URL` in that same file; see
the root `.env.example`. An owner/BYPASSRLS login fails the existing runtime
security checks and must not be substituted for the restricted Teaching login.
No `local-data/` files are required for normal API startup.

`TEACHING_GENERATION_READINESS=ENABLED` is the default in all environments.
`DEVELOPMENT` remains a compatible alias; `DISABLED` disables only readiness.
`TEACHING_FORM_SENDING_ENABLED=true` enables permission-checked sending using
Teaching's own shared database. Neither setting starts CP-SAT or a worker.
Generation remains explicitly opt-in and disabled by default.

The normal readiness path calls Academic and Resource over GET-only HTTP,
forwarding the current verified caller's bearer token and authorized institution
on every request. It has no owner database credentials, database-target pins,
private workbook paths, or shared token cache. HTTP redirects are rejected;
owner 401/403 responses remain authorization failures. Teaching's own snapshots
are read in tenant-bound read-only transactions, with integrity validation and
an explicit institution predicate in addition to existing RLS. Existing pre-solve
validation and Scheduling Rules are reused; no solver runs.

Optional readiness/sending/worker configuration failures log a safe warning and
leave the core runtime available. Missing readiness dependencies produce
`503 READINESS_UNAVAILABLE`; missing sending dependencies produce
`503 FORM_SENDING_UNAVAILABLE`. Timetable reads and draft Forms remain usable.
An authenticated authorized timetable list can return `200` with an empty list
when no eligible canonical timetable exists.

**Current owner integration blockers:** this checkout's Academic API exposes
`GET /api/v1/academic/staff`, but not the teaching-input or recipient-directory
exports below. StaffOut lacks teaching-profile/department allocation data.
Resource has models/migrations and no HTTP runtime. Therefore a fresh clone can
start Teaching and use timetables/drafts, but live readiness/sending cannot pass
until those owners provide the missing APIs. Teaching does not implement or
pretend to implement routes in another service. See the exact contracts in
[owner-api-integration.md](docs/owner-api-integration.md).

Readiness prepares from an existing integrity-checked Teaching input snapshot
in the shared database and verifies it against fresh HTTP owner exports. It
never loads `pilot-previews/`, creates a synthetic campus timetable, or silently
changes a historical snapshot. If no snapshot exists, it reports `ready=false`
with a `prepared-input` MISSING check. The canonical Neon tenant currently has
no approved input snapshot: the earlier experimental PP1 snapshots were
deliberately excluded from migration. Importing an approved input/calendar is
a separate prerequisite, not something readiness guesses or writes.

Legacy explicitly enabled development worker modes still use their existing
isolated configuration. Those settings are not prerequisites of readiness or
Forms sending and are not enabled by the shared-env defaults.

The FastAPI service currently exposes `GET /health`. Its Pydantic v2 schemas
are input contracts for records supplied by the Academic Data Service, which
owns authoritative staff, module, offering, and group data. These contracts
introduce no database or academic master records.

Current research scope: the previously proposed building-distance/travel Step 11
is removed. Building/floor values remain venue inventory metadata only. There
will be no building/floor distance model, walking-time matrix, travel penalty
or location-based scheduling preference. Step 11 now concerns consecutive
teaching and load distribution; Step 11A assesses existing validated timetables
and Step 11B adds explicit optional optimization using the same scoring rules.

## Teaching input models

Import the contracts from `schedence_teaching.schemas`.

| Model | Meaning and relationships |
| --- | --- |
| `StaffMember` | Existing staff ID, name, position, optional department, and `ACADEMIC` or `INSTRUCTOR` type. Supplied IDs such as `LEC001` through `LEC090` and separate instructor IDs are preserved; no IDs are generated and no prefix format is imposed. |
| `ModuleOffering` | Identified by `offering_id`, with module code/name, study year, semester, specialization, degree type, and `WEEKDAY` or `WEEKEND` stream. `module_code` is not unique: distinct offerings can both use `IT2100`. |
| `TeachingTeamMember` | Links `offering_id` and `staff_id` with `LIC`, `CO_LIC`, or `TEAM_MEMBER`. This represents candidate eligibility, not a teaching-session assignment. LIC and Co-LIC require academic staff; instructor candidates retain their instructor type. |
| `StudentGroup` | Offering-independent study year, semester, stream, and supplied positive student count. An optional `parent_group_id` links a subgroup to its main group. Groups can participate in multiple offerings. Sizes such as 120 with two subgroups of 60 are examples, not rules. |
| `SessionRequirement` | Links an offering and a group with `LECTURE`, `TUTORIAL`, `WORKSHOP`, or `LAB`, an explicit positive duration in minutes, and a positive weekly session count. Missing hours and requirements are never inferred. |

Required text must be a nonblank string. Text and IDs are preserved as supplied.
Numeric values must be actual integers: booleans, fractions, and numeric strings
are rejected. Unknown fields and unknown or differently cased enum values are
rejected. Department and parent group may be omitted or `null`, but cannot be
blank when supplied.

The confirmed contract interprets `academic_year` as study year **1–4**, rather
than a calendar year, and `semester` as **1–2**. These ranges are declared once
in `schemas/models.py`.

`TeachingTimetableInput` is an optional collection model for a complete supplied
snapshot. Its five required lists are `staff_members`, `module_offerings`,
`teaching_team_members`, `student_groups`, and `session_requirements`. It checks
unique staff, offering, group, and requirement IDs; resolves references within
the supplied lists; checks academic-only LIC/Co-LIC roles; and rejects missing
parents and group-parent cycles. Individual models cannot verify referenced
staff types or collection-wide ID uniqueness on their own. Use the collection
model when all referenced records are available.

The schemas impose no required LIC count, subgroup size, subgroup count, or
group/offering study-year match. Step 4 preparation adds period-compatibility
checks before expanding session demand. Teaching-team membership does not select staff for sessions.
The schemas provide no persistence, import workflow, optimization, or allocation.

## Local validation

From the repository root, using the existing virtual environment:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".\services\teaching-service[dev]"
.\.venv\Scripts\python.exe -m pytest services/teaching-service/tests -q
.\.venv\Scripts\python.exe -m uvicorn schedence_teaching.main:app --app-dir services/teaching-service/src --host 127.0.0.1 --port 8004 --loop asyncio:SelectorEventLoop
```

Or start it with the other services: `python scripts\run_services.py --only teaching` (add
`--teaching-local-preview <manifest>` to opt in to the development-only local preview store).

Test records are synthetic examples and do not assert authoritative module hours.

## Excel import and dry-run validation

`schedence_teaching.excel_import` uses `openpyxl` to read `.xlsx` files in
read-only mode. It never saves workbooks or writes to a database. No actual
datasets were found in the repository, so no real worksheet or column names
are assumed. The existing Step 2 models remain unchanged.

Copy [the mapping template](examples/excel-import.mapping.example.json) into
the git-ignored `local-data/` directory and replace every `REPLACE_*` value
with inspected source names. Its preference field name and type are also
placeholders, not university-defined preferences. Workbook paths are relative
to the mapping file; absolute paths are also accepted. Store real workbooks,
local mappings, and any saved reports in `local-data/`. The service `.gitignore`
also excludes Excel workbooks throughout this service directory.

Each mapping defines a table `kind`, workbook, exact worksheet name, header row,
and `columns` mapping **model field names to exact Excel headers**. Supported
kinds are `staff`, `preferences`, `offerings`, and `teaching_teams`; tables may
be spread across multiple workbooks or worksheets. Header matching is exact
and case-sensitive. Unmapped columns are ignored with a warning. Empty rows
are skipped. Mapped columns must be present exactly once, including optional
fields whose headers are explicitly configured.

Staff, offerings, and team rows validate through `StaffMember`, `ModuleOffering`,
and `TeachingTeamMember`. Department can remain unmapped or `null`; it is never
inferred from staff names, IDs, or positions. Numeric study years and semesters
must satisfy the confirmed Step 2 rules. Staff IDs are preserved verbatim and
must be text cells; numeric IDs are not converted or zero-padded.

If source enum labels differ, declare translations explicitly, for example:

```json
"value_maps": {"role": {"REPLACE_SOURCE_CO_LIC_LABEL": "CO_LIC"}}
```

Translations may target only `staff_type`, `stream`, or `role`, and destinations
must be valid existing enum values. ID translation is prohibited. Academic
staff type is never inferred from ID prefixes or academic positions.

Preferences have no confirmed scheduling schema yet. A `preferences` table
maps `staff_id` in `columns`, and lists explicitly named `preference_fields`,
each with its Excel `column`, `value_type` (`text`, `integer`, `number`, or
`boolean`), and `required` flag. `LecturerPreferenceRecord` is an import-only
carrier for these supplied values; it does not establish availability rules,
weights, or defaults. Empty optional fields are omitted. At least one supplied
preference value is needed for a record. Dates, formulas, numeric strings for
numeric fields, and inferred yes/no conversions are not accepted.

Validation checks include:

- Required mappings, headers, values, and existing Pydantic field/enum rules.
- Duplicate staff IDs, preference staff IDs, offering IDs, and exact team
  `(offering_id, staff_id, role)` records. All duplicate occurrences are excluded,
  including valid records whose duplicate has invalid non-identity fields.
- Staff references against a valid unique Staff Master record; team offering
  references against a valid unique offering record.
- Academic-only LIC and Co-LIC roles. Instructor team candidates remain
  instructors. Lecturer preference records must reference academics.
- Missing academic preferences as warnings when a preference table is configured.
  Instructors never require preferences. No missing preference is fabricated.
- Shared module codes, including `IT2100`, as warnings while preserving distinct
  offering IDs and their records. No offering IDs are generated.
- Formula and Excel error cells in mapped columns as errors. Source formulas
  are never evaluated or trusted as cached literals.

The first version expects normalized tables: one staff row per ID, one
preference row per academic, one offering row per offering ID, and one candidate
team member per row. Wide LIC/Co-LIC/team lists, merged-cell layouts, and matrix
preferences require a source-specific adapter after inspecting the real files.
No session requirements or module hours are imported or synthesized in Step 3.

Run from the repository root after installing the service's development extras:

```powershell
.\.venv\Scripts\python.exe -m schedence_teaching.excel_import.cli --config services/teaching-service/local-data/mapping.json
# Equivalent installed entry point:
.\.venv\Scripts\schedence-teaching-import.exe --config services/teaching-service/local-data/mapping.json
```

The JSON report contains rows read, accepted record counts (academics and
instructors separately), errors, and warnings. Each issue includes workbook
name, worksheet, physical Excel row number, source header, code, and explanation.
Unavailable locations are `null` for workbook/configuration-level issues.
Rejected rows are excluded from accepted counts. Reports omit source IDs,
staff names, and preference values; the source filename and column names remain
visible for diagnosis. Errors may cascade when a referenced source record is
invalid. A report with any error is unsuitable for downstream use, even when
some records were accepted. Exit codes: `0` for no errors, `1` for dataset
errors, `2` for configuration errors. Warnings alone do not fail the command.
All imports carry `data_status: development_unverified`; proposed allocations
are not presented as university-verified teaching assignments.

Final integration needs the private source workbooks (or representative redacted
samples), exact worksheet/header names and header-row positions, existing
offering IDs and their team references, explicit staff-type/role/stream labels,
and confirmed preference keys, value types, missing-value meanings, and layouts.
If authoritative offering IDs are absent, their definition must be supplied by
the Academic Data owner rather than derived from module codes. Keep real staff
and preference data out of committed examples and tests.

## Department-aligned source adapter (Step 3.1)

The `wide-teaching-v1` layout reads the actual workbook headers without changing
the Step 2 contracts or the generic Step 3 mappings. The available inputs were
the three Department_Aligned workbooks, which the dataset owner explicitly
accepted for development; the four originally named Audited files, including
the pre-import audit workbook, were not present. This adapter does not claim
independent university verification.

| Source | Worksheet / header row | Mapping |
| --- | --- | --- |
| Staff Master | `Staff_Master` / 1 | `Staff_ID` to `staff_id`; `Staff_Name` to `staff_name`; `Position` to `position`; `Department` to `department`. |
| Staff classification | `Staff_Master` / 1 | Explicit configuration maps the observed `Data_Status` labels to `ACADEMIC` or `INSTRUCTOR`; IDs and positions are never used to guess staff type. |
| Lecturer preferences | `Lecturer_Preferences` / 4 | `Staff_ID` reference; exact source keys for `Expected_Working_Days`, `Weekend_Work_Pattern`, `Preferred_Free_Day`, `Can_Work_Both_Weekend_Days`, Monday–Sunday morning/afternoon slots, and optional `Special_Notes`. |
| Modules | `Modules_183` / 1 | `Module_Code`, `Module_Name`, `Year`, `Semester`, `Specialization`, `Module_Degree_Type` map to existing offering field annotations. The sheet name does not determine row count. |
| Lead assignments | `Modules_183` / 1 | `LIC_Staff_ID` paired with `LIC`; `Co_LIC_Staff_ID` paired with `Co_LIC`. Roles become `LIC` and `CO_LIC`. |
| Team members | `Modules_183` / 1 | `Team_Staff_ID_1`–`Team_Staff_ID_5`, paired with `Teaching_Team_Member_1`–`Teaching_Team_Member_5`, become individual `TEAM_MEMBER` source entries. |

Semester translation is explicit: `Semester 1` to integer `1`, `Semester 2`
to integer `2`. Preference `Yes`/`No` and slot labels stay literal strings;
the adapter does not calculate solver penalties or reinterpret preferences.
Staff profile fields in preferences must match the referenced Staff Master.
Assignment display labels must match the exact `Position | Staff_Name` for
their explicit Staff ID. Names are never substituted for missing IDs.

`Data_Status`, `Department_Source`, exam metadata, contact-hour columns,
derived contact-hour formulas, and other source columns are preserved in
in-memory staging. Formula cells are rejected in contract/reference inputs;
unconsumed source formulas are retained as source text, never evaluated or used
to invent session requirements. Provisional affiliations and demo instructors
remain identified by their original source provenance.

`SourceModuleRecord` and `SourceTeamAssignment` preserve every source module
and every supplied assignment slot, including invalid or repeated entries for
review. Exact duplicate rows, duplicate IDs, conflicting names, repeated team
members, missing LIC/Co-LIC slots, invalid references/roles, and invalid year or
semester values are reported with source locations. Same names under different
IDs and unresolved repeated module identities are review warnings, not a reason
to merge people or modules. Both distinct `IT2100` records remain separate.

The source-row diagnostic key contains workbook, worksheet, and physical row.
It is **never an offering ID**. `ModuleOffering` and `TeachingTeamMember`
conversion is blocked until authoritative offering IDs are supplied; offerings
also require a supplied weekday/weekend stream. Optional `offering_id_column`
and `stream_column` configuration may point to those actual columns once they
exist. Invalid or duplicated identifiers do not enter the validated contracts.
No IDs, streams, institution UUIDs, groups, or session durations are generated.

Run the real local mapping, which remains ignored by Git:

```powershell
.\.venv\Scripts\python.exe -m schedence_teaching.excel_import.cli --config services/teaching-service/local-data/department-aligned.mapping.json
```

`local-data/department-aligned.dry-run.json` contains the private local report.
It distinguishes `imported_counts` (complete existing contracts) from
`staged_counts` (preserved source modules and assignments), includes duplicate
checks and provenance aggregates, and declares `ready_for_scheduling: false`.
Consistency validation can pass while contract conversion remains blocked.
Raw staff names, IDs, preference values, and full records are not reported.

Before CP-SAT work, obtain authoritative offering identities and streams,
approved student groups/counts/subgroups, explicit session types/durations and
weekly frequencies, and confirmed staff eligibility and preference semantics.
Supplied weekly hours are retained but do not determine per-session durations.
Institution UUIDs are an upstream persistence concern, not a generated Teaching
input field. Resource availability and approved scheduling policies will also
be needed for a feasible scheduling problem; none is implemented here.

## Group hierarchy and unscheduled session preparation (Step 4)

`schedence_teaching.preparation` is a pure-Python layer over the existing
`StudentGroup`, `ModuleOffering`, `SessionRequirement`, and
`TeachingTimetableInput` contracts. It performs no file/database access and
assigns no staff, rooms, days, or time slots. No solver or new endpoint is added.

- `build_group_hierarchy(groups)` validates supplied main groups and arbitrarily
  nested subgroups. Existing Pydantic validators check IDs, positive counts,
  parent references, and cycles; preparation also requires each parent/child
  pair to have the same study year, semester, and stream.
- `groups_conflict(hierarchy, first_id, second_id)` checks self and
  ancestor/descendant conflicts in either direction. Sibling branches and
  unrelated groups have no automatically inferred conflict. A `False` result
  means no known hierarchy conflict, not proof of disjoint student membership.
  Unknown group IDs produce an error.
- `prepare_timetable_input(inputs)` takes an independent validated snapshot,
  checks each requirement's offering/group year, semester, and stream, expands
  weekly occurrences, and returns the hierarchy, sessions, original contract
  snapshot, and summary.
- `expand_session_requirements(inputs)` performs the same checks and returns
  just the unscheduled occurrences.

Group sizes are preserved. There is no fixed subgroup size, partitioning rule,
or requirement that child counts sum to a parent count. Main groups have no
parent; every non-root group counts as a subgroup, including nested groups.
Hierarchy traversal is iterative and does not depend on Python recursion depth.

An occurrence ID is the supplied `requirement_id` followed by `:<number>`, with
one-based numbering. Output is ordered by requirement ID and then occurrence
number, making IDs and ordering independent of input list order. Every
occurrence retains its offering ID, requirement ID, group ID, session type,
explicit duration, occurrence number, and stream. No random IDs or missing
contact hours are generated. Adding an unrelated requirement leaves existing
occurrence IDs unchanged.

Weekday/weekend counts and views remain separately identifiable in one global
prepared demand set. The snapshot retains candidate teaching teams across both
streams, so a future solver can consider shared staff and resources. Streams
are not independent resource pools, and candidate eligibility is not converted
to an assigned lecturer.

`PreparationError` provides located `PreparationIssue` entries and an `errors()`
JSON-friendly representation. Invalid references and relationships prevent any
partial session result. Existing input schemas are revalidated to catch mutable
models changed since their initial construction. Compatibility issues identify
the input collection/index and the mismatched period attribute.

### Synthetic example

[preparation-input.example.json](examples/preparation-input.example.json) contains
synthetic offering IDs, groups, frequencies, and durations solely to demonstrate
the validated interface. It does not fill gaps in the real workbooks.

```python
from pathlib import Path
from schedence_teaching.schemas import TeachingTimetableInput
from schedence_teaching.preparation import prepare_timetable_input

path = Path("services/teaching-service/examples/preparation-input.example.json")
inputs = TeachingTimetableInput.model_validate_json(path.read_text(encoding="utf-8"))
prepared = prepare_timetable_input(inputs)
print([session.to_dict() for session in prepared.sessions])
print(prepared.summary.to_dict())
```

| Requirement | Offering / group | Weekly frequency | Duration | Occurrences |
| --- | --- | --- | --- | --- |
| `REQ001` | `OFFER-WD` / `MAIN-WD` | 2 | 120 minutes | `REQ001:1`, `REQ001:2` |
| `REQ002` | `OFFER-WD` / `LAB-A` | 1 | 60 minutes | `REQ002:1` |
| `REQ003` | `OFFER-WE` / `MAIN-WE` | 1 | 90 minutes | `REQ003:1` |

```json
{
  "total_student_groups": 4,
  "main_groups": 2,
  "subgroups": 2,
  "session_requirement_count": 3,
  "expanded_weekly_session_count": 4,
  "weekly_contact_minutes": 390,
  "session_type_counts": {"LECTURE": 2, "TUTORIAL": 1, "WORKSHOP": 0, "LAB": 1},
  "stream_counts": {"WEEKDAY": 3, "WEEKEND": 1}
}
```

Session-type and stream counts refer to expanded occurrences. Contact minutes
are the sum of their explicit durations, not duration multiplied by student
counts. In this example, `MAIN-WD` conflicts with `LAB-A` and `LAB-B`; the two
lab subgroups do not automatically conflict with each other.

### Remaining source integration warnings

The unchanged Step 3.1 report has zero errors and eight warning entries:

| Warning code | Entries | Meaning |
| --- | --- | --- |
| `development_data` | 1 | Inputs and proposed allocations are development data, not independently verified university records. |
| `source_metadata_retained` | 2 | Additional staff/preference columns remain in staging outside the existing contracts; they are not discarded. |
| `shared_module_code` | 2 | One warning for each distinct `IT2100` row. Both rows and their assignments are intentionally preserved. |
| `contract_conversion_blocked` | 1 | Authoritative offering IDs and weekday/weekend streams are still missing. |
| `source_provenance_unverified` | 1 | Staff/department provenance includes provisional affiliations and demo instructors that remain unverified. |
| `session_requirements_not_inferred` | 1 | Supplied weekly hours/formulas do not define group-specific session durations and frequencies. |

The 184 source modules and 1,288 assignment entries remain in staging. Real
workbook-to-preparation conversion still requires authoritative offering IDs,
streams, group records/counts/hierarchy, and explicit session requirements.
Before CP-SAT integration, also confirm staff eligibility/preference semantics,
resource availability, and scheduling policies. This step creates none of that
missing source information and makes no claim of real scheduling readiness.

## Basic CP-SAT feasibility solver (Step 5)

`schedence_teaching.solver` consumes `PreparedTimetableInput` returned by
`prepare_timetable_input()`. The solver does not read workbooks or contact
databases. Step 6 adds responsible-staff selection; Step 7B adds optional joint
venue allocation as described below. The Teaching Service depends on
`ortools>=9.15,<9.16`; the tested version is `9.15.6755`. OR-Tools 9.15 adds
[Python 3.14 support](https://github.com/google/or-tools/releases/tag/v9.15),
matching the existing local virtual environment.

`SolverConfig` requires explicit scheduling days, windows keyed by stream,
granularity in minutes, and a positive finite time limit. A deterministic
random seed is optional. There are no campus-hours, lunch, or weekend-policy
defaults. Day names are opaque identifiers, ordered as supplied; no calendar
policy is inferred from their names. Both streams share one global model.
Times are integer minutes from midnight. Windows are half-open `[start, end)`
within a day; `1440` is allowed as an end boundary. Window boundaries and
every session duration must align exactly with granularity. Unsupported
durations/configurations are rejected, never rounded.

For each occurrence the model creates an integer global start, an integer day
and local start derived from it, a fixed-duration mandatory interval, and an
end constrained to start plus duration. Its start domain is a union of the
configured stream/day windows shortened by the occurrence's duration. This
ensures each occurrence appears exactly once, fits wholly inside one window,
and cannot cross a day boundary. Separate windows do not permit sessions to
straddle gaps, even if their combined available minutes would suffice.

For each group, `AddNoOverlap` includes that group's intervals and its
ancestors' intervals. Parent intervals participate in each descendant chain,
blocking all same-group and ancestor/descendant clashes. Sibling branches are
not combined into an exclusion constraint. Siblings/unrelated groups can run
simultaneously under these rules, but their student overlap remains unknown;
additional actual overlap data may be required before publication.

The solver performs feasibility search without a quality objective. It uses
one search worker and preserves input occurrence ordering, identifiers,
session metadata, and durations. Fixed-seed complete solves are repeatable in
the tested environment; identical schedules are not promised across OR-Tools
versions/platforms or near wall-clock timeout boundaries. Runtime includes
input checks, model construction, solving, and output validation.

`SolverResult` contains status (`OPTIMAL`, `FEASIBLE`, `INFEASIBLE`, `UNKNOWN`,
or `MODEL_INVALID`), schedule, assigned count, runtime, validation summary,
failure reason, and explicit limitations. Only `OPTIMAL`/`FEASIBLE` results
return schedules, after independent validation. All other statuses return an
empty schedule with no claim of completed constraint validation. An occurrence
that cannot fit any window proves infeasibility during preflight. Status
meanings are documented by [Google CP-SAT](https://developers.google.com/optimization/cp/cp_solver).
For this feasibility-only model, `OPTIMAL` does not mean university timetable
quality was optimized. Every result reports `university_ready: false`.

`validate_timetable(prepared, config, schedule)` checks exact occurrence
coverage, original metadata/types, configured days, integer times, durations,
granularity, and stream windows. It checks pairwise overlap using direct parent
traversal independently of CP-SAT intervals and the preparation hierarchy's
interval index. Touching session endpoints are permitted. Invalid output raises
`TimetableValidationError` with located occurrence issues; it cannot be
returned as a complete valid timetable. Mutated preparation/configuration
objects are revalidated before model building and independent validation.

### Explicit synthetic solve

[solver-config.example.json](examples/solver-config.example.json) supplies small
synthetic windows for the existing Step 4 example. These times do not establish
university working hours or weekend rules.

```python
from pathlib import Path
from schedence_teaching.schemas import TeachingTimetableInput
from schedence_teaching.preparation import prepare_timetable_input
from schedence_teaching.solver import SolverConfig, solve_timetable, validate_timetable

examples = Path("services/teaching-service/examples")
inputs = TeachingTimetableInput.model_validate_json(
    (examples / "preparation-input.example.json").read_text(encoding="utf-8")
)
config = SolverConfig.model_validate_json(
    (examples / "solver-config.example.json").read_text(encoding="utf-8")
)
prepared = prepare_timetable_input(inputs)
result = solve_timetable(prepared, config)
print(result.to_dict())
if result.validation is not None:
    print(validate_timetable(prepared, config, result.schedule).to_dict())
```

One tested `OPTIMAL` result schedules `REQ001:1` on synthetic Monday 09:00–11:00,
`REQ001:2` 11:00–13:00, `REQ002:1` 13:00–14:00, and `REQ003:1` on synthetic
Saturday 09:00–10:30. All four occurrences pass the independent validator.
Tests also exercise actual insufficient-capacity `INFEASIBLE` and tiny-budget
`UNKNOWN` results, which emit no partial timetable. `FEASIBLE`/`MODEL_INVALID`
status handling is separately tested with controlled native-status responses.

### Scope and remaining publication requirements

Implemented constraints now cover occurrence coverage, configured times, known
student group hierarchy conflicts, and one eligible responsible staff without
overlapping assignments (Step 6), plus explicit venue constraints when enabled
(Step 7B). Staff availability/preferences/working hours, administrative hours,
research days, lunch crowds,
fairness, persistence, and scheduling endpoints remain unimplemented.
They must be added incrementally with appropriate authoritative data and policy.

Real-workbook scheduling is still blocked by missing authoritative offering IDs,
streams, student groups/hierarchy/overlap, and explicit session requirements.
The accepted 184 module rows and 1,288 source assignments remain unchanged in
staging. Synthetic test/example identifiers and windows do not fill those gaps.

## Joint staff allocation and clash prevention (Step 6)

`solve_timetable()` now requires an explicit eligible candidate pool for every
offering with session demand. Pools come only from the existing
`TeachingTimetableInput.teaching_team_members`. Input contracts continue to
validate all staff/offering references and restrict LIC/Co-LIC roles to academic
staff. LIC, Co-LIC and Team Member records remain unchanged. Repeated membership
for the same person produces one candidate choice, without deleting any source
records. There is no role priority or inferred eligibility by session type,
rank, name, department or staff-ID format. Instructors with a Team Member
membership are eligible on the same explicit basis as other candidates.

For each occurrence, the model creates one Boolean selection variable per
candidate and an `ExactlyOne` constraint. Each choice controls an optional
fixed-duration staff interval using the occurrence's existing start and end
variables. One `AddNoOverlap` constraint per staff member covers these optional
intervals across all offerings and both streams. Time and staff selection
therefore happen together within CP-SAT. The mandatory student intervals and
all same-group/ancestor constraints remain in place. Sibling groups can still
run concurrently when there is sufficient eligible staff capacity. Staff
sessions with touching endpoints are allowed; time on different configured
days is separate even if local clock times match.

Every successful `ScheduledSession` requires `assigned_staff_id` in addition to
the original occurrence and day/start/end values. `to_dict()` preserves the
original IDs, session type, stream, duration and occurrence number and exposes
the staff ID plus formatted start/end times. The staff assignment is generated
output, not a replacement for any source teaching-team membership. This version
models **one responsible staff member per occurrence**; team teaching with
multiple simultaneous staff is not yet supported.

An occurrence without eligible staff returns `INFEASIBLE`, an empty schedule,
no completed validation, and a reason identifying the occurrence and offering.
Unused offerings may have empty pools. Missing references or invalid roles
are rejected by the existing input contracts before solving. Timeout/unknown,
infeasible and model-invalid statuses retain the empty-output semantics from
Step 5; no staff/time values are decoded from failed solves.

The independent validator reads staff IDs and offering memberships directly
from the validated input snapshot, rather than trusting model candidate pools
or selection variables. It checks staff ID type/existence, eligibility for the
**original** offering, and half-open pairwise staff overlaps across streams,
alongside all previous coverage, metadata, time and student checks. Invalid
output raises `TimetableValidationError` with occurrence IDs and issue codes
such as `invalid_staff_id`, `unknown_staff`, `ineligible_staff` and `staff_clash`.
Successful summaries include `assigned_staff_exists`,
`offering_staff_eligibility` and `staff_clashes` in their checks. Every result
still reports `university_ready: false`.

### Synthetic staff examples and verification

The existing example input supplies `SYNTHETIC-STAFF` as the explicit LIC
candidate for both synthetic offerings. Running the example above now returns
that ID for all four occurrences; the configured weekday and weekend windows
are on different days. No source availability or preference policy is inferred.

A separate test illustrates joint choice: offering A has LIC `LEC001` and
Co-LIC `LEC002`, offering B has only LIC `LEC001`, and unrelated groups each
need a 30-minute lecture in the same single 30-minute window. The feasible
result assigns A to `LEC002` and B to `LEC001`, both at 00:00–00:30 on the
synthetic day. With both offerings restricted to `LEC001`, the same window is
infeasible. These staff IDs and requirements are explicitly synthetic test data.

`tests/test_staff_solver.py` covers feasible and insufficient-capacity cases,
shared staff across offerings/streams, instructors, all supported session
types and roles, exact source IDs, empty pools, missing references, source
preservation, corrupted output, native timeout and failure statuses. Existing
Step 5 tests retain their assertions and now supply synthetic candidate records
so that group/time checks have sufficient explicit staff capacity.

```powershell
.\.venv\Scripts\python.exe -B -m pytest services/teaching-service/tests -q
.\.venv\Scripts\python.exe -B -m pip check
```

The 184 staged module rows still require authoritative offering IDs and stream
values, student groups/counts/hierarchy/overlap and explicit session demands
before real-workbook scheduling is possible. Availability/preferences, working
days, teaching-hour limits, administrative/research periods, lunch and
fairness remain future work requiring explicit data and policy. Step 7B adds
venue/capacity constraints using explicit venue scheduling inputs.
This step does not read or change accepted workbooks, apply preferences, persist
assignments, contact Neon, or add scheduling or authorization endpoints.

## Venue contracts, import and validation (Step 7A)

Step 7A adds input contracts and read-only venue import only. The existing
student/staff scheduling model is unchanged; venue selection, capacity
constraints and venue clash prevention are not implemented in this step.

`Venue` is a Pydantic input contract with required source `venue_id`,
`venue_name`, `venue_type` and positive integer `capacity`, plus optional
`building`, `floor` and `lab_specialization`. `VenueType` accepts the four
inspected source types: Lecture Hall, Computer Lab, Smart Classroom and BYOD
Lab. Floor accepts an integer or an explicitly supplied nonblank textual floor
label; no floor, building or campus identity is derived from a venue name.
Existing teaching contracts and `TeachingTimetableInput` remain unchanged.

The supplied `Schedence_Actual_Venue_List_Sample_Capacities.xlsx` has one
`Venue_Master` worksheet, with headers on row 1. Explicit mappings are:

| Contract field | Actual source header |
| --- | --- |
| `venue_id` | `Venue_ID` |
| `venue_name` | `Venue_Name` |
| `venue_type` | `Venue_Type` |
| `capacity` | `Capacity` |
| `building` | `Building` |
| `floor` | `Floor` |
| `lab_specialization` | `Lab_Specialization` |

All raw row values, including `Type_Data_Status`, `Capacity_Data_Status`,
`Location_Data_Status` and `Notes`, are retained in
`VenueImportResult.source_venues` with workbook/worksheet/row locations.
Source text, IDs, names, capacities and types are preserved exactly. The
configured location placeholder `To Verify` becomes `None` in the contract,
with a located warning; the original placeholder remains in source staging.
Combined-room IDs stay single source records with their exact supplied
capacity. The importer neither splits them nor derives room-sharing resources.

### Campus confirmation separate from original source labels

The project owner reports that the 62 supplied records, their types,
capacities and provided location information are campus-confirmed development
inputs. Older workbook labels such as `RANDOM SAMPLE / VERIFY` and old notes
remain untouched for traceability; they do not override the updated confirmation
or generate errors merely because their wording is older.

`VenueVerification` stores `campus_confirmed_development`, an explicit
`project_owner_reports_campus_confirmation` evidence source, the owner's
confirmation statement, confirmed fields, expected record count and SHA-256
of the exact workbook bytes. This is owner-reported confirmation, not a claim
that the Teaching Service independently audited the campus or obtained official
university master identifiers. The accepted confirmation is stored separately
in ignored `local-data/venues.campus-confirmation.json`, referenced by
`local-data/venues.mapping.json`. It covers supplied location information only;
availability, blocked periods and combined-room booking relationships remain
unresolved. The five previously unmapped building/floor pairs are now supplied
through the campus-confirmed location corrections described below.

Confirmation applies only when the source hash and row count match and input
validation succeeds. A changed workbook, incorrect count or invalid metadata
produces an error and `verification_applied: false`; confirmation is never
silently carried forward to a different dataset. A workbook without a
verification file can still be validated but reports `development_unverified`.
The example mapping in
[venues.mapping.example.json](examples/venues.mapping.example.json) is for
synthetic input and deliberately carries no campus confirmation.

### Local venue dry-run

```powershell
.\.venv\Scripts\python.exe -B -m schedence_teaching.excel_import.cli --config services/teaching-service/local-data/venues.mapping.json
```

The dry-run prints aggregate counts, separate updated verification metadata,
and located diagnostics without dumping venue records or notes. It never
saves the workbook or writes to a database. Exit status is 0 for valid input,
1 for dataset/verification errors and 2 for invalid mapping configuration.
Missing/duplicate headers, missing required values, invalid types/capacities,
duplicate venue IDs, formula/error input cells and unreadable workbooks are
validated. Duplicate IDs exclude all affected records from validated contracts
while keeping their source rows; no conflicting row wins silently.

The accepted dataset imports **62 venues**, with **zero errors and zero
duplicate IDs**:

| Source venue type | Count |
| --- | ---: |
| Lecture Hall | 31 |
| Computer Lab | 19 |
| Smart Classroom | 10 |
| BYOD Lab | 2 |

Capacities remain exactly supplied: 33 venues with 120 seats, 12 with 60,
11 with 30 and 6 with 240. All 62 venues now have building/floor values. The
four outstanding warnings are two combined-room booking relationships, one
missing availability dataset and one missing blocked-period dataset. The ten
previous missing-location warnings are resolved. None of the remaining warnings
challenges the owner's confirmation of supplied attributes.
The local aggregate report is saved as ignored `local-data/venues.dry-run.json`.

### Campus-confirmed location corrections

The project owner confirmed these exact mappings were provided during Step 7A:

| Venue ID | Building | Floor |
| --- | --- | ---: |
| CyberSecLab | G | 9 |
| Emblab | F | 11 |
| DClab | G | 9 |
| Robotics Lab | F | 11 |
| MMlab | G | 13 |

The original `Schedence_Actual_Venue_List_Sample_Capacities.xlsx` remains intact.
The active mapping now reads the corrected copy
`Schedence_Actual_Venue_List_Campus_Confirmed_Locations.xlsx` in `local-data`.
Only the ten `Venue_Master!E50:F54` values/types changed; all other cell values,
cell styles, worksheet layout and other workbook package parts were preserved.
Original status fields and notes remain unchanged even where their wording
predates these supplied corrections.

Ignored `local-data/venues.location-updates.json` records the user-provided,
campus-confirmed evidence, original/corrected checksums, each old/new cell
value and preservation checks. The original mapping, confirmation metadata and
report are backed up under `local-data/venues.before-location-corrections/`.
The active confirmation metadata retains its confirmation scope and now binds
to the corrected workbook checksum; the dry-run report was regenerated.

### Remaining venue integration requirements

Venue availability and blocked periods must be supplied explicitly; missing
data does not mean a venue is available all day or has no blocks. The two
combined-room entries need explicit booking relationships describing which
resources can be used independently or exclude each other. Session-to-venue type/specialization eligibility
requires an explicit policy rather than inferred rules based on names.
Authoritative offering IDs, streams, student groups and session requirements
remain necessary for real-workbook scheduling. Campus-confirmed venue
attributes alone do not make the timetable ready for university use; reports
keep `ready_for_scheduling: false` and `university_ready: false`.

Synthetic tests cover all four types, source preservation, positive capacities,
located errors, missing locations, combined rooms, duplicate identities,
formula rejection, separate confirmation and changed-source safeguards.
The shared source reader is reused by the existing wide teaching importer;
its existing behavior is preserved.

## Joint CP-SAT venue allocation (Step 7B)

`solve_timetable(prepared, config, venue_input=venue_inputs)` now chooses day,
time, one eligible staff member and one eligible physical venue in the same
feasibility model. There is no greedy room assignment after student/staff
scheduling and no quality objective. All occurrences in venue-enabled mode
are physical; every successful occurrence requires exactly one venue.
No occurrence is converted to online or venue-free mode to make it feasible.

Calling `solve_timetable(prepared, config)` retains the previous venue-free
mode. It returns `assigned_venue_id: null` and `venue_enabled: false`; it makes
no venue guarantees and does not imply that sessions are online. Venue-enabled
results, including unsuccessful results, explicitly report `venue_enabled: true`.
The independent validator also takes `venue_input=`; it rejects non-null venue
assignments if the venue inputs are omitted, avoiding unvalidated venue claims.

### Explicit venue scheduling contracts

The public contracts are exported from `schedence_teaching.solver` and reuse
the existing Step 7A `Venue`, `VenueType`, `VenueVerification` and Step 5
`TimeWindow`. The existing academic/teaching contracts are unchanged.

| Contract/field | Meaning |
| --- | --- |
| `VenueSchedulingInput.venues` | Supplied venue attributes; capacities and locations are preserved, never inferred. |
| `unresolved_venue_ids` | Required explicit declaration of unresolved booking identities; `[]` declares no unresolved venues for a synthetic input. |
| `VenueCalendar.available_windows` | Every configured day ID mapped to explicit windows; `None`, `{}` or an omitted day is unknown and rejected. `day: []` explicitly closes the venue on that day. |
| `VenueCalendar.blocked_periods` | Day/start/end intervals; `None` is unknown, while `[]` explicitly confirms no blocks in the configured horizon. |
| `SessionVenueRequirement` | Rules keyed by original `requirement_id`, applying to all its occurrences. |
| `allowed_venue_types` | Optional set of allowed source venue types; no default lecture/lab type rule. |
| `required_features`, `venue_features` | Exact explicit feature/equipment tokens; absent venue evidence cannot satisfy a required feature. |
| `required_lab_specialization` | Optional exact match against supplied lab specialization, separate from equipment/features. |
| `eligible_venue_ids`, `excluded_venue_ids` | Explicit restrictions; `None` allows any otherwise eligible venue, an empty eligible set allows none. |
| `CombinedVenueRelationship` | Explicit combined venue and component IDs; no parsing room names or adding their capacities. |
| `SharedVenueResource` | Explicit named physical resource shared by listed venue IDs; these are supplied scheduling keys, not generated institution identifiers. |

Input checks validate unique IDs, references, known requirement/day IDs,
positive capacities, supported venue types, noncyclic combined relationships
and calendar alignment with scheduling granularity. Mutated model instances
are snapshotted and revalidated. All bookable venues need explicit availability
and known blocked-period data, even if a particular requirement would exclude
them. A missing calendar produces `SolverInputError` with
`missing_venue_availability`, including omitted configured days; unknown blocks produce
`unknown_venue_blocked_periods`. Unresolved spaces are excluded, and any
combined venue depending on an unresolved component is also excluded. Blocks
on explicitly related excluded spaces must still be supplied when they could
affect a bookable shared resource.

### Joint model and shared-space rules

Eligible rooms must have capacity at least the original group's supplied
`student_count`, satisfy every explicit policy and have usable time. In
particular, a LAB occurrence can use a Lecture Hall if no explicit type or
equipment policy excludes it; no university eligibility rules are invented.

The model creates a Boolean choice and optional fixed-duration interval for
each usable venue candidate. `ExactlyOne` selects one room, sharing the existing
occurrence start/end variables with staff and student intervals. A conditional
integer start domain intersects stream windows with venue availability and,
for a combined venue, every component's availability. Explicit blocks are
subtracted from these windows; sessions cannot straddle gaps. Overlapping
blocked periods do not create conflicting reservations with each other.

`AddNoOverlap` applies to each venue and each explicitly shared physical
resource across all offerings and streams. If A uses components B and C, A
cannot overlap B or C, while B and C can still run concurrently unless another
explicit relationship says otherwise. Nested components and explicit shared
resource groups are supported. Blocks on a component prevent bookings of the
combined space; a block on the combined space also protects its components.
Touching endpoints are allowed and different configured days are distinct.
Existing staff, instructor, same-group and ancestor/descendant constraints
continue to apply, while sibling student overlap remains unknown.

No eligible room/time combination returns `INFEASIBLE` with an empty schedule
and a reason identifying the occurrence. Search results `INFEASIBLE`, `UNKNOWN`
and `MODEL_INVALID` never decode or emit partial staff/room assignments.
`OPTIMAL`/`FEASIBLE` schedules are returned only after independent validation.
Every result still reports `university_ready: false`.

### Independent physical timetable validation

The validator checks room existence, capacity, explicit type/features and
restrictions against original occurrence/requirement/group IDs. It checks
availability and blocks directly in minutes and traverses source combined/shared
relationships separately from the model's candidate-domain and resource builders.
It checks same-room and shared-space overlaps alongside all previous staff,
student, metadata, duration and time-window checks. Corrupted or incomplete
output raises `TimetableValidationError` with occurrence IDs and issue codes.
Validation summaries identify venue mode and list the additional venue checks.
Tests intentionally break the model's capacity filter/resource builder to
verify that invalid output still fails independently.

### Synthetic physical example

[venue-scheduling.example.json](examples/venue-scheduling.example.json) contains
explicit synthetic teaching contracts, calendars, room restrictions and solver
configuration. It defines two unrelated groups of 25, two 30-seat rooms and a
single synthetic 09:00–09:30 window. These times, features and identifiers do
not establish campus policies or availability.

```python
import json
from pathlib import Path
from schedence_teaching.schemas import TeachingTimetableInput
from schedence_teaching.preparation import prepare_timetable_input
from schedence_teaching.solver import SolverConfig, VenueSchedulingInput, solve_timetable, validate_timetable

example = json.loads(Path("services/teaching-service/examples/venue-scheduling.example.json").read_text())
prepared = prepare_timetable_input(TeachingTimetableInput.model_validate(example["teaching_input"]))
config = SolverConfig.model_validate(example["solver_config"])
venues = VenueSchedulingInput.model_validate(example["venue_input"])
result = solve_timetable(prepared, config, venue_input=venues)
print(result.to_dict())
if result.validation is not None:
    print(validate_timetable(prepared, config, result.schedule, venue_input=venues).to_dict())
```

The tested `OPTIMAL` result has two complete occurrences, one pair checked and
all independent checks passing:

| Occurrence | Assigned staff | Assigned venue | Synthetic day | Time |
| --- | --- | --- | --- | --- |
| REQ-A:1 | SYNTHETIC-STAFF-B | SYNTHETIC-ROOM-B | SYNTHETIC-DAY | 09:00–09:30 |
| REQ-B:1 | SYNTHETIC-STAFF-A | SYNTHETIC-ROOM-A | SYNTHETIC-DAY | 09:00–09:30 |

Offering A can use either staff member or room; offering B requires staff A
and room A. Both resources must therefore be selected jointly to make the
single-window timetable feasible. Tests also exercise genuine insufficient
staff/room/time/shared-space capacity and native timeout, returning no schedule.

### Corrected campus dataset bridge and remaining blockers

Use the existing active mapping, which references the corrected workbook:

```python
from pathlib import Path
from schedence_teaching.excel_import import VenueImportConfig, import_venue_excel
from schedence_teaching.solver import venues_from_import

path = Path("services/teaching-service/local-data/venues.mapping.json")
mapping = VenueImportConfig.model_validate_json(path.read_text())
imported = import_venue_excel(mapping, base_dir=path.resolve().parent)
venue_inputs = venues_from_import(imported)
```

`venues_from_import()` snapshots all 62 imported records and their existing
campus-confirmation metadata. It retains the unresolved booking IDs identified
by the Step 7A source diagnostics; a caller cannot erase them by passing an
empty unresolved set. Only supplied, validated combined/component relationships
resolve these diagnostics. Imported records and provenance cannot be overridden
through scheduling-evidence arguments. The bridge adds no availability, blocks,
features or preferences; supply explicit calendar/policy evidence via its
keyword arguments when that evidence becomes available.

The real corrected workbook was inspected through this bridge: all 62 source
attributes and confirmation metadata were preserved. `F1301+F1302` and
`F1303+F1304` remain excluded until their booking relationships are confirmed.
Attempting a venue-enabled solve with this dataset currently raises
`missing_venue_availability`, rather than inventing hours or scheduling online.

Real campus scheduling still requires venue calendars and confirmed block
information, combined-room relationships, explicit session venue/feature
policies, authoritative offering IDs/streams, student groups/hierarchy/overlap
and session demands. Lecturer availability/preferences and the other deferred
workload, administration, research, lunch and fairness policies remain
future work. No campus source workbooks or confirmation files are changed,
and no database, migration or scheduling API is added in Step 7B.

## Student group combination foundation (Step 7C)

`prepare_group_combinations()` generates optional combined lecture alternatives
from validated inputs. It does not choose between alternatives, book resources
or change the CP-SAT solver. Every original group, requirement and unscheduled
occurrence remains available for separate delivery. No accepted workbook,
academic master record or teaching assignment is modified.

### Supplementary contracts and permissions

`GroupCombinationInput` wraps the existing `TeachingTimetableInput` rather than
redesigning `StudentGroup`, `ModuleOffering` or `SessionRequirement`. Existing
input files, preparation functions and solver calls remain compatible.
The wrapper supplies explicit group specialization and combination evidence:

| Contract/field | Meaning |
| --- | --- |
| `teaching_input` | Unchanged groups, counts, study year 1–4, semester 1–2, stream, offerings, staff eligibility, session type, duration and weekly frequency. |
| `group_specializations` | Explicit specialization by existing group ID; never inferred from an offering, ID or name. |
| `GroupCombinationPolicy.allow_combination` | Default permission is false. Permitted delivery still has both combined and separate alternatives. |
| `max_groups`, `max_combined_students` | Optional supplied policy limits; neither has a university-wide default. |
| `RequirementCombinationPermission.allow_combination` | Optional per-requirement permission or veto; `None` inherits the global default. |
| `shared_content_reference` | Explicit shared lecture content across requirements; otherwise the offering ID defines the content scope. Module codes are never identity keys. |
| `GroupPopulationEvidence` | A referenced authoritative assertion that a set of groups is pairwise disjoint, or a specific overlapping/unknown pair. |
| `GroupMembershipEvidence` | Supplied student IDs, explicit completeness flag and authoritative evidence reference; complete membership counts must match the original group count. |
| `DeliverySelection` | Hypothetical chosen combined/separate occurrence IDs for coverage validation, not a solver result. |

Combining is restricted to LECTURE in this foundation. Groups must have the
same explicitly supplied specialization, academic year, semester and stream.
Requirements must have the same content identity, duration and weekly frequency.
Different offerings remain distinct even if their module codes match. A shared
content reference must be supplied consistently; an explicit reference is not
silently equated with an offering reference that happens to have the same text.
This adds no degree-type, rank, department or lecturer-preference policy.

An enabled requirement without group specialization or staff eligibility
receives a diagnostic and stays separate. Missing or incompatible combination
evidence does not remove its original teaching demand. Invalid contract values,
unknown references or contradictory evidence raise located `PreparationError`.

### Reliable disjointness and common staff

Different IDs, unrelated groups or siblings do not prove disjoint students.
The generator accepts a referenced authoritative disjoint-set assertion, or
two complete, nonintersecting membership rosters whose sizes match the supplied
group counts. A shared student in even a partial roster proves overlap; two
nonintersecting partial rosters cannot prove disjointness. Disjoint A/B and B/C
evidence does not imply disjoint A/C. Every pair within a larger combination
needs positive evidence. Parent/descendant hierarchy overlap is respected, and
contradictory roster/partition/hierarchy evidence is rejected.

The contract validates consistency and uses caller-supplied authoritative
evidence references/completeness declarations; it does not independently
authenticate those references. The Academic Data Service must eventually
supply reliable specialization, population and content evidence. This service
creates no competing academic master database. Student identifiers remain in
the input snapshot and are excluded from candidate reports and diagnostics.

Combined demand is the sum of original counts only after pairwise disjointness
is established. No subgroup partition size, count or population union is
invented. The foundation does not generate overlapping-population combinations.
Eligible staff are the intersection of offering teaching-team candidates across
**every** covered requirement. Pairwise staff intersections alone are insufficient
for three or more groups. LIC/Co-LIC roles and academic-only validation remain
unchanged; an explicitly eligible instructor Team Member is also supported.

### Candidate generation and coverage

The generator builds a compatibility graph and enumerates eligible subsets,
including pairs and larger combinations, without a manual Excel pairing list.
It prunes incompatible population/content/period/staff/policy branches.
`max_candidates=10000` is an operational guard against exponential alternative
growth, not an academic policy. Exceeding it raises an error without returning
a truncated list; callers can narrow explicit policy or adjust the limit.

`GroupCombinationCandidate` retains original requirement/group/offering IDs,
period, specialization, content scope, duration, frequency, combined count,
common staff IDs and population evidence references. It creates one
`CombinedLectureOccurrence` per weekly occurrence number, with exact links to
the corresponding original occurrences. Matching occurrence numbers form the
canonical coverage mapping; no teaching days or chronological order are inferred.
Generated `lecture-combination:<SHA-256>` IDs and their occurrence IDs are
stable alternative identifiers, not authoritative offering/group/database IDs.

A combined occurrence represents one future staff/venue booking and one
duration of teaching workload, while covering multiple original occurrences.
The candidate catalog is mutually alternative demand: its candidates are not
all scheduled, and their contact minutes must not be summed as actual workload.

`validate_delivery_selection()` regenerates alternatives from the validated
snapshot and checks that each original occurrence is fulfilled exactly once.
It rejects unknown choices, omissions, repeated choices, overlapping combined
choices and simultaneous combined/separate fulfillment of the same occurrence.
It permits fully separate, fully combined and mixed weekly delivery patterns.
Its `DeliveryCoverageSummary` counts selected deliveries/contact minutes once
per actual delivery alternative and explicitly reports `scheduled: false` and
`university_ready: false`.

### Synthetic example

[group-combination-input.example.json](examples/group-combination-input.example.json)
supplies two synthetic groups of 120, one specialization, one offering/common
staff member, a referenced synthetic disjoint partition and explicit permission.
Each group requires two 120-minute lectures per week. These are illustrative
contracts, not invented real academic records or university-wide hours/sizes.

```python
from pathlib import Path
from schedence_teaching.schemas import GroupCombinationInput, DeliverySelection
from schedence_teaching.preparation import prepare_group_combinations, validate_delivery_selection

inputs = GroupCombinationInput.model_validate_json(
    Path("services/teaching-service/examples/group-combination-input.example.json").read_text()
)
prepared = prepare_group_combinations(inputs)
candidate = prepared.candidates[0]
print(candidate.to_dict())  # 240 students; SYNTHETIC-STAFF is eligible for both requirements.
projection = validate_delivery_selection(prepared, DeliverySelection(
    combined_occurrence_ids=[occurrence.occurrence_id for occurrence in candidate.occurrences]
))
print(projection.to_dict())
```

| Hypothetical choice | Original occurrences covered | Delivery bookings projected | Teaching contact minutes |
| --- | ---: | ---: | ---: |
| Separate | 4 | 4 | 480 |
| Combined | 4 | 2 | 240 |
| First weekly lecture combined, second separate | 4 | 3 | 360 |

The candidate contains two shared occurrences, each covering the matching
original `SYNTHETIC-REQ-A` and `SYNTHETIC-REQ-B` occurrence. Three compatible
groups produce three pair alternatives and one triple; the triple can cover
six original weekly occurrences with two delivery bookings. All original
requirements remain intact in both examples. Tests cover the input/evidence
errors above, incompatible content/duration/frequency, staff intersections,
policy limits, stable IDs, privacy, preserved source demand and exact coverage.

### Step 7D integration requirements

Step 7D must add optional combined and separate interval choices to CP-SAT,
with exactly-one coverage across all alternatives for every original occurrence.
Each chosen shared delivery needs one staff choice from the common eligibility
pool and one physical venue booking using the combined student count. Venue
type/features/restrictions and calendars must satisfy all covered requirements.
The shared interval must participate in every covered group's hierarchy conflicts,
while staff, venue and shared-resource booking/workload counts occur only once.
An infeasible combined alternative must leave separate delivery available.

The benefit objective and any additional combination policies need explicit
definition; this foundation does not force every lecture to combine. The
schedule output and independent validator will need multi-group coverage and
resource validation. Real integration still needs authoritative offering IDs,
streams, student groups/specializations, reliable population/content evidence,
explicit session demands, venue availability/blocks/shared-space relationships
and the other previously deferred policies. No CP-SAT decisions, API, database
or workbook changes are part of Step 7C.

## Combined vs separate CP-SAT delivery (Step 7D)

The existing `solve_timetable()` now accepts optional `combination_input`, the
result of `prepare_group_combinations()`, and `CombinationSolverConfig` as
`combination_config`. Calls without combination input retain the previous
feasibility-only behavior and `ScheduledSession` output. Venue input remains
optional. The original academic contracts and source records are unchanged.

```python
from schedence_teaching.preparation import prepare_group_combinations
from schedence_teaching.solver import (
    CombinationSolverConfig, DeliveryObjective, solve_timetable,
    validate_delivery_result,
)

alternatives = prepare_group_combinations(inputs, max_candidates=1000)
options = CombinationSolverConfig(
    objective=DeliveryObjective.DELIVERIES_THEN_MINUTES,
    max_candidates=1000,
)
result = solve_timetable(
    alternatives.prepared, config, combination_input=alternatives,
    combination_config=options, venue_input=venues,
)
# Validate successful results; INFEASIBLE/UNKNOWN carry no completed timetable.
if result.validation is not None:
    validate_delivery_result(
        alternatives.prepared, config, result, combination_input=alternatives,
        combination_config=options, venue_input=venues,
    )
```

### Hard constraints and operational bounds

Each original occurrence and each eligible shared occurrence has a delivery
selection Boolean. For every original occurrence, the sum of all covering
alternative selections equals one. Each selected delivery receives exactly
one common eligible responsible staff member and, in physical mode, exactly
one eligible venue. Unselected deliveries have no staff/venue choice and only
absent optional intervals. They consume no time or resources and never appear
in the decoded timetable. Impossible optional combinations are disabled;
separate alternatives remain available. Weekly occurrences may independently
use combined or separate delivery, including larger validated combinations.

The same global staff, venue/shared-space and student-hierarchy `NoOverlap`
constraints apply to the alternative intervals. A combined interval occupies
every covered group; shared intervals are deduplicated inside each constraint.
Explicit overlapping-population evidence, including a shared student in partial
rosters, additionally prevents concurrent attendance. Those known overlaps
propagate upward to ancestor attendance, never downward to invent overlap
between child subsets. Unknown overlap adds no unsupported student policy.

Combined venue capacity uses the Step 7C proven-disjoint student sum. Every
covered requirement's venue type/features/specialization/eligible/excluded
policy must be satisfied. All candidates already have identical stream and
duration, so their stream/calendar start domains are identical for a given
venue. Existing venue availability, blocks and component/shared-resource
relationships remain enforced. No actual campus calendars are invented.

Inputs are regenerated and checked against the same validated teaching
snapshot. Default operational limits are 1,000 combination candidates, 10,000
delivery alternatives (including original occurrences), 100,000 staff/venue
choices, 200,000 model variables and 200,000 model constraints. These are
configurable safeguards rather than university policies. Exceeding a limit
raises `SolverInputError`; valid alternatives are never silently truncated.
Set the preparation generator's limit consistently with the solver's limit.

### Explicit objective and status

The default `DELIVERIES_THEN_MINUTES` objective lexicographically minimizes
actual delivery count, then actual lecturer contact minutes among solutions
with that count. Let `M` be total original contact minutes and `D` and `T` be
actual selected delivery count and contact minutes. The integer objective is
`D * (M + 1) + T`. Exactly-once coverage guarantees `0 <= T <= M`, so minutes
can never override the first priority. This is a priority ordering, not two
additive rewards for the same combination saving. Each selected shared
delivery contributes its duration once, irrespective of the covered group
count. It is contact time, not student-weighted demand or an enforced workload
limit. Integer objective magnitude is guarded before solving.

`DELIVERIES`, `CONTACT_MINUTES` and `FEASIBILITY` are alternative explicit
modes. The latter has no objective value/status. Deterministic input traversal,
sorted choices, a supplied seed and one search worker provide repeatable
choices for a fixed environment; equally good schedules are not guaranteed
to have unique times or remain identical across OR-Tools versions. No
unconfirmed fairness, preferences, travel or working-hour objective is added.

`OPTIMAL` means the solver proved the configured objective optimal within the
implemented model. `FEASIBLE` reports a validated solution without claiming
optimality. `UNKNOWN`, `INFEASIBLE` and `MODEL_INVALID` return empty schedules
and no successful validation or objective value. `university_ready` remains
false in every mode.

### Output and independent validation

Combination-enabled output uses `ScheduledDelivery`: stable alternative ID,
combined flag, exact original occurrence/requirement references, participating
groups, validated student count, duration, assigned staff/venue and day/times.
Result reports distinguish original occurrence count from actual delivery
count and include combined/separate counts, actual lecturer contact minutes,
objective mode/value and native solution status. Generated combination IDs
remain development alternative identities, not authoritative database IDs.

`validate_delivery_timetable()` regenerates the canonical candidate catalog,
rejects altered coverage/group/count/flag/occurrence metadata, and reuses
Step 7C `validate_delivery_selection()` for exactly-once fulfillment. It then
expands coverage views solely for the existing independent time/staff/student/
venue checks. Copies within one validated physical delivery do not clash with
each other; every covered offering's staff eligibility and venue policy is
still checked. Resource clashes with other deliveries remain prohibited.
`validate_delivery_result()` additionally recomputes and checks the reported
delivery counts, workload, modes and objective. Validation does not certify
source evidence or independently prove the CP-SAT optimality status.

### Executable synthetic examples and verification

Run from the repository root:

```powershell
.\.venv\Scripts\python.exe -B services/teaching-service/examples/combined-delivery-demo.py
.\.venv\Scripts\python.exe -B -m pytest services/teaching-service/tests -q
.\.venv\Scripts\python.exe -B -m pip check
```

The demo uses the existing sanitized example: two explicitly disjoint groups
of 120, one eligible staff member, two 120-minute lectures each, and supplied
synthetic calendars. It emits full, independently validated schedules.
For example, on `SYNTHETIC-DAY`, using `SYNTHETIC-STAFF` throughout:

| Scenario | Scheduled delivery coverage and times | Venue | Deliveries | Contact minutes | Objective |
| --- | --- | --- | ---: | ---: | ---: |
| Combined | A:1+B:1 08:00–10:00; A:2+B:2 10:00–12:00 | Large, capacity 240 | 2 | 240 | 1202 |
| Separate | B:1 08:00–10:00; A:2 10:00–12:00; B:2 12:00–14:00; A:1 14:00–16:00 | Small, capacity 120 | 4 | 480 | 2404 |
| Mixed | A:1+B:1 08:00–10:00; A:2 12:00–14:00; B:2 10:00–12:00 | Large for shared lecture, small for separate | 3 | 360 | 1803 |

All three scenarios cover four original occurrences exactly once and report
`OPTIMAL`. In the mixed case, the large venue is available only 08:00–10:00.
The table abbreviates `SYNTHETIC-REQ-A/B` and the synthetic venue IDs; exact
IDs and original metadata are retained in the emitted JSON. Equal-cost times
may differ; the table is an observed run, not a new scheduling policy.

Tests cover compatible larger combinations, separate fallback, mixed weekly
choices, common staff across explicitly shared content, capacity/calendars/
blocks, per-requirement venue policies, shared spaces, attendance conflicts
for any member, hierarchy and explicit roster overlap, cross-stream staff
conflicts, objective priority, workload once, exactly-once coverage, size
guards, corrupted metadata/reporting and backward compatibility. A native
tiny-time-limit test checks `UNKNOWN`; a controlled `FEASIBLE` status test
uses a real feasible assignment without relying on machine-dependent timing.

Real integration still requires authoritative offering IDs and streams,
student groups/specializations/hierarchy and reliable population/content
evidence, explicit durations/frequencies, venue availability/block data and
confirmed combined-room relationships. The accepted 184 module rows and
1,288 assignments remain staged; their absence of authoritative offering IDs
and streams is not hidden by this synthetic solver integration. Staff
availability/preferences and deferred workload, administration/research,
lunch and fairness policies remain later work. No datasets, database
objects, APIs or other services are changed by Step 7D.

## Confirmed teaching, administrative and attendance policy foundation (Step 8, corrected in Step 9A)

`schedence_teaching.workload` supplies configurable input contracts, explicit
policy resolution and pure weekly fulfillment assessment. This foundation
does not add CP-SAT workload/attendance constraints or change existing staff,
venue, stream or group-combination scheduling. Its reports explicitly state
`solver_enforced: false` and `university_ready: false`.

The clarified campus requirements separate **teaching hours** from
**administrative allocation**. The current campus preset is `TEACHING_CONTACT`:
administrative/other recognized work cannot reduce the teaching requirement.
The earlier combined-target interpretation is superseded. Explicit
`TOTAL_RECOGNIZED_WORK` policies remain for legacy/custom inputs, not the
clarified campus preset. All durations use integer minutes.

| Classification / appointment | Weekly teaching hours | Separate admin allocation hours | Compulsory campus days |
| --- | ---: | --- | --- |
| Instructor | 20 | Not supplied | Not supplied; no academic attendance rule inferred |
| Assistant Lecturer | 18 | Not supplied | 6 |
| Lecturer | 16 | Not supplied | 6 |
| Senior Lecturer | 12 | Not supplied | 6 |
| Dr. | 8 | Not supplied | 6 |
| Assistant Professor | 6 | Not supplied | 6 |
| Professor | 6 | 4 | Exempt |
| HOD appointment | 4 | 4 | Exempt |
| Dean appointment | 4 | 4 | Exempt |

`StaffWorkProfile` supplements the unchanged `StaffMember` with an explicitly
supplied `workload_band`, optional `appointment` and evidence reference. The
code never parses staff names or `position` to guess a policy. `DR` is a
supplied workload classification, not a claim that the title is an academic
rank. HOD and Dean are appointment overrides, not workload/academic ranks;
their rule takes precedence while the underlying classification and original
staff position remain unchanged. Assistant Professor does not inherit the
Professor attendance exception. Other positions or concurrent appointment
policies require an explicit upstream resolution; no precedence is guessed.

`WorkloadPolicy` carries a reference, target basis, configurable band rules
and appointment overrides. Each rule has `weekly_target_minutes`, optional
`compulsory_campus_days`, separate optional `administrative_allocation_minutes`
and a distinct exemption flag. Exempt rules cannot
also impose compulsory days. Removing a needed rule raises an error rather
than silently falling back. Instructor attendance is unspecified, not exempt;
it can be configured when confirmed. No campus opening hours, daily
availability, minimum teaching days or unsupplied administrative-hour amounts
are invented. Assistant Professors and lower classifications receive no
automatic Professor administrative allocation.

### Evidence and fulfillment

`WeeklyWorkloadInput` identifies the assessed scheduling week and supplies
profiles, recognized work and campus attendance evidence. It is an input
contract for the Teaching Service, not another academic master database.
The Academic Data Service remains authoritative for staff/appointments and
policy data; no API call or database write is added here.

`RecognizedWorkRecord` contains a unique work ID, exact staff ID, work type
(`ADMINISTRATIVE` or `OTHER_RECOGNIZED`), positive minutes and an evidence
reference. These are explicitly recognized credits, not guessed hours or
automatically completed administrative requirements. An upstream
`required_admin_hours_per_week` value alone is not evidence that work was
performed or otherwise recognized. Duplicate IDs and unknown staff references
are rejected. `recognized_work_complete_for` explicitly identifies staff whose
weekly credit ledger is complete. Without that evidence, administrative
completion below its allocation stays `NOT_VERIFIED`. Teaching fulfillment
is determined by the validated teaching schedule, independently of that ledger.

`CampusAttendanceRecord` separately supplies unique evidenced working-day
IDs, an explicit completeness flag and a reference. Teaching-day IDs are
reported separately and never converted into campus-attendance evidence or
daily availability. Six campus days can include days with no teaching.
Complete attendance evidence below a compulsory count reports
`BELOW_REQUIREMENT`; missing/partial evidence reports `NOT_VERIFIED` unless
it already proves sufficient days. Professor/HOD/Dean attendance reports
`EXEMPT`, while their workload target still applies.

`resolve_work_policy()` resolves one supplied staff/profile pair.
`assess_weekly_workload()` revalidates a successful solver schedule with the
original venue/combination inputs before crediting it. One combined physical
delivery contributes its duration once. It reports scheduled teaching,
recognized administration, other recognized work, their known total,
target-basis credit, target/status/shortfall and distinct teaching/campus days.
The target is explicitly assessed rather than interpreted as a maximum:
below a target with complete evidence is `BELOW_TARGET`, exact known credit
is `MET`, and excess known credit is `EXCEEDED`. Both `MET` and `EXCEEDED`
fulfill the target; no upper limit is invented. Enough explicit credit proves
fulfillment even if additional records remain unknown, with ledger
completeness still reported for explicit legacy combined policies. Under the
current teaching basis, administrative completion is assessed separately
against the confirmed allocation. Unclassified staff are listed as unassessed;
their policies and workload are not guessed.

```python
from schedence_teaching.workload import (
    StaffWorkProfile, WeeklyWorkloadInput, confirmed_campus_policy,
    assess_weekly_workload,
)

inputs = WeeklyWorkloadInput(
    week_reference="synthetic:one-scheduling-week",
    policy=confirmed_campus_policy(),  # Separate teaching + administrative requirements.
    profiles=[StaffWorkProfile(
        staff_id="SYNTHETIC-STAFF", workload_band="LECTURER", appointment="HOD",
        evidence_reference="synthetic:explicit-appointment",
    )],
    recognized_work_complete_for={"SYNTHETIC-STAFF"},
)
# prepared/config/result must come from the same successful synthetic solve.
report = assess_weekly_workload(prepared, config, result, inputs)
```

A Professor needs 360 teaching minutes and a separate 240-minute administrative
allocation; an HOD/Dean needs 240 teaching minutes and a separate 240-minute
administrative allocation. Exemption from six-day attendance does not waive
either requirement. Allocation/reservation does not prove completed work.
A Lecturer's 360 teaching minutes plus 600 admin minutes total 960 known work
minutes but still leave a 600-minute teaching shortfall against the 16-hour
teaching requirement. Six campus days need not all contain teaching sessions.
These are synthetic examples, not fabricated campus work records/availability.

Tests cover every confirmed target, appointment precedence, attendance
exceptions, missing/partial evidence, target shortfalls/excess, recognized
work categories, instructor separation, exact staff references, duplicates,
invalid minutes/days, combined workload once, schedule corruption and preserved
inputs. Step 8B below adds optional availability and teaching-cap enforcement;
real integration needs authoritative staff policy classifications/appointments
for the relevant week, recognized-work evidence and completion semantics,
and actual campus attendance/availability inputs.
Administrative blocking/working-time scheduling requires explicit intervals
and upstream policy resolution; this assessment does not allocate those hours
or claim they fit a campus calendar. Source datasets and existing solver code
remain unchanged.

## Dated staff availability and workload integration (Step 8B)

`solve_timetable(..., staff_input=StaffSchedulingInput(...))` enables staff
policy mode in the existing joint CP-SAT model. Omitting `staff_input`
preserves previous feasibility, venue and combination behavior. The existing
`WeeklyWorkloadInput` and campus preset remain the sole workload policy inputs;
no competing rank/appointment policy system is introduced.

### Explicit calendar contracts

`StaffSchedulingInput` contains the existing workload input, a date for every
configured scheduling-day ID, an inclusive policy-effective date range and
per-staff `StaffAvailability` records. The dates must be distinct and fit in
one seven-day scheduling period, with the policy applicable throughout.
Day IDs remain caller-supplied; no weekday/weekend campus-day rule is guessed.
Profiles/appointments must be resolved upstream for that referenced week.

Each staff availability record explicitly supplies:

- Exact existing staff ID and allowed teaching-day IDs.
- Available windows for every allowed day. `[]` explicitly means unavailable;
  absent windows are invalid. An empty allowed-day set plus `{}` explicitly
  permits no teaching days.
- Blocked intervals; `[]` explicitly means no supplied blocks. Missing or
  `None` block data is invalid rather than assumed clear.
- Inclusive availability-effective dates. Outside that range the staff member
  cannot teach, even if a window exists for the day.
- Optional nonnegative `daily_teaching_max_minutes` and
  `weekly_teaching_max_minutes`. `None` imposes no cap; zero forbids teaching.

Every staff member eligible for any demanded offering needs availability and
an explicit workload profile before policy-mode solving. Missing required
availability is a `SolverInputError`, not an unlimited-calendar fallback.
Unknown staff/day references, duplicate calendars, invalid intervals or
inconsistent dates/windows are rejected before building the model. Staff
with no applicable teaching eligibility do not require teaching calendars;
missing workload profiles remain visible as unassessed staff in the report.
There are no campus-hour defaults. The minute values and dates in tests and
the demo are explicitly synthetic inputs.

### CP-SAT hard constraints

For each delivery/staff assignment Boolean, the start domain intersects the
existing stream windows with that staff member's dated available windows,
then removes every overlapping blocked interval. The start-domain constraint
is enforced only when that staff member is selected. This stays inside the
same model as combination, venue and student decisions. A staff member with
no possible start cannot be selected; another eligible staff member or
another delivery arrangement may still make the timetable feasible.

Intervals are half-open, so touching a blocked endpoint is legal. Availability
and block edges need not align with solver granularity: valid starts round
inward, never into blocked time. Every session must fit one supplied window;
adjacent windows are not silently treated as permission to straddle boundaries.

Weekly teaching caps sum `duration_minutes * assigned_staff_boolean` over
actual alternatives. Daily caps additionally require that the delivery is
scheduled on the relevant day. These sums cover all offerings and both
streams. A selected combined lecture contributes once; separate lectures
each contribute once; unselected alternatives contribute zero. Model-size
guards also count the new conditional-domain/day/cap constraints and variables.

Only explicitly supplied teaching maxima are hard caps. The campus workload
target never becomes an implicit teaching maximum or lecture quota. The
solver does not invent extra sessions/admin time to make workload targets
balance. Existing global staff, student/hierarchy/population, venue/shared-space
clashes, capacity, calendar and combination-coverage constraints are retained.

### Results, evidence and independent validation

Successful policy-enabled results attach `workload_report`, serialized as
`workload_assessment`, and declare `staff_policy_enabled: true`. Reports reuse
the Step 8A assessment and include per-staff daily/weekly scheduled teaching,
verified supplied administrative/other credits, required target, known verified
total, shortfalls, attendance assessment and missing-evidence labels. With the
current teaching basis, `target_shortfall_minutes` is the definite teaching
shortfall; missing admin work cannot change it. Separate
`administration.completion_shortfall_minutes` stays `None` when missing
completion evidence could change the admin shortfall. Explicit legacy combined
policies retain their unverified-total-shortfall behavior. A partially known
ledger cannot make the overall report `COMPLETE`.

`completion_status` is `COMPLETE` only when all required classifications,
work evidence, teaching requirements, confirmed administrative completion
requirements and applicable attendance requirements are resolved and met.
Known shortfalls report `INCOMPLETE`; otherwise missing evidence reports
`NOT_VERIFIED`. Professor/HOD/Dean attendance is exempt, Assistant Professor
still requires six campus days, and instructor attendance remains unspecified.
Teaching days never populate attendance evidence, and an attendance shortfall
does not masquerade as an availability violation. No six-teaching-day constraint
is added. A scheduling-feasible result can have an incomplete workload report;
it is not a completed campus work plan.

The report separates `staff_availability_and_caps_enforced: true` from
`solver_enforced: false`: the latter describes workload-target/attendance
assessment, which is not a CP-SAT obligation to manufacture work. Missing
non-teaching evidence never makes the teaching timetable infeasible solely
because the weekly workload cannot yet be proven. No admin/research blocks
are generated from credit records. `INFEASIBLE`, `UNKNOWN` and `MODEL_INVALID`
results emit neither partial timetables nor successful workload reports.
Every result remains `university_ready: false`.

`validate_timetable(..., staff_input=...)` and the combined-delivery validator
independently check allowed days, effective dates, direct window containment,
block overlap and actual daily/weekly teaching totals. The validator uses
source minutes and canonical coverage, not the model's domain/cap builders.
Within-delivery coverage copies are deduplicated before totaling a shared
lecture. `validate_staff_policy_result()` accepts either scheduling mode and
also recomputes the attached workload/evidence/attendance report; missing or
corrupted reports are rejected. `validate_delivery_result(..., staff_input=...)`
provides the same checks for combined mode. To reassess a policy-enabled result,
pass the original staff/venue/combination inputs to `assess_weekly_workload()`.

### Synthetic demonstrations and tests

Run from the repository root using the existing virtual environment:

```powershell
.\.venv\Scripts\python.exe -B services/teaching-service/examples/staff-policy-demo.py
.\.venv\Scripts\python.exe -B services/teaching-service/examples/combined-delivery-demo.py
.\.venv\Scripts\python.exe -B -m pytest services/teaching-service/tests -q
.\.venv\Scripts\python.exe -B -m pip check
```

The new demo uses the existing two-group example, supplied synthetic dates,
staff/venue windows and recognized credits. All its successful schedules are
independently validated. One observed run on `SYNTHETIC-DAY` is:

| Scenario | Teaching schedule | Teaching min | Admin min | Target min | Workload/attendance outcome |
| --- | --- | ---: | ---: | ---: | --- |
| Combined Professor | Both groups 08:00–10:00, 10:00–12:00 and 12:00–14:00, large venue | 360 | 240 | 360 | Teaching and separate admin requirement met; attendance exempt |
| Separate HOD | Four separate lectures 08:00–16:00, small venue | 480 | 240 | 240 | Teaching exceeded; separate admin requirement met; no explicit cap |
| Mixed Assistant Professor | Combined 08:00–10:00; separate groups 10:00–12:00 and 12:00–14:00 | 360 | 0 | 360 | Target met; six-day attendance unverified |
| Lecturer, partial evidence | Two combined lectures 08:00–12:00 | 240 | 120 | 960 | Definite 720-minute teaching shortfall; missing ledger/attendance evidence |
| Explicit-cap infeasibility | Minimum feasible teaching is 240 minutes, explicit weekly cap is 239 | — | — | 360 | `INFEASIBLE`, empty timetable and no workload assessment |

The dates/windows are examples, not actual campus opening hours. Equally good
times may differ. The HOD example deliberately demonstrates that a four-hour
workload target is not automatically a four-hour teaching maximum.

86 new tests cover availability/configuration errors, blocked/unaligned edges,
effective dates, allowed days, explicit zero/daily/weekly caps, cross-stream
totals, combined/separate/mixed totals, target-vs-cap behavior, rank/appointment
exceptions, instructor ambiguity, partial work/attendance evidence and
shortfalls, native timeout, controlled `FEASIBLE`, corrupted schedules/reports,
independence from model builders, executable demos and unchanged inputs.
Step 8B verification before the Step 9A clarification: **847 tests passed**; `pip check`
passed. The existing Starlette/httpx deprecation warning remains.

Real campus integration still needs dated staff teaching availability and
blocks, any explicitly approved teaching caps, authoritative week-specific
policy profiles/appointments, recognized-work/completeness and attendance
evidence. Instructor attendance and actual daily operating hours remain
unconfirmed. Offering IDs/streams, groups/population/content evidence, session
demands, venue calendars/blocks and combined-room booking relationships also
remain prerequisites for the real dataset. Preferences, fairness, administrative
block generation, research days and lunch remain later work. No
accepted workbook, other service, database or API is changed.

## Separate administrative allocation foundation (Step 9A)

The clarification exposed a concrete Step 8 inconsistency: its default combined
target allowed a Professor's 240 teaching minutes plus 120 admin minutes to
fulfill a 360-minute target. The confirmed requirement is instead **360 teaching
minutes plus a separate 240-minute administrative allocation**. HOD and Dean
require **240 teaching minutes plus a separate 240-minute administrative
allocation**. The old example is short by 120 teaching minutes and 120 minutes
of recognized admin completion under the corrected policy.

`confirmed_campus_policy()` now defaults to `TEACHING_CONTACT` and assigns
`administrative_allocation_minutes=240` only to Professor and explicit HOD/Dean
appointment overrides. The factory allocation argument and individual
`WorkloadRule` values are configurable. Appointments preserve the underlying
academic classification and do not stack another four hours onto a Professor's
allocation. Assistant Professor/lower classifications get `None` (unconfirmed),
not an invented zero-hour rule. An explicit HOD/Dean appointment applies its
own override; rank alone never grants it.

### Backward compatibility

Existing `weekly_target_minutes`, target/status/shortfall fields and solver
entry points remain. The resolved policy additionally reports
`required_teaching_minutes` for teaching policies and the independent admin
allocation. Old serialized rules without the new field parse with `None`; old
weekly inputs omit reservations safely. Explicit `TOTAL_RECOGNIZED_WORK`
inputs retain prior accounting. The factory's explicit TOTAL option is labelled
legacy compatibility and adds no administrative requirement retroactively.
Reports flag `legacy_combined_target_policy: true`; those rules are not the
clarified campus preset. Custom admin allocation arguments are rejected for
the legacy factory instead of silently ignored.

The corrected default necessarily changes the old implicit combined-target
interpretation. Use the current campus preset for confirmed rules, and explicit
old policies where legacy compatibility is intended. Prior tests for combined
accounting now explicitly select legacy mode; default-policy/separation tests
exercise the clarified requirements.

### Allocation, reservation and completion

`AdministrativeReservation` is weekly planning metadata: exact staff ID,
unique reservation ID, positive minutes, `PROPOSED`/`APPROVED` status, evidence
reference and an explicit approval reference when approved. It belongs to
`WeeklyWorkloadInput.administrative_reservations`, with no day/time fields.
Unknown staff, duplicates and reservations without an applicable explicit
allocation/profile are rejected. A lower rank's proposed reservation cannot
create an administrative policy.

`AdministrativeAllocationAssessment` reports policy allocation, proposed and
approved reservation totals separately, unreserved minutes and planning status,
explicitly recognized completed admin minutes, completion status and
known/unverified shortfall. It always states `calendar_slots_assigned: false`
and `hard_blocks_generated: false`. Excess reservation metadata is visible
as `OVER_RESERVED`, without an invented scheduling restriction.

Only supplied `RecognizedWorkRecord` entries of type `ADMINISTRATIVE` credit
admin completion. Policy allocation and proposed/approved reservations do not
become completed work, attendance or teaching credit. Other recognized work
is reported separately and cannot replace either confirmed requirement.
Evidence references are supplied by the caller, not independently authenticated
by this layer. Missing completion records remain unverified when the ledger
is incomplete; no completed hours are fabricated.

The report distinguishes teaching-target fulfillment from
`all_administrative_allocations_fulfilled`. A Professor with six teaching hours
and no completed admin work has a met teaching target but incomplete admin
fulfillment. Six teaching hours plus four recognized completed admin hours can
fulfill both; HOD/Dean totals are four plus four. `COMPLETE` refers to supplied
work/attendance evidence, not a verified admin calendar or publishable timetable.

### Verification and Step 9B boundary

51 new tests cover separate requirements, rank/appointment scope, non-stacking,
the old substitution error, allocation vs planning vs completion, missing
evidence, configurable values, approval/duplicate/reference validation, legacy
serialized inputs, unchanged schedules and corrupted completion reports
rejected by the existing independent validator. **898 tests passed** and
`pip check` passed. The existing Starlette/httpx warning remains.
The updated `staff-policy-demo.py` uses six teaching hours plus four recognized
admin hours for its Professor example.

No solver intervals, availability or teaching caps change in this step.
Proposed and approved weekly reservation metadata both leave the timetable
unchanged. Before Step 9B, supply approved dated administrative intervals,
authoritative availability, booking/overlap semantics and completion-evidence
integration. No slots are invented, and no Academic Data Service, dataset,
database or API is modified.

## Administrative time and research-day foundation (Step 9A.2)

The previous steps already provide separate teaching/admin requirements,
Professor/HOD/Dean campus-day exceptions, weekly allocation/reservation
assessment, completion evidence, dated teaching availability and explicit
teaching caps. These remain in use. The original `AdministrativeReservation`
still represents weekly planning metadata; the new dated contracts extend
calendar preparation without replacing that accounting or changing CP-SAT.

The confirmed campus preset remains Professor 360 teaching minutes plus 240
admin allocation minutes, and HOD/Dean 240 teaching plus 240 admin allocation
minutes. Appointment overrides preserve the underlying academic classification.
Assistant Professors/lower classifications do not automatically receive the
four-hour allocation or Professor attendance exception. Explicit legacy
combined-target policies remain labelled and supported.

### Calendar and permission contracts

`workload/period_contracts.py` reuses the solver's existing `TimeWindow` minute
contract. Start/end minutes are half-open and within one date. All staff,
institution, appointment, policy and source IDs are supplied, never generated.
`PeriodProvenance` preserves a source, reference and optional source record ID.
Specific dates and recurring weekday selectors are mutually exclusive;
effective bounds are inclusive, with `None` meaning an explicitly open-ended
end date. `WorkPeriodInput` supplies the academic week reference and a bounded
calendar-date range of at most seven days.

| Contract | Purpose |
| --- | --- |
| `AdministrativeTimeReservation` | Staff/institution, policy and optional appointment reference, week/date or recurring weekday, effective dates, exact window/planned duration, approval/protection and provenance. |
| `AppointmentApplicability` | Supplied Academic Data Service appointment identity, position code, institution, appointment and policy effective dates, policy reference and provenance. |
| `AdministrativePolicyResolution` | Explicit upstream `COMPUTED`/`NOT_COMPUTED` aggregate, covered appointment IDs, allocation minutes, rule/reference, dates and provenance. No local aggregation rule is invented. |
| `FlexibleAdministrativePolicy` | Explicit permission for flexible scheduling and hard protection, linked administrative allocation policy, staff, effective dates and approval evidence. |
| `FlexibleAdministrativeRequest` | Requested duration, supplied dated eligible windows, protection, explicit split permission/minimum period, effective dates and approval. It describes future choices, not selected slots. |
| `ResearchDayPolicy` | Explicit per-staff permission for full/partial days and hard protection, optional allowed weekdays, effective dates, approval and provenance. Academic rank grants no research entitlement. |
| `ResearchDayReservation` | Specific date/weekday, full-day or explicit partial windows, effective dates, hard/soft protection, approval and source metadata. |

Period status is `PROPOSED`, `PENDING`, `APPROVED`, `CANCELLED` or `EXPIRED`.
Approved records require an approval reference. Cancelled/expired records can
retain historical approval provenance but produce no active protection.
Approved research/flexible reservations additionally need an applicable approved
permission policy. Hard protection must be permitted explicitly. References
are supplied evidence assertions, not independently authenticated approvals.

### Reusable preparation and conflict reporting

```python
from schedence_teaching.workload import prepare_work_periods

periods = prepare_work_periods(teaching_input, workload_input, work_period_input)
print(periods.to_dict())
# periods.protected_intervals: approved hard protection for future Step 9B.
# periods.soft_preferences: approved preferences, never hard blocks.
# periods.flexible_candidates: eligible windows, with no slot chosen.
# periods.diagnostics: pending, ignored, unverified and warning summaries.
```

The function revalidates independent snapshots of the existing teaching and
workload contracts and the new period inputs. Unknown staff/institution mapping
references, invalid appointment links, inconsistent dates, durations, duplicate
IDs and conflicting source resolutions raise the existing located
`PreparationError`. Every normalized interval retains its original source ID,
approval/policy/appointment references and provenance. A deterministic derived
interval ID distinguishes each source/date/window occurrence; it is not an
authoritative booking or database ID. No partial output is returned on errors.
The configurable `max_intervals=10000` safety limit also fails without truncation.

Only currently approved, applicable HARD records enter `protected_intervals`.
Approved SOFT records stay in `soft_preferences`. Pending/unverified permission
or appointment records produce diagnostics rather than fabricated restrictions.
Expired/out-of-week/cancelled data is retained in the snapshot and reported as
ignored. Approved hard intervals for the same staff/date cannot overlap;
overlapping soft preferences or pending-vs-approved reservations are reported
without giving them hard precedence. Touching endpoints remain legal.

A full research day represents protection of the entire requested calendar
date (0–1440), not a claim of 24 working hours, campus opening hours or actual
attendance. Partial research protects only the supplied windows. A Professor
with no explicit research policy receives no research protection.

Flexible candidates require both explicit approved policy permission and
approved requests. Their supplied windows are normalized as a union, so
overlapping eligible windows do not multiply available time. Approved hard
protection is removed from candidate windows with a diagnostic. Duration,
single-period fit or permitted split/minimum feasibility is checked in these
windows; no administrative period is chosen. Granularity, all resource calendars
and actual solver feasibility remain Step 9B concerns. Administrative windows
are not invented from teaching availability or unknown campus hours.

### Academic Data Service integration boundary

The existing Academic Data Service administrative-workload response contains
staff/institution IDs, dated appointments, policy versions, required hours and
an aggregate `COMPUTED`/`NOT_COMPUTED` result. It owns ADDITIVE/CAPPED/REPLACEMENT
semantics. This Teaching Service foundation consumes explicitly supplied dated
snapshots/resolutions and never implements another aggregation engine.

An HOD/Dean period needs an explicitly linked dated appointment snapshot.
Concurrent appointments require an upstream-computed resolution covering
every active appointment, with applicable source policy snapshots. Unresolved
aggregates stay unverified. An aggregate that conflicts with the configured
allocation raises a reconciliation error; neither source is overwritten.
When a single-appointment resolution is supplied, it must also be consistent.
An upstream no-appointment aggregate does not erase the separately confirmed
Professor allocation. Source policy changes across a week need applicable
dated snapshots; incompatible/overlapping resolutions are never given an
invented priority.

No Academic Data Service API is called or modified here. Real integration
needs explicit ADS staff-UUID-to-existing-teaching-ID mapping, source policy
versions/effective dates and exact validated hour-to-minute conversion.
Required admin hours in that API are allocation data, not completed-work
evidence or approval of particular calendar slots. No institution UUID is
fabricated where source data is missing.

### Completed work and backward compatibility

`RecognizedWorkRecord` gains optional `work_date`, `start_minute`, `end_minute`
and `credit_source_id`. The three timing fields must appear together and their
duration must match the supplied credit. Weekly workload validation rejects
overlapping timed evidence for one staff/date, including across ADMINISTRATIVE
and OTHER_RECOGNIZED categories, and duplicate exact credit-source identities.
Different staff/date evidence and touching intervals remain valid.

Legacy untimed work records and all old input shapes continue to parse. They
are preserved with an `untimed_work_evidence` preparation diagnostic: their
temporal overlap cannot be proven from missing timestamps. Generic evidence
references are not incorrectly treated as unique work IDs. Timed evidence
outside the explicitly assessed week is rejected during period preparation.

Neither allocation nor proposed/approved fixed or flexible reservations create
completed-work records. Preparation reports `work_minutes_credited: 0`,
`attendance_inferred: false` and `solver_enforced: false`. Existing recognized
work and attendance inputs remain unchanged. A supplied completed-work record
matching a reservation is still counted once, not once for work plus once for
the reservation. The existing workload assessment preserves separate teaching
and admin fulfillment and its independent result validation.

### Synthetic examples and verification

Run from the repository root:

```powershell
.\.venv\Scripts\python.exe -B services/teaching-service/examples/work-periods-demo.py
.\.venv\Scripts\python.exe -B -m pytest services/teaching-service/tests -q
.\.venv\Scripts\python.exe -B -m pip check
```

The executable demo contains only explicit synthetic records:

| Example | Supplied dates/windows | Preparation output |
| --- | --- | --- |
| Approved admin + research | Admin 2026-10-19 09:00–13:00; explicitly permitted Friday research on 2026-10-23 | Two approved hard intervals; zero credited work |
| Pending admin | 2026-10-19 09:00–13:00, pending approval | Pending diagnostic; no protected interval |
| Soft research | 2026-10-21 14:00–16:00, approved SOFT | One soft preference; no hard restriction |
| Flexible admin | 240-minute request inside supplied 2026-10-20 09:00–13:00 window | One candidate; no selected/admin booking |

**987 tests passed**, including all 898 prior tests and 89 new tests.
`pip check` passed; the existing Starlette/httpx deprecation warning remains.
New tests exercise approvals/statuses, dated appointments, concurrent source
resolutions, full/partial/soft research, flexible authorization/splitting/window
budgets, conflicts, effective dates, scope/provenance, work-evidence overlap,
legacy inputs, unchanged workload/attendance/source records and the demo.

Step 9B must consume and independently revalidate this preparation alongside
the original source snapshots and staff calendars, map dates to configured
scheduling days, apply approved hard protection to teaching, and introduce
optional flexible admin interval choices with explicit coverage and granularity.
It must reconcile fixed and flexible planning against the administrative
allocation without treating either as completed work, preserve shared staff
conflicts across streams, and decide explicit soft-preference scoring later.
Real authorized admin/research periods, applicability, availability, institution
mapping and completion/attendance evidence are still needed. No current CP-SAT
constraints, source workbook, Academic Data/Identity Service, database or API
is changed by Step 9A.2.

## Step 9B: joint teaching and protected work-period scheduling

Step 9B consumes Step 9A.2 preparation in the existing CP-SAT model. Legacy
calls still use the same teaching constraints and objective. No new policy
engine, API, database integration or real calendar data is introduced.

```python
from schedence_teaching.solver import (
    WorkPeriodSolverConfig, solve_timetable, validate_work_period_result,
)
from schedence_teaching.workload import prepare_work_periods

# Supplied teaching input, original dated staff calendars and approved source periods.
periods = prepare_work_periods(
    prepared.input_snapshot, staff_input.workload_input, work_period_input,
)
calendar = WorkPeriodSolverConfig(
    day_weekdays={"MONDAY": "MONDAY"},
    day_streams={"MONDAY": {"WEEKDAY"}},
)
inputs = dict(staff_input=staff_input, work_periods=periods,
              work_period_config=calendar)
result = solve_timetable(prepared, config, **inputs)
validate_work_period_result(prepared, config, result, **inputs)
```

For venue/combination mode, also pass the original `venue_input`,
`combination_input` and optional `combination_config` to both functions.
`validate_staff_policy_result`, `validate_delivery_result` and
`assess_weekly_workload` accept the same work-period context. Result validation
rejects omission of that context for a work-period-enabled result.
Schedule-only validators continue to check teaching constraints; use a result
validator for administrative/research protection and reservation reports.

### Explicit dates and protected staff resources

`StaffSchedulingInput.day_dates` must supply a distinct date for every solver
day. `WorkPeriodSolverConfig` additionally supplies its weekday and applicable
streams; both maps must cover exactly the configured days. Dates must fall
within the prepared academic week, weekday declarations must match the actual
dates, and named weekdays must match their labels. Declared streams must match
the configured stream windows. No Monday/weekday or Saturday/weekend rule is
inferred. Include non-teaching calendar days explicitly, with empty stream
windows/declarations when appropriate. Protected and eligible flexible dates
cannot silently disappear through an absent day mapping.

The original workload snapshot must match the staff scheduling snapshot.
Period preparation is regenerated and checked at consumption and validation,
including staff identities, source permissions, effective dates, approvals,
appointment applicability and week references. Changed derived intervals or
candidates are rejected.

Approved applicable HARD administrative and research periods become constant
intervals on the existing global staff resource. The same `NoOverlap` protects
selected separate/combined/mixed teaching, both streams and selected flexible
administration. Unselected alternatives consume no resource. Generic staff
teaching availability/blocks and explicit teaching caps remain unchanged.
Pending, proposed, expired, cancelled, unverified and SOFT periods do not create
hard protection. Soft records remain in reports without an optimization score.
Full-day research blocks its calendar date, with zero completed-work credit.

Fixed source minute values are preserved. Non-grid boundaries are rounded
outward only inside the model, excluding exactly the grid-aligned teaching
intersections. Independent checks use the original minute intervals. Endpoint
touching remains legal. The existing `TimeWindow` class now lives in
`schemas/time.py`, with its old `solver.config` and `solver` import paths
preserved. Moving it prevents a circular workload/solver import dependency;
its contract and validation have not changed.

### Explicit flexible reservation choices

`FlexibleAdministrativePolicy` gains `reservation_required: bool | None`.
`True` requires an exact reserved duration; `False` allows the entire request
to be omitted. `None` preserves old preparation inputs but is rejected at the
solver boundary: mandatory/optional policy is not guessed.

Each approved, authorized request has a presence decision and administrative
intervals inside its supplied eligible windows. If splitting is forbidden,
selection requires one continuous interval. Explicit split permission requires
the supplied minimum block size; all selected fragments sum to the exact
requested duration. Grid boundaries round inward and split minima round up,
without changing the requested minute total. A request whose exact duration
cannot be represented on the grid cannot be selected.

Requests cannot overlap the assigned staff member's teaching, approved hard
periods or other selected flexible requests. Administration uses its explicitly
approved eligible windows; teaching availability is not interpreted as campus
office hours. No allocation alone generates a calendar reservation.

For an explicitly mandatory/optional policy, insufficient prepared windows
produce a warning and a retained candidate. The joint solver returns
`INFEASIBLE` for a mandatory request that cannot fit; an optional request can
be omitted. Older policies with unspecified reservation obligation retain the
previous preparation error for insufficient windows. Invalid/unverified source
permissions remain preparation errors or diagnostics as before.

Optional reservations have no added optimization reward and are not guaranteed
selection. The existing combined-delivery objective remains delivery count,
then actual teaching contact minutes. Mandatory administration and exact
original teaching coverage are constraints, so neither can be dropped to
improve that objective. There is no fairness, research preference, travel or
lunch objective. Configurable interval/block/model limits reject excessive
inputs without truncating requests.

### Results, evidence and independent checks

`SolverResult.schedule` still contains only actual teaching deliveries.
`administrative_schedule` contains selected flexible physical reservations,
with stable `administrative:{request_id}:{ordinal}` identities, dated times,
source policy/approval/appointment/institution metadata and provenance.
Identities describe derived occurrences, not authoritative database bookings.
`work_period_report` includes approved fixed administrative/research protection,
soft preferences, omitted optional requests, preparation diagnostics and staff
summaries. Runtime/objective/status retain their existing meanings.

Staff summaries keep required teaching, scheduled teaching, administrative
allocation, fixed/flexible reserved time, verified completed administrative
work, verified other work and attendance evidence separate. They report
unreserved/excess reserved minutes and missing evidence. Extra approved
reservations are reported rather than converted into an invented allocation
cap. Legacy untimed administrative plans remain in the original workload
assessment and are not added again to physical calendar reservation totals.
Approved SOFT fixed periods are preferences, not confirmed occupied bookings.

Professor remains 360 teaching + 240 administrative allocation; HOD/Dean
appointment overrides remain 240 + 240 without changing academic rank.
Assistant Professor receives neither the Professor administrative allocation
nor the attendance exemption. All other confirmed teaching targets remain
unchanged. Targets are assessed, not imposed as automatic maximum caps or
new compulsory teaching constraints. Campus attendance remains evidence-based,
never inferred from teaching days, reservations or research protection.

Only original recognized-work evidence contributes completed work. Reserving
240 minutes does not establish administrative completion, and planned research
does not become verified other work. A combined lecture contributes its actual
duration once, regardless of covered group requirements. Results always keep
`university_ready=false`.

`validate_work_period_result` reuses existing teaching, staff, group, venue and
combination validators. Additional checks independently use dated source-minute
comparisons for hard protection, eligible windows, approval metadata, exact
flexible coverage, split/minimum rules and cross-commitment staff clashes.
Reports are recomputed from validated physical bookings and original evidence.
Corrupted/missing reservations, changed source protection, invented completion
credit and changed summaries are rejected. Unsuccessful statuses emit no
partial teaching/admin schedule, fulfillment report or objective value.

Run the synthetic hard/flexible demonstrations and full checks from the root:

```powershell
.\.venv\Scripts\python.exe -B services/teaching-service/examples/work-period-solver-demo.py
.\.venv\Scripts\python.exe -B -m pytest services/teaching-service/tests -q
.\.venv\Scripts\python.exe -B -m pip check
```

The demo uses an explicitly synthetic Monday (2026-10-19), two proven-disjoint
groups, three 120-minute shared lectures and one Professor. Both successful
examples reserve 240 admin minutes and schedule 360 teaching minutes, with
60 minutes of approved partial research protection. Restricting teaching to
480 minutes makes the fixed example infeasible; restricting both teaching and
the explicitly supplied flexible eligible window to 480 minutes also makes
the mandatory-flexible example infeasible. Teaching windows alone do not
restrict approved administrative windows.
Reserved admin completion remains unverified. These windows are fixtures,
not campus opening hours.

Step 9B verification: **1,092 tests passed** (all 987 previous tests plus 105
new tests). `pip check` found no broken requirements. The existing
Starlette/httpx deprecation warning remains. Both fresh-process import orders,
hard/flexible demonstrations, mandatory/optional infeasibility, split and grid
rules, mixed/combined venue scheduling, protected-period corruption and
evidence/report integrity are covered. No dependency versions were changed.

Real scheduling still needs authoritative offering IDs/streams, student groups
and population evidence, session demand, staff classifications/eligibility and
dated availability, venue availability/blocked periods/shared-space relations,
authorized academic-week/date mappings, actual approved administrative/research
policies and periods, and completion/attendance evidence. Academic Data Service
identity/appointment/policy reconciliation remains an input integration task.
Accepted Excel source values and assignments are untouched.

## Step 10A: lecturer preferences and post-schedule scoring

This step adds soft input contracts, a projection of existing Excel staging and
post-solve evaluation. The solver engine, objective, hard constraints and old
input contracts are unchanged. Preferences do not generate availability,
administrative/research protection, teaching caps or attendance evidence.

### Inspected workbook and source interpretation

`local-data/Schedence_Lecturer_Preferences_Department_Aligned.xlsx` has four
sheets: `Lecturer_Preferences`, `Instructions`, `Lists`, and `Solver_Mapping`.
The preference table has 24 headers on row 4, followed by 90 distinct academic
lecturer records. No instructor preference records are required.

| Actual source fields | Step 10A treatment |
| --- | --- |
| `Staff_ID`, `Staff_Name`, `Department`, `Position` | Check against existing imported Staff Master. Preserve IDs exactly; reject unknown/duplicate IDs and conflicting profile metadata. |
| `Preferred_Free_Day` | Named day becomes a soft AVOID-day expression; `No Preference` produces no invented selection. |
| `Monday_Morning` / `Monday_Afternoon` through `Sunday_Morning` / `Sunday_Afternoon` | `Preferred` becomes a preferred day-part; `Not Preferred` becomes an avoided day-part. Keep the labels without inventing minute boundaries. |
| Day-part value `Available` | Neutral source capability, retained in staging; no preference penalty or hard window is generated. |
| Day-part value `Not Available` | Retain as a source unavailability assertion needing confirmation and explicit hard-availability integration. It becomes neither a soft substitute nor a hard block here. |
| `Weekend_Work_Pattern` | Actual values are `Saturday Only`, `Sunday Only`, and `Saturday and Sunday`. Their meaning as soft preferences versus availability is left unresolved. Explicit translations require a separate confirmation reference. |
| `Expected_Working_Days` | Retain separately as an attendance/work-pattern assertion. Never translate into compulsory teaching days or override the confirmed role-specific attendance policy. |
| `Can_Work_Both_Weekend_Days` | Retain `Yes`/`No` as a capability assertion requiring availability confirmation. |
| `Special_Notes` | Retain unstructured text in private source staging; never parse or echo it into diagnostics. All 90 real cells are blank. |

`Instructions` mentions an academic role, but the actual preference table has
no `Academic_Role` column. No such column or role value is invented.
The workbook also supplies no academic week/effective dates, stream scope,
numeric morning/afternoon boundaries, explicit preference strengths or
confirmed weight-policy provenance.

The source `Solver_Mapping` sheet suggests selection costs `Preferred=0`,
`Available=2`, `Not Preferred=6`, and `Not Available=Hard constraint`.
`Instructions` likewise describes `Not Available` as hard. These original
template rules are preserved as source information, without promoting them
to confirmed campus policy. Selection costs are not automatically equivalent
to the new contracts' per-violation weights. Both meaning and authority need
confirmation before Step 10B; no 0/2/6 weights or hard restrictions are applied.

Thirty-three real lecturer/day combinations mark both day-parts `Preferred`.
The projection preserves both source-cell expressions and gives them an
explicit preferred-alternative group for that staff/day. Either matching
day-part satisfies that group once per delivery. This avoids penalizing a
lecture merely because it cannot occupy both preferred periods at once.

### Contracts and preparation

The new `schedence_teaching.preferences` package reuses `StaffMember`,
`TeachingTimetableInput`, `Stream`, `Weekday`, shared `TimeWindow` and existing
period provenance conventions. It adds:

- `PreferenceExpression`: staged source intent with staff/preference IDs,
  category (`WORKING_DAY`, `WEEKEND_DAY`, `TIME_OF_DAY`, `TEACHING_WINDOW`),
  PREFER/AVOID direction, selected days, explicit window or time-of-day label,
  optional stream/strength/weight, confirmation status and source provenance.
- `LecturerPreference`: the same expression with required `PreferenceScope`.
  Scope requires an academic-week reference or effective-from date, optionally
  an inclusive end date. When both week and dates exist, all apply.
- `LecturerPreferenceInput` and `prepare_lecturer_preferences`: validate
  unique IDs, exact staff references, input snapshots and explicit alternative
  groups. Conflicting preferences produce review diagnostics, not infeasibility.
- `PreferenceCalendar`: explicit week reference, bounded dates and mapping of
  every solver day to a distinct date. Named weekdays must align. Existing
  staff calendars and protected work-period week/date mappings must agree.
- `PreferenceScoringPolicy`: referenced configuration for time-of-day windows
  and weight mode. No morning, afternoon or campus-hour defaults are supplied.

Preferred alternatives share category, applicability and weight policy, and
are evaluated as one OR expression while retaining every member preference ID.
AVOID expressions remain separate restrictions on satisfaction. Other source
records are not silently merged. Multiple ungrouped positive records are
separate criteria; preparation flags repeated or potentially competing intent
for review. A set of days in one day preference represents alternative days.

`strength` is retained as supplied text; no rank-to-number conversion is made.
An explicit numeric `penalty_weight` requires a `weight_reference`. Confirmed
weight mode additionally requires `confirmation_status=CONFIRMED`, asserting
the supplied policy's authority; the service does not authenticate that source.
Unknown and UNCONFIRMED preferences can be assessed as supplied soft intent,
with confirmation counts reported. REJECTED records are not applied.

Excel candidates intentionally remain `PreferenceExpression` staging objects
until an actual scope is supplied. `expression.with_scope(scope)` creates the
full typed contract. Derived source-preference and alternative-group IDs are
traceable diagnostic identities, not authoritative database IDs.

### Excel integration and local dry-run

`excel_import/preference_adapter.py::map_lecturer_preferences` consumes the
unchanged `WideImportResult`, including its original source cells and validated
staff records. It does not replace the generic `LecturerPreferenceRecord` or
re-read/rewrite source worksheets. Each expression preserves workbook,
worksheet, row, column, source-row reference and interpretation reference.
Missing/ambiguous selections produce located diagnostics. No real preference
records, IDs, profile values or source assignments are changed.

The public configuration example contains only inspected column/value mappings
and unresolved settings, without staff records:
`examples/department-aligned-preference-mapping.example.json`.
`scope`, `stream` and confirmation status remain unset. Weekend translations
remain empty until their soft semantics are confirmed.

Run from the repository root:

```powershell
.\.venv\Scripts\python.exe -B -m schedence_teaching.excel_import.cli `
  --config services/teaching-service/local-data/department-aligned.mapping.json `
  --preference-config services/teaching-service/examples/department-aligned-preference-mapping.example.json
```

Without `--preference-config`, the existing import CLI behavior is preserved.
Summaries contain aggregate counts and located messages, with no staff names,
IDs, source preference values or private notes. The local, ignored report is
`local-data/preferences.step10a.dry-run.json`; it also records the inspected
workbook checksum and the unconfirmed template-rule review.

| Real-workbook result | Count |
| --- | ---: |
| Preference rows / mapped academic staff | 90 / 90 |
| Soft source-cell candidates | 594 |
| Preferred day-parts / avoided day-parts | 295 / 219 |
| Preferred-free-day expressions | 80 |
| Explicit `No Preference` free-day selections | 10 |
| Neutral `Available` day-part cells | 514 |
| `Not Available` cells retained for hard-availability confirmation | 232 |
| Preferred day-part alternative groups / groups with two choices | 262 / 33 |
| Scoped contracts / invented dates, weights or streams | 0 / 0 |

There are no mapping errors in the current real inputs. Located warnings cover
514 missing time-boundary definitions, 90 ambiguous weekend patterns, 232
unavailability assertions, 90 attendance assertions, 90 weekend-capability
assertions and 90 rows needing applicability scope. All 594 expressions lack
explicit weights and stream scope; absence of stream scope is reported rather
than inferred from weekend labels. Original wide-import diagnostics remain
separate and unchanged. These are accepted development inputs, not independently
verified official university records, and are not yet a real scored timetable.

### Post-schedule scoring methodology

```python
from schedence_teaching.preferences import (
    PreferenceScoringPolicy, prepare_lecturer_preferences, score_lecturer_preferences,
)

preferences = prepare_lecturer_preferences(prepared.input_snapshot, preference_input)
policy = PreferenceScoringPolicy(policy_reference="supplied:unweighted-policy")
report = score_lecturer_preferences(
    prepared, config, result, preferences, calendar, policy,
    staff_input=staff_input, work_periods=periods, work_period_config=work_calendar,
    combination_input=combinations, venue_input=venues,
)
print(report.to_dict())  # Aggregate output; individual details require explicit opt-in.
```

Supply exactly the original resource/combination/work-period inputs enabled for
the solved result; omit disabled modes. The function rejects unsuccessful or
partial results and reuses existing independent timetable, staff, group,
venue, coverage and administrative/research validation before evaluation.
It cannot reschedule teaching or override hard constraints.

Each selected physical teaching delivery contributes once for its assigned
staff, regardless of participating group count. Unselected alternatives and
administrative/research intervals are absent from preference penalties and
teaching metrics. Each applicable preference criterion is checked:

- Preferred days are satisfied by a listed day; avoided days by other days.
  Weekend-day criteria apply only to Saturday/Sunday teaching. They do not
  penalize weekday teaching merely for missing a weekend selection.
- Preferred windows require the whole delivery to fit; avoided windows are
  violated by any positive overlap. Touching endpoints are legal.
- Time-of-day labels use only configured, referenced windows. Without the
  relevant definition, evaluation is UNRESOLVED rather than guessed.
- Day selectors on clock preferences limit applicability to those days.
  Inclusive effective dates, academic-week and optional stream scope also
  filter applicability.
- Explicit preferred-alternative groups are satisfied if any branch matches.
  A known match suffices even if another branch is unresolved; otherwise an
  unresolved branch prevents declaring a violation or complete weighted total.

Reports distinguish SATISFIED, VIOLATED and UNRESOLVED outcomes; category and
staff totals; violated deliveries; weighted known subtotal and complete total;
unconfirmed/inactive records; diagnostics and descriptive teaching metrics.
Violation counts mean preference-criterion/delivery pairs, not unique bookings.
One delivery can violate different independent criteria; an explicit OR group
counts once. Evaluation completeness refers only to supplied scoped contracts,
not completeness or verification of the original source dataset.

Weight modes are explicit:

| Mode | Meaning |
| --- | --- |
| `UNWEIGHTED` (default) | Satisfaction/violation diagnostics only; weighted totals are `None`. |
| `CONFIRMED_WEIGHTS` | Use only explicitly supplied weights with reference and CONFIRMED status. Missing weights leave the total `None`, with a known subtotal and diagnostic. |
| `SYNTHETIC_DEFAULTS` | Caller supplies a default number and policy reference; allowed only when `PreferenceCalendar.synthetic_data=True`. Reports label these as synthetic weights, never campus policy. |

Each evaluated violation contributes its explicit weight, a satisfaction zero.
Unresolved criteria prevent claiming a complete weighted total. A missing
weight is never silently replaced with zero or one. Soft violations do not
make the native timetable INFEASIBLE. No new preference score is attached to
`SolverResult` or fed back into CP-SAT in Step 10A.

### Fairness preparation for Step 10C

Staff descriptors include actual teaching minutes, actual delivery count,
teaching-day count, daily minute distribution and longest consecutive teaching
duration. Consecutive means adjacent half-open teaching intervals on one day;
any positive gap ends the run. No maximum-run, lunch or travel rule is inferred.
Existing workload reports already supply role/appointment policy, required
teaching minutes, recognized admin/other work and independent attendance status.

When original validated staff policy is available, the report supplies
`teaching_target_ratio = scheduled_teaching_minutes / required_teaching_minutes`.
Otherwise target and ratio remain unset, including legacy combined-work targets.
Do not rank staff by raw equal-hour differences: Professor 360, HOD/Dean 240
and Instructor 1200 teaching-minute targets differ. Required targets are not
automatic teaching-capacity maxima; availability, explicit caps, assignment
eligibility and demand must also be considered. Keep administrative obligations,
recognized work and six-day attendance/exemptions separate. Preference scores
also require comparable input completeness before any cross-staff comparison.
No fairness objective or ranking is implemented.

### Verification and Step 10B prerequisites

```powershell
.\.venv\Scripts\python.exe -B services/teaching-service/examples/lecturer-preferences-demo.py
.\.venv\Scripts\python.exe -B -m pytest services/teaching-service/tests -q
.\.venv\Scripts\python.exe -B -m pip check
```

The synthetic demo preserves an OPTIMAL timetable with two combined Sunday
lectures covering four originals, 240 actual teaching minutes and the unchanged
teaching objective. Three soft criteria yield six evaluations, two satisfactions
and four violations. An explicitly synthetic weight of 2 yields penalty 8;
the schedule remains feasible. Demo clock bands and weights are fixtures only.

Before Step 10B, confirm applicability periods/streams, numeric day-part windows,
weekend-pattern semantics, availability authority and selection-cost versus
violation-weight semantics. Supply actual scopes and policy references, and
decide preference priority relative to the existing validated teaching objective.
Step 10B must link costs to selected delivery/staff/time decisions only, respect
preferred OR groups, preserve all hard protections and coverage, bound integer
objective arithmetic, and compare model penalties with this independent scorer.
Missing data must remain unresolved rather than silently generating costs.
Authoritative offerings, groups/population evidence, session demand and resource
calendars remain prerequisites for any real-campus optimization.

Step 10A verification: **1,197 tests passed** (all 1,092 previous tests and 105
new tests). `pip check` found no broken requirements. The existing
Starlette/httpx deprecation warning remains. Tests cover scoped preferences,
day/weekend/clock satisfaction, explicit alternative groups, missing weights
and boundaries, source identity/provenance/ambiguity, hard protection taking
priority, mixed/combined delivery counting, role-aware descriptors and the
synthetic demo. Existing solver/workload code, prior tests, source workbooks
and original local mappings/reports are unchanged; the new local preference
report remains ignored by Git. No dependencies, databases or APIs were changed.

## Step 10B: opt-in CP-SAT lecturer preference optimization

Preference optimization is disabled by default. The new solver arguments are
`preference_input` (existing `PreparedLecturerPreferences`), `preference_calendar`,
`preference_policy` and `preference_config`. Supplying optimization options
without prepared input is an error. No Excel staging result is activated
automatically, and no source workbook or original assignment is modified.

### Explicit input and policy requirements

Use the existing Step 10A preparation and contracts. Optimization requires:

- At least one applicable criterion for an eligible teaching staff member.
  Academic-week/effective-date applicability and exact staff IDs are checked
  again; changed preparation diagnostics are rejected.
- A `PreferenceCalendar` mapping every solver day to its actual date. Named
  weekdays, weekly bounds, staff calendars and protected-period dates must
  agree. No real date is inferred from an abstract day identifier.
- `PreferenceOptimizationConfig` with an explicit priority-policy reference,
  calendar-semantics reference and `day_streams` matching every configured day.
  The required `unscoped_preferences_apply_to_all_streams` boolean explicitly
  states whether an absent per-record stream means all streams; otherwise an
  applicable unscoped record is rejected. Weekend labels never imply a stream.
- Explicitly referenced morning/afternoon boundaries for applicable day-part
  records, including every branch of a preferred-alternative group.
- An explicit weighted `PreferenceScoringPolicy`. `UNWEIGHTED` remains useful
  for assessment but cannot optimize. Real-data mode requires CONFIRMED
  applicable preference contracts and confirmed explicit weights/references.
  `SYNTHETIC_DEFAULTS` requires an explicit numeric default and
  `PreferenceCalendar.synthetic_data=True`; it cannot activate real data.

Confirmation references/statuses are supplied evidence assertions, not an
independent authentication of university approvals. Default priorities remain
development configuration even when the caller supplies confirmed preferences.
Missing applicability, weights, semantics or time definitions raise
`SolverInputError` before optimization; they are not translated into hard
constraints or silently assigned zero cost. Rejected/out-of-week/inapplicable
records stay in the source snapshot and assessment diagnostics rather than
contributing fabricated penalties. No compulsory instructor preference records
are introduced.

```python
from schedence_teaching.solver import (
    PreferenceOptimizationConfig, solve_timetable, validate_preference_result,
)

optimization = PreferenceOptimizationConfig(
    priority_policy_reference="development:teaching-before-soft-preferences",
    calendar_semantics_reference="supplied:validated-week-and-stream-semantics",
    day_streams={day.day: set(day.windows) for day in config.days},
    unscoped_preferences_apply_to_all_streams=True,  # Explicit caller assertion.
)
preference_args = dict(
    preference_input=prepared_preferences, preference_calendar=calendar,
    preference_policy=weighted_policy, preference_config=optimization,
)
result = solve_timetable(prepared, config, **original_resource_inputs, **preference_args)
validate_preference_result(prepared, config, result, **original_resource_inputs, **preference_args)
```

`original_resource_inputs` contains exactly the original enabled combination,
venue, staff and administrative/research inputs. Existing result validators
accept the same optional preference arguments and reject missing context for
an optimized result. `assess_weekly_workload` also accepts them. To re-run the
public `score_lecturer_preferences` on an optimized result, supply its original
`preference_config` alongside the existing preference/calendar/policy arguments
and original hard-resource context.

### Exact soft-penalty variables

`solver/preference_constraints.py` adds per-delivery, per-eligible-staff,
per-criterion variables for applicability, satisfaction, violation and integer
penalty. Applicability and satisfaction are fully reified membership predicates
over the original global start variable; their complements are enforced too.
The violation is exactly `selected_staff AND applicable AND NOT satisfied`.
The penalty is exactly explicit weight times violation.

Unselected staff and unselected separate/combined alternatives contribute zero.
A combined physical booking is evaluated once for its actual assigned lecturer;
separate bookings are evaluated separately. Mixed schedules are supported.
No lecturer inherits another lecturer's preference penalty. Staff with no
supplied applicable criteria receive no invented criteria; reports show input
coverage, not a claim that all their preferences have been collected.

The predicates preserve Step 10A semantics:

- PREFER-day sets and AVOID-day sets operate on the mapped calendar weekday.
  Preferred free days are soft AVOID-day selections, not attendance evidence.
  Weekend-day criteria apply only to actual Saturday/Sunday deliveries.
- Preferred clock windows require whole-delivery containment. Avoided windows
  are violated by any positive intersection; endpoint touching is legal.
- Day-part labels use supplied windows, with exact integer ceil/floor bounds
  for non-grid policy endpoints. Crossing a preferred boundary is a soft
  violation unless another explicitly preferred branch contains the delivery.
- Explicit preferred-alternative groups use the union of their satisfaction
  domains and one charge. Source records/provenance and all member IDs remain
  preserved. Acceptable morning and afternoon alternatives do not impose two
  separate obligations. Crossing both is not automatically equivalent to
  fitting wholly in either; no unstated union-of-hours campus rule is added.

None of these predicates requires satisfaction. Hard availability, teaching
caps, student/staff/venue clashes, exact coverage/duration and mandatory
administrative/research commitments remain the original constraints. A soft
violation does not make a feasible timetable infeasible. Attendance requirements,
role exemptions and teaching/admin/completed-work distinctions are unchanged.

### Bounded lexicographic objective

The default development strategy is hard feasibility, then minimum delivery
count, then minimum actual lecturer contact minutes, then minimum preference
penalty. Hard feasibility remains constraints, never a weighted objective tier.
For explicitly selected legacy combination objective modes, preserve their
teaching tiers: DELIVERIES uses count, CONTACT_MINUTES uses minutes and
FEASIBILITY has no teaching objective. Non-combination bookings have fixed
count and contact minutes, so only preferences can change their objective.

Let `N` be the number of original required occurrences, `T0` their summed
minutes, and `Wmax` the maximum sum of all applicable criterion weights for
any one eligible staff member. Preferred OR groups contribute one weight.
Exact coverage and one responsible lecturer per booking establish:

```text
0 <= deliveries <= N
0 <= contact_minutes <= T0
0 <= preference_penalty <= N * Wmax
```

Starting from the lowest-priority coefficient 1, each earlier coefficient is
the product of `(lower_tier_bound + 1)`. It therefore equals one plus the
maximum possible contribution of all lower tiers. A one-unit improvement in
an earlier tier dominates every possible lower-tier change. These are derived
integer bounds, not arbitrary preference/trade-off weights. The model rejects
an objective bound above `2**60` before integer overflow, without truncation.

`PreferencePriority` explicitly configures later research experiments:

| Strategy | Priority with default teaching mode |
| --- | --- |
| `TEACHING_THEN_PREFERENCES` (default) | Deliveries, contact minutes, preference penalty |
| `PREFERENCES_THEN_TEACHING` | Preference penalty, deliveries, contact minutes |
| `DELIVERIES_THEN_PREFERENCES_THEN_MINUTES` | Deliveries, preference penalty, contact minutes |

The between-tier strategy requires the count-then-minutes teaching mode.
These configurations never relax hard constraints. They are development
priorities, not confirmed university optimization policy. A single CP-SAT solve
uses the derived bounded equivalent; no per-phase time-budget or optimality
assumption is hidden. Configurable record/term/model-size limits fail rather
than dropping criteria or alternatives.

### Output and independent validation

`SolverResult` gains `preference_optimization_enabled` (default false) and an
optional `preference_report`. The report includes the Step 10A score, source
and priority policy identifiers, configured/confirmed criterion counts,
modeled selected-delivery charges, staff summaries, full priority order,
component values, bounds, coefficients, encoded objective value and native
objective status. Original source preferences remain in their input snapshot.
Synthetic default penalties are labelled separately from confirmed-weight
penalties; they do not become confirmed campus scores.

In preference mode, legacy `objective_value` still reports the existing teaching
component and `objective_status` becomes `EVALUATED_COMPONENT` where that
component exists. The complete optimization value/status live in
`preference_report`. This avoids falsely claiming that a legacy teaching
component is itself globally optimal when an explicitly chosen research
strategy prioritizes preferences first. Disabled mode retains its original
objective behavior and metadata.

`validate_preference_result` retains original hard validators and independently
re-runs Step 10A scoring in source minutes. It never consults CP-SAT domain
predicates to establish satisfaction. It compares every modeled charge's
delivery, staff, original preference IDs, violation and penalty with actual
selected-delivery evaluations; missing, duplicate or unselected charges fail.
It also recomputes score/staff/confirmation summaries and the objective's
components, bounds, coefficients, priorities and exact integer value.
Corrupt native objective values and altered reports are rejected.

OPTIMAL and FEASIBLE are reported honestly: a feasible incumbent is not labelled
preference-optimal. Independent validation checks correctness of the emitted
solution and reported objective, not a second proof of global optimality.
INFEASIBLE, UNKNOWN and MODEL_INVALID results contain no partial timetable,
administrative bookings, preference score, fulfillment report or objective
value. Active unresolved or incompletely weighted criteria cannot claim a
complete optimized score. All results retain `university_ready=false`.

### Synthetic verification and campus integration blockers

Run from the repository root:

```powershell
.\.venv\Scripts\python.exe -B services/teaching-service/examples/preference-optimization-demo.py
.\.venv\Scripts\python.exe -B -m pytest services/teaching-service/tests -q
.\.venv\Scripts\python.exe -B -m pip check
```

The synthetic demo first solves without preferences, then explicitly requests
the other available day-part. The verified run moves one combined lecture
from 09:00 to 14:00 and reduces its synthetic penalty from 5 to 0. It still
covers two originals in one actual 120-minute delivery. Its dates, day-part
windows and weight are synthetic inputs, not university operating policy.

Priority tests include a 100,000-point preference penalty that cannot buy an
extra delivery under the default strategy. At equal three-delivery coverage,
the default selects 120 contact minutes with a 10,000-point penalty over 150
minutes with zero penalty. Explicit preference-first/between-tier experiments
reverse those choices as configured. Additional tests compare CP-SAT's minimum
with exhaustive source-minute scoring of every feasible start/day in small
fixtures, and verify corrupt model predicates cannot pass independent scoring.

The real workbook remains inactive: 90 rows, 594 unscoped soft expressions,
232 unconfirmed unavailability assertions, 90 ambiguous weekend work patterns
and unconfirmed 0/2/6 template selection costs are unchanged. Before real use,
obtain authoritative week/effective dates, stream/all-stream applicability,
day-part boundaries, preference/weight confirmation and selection-cost versus
violation-weight semantics, explicit weekend-pattern interpretation and
confirmed objective priorities. Any approved `Not Available` information must
enter the separate hard staff availability contract, not be silently promoted
from preferences. Offering/group/session/resource-calendar prerequisites also
remain. No fairness, travel, database, migration, API or other-service work is
introduced.

Step 10B verification: **1,291 tests passed**, including all 1,197 previous
tests and 94 new tests. `pip check` found no broken requirements. The existing
Starlette/httpx deprecation warning remains. The synthetic optimization demo,
fresh import orders, objective priority/bounds tests, exhaustive small-instance
scoring comparisons, hard protection, mixed/combined booking counts, corrupted
scores and unsuccessful/non-optimal statuses all passed. Source workbooks,
local data/mappings/reports, original tests and Excel importer are unchanged.
No dependencies were changed, and no branch, commit, push or database operation
was performed.

## Step 10C.1: independent fairness assessment foundation

`schedence_teaching.fairness` evaluates a complete, independently validated
selected timetable. It does not change CP-SAT objectives, constraints, staff
assignments, group-combination choices or solver output contracts. The existing
preference evaluator and fairness assessment share the validation dispatcher in
`solver/evaluation.py` and physical-booking metrics in `workload/metrics.py`.
Campus workload rules still come exclusively from the existing workload policy
resolver; no second rank/appointment policy table is introduced.

### Contracts and scope

`FairnessPolicy` requires a policy identifier, academic week, source provenance
and an explicit comparison strategy. `EXPLICIT_GROUPS` uses supplied
`ComparisonGroup` staff IDs and a reason for each cohort.
`IDENTICAL_ELIGIBILITY_POOLS` builds deterministic cohorts with identical sets
of offering IDs among the supplied session demand. It does not equate all
staff, infer department cohorts or assume that matching eligibility proves
matching availability. Automatic cohort IDs are local report identifiers.

Policies support:

- `TEACHING_TARGET` normalization (default) or an explicit `RAW_MINUTES`
  descriptive experiment. Raw minutes are never the default comparison across
  ranks. Targets remain required evidence for cohort inclusion in either mode.
- Maximum absolute deviation from the cohort mean, mean absolute deviation, or
  population variance. Variance and maximum-deviation output are configurable.
- An optional relative-deviation threshold, measured as
  `abs(value - cohort_mean) / cohort_mean`. Exceeding it is a descriptive flag,
  never a hard violation or an assertion that a person is unfairly underloaded.
- Optional positive rational staff weights with a required weight source
  reference. Unweighted assessment gives each included member equal weight.
  There is no generated aggregate fairness objective or campus-confirmed weight.
- Inclusion/exclusion decisions with explicit reasons. Inclusion cannot override
  missing targets, teaching-team eligibility or supplied hard restrictions.
- `EXCLUDE` missing-evidence behavior (default) or explicit `ERROR` mode for
  requested comparison members. Explicitly excluded members retain metrics and
  diagnostics without causing strict comparison-evidence failure.
- Optional `eligibility_offering_ids`. This scopes eligibility evidence and
  cohort construction, **not** the weekly teaching numerator or role target.
  Each staff member's actual workload still includes all selected weekly
  deliveries. Comparison cohorts may overlap; their summaries are not additive.

Weights and thresholds accept integers, rational strings such as `"1/2"`, or
`Fraction` values. Floating-point policy inputs are rejected to avoid silently
rounded thresholds. Assessment defaults are development configuration choices,
not additional campus policies.

The existing `PreferenceCalendar` is reused for explicit academic-week bounds
and solver-day dates; it is not a preference prerequisite. Fairness, staff,
work-period and any enabled preference calendars must agree. No calendar
opening hours or campus attendance are inferred from teaching days.

### Calculations and evidence

`assess_fairness(prepared, config, result, calendar, policy, **original_context)`
requires the original contexts of every enabled solver mode. It revalidates
coverage, eligibility, clashes, venues, staff calendars, explicit caps,
administrative/research protection, and any attached workload/work-period/
preference reports before computing fairness. Unsuccessful or incomplete solver
results are rejected. `validate_fairness_report` regenerates the assessment from
the same sources and rejects altered metrics, cohorts, metadata or policies.
It does not use a caller's workload totals as evidence.

Per-staff results include physical delivery count, actual teaching minutes,
teaching dates/days, daily minutes (including zero on supplied days), maximum
daily load and longest consecutive teaching run. Adjacent sessions on the same
day form a run; any gap resets it. Combined lectures count once per physical
delivery, independently of the number of covered student groups. Mixed and
separate delivery use the same calculation. Original requirements are preserved.

`teaching_target_metrics(actual_minutes, target_minutes)` returns exact rational
utilization, shortfall, excess and descriptive target status. Missing targets
stay missing. Explicit zero produces `ZERO_TARGET` and no ratio. The existing
`WorkloadRule` continues to reject zero policy targets, preserving the earlier
input contract and tests; the reusable arithmetic helper safely handles zero
for a future authoritative adapter. Invalid or mutated zero workload policies
are rejected rather than bypassing that contract.

Confirmed teaching targets remain Instructor 1,200; Assistant Lecturer 1,080;
Lecturer 960; Senior Lecturer 720; Dr. 480; Assistant Professor 360; Professor
360; HOD/Dean 240 minutes. HOD/Dean overrides preserve the underlying rank.
Professor/HOD/Dean administrative allocation remains a separate 240 minutes.
Neither administrative allocation, reserved periods nor verified administrative
completion contributes to teaching utilization. Assistant Professors do not
inherit Professor attendance exemptions or administrative allocation.

An explicit legacy `TOTAL_RECOGNIZED_WORK` policy does not establish a separate
teaching target and cannot support normalized comparison. Missing classification
or an unresolved appointment override is diagnosed without rank inference or
fallback. Concurrent appointment evidence is inspected across the supplied
academic week: unresolved administrative aggregation is reported, and even an
upstream-computed administrative allocation does not establish which competing
teaching override applies. Such staff remain descriptively visible but are
excluded pending an authoritative teaching-policy reconciliation. This layer
does not duplicate Academic Data Service appointment aggregation.

Teaching targets are not caps. A utilization above one reports excess teaching
and remains valid unless a supplied daily/weekly teaching maximum is violated.
No extra session is fabricated to meet a target. Reports keep explicit caps,
administrative allocation, evidenced completed administration, ledger
completeness, protected research time, reservations and attendance separate.
A full-day research interval can protect 1,440 clock minutes; that is **not**
1,440 minutes of completed research work.

Opportunity records preserve eligible offering, requirement and original
occurrence IDs, derived only from teaching-team membership. With supplied
staff calendars, `individually_available_occurrence_ids` additionally checks
stream windows, effective dates, allowed days, blocked periods, granularity,
single-session fit under explicit caps and supplied hard protections (including
selected flexible administrative bookings). This reuses the existing staff
domain calculation without building or solving another CP model.

These opportunity counts are **not** jointly schedulable delivery capacity:
they do not reserve student/venue resources, solve competition with other
teaching, allocate remaining weekly capacity, or enumerate combination
alternatives. Their IDs/counts are not additive across staff or combined groups.
Flexible-period fit is conditional on the selected administrative schedule.
Protection checks cover supplied contracts, not proof that every real duty has
been supplied. Missing availability is excluded by default; explicitly allowing
membership-only comparisons preserves an availability warning. Zero-assignment
staff remain visible. No eligible demand or no individual fit produces an
explicit exclusion reason, not an unfair-underload verdict.

Cohort reports include exact member distributions, weights, mean, selected
balance statistic and optional variance/maximum deviation. Population variance
uses `sum(weight * (value - mean)^2) / sum(weight)`. Relative deviation is
undefined for a zero mean and is reported as missing with a diagnostic.
Empty groups have no invented score; fewer than two comparable members produces
a diagnostic. Fractions serialize as numerator/denominator pairs. Reports carry
the fairness policy/provenance, applicable week, missing-evidence diagnostics
and `cp_sat_fairness_objective_enabled=false`.

### Synthetic example and verification

Run from the repository root:

```powershell
.\.venv\Scripts\python.exe -B services/teaching-service/examples/fairness-assessment-demo.py
.\.venv\Scripts\python.exe -B -m pytest services/teaching-service/tests -q
.\.venv\Scripts\python.exe -B -m pip check
```

The example explicitly authorizes a synthetic cross-role comparison; every
module, group, classification, date and availability window is synthetic.
Thirty-minute occurrences produce the following weekly totals:

| Classification | Actual teaching | Teaching target | Exact utilization |
| --- | ---: | ---: | --- |
| Lecturer | 720 minutes | 960 minutes | 3/4 |
| Dr. | 360 minutes | 480 minutes | 3/4 |
| Professor | 360 minutes | 360 minutes | 1 |

There are 48 physical deliveries and 1,440 teaching minutes. The explicitly
requested three-person cohort has mean utilization `5/6`, population variance
`1/72`, maximum absolute deviation `1/6`, and maximum relative deviation `1/5`.
Professor administration remains a separate 240-minute allocation with no
fabricated completion or reservation. This example assesses an existing solve;
it does not optimize fairness.

Real integration still needs authoritative workload profiles and dated teaching
overrides, reconciled concurrent appointments, explicit comparison policy and
any approved fairness weights/thresholds, complete scoped eligibility, staff
calendars and protected duties. Existing offering IDs, streams, groups,
disjoint-population evidence, session demand and venue calendars also remain
prerequisites; staged workbook rows cannot substitute for them. Recognized-work
completion and attendance need separate evidence.

For Step 10C.2, a suitable research starting point is a bounded integer-scaled
maximum normalized deviation, followed by mean absolute deviation, **below the
existing delivery/contact-minute/preference tiers** and within explicitly
approved eligibility cohorts. Exact rational scaling and objective coefficient
bounds must be checked before building a model. Keep every hard resource,
availability, protection and coverage constraint unchanged, handle missing
evidence explicitly, and independently recompute the new objective from
selected physical deliveries. Objective priorities, cohort definitions and
weights still require an explicit research/campus decision. No such fairness
objective is implemented in Step 10C.1.

Step 10C.1 verification: **1,394 tests passed**, including all 1,291 previous
tests and 103 new synthetic fairness tests. `pip check` found no broken
requirements. The existing Starlette/httpx deprecation warning remains. The
standalone fairness demonstration and fresh fairness/preference/solver/FastAPI
imports passed. Tests cover exact normalization, all confirmed classifications,
appointment overrides and unresolved concurrent appointments, combined/mixed/
separate bookings, eligibility pools, availability and protected duties,
zero/missing-target arithmetic, empty and zero-utilization cohorts, explicit
caps, consecutive/daily load, cohort filters, optional weights/statistics,
strict source references, corrupted summaries and report regeneration. A test
also prevents constructing a CP model during assessment. Existing tests,
source datasets, importer, workload policy definitions and solver objectives/
constraints are unchanged. No dependency, database, API or other-service change
was made. Work remains local and uncommitted on `feat/teaching-timetable`.

## Step 10C.2: optional fairness-aware lecturer allocation

`solve_timetable` accepts optional `fairness_policy`, `fairness_calendar` and
`fairness_config`. Supplying a validated `FairnessOptimizationConfig` explicitly
enables fairness; the default path retains the existing objective, assignments
and output keys. Supplying a policy/calendar without the enabling configuration
is rejected. This is a synthetic/development optimization strategy, not a newly
confirmed university fairness policy.

### Input contracts and comparison membership

The existing `FairnessPolicy`, comparison groups, staff decisions, provenance,
eligibility scopes and `PreferenceCalendar` academic-week contract are reused.
Teaching targets are resolved exclusively through the existing staff workload
profiles, rank rules and appointment overrides. All confirmed teaching targets,
separate Professor/HOD/Dean administrative allocations, attendance policies,
research protections and explicit caps retain their existing meanings.

Fairness mode requires original `StaffSchedulingInput`, its explicit calendars
and workload evidence. The policy, staff, work-period and enabled preference
calendars must agree on the applicable week. Missing/zero teaching targets,
legacy combined teaching-plus-work targets, missing classifications and
unresolved appointment teaching overrides reject the run. Assessment-only
`EXCLUDE` behavior cannot silently hide missing required optimization evidence.
Explicitly excluded comparison staff remain eligible for their original
teaching assignments. Staff outside the comparison retain workload diagnostics.

Real-data mode additionally requires explicit `target_verification_reference`
and `policy_approval_reference` in the optimization configuration. These record
caller-supplied authority/approval evidence; the service does not independently
verify university records or contact an academic master database. Synthetic
examples explicitly set `calendar.synthetic_data=true`.

Configuration requires these explicit declarations:

```python
FairnessOptimizationConfig(
    priority="EXISTING_OBJECTIVES_THEN_FAIRNESS",
    priority_policy_reference="synthetic:fairness-last",
    deviation_reference="TEACHING_TARGET",
    cohort_rule="INPUT_ELIGIBILITY_AND_FIXED_CALENDAR",
)
```

The cohort rule applies the supplied comparison policy, offering eligibility and
individual fit under supplied calendars and **fixed** hard protections. It
records `outside_comparison_groups`, explicit exclusions, no eligible demand,
or no individual fit as applicable. Membership is frozen before CP-SAT makes
assignments or flexible administrative bookings. A selected flexible booking
cannot remove an included person to improve the reported fairness score.
Input opportunity records remain available for audit and do not establish
joint feasibility, venue capacity or residual teaching capacity.

Only teaching-target normalization with the maximum-deviation balance policy
is schedulable in this step. Raw-minute/variance policies remain available for
descriptive Step 10C.1 assessment. Active optimization cohorts must be disjoint;
overlapping membership is rejected rather than silently double weighting staff.
Multiple disjoint cohorts share one global maximum-deviation tier. Optional
positive rational `cohort_weights` reference existing cohort IDs and require a
`cohort_weight_reference`. They multiply the existing optional policy staff
weights for the aggregate tier only; an omitted weight contributes an explicit
equal-weight factor of one. Real-data use requires the policy approval evidence
above. None of these weights is asserted to be a campus default.

### Exact model calculations

For included staff `i`, let `W_i` be actual selected teaching minutes and `T_i`
the positive, resolved teaching target. The target deviations optimized here
are `abs(W_i / T_i - 1)`. Step 10C.1's cohort-mean dispersion remains a separate
descriptive statistic; it is not silently reinterpreted as target deviation.

The model reuses the original staff-choice Booleans:

```text
W_i = sum(delivery_duration * selected_staff_choice)
L   = lcm(all included T_i)
U_i = W_i * (L // T_i)
D_i = abs(U_i - L)
M   = max(D_i)
A   = sum(integer_weight_i * D_i)
```

There are three integer variables per included staff member plus global maximum
and aggregate variables. Workload equalities, exact `AddAbsEquality` and
`AddMaxEquality` constraints link them to existing choices. No float solver
expressions or approximate normalization are used. Zero assigned deliveries
produce utilization zero and normalized target deviation one, without being
removed from an otherwise included cohort.

Combined lectures contribute one physical duration to one assigned lecturer;
separate and mixed selections contribute their actual bookings. Unselected
alternatives contribute zero. Every weekday/weekend stream contributes to the
same weekly workload. Original occurrence coverage, common teaching eligibility,
student/staff clashes, venues, availability, protection and cap constraints
remain intact. Targets introduce no hard caps or exact-fulfillment constraints.

Optional rational effective weights use a second exact LCM `Q` over their
denominators. `integer_weight_i = effective_weight_i * Q`. Reports recover exact
maximum deviation as `M/L` and aggregate deviation as `A/(L*Q)`, together with
absolute target deviation in minutes. No administrative allocation, reservation,
completed administration, protected research time or attendance is added to
`W_i`.

Default development safety limits are normalization scale 1,000,000, weight
scale 1,000,000, integer expression bound `2**50`, 1,000 comparison staff,
and 200,000 model variables/constraints. Exact LCM or expression overflow risks
reject the input before solving; targets/weights are never rounded or dropped.
The LCM of all confirmed campus teaching targets is 43,200. The two-target
synthetic example below uses `L=1,440` and unweighted `Q=1`.

### Lexicographic priorities and timeout handling

The explicit configuration appends fairness to the existing configured objective
order. With normal teaching-first preference configuration, the provisional
development order is:

1. Mandatory hard feasibility and exact teaching coverage.
2. Minimum physical delivery count.
3. Minimum lecturer contact minutes.
4. Minimum applicable confirmed preference penalty (or explicitly configured
   synthetic penalties in synthetic mode).
5. Minimum maximum normalized absolute teaching-target deviation.
6. Minimum aggregate normalized absolute teaching-target deviation.

Without preference optimization there is no invented preference tier. Existing
explicit combination objective modes and alternative preference-priority
experiments remain the configured prefix; fairness never silently reorders
them. These alternatives must be understood as research configurations, not
campus-confirmed priorities.

Fairness-enabled runs use sequential minimization, not a weighted sum across
tiers. All stages share `SolverConfig.max_time_seconds` as a **search** budget.
An earlier objective is fixed by equality only after native CP-SAT returns
`OPTIMAL`. A `FEASIBLE` stage stops lower-priority optimization and returns its
complete validated incumbent. A later `UNKNOWN` or exhausted budget retains
the last complete incumbent with overall `FEASIBLE` status and an explicit
stage-history entry. Full lexicographic `OPTIMAL` requires every stage to be
proven optimal. A later native infeasibility/model error contradicting a known
fixed incumbent raises an error instead of being described as a timeout.

Without any incumbent, unsuccessful runs emit empty teaching/administrative
schedules and no workload, preference, fairness or objective values. Their mode
flags and failure status remain available. Native status history records the
stages attempted for successful/retained results. Validation cannot independently
re-prove global optimality; it checks observed status consistency and recomputes
every reported objective value from the final timetable.

The legacy teaching objective and preference composite retain their evaluated
values and are labelled `EVALUATED_COMPONENT` in a fairness-enabled result.
The fairness report carries the complete lexicographic objective vector and
per-stage statuses. Its descriptive assessment separately reports post-selection
opportunities: selected flexible duties can reduce those opportunities while
the optimization cohort remains frozen. Both scopes are labelled in the report.
Disabled mode retains the original output keys and component status behavior.

### Independent validation and reporting

`validate_fairness_result` requires the original policy, calendar, optimization
configuration and every enabled resource/preference/work-period context. Public
staff, combination, work-period and preference result validators route enabled
results through this validator; omitting fairness context fails clearly.
Post-solve workload, preference and fairness assessments also retain this check.

A private validation projection lets the unchanged earlier arithmetic validators
check legacy components without claiming that their composite was the final
optimized objective. The public result keeps the evaluated-component labels.
After all earlier coverage/resource/report checks, fairness validation regenerates
targets, cohorts, exclusions, input opportunities and exact scale/bounds from
source contracts. It recomputes actual physical teaching minutes, utilization,
target deviations, weighted aggregate, objective vector and earlier-stage values.
Modeled workload/deviation values are compared with these independent totals.
Corrupted assignments, reports, source provenance, membership, values, scales,
statuses and illegal stage progress are rejected. A test deliberately breaks
native absolute-deviation constraints and confirms independent rejection.

Optional reports contain the applied policy/provenance and configuration,
academic week, included cohorts, exclusion reasons, missing-evidence diagnostics,
per-staff targets/workload/utilization/shortfall/excess, absolute target deviation,
the two normalized objective values, integer scales/weights/bounds, exact
objective vector and stage history. Administrative and attendance evidence stay
separate. Fractions serialize exactly as numerator/denominator pairs.
`university_ready` remains false.

### Native synthetic demonstration and remaining requirements

Run from the repository root:

```powershell
.\.venv\Scripts\python.exe -B services/teaching-service/examples/fairness-optimization-demo.py
.\.venv\Scripts\python.exe -B -m pytest services/teaching-service/tests -q
.\.venv\Scripts\python.exe -B -m pip check
```

The example uses entirely synthetic staff, offerings, groups, evidence, dates
and availability windows. Six 30-minute bookings are eligible only for a
Professor; fourteen additional bookings allow that Professor or a Dr. The
same confirmed synthetic preference contracts are used for both solves.
Actual native outputs were:

| Metric | Fairness disabled | Fairness enabled |
| --- | --- | --- |
| Professor: teaching / 360-minute target | 600 minutes; `5/3` utilization | 270 minutes; `3/4` utilization |
| Dr.: teaching / 480-minute target | 0 minutes; `0` utilization | 330 minutes; `11/16` utilization |
| Maximum normalized target deviation | `1` | `5/16` |
| Aggregate normalized target deviation | `5/3` | `9/16` |
| Physical deliveries / teaching minutes | 20 / 600 | 20 / 600 |
| Confirmed synthetic preference penalty | 0 | 0 |
| Overall native status | OPTIMAL for original objective | OPTIMAL for all five lexicographic stages |

The enabled objective vector is `(20, 600, 0, 450, 810)` with `L=1,440` and
`Q=1`. Independent validation confirms every value and the source eligibility.
The demonstration reports improvement only by comparing its actual solver
outputs. Neither solve fabricates administrative work or extra teaching demand.

The model adds linear work/deviation bookkeeping in comparison-staff count;
combination-alternative/resource-choice growth remains governed by existing
guards. Staged solves can repeat search and take longer than the previous
single-objective solve. The shared search budget does not include Python input
preparation, model construction or independent validation. Existing pairwise
clash validation and opportunity assessment can also dominate larger instances;
real-scale benchmarks are still required. Exact LCM scaling intentionally rejects
unmanageable custom targets/weights rather than degrading precision.

Real scheduling still needs authoritative offering IDs, stream assignments,
student groups and disjoint-population evidence, session frequencies/durations,
complete staff eligibility, explicit staff and venue calendars/blocked periods,
shared-room relationships, verified dated workload/appointment policy snapshots,
approved administrative/research duties, scoped confirmed preferences/weights,
and an approved fairness comparison/priority policy. Administrative completion
and campus attendance remain separate evidence requirements. No accepted Excel
values, Academic/Identity Service code, database, migration, API or travel
optimization is changed by this step.

Step 10C.2 verification: **1,488 tests passed**, preserving all 1,394 previous
tests and adding 94 synthetic optimization tests. `pip check` found no broken
requirements. The existing Starlette/httpx deprecation warning remains. Native
before/after optimization, exhaustive small-allocation comparisons, exact scale
boundaries, optional rational weights, priority conflicts, combined/mixed/
separate bookings, all hard resource/protection/cap checks, frozen cohorts,
zero assigned staff, incomplete evidence rejection, corrupted model/reports and
staged FEASIBLE/timeout/failure behavior passed. Fresh fairness/solver/preference/
FastAPI imports passed. No dependencies or original tests were changed.

## Step 11A: consecutive teaching and load distribution foundation

`schedence_teaching.consecutive` provides `assess_consecutive_sessions`,
`detect_consecutive_sequences` and `validate_consecutive_report`. It reuses
existing scheduled session/delivery records, explicit time intervals, academic
week calendars, hierarchy and independent result validation. This layer does
not construct a CP model, change solver inputs/results, add constraints or alter
any objective. Existing lecturer allocation, combinations, venue checks,
workload policies, availability, protections, preferences and fairness remain
unchanged. No travel calculation or location preference is introduced.

### Metrics and attendance

Assessment requires a complete feasible result and every original enabled
resource/work-period/preference/fairness context. Original coverage, eligibility,
clashes, calendars/caps, protected duties and attached reports are validated
before sequence scoring. Overlap or duplicate requirements are errors, not
consecutive-session penalties. Report regeneration checks policy, calendar,
attendee membership, metrics, penalties and metadata against fresh calculations.

Each lecturer/teaching staff member and each student group is evaluated
independently. Metrics include:

- Actual physical delivery count and teaching minutes; teaching days and daily
  minute distribution, including zero on supplied calendar days.
- Chronological adjacent session gaps, their frequency distribution,
  back-to-back teaching pairs and back-to-back lecture-only pairs.
- Maximal uninterrupted runs with preserved delivery IDs, start/end, duration
  and session count; maximum consecutive sessions and continuous minutes per
  actual date/stream and per entity.
- Preferred-break shortfall, excess continuous minutes, optional exact integer
  penalties, missing policy diagnostics and separately assessed confirmed hard
  minimum-break violations.

Touching endpoints (`previous.end == next.start`) form a back-to-back pair.
Three touching sessions have two pairs and one three-session run. Any positive
gap ends a strictly uninterrupted run; a short positive gap can separately
incur an explicitly configured preferred-break penalty. A single long required
session can exceed a preferred continuous limit without being split or resized.

Sequences compare only the same actual date and stream. A stream transition
ends a sequence and is reported as unscored. Intervening teaching in another
stream is not skipped to fabricate a break between two same-stream sessions.
Global/daily teaching totals still include every stream. Gaps are elapsed
clock time without that entity's intervening teaching, not proof of rest,
administrative completion or campus attendance. Existing hard staff clashes
continue to apply across all streams.

One combined booking counts once for its lecturer and once for each attending
group, regardless of the number of covered original requirements. Each subgroup
also attends its ancestor-group bookings. Parent rows do not union all child
labs; siblings and unknown cross-group membership are not merged. Direct and
inherited delivery IDs are reported separately and deduplicated per attendee.
Group rows are overlapping hierarchy perspectives, not a partition of unique
students; their totals are never added to lecturer teaching workload.

### Explicit policies and scoring

`ConsecutivePolicy` requires an applicable academic week, policy reference and
source provenance. No break duration, maximum continuous duration or weight is
defaulted. The default policy is descriptive only. Explicit `soft_criteria`
select `BACK_TO_BACK`, `SHORT_BREAK` and/or `EXCESS_CONTINUOUS_MINUTES`:

```text
BACK_TO_BACK = pair_count * back_to_back_penalty
SHORT_BREAK = sum(max(preferred_minimum_break - gap, 0)) * short_break_minute_penalty
EXCESS_CONTINUOUS_MINUTES = sum(max(run_minutes - preferred_maximum, 0)) * excess_continuous_minute_penalty
```

Excess is charged once per maximal run; nested prefixes are not charged again.
Only chronological adjacent, comparable pairs contribute gaps. Selecting both
pair and short-break penalties explicitly charges both components for a touching
pair. A supplied weight requires its criterion to be selected. Weights are
strict nonnegative integers; explicit zero disables that component's cost.

`score_lecturers` and `score_student_groups` independently select penalty
contributions; metrics remain visible for both. Development defaults select
lecturer scoring only, with no soft criteria or weights. If both scopes score,
the combined score deliberately sums lecturer and per-group costs, not unique
student workload. Entity details are optional in serialized reports.

An enabled criterion with missing thresholds/weights yields diagnostics,
`scoring_complete=false`, a known partial subtotal and `total_soft_penalty=null`.
Disabled/descriptive scoring uses a null total/completeness rather than claiming
a complete zero score. Missing values are never replaced with campus defaults.

Optional `ConfirmedMinimumBreak` is separate from soft preferences. It requires
a positive duration, explicit `CONFIRMED` value, campus-policy reference,
approval reference and specified lecturer/student-group scope. These are
caller-supplied confirmation records, not independent verification. Unconfirmed
or incomplete hard rules are rejected. Step 11A reports compliance within the
same date/stream comparison scope but **does not enforce the rule in CP-SAT**.
A result can remain valid for existing solver constraints while this separate
policy assessment reports a hard-break violation. Reports explicitly set
`confirmed_hard_break_enforced_by_solver=false` and
`cp_sat_consecutive_objective_enabled=false`. No confirmed campus break duration
has been supplied for real scheduling.

### Synthetic demonstration and Step 11B

Run from the repository root:

```powershell
.\.venv\Scripts\python.exe -B services/teaching-service/examples/consecutive-teaching-demo.py
.\.venv\Scripts\python.exe -B -m pytest services/teaching-service/tests -q
.\.venv\Scripts\python.exe -B -m pip check
```

The entirely synthetic demonstration has three 30-minute lectures at 09:00,
09:30 and 10:00. It detects two back-to-back pairs, a three-session/90-minute
run, and two zero-minute gaps. Explicit example-only preferences are a
15-minute preferred break, 60-minute preferred continuous limit and weights
5 per touching pair, 2 per shortfall minute, 3 per excess continuous minute.
The lecturer penalty is `10 + 60 + 90 = 160`. The group independently attends
the same three deliveries; group scoring is disabled in this example. These
durations and weights are not university policy. Nothing is rescheduled.

For Step 11B, reuse physical-delivery presence and assigned-staff choices for
optional chronological successor relationships within actual date/stream.
Only true neighbors should generate pair/gap penalties. Continuous-run
bookkeeping must match this independent evaluator, count combined bookings once
per attendee, and propagate ancestor-group attendance without merging siblings.
Preserve all hard constraints and existing lexicographic tiers; append a
consecutive-score tier only under an explicit approved/research priority
configuration. Use proven integer bounds or the existing staged optimizer,
retain honest FEASIBLE/timeout handling, guard model size, and independently
recompute selected-delivery penalties. Do not split long required sessions or
enforce a hard break without the complete confirmed policy and explicit opt-in.
There is no travel objective in the revised research scope.

Step 11A verification: **1,574 tests passed**, preserving all 1,488 existing
tests and adding 86 synthetic tests. `pip check` found no broken requirements;
the existing Starlette/httpx deprecation warning remains. The standalone native
synthetic demonstration passed. Tests cover runs/gaps/dates/streams, different
lecturers and durations, direct/nested/combined/mixed attendance, optional
scoring and missing policy data, confirmed hard-break assessment, exact
penalties, corrupt inputs/reports and original availability/protection/
preference/fairness context validation. A test prevents constructing a CP model
during assessment. Existing solver code, contracts, objectives, constraints,
original tests and source datasets are unchanged. No dependency, database,
API, migration, other-service, branch, commit or push action was performed.

## Step 11B: optional consecutive-teaching optimization

Pass `consecutive_policy`, `consecutive_calendar` and `consecutive_config` to
`solve_timetable`. Without these arguments, the existing solver behavior and
serialized output are preserved. The policy is the Step 11A `ConsecutivePolicy`,
and the calendar is the existing `PreferenceCalendar`; there is no second
delivery, date, workload or break-scoring contract. `ConsecutiveOptimizationConfig`
requires an explicit priority and priority-policy reference. Non-synthetic
calendars additionally require a supplied policy approval reference. References
record supplied provenance; they do not independently establish campus approval.

Enabled soft criteria must have their thresholds and weights supplied; the
optimizer rejects incomplete configuration rather than optimizing a partial
score. The default policy still contains no campus break durations. Optional
`effective_from`/`effective_until` narrow the academic-week applicability, with
ordered dates required. Metrics remain visible outside those dates, but costs
and confirmed hard-break enforcement apply only within them.

### Native model and independent source assessment

Each scored or hard-rule-covered lecturer/group has an optional chronological
circuit over possible physical deliveries. Selected staff-choice or delivery-
presence literals activate its nodes; inactive alternatives self-loop. A dummy
node closes the path. Real successor arcs require increasing actual calendar
time, so the circuit visits every selected event exactly once in chronological
order and cannot skip intervening teaching. Mutually exclusive alternatives
covering the same original occurrence have no connecting arc.

Comparable neighbors share an actual date and stream. A transition to another
date/stream resets the continuous run and has no pair/gap cost. An intervening
other-stream session cannot be skipped to create an apparent break. Reified
gap/touch variables identify true back-to-back sessions; run-prefix minutes
propagate only across touching comparable neighbors. Terminal-run literals
charge excess once per maximal uninterrupted run. Maximum run minutes are
reported independently of the cost. All costs reproduce Step 11A:

```text
entity cost = back-to-back pairs * configured pair weight
            + sum(max(preferred break - adjacent gap, 0)) * shortfall weight
            + sum(max(maximal run minutes - preferred maximum, 0)) * excess weight
total = lecturer_scope_weight * sum(lecturer costs)
      + student_group_scope_weight * sum(student-group costs)
```

Scope weights are explicit configurable nonnegative integers (default one).
Lecturer and group scoring remain independently selectable. An optional
`idle_gap_minute_penalty` adds comparable idle-gap minutes times that weight
only when its `idle_gap_policy_reference` is also supplied. It is an explicit
research compactness choice, not a default university policy. No teaching-day
minimization, travel or other new objective is implicitly introduced.

Combined deliveries count once for their lecturer and once for each attending
group. Attendance includes ancestor teaching for descendants; parents do not
inherit child labs and siblings do not inherit each other's teaching. A
physical delivery reached through multiple ancestor paths is deduplicated.
Original requirement coverage, lecturer/venue bookings and teaching workload
retain the existing combined/separate/mixed semantics. Administrative/research
reservations remain hard protections where already approved, rather than
being counted as teaching or evidence of completed administrative work.

`enforce_confirmed_hard_break` defaults to false. Enforcing it requires the
existing complete `ConfirmedMinimumBreak`, its explicit lecturer/group scope,
and applicable policy dates. A soft shortfall alone never makes the model
infeasible. An impossible explicitly enabled hard rule returns `INFEASIBLE`
without partial bookings or reports.

The independent validator calls the original availability, resources, exact
coverage, workload, preference and fairness validators, then regenerates Step
11A attendance, successors, gaps, runs and costs directly from selected
bookings. It compares every modeled audit value and the complete report,
including policy/config snapshots, scope weights, spans, objective components
and stage history. It builds no CP model and rejects corrupted identities,
coverage, metrics, penalties or optimality labels. High-level assessment APIs
require and preserve the original consecutive context when validating an
optimized result.

### Priorities, status and visible trade-offs

The only supported new priority is `EXISTING_OBJECTIVES_THEN_CONSECUTIVE`.
Existing explicitly configured delivery/contact/preference orders are retained;
when fairness is enabled its maximum and aggregate target-deviation tiers also
remain ahead of consecutive cost. The full synthetic development order is:

```text
hard feasibility; DELIVERIES; CONTACT_MINUTES; PREFERENCE_PENALTY;
MAXIMUM_TARGET_DEVIATION; AGGREGATE_TARGET_DEVIATION; CONSECUTIVE_PENALTY
```

This ordering is provisional research configuration. The shared staged solver
fixes only proven optimal values and uses one total search-time budget. It
stops after a `FEASIBLE` stage. A later timeout retains the earlier validated
complete incumbent with overall `FEASIBLE`; only all proven stages yield
overall `OPTIMAL`. Earlier reports are labeled `EVALUATED_COMPONENT` while
their stage histories retain the actual prefix proofs.

The optional report contains Step 11A diagnostics, per-entity modeled audit
data, weighted cost, bounds/vector/stages, and teaching-day/span/idle
distributions. Teaching gaps are elapsed clock time, not verified rest or
completed recognized work. Reducing touching sessions may increase daily span
or teaching days; these trade-offs are reported rather than hidden. Synthetic
cost is labeled configured cost, with confirmed cost left null.
`university_ready` remains false.

### Guards, demonstration and remaining data

The model uses bounded quadratic successor arcs per participant, avoiding an
all-triples adjacency encoding. Explicit technical guards default to 500
participants, 60 candidate nodes per participant, 20,000 estimated circuit
arcs, 200,000 model variables/constraints, and integer bounds at most `2**50`.
Oversized inputs fail clearly before or during construction. These are
implementation limits, not campus rules; increasing them requires benchmarking
and checked integer bounds. Full real-dataset scalability is not established.

Run from the repository root:

```powershell
.\.venv\Scripts\python.exe -B services/teaching-service/examples/consecutive-optimization-demo.py
.\.venv\Scripts\python.exe -B -m pytest services/teaching-service/tests -q
.\.venv\Scripts\python.exe -B -m pip check
```

The runnable example uses one synthetic Professor, one group and three
30-minute lectures in a supplied 120-minute Monday window, with preference
and fairness optimization enabled in both runs. Explicit example-only choices
are a preferred 15-minute break, preferred 60-minute continuous limit, weights
5/2/3, and idle-minute weight one. Native baseline bookings are 0–30, 30–60,
60–90; optimized bookings are 0–30, 45–75, 90–120. For both the lecturer and
group, touching pairs drop from two to zero and maximum continuous minutes
from 90 to 30. Total configured cost drops from 320 to 60; the remaining 60
is the deliberately configured idle cost for both scopes. Span rises from 90
to 120 minutes, gaps become 15/15, and teaching days remain one. Both results
are `OPTIMAL` with unchanged three deliveries, 90 contact minutes, zero
preference penalty, and maximum/aggregate normalized target deviation `3/4`.
Actual timing and equivalent occurrence order may differ. The example asserts
improvement from native outputs, never from a rewritten baseline.

Real integration still needs authoritative offering IDs, streams, groups and
membership/disjointness evidence, weekly session demand, explicit staff and
venue calendars/protections, and approved dated/scoped break weights and
optimization priorities. Existing venue inventory and teaching assignments
are preserved. There is no building-distance, floor-travel or location
preference optimization.

Step 11B verification: **1,659 tests passed in 48.67 seconds**, preserving all
1,574 previous tests and adding 85 synthetic tests. The existing
Starlette/httpx deprecation warning remains. `pip check` reported no broken
requirements. The standalone native example and independent validation passed;
in the measured example, baseline and optimized solver runtimes were about
0.12 and 0.29 seconds respectively (not scalability guarantees). Tests include
exhaustive comparison against a tiny synthetic assignment/start-time space,
corrupt native audit rejection, approved hard/soft distinctions, combined and
hierarchical coverage, preference/fairness priority preservation, disabled
mode, honest timeout/incumbent handling and original API/import behavior.
No existing test file, source dataset, venue inventory, dependency declaration
or unrelated service was changed; all original files remain present.

## Step 11C: end-to-end integration and readiness audit

The [integration audit and real-data readiness matrix](docs/step-11c-audit.md)
documents the complete synthetic week, independent validation, negative tests,
bounded small/medium benchmarks, genuine integration limitations and remaining
authoritative campus inputs. All existing objectives are enabled together;
this step adds no scheduling objective or campus policy. The only production
correction is outdated solver limitation text about fairness and travel scope.

```powershell
.\.venv\Scripts\python.exe -B services/teaching-service/examples/end-to-end-scheduling-demo.py
.\.venv\Scripts\python.exe -B services/teaching-service/examples/scheduling-benchmark.py
.\.venv\Scripts\python.exe -B -m pytest services/teaching-service/tests -q
.\.venv\Scripts\python.exe -B -m pip check
```

The small example independently covers seven original occurrences through six
physical bookings, including mixed shared/separate lectures, child-group labs,
weekday/weekend streams, hard resource/availability protections, separate
administrative allocation, preferences, role-normalized fairness and consecutive
scoring. OPTIMAL describes the configured objective stages, not complete
workload fulfillment or campus readiness. The earlier general lunch-window
proposal is superseded by the fixed year-specific periods in Step 11D.1 below;
hard enforcement remains for Step 11D.2. Real inputs still
lack authoritative offerings/streams/groups/session demand and scheduling
calendars/policies. `university_ready=false` remains explicit.

Step 11C verification: **1,707 tests passed** (48 new integration cases),
`pip check` passed, and the standalone end-to-end example and bounded
small/medium benchmark ran successfully. The existing Starlette/httpx
deprecation warning remains. Source workbooks, saved local import reports,
original tests, solver constraints/objectives and other services are unchanged.

## Step 11D.1: fixed student lunch policy and independent assessment

The confirmed fixed periods supersede the previous general lunch-window
proposal: **study years 1/2: 12:30–13:30; study years 3/4: 13:30–14:30**.
Each is one continuous hour, selected from validated
`StudentGroup.academic_year`, never from the calendar year. No lecturer lunch
rule or canteen/crowd policy is inferred.

`schedence_teaching.lunch` supplies configurable `StudentLunchPeriod` and
`StudentLunchPolicy` contracts, `confirmed_student_lunch_policy`,
`assess_student_lunch` and `validate_student_lunch_report`. Applicable dates
and weekday/weekend streams remain unknown by default and require an explicit
referenced configuration. Assessment preserves combined delivery coverage and
ancestor/subgroup attendance, validates every original enabled scheduling
context, reports half-open interval overlaps and rejects corrupted reports.
Unknown applicability never becomes a compliance claim.

See the [Step 11D.1 contracts, examples and enforcement boundary](docs/step-11d1-student-lunch.md).
The end-to-end demo now separately reports the existing Year 2 12:00–13:00 lab
overlap (30 minutes) under explicit synthetic applicability. Original bookings
and all existing objective stages remain unchanged. Earlier example timelines
do not establish lunch compliance. **CP-SAT hard lunch enforcement is reserved
for Step 11D.2**; the independent report explicitly marks it false and retains
`university_ready=false`.

Step 11D.1 verification: **1,804 tests passed** (all 1,707 previous tests plus
97 new lunch tests), `pip check` passed, and the updated end-to-end CLI reported
the existing lunch conflict independently. The existing Starlette/httpx
deprecation warning remains. Solver code, original test files, source datasets
and local import reports are unchanged.

## Step 11D.2: optional hard fixed student lunch enforcement

Pass `student_lunch_policy`, `student_lunch_calendar` and
`StudentLunchEnforcementConfig` as `student_lunch_config` to `solve_timetable`.
The config requires explicit confirmation and a reference for the policy's
date/stream applicability; unknown scope is rejected. No weekend applicability
is assumed. Current fixed periods are unchanged: study years 1/2 12:30–13:30,
years 3/4 13:30–14:30. The existing Step 11D.1 policy remains the only source
of lunch rules; default callers stay disabled with unchanged serialized output.

Native start-domain exclusions prevent positive teaching overlap, including
all directly attending groups and descendants of ancestor teaching. Combined
and separate alternatives preserve exact original coverage; inactive
alternatives are not booked. No lunch variables/intervals, staff blocks,
resource reservations or objective tier are added. Touching endpoints are
legal, and grid alignment never changes the fixed lunch times.

Independent result validation reuses Step 11D.1 assessment after all original
resource/protection/preference/fairness/consecutive checks. Enabled output
includes its policy, confirmation, years/groups, dates/streams, fixed periods
and regenerated compliance. Unknown/infeasible failures contain no partial
timetable or lunch report. `university_ready=false` remains explicit.

The [Step 11D.2 report](docs/step-11d2-student-lunch-enforcement.md) contains the
complete before/after timetable, native constraints, integration checks,
performance observations and remaining authoritative campus inputs. Run:

```powershell
.\.venv\Scripts\python.exe -B services/teaching-service/examples/student-lunch-enforcement-demo.py
.\.venv\Scripts\python.exe -B -m pytest services/teaching-service/tests -q
.\.venv\Scripts\python.exe -B -m pip check
```

The unchanged historical fixture's Year 2 lab moves natively from Tuesday
12:00–13:00 to 10:00–11:00. All seven originals and six bookings remain, with
unchanged objective vector and OPTIMAL proof for every tier. In one bounded
comparison there are zero extra variables and nine extra constraints. This
does not establish real-campus scalability or confirm real date/stream scope.

Verification: **1,906 tests passed** (all 1,804 previous tests plus 102 new
tests); `pip check` passed and the standalone lunch-enabled demo independently
validated. The existing Starlette/httpx deprecation warning remains. Source
workbooks, saved local reports, original test files and the historical fixture
are unchanged. No other service, database, API or Git branch was changed.

## Step 12A: authoritative scheduling input foundation

`schedence_teaching.integration` composes the existing academic, combination,
staff, venue, workload, preference, fairness, consecutive and fixed-lunch
contracts into one institution/week-scoped input profile. Explicit export and
ADS adapters preserve stable IDs and source provenance. Readiness reports
CONFIRMED/MISSING/AMBIGUOUS/UNVERIFIED/INVALID fields and blocks incomplete campus
requests before the unchanged solver runs. Complete runs pass the full
independent validation gateway. Synthetic inputs require explicit synthetic
mode; they cannot become an actual-campus fallback.

The [input requirements and mappings](docs/step-12a-authoritative-inputs.md),
[fresh read-only dataset audit](docs/step-12a-dataset-audit.md) and
[proposed ownership/database plan](docs/step-12a-ownership-plan.md) document the
remaining authoritative exports. Existing workbook importers remain the readers;
no source values, assignments, solver constraints/objectives or other services
are changed. No database connection, persistence or new API is introduced.

```powershell
.\.venv\Scripts\python.exe -B services/teaching-service/examples/authoritative-data-audit.py
.\.venv\Scripts\python.exe -B services/teaching-service/examples/authoritative-input-demo.py
.\.venv\Scripts\python.exe -B -m pytest services/teaching-service/tests -q
.\.venv\Scripts\python.exe -B -m pip check
```

Step 12A verification: **1,987 tests passed** (1,906 existing plus 81 new),
`pip check` passed, and the standalone synthetic bridge returned an independently
validated OPTIMAL timetable with seven original occurrences, six physical
deliveries and 315 teaching minutes. The existing Starlette/httpx deprecation
warning remains. Hash verification found no missing original files or changes
to existing solver code, tests, workbooks or saved local reports.

## Step 12A.1: university collection pack and pilot readiness

The [collection pack](data-collection/README.md) includes twelve blank CSV
worksheets, a separate field-level dictionary, gap report, university checklist
and one-week pilot workflow. Known venue attributes and confirmed teaching/admin
targets/lunch times are reused. Missing offering IDs, populations, demand,
calendars, weights and approvals are never filled with synthetic values.

`python -m schedence_teaching.collection create <new-directory>` creates a pack.
Adding `--local-data services/teaching-service/local-data` creates a private
known-value prefill inside that ignored folder. The existing source importers
remain the readers. Original module source rows distinguish both IT2100 records
and bind unchanged staff assignments to supplied offering IDs.

`python -m schedence_teaching.collection validate <directory>
--base-submission <reviewed-json> --institution-id <existing-uuid>` maps completed
CSVs into a copy of reviewed Step 12A context and reuses its readiness gate.
No solver runs during validation. Missing/invalid/unverified inputs withhold the
submission and return safe row diagnostics. Actual-campus mode is the default;
synthetic fixtures require explicit `--synthetic` and matching base flags.

Use the existing virtual environment from the repository root:

```powershell
.\.venv\Scripts\python.exe -B services/teaching-service/examples/pilot-readiness-demo.py
.\.venv\Scripts\python.exe -B -m pytest services/teaching-service/tests -q
.\.venv\Scripts\python.exe -B -m pip check
```

Step 12A.1 verification: **2,040 tests passed** (1,987 previous plus 53 new),
`pip check` passed, and the synthetic completed-CSV pilot passed the existing
readiness/preparation checks without invoking CP-SAT. The sole existing
Starlette/httpx deprecation warning remains. All pre-existing code, tests,
workbooks, local reports and confirmation metadata are unchanged.

## Step 12A.2: editable student counts and a labelled timetable preview

`schedence_teaching.pilot` reads literal cohort counts from the inspected
`STUDENT_GROUPS` sheet and reuses existing StudentGroup/hierarchy contracts.
Configurable balanced grouping preserves exact totals and supplied caps without
inventing specialization or population proof. The supplied 16 scopes contain
6,720 students: 56 main groups and 112 labs. The first Year 1 semester 1 weekday
pilot contains 720 students, six main groups and twelve labs.

The [Step 12A.2 report](docs/step-12a2-student-group-pilot.md) documents mappings,
source status, assumptions, readiness and commands. Explicit DEMO mode supplies
fictional session/policy/calendar/population assumptions while retaining valid
source staff/module/venue references. Independently validated physical deliveries
are exported to standalone HTML, CSV, solver JSON and an assumption report in
ignored `local-data/pilot-previews/`, clearly labelled SYNTHETIC_DEMO.

REAL_CAMPUS is the default and stays behind the unchanged Step 12A readiness
gate; missing data produces a blocked report, never a synthetic fallback. Only
validated actual inputs/results may use CAMPUS_INPUT_VALIDATED. No source
workbooks, solver algorithms, APIs or database objects are changed.

```powershell
.\.venv\Scripts\python.exe -B services/teaching-service/examples/student-group-pilot.py --mode DEMO --output services/teaching-service/local-data/pilot-previews/new-demo
```

Step 12A.2 verification: **2,075 tests passed** (2,040 previous plus 35 new),
`pip check` passed, and the runnable 720-student demo produced an independently
validated FEASIBLE timetable: 18 exact-once original occurrences, 16 physical
deliveries, two combined lectures and zero student-lunch violations. The preview
was checked in a headless browser. REAL_CAMPUS without reviewed inputs returned
BLOCKED and exported only a readiness report. The existing Starlette/httpx
deprecation warning remains.

## Step 12A.3: confirmed academic inputs and continuous lectures

The supplied academic records are user-confirmed university-sourced inputs.
Checksum-bound source confirmation is recorded separately from original workbook
labels. Existing records are reused without requesting university verification
again; missing dated scheduling inputs remain explicit.

The [Step 12A.3 implementation report](docs/step-12a3-academic-demand.md) describes
the actual module catalog and specialization-specific demand builder. All 184
module rows and 1,288 team entries are retained, including both IT2100 records.
Two weekly lecture hours create one continuous 120-minute weekly lecture; one
hour creates one 60-minute lecture. Other session types retain separate weekly
hours and require explicit duration/frequency patterns.

`pilot.academic` builds one TeachingTimetableInput from supplied offerings,
groups, specialization mappings and session audiences. It never divides the
all-specialization cohort totals into invented specialization populations.
Shared module rows retain their applicability lists and may use one supplied
offering ID across applicable groups. Existing combination rules remain intact.
Validated previews provide specialization, lecturer, group and venue views of
the same global timetable, without separate solves or duplicate bookings.

```powershell
.\.venv\Scripts\python.exe -B -m schedence_teaching.pilot.academic_cli --output services/teaching-service/local-data/pilot-previews/new-academic-report
```

The actual extraction contains 171 two-hour lecture rows, nine one-hour rows,
four zero-hour rows and 596 specialization/session demand drafts. The user
clarified that no separate specialization-start policy is required: use module
year/semester/specialization applicability directly. Source workbooks and native
CP-SAT constraints/objectives are unchanged.

Step 12A.3 verification: **2,120 tests passed** (2,075 existing plus 45 new),
`pip check` passed, and all source workbook hashes are unchanged. An existing
time-limited lunch fixture needed an isolated check and full-suite rerun; its
original tests and solver remained unchanged. The sole existing Starlette/httpx
deprecation warning remains.

### PP1 proportional allocations and coordinated full-scope run

The new `STUDENT_ALLOCATIONS` workbook is imported read-only and reconciles
128 approved proportional allocations against all 16 original cohorts: 6,720
students, 140 main groups and 198 lab subgroups. Estimates remain labelled as
PP1 testing allocations. Exact group-to-specialization mappings are generated
automatically; original module fields and teaching teams remain unchanged.

The editable [PP1 calendar](examples/pp1-test-calendar.json) contains the user's
2026-10-19 through 2026-10-25 reference week and 08:30-17:30 teaching windows.
Both semesters, Years 1-4 and both streams are included concurrently. Native
staff/venue/group constraints and year-specific lunch are reused. Shared lectures,
holiday optimization and cross-specialization overlap optimization are excluded.
Existing source day/weekend preferences are soft, using explicit test weights.
Staff/approved protected-period overrides can be supplied; no actual staff
calendar or administrative blocks are invented from weekly allocations.

```powershell
.\.venv\Scripts\python.exe -B -m schedence_teaching.pilot.pp1_cli --config services/teaching-service/examples/pp1-test-calendar.json --output services/teaching-service/local-data/pilot-previews/new-pp1-run
```

The full run produced 368 local source-row/stream offering references and 1,461
occurrences (616 lectures, 258 tutorials, 95 workshops, 492 labs). The global model
returned UNKNOWN, with no complete schedule. Independently, 41 leaf groups in 27
weekend scopes exceed the 16 available teaching hours; one needs 28 hours. A
native source-subset diagnostic returned INFEASIBLE, proving the complete request
cannot fit the configured calendar. Inputs and academic limits were preserved.

Ignored `local-data/pilot-previews/step12a3-pp1-reviewed-results/` contains the
PP1_TEST_CALENDAR-labelled HTML, eight specialization pages, CSVs, view index,
native result, report and witness. There are **0 scheduled / 1,461 unscheduled**
occurrences, all retained. No clash-free timetable is claimed for an unsuccessful
run. See the [full report](docs/step-12a3-academic-demand.md) for the exact capacity
proof, source mappings and honest staff/policy limitations.

An explicit default-false testing option allows the native availability/cap
checks to retain five staff whose source titles lack underlying workload ranks;
no rank is invented. Actual-campus readiness rejects that testing option. All
other original staff-policy checks and targets remain unchanged.

PP1 verification: **2,156 tests passed** (all 2,120 previous tests plus 36 new),
`pip check` passed, and every source workbook remained byte-identical. The
existing Starlette/httpx deprecation warning remains. No source assignments,
database records, other services or Git branches were changed; nothing was
committed or pushed.

### Step 12A.3.1: demand audit and complete feasible scopes

The independent source audit reconciles all 1,461 occurrences without an
expansion defect. Year 4 Semester 1 IT Weekend has eight applicable source
modules: 16 lecture + 3 tutorial + 1 workshop + 8 lab hours per leaf. The
unchanged weekend calendar offers 16 hours, leaving a 12-hour shortage.
All 41 overloaded leaves in 27 scopes have genuine calendar time shortages.

`pilot.demand_cli` exports source-row/module/group demand, every leaf's attendance,
all scope capacities, overloads and minimum-extra-hours comparisons. It selects
complete cohorts and schedules them in progressively larger coordinated native
models. The four previews solved 20, 106, 233 and 356 occurrences respectively,
each OPTIMAL with zero unscheduled sessions inside its explicit scope and passed
independent validation. Other scopes remain intact and separately reported.
No calendar extension or source-demand reduction was applied.

```powershell
.\.venv\Scripts\python.exe -B -m schedence_teaching.pilot.demand_cli --output services/teaching-service/local-data/pilot-previews/new-demand-audit --seconds-per-scope 120
```

Add `--audit-only` for source reconciliation and calendar reports without solving.
Private exports include an interactive `weekly.html`, specialization views,
scheduled CSV/JSON, unscheduled/outside-scope CSVs and independent validation.
See [the Step 12A.3.1 report](docs/step-12a31-demand-audit.md) for exact module
rows, scope exclusions, output paths and the smallest justified calendar decision.

### Step 12A.3.3: registered elective attendance and hybrid previews

The clarified Year 4 rule supersedes the earlier all-applicable-modules attendance
assumption: each test student selects exactly two electives, while Research is
compulsory and pending its actual session mapping. Source module contact hours
remain unchanged. Editable settings in `examples/pp1-elective-test-settings.json`
configure reproducible test registrations and the approved Wednesday/Monday
online lecture opportunities.

IT Year 4 Semester 1 Weekday and Weekend each scheduled all 16 elective sessions.
One coordinated model scheduled all 282 sessions across 30 supported Year 4
cohorts, with independent student, lecturer, venue and lunch validation.
These results are `ELECTIVE_COMPONENT_COMPLETE`, not complete academic
timetables. The two Semester 1 DS cohorts remain blocked with counts preserved.

The larger 1,401-session PP1 run returned `UNKNOWN`; an independent physical lab
capacity bound proves the current full resource/calendar scope infeasible.
Private HTML/CSV/JSON, registrations, Research obligations, blocked counts and
capacity diagnostics are under
`local-data/pilot-previews/step12a33-resumed-reviewed/`. See the
[Step 12A.3.3 report](docs/step-12a33-elective-components.md) for reproduction,
validation, precise limits and the remaining Research/resource requirements.

### Step 12A.3.4: semester scenarios and coordinated lab bookings

Separate semester room-capacity bounds pass, while the simultaneous-semester
stress test retains its proven lab-booking deficits. A new independent student
packing bound also proves two Weekend cohorts cannot fit ten continuous
two-hour attendances into the nine slots left by the approved calendar/lunch
periods: Year 3 Semester 1 IT and Year 3 Semester 2 IM.

Explicit feasible-cohort subsets scheduled **681/681** Semester 1 and **693/693**
Semester 2 occurrences using native CP-SAT subproblems with all previous shared
resource bookings reserved. Each complete union independently passed student,
elective, lecturer, venue, duration, lunch and delivery-mode validation. Their
result is FEASIBLE, without claiming global objective optimality or full-semester
completion. The 16 and 11 excluded original occurrences are retained separately.

Research and the two Year 4 Semester 1 DS input blocks remain unresolved. No
calendar extension, source demand change or room-capacity change was applied.
See the [Step 12A.3.4 report](docs/step-12a34-semester-scenarios.md) for exact
scope boundaries, optional search settings, reproduction commands, private
exports and the proposed minimum calendar correction for review.

### Step 12A.3.5: experimental complete supported semesters

The isolated `PP1_EXPERIMENTAL_CALENDAR` extends Weekend online Monday to 18:30.
It adds one usable continuous two-hour slot after the fixed Year 3 lunch period;
physical windows and source demand stay unchanged. Both affected cohorts solve
OPTIMAL (16/16 IT and 11/11 IM occurrences). Coordinated full supported semesters
solve FEASIBLE at **697/697** and **704/704**, with exact coverage and independent
clash, capacity, lunch and delivery-mode validation.

These are separate semester alternatives and are not an approved university
timetable or a jointly conflict-free concurrent timetable. Research mapping and
Year 4 Semester 1 DS catalog mapping remain separate blockers. The baseline and
earlier 681-/693-session previews are preserved. See the
[Step 12A.3.5 report](docs/step-12a35-experimental-calendar.md) for source module
details, the mathematical comparison, file changes, provenance and reproduction.

### Step 12B.1: persistence foundation (not applied)

An opt-in `persistence` package defines Teaching-owned PostgreSQL history models,
versioned input/provenance envelopes, database-independent repository ports and a
shared db-core unit of work. Draft persistence reruns existing independent
validation and retains one actual booking with unique original occurrence
coverage. Academic, Identity and Resource masters remain externally owned.

The initial Alembic revision is prepared for review with tenant-scoped foreign
keys, forced row-level security and immutable history. No database connection,
migration application, data write, new API or solver dependency was introduced.
See the [architecture and migration review](docs/step-12b1-persistence-foundation.md)
for the ten tables, provenance categories, ownership/ID mappings, offline checks
and decisions required before applying migrations.

### Step 12B.2: database mapping and safety review (historical preflight)

Read-only inspection of the shared Neon database and the existing Excel adapters
produced an exact pre-import mapping review. Two validated private PP1 snapshot
documents preserve the 697-/704-occurrence semester scopes and separate source,
estimated allocation, synthetic registration and experimental-calendar provenance.

The earlier mapping review proposed Teaching-only snapshots. The user subsequently
approved additive Academic/Resource master integration and Teaching persistence,
conditional on production recovery evidence and restricted runtime access. The
subsequent approved production integration is recorded separately below. See the
[Step 12B.2 review](docs/step-12b2-preimport-review.md) and the detailed ignored
`local-data/persistence-preview/step12b2/mapping-and-safety-review.md` report.

### Step 12B.2: approved production integration

Owner-managed additive migrations preserve Academic master records, Resource
venue inventory and Teaching history separately. `persistence.master_import`
only inserts or reuses identical records; it refuses to overwrite conflicting
identities. `master_plan` maps validated existing imports explicitly, while
`source_review` checks the new Year 4 review against every original Year 4 row.
Academic classification and PP1 candidate selection are independent fields.

The solver remains database-independent. Database snapshots can be detached and
replayed through the existing preparation and independent validation gateway.
Historical experimental semester previews retain their original synthetic
elective policy, pending Research and blocked DS mappings. They are validated
draft alternatives, not published or concurrently validated semester timetables.

The deployment report and exact private verification artifacts are described in
[the production integration report](docs/step-12b2-production-integration.md).
Dedicated service credentials are kept only inside Git-ignored local data;
neither this README nor source code contains a connection URL or password.

### Step 12B.3: read-only database-backed generation

`persistence.database_read.load_generation_scope` loads a selected institution's
persisted snapshot and checks its Academic/Resource references in database-enforced
read-only transactions. `generation_inputs` reconstructs the existing validated PP1
contracts, including elective attendance, source contact hours, preferences and
calendar policy. The CP-SAT solver receives detached Python inputs and has no
database dependency. Missing or inconsistent mappings fail before scheduling.

The opt-in `python -m schedence_teaching.persistence.database_preview` command
requires explicit semester snapshot IDs, calendar and historical policy selection.
It compares cached historical input JSON, revalidates archived deliveries, runs a
small scope first, then generates separate coordinated semester previews in a new
Git-ignored local directory. It never reads Excel, writes database records or
replaces previous outputs. Read-only live tests have their own opt-in flag;
the earlier rollback-write integration tests remain disabled for this step.

The experimental-calendar previews scheduled **16/16**, **697/697** and **704/704**
known occurrences with zero independently detected hard violations. Research
session mapping and Year 4 Semester 1 DS remain unresolved, so these are teaching
component previews rather than complete academic timetables. See the
[database-backed generation report](docs/step-12b3-database-generation.md) for
ownership boundaries, historical IT4070 policy differences, commands and results.

### Step 12B.4: transactional draft saves and version retrieval

`persistence.timetable_service.persist_generation_result` revalidates a complete
solver result, saves its run/report/version/bookings/coverage in one transaction,
and recognizes identical retries, including legacy drafts. Existing series locks,
optimistic parent checks and unique version constraints protect concurrent saves.
`timetable_reader` opens a database-enforced read-only transaction and returns
detached series, latest/specific versions, booking views and coverage evidence.
Student views preserve hierarchy and supplied elective population overlap;
combined lectures retain one actual booking with multiple original occurrences.

Lifecycle contracts name DRAFT, VALIDATED, PENDING_APPROVAL, APPROVED, PUBLISHED
and SUPERSEDED. This step saves only logical DRAFT/VALIDATED records using the
existing immutable `DRAFT_VALIDATED` storage format. Approval/publication checks
are prepared; no transition writes, migrations or publication were introduced.
Research, blocked DS mappings and testing/experimental provenance remain visible.

Write-capable integration tests use an explicitly configured isolated localhost
PostgreSQL database. Production verification uses restricted read-only access;
the two historical drafts and all 1,401 deliveries remain unchanged. See the
[persistence and version-management report](docs/step-12b4-timetable-versioning.md)
for interfaces, idempotency semantics, lifecycle limits and verification results.

### Step 12C.1: Identity-authorized timetable read APIs

Five GET endpoints under `/api/v1/teaching/timetables` expose version summaries,
sessions, validation and history; `/api/teaching/timetables` is the requested
compatibility alias. They verify Cognito access tokens using shared `auth-core`,
then fetch the caller's current institution grants from Identity's existing
`/api/v1/identity/me/permissions` contract. No JWT role/institution claims or UI
role headers are trusted, and no roles or permissions are seeded by Teaching.

Existing Identity grants `teaching:generate`/`teaching:approve` authorize the
Academic Admin read workflow; explicit Teaching read permissions have compatible
extension contracts. Institution Admin, System Admin, Examination Admin and Venue Manager get no
automatic private Teaching access. Lecturer/student reads require trusted
Academic account/scope bindings; missing bindings fail closed. Students cannot
see the current unpublished experimental drafts. HOD/Professor/coordinator
titles never grant system access.

All API database operations use the restricted runtime role through the existing
read-only/RLS facade. Responses whitelist dashboard fields, preserve calendar,
Research/DS and historical policy limitations, and omit registration rosters and
raw solver/workload payloads. No management endpoints or production writes were
introduced. See the [RBAC and read API report](docs/step-12c1-rbac-read-api.md) for
role ownership gaps, deployment settings, examples and verification results.

### Step 12C.2: asynchronous timetable generation

`POST /api/v1/teaching/generations` accepts an institution-authorized generation
request and `GET /api/v1/teaching/generations/{job_id}` reports its progress;
both have `/api/teaching` aliases. They reuse Cognito and fresh Identity grants,
requiring explicit `teaching:generate` independently of read permission.

Execution is disabled by default. The opt-in development registry is explicitly
non-durable, uses disposable solver processes, bounds concurrency/timeouts, and
deduplicates idempotent submissions within one application lifetime. Input reads
reuse the existing restricted database builder. Independent exact-coverage
validation precedes any isolated-test draft persistence through Step 12B.4.
No production job/version writes, migrations or solver changes are introduced.
See the [asynchronous generation design](docs/step-12c2-asynchronous-generation.md)
for requests, deployment controls, persistence boundaries and the unapplied
durable-job schema proposal.

### Step 12C.2.1: durable queue and Swagger verification

The isolated PostgreSQL queue persists acceptance, idempotency, leases,
heartbeats, retry attempts and job results across worker/API restarts. Fencing
prevents stale workers from saving; complete validated drafts and job completion
commit through the existing persistence boundary in one transaction. Production
execution remains disabled, and the additive queue migration/grants are prepared
for review without being applied to Neon.

Teaching runs separately from Identity (8001), Academic (8002) and Examination (8003) on local
port **8004**, with Swagger at **http://localhost:8004/docs**. Both route prefixes are
visible. `python -m schedence_teaching.api.inspect_permissions` safely inspects
your own effective Identity Teaching grants using a hidden Cognito-token prompt.
No real grants are changed by Teaching. See the [durable jobs and Swagger guide](docs/step-12c2-1-durable-jobs-swagger.md)
for the migration plan, exact startup/settings, request examples, account-management
ownership, isolated demonstration and remaining production requirements.

### Generation input readiness (read-only)

`GET /api/v1/teaching/generation-inputs?semester=1[&academic_year=1][&stream=WEEKDAY]`
(also `/api/teaching`) prepares one institution's input snapshot with the existing
reviewed PP1 preparation, binds it to that institution's own staff, module and venue
identifiers, and runs the generation worker's pre-solve validation against the
institution's restricted, RLS-scoped Academic and Resource rows. It returns counts
(staff, modules, student groups, venues, teaching teams, lecturer preferences,
expected sessions) and Ready/Warning/Missing checks for the selected scope. It
requires `teaching:generate`, never stores the snapshot and never solves. The
scheduling configuration is labelled `EXPERIMENTAL_CONFIGURATION_NOT_UNIVERSITY_APPROVED`.

It is disabled by default. Development opt-in needs `TEACHING_GENERATION_READINESS=DEVELOPMENT`,
`TEACHING_DATABASE_ENV_FILE` (the ignored restricted-role credentials) and the explicit
`TEACHING_GENERATION_DATABASE`/`_PROJECT_ID`/`_BRANCH_ID` target; production refuses it.
API reads keep a small pre-pinged connection pool, so requests no longer open a new
Neon connection each time.

The [Scheduling Rules backend guide](docs/scheduling-rules.md) documents read-only
rule validation with `POST /generation-inputs` and the optional per-request
`scheduling_rules` integration with the existing solver.

## Academic teaching tracks and unified main-group PDF previews

Reviewed `AcademicPathwayConfiguration` rules can pool provisional PP1 allocation
populations only when their exact source-row curricula match. This separates
common/foundation teaching from official specialization enrollment policy.
Historical inputs remain reproducible; use the explicit `academic_pathways`
argument to `prepare_pp1` for a new reviewed preview. Unified PDFs show all main
and subgroup teaching on the same main-group timetable, including parallel
subgroup blocks. Install the optional `pdf` extra for offline PDF export.

See [academic pathways and unified PDFs](docs/academic-pathways-and-unified-pdfs.md)
for the approved Year 1 mapping, reconciliation, new contracts and limitations.

## Lecturer workload reports

Authenticated institution-wide Teaching readers can report on one validated saved
timetable version through `/api/teaching/reports/lecturer-workload` (also under
`/api/v1/teaching`). Read-only RLS-backed retrieval feeds pure actual-delivery
accounting and private PDF/CSV exports. Reports preserve zero-assignment staff,
explicit rank/appointment targets, separately configured caps, and separate
administrative/research reservations and verified completion evidence.

See [lecturer workload reporting](docs/lecturer-workload-reporting.md) for filters,
download routes, counting rules, authorization, provenance and limitations.

## Confirmed teaching assignment policy

New institution-policy-bound inputs seal a reviewed revision, effective dates,
approval provenance and effective scheduling inputs with a deterministic digest.
Solver, validation, workload reporting and new timetable-version metadata verify
that digest. See [institution policy boundary](docs/institution-policy-boundary.md)
for configurable lunch/mode rules, staff overrides, legacy compatibility and the
future administrator authorization contract. No management endpoints are added.

New local policy previews require an explicit resolved institution policy. The
initial university preset enforces its confirmed rank/appointment eligibility
and six-date maximum across streams and delivery modes; these values are not
generic solver defaults. Previews support target-overage and deviation optimization
while keeping explicit maximum
caps separate. Historical snapshots retain their original serialization and are
audited separately against current policy. See the [teaching assignment policy](docs/teaching-assignment-policy.md)
for the confirmed permissions, diagnostics and unresolved rank evidence.
