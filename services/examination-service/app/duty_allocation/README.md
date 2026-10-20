# Fairness-Aware Staff Allocation for Examination Duties (preview API)

CP-SAT staff duty allocation, fairness analysis, duty swap / emergency replacement previews and
student seating, exposed under `/api/v1/duty-allocation`. **Generation is always a preview**: nothing is
saved, approved, published or applied unless saving is explicitly enabled (section 9). All data is the **synthetic** `m3_demo` data set (never
SLIIT data or policy), and the synthetic institution is never merged with the real one.

## 1. Install

From the repository root (Python 3.14 is needed for OR-Tools; 3.11+ works without the solver):

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ./packages/db-core -e ./packages/auth-core
python -m pip install -e "./services/examination-service[dev,solver]"
```

## 2. Environment variables (names only – never commit values)

The service keeps its usual authentication/database settings (`AWS_REGION`, `COGNITO_*`,
`SLIIT_INSTITUTION_ID`, `DATABASE_URL`, ...). This component adds `M3_DEMO_*`. They are read
from the **process environment only** (never from `.env`). The feature is **disabled by default**: while
`M3_DEMO_SOURCE` is `DISABLED` the routes are not mounted at all (they return 404 and are absent from OpenAPI).

| Variable | Meaning |
|---|---|
| `M3_DEMO_SOURCE` | `DISABLED` (default), `LOCAL_DEMO` or `NEON_READ_ONLY` |
| `M3_DEMO_PERIOD_CODE` | default period (`PP1-2026-S2-FINAL`) |
| `M3_DEMO_INSTITUTION_ID` | synthetic institution (default `11111111-1111-4111-8111-111111111111`); the service refuses to run if it equals the real institution |
| `M3_DEMO_SNAPSHOT_TTL_SECONDS` | how long the read-only snapshot is reused (default 300) |
| `M3_DEMO_MAX_SOLVER_SECONDS` | upper limit for one solver run (default 60) |
| `M3_DEMO_PERSISTENCE` | saving of drafts: `DISABLED` (default), `MEMORY` (development; lost on restart) or `DATABASE` |
| `M3_DEMO_PERSISTENCE_ACKNOWLEDGEMENT` | DATABASE only: exact text `I APPROVE SAVING SYNTHETIC DRAFTS TO THE EXAMINATIONS SCHEMA` |
| `M3_DEMO_SEED_DIR` | LOCAL_DEMO: folder with `manifest.json` and `csv/` |
| `M3_DEMO_NEON_ENV_FILE` | NEON_READ_ONLY: **absolute** path of an env file holding the URL |
| `M3_DEMO_NEON_URL_VARIABLE` | name of the variable inside that file |
| `M3_DEMO_NEON_HOST`, `M3_DEMO_NEON_DATABASE` | endpoint host and database, as confirmed in Neon Console |
| `M3_DEMO_NEON_ROLE` | the **dedicated read-only role** (never the owner) |
| `M3_DEMO_NEON_BRANCH` | defaults to `production` |
| `M3_DEMO_NEON_CONFIRMED_IN_CONSOLE` | `true` once you checked the endpoint in Neon Console |
| `M3_DEMO_NEON_CREDENTIAL_ROTATED` | `true` once the previously exposed password was rotated |
| `M3_DEMO_NEON_ACKNOWLEDGEMENT` | exact text `I APPROVE READ-ONLY ACCESS TO m3_demo ON THE PRODUCTION BRANCH` |

## 3. Run locally with the LOCAL DEMO source (no database for the demo data)

```powershell
$env:M3_DEMO_SOURCE = "LOCAL_DEMO"
$env:M3_DEMO_SEED_DIR = "C:\Users\Hp\Desktop\Schedence-M3-Seed"
cd services\examination-service
uvicorn app.main:create_app --factory --port 8003 --loop asyncio:SelectorEventLoop
```

* Swagger UI: <http://localhost:8003/docs> (hidden when `APP_ENV=production`); OpenAPI JSON: `/openapi.json`.
* The local source is refused when `APP_ENV=production`.
* Every request needs a Cognito **access token**: GET routes need `examinations:read`, POST routes
  (they compute) need `examinations:generate`.

## 4. Enable NEON READ-ONLY later (one-time setup; nothing connects until all of it is done)

1. Rotate the previously exposed Neon password.
2. As the database owner, review and run `DEDICATED_ROLE_SETUP_SQL` from
   [demo_production.py](demo_production.py) (or the Neon Console equivalent). It creates
   `m3_demo_reader` with `SELECT` on `m3_demo` only. Nothing in this code creates roles.
3. Put the new URL of that role in an env file **outside the repository** and set the variables above.
4. At the first read the role is audited inside the read-only `REPEATABLE READ` transaction; it is
   refused if it is a superuser, can create objects, can write any table, can use other schemas or
   read tables outside `m3_demo` (so the owner role can never be used).

If anything is missing the API answers `503 M3_DEMO_NOT_CONFIGURED` naming the missing variables. It
never falls back to local data: every response says its `meta.data_source`.

## 5. Endpoints (all under `/api/v1/duty-allocation`)

| Method & path | Purpose |
|---|---|
| `GET /status` | active source, what is missing, safeguards (never connects) |
| `POST /snapshot/refresh` | re-read the read-only source now |
| `GET /exam-periods` · `GET /exam-periods/{period}/sessions` | periods and sessions (date, time, rooms, students) |
| `GET /exam-periods/{period}/requirements` | supervisors / invigilators per room (calculated from the staffing policy) |
| `GET /staff` · `GET /staff/{id}` · `GET /staff/{id}/constraints` | staff, rank, eligible roles, availability, unavailability, preferences, restrictions, history, limits |
| `GET /staff/workload` | administrative workload: `COMPUTED`, `NOT_COMPUTED`, `MISSING_RESPONSE` stay distinct |
| `POST /allocations/baseline` · `/fair` · `/compare` | generate a duty roster preview (CP-SAT) |
| `GET /allocations/{run}` · `/roster` · `/fairness` | retrieve the draft roster and fairness metrics |
| `POST /allocations/{run}/validation` | independent validation of the roster, or of a submitted roster |
| `POST /allocations/{run}/swap-preview` | preview two staff exchanging duties |
| `POST /allocations/{run}/replacement-preview` | preview emergency replacements (ranked candidates and rejections) |
| `POST /seating` · `GET /seating/{id}` · `GET /seating/{id}/rooms/{session}/{venue}` | student seating preview, room capacities, room-wise seats |

Run and seating ids are unguessable, held **in memory only** (last 25, lost on restart or with several
workers) and return `404 M3_PREVIEW_NOT_FOUND` once gone – generate again.

## 6. Example requests

```http
POST /api/v1/duty-allocation/allocations/compare
Authorization: Bearer <access token>
{ "time_limit_seconds": 20 }
```
```jsonc
{
  "run_id": "9f2c…", "mode": "COMPARE", "status": "COMPLETED",
  "saved": false, "approved": false, "published": false,
  "rosters": [
    { "label": "FAIR", "solver_status": "OPTIMAL", "independently_valid": true,
      "assignments": [ { "session_id": "X1-IT1010", "venue_id": "H1", "venue_name": "…", "date": "2026-11-16",
                         "start": "2026-11-16T09:00:00", "end": "2026-11-16T11:00:00",
                         "staff_id": "…", "staff_number": "EMP0003", "staff_name": "…", "role": "SUPERVISOR" } ],
      "metrics": { "gini": 0.095, "std_load": 1.2, "per_staff": [ … ] } }
  ],
  "comparison": { "fairer_by_gini": "FAIR", "gini_baseline": 0.289, "gini_fair": 0.095, "assignments_different": 31 },
  "fairness_complete": false, "warnings": [ "Fairness is partial: …" ],
  "meta": { "data_source": "LOCAL_DEMO", "synthetic": true, "preview_only": true, "institution_id": "1111…" }
}
```

```http
POST /api/v1/duty-allocation/allocations/{run_id}/replacement-preview
{ "roster": "FAIR", "duty": { "session_id": "X1-IT1010", "venue_id": "H1", "staff_id": "…", "role": "INVIGILATOR" },
  "reason": "Sick leave" }
```
→ `status` `PROPOSED | INFEASIBLE | INVALID_REQUEST`, `candidates` (best first, with fairness `metrics`),
`rejected` (with `reasons`), `proposed_roster`, `independent_check_violations`, and always `"applied": false`.

## 7. Frontend integration guide

* Send `Authorization: Bearer <Cognito access token>`; CORS allows GET and POST from `FRONTEND_URL`.
* Show `meta.data_source`, `meta.synthetic` and a "preview – not saved" banner on every screen.
* Typical flow: `GET /status` → `GET /exam-periods` → `GET …/sessions` → `POST /allocations/compare`
  → render `rosters[].assignments` (group by `date`, `session_id`, `venue_id`) and `comparison`
  → `POST …/validation` after manual edits → `POST …/replacement-preview` / `swap-preview` →
  `POST /seating` → room grids from `GET /seating/{id}/rooms/…`.
* `seat_state` is `SYNTHETIC` or `PROVISIONAL` for the demo data: never present seat numbers as verified.
* Errors: `{ "detail": "<message>", "code": "M3_…" }` (plus `problems[]` for invalid data); validation
  errors are the standard FastAPI `422`. Workload `status` must be shown as-is; `hours_per_week` is
  `null` unless `COMPUTED`.

## 8. Tests

```powershell
cd services\examination-service
python -m pytest tests/test_duty_allocation_*.py      # Member 3 (use Python 3.14 + [solver] for all of them)
python -m pytest tests/test_scheduling_*.py tests/test_seating_rules.py tests/test_sources.py tests/test_auth.py tests/test_database.py tests/test_migrations.py
```

No test connects to a database. The optional seed-pack tests read `M3_SEED_PACK_DIR` (default
`C:\Users\Hp\Desktop\Schedence-M3-Seed`) and skip when it is absent.

## 9. Saved drafts and the approval workflow

Everything in sections 5-8 is a preview. With `M3_DEMO_PERSISTENCE=MEMORY` (development) or `DATABASE` the
following lifecycle becomes available, under the same `/api/v1/duty-allocation`:

```
DRAFT -> VALIDATED -> APPROVED -> PUBLISHED        REJECTED (revise to continue)   SUPERSEDED (replaced by a published revision)
```

| Action | Endpoint | Permission |
|---|---|---|
| Save a generated roster as DRAFT (validated first; one working roster per period) | `POST /rosters` `{run_id, roster?}` | `examinations:generate` |
| List / read / audit trail | `GET /rosters`, `/rosters/{id}`, `/rosters/{id}/history` | `examinations:read` |
| Validate (rebuilds the inputs, re-runs the independent checks) | `POST /rosters/{id}/validate` | `examinations:generate` |
| Approve (validates again; any hard-constraint violation blocks it) | `POST /rosters/{id}/approve` | `examinations:approve` |
| Publish (needs a verified published timetable and a compatibility check) | `POST /rosters/{id}/publish` | `examinations:approve` |
| Reject with a reason | `POST /rosters/{id}/reject` | `examinations:approve` or `:manage` |
| Controlled revision of a PUBLISHED or REJECTED roster (new DRAFT version) | `POST /rosters/{id}/revisions` | `examinations:manage` |
| Preview a change | `POST /rosters/{id}/change-previews/swap` \| `/replacement` | `examinations:generate` |
| Submit a change request | `POST /rosters/{id}/change-requests/swap` \| `/replacement` | `examinations:generate` |
| List / read requests | `GET /rosters/{id}/change-requests`, `/change-requests/{rid}` | `examinations:read` |
| Approve / reject a request | `POST /change-requests/{rid}/approve` \| `/reject` | `examinations:approve` |
| Apply an approved request (atomic) | `POST /change-requests/{rid}/apply` | `examinations:approve` |
| Cancel an open request (requester, or a manager) | `POST /change-requests/{rid}/cancel` | `examinations:generate` |
| Save a seating plan as a draft; read drafts | `POST /seating/{id}/save`, `GET /seating-drafts[/{id}[/rooms/{session}/{venue}]]` | `generate` / `read` |

Rules the backend enforces:

* Permissions come only from the verified Cognito identity and the Identity Service; no request field or header can grant
  one (a `role` field is rejected with 422).
* Every changing request may send `expected_version` (the roster's `row_version`); a stale screen gets `409 STALE_ROSTER`.
* A roster stores the hash of the inputs it was built on; if the timetable, staff or constraints changed, validation,
  approval and changes are refused (`ROSTER_INPUT_CHANGED`) until a revision is made.
* Staff cannot hold two duties in one session, and cannot overlap duties in another period's working or published roster.
* A **PUBLISHED roster is never edited**. A swap or replacement on it answers `REVISION_REQUIRED`; create a revision,
  change it, validate, approve and publish it again - the old version becomes SUPERSEDED only then.
* Applying a change to an APPROVED roster withdraws the approval (it becomes VALIDATED and must be approved again), and every other open
  request for that roster becomes STALE.
* **Publishing is closed by default.** Synthetic `m3_demo` rosters can never be published
  (`SYNTHETIC_ROSTER_CANNOT_BE_PUBLISHED`), and with no published-timetable contract the answer is `503
  PUBLISHED_TIMETABLE_UNAVAILABLE`. The interface is `PublishedTimetableProvider` in
  [persistence/published_timetable.py](persistence/published_timetable.py); install an implementation as
  `app.state.m3_published_timetable_provider` once Member 2 provides the contract.
* Seats are never `VERIFIED` without a verified hall layout; demo seats stay `SYNTHETIC`.

### Tables and migration (not applied)

[persistence/models.py](persistence/models.py) defines six tables in the `examinations` schema: `duty_rosters`,
`duty_roster_assignments`, `duty_roster_events`, `duty_change_requests`, `seating_drafts`, `seating_draft_seats`. The Alembic
migration is a **proposal** at
[proposed_migrations/20261020_0007_duty_roster_persistence.py](../../proposed_migrations/20261020_0007_duty_roster_persistence.py);
it is outside `alembic/versions` so the shared migration chain and its tests are unchanged, and nothing was run against any
database. To activate it after review: copy it to `alembic/versions`, register the models on `app.db.base.Base`
(`app/db/__init__`/`alembic/env.py` imports), run `alembic upgrade head` yourself on a database you chose, then set
`M3_DEMO_PERSISTENCE=DATABASE` and the acknowledgement. Without the tables, `DATABASE` mode answers `503 PERSISTENCE_NOT_MIGRATED`.

## 10. Not implemented yet / external blockers

* Applying the migration (needs your review and approval) and the first real database save.
* Publishing real rosters: needs Member 2's published-timetable contract and non-synthetic input data.
* Real timetables: the demo uses a `DEMO-UNPUBLISHED:` reference; full Member 2 H1-H11 validation needs their original input.
* Verified physical seat layouts (all demo seats stay `SYNTHETIC`).
* Live Neon read: needs the rotated password, the dedicated read-only role and your confirmations.
* Run / seating previews (sections 5-8) are per process; `MEMORY` drafts are lost on restart.
