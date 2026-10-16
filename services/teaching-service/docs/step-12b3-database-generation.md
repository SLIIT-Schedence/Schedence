# Step 12B.3: database-backed timetable previews

Database-loaded inputs generated the small Year 4 IT Weekday elective preview and
both complete supported known-session semester alternatives. Every selected
occurrence was scheduled and independently validated. These are local previews;
no production timetable version, master record, migration or grant was changed.

## Application boundary

`load_generation_scope(engines, target, request)` is the async application-service
entry point. `DatabaseGenerationRequest` takes an existing institution UUID,
snapshot UUID, study-year set, semester, stream set, optional specialization set,
explicit calendar label, explicit policy version and search budget. It returns
`DatabaseGenerationInputs`: the existing `EnrolledPP1`/prepared solver inputs,
equivalence results, provenance and blocked-scope diagnostics.

The adapter resolves existing owner UUIDs; local offering identifiers remain
labelled PP1 source-row references, not authoritative offering IDs. It rebuilds
the existing catalog/preference adapter records from PostgreSQL rows, without
opening their source workbooks. It reuses snapshot replay, group hierarchy,
elective selection validation, demand assessment, scope projection, native
CP-SAT and independent timetable validation. No solver module was changed.

Database transactions close before scheduling. The generation contracts and
solver remain independent of SQLAlchemy/PostgreSQL. A missing owner reference,
inconsistent source checksum, altered academic scope or incompatible policy
fails before generation. Blocked-only requests fail explicitly instead of
returning an empty successful timetable.

## Database reads and ownership

| Owner | Tables read | Purpose |
| --- | --- | --- |
| Academic | `staff`, `staff_teaching_profiles`, `departments` | Preserve staff identity, type, source rank and department |
| Academic | `source_datasets`, `modules`, `module_specializations`, `specializations`, `module_teaching_teams` | Versioned source rows, distinct module identities, applicability and staff eligibility |
| Academic | `lecturer_preferences` | Reconstruct the historical confirmed interpretation of source preferences |
| Academic | `cohort_totals`, `pp1_specialization_allocations`, `pp1_student_groups` | Exact aggregate reconciliation, estimated specialization allocation and base group hierarchy |
| Academic | `module_classification_reviews` | Report differences from the selected historical elective policy |
| Resource | `source_datasets`, `venues` | Source provenance, capacity, suitability and inventory attributes |
| Teaching | `input_snapshots`, `snapshot_sources`, `snapshot_scopes`, `snapshot_issues` | Immutable validated demand, elective memberships, calendar, policy, source bindings and unresolved requirements |
| Teaching | `timetable_series`, `generation_runs`, `validation_reports`, `timetable_versions`, `deliveries`, `delivery_coverage` | Revalidate and compare archived drafts; verify their preservation |

Teaching uses `schedence_teaching_runtime`. It has no master-schema privileges.
Academic and Resource reads use their existing restricted owner-role connections,
`schedence_academic_excel_importer` and `schedence_resource_excel_importer`, only
inside database-enforced read-only transactions. Their import responsibilities
are not invoked. This preserves service ownership without granting Teaching
cross-service master access or using the privileged database owner.

Every connection checks the expected database/project/branch, restricted role,
absence of BYPASSRLS/elevated membership/table ownership, and read-only mode.
Every query includes the institution UUID. The 24 new domain tables retain
forced institution RLS with the reviewed USING/WITH CHECK predicate. Read-only
live tests verify all three schema boundaries by switching transaction-local
institution context and observing zero visibility of the real tenant's rows.
Loading the real snapshot under another institution also fails.

**Legacy exception:** `academic.staff` predates this integration and has no RLS.
The adapter applies an explicit institution filter through the restricted
Academic connection; it does not claim database-enforced isolation on that
legacy table or change its security. A future owner-approved Academic reader
interface could replace these owner connections without changing the solver.

Transactions begin with `SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ
ONLY`, set only transaction-local institution context and end with rollback.
Reads span three separate owner transactions; they are not a distributed atomic
snapshot. Semantic cross-checks and final before/after fingerprints reject
observable changes during this preview. A future multi-writer workflow may need
explicit immutable owner versions or a coordinated read service.

## Equivalence and provenance

The adapter verifies source filename **and checksum version**, source provenance,
owner references, all selected module metadata, specialization links, source
weekly contact minutes, staff/team eligibility and venue attributes. It preserves
the distinct IT2100 source records and unsplit two-hour weekly lectures.

All 128 approved proportional allocations reconcile to the 16 original cohort
totals. The 338 base planning groups retain hierarchy and counts. Historical
elective attendance groups are replayed separately and checked using exact-two
elective registration evidence and the existing student demand validator; base
groups are not substituted for elective attendance groups. Planning caps remain
120/main group and 60/lab subgroup.

Calendar configuration, year-specific lunch, hybrid delivery windows, staff
availability/protected work, preference interpretation references and existing
solver context are replayed from the selected immutable snapshot. University
source data, estimated PP1 specialization allocations, synthetic elective
registrations and experimental calendar settings retain separate provenance.
Research stays compulsory and unresolved, not zero academic work.

The fresh database inputs match both corresponding persisted snapshots and the
earlier validated source-input JSON structures. The earlier JSON is comparison
evidence only, never a fallback generation source. No Excel file is opened by
the database-backed path. Tests patch `openpyxl.load_workbook` to fail if called.

## Generated previews

Explicit selections: `PP1_EXPERIMENTAL_CALENDAR` and `HISTORICAL_SNAPSHOT`.
Weekend physical teaching remains Saturday/Sunday 08:30–17:30; Weekend online
Monday ends at 18:30 in this experimental calendar. Weekday online teaching is
Wednesday. The configured 2026-10-19 through 2026-10-25 week is testing-only.

| Selected scope | Expected | Scheduled | Unscheduled | Status | Hard violations | Solver seconds |
| --- | ---: | ---: | ---: | --- | ---: | ---: |
| Year 4 Semester 1 IT Weekday elective component | 16 | 16 | 0 | OPTIMAL | 0 | 0.57 |
| Semester 1, all supported known-session scopes, both streams | 697 | 697 | 0 | FEASIBLE | 0 | 84.55 |
| Semester 2, all supported known-session scopes, both streams | 704 | 704 | 0 | FEASIBLE | 0 | 81.51 |

Full semesters use the existing coordinated cohort decomposition: previous
lecturer/venue bookings are reserved for subsequent native CP-SAT subproblems,
then the complete union is independently checked. FEASIBLE does not imply global
objective optimality. Runtime is the existing solver's aggregate runtime, not
database loading/export wall time. Semester 1 and Semester 2 remain separate
alternatives, with no claim of concurrent conflict freedom.

Both freshly generated full-semester results have identical occurrence coverage
and identical lecturer, venue, day and time bookings to the saved drafts. Saved
bookings were comparison evidence only, not solver hints. Archived normalized
deliveries were independently revalidated first. Fresh post-generation reads
found every inspected owner/history row unchanged.

The current classification review excludes IT4070 (source row 127) from the PP1
candidate pool. The selected historical Semester 2 policy includes **14
occurrences: 7 Weekday and 7 Weekend**. They are retained explicitly for this
historical-policy preview. The new review is reported, not silently applied.
A future run under the revised policy needs a reviewed new input/version; no
historical draft was rewritten here.

## Local outputs and reproduction

All private outputs are under:

`local-data/database-previews/step12b3-reviewed/previews/`

- `index.html`: links to all three generated previews.
- Each scope: `weekly.html`, `timetable.html`, `timetable.csv`,
  `solver-result.json`, specialization HTML, group/lecturer/venue views,
  independent and elective validation, coverage and database generation reports.
- `semester-{1,2}.input-equivalence.json`: database and earlier-source comparison.
- `semester-{1,2}.historical-validation.json`: recomputed archived-draft checks.
- Each full semester: `historical-comparison.json` with booking differences.
- `database-preservation.json`: identical before/after owner/history fingerprints.
- `run-summary.json` and `remaining-blockers.json`: coverage and explicit limits.

Use the existing virtual environment from the repository root. The private
request file holds actual institution and snapshot UUIDs, calendar, policy and
search budget; the credentials file remains ignored and is never printed.
Choose a **new** output directory for another run:

```powershell
.\.venv\Scripts\python.exe -m schedence_teaching.persistence.database_preview `
  --request services/teaching-service/local-data/database-previews/step12b3-reviewed/request.json `
  --credentials-file services/teaching-service/local-data/persistence-preview/step12b2/resumed-production/restricted-runtime.env `
  --earlier-snapshots services/teaching-service/local-data/persistence-preview/step12b2/prepared-inputs `
  --output services/teaching-service/local-data/database-previews/ANOTHER-NEW-RUN/previews `
  --database neondb --project-id <NEON_PROJECT_ID> --branch-id <NEON_BRANCH_ID>
```

There is no default experimental calendar or policy upgrade. A request with a
calendar label differing from its selected snapshot is rejected. The preview
directory must be new and inside `local-data`; older artifacts are preserved.

## Files and verification

Created:

- `src/schedence_teaching/persistence/database_read.py`
- `src/schedence_teaching/persistence/generation_inputs.py`
- `src/schedence_teaching/persistence/historical_generation.py`
- `src/schedence_teaching/persistence/database_preview.py`
- `tests/test_database_generation.py`
- `tests/test_database_generation_readonly_neon.py`
- This report.

Modified: Teaching Service `README.md`. Local request, test logs, preservation
evidence and preview outputs are private ignored artifacts. No other service,
solver module, database migration, source workbook or existing preview changed.

Verification completed successfully:

| Check | Result | Runtime |
| --- | --- | ---: |
| Full Teaching suite, including read-only Neon tests | **2,516 passed, 9 skipped**, zero failures/errors | 239.19 s pytest; 240.75 s including wrapper startup |
| New synthetic database-generation tests | 61 passed (also included above) | 8.01 s |
| New live read-only Neon tests | 7 passed (included in full suite) | Included above |
| Relevant Academic suite | **33 passed** | 6.48 s pytest; 10.35 s including wrapper startup |
| `python -m pip check` | No broken requirements found | — |
| Archived draft revalidation and new preview validation | Passed; zero hard violations | Included in generation workflow |
| CSV/JSON/export coverage | 16/16, 697/697, 704/704; all four view categories present | — |
| Database before/after fingerprints | Both snapshots and all inspected owner/history rows unchanged | — |

The single Teaching test warning is the existing FastAPI/Starlette TestClient
httpx deprecation; no dependency upgrade was made for this step. Tests cover
source and owner-reference corruption, calendar/scope selection, elective
reconstruction, semantic equivalence, archived coverage corruption, restrictive
role/RLS requirements, native CP-SAT generation and independent validation.

The new live tests are enabled only by
`SCHEDENCE_TEACHING_READONLY_DB_TESTS=1` plus explicit private request/connection
configuration. Existing `SCHEDENCE_TEACHING_DB_TESTS` rollback-write tests stay
disabled: even rolled-back production writes are outside this step's scope.

Commands used for final checks:

```powershell
.\.venv\Scripts\python.exe services/teaching-service/local-data/database-previews/step12b3-reviewed/verify_step12b3.py
.\.venv\Scripts\python.exe -m pytest services/teaching-service/tests/test_database_generation.py -q -x --tb=short
.\.venv\Scripts\python.exe -m pip check
```

The ignored verification wrapper explicitly disables the old production-write
test opt-in, supplies the private read-only test configuration, and saves
`teaching-tests.log` and `teaching-test-results.json`. Academic tests were run
with `pytest.main(['tests', '-q', '--tb=short'])` from the Academic Service root,
using the repository virtual environment and its existing service/auth-core
import paths; logs and runtime evidence are saved alongside the Teaching log.
Workspace preservation hashes confirm all prior source workbooks, existing
preview artifacts, solver files and other services' files remain unchanged.

## Remaining blockers

- Year 4 Semester 1 DS: two blocked scopes, 42 Weekday + 106 Weekend students
  (**148**); no module mapping is fabricated.
- Compulsory Year 4 Research session type/duration/frequency/semester/staff mapping
  remains `RESEARCH_PENDING_SESSION_MAPPING`. The small result is
  `ELECTIVE_COMPONENT_COMPLETE`; full-semester results cover available known
  teaching components. All three have `academic_complete=false`.
- The Monday 18:30 calendar remains experimental and explicitly selected.
- Historical IT4070 demand requires a separately reviewed new policy/input
  version before regenerating under the latest candidate exclusion.
- Local offering IDs remain source-row references pending authoritative offering
  records; previews do not invent external academic identifiers.
- These previews have not been published or persisted as new production versions.
- Concurrent-semester validation remains separate future work.
