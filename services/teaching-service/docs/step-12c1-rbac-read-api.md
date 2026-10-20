# Step 12C.1: Academic Admin RBAC and timetable read APIs

The Teaching Service now exposes authenticated, institution-authorized read APIs
using existing Identity contracts. Its production database access remains
read-only. No Identity permission catalogue, user assignment, database schema,
academic record, solver constraint or historical timetable was changed.

## Existing architecture and ownership

Identity uses AWS Cognito managed login and shared `packages/auth-core` access-token
verification. That verifier checks RS256/JWKS, issuer, expiry, token use, app-client
ID and configured OAuth scopes. Identity then resolves the verified Cognito subject
to an active database account and its active institution-specific role grants.
User-role assignment remains Identity-owned through its existing account/role
operations and operator System Admin bootstrap. Teaching does not read Identity
tables, manage accounts, seed grants or mint production tokens.

The existing service-to-service authorization endpoint is
`GET /api/v1/identity/me/permissions`, called with the caller's own verified bearer
token. Teaching reuses the Academic Service's integration pattern and that exact
contract: it verifies locally, calls Identity, requires ACTIVE account status and
a matching subject, then uses only the returned institution roles/permissions.
The Teaching adapter lives in its own package because the other services' top-level
`app` packages are separately deployed applications, not shared import libraries.

No role/permission data is taken from JWT custom claims, Cognito groups, query
parameters or caller-controlled headers. Permissions are fetched on every request;
there is no Teaching role cache delaying revocation. Identity failure, redirect,
inconsistent subject, malformed response or disabled account fails closed.
Identity's origin is operator-configured, requires HTTPS outside loopback, and
redirects are not followed. Tokens, credentials and driver errors are not logged
or returned by the new HTTP boundary.

The repository README describes the gateway/BFF as conceptual. No implemented
gateway directory/configuration was found; no gateway changes were invented.
Canonical routes follow the existing `/api/v1/<service>` convention. The requested
unversioned `/api/teaching` alias is supported but hidden from duplicate OpenAPI
documentation.

## Roles and compatible permission mapping

All roles already exist in Identity: the platform role `SYSTEM_ADMIN` and the
institution roles below. The user's Non-Academic/Super Admin maps to the existing
institution-scoped `INSTITUTION_ADMIN`, not a newly invented role. (Earlier
Identity versions called this role `SUPER_ADMIN`.) Academic rank, appointment and
access role stay separate.

| Existing role | Teaching behavior in this step |
| --- | --- |
| `ACADEMIC_ADMIN` | Existing institution-scoped `teaching:generate` or `teaching:approve` grants imply administrative read access only; no generation/management endpoint is implemented |
| `EXAMINATION_ADMIN` | Examination permissions grant no Teaching access; a separately explicit Teaching grant is required |
| `INSTITUTION_ADMIN` | Existing identity/academic administration permissions do not grant private Teaching reads |
| `SYSTEM_ADMIN` | Platform-only `platform:*` permissions sit outside every institution and grant no Teaching access |
| `VENUE_MANAGER` | Venue permissions remain Resource-owned; they do not grant Teaching draft access, lecture edits or approval |
| `LECTURER` | Personal read contract requires explicit permission plus an owner-resolved staff binding; private personal drafts require an additional explicit grant |
| `STUDENT` | Published-only read contract requires explicit permission and a complete owner-resolved academic/group scope; private history and institutional validation remain forbidden |

The existing catalogue has no dedicated Teaching read permission. Compatibility
with existing `teaching:generate`/`teaching:approve` avoids changing Identity's
ownership or granting an unrelated role a new implicit entitlement. Any role
explicitly granted those Teaching permissions or a dedicated administrative read
permission can perform the corresponding read; unrelated examination/venue/
academic-workload permissions cannot.

Dedicated extension contracts, **not seeded or assumed present in production**:

- `teaching:timetables:read`: institution-level administrative read.
- `teaching:timetables:read:self`: lecturer published personal view.
- `teaching:timetables:read:self:drafts`: additional personal draft access.
- `teaching:timetables:read:published`: student published academic-scope view.

Future management permission vocabulary is declared only: existing
`teaching:generate`, `teaching:approve`, and proposed
`teaching:timetables:edit`, `teaching:timetables:regenerate`,
`teaching:timetables:publish`. Identity-owner review is required before catalogue
or assignment changes. No POST/PATCH/DELETE routes, approval actions or publication
actions are implemented here.

## Institution and personal-scope enforcement

Authorization finishes before opening the timetable repository. An explicit
`institution_id` selects only an institution where Identity grants the required
Teaching permission. Without it, exactly one authorized institution is selected;
multiple authorized institutions require explicit selection. Membership alone or
an institution query value never establishes permission.

Every SQL query retains institution filters. The existing `timetable_reader`
starts a database-enforced read-only transaction, sets transaction-local tenant
context, and verifies the restricted runtime role and forced institution RLS.
Wrong-institution version IDs yield the same 404 envelope as missing IDs.
Unauthorized institution selection yields 403 without querying the database.

**Current owner-integration gaps:** Identity's permissions response contains no
staff number or student enrollment scope. `academic.staff.identity_user_id`
exists, but the Academic Service has no current lecturer self-mapping endpoint,
and its general staff endpoint requires administrative read permission. There is
no authoritative student-account-to-registration/group endpoint. Teaching does
not guess these links from names, email, JWT username, staff rank or PP1 synthetic
student identifiers, and does not query the legacy staff table directly.

`AcademicBinding` and the injectable scope-resolver contract prepare that owner
integration. A binding must match both Identity user UUID and authorized institution
and carry an owner provenance reference. Lecturer bindings supply the actual staff
reference. Student bindings require explicit group IDs, study years, semesters,
streams and specializations. The production default resolver returns no mapping
and denies these reads until the owning integration exists.

Students see only their explicitly bound attendance groups and their ancestor
sessions. A parent-group binding does not guess the student's lab subgroup;
partial roster overlap does not establish an individual's elective attendance.
Administrative group filters retain the existing hierarchy and explicit population
evidence semantics. Lecturers are always filtered to their bound staff reference,
including when personal draft access is explicitly granted. Private version
history and institutional validation require administrative Teaching read access.

Neither HOD nor Program Coordinator appointments nor Professor rank are consulted
by authorization. Existing administrative-hour/workload and solver rules are
unchanged.

## GET endpoints and filters

| Canonical endpoint | Result |
| --- | --- |
| `/api/v1/teaching/timetables` | Paginated authorized version summaries |
| `/api/v1/teaching/timetables/{version_id}` | Authorized timetable metadata, scope and provenance |
| `/api/v1/teaching/timetables/{version_id}/sessions` | Paginated actual deliveries with covered original occurrences |
| `/api/v1/teaching/timetables/{version_id}/validation` | Administrative validation, coverage and unresolved limitations |
| `/api/v1/teaching/timetables/{version_id}/versions` | Paginated administrative history of the same series |

All five also work with `/api/teaching` in place of `/api/v1/teaching`.
Filters: institution UUID, `academic_year` 1–4, `semester` 1–2,
`stream=WEEKDAY|WEEKEND`, `specialization`, `student_group`, `lecturer`, `venue`,
and the six logical lifecycle statuses. Pagination follows existing service
conventions: `page` defaults to 1, `page_size` to 20, maximum 100. Invalid/unknown
query fields are rejected with 422. Version searches/counts and pagination are
performed in tenant-filtered SQL; sessions are projected from a verified selected
version, then paginated. Histories order by descending version number.

Version filters select versions containing the declared scope and requested
assignments. Session filters restrict actual booking coverage; a combined delivery
remains one booking with all matching original occurrence entries. Version-wide
validation retains its original complete input boundary and is not misrepresented
as a new validation run for a narrow display filter.

Dashboard responses include version/series, institution and study scopes, module
and original occurrence references, group, session type/duration, assigned lecturer,
venue, ONLINE/PHYSICAL mode, actual date/day/time, solver/validation/lifecycle
status, input snapshot, policy evidence version and calendar provenance/windows.
Offering references remain explicitly labelled snapshot-local, not fabricated
authoritative academic offering IDs. The policy evidence SHA-256 identifies the
actual preserved registration/classification version; it is labelled as a source
evidence hash rather than inventing an authoritative policy-version UUID.

Explicit Pydantic response models omit raw snapshots, registrations, individual
student IDs, staff preference/workload reports, SQL metadata and credentials.
Personal/student views also omit administrative snapshot/source/validation/history
details. Online bookings explicitly have `venue: null`. API responses use safe
error envelopes, request IDs, `Cache-Control: no-store` and `nosniff`.

## Existing drafts remain unpublished

The database still stores only the two immutable experimental draft alternatives:
697 Semester 1 and 704 Semester 2 known occurrences. Logical VALIDATED is distinct
from raw `DRAFT_VALIDATED`; neither means PUBLISHED. A published-only request never
relabels these drafts. The publication guard additionally requires a genuinely
published storage/logical status, academic completion, passed validation and no
testing/experimental or unresolved academic limitations.

The deployed draft-only schema and read-back adapter do not yet support real
published records. Positive student publication tests use a hypothetical sanitized
future published record; they do not create or claim a published production
timetable. The existing production student portal remains denied until both
authorized enrollment bindings and a reviewed publication workflow exist.

Year 4 S1 DS remains blocked for 148 students. Compulsory Research remains pending
session mapping. Semester 2's 14 historical IT4070 occurrences remain exactly
preserved under the original policy. The two experimental semesters remain
separate alternatives; the API does not imply concurrent clash freedom.

## Local startup and examples

Shared `auth-core` is now an explicit Teaching runtime dependency. Install local
shared packages before the service; no second JWT verifier or login system is used:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ./packages/db-core -e ./packages/auth-core -e "./services/teaching-service[persistence,dev]"
$env:TEACHING_DATABASE_ENV_FILE = (Resolve-Path 'services/teaching-service/local-data/persistence-preview/step12b2/resumed-production/restricted-runtime.env').Path
.\.venv\Scripts\python.exe -m uvicorn schedence_teaching.main:app --app-dir services/teaching-service/src --port 8003 --loop asyncio:SelectorEventLoop
```

Identity conventionally uses 8001 and Academic 8002; Teaching must use a distinct
port when they run together. Configure existing Cognito settings through the
shared ignored `.env`/`SCHEDENCE_ENV_FILE`, and `IDENTITY_SERVICE_URL` for the
trusted Identity origin. Use dedicated `TEACHING_` database settings; do not point
Teaching at owner credentials. Health/import remain free of configuration and
database side effects; authenticated read runtime configuration is loaded lazily.

An authorized GET summary contains fields such as the following actual-result
excerpt (identifiers and private staff details omitted):

```json
{
  "version_number": 1,
  "lifecycle_status": "VALIDATED",
  "storage_status": "DRAFT_VALIDATED",
  "solver_status": "FEASIBLE",
  "validation_status": "PASSED",
  "academic_complete": false,
  "full_scope_expected_occurrences": 697
}
```

`GET .../{version_id}/sessions?page_size=1` returns one booking plus `total: 697`
or `704`, preserving original module hours and assignments. Validation returns
zero hard violations and separate Research/DS limitations. Do not paste access
tokens into source, issue text or logs. Use the existing Identity login/tooling.

## Verification and affected files

Created `api/__init__.py`, `authorization.py`, `contracts.py`, `errors.py`,
`identity.py`, `routes.py`, `runtime.py`, `service.py`, `store.py`;
`tests/test_teaching_read_api.py`, `tests/test_teaching_read_api_postgres.py`;
and this report. Modified Teaching `main.py`, `pyproject.toml`,
`persistence/repositories.py` and `README.md`. Other services, shared package
sources, database models/migrations and solver modules are unchanged.

The 53 focused checks exercise real shared RS256/JWKS verification with local
signing fixtures, trusted Identity HTTP responses, all roles, spoofed JWT/header
claims, expired/invalid tokens, fresh revocation, cross-institution rejection,
personal/student restrictions, pagination/filters, response redaction and SQL/RLS.
The isolated PostgreSQL HTTP test records only SELECT/SET during API requests;
its synthetic setup writes are confined to the dedicated localhost test database.

A real Uvicorn localhost demonstration performed **19 successful expected-status
checks** against the existing production drafts through the restricted reader.
Authentication used clearly labelled local signed test fixtures and a mocked
trusted Identity response using the existing role catalogue. This validates the
HTTP/database path; it is not a claim of live Cognito account assignment testing.
The demonstration server was stopped. Both production fingerprints match, with
two snapshots/runs/versions/reports and 1,401 deliveries/coverage rows unchanged.

Private outputs are under `local-data/api-preview/step12c1-reviewed/`:
`http-verification.json`, `authorized-api-responses.json`, production before/after
fingerprints/counts, test logs, preservation evidence and ignored test-auth/demo
helpers. Real staff information and test bearer material remain ignored and are
not copied into this public report.

| Verification | Result | Pytest runtime |
| --- | --- | ---: |
| Full Teaching suite | **2,626 passed, 9 skipped**, zero failures/errors | 268.58 s |
| Focused API and PostgreSQL HTTP checks | 53 passed, included above | 15.92 s |
| Relevant Identity suite | **76 passed** | 7.23 s |
| Relevant Academic suite | **33 passed** | 6.92 s |
| Shared auth-core suite | **29 passed** | 5.03 s |
| Actual localhost Uvicorn GET checks | **19 expected HTTP outcomes passed** | Recorded separately |
| `pip check` | No broken requirements found | — |
| Production record fingerprint | Unchanged | — |

Across the four pytest suites: **2,764 passed, 9 skipped**. The nine skips are
the older production write-capable checks; that opt-in stayed disabled. The
existing 20 isolated write tests and new PostgreSQL HTTP test used localhost;
seven existing live Neon tests were read-only. The single Teaching suite warning
is the existing Starlette/httpx TestClient deprecation. No authentication library
upgrade or solver change was introduced.

The existing virtual environment lacked `schedence-auth-core` installation and
Identity's declared boto3/botocore dependency. Shared auth-core and Teaching were
installed editable from local source, and the existing Identity dependency was
installed before rerunning its complete tests successfully. No Identity/shared
source file or dependency manifest was changed.

Commands used include:

```powershell
.\.venv\Scripts\python.exe services/teaching-service/local-data/api-preview/step12c1-reviewed/run_demo.py
.\.venv\Scripts\python.exe services/teaching-service/local-data/api-preview/step12c1-reviewed/verify_step12c1.py
.\.venv\Scripts\python.exe services/teaching-service/local-data/api-preview/step12c1-reviewed/verify_owning_service.py identity
.\.venv\Scripts\python.exe services/teaching-service/local-data/api-preview/step12c1-reviewed/verify_owning_service.py academic
.\.venv\Scripts\python.exe services/teaching-service/local-data/api-preview/step12c1-reviewed/verify_owning_service.py auth-core
.\.venv\Scripts\python.exe -m pip check
```

The ignored full-suite helper explicitly disables production write tests, enables
guarded read-only Neon checks, and supplies the isolated localhost database
configuration. Owner-service suites run in separate processes to avoid their
shared `app`/`tests` module names colliding. Temporary local test servers are
stopped after verification. Source workbooks, earlier previews and existing
uncommitted work remain preserved; no branch switch, commit or push occurred.

## Remaining Step 12C.2 work

- Review/seed dedicated read permissions in the Identity-owned catalogue when
  needed; no permission changes were made here.
- Add owner-approved staff self-link and student enrollment/group scope providers;
  default denial remains in place until those bindings are available.
- Verify deployment with real Cognito sign-in and the running Identity Service;
  the signed fixtures do not replace production authentication.
- Design/review the immutable approval/publication lifecycle before any schema
  changes or published student timetable rollout.
- Resolve Research/DS mappings and separately review the updated IT4070 policy
  snapshot before academic-completion or policy-regeneration claims.
- Define the next management API/job workflow separately; this step adds only GET.
