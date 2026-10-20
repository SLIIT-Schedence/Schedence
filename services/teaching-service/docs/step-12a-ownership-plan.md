# Step 12A: service ownership and proposed persistence

This is a proposal only. No DDL, migration, connection, data write or remote
schema inspection was performed. The review used repository models, contracts
and migration declarations; it does not assert which migrations are deployed.

## Current service boundaries

| Owner | Current declared implementation and integration boundary |
| --- | --- |
| Academic Data Service | `app/models/entities.py`, schema `academic`: `staff`, `administrative_position_types`, `administrative_workload_policies`, `staff_administrative_appointments`, `appointment_audit_logs`; initial migration `20261013_0001_academic_initial.py`. Staff UUIDs, institution-scoped staff numbers, dated administrative policies/appointments and upstream aggregate workload response are implemented. Full offering/group/population/demand masters remain future exports, not Teaching-owned copies. |
| Identity Service | Users, roles, permissions, role permissions, institution-scoped user roles, provisioning and identity audit records. `teaching:generate` and `teaching:approve` already exist. Access roles are separate from academic ranks and HOD/Dean appointments. |
| Resource Service | Expected owner of authoritative venue inventory, calendars, shared/combined-space relationships and booking state. The local 62-venue workbook is confirmed development inventory; no live authoritative booking integration is claimed. |
| Teaching Service | Validated input snapshots, generation configuration, solver runs, draft versions, physical allocations, reports and review/publication history. It does not become the academic staff/appointment/identity master. |
| `packages/db-core` | Shared configuration, per-service async engine/session utilities and connection checks; not ownership of domain entities or academic records. |

Academic `staff.id` is a UUID, while existing Teaching `staff_id` is a preserved
staff number such as LEC001. Academic rank is optional/informational in the
current minimal ADS contract; typed staff category and department need explicit
supplemental authoritative data. `identity_user_id` is optional and opaque:
staff can exist without an Identity login. Institution IDs are currently opaque
references in these service models, not a new institution registry owned here.

ADS administrative policies permit versioned effective dates, explicit overrides
and multiple appointments. Consume its resolved aggregate, including
`NOT_COMPUTED`; do not reimplement that policy or infer hours from RBAC roles.
The daily `/administrative-workload` response needs explicit dated upstream
evidence before full-week integration. The adapter preserves its daily scope.

Identity's ACADEMIC_ADMIN currently has teaching generation/approval permissions.
Use explicit permission and institution checks in a future authenticated API;
do not assume INSTITUTION_ADMIN, SYSTEM_ADMIN or an academic rank implies every
teaching permission.
No Identity roles or Academic Data Service contracts were changed in Step 12A.

`db-core.session_scope` defaults to commit; `commit=False` alone is not a
database-enforced read-only transaction. A future authorized live audit should
set transaction READ ONLY explicitly. This step does not instantiate an engine,
read `.env`, execute queries or open database sessions.

## Proposed Teaching-owned tables (not implemented)

| Proposed table | Main data / relationship |
| --- | --- |
| `teaching.generation_requests` | `(institution_id, request_id)`, term/week/scope, requester Identity user reference, research configuration and requested status |
| `teaching.input_snapshots` | Immutable `(institution_id, snapshot_id)`, request reference, schema version, canonical payload/content digest, source versions/checksums, provenance and readiness report; restricted access/retention for personal data |
| `teaching.solver_executions` | `(institution_id, execution_id)`, snapshot/config references, solver/version/seed/limits, objective order/stage proof statuses, timings, failures and software version |
| `teaching.timetable_versions` | `(institution_id, version_id)`, request/execution/snapshot references, monotonic request version, draft state and prior-version reference |
| `teaching.delivery_allocations` | One row per actual physical delivery: version, date/start/end, stream/content metadata, preserved staff/venue references; unique `(institution_id, version_id, delivery_id)` |
| `teaching.delivery_coverage` | Allocation → original occurrence/requirement/offering/group snapshot references; unique `(institution_id, version_id, occurrence_id)` prevents duplicate fulfillment. A combined lecture has several coverage rows and one physical booking/workload occurrence. |
| `teaching.validation_results` | Immutable version/snapshot/config references, independent validator version, complete hard checks, policy/coverage reports, timestamps and outcome; never treat solver status alone as validation |
| `teaching.timetable_state_events` | Append-only review/approval/publication/rejection/supersession transitions, institution-scoped Identity actor reference, approval evidence, reason and validated snapshot/version |

Teaching-local references should use composite foreign keys including
`institution_id`, and parent tables should expose matching composite unique
keys. Scope every query, key, cache and export by institution. Use suitable
database row isolation in addition to service authorization when persistence is
implemented; neither is present in this pure-Python foundation.

Academic staff UUIDs/staff numbers, offering/group/requirement IDs and appointment
policy references are immutable **external references plus source versions**
inside snapshots. Resource venue IDs/booking references and Identity user IDs
are likewise opaque references. Do not add cascading cross-service FKs or let
Teaching edit/delete their masters. Existing internal ADS appointment foreign
keys remain ADS-owned. Do not use module codes, source-row diagnostics or
generated synthetic IDs as authoritative external keys.

Lifecycle proposal: DRAFT → UNDER_REVIEW → APPROVED → PUBLISHED, with explicit
REJECTED/SUPERSEDED history rather than destructive overwrites. Publication
requires the relevant existing Identity permission, current independent
validation, approved policies, source-version checks and Resource Service
availability/atomic booking confirmation. A solved draft is not a reservation;
stale availability requires revalidation. Review approval and booking semantics
need agreement before any implementation.

Teaching/admin/research amounts and completed work should remain distinct in
stored reports. Do not store inferred attendance, treat reserved admin minutes
as completed work, or claim unmet teaching targets are fulfilled. A published
calendar should reference a validated immutable version, never mutable source
Excel or an incomplete staging export.
