# Staff duty allocation on the existing Neon database

This is the selected setup: **use the existing Neon database**, with no new branch or local database.
The root `.env` already holds its pooled `DATABASE_URL` and direct `DIRECT_DATABASE_URL`.
The launcher uses those settings without modifying or copying the credentials.

## Start the component

From PowerShell in `C:\Users\Hp\Desktop\Schedence`:

```powershell
.\.venv\Scripts\python.exe scripts/run_duty_allocation.py check
.\.venv\Scripts\python.exe scripts/run_duty_allocation.py
```

The first command checks Neon without changing anything. Expected output: shared examination migration
**0012**, component migration **0013**, no missing input tables, and no missing component tables.
The second starts the Examination Service on **http://localhost:8003** with `NEON_SHARED` inputs
and `DATABASE` draft persistence. The solver runs in Python on your computer; input data and
saved drafts are stored in Neon. This does not start a local database.

Use your normal Identity Service and frontend processes, sign in as Examination Admin, and open
**http://localhost:3000/exams/duties**. The source should say **Neon database**, and saved drafts should
say **Database**. Do not also launch another Examination Service on port 8003.

If generation says permissions cannot be verified, check that the Identity Service on port 8001
is running and inspect the Examination Service log. Permission lookups have a bounded 20-second
timeout by default to allow a cold Neon connection; `IDENTITY_TIMEOUT_SECONDS` can override it
(maximum 30 seconds). Every allocation/save request still checks current permissions and refuses
the action if Identity cannot verify them. Restart the Examination Service after configuration changes.

## Component tables and teammate protection

Inputs in `m3_demo` are read through a **read-only REPEATABLE READ** snapshot and never updated.
Generated previews stay in memory until explicitly saved. Saved drafts use only these component-owned
tables in `examinations`:

- `duty_rosters`
- `duty_roster_assignments`
- `duty_roster_events`
- `duty_change_requests`
- `seating_drafts`
- `seating_draft_seats`

The component also owns `examinations.duty_allocation_alembic_version`. Its migration does not update
the team's `examinations.alembic_version`, change existing tables, insert demo inputs, or apply teammates'
migrations. A SQL guard rejects migration writes outside these seven objects. The migration uses one
transaction and refuses to take over tables in an unexpected state. Repeating installation is read-only
when the component is already installed.

For another environment that has not installed these tables, preview and apply the component migration:

```powershell
.\.venv\Scripts\python.exe scripts/run_duty_allocation.py sql
.\.venv\Scripts\python.exe scripts/run_duty_allocation.py migrate
```

Only `migrate` changes schema. It creates only the component's objects. Use the `sql` command above to review the exact table/index definitions. Do not run generic shared migrations
to install this component.

## See and test the results

In Neon Console, keep the existing branch and `neondb` selected. Choose `m3_demo` for input tables,
or **`examinations`** for the new component tables. New roster tables are empty until you save a draft.

Generate an allocation, inspect its fairness and validation, and click **Save draft**. Restart the
Examination Service with the same launcher, then open the saved roster again. The saved draft must
remain; an unsaved preview may be gone. Existing demo data remains **synthetic**, and cannot be published
as a real institutional roster. Database integration does not grant application permissions or replace
the future real staff / published-timetable service contracts.

## Automated checks

From `services/examination-service`:

```powershell
..\..\.venv\Scripts\python.exe -m pytest tests/test_duty_allocation_neon_shared.py tests/test_duty_roster_repository.py tests/test_duty_roster_api.py tests/test_migrations.py tests/test_database.py -q
```

These tests use synthetic fixtures and fake database settings; they do not contact Neon.
