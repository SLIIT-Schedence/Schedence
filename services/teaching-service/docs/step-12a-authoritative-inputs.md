# Step 12A: authoritative scheduling input foundation

This layer composes the existing contracts; it creates no academic master data,
API or database connection. Accepted development workbooks stay in staging until
the university supplies missing identifiers, populations, demand and policies.
Source confirmation is a supplied claim, not proof obtained by this code.

## Entry contract and ownership

`integration.NormalizedSchedulingInput` is the complete-policy profile used by
the new bridge. Existing direct solver callers and optional modes are unchanged.
It embeds existing Pydantic models rather than defining another staff, offering,
group, requirement, venue, calendar or policy model.

| Envelope field | Existing contract / meaning | Authoritative source |
| --- | --- | --- |
| `institution_id` | Supplied UUID; never generated or inferred | Authorized institution context supplied upstream |
| `academic_term_reference`, `semester` | Calendar term identifier and semester 1–2 | Academic Data Service (ADS) |
| `calendar` | `PreferenceCalendar`: actual week reference, inclusive start/end, day-to-date mapping, synthetic flag | ADS/calendar authority |
| `academic_staff_references` | Preserved Teaching staff ID → ADS staff UUID, one-to-one | ADS |
| `combination_input.teaching_input` | Existing `TeachingTimetableInput` with all five academic collections | ADS |
| `combination_input.group_specializations` | Explicit specialization per original group | ADS |
| `combination_input.policy`, `requirement_permissions` | Explicit combination permission and shared-content evidence; no module-code inference | ADS/confirmed academic policy |
| `combination_input.population_evidence`, `memberships` | Existing relationship assertions or referenced membership evidence | ADS/student population authority |
| `demand_complete` | Explicit affirmation that the requested demand export is complete | ADS |
| `venue_input` | `VenueSchedulingInput`, source verification, calendars, eligibility, combined/shared spaces | Resource Service or its confirmed development export |
| `staff_input` | `StaffSchedulingInput` and `WeeklyWorkloadInput`, dated availability, target classification, explicit caps | ADS/confirmed staff policy |
| `work_period_input` | `WorkPeriodInput`: appointments, upstream resolutions, approvals, fixed/flexible admin and research policies/periods | ADS/authorized policy authority |
| `preference_input` | `LecturerPreferenceInput`, confirmed applicability, numeric weights and provenance | ADS/authorized staff preference export |
| `student_lunch_policy` | `StudentLunchPolicy`, confirmed fixed periods and explicit date/stream scope | Confirmed academic campus policy |
| `solver_config`, `combination_config` | Existing solver grid, operational limits, combination objective | Teaching Service research configuration |
| `work_period_config`, `preference_policy/config`, `fairness_policy/config`, `consecutive_policy/config`, `student_lunch_config` | Existing policy enablement, interpretation and objective-priority approvals | Teaching Service, referencing required campus approvals |
| `mode` | Explicit `ACTUAL_CAMPUS` or `SYNTHETIC` | Teaching request context |

`StudentGroup.academic_year` and `ModuleOffering.academic_year` mean **study year
1–4**, never calendar year. Semester must agree with the envelope and between
groups and offerings. Streams remain explicit, while all streams share the
same global staff/venue scheduling problem.

The initial complete profile requires every policy block, even if the source
explicitly confirms an empty preference or reservation collection. This is a
deliberate integration boundary; it does not imply every policy must constrain
or penalize every staff member. A future smaller profile must have explicit
feature declarations rather than dropping unknown source data silently.

## Required academic records

* `StaffMember`: stable ID, name, position, explicit ACADEMIC/INSTRUCTOR type;
  department remains optional. LIC/CO_LIC eligibility uses academic type, not
  login role. No instructor preference rows are invented.
* `ModuleOffering`: authoritative offering ID, module code/name, study year,
  semester, specialization, degree type, stream. Two offerings may share
  `IT2100`; source-row diagnostic keys must never become offering identifiers.
* `TeachingTeamMember`: offering/staff references and role; these describe
  eligibility, not confirmed session allocation.
* `StudentGroup`: stable ID, year, semester, stream, positive supplied count,
  optional parent. Specialization is retained in the existing combination map.
  Hierarchy does not establish sibling disjointness or university-wide sizes.
* `SessionRequirement`: stable requirement/offering/group IDs, session type,
  positive duration and weekly frequency. Weekly hours alone cannot supply
  group-specific duration/frequency. Original requirements remain intact.

For this profile, every unrelated group pair must have a confirmed known
population relationship, including across streams and specializations. Parent/
descendant overlap follows the existing hierarchy. Known cross-group overlap
uses the existing solver clash logic; unknown relationships block readiness.
This is stricter input completeness, not a change to solver constraints. It
avoids claiming a campus timetable is ready while repeat-student clashes remain
unknown. Referenced disjoint partitions can cover several groups in one record;
users need not enumerate combination pairings. Complete rosters are supported
but are private input snapshots and must not enter diagnostic reports or Git.

Existing combination preparation decides eligible same-content, specialization,
year/semester/stream, duration/frequency and common-staff combinations. Separate,
combined and mixed delivery remain native alternatives. Group counts are added
only with positive disjointness evidence. Physical delivery coverage and
lecturer minutes are independently validated without duplicate fulfillment.

## Explicit adapters and source-to-field mappings

`adapt_academic_tables()` accepts five `AcademicSourceTable` exports. Each uses
an explicit `{existing_contract_field: exact_exported_column}` map and
`AcademicSourceRow(values=..., source=SourceAttestation(...))`. No guessed header,
case-folded ID, generated offering ID, row filtering or duplicate merging is
performed. Rows remain in the returned payload when invalid. `teaching_input`
is available only when the adaptation and existing cross-record checks pass.
The result also retains copied source tables and path-scoped evidence. Empty
collections need an explicit collection-level attestation at submission time.

For example, a **synthetic** export can map `offering_id` to `OfferingKey` and
`module_code` to `ModuleCode`. These are examples, not assertions about the
current campus workbooks. Optional columns such as `parent_group_id` must also
be included in the explicit map when supplied by that export.

`adapt_academic_staff()` consumes the current ADS `StaffOut` representation:

| Actual ADS field | Teaching destination |
| --- | --- |
| `staff_number` | `StaffMember.staff_id`, preserved verbatim |
| `full_name` | `StaffMember.staff_name` |
| `academic_rank` | `StaffMember.position`; missing rank/position blocks conversion |
| `id` | Separate `academic_staff_references[staff_id]` UUID |
| `institution_id` | Must equal trusted requested institution |
| `employment_status` | Must be explicitly ACTIVE |
| No current typed field | `staff_type` requires explicit authoritative supplemental input |
| No current field | `department` optional, accepted only when supplied |

`adapt_administrative_resolution()` consumes current ADS
`AdministrativeWorkloadOut`: UUID `staff_id` resolves through the explicit
crosswalk; `aggregate.status/rule/total_admin_hours_per_week` map to the existing
`AdministrativePolicyResolution`. Hours convert exactly to integer minutes;
fractional minutes, negative and nonfinite values are rejected. It preserves
appointment IDs, source provenance and `NOT_COMPUTED`; it never recomputes ADS
aggregation. Both effective dates are the response's `as_of` date. A single
daily response does **not** establish full-week appointment/policy applicability.
Obtain explicit dated appointment/policy exports for `AppointmentApplicability`
and validated weekly resolutions. Override/no-policy/ambiguous aggregation must
remain unresolved until authoritative evidence is supplied.

Existing accepted-workbook adapters remain the only workbook readers:

| Source layout / actual headers | Existing destination and boundary |
| --- | --- |
| Staff `Staff_ID`, `Staff_Name`, `Position`, `Department`, `Data_Status` | `wide_adapter` staff records with explicit configured type classification; development provenance remains unverified |
| Modules `Module_Code`, `Module_Name`, `Year`, `Semester`, `Specialization`, `Module_Degree_Type` | Staged offering fields; configured semester translation, no offering ID/stream fabrication |
| `LIC_Staff_ID`/`LIC`, `Co_LIC_Staff_ID`/`Co_LIC`, `Team_Staff_ID_1`–`5`/`Teaching_Team_Member_1`–`5` | Original normalized team roles with source location; offering conversion blocked until actual offering ID exists |
| Preference `Preferred_Free_Day`, `Weekend_Work_Pattern`, weekday `Morning`/`Afternoon` columns and retained attendance fields | Existing `map_lecturer_preferences`; unscoped/unweighted candidates and unresolved hard-availability assertions stay staging |
| Venue `Venue_ID`, `Venue_Name`, `Venue_Type`, `Capacity`, `Building`, `Floor`, `Lab_Specialization` | Existing `import_venue_excel` and `venues_from_import`, preserving checksum-bound campus confirmation and original status columns |

Use `venues_from_import(import_result, **explicit_scheduling_evidence)` when
building a complete venue input. It preserves imported inventory/source
verification while requiring separate availability, blocks and relationship
evidence. Building/floor are inventory metadata; distance/travel optimization
remains outside scope.

## Provenance and readiness

`SchedulingSubmission(inputs=..., evidence=[InputEvidence(...)])` intentionally
accepts incomplete raw input so missing fields can be reported without a
solver call. The enclosing submission/evidence must themselves be valid typed
contracts. Each attestation contains an exact tuple path, owner, institution,
verification status, confirmation reference, source/reference, validity dates,
synthetic flag and optional existing `SourceLocation` plus column mapping.
Validity dates describe the export's applicability, not dates invented for
individual appointments. Required source claims cover the whole academic week.

A record or collection attestation covers supplied descendants. A more specific
field/row attestation overrides it; equally specific claims are AMBIGUOUS. No
claim can supply a missing value. All supplied non-null values, including empty
collections, need an attestation. Required unknown calendar/policy fields are
checked explicitly even if reusable models have backward-compatible defaults.
Every work-period staff ID also needs an explicit institution mapping; supplied
institutions on appointments, resolutions, policies and reservations must agree.

`assess_readiness()` returns CONFIRMED, MISSING, AMBIGUOUS, UNVERIFIED or INVALID
diagnostics. Paths identify fields/row indices; mapped Excel provenance adds
workbook, worksheet, actual row and column. Messages never echo source values,
names or membership lists. Existing aggregate model failures identify their
collection; native policy preflight failures identify the responsible context.
No partially valid records are discarded to manufacture a ready payload.

`run_scheduling(submission, expected_institution_id=trusted_uuid,
actual_campus=True)`:

1. Copies/revalidates the submission and rejects tenant/mode mismatches.
2. Checks identifiers, references, completeness, evidence, population coverage,
   source ownership and date/stream applicability.
3. Reuses group/occurrence/combination, work-period and preference preparation.
4. Runs existing solver input checks without constructing CP.
5. Returns readiness plus `timetable=None` if blocked. No synthetic fallback.
6. Otherwise calls the unchanged CP-SAT engine and full independent evaluation
   gateway. Failure results are also checked for absence of partial schedules.

Synthetic runs require explicit `actual_campus=False`, matching payload/calendar
flags and synthetic evidence. `ready_for_generation` establishes technical input
completeness, not feasibility, official verification, target fulfillment,
booking rights or approval. `ready_for_actual_campus` additionally requires actual
mode. All reports retain `university_ready=false`. A future authenticated
boundary must verify the caller, source attestations and institution using
Identity permissions; metadata alone is not a security boundary.

Staff targets remain separate teaching targets and administrative allocations:
Professor 360+240 minutes, HOD/Dean 240+240. Other teaching targets and attendance
exemptions remain unchanged; Assistant Professor receives neither automatic
Professor exemption nor administrative allocation. Appointment overrides never
change academic rank. Reservations do not prove completed administrative work
or attendance, and targets are not silently maximum-only caps.

Fixed student lunch remains 750–810 minutes for years 1/2 and 810–870 for years
3/4, separately from lecturers. Explicit dates, streams and confirmation are
mandatory here; no weekday/weekend applicability or opening hours are inferred.

## Run and verify

From the repository root, using the existing virtual environment:

```powershell
.\.venv\Scripts\python.exe -B services/teaching-service/examples/authoritative-data-audit.py
.\.venv\Scripts\python.exe -B services/teaching-service/examples/authoritative-data-audit.py --include-row-diagnostics
.\.venv\Scripts\python.exe -B services/teaching-service/examples/authoritative-input-demo.py
.\.venv\Scripts\python.exe -B -m pytest services/teaching-service/tests -q
.\.venv\Scripts\python.exe -B -m pip check
```

The audit writes nothing and emits safe aggregates by default; optional row
diagnostics contain metadata only. Missing files or import errors fail visibly.
Private workbooks and prior local reports remain Git-ignored.

The new synthetic fixture wraps a copy of the historical Step 11C fixture,
supplies explicitly fictional institution/crosswalk/population evidence and
both-stream lunch scope, then uses the integration entry point. It produces
seven exact-once original occurrences, six physical deliveries, one combined
lecture and 315 lecturer contact minutes, with all native policy checks. No
real input or existing historical example is modified. This is not a scalability
benchmark or a real campus timetable.

See [the fresh dataset audit and required campus inputs](step-12a-dataset-audit.md)
and [the proposed ownership/persistence plan](step-12a-ownership-plan.md).

Verification on 2026-10-15: **1,987 tests passed**, comprising all 1,906 previous
tests plus 81 integration tests; `pip check` reported no broken requirements.
The standalone demo returned OPTIMAL with independent validation and the exact
coverage/delivery figures above. The sole full-suite warning is the existing
Starlette/httpx TestClient deprecation. Source hashes confirmed all pre-existing
service files unchanged except the README, with no missing files. No dependency
change was required.
