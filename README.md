# Schedence

**Intelligent University Timetable Generation and Resource Management**

Schedence is a university scheduling platform designed to help institutions plan teaching timetables, examination timetables, examination duties, and venue reservations. The initial case study is the **Sri Lanka Institute of Information Technology (SLIIT)**.

The goal is to generate **feasible, practical timetables** that respect institutional policies, staff availability, student groups, venue capacities, and scheduling conflicts, while improving timetable quality through optimization.

> **Project status:** This README describes the **planned architecture and repository structure**. Creating the folders does not mean the services, endpoints, or infrastructure are implemented yet.

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
│   └── optimization-common/       # Reusable optimization utilities (not business rules)
│
├── infrastructure/
│   ├── docker/                      # Dockerfiles and container configuration
│   └── migrations/                 # Infrastructure-level migration tooling, if needed
│
├── tests/
│   ├── integration/                # Cross-service tests
│   └── end-to-end/                 # Full user-workflow tests
│
├── docker-compose.yml              # Planned local service orchestration
└── README.md
```

_Each FastAPI service should keep its own application source, tests, dependencies, and service-specific database migrations inside its service folder. The top-level `infrastructure/migrations/` folder is reserved for shared infrastructure work, not for changing another service's tables._

## Service responsibilities

### 1. Identity Service (`services/identity-service/`)

Manages **who can access Schedence and what they are permitted to do**.

- User accounts, activation/deactivation, and sign-in integration.
- System roles, permissions, and access scopes (institution, campus, department, etc.).
- Access for Super Admin, Academic Admin, Examination Admin, Venue Manager, lecturers, and students.
- Audit-relevant identity events.

**Important:** A staff member's **academic rank** (e.g., Professor) or **administrative appointment** (e.g., HOD or Program Coordinator) does **not** automatically grant additional system permissions. Appointments and workload policies belong to the Academic Data Service, while access permissions belong here.

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
| **Super Admin (Non-Academic Admin)** | Campus onboarding, account/permission management, institutional setup and oversight.                |
| **Academic Admin**                   | Configure and review teaching schedules; approve/publish teaching timetables within assigned scope. |
| **Examination Admin**                | Configure, review and publish examination schedules and duty allocations.                           |
| **Venue Manager**                    | Manage venue availability, maintenance, and booking requests.                                       |
| **Lecturer / Instructor**            | View own teaching and examination duties; submit allowed preferences/requests.                      |
| **Student**                          | View own class, lab, and examination timetables.                                                    |

Actual permissions should be **scoped** and configurable. A person may have multiple permissions, but their academic appointment alone does not grant them.

## Example scheduling workflow

1. Super Admin onboards a campus and its users; academic and venue owners maintain approved data.
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
- [ ] Build identity, authorization, and campus onboarding.
- [ ] Build academic data imports and workload policies.
- [ ] Build centralized venue availability and booking validation.
- [ ] Implement a small **teaching timetable MVP** with CP-SAT.
- [ ] Extend teaching constraints (weekend, administrative hours, lunch, lab groups).
- [ ] Implement examination timetabling and staff duty allocation.
- [ ] Add publishing, notifications, rescheduling, and benchmarking.

## Local development

**Prerequisites (planned):** Node.js, a compatible Python release for FastAPI/OR-Tools, Docker, and Docker Compose.

The repository currently contains an **architecture scaffold**. Once the Next.js app, FastAPI apps, and `docker-compose.yml` are configured, add the exact installation, environment-variable, and startup commands here. Do not assume `docker compose up` will work before those files are implemented.

## Project principles

- University policies are **configurable**, documented, and versioned.
- Hard constraints (no clashes, adequate capacity, valid staff assignment) cannot be traded for convenience.
- Soft objectives (preferences, fairness, utilization, and minimal disruption) improve schedule quality.
- Human administrators retain review and approval authority.
- Student and staff information must be accessed only by authorized roles and handled with appropriate privacy controls.

---

**Schedence — Smarter scheduling for complex university operations.**
