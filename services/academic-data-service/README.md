# Academic Data Service: administrative positions and appointments

In this implementation the Academic Data Service covers **only**:

- configurable **administrative position types** (Head of Department, Program Coordinator, Dean, ...);
- versioned **administrative-hour policies** for those positions;
- a **minimal staff reference** (enough to attach appointments; not a full HR or academic registry);
- **staff appointments** with effective dates and full history;
- the **administrative-workload contract** that future timetable services will read.

### Appointments are not login roles

```text
Staff member:            Example Lecturer
Academic rank:           Senior Lecturer               (stored here, informational)
Academic appointment:    Program Coordinator, BSC_IT   (stored here: responsibility + protected hours)
Schedence login role:    LECTURER                      (Identity Service only)
```

- Appointing someone HOD does **not** grant INSTITUTION_ADMIN.
- Appointing someone Program Coordinator does **not** grant ACADEMIC_ADMIN.
- This service never writes to the Identity Service and stores no system roles.
- A staff member may have **no Schedence login at all**: `identity_user_id` is optional.

Tests prove both independence points.

## Data model (`academic` schema)

| Table | Purpose |
|---|---|
| `staff` | `institution_id`, unique `staff_number` per institution, name, optional e-mail, academic rank, employment status, optional `identity_user_id` (opaque, no FK) |
| `administrative_position_types` | Per-institution definition: `code` (e.g. `PROGRAM_COORDINATOR`), name, description, `unit_type` (DEPARTMENT, PROGRAM, FACULTY, INSTITUTION, OTHER), `is_exclusive_per_unit` (e.g. one HOD per department), `is_active` |
| `administrative_workload_policies` | Versioned hours per position: `admin_hours_per_week`, `combination_rule` (UNSPECIFIED, ADDITIVE, CAPPED + `cap_hours_per_week`, REPLACEMENT), `policy_reference`, `effective_from`/`effective_until` |
| `staff_administrative_appointments` | Staff × position × unit, `effective_from`/`effective_until` (inclusive), optional `admin_hours_override` + mandatory reason, end details |
| `appointment_audit_logs` | Who did what to staff, positions, policies and appointments |

**No hour value is built in.** The 6 hours used for Program Coordinator in the tests is only an example; every position's hours come from a policy record entered by an administrator.

Changing hours adds a **new policy version** and closes the previous one the day before, so the policy in force on any past date can be reconstructed.

## Rules

- **Valid staff.** The staff member must exist in the same institution and be ACTIVE.
- **Valid position.** The position must belong to that institution and be active. Its unit type decides whether `unit_code` is required.
- **Valid dates.** `effective_until` cannot be before `effective_from`.
- **Policy in force.** A policy must be in effect on the start date unless an hours override (with a reason) is given.
- **No duplicates.** The same staff member, position and unit cannot overlap in time.
- **Exclusive positions.** For positions marked `is_exclusive_per_unit`, no other staff member can hold the same unit at an overlapping time.
- **Locking.** Checks run under row locks (`SELECT ... FOR UPDATE` on the staff and position rows), so concurrent requests cannot both pass. No database extension is needed.
- **Multiple appointments.** A person may hold several different appointments.
- **Ending appointments.** Ending sets `effective_until`, `ended_at` and `end_reason`. Ended appointments are read-only.
- **What can be changed.** Unit name, effective-until, override + reason, and notes. To change staff, position, unit or start date, end the appointment and create a new one.
- **Disabling positions.** A position type can only be disabled when it has no current or scheduled appointments.

## Administrative workload contract

`GET /api/v1/academic/staff/{staff_id}/administrative-workload?as_of=2026-11-01`, with permission `academic:workload:read`.

```json
{
  "staff_id": "…", "institution_id": "…", "as_of": "2026-11-01",
  "appointments": [
    {
      "appointment_id": "…", "position_type_id": "…", "position_code": "HEAD_OF_DEPARTMENT", "position_name": "Head of Department",
      "unit_type": "DEPARTMENT", "unit_code": "COMPUTING", "unit_name": "Department of Computing",
      "effective_from": "2026-11-01", "effective_until": null,
      "required_admin_hours_per_week": 10.0, "hours_source": "POLICY",
      "policy": {"policy_id": "…", "version": 1, "combination_rule": "UNSPECIFIED", "cap_hours_per_week": null,
                 "policy_reference": "…", "effective_from": "2026-01-01", "effective_until": null}
    }
  ],
  "aggregate": {"status": "COMPUTED", "rule": "SINGLE_APPOINTMENT", "total_admin_hours_per_week": 10.0, "reason": null}
}
```

**When the aggregate is computed:**
- With zero or one active appointment.
- With several, only if **every** policy declares the same rule:
  - ADDITIVE: the sum
  - CAPPED: the sum limited by a shared cap
  - REPLACEMENT: the largest single requirement
- Otherwise `status` is `NOT_COMPUTED` with a reason. A scheduler must then use the per-appointment figures. The service never guesses how unresolved policies combine.

Future scheduling services will call this endpoint with a user token today. Machine-to-machine access (Cognito client-credentials with a custom scope) can be added without changing the response shape.

## Authorisation (no shared database)

1. The Cognito **access token** is verified locally with `packages/auth-core` (same checks as the Identity Service).
2. Permissions come from the Identity Service: `GET {IDENTITY_SERVICE_URL}/api/v1/identity/me/permissions` with the caller's own token. The returned `cognito_sub` must match the verified token.
3. Reads may reuse that answer for `IDENTITY_PERMISSIONS_CACHE_SECONDS` (default 15). The cache is keyed by a SHA-256 of the token. Writes always re-check.
4. The service fails closed: if the Identity Service is unreachable, the response is 503.
5. Every resource is checked against the caller's permissions **in that resource's institution**. Outside your institutions, resources answer 404.

Frontend role headers are never read. Outside local development, use HTTPS or mTLS between services.

## API

Base path `/api/v1/academic`.

| Method & path | Permission |
|---|---|
| `POST /positions` (with first policy) | `academic:positions:create` |
| `GET /positions`, `GET /positions/{id}` (with policy history) | `academic:positions:read` |
| `PATCH /positions/{id}` | `academic:positions:update` |
| `POST /positions/{id}/workload-policies` (new hours version), `GET` history | `academic:positions:update` / `:read` |
| `POST /positions/{id}/disable`, `/enable` | `academic:positions:disable` |
| `POST /staff`, `GET /staff`, `GET /staff/{id}` | `academic:staff:create` / `academic:staff:read` |
| `POST /staff/{staff_id}/appointments` | `academic:appointments:assign` |
| `GET /staff/{staff_id}/appointments?include_ended=true` | `academic:appointments:read` |
| `GET /appointments/{id}` (with audit trail) | `academic:appointments:read` |
| `PATCH /appointments/{id}` | `academic:appointments:update` |
| `POST /appointments/{id}/end` | `academic:appointments:end` |
| `GET /staff/{staff_id}/administrative-workload` | `academic:workload:read` |
| `GET /health`, `GET /health/db` | none |

INSTITUTION_ADMIN holds all of these. ACADEMIC_ADMIN and EXAMINATION_ADMIN can read workload. SYSTEM_ADMIN (platform) holds none: it has no access here.

## Running (PowerShell)

```powershell
# repository root
python -m pip install -e ./packages/db-core -e ./packages/auth-core
python -m pip install --group services/academic-data-service/pyproject.toml:dev

cd services\academic-data-service
python -m pytest
alembic upgrade head --sql                  # review first; apply with `alembic upgrade head` after approval
uvicorn app.main:create_app --factory --port 8002 --loop asyncio:SelectorEventLoop
```

The Identity Service must be running (default `IDENTITY_SERVICE_URL=http://localhost:8001`). It uses the same root `.env` Cognito variables.

| Variable | Default | Meaning |
|---|---|---|
| `IDENTITY_SERVICE_URL` | `http://localhost:8001` | Where permissions are resolved |
| `IDENTITY_PERMISSIONS_CACHE_SECONDS` | `15` | Read-only permission cache (0 disables) |
| `INSTITUTION_TIMEZONE` | `Asia/Colombo` | Defines "today" for effective dates |

## Tests

All tests are **mocked**:
- in-memory repositories
- a fake Identity Service behind `httpx.MockTransport`, used with the real permissions client
- locally signed JWTs

No database or AWS is touched. Coverage:
- HOD and Program Coordinator creation
- configurable hours with version history
- appointment creation
- invalid staff
- duplicate and exclusive conflicts
- date validation
- termination and history
- institution scope
- HOD ≠ INSTITUTION_ADMIN and PC ≠ ACADEMIC_ADMIN
- staff without logins
- workload retrieval and aggregation rules
- Identity Service outage, disabled accounts, permission caching
- migration/model consistency
