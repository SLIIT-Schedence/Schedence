# Schedence

**Intelligent University Timetable Generation and Resource Management**

Schedence is a university scheduling platform designed to help institutions plan teaching timetables, examination timetables, examination duties, and venue reservations. The initial case study is the **Sri Lanka Institute of Information Technology (SLIIT)**.

The goal is to generate **feasible, practical timetables** that respect institutional policies, staff availability, student groups, venue capacities, and scheduling conflicts, while improving timetable quality through optimization.

> **Project status:** This README describes the **planned architecture and repository structure**. Creating the folders does not mean the services, endpoints, or infrastructure are implemented yet.
>
> **Implemented so far:** `packages/db-core`, `packages/auth-core`, the [Identity Service](services/identity-service/README.md) (Cognito authentication, institution registry, accounts, roles, permissions, System Admin bootstrap, audit log), and the administrative-positions part of the [Academic Data Service](services/academic-data-service/README.md) (position types, workload policies, staff appointments, workload contract). Other services are still planned.

## Technology stack (planned)

| Area               | Technology              | Purpose                                                      |
| ------------------ | ----------------------- | ------------------------------------------------------------ |
| Web frontend       | Next.js, TypeScript     | Role-based dashboards, calendars, and self-service portals   |
| UI                 | Tailwind CSS, shadcn/ui | Interface components and styling                             |
| Backend            | FastAPI (Python)        | Separately deployable APIs and business logic                |
| Optimization       | Google OR-Tools CP-SAT  | Constraint-based timetable and duty allocation               |
| Persistent storage | PostgreSQL              | Institutional, academic, scheduling, and reservation records |
| Database access    | SQLAlchemy, Alembic     | ORM and database migrations                                  |
| Background jobs    | Celery + Redis          | Run long timetable-generation jobs outside HTTP requests     |
| Authentication     | OIDC/JWT with RBAC      | Identity and scoped permissions                              |
| Local development  | Docker Compose          | Run services and dependencies locally                        |

## Repository structure

```text
schedence/
├── apps/
│   └── web/                        # Next.js web application
│
├── services/
│   ├── identity-service/           # Accounts, authentication, roles, permissions
│   ├── academic-data-service/      # Staff, modules, batches, registrations, policies
│   ├── resource-service/           # Venues, availability, booking, reservations
│   ├── teaching-service/           # Semester teaching timetable generation
│   ├── examination-service/        # Examination schedules, sessions, and duties
│   └── notification-service/       # Timetable publication and change alerts
│
├── packages/
│   ├── api-contracts/              # Shared API specifications and event contracts
│   ├── shared-schemas/             # Cross-service DTOs and validation conventions
│   ├── optimization-common/       # Reusable optimization utilities (not business rules)
│   └── db-core/                    # Shared async PostgreSQL (Neon) connection utilities
│
├── infrastructure/
│   ├── docker/                      # Dockerfiles and container configuration
│   └── migrations/                 # Infrastructure-level migration tooling, if needed
│
├── tests/
│   ├── integration/                # Cross-service tests
│   └── end-to-end/                 # Full user-workflow tests
│
├── .env.example                    # Environment template (placeholders only; real values go in .env)
├── docker-compose.yml              # Planned local service orchestration
└── README.md
```

_Each FastAPI service should keep its own application source, tests, dependencies, and service-specific database migrations inside its service folder. The top-level `infrastructure/migrations/` folder is reserved for shared infrastructure work, not for changing another service's tables._

## Service responsibilities

### 1. Identity Service (`services/identity-service/`)

Manages **who can access Schedence and what they are permitted to do**.

- User accounts, activation/deactivation, and sign-in integration.
- System roles, permissions, and access scopes (institution, campus, department, etc.).
- The institution registry, managed by System Admins (the Schedence product owners).
- Access for System Admin, Institution Admin, Academic Admin, Examination Admin, Venue Manager, lecturers, and students.
- Audit-relevant identity events.

**Important:** A staff member's **academic rank** (e.g., Professor) or **administrative appointment** (e.g., HOD or Program Coordinator) does **not** automatically grant additional system permissions. Appointments and workload policies belong to the Academic Data Service, while access permissions belong here.

**Status: implemented.** AWS Cognito (managed login, Authorization Code + PKCE), access-token verification via `packages/auth-core`, database-driven RBAC per institution, System Admin → Institution Admin → user onboarding with retryable Cognito/Neon provisioning, operator-only System Admin bootstrap, and audit logs. See [services/identity-service/README.md](services/identity-service/README.md).

### 2. Academic Data Service (`services/academic-data-service/`)

Owns academic master data needed by timetable generators.

- Institutions/campuses, faculties, departments, programs, academic years, and semesters.
- Modules, specializations, module offerings, and required teaching activities.
- Student batches, teaching groups, lab subgroups, and module registrations (including repeat students).
- Lecturer and instructor profiles, academic ranks, qualifications, and teaching assignments (e.g., LIC, Co-LIC, teaching team).
- Administrative appointments (e.g., HOD, Program Coordinator), appointment dates, and applicable workload policies.
- Teaching-hour targets, protected administrative-hour requirements, availability, and workday rules.
- Import validation for approved CSV/Excel academic datasets.

**Example:** If a lecturer has an HOD appointment, Schedence can reserve the required **administrative hours** as scheduling constraints without giving that person an admin login role.

**Status: partially implemented.** Administrative position types, versioned administrative-hour policies, minimal staff references, staff appointments and the administrative-workload API are in place. See [services/academic-data-service/README.md](services/academic-data-service/README.md). The rest of the academic master data is still planned.

### 3. Resource Service (`services/resource-service/`)

The **authoritative source for venue availability and reservations** shared by teaching, examinations, and ad-hoc bookings.

- Lecture halls, laboratories, venue type, capacity, and equipment.
- Effective laboratory capacity (e.g., number of working computers).
- Examination capacity policies (e.g., 50% of normal capacity where required).
- Venue unavailability, maintenance blocks, reservation requests, and booking approvals.
- Availability checks and **atomic** conflict-safe reservation confirmation.
- Reservation lifecycle and audit trail.

**Important:** Checking that a room is free is not sufficient; simultaneous requests must be prevented from booking the same venue/time range. Use database transactions and appropriate database constraints. Scheduling services must revalidate their proposed reservations when committing or publishing a timetable.

### 4. Teaching Service (`services/teaching-service/`)

Creates and manages **semester teaching timetables** for weekday and weekend streams using the academic data and resources supplied by other services.

- Build teaching session requirements (lectures, tutorials, workshops, labs).
- Enforce student-group, staff, room, equipment, and time conflicts.
- Enforce configured lecturer working-day and teaching-hour policies.
- Protect mandatory administrative periods for eligible staff.
- Respect weekend preferences and other approved soft constraints.
- Divide large cohorts into compatible teaching groups and lab subgroups.
- Allocate one-hour lunch breaks within permitted windows while balancing student crowd levels.
- Generate draft schedules, report violations/quality metrics, and support review and publication.
- Support controlled timetable repair when staff, rooms, or requirements change.

**Optimization engine:** OR-Tools CP-SAT, executed in background workers. A generated draft is not a confirmed venue booking until the Resource Service validates and commits the reservation.

### 5. Examination Service (`services/examination-service/`)

Creates **examination timetables**, allocates **examination sessions and venues**, and assigns **supervisors/invigilators**.

- Use actual student-module registrations to detect conflicts, including repeat students.
- Enforce examination duration, venue availability, seating limits, and mandatory student breaks.
- Assign one examination to multiple halls where permitted.
- Support multiple sessions for a module on the same day when institutional policy allows.
- Calculate required examination staff from configurable staffing rules.
- Allocate qualified, available staff without overlapping duties.
- Optimize duty fairness and student examination spacing.
- Provide draft approval, publishing, reporting, and emergency rescheduling workflows.

**Note:** Policies such as staff-to-student ratios and same-day multiple sittings must be confirmed with the university; example numbers should not be hardcoded as universal rules.

### 6. Notification Service (`services/notification-service/`)

Delivers communications after authorized scheduling events.

- Timetable-published alerts.
- Teaching session, examination, duty, and venue change notices.
- In-app notifications and future email delivery.
- Delivery status and retry handling.

This is a **planned service** that may initially remain inside another application module and be extracted once needed.

## Shared packages

| Package               | Responsibility                                                                                                                                         |
| --------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `api-contracts`       | OpenAPI specifications, request/response contracts, and event schemas so services integrate consistently.                                              |
| `shared-schemas`      | Narrow, stable DTOs, IDs, and validation conventions. Avoid sharing internal database models.                                                          |
| `optimization-common` | Reusable solver setup, time-slot helpers, objective reporting, and diagnostics. Keep teaching/examination business rules in their respective services. |
| `db-core`             | **Implemented.** Database configuration, async SQLAlchemy engines, per-request sessions, and health checks for Neon PostgreSQL. Owns no tables or models. See [Database infrastructure](#database-infrastructure-packagesdb-core). |
| `auth-core`           | **Implemented.** AWS Cognito access-token verification (JWKS cache with key rotation, issuer, expiry, `token_use`, `client_id`) and shared Cognito settings. No roles or permissions. See [packages/auth-core](packages/auth-core/README.md). |

## How the components communicate

```text
Students / Lecturers / University Admins
                 |
                 v
           Next.js Web App
                 |
                 v
          API Gateway / BFF
                 |
     +-----------+----------------------------+
     |           |              |             |
     v           v              v             v
 Identity    Academic Data    Teaching     Examination
 Service       Service        Service        Service
                              |               |
                              +-------+-------+
                                      |
                              Resource Service
                         (authoritative bookings)
                                      |
                            PostgreSQL (service-
                             owned data/schemas)

Teaching / Examination --> Celery + Redis --> CP-SAT workers
Published changes -------------------------> Notification Service
```

The diagram is **conceptual**, not a claim that a dedicated API Gateway is already running. The gateway/BFF can start as a lightweight routing layer. Internal API calls should authenticate and authorize requests; do not assume that traffic inside the network is trusted.

### Key architecture rules

1. **Each service owns its data.** Start with one PostgreSQL server if convenient, but isolate data ownership (e.g., separate schemas/databases and credentials). Do not let one service edit another service's tables directly.
2. **One reservation authority.** The Resource Service prevents room double-booking across teaching, examinations, and venue requests.
3. **Run optimization asynchronously.** FastAPI submits a job; Celery workers run CP-SAT; the frontend polls or subscribes for status/results.
4. **Validate before publication.** Draft timetables must pass an independent conflict check and receive appropriate admin approval.
5. **Distinguish identity from workload.** HOD and Program Coordinator are staff appointments, not automatically application-admin roles.
6. **Keep mandatory constraints mandatory.** If no valid timetable exists, report the reason instead of silently violating academic policy.
7. **Publish immutable versions.** Track policy version, input snapshot, solver settings, approvals, and later changes for auditability.

## System access roles

| User type                            | Main access                                                                                         |
| ------------------------------------ | --------------------------------------------------------------------------------------------------- |
| **System Admin (product owners)**    | Register institutions and create each institution's admin. No access inside an institution.         |
| **Institution Admin**                | Account/permission management, institutional setup and oversight within one institution.           |
| **Academic Admin**                   | Configure and review teaching schedules; approve/publish teaching timetables within assigned scope. |
| **Examination Admin**                | Configure, review and publish examination schedules and duty allocations.                           |
| **Venue Manager**                    | Manage venue availability, maintenance, and booking requests.                                       |
| **Lecturer / Instructor**            | View own teaching and examination duties; submit allowed preferences/requests.                      |
| **Student**                          | View own class, lab, and examination timetables.                                                    |

Actual permissions should be **scoped** and configurable. A person may have multiple permissions, but their academic appointment alone does not grant them.

## Example scheduling workflow

1. A System Admin registers the institution and its Institution Admin, who onboards the campus users; academic and venue owners maintain approved data.
2. Academic Admin or Examination Admin requests timetable generation.
3. The relevant service validates inputs and reads consistent staff, module, registration, and venue-availability data.
4. A background CP-SAT job finds a timetable satisfying all hard constraints and attempts to improve soft objectives.
5. The service saves a **draft**, including conflicts, solver status, objective metrics, and input/policy versions.
6. The responsible admin reviews the draft and requests publication.
7. Shared venue reservations are rechecked and committed atomically. If data changed meanwhile, publication is blocked or a repair is requested.
8. The published timetable is versioned; students, lecturers, and other authorized users can view updates.

## Development roadmap

- [ ] Scaffold Next.js in `apps/web/`.
- [ ] Scaffold FastAPI projects for the core services.
- [ ] Set up PostgreSQL, Redis, local Docker Compose, and service configuration.
- [x] Build identity, authorization (Cognito + RBAC), and administrator onboarding.
- [x] Build administrative positions, appointments, and administrative workload policies.
- [ ] Build campus onboarding and academic data imports.
- [ ] Build centralized venue availability and booking validation.
- [ ] Implement a small **teaching timetable MVP** with CP-SAT.
- [ ] Extend teaching constraints (weekend, administrative hours, lunch, lab groups).
- [ ] Implement examination timetabling and staff duty allocation.
- [ ] Add publishing, notifications, rescheduling, and benchmarking.

## Local development

**Prerequisites (planned):** Node.js, a compatible Python release for FastAPI/OR-Tools, Docker, and Docker Compose.

### Running the backend (all services, one command)

Each backend service is its own FastAPI application. Its entry point is `services/<name>/app/main.py` → `create_app()`, which uvicorn runs as a factory. The database is the existing Neon project, so there is nothing local to start. One launcher checks the database and starts every implemented service:

```powershell
# repository root, once:
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ./packages/db-core -e ./packages/auth-core
python -m pip install --group services/identity-service/pyproject.toml:dev --group services/academic-data-service/pyproject.toml:dev
python -m pip install -e "./services/examination-service[dev]"

# every time:
python scripts\run_services.py            # Ctrl+C stops everything
python scripts\run_services.py --reload   # auto-restart on code changes
```

| Service | URL | Swagger UI |
|---|---|---|
| Identity Service | http://localhost:8001 | http://localhost:8001/docs |
| Academic Data Service | http://localhost:8002 | http://localhost:8002/docs |
| Examination Service | http://localhost:8003 | http://localhost:8003/docs |
| Teaching Service | http://localhost:8004 | http://localhost:8004/docs |

The launcher works in five steps:
1. It wakes the Neon compute and checks the connection with `SELECT 1`. Credentials are never printed.
2. It reports each service's migration state. It **never applies migrations**; run `alembic upgrade head` in the service folder after review.
3. It starts the services with their log lines prefixed, `[identity]` / `[academic]` / `[examination]`. The Academic Data and Examination services are pointed at the Identity Service it started.
4. It waits until `/health` and `/health/db` answer.
5. It stops all services when you press Ctrl+C, or when one of them crashes.

Other options:
- `--only identity`: start one service only (repeatable, e.g. `--only identity --only examination`).
- `--smoke`: start everything, check health, then exit.
- `--identity-port` / `--academic-port` / `--examination-port`: use other ports.

The other planned services (resource, teaching, notification) and `docker-compose.yml` are not implemented yet. The Examination Service has its data, read API and seating settings; timetable generation is not implemented yet.

### Environment variables

Settings live in a git-ignored `.env` file at the repository root. [`.env.example`](.env.example) is the template; it contains placeholders only.

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }   # never overwrites an existing .env
```

Put real values only in `.env`: never in `.env.example`, source files, or commits. The syntax is `KEY=value`, with an equals sign.

### Python and the database

The shared database package is ready. See [Database infrastructure](#database-infrastructure-packagesdb-core) for setup, the connection check, and tests.

## Database infrastructure (`packages/db-core`)

`packages/db-core` (installed as `schedence-db-core`, imported as `schedence_db`) is the shared database infrastructure for the FastAPI services. It provides configuration, async SQLAlchemy engines, session lifecycles, and a health check for **Neon PostgreSQL**.

It is a **library**, not a service. It decides *how* services connect; it owns no tables, models, or migrations. Each service keeps its own models, schema, migrations, and data, and creates its own engine from its own configuration.

| Concern | Lives in |
|---|---|
| Reading and validating `DATABASE_URL` and pool settings | `packages/db-core` |
| Creating and disposing of the async engine | `packages/db-core` |
| One session per request, rollback on error | `packages/db-core` |
| `SELECT 1` health check | `packages/db-core` (the HTTP route lives in each service) |
| ORM models, `DeclarativeBase`, schema name | each service |
| Alembic migrations | each service |
| Credentials | `.env` (git-ignored) or the deployment's secret store |

**In this section:**
[Package layout](#package-layout) ·
[Quick start](#database-quick-start-powershell) ·
[Configuration](#database-configuration) ·
[Public API](#db-core-public-api) ·
[FastAPI usage](#using-db-core-in-a-fastapi-service) ·
[Connectivity check](#checking-database-connectivity) ·
[Neon pooled vs. direct](#neon-pooled-vs-direct-connections) ·
[SQLAlchemy and psycopg](#sqlalchemy-and-psycopg-configuration) ·
[Service-owned schemas](#service-owned-schemas) ·
[Migrations](#migrations-alembic-per-service) ·
[Depending on db-core](#depending-on-db-core) ·
[Tests](#database-tests) ·
[Troubleshooting](#database-troubleshooting) ·
[Security](#database-security)

### Package layout

```text
packages/db-core/
├── src/schedence_db/
│   ├── __init__.py          # public API re-exports
│   ├── config.py            # DatabaseSettings, load_settings(), URL normalisation, .env discovery
│   ├── connection.py        # create_db_engine(), dispose_engine()
│   ├── session.py           # create_session_factory(), session_scope(), session_dependency()
│   ├── health.py            # check_database(), DatabaseHealth
│   ├── exceptions.py        # DatabaseConfigurationError, describe_db_error()
│   ├── runtime.py           # run(): asyncio.run() on a selector loop on Windows
│   ├── check_connection.py  # CLI: python -m schedence_db.check_connection
│   └── py.typed
├── tests/                   # unit tests (no database needed) + opt-in live test
└── pyproject.toml           # hatchling build, dependencies, pytest config
```

The environment template is the repository-root [`.env.example`](.env.example).

Runtime dependencies: `sqlalchemy[asyncio]` 2.x, `psycopg[binary]` 3.x, `pydantic` 2.x, and `pydantic-settings` 2.x. Python 3.11+.

### Database quick start (PowerShell)

Run all commands from the **repository root**.

```powershell
# 1. Create and activate a virtual environment (once)
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
# If activation is blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

# 2. Install the package in editable mode (with test tools)
python -m pip install -e "./packages/db-core[dev]"

# 3. Create .env from the template if you don't have one, then set DATABASE_URL=...
if (-not (Test-Path .env)) { Copy-Item .env.example .env }

# 4. Check the connection
python -m schedence_db.check_connection
```

Expected output. Credentials, host names, and user names are never printed.

```text
Schedence Database Connection Test

Configuration: Loaded
Endpoint: pooled (DATABASE_URL)
Database: PostgreSQL 18.6
Connection: Successful
Query: SELECT 1
Latency: 5793.0 ms
Status: Healthy
```

The first connection from a developer machine can take several seconds. See [Latency](#latency).

### Database configuration

#### Variables

| Variable | Required | Default | Meaning |
|---|---|---|---|
| `DATABASE_URL` | **yes** | – | Neon **pooled** URL, used by services at runtime |
| `DIRECT_DATABASE_URL` | no | falls back to `DATABASE_URL` | Neon **direct** URL, for migrations and admin tasks |
| `APP_ENV` | no | `development` | `development`, `testing` or `production` |
| `DB_POOL_SIZE` | no | `5` | Connections kept open per process (1–50) |
| `DB_MAX_OVERFLOW` | no | `5` | Extra connections allowed under load (0–50) |
| `DB_POOL_TIMEOUT` | no | `30` | Seconds to wait for a free pooled connection |
| `DB_POOL_RECYCLE` | no | `1800` | Seconds before a pooled connection is replaced (`-1` = never) |
| `DB_POOL_PRE_PING` | no | `true` | Test a pooled connection before handing it out |
| `DB_USE_NULL_POOL` | no | `true` if `APP_ENV=testing`, else `false` | Disable client-side pooling |
| `DB_CONNECT_TIMEOUT` | no | `10` | Seconds to wait when opening a connection |
| `DB_ECHO` | no | `false` | Log SQL. **Rejected in production.** |
| `DB_APPLICATION_NAME` | no | – | Shown in `pg_stat_activity`, e.g. `identity-service` |
| `DB_DISABLE_PREPARED_STATEMENTS` | no | `false` | See [Prepared statements](#prepared-statements) |
| `SCHEDENCE_ENV_FILE` | no | – | Explicit path to the `.env` file to use |

`.env` syntax is `KEY=value`: an equals sign, not a colon. Quotes are optional. [`.env.example`](.env.example) is a complete template with placeholders.

```env
DATABASE_URL=postgresql://USER:PASSWORD@ep-EXAMPLE-123456-pooler.REGION.aws.neon.tech/DATABASE?sslmode=require&channel_binding=require
```

#### Where settings come from

From highest to lowest precedence:

1. keyword arguments: `load_settings(db_pool_size=2)`
2. process environment variables (how containers and CI provide settings)
3. a `.env` file
4. defaults

The `.env` file is chosen as follows. No path is hard-coded:

1. `load_settings(env_file=...)` if given (`env_file=False` reads only the environment);
2. otherwise `$SCHEDENCE_ENV_FILE` if set;
3. otherwise the **nearest** `.env` in the current directory or a parent, stopping at the repository root (the directory containing `.git`).

Because the nearest file wins, a service can keep its own `services/<name>/.env`. Run from that directory, it overrides the repository-root `.env`. Run from anywhere else in the repository, the root `.env` is used.

A service can also read prefixed variables so several services can share one file:

```python
settings = load_settings(env_prefix="IDENTITY_")  # IDENTITY_DATABASE_URL, IDENTITY_DB_POOL_SIZE, ...
```

#### URL handling

The URL is validated when settings load, so mistakes fail at startup rather than on the first query:

* `postgresql://` and `postgres://` are rewritten to `postgresql+psycopg://` (psycopg 3). Other drivers (`asyncpg`, `psycopg2`) and other databases are rejected.
* A host and database name are required.
* **TLS is never weakened.** For any non-local host, a missing `sslmode` becomes `sslmode=require`, and `sslmode=disable|allow|prefer` is rejected. Neon's `channel_binding=require` and any other query parameters are kept.
* `localhost` / `127.0.0.1` / `::1` may connect without TLS (for local Docker Postgres).
* Special characters in the password must be URL-encoded (`@` → `%40`, `:` → `%3A`, `/` → `%2F`, `%` → `%25`).

Validation errors name the variable but never echo its value.

### db-core public API

Everything is importable from `schedence_db`:

| API | Purpose |
|---|---|
| `load_settings(env_file=None, *, env_prefix="", **overrides)` | Load and validate `DatabaseSettings` |
| `DatabaseSettings` | Pydantic settings model. Secrets are `SecretStr`. `get_url(direct=False)` returns a SQLAlchemy `URL` |
| `create_db_engine(settings=None, *, direct=False, **engine_options)` | New `AsyncEngine` for this process |
| `dispose_engine(engine)` | Close the engine's connections. Never raises; accepts `None` |
| `create_session_factory(engine, **options)` | `async_sessionmaker[AsyncSession]` with `expire_on_commit=False` |
| `session_scope(factory, *, commit=True)` | Async context manager: commit on success, rollback on error, always close |
| `session_dependency(factory)` | FastAPI dependency: one session per request, rollback on error, no auto-commit |
| `check_database(target, *, timeout=10.0)` | `SELECT 1` on a session, connection, or engine, returning `DatabaseHealth` |
| `describe_db_error(exc)` | Safe, fixed description of a database error, suitable for logs and responses |
| `DatabaseConfigurationError`, `SchedenceDBError` | Exceptions |
| `schedence_db.runtime.run(coro)` | `asyncio.run()` that works with psycopg on Windows (for scripts) |

The engine factory is named `create_db_engine`, not `create_engine`, so it is not confused with SQLAlchemy's **synchronous** `sqlalchemy.create_engine`.

There is deliberately **no global engine or session**. Every call to `create_db_engine` returns a new, independent engine.

#### Scripts and background jobs

```python
from schedence_db import create_db_engine, create_session_factory, dispose_engine, load_settings, session_scope
from schedence_db.runtime import run


async def main() -> None:
    engine = create_db_engine(load_settings())
    factory = create_session_factory(engine)
    try:
        async with session_scope(factory) as session:  # commits on success, rolls back on error
            ...
    finally:
        await dispose_engine(engine)


run(main())
```

### Using db-core in a FastAPI service

This is an **example for future services**. Nothing here is implemented in `services/` yet. It was tested against the real Neon database with FastAPI 0.143 and uvicorn 0.54.

`services/identity-service/app/database.py`

```python
from schedence_db import create_db_engine, create_session_factory, load_settings, session_dependency

settings = load_settings()                         # this service's own configuration
engine = create_db_engine(settings)                # one engine per service process
session_factory = create_session_factory(engine)
get_session = session_dependency(session_factory)  # one session per request
```

Creating the engine opens no connection. The first query does.

`services/identity-service/app/main.py`

```python
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from schedence_db import check_database, dispose_engine

from app.database import engine, get_session


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await dispose_engine(engine)  # close pooled connections on shutdown


app = FastAPI(title="Identity Service", lifespan=lifespan)
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@app.get("/health/db")
async def database_health(session: SessionDep, response: Response) -> dict:
    health = await check_database(session)
    if not health.healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return health.to_dict()
```

Inside routes, **commit explicitly**:

```python
@app.post("/users")
async def create_user(payload: UserIn, session: SessionDep) -> UserOut:
    user = User(email=payload.email)
    session.add(user)
    await session.commit()  # a failed commit becomes an error response
    return UserOut.model_validate(user)
```

The dependency never auto-commits. If it committed after the route returned, a failed commit could happen after a success response had already been sent. Work that is not committed is discarded when the session closes, and an exception triggers a rollback.

#### Running on Windows

psycopg's async mode cannot use Windows' default `ProactorEventLoop`. Uvicorn uses that loop on Windows unless it starts a subprocess (`--reload` or `--workers`). Use the selector loop explicitly:

```powershell
uvicorn app.main:app --port 8001 --loop asyncio:SelectorEventLoop
```

Without that flag, `/health/db` answers `503` with `incompatible event loop: psycopg async needs a selector event loop on Windows`. Linux containers are unaffected.

For FastAPI tests on Windows, give the test client a selector loop:

```python
client = TestClient(app, backend_options={"loop_factory": asyncio.SelectorEventLoop})
```

With pytest-asyncio 1.4 or later, add the `pytest_asyncio_loop_factories` hook from [`packages/db-core/tests/conftest.py`](packages/db-core/tests/conftest.py).

### Checking database connectivity

```powershell
python -m schedence_db.check_connection                    # pooled DATABASE_URL
python -m schedence_db.check_connection --direct           # DIRECT_DATABASE_URL
python -m schedence_db.check_connection --env-file services\identity-service\.env
python -m schedence_db.check_connection --timeout 30
schedence-db-check                                         # same CLI, installed as a script
```

| Exit code | Meaning |
|---|---|
| `0` | Healthy |
| `1` | Configuration loaded, but the database did not answer `SELECT 1` |
| `2` | Configuration missing or invalid |

The check runs only `SELECT 1`, uses one connection without pooling, and disposes of the engine before exiting. On failure it prints a fixed reason, for example `authentication failed: check the database user name and password`, never the raw driver message.

In code, `check_database()` accepts an `AsyncSession`, an `AsyncConnection`, or an `AsyncEngine`. It returns `DatabaseHealth(healthy, latency_ms, error)` and never raises for database, network, or timeout errors.

### Neon: pooled vs. direct connections

Neon gives every branch two endpoints for the same database:

| | Pooled (`DATABASE_URL`) | Direct (`DIRECT_DATABASE_URL`) |
|---|---|---|
| Host | `ep-…-pooler.<region>.aws.neon.tech` | `ep-….<region>.aws.neon.tech` |
| What's behind it | PgBouncer in **transaction** mode | the Postgres compute itself |
| Connection limit | very high: Neon documents up to 10,000 clients | the compute's `max_connections`, which depends on compute size |
| Use for | service runtime traffic | migrations, `pg_dump`/restore, admin tasks |
| Session state | **not kept between transactions** | normal Postgres sessions |

In transaction mode, consecutive transactions on your connection may run on different server connections. Avoid anything that relies on session state: `SET` without `LOCAL` (including `SET search_path`), `LISTEN`/`NOTIFY`, session-level advisory locks, temporary tables used across transactions, and SQL-level `PREPARE`/`DEALLOCATE`.

How db-core applies this:

* Runtime engines use the pooled URL with a small client pool.
* `create_db_engine(settings, direct=True)` uses the direct URL, falling back to the pooled one if none is set, with `NullPool`. Admin tasks are short-lived and direct connections are scarce.
* Neon suspends idle computes (scale to zero, after 5 minutes by default), which closes their connections. `pool_pre_ping` detects such connections and replaces them transparently. A suspended compute takes a moment to wake, so the first query after idle time is slower.

#### Latency

These measurements come from the development machine on which db-core was built. A network round trip to the Neon region took about 320 ms. A new connection (TCP + TLS + SCRAM with channel binding) took ~2.4 s. The first engine-based health check took ~5 s, which includes SQLAlchemy's one-time dialect initialisation queries. Checks on a reused pooled connection took ~1 s.

These numbers depend on network distance, not on the package. Deploy services in the same AWS region as the Neon project, where a round trip takes milliseconds, and keep health-check timeouts at 10 s or more for local development.

### SQLAlchemy and psycopg configuration

| Choice | Why |
|---|---|
| **Async** (`AsyncEngine`, `AsyncSession`) | FastAPI is async. A blocking driver would stall the event loop under I/O |
| **psycopg 3** via `postgresql+psycopg://` | One driver for both the async runtime **and** sync tooling such as Alembic. `psycopg[binary]` bundles libpq, so `channel_binding` works on Windows without a local PostgreSQL install |
| `pool_size=5`, `max_overflow=5` | At most 10 connections per process |
| `pool_pre_ping=True` | Neon closes connections when computes suspend. Costs one round trip per checkout |
| `pool_use_lifo=True` | Reuses the most recent connection, so surplus ones go idle and get recycled |
| `pool_recycle=1800` | Replaces long-lived connections |
| `connect_timeout=10` | Covers TLS setup plus a compute waking up |
| `expire_on_commit=False` | Async sessions cannot lazy-load expired attributes after `commit()` |
| `NullPool` when `APP_ENV=testing` | Each test gets fresh connections, never shared across event loops |

#### Connection budget

Each process opens at most `DB_POOL_SIZE + DB_MAX_OVERFLOW` connections, 10 by default. Across the system:

```text
total client connections ≤ Σ over services ( processes × (DB_POOL_SIZE + DB_MAX_OVERFLOW) )
```

For example, 6 services × 2 uvicorn workers × 10 = 120 client connections. That is comfortable for the pooled endpoint but would exhaust a small compute's `max_connections` on the direct endpoint. Use the pooled URL for services, and lower `DB_POOL_SIZE` before adding workers.

#### Prepared statements

psycopg prepares frequently repeated statements at the protocol level, which current PgBouncer versions, including Neon's pooler, support. If you ever see `prepared statement "_pg3_…" does not exist` through a pooler, set `DB_DISABLE_PREPARED_STATEMENTS=true`. This sets psycopg's `prepare_threshold=None`.

### Service-owned schemas

One Neon project, one database, **one schema per service**:

```text
neondb
├── identity      ← identity-service only
├── academic      ← academic-data-service only
├── resources     ← resource-service only
├── teaching      ← teaching-service only
└── examinations  ← examination-service only
```

#### 1. Each service declares its own base and schema

```python
# services/identity-service/app/models.py
from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    metadata = MetaData(schema="identity")  # every table becomes identity.<table>
```

Explicit schema qualification is **pool-safe**: every statement names its schema, so nothing depends on `search_path`. Never run `SET search_path` at runtime. Through a connection pool it can leak into other requests, and through Neon's transaction-mode pooler it may silently not apply.

`db-core` contains no `DeclarativeBase` and no models. Services never import each other's models.

#### 2. Each service gets its own role (recommended before production)

During early development, every service may use the owner role's URL. Before production, give each service a role that can reach only its own schema.

1. In the **Neon Console → Roles**, create `identity_service`. Neon generates a strong password.
2. As the database owner, review and run this in the Neon SQL Editor:

```sql
-- Template: run manually after review. db-core never executes this.
CREATE SCHEMA IF NOT EXISTS identity AUTHORIZATION identity_service;
REVOKE ALL ON SCHEMA identity FROM PUBLIC;
```

3. Put that role's pooled URL in the identity service's own `DATABASE_URL`, either in `services/identity-service/.env` or in the deployment's secret store.

The role owns its schema, so its migrations can create tables there. Without `USAGE` on other schemas, it **cannot read or write other services' tables**; the database enforces this. A service that needs another service's data calls that service's API or consumes its events instead of querying its tables.

A pool-safe alternative to qualifying schemas in code is a role-level default: `ALTER ROLE identity_service SET search_path = identity`. Postgres applies it when each connection for that role starts. Explicit `MetaData(schema=...)` remains the clearer default.

### Migrations (Alembic, per service)

Migrations belong to the service that owns the schema. `db-core` only supplies the URL.

```text
services/identity-service/
├── alembic.ini
├── alembic/
│   ├── env.py
│   └── versions/
└── app/models.py
```

`services/identity-service/alembic/env.py` (core of the file):

```python
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from schedence_db import load_settings

from app.models import Base

SCHEMA = "identity"
config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
url = load_settings().get_url(direct=True)  # DIRECT_DATABASE_URL, else DATABASE_URL


def include_name(name, type_, parent_names):
    # Autogenerate only ever looks at this service's own schema.
    return name == SCHEMA if type_ == "schema" else True


migration_options = dict(
    target_metadata=target_metadata,
    version_table_schema=SCHEMA,  # alembic_version lives in identity, not public
    include_schemas=True,
    include_name=include_name,
)


def run_migrations_offline() -> None:
    context.configure(url=url, literal_binds=True, **migration_options)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # Synchronous psycopg engine: migrations are short scripts, and sync mode
    # needs no event-loop changes on Windows.
    engine = create_engine(url, poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, **migration_options)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

Notes:

* The URL is passed as a SQLAlchemy `URL` object and never written into `alembic.ini`. This keeps the password out of files, and keeps `%` in passwords away from ConfigParser interpolation.
* `version_table_schema` gives every service its own `alembic_version` table, so revision histories never collide.
* `include_name` stops autogenerate from proposing changes to other services' schemas.
* The schema must exist before the first migration (step 2 above), because Alembic creates its version table inside it.
* Review generated migrations before applying them. Preview the SQL first with `alembic upgrade head --sql`.

### Depending on db-core

#### In local development

Install `db-core` once into the virtual environment you run services from, then install the service:

```powershell
python -m pip install -e ./packages/db-core
python -m pip install -e ./services/identity-service   # once that service has a pyproject.toml
```

With an editable install, changes to `packages/db-core/src` take effect without reinstalling. A service lists `schedence-db-core` as a dependency by name; pip sees it is already installed and does not download anything.

#### In containers

Build from the repository root so the image can copy the shared package. Install it as a regular, non-editable package, then install the service:

```dockerfile
# services/identity-service/Dockerfile (sketch). Build with:
#   docker build -f services/identity-service/Dockerfile -t schedence-identity .
FROM python:3.11-slim
WORKDIR /app
COPY packages/db-core /tmp/db-core
RUN pip install --no-cache-dir /tmp/db-core && rm -rf /tmp/db-core
COPY services/identity-service/ /app/
RUN pip install --no-cache-dir -r requirements.txt
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Provide `DATABASE_URL` and the other settings as **environment variables** from the platform's secret store. Never copy a `.env` file into an image. A Linux container needs no `--loop` flag.

`schedence-db-core` is not published on PyPI. Always install it from this repository **before** the service. Otherwise pip could resolve the name from the public index, where anyone could publish a package with that name.

### Database tests

```powershell
python -m pytest packages/db-core            # unit tests: no database, no credentials needed
python -m pytest packages/db-core --live     # also run SELECT 1 against the configured database
```

* **Unit tests** run in a temporary directory with every `DATABASE_URL` / `DB_*` variable removed, so they never read your real `.env`. They use fake credentials and assert that those never appear in errors, logs, `repr()`, or CLI output. Some tests open real sockets to a closed local port to exercise psycopg's real failure handling.
* **The live test** is skipped unless you pass `--live` (or set `$env:SCHEDENCE_DB_LIVE_TESTS = "1"`). It loads the normal configuration and runs only `SELECT 1`, plus a direct-URL variant when `DIRECT_DATABASE_URL` is set. It modifies nothing.

### Database troubleshooting

| Symptom | Cause / fix |
|---|---|
| `DATABASE_URL is required but was not set` | The variable isn't in the environment or in the `.env` that was found. The message names the file it read. Check the `KEY=value` syntax: a `Hint:` line flags `DATABASE_URL:` written with a colon |
| `Environment file not found` | `--env-file` / `SCHEDENCE_ENV_FILE` points to a missing file |
| `... is not UTF-8 encoded` | Windows PowerShell 5.1's `>` writes UTF-16. Re-save the file as UTF-8 in VS Code (status bar → encoding) |
| `DATABASE_URL is not a valid database URL` | Copy the URL again from Neon. URL-encode special characters in the password |
| `uses sslmode=disable ...` | TLS is mandatory for remote hosts. Use `sslmode=require` |
| `authentication failed` | Wrong user or password, or the password was reset. Copy a fresh URL from Neon Console → Connect |
| `database host could not be resolved` | Typo in the host name, or DNS/network problems (VPN, captive portal) |
| `connection timed out` | Outbound port **5432** is blocked (common on campus or corporate networks; try another network), or the compute is waking up (retry, or raise `DB_CONNECT_TIMEOUT`) |
| `Neon endpoint error` | The endpoint ID in the host is wrong, or the compute/branch is disabled |
| `SSL/TLS negotiation failed` | Keep `sslmode=require`. Update psycopg (`python -m pip install -U "psycopg[binary]"`) |
| `too many connections` | Use the **pooled** URL for services. Lower `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` |
| `incompatible event loop` | Windows Proactor loop. See [Running on Windows](#running-on-windows) |
| `prepared statement "_pg3_…" does not exist` | Set `DB_DISABLE_PREPARED_STATEMENTS=true` |
| `pg_stat_activity` doesn't show which service is connected | Set `DB_APPLICATION_NAME` per service |

### Database security

* **Secrets stay out of Git.** The repository-root `.gitignore` excludes `.env` and `.env.*` everywhere but keeps `.env.example`. Check before committing: `git check-ignore -v .env`.
* **Secrets stay out of output.** Settings hold URLs as `SecretStr`. Validation errors are rebuilt from field names only: input values are stripped and the original exception is suppressed. Driver errors are reduced to fixed descriptions by `describe_db_error()`, and the `repr()` of a SQLAlchemy `URL` or engine masks the password. Unit tests assert all of this.
* **TLS is enforced** for every non-local host. `channel_binding=require` is preserved; SCRAM channel binding protects against man-in-the-middle attacks. For maximum assurance use `sslmode=verify-full`, which also verifies the server certificate and host name.
* **`DB_ECHO` is refused in production.** SQL logs can contain personal data.
* **Least privilege**: give each service its own role, limited to its own schema (see [Service-owned schemas](#service-owned-schemas)). Keep the owner role for administration and migrations only.
* **Don't type secrets into the terminal.** PowerShell saves command history to disk (`(Get-PSReadLineOption).HistorySavePath`). Keep secrets in `.env` locally and in a secret store (for example AWS Secrets Manager) in deployments.
* **If a credential is ever committed**: first reset the role's password in the Neon Console; rotating the password is the real fix. Then remove the file from Git (`git rm --cached .env`). If it was pushed, rewrite history with `git filter-repo` and force-push after coordinating with the team.

## Project principles

- University policies are **configurable**, documented, and versioned.
- Hard constraints (no clashes, adequate capacity, valid staff assignment) cannot be traded for convenience.
- Soft objectives (preferences, fairness, utilization, and minimal disruption) improve schedule quality.
- Human administrators retain review and approval authority.
- Student and staff information must be accessed only by authorized roles and handled with appropriate privacy controls.

---

**Schedence — Smarter scheduling for complex university operations.**
