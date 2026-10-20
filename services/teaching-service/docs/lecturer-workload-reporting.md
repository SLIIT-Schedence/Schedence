# Saved-timetable lecturer workload reporting

Reporting reads one validated, institution-scoped saved timetable version. It
does not run CP-SAT, change bookings, persist a report, or publish a draft.
The immutable scheduling snapshot supplies staff, offering identities, policy
profiles, appointments and provenance applicable to that version. Live master
data is not silently substituted for historical policy inputs.

## Access and endpoints

Use the existing Cognito access-token verifier and trusted Identity permissions.
Institution-wide Teaching read authorization is required. The existing
`teaching:timetables:read` grant and legacy `teaching:generate`/`teaching:approve`
administrative read grants remain supported. A role label, HOD appointment,
JWT claim, lecturer self-read grant or student read grant grants no additional
report access. No real account grants are changed by this feature.

The routes appear under both `/api/teaching` and `/api/v1/teaching`:

- `GET /reports/lecturer-workload?version_id=<saved-version-uuid>`
- `GET /reports/lecturer-workload/<staff-id>?version_id=<saved-version-uuid>`
- `GET /reports/lecturer-workload/export?version_id=<saved-version-uuid>&format=pdf&kind=summary`

Other filters are `institution_id`, `academic_year` (study year 1–4),
`semester` (1–2), `stream`, `lecturer`, `academic_rank`, `appointment`,
`module_code` and `offering_id`. Institution selection follows existing trusted
grant rules. `version_id` is mandatory; alternative semesters are never summed.
Select `offering_id` when a module code is ambiguous.

`format=pdf` supports `summary`, `individual`, `detailed`; `format=csv`
supports `summary`, `assignments`, `targets`, `fairness`. Install the existing
`pdf` optional extra for PDF generation. Download responses are private,
`no-store`, and documented in Swagger at `http://127.0.0.1:8003/docs` after
starting the existing service:

```powershell
.\.venv\Scripts\python.exe -m uvicorn schedence_teaching.main:app --app-dir services/teaching-service/src --port 8003
```

Authenticate through Swagger's Authorize dialog using a valid access token
and an existing authorized Identity account. Do not paste tokens in logs or
URLs. This reporting workflow requires no write credentials or generation job.

## Accounting rules

Actual saved delivery duration is credited once to its saved responsible staff
member. Combined lecture coverage, repeated group/subgroup views, eligible
teaching-team members and unscheduled requirements do not multiply that credit.
All snapshot staff, including instructors and zero-assignment staff, are listed.

The internal `VerifiedParticipation` adapter supports explicitly verified
partial participation and additional co-teachers. It is not a request parameter
or a new grant. Evidence must match the institution, version, booking and known
staff; intervals must be within the delivery and may not overlap or be credited
twice. The current saved schema has one responsible staff member per delivery;
real co-teaching cannot be reconstructed from candidate eligibility. No extra
co-teachers have been inferred for the supplied export.

Minutes are exact integers; hour values are presentation conversions. Lecture,
tutorial, workshop, lab, online/physical and weekday/weekend-stream breakdowns
use actual assigned participation. Daily counts use distinct actual dates,
not the campus attendance requirement. A shared-content delivery may carry
multiple offering labels; such module breakdowns are explicitly non-additive.

Display filters restrict reported assignments, but target gaps, explicit cap
checks and normalized comparisons always use the complete selected version's
week. Each row exposes both totals and `target_comparison_scope` to avoid
mistaking a module-only slice for a weekly target deficit.

Existing explicit policy profiles resolve rank and HOD/Dean overrides without
changing rank or parsing display titles. Confirmed teaching targets are 20h
Instructor, 18h Assistant Lecturer, 16h Lecturer, 12h Senior Lecturer, 8h Dr.,
6h Assistant Professor/Professor and 4h HOD/Dean. Targets are not caps. Missing
profiles or legacy combined-work targets yield a missing separate teaching
target. Weekly/daily maximum caps are checked only when explicitly configured.

Separate administrative allocation, approved/proposed weekly reservation,
timed administrative reservation, timed research reservation and verified
completed administrative work remain distinct. Overlapping timed reservations
are unioned per date; untimed and timed reservations are not added together.
Missing completed-work evidence is not proof of zero work; ledger completeness
is reported separately. Six campus days never become six teaching days.

Fairness uses existing exact target-normalized distribution metrics by explicit
rank/appointment. It is descriptive, not a ranking or a claim of equal
opportunity. Missing targets are excluded from normalization; stored fairness
optimization evidence is exposed separately when available.

## Storage and validation

`WorkloadReadService` uses the existing `DatabaseTimetableStore.read` boundary,
database-enforced read-only transactions, restricted runtime role and tenant RLS.
It releases the connection before report calculation/rendering. Pure reporting
and PDF/CSV projections import no solver execution functions. No schema change
or migration is needed. Formula-like display strings are escaped in CSV exports.

Synthetic tests cover policy targets, appointment overrides, zero assignments,
combined coverage, co-teaching, missing evidence, overlapping participation,
filters, target/cap separation, reservations, exact fairness, exports, Cognito/
Identity authorization, tenant denial and OpenAPI. A guarded local PostgreSQL
test verifies RLS, read-only transaction settings, SELECT-only data access and
unchanged saved timetable contents. Production write tests remain disabled.

## Supplied local report

The generated private report selects the historical stored **697-delivery
Semester 1 draft**, not the later corrected **631-delivery local preview**, which
has not replaced the saved version. It contains 110 staff (90 academic, 20
instructors), four with zero assignments, and 72,480 teaching minutes (1,208h).
Five staff lack explicit workload profiles. The saved input provides no explicit
weekly/daily maximum teaching caps; their absence is not a policy compliance
claim. Research and DS mapping blockers remain unchanged. Semester 2 is a
separate version and is not included.

Final PDFs and four CSV exports are under the Git-ignored
`local-data/workload-reports/2026-10-18/reviewed-output/`. The original exports,
report JSON, detached saved record and read-only evidence are preserved under
the adjacent `semester-1-saved-draft/` directory.
They contain private staff display names and must not be committed or published.
