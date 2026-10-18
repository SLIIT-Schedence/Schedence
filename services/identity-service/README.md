# Identity Service

Authentication (AWS Cognito), Schedence user accounts, the institution registry, system access roles, permissions, administrator onboarding and the identity audit log.

- **Cognito** owns credentials: passwords, MFA, invitations, password reset.
- **Neon** (`identity` schema) owns Schedence data: accounts linked by Cognito `sub`, roles, permissions, provisioning state and audit logs.
- No passwords are stored in PostgreSQL. There is **no public registration**, and **no API grants the System Admin role**.

Accounts are created top-down:

```text
System Admin (Schedence product owners; operator bootstrap)
  └─ registers an institution and creates its Institution Admin      POST /platform/institutions, /platform/institutions/{id}/admins
       └─ Institution Admin creates the institution's users and roles   POST /users, PUT /users/{id}/roles
```

Academic appointments (HOD, Program Coordinator, Dean, ...) are **not** handled here and never grant login permissions; see [`../academic-data-service`](../academic-data-service/README.md).

**Contents:**
[Architecture](#architecture) ·
[Quick start](#quick-start-powershell) ·
[Configuration](#configuration) ·
[AWS Cognito and IAM](#aws-cognito-and-iam) ·
[Database and migrations](#database-and-migrations) ·
[Seeding roles](#seeding-roles-and-permissions) ·
[System Admin bootstrap](#bootstrapping-a-system-admin) ·
[Platform administration](#platform-administration-system-admin) ·
[Onboarding flow](#user-onboarding-flow) ·
[RBAC](#roles-and-permissions) ·
[API](#api) ·
[Swagger and Postman](#testing-with-swagger-ui-and-postman) ·
[Tests](#automated-tests) ·
[Troubleshooting](#troubleshooting) ·
[Security](#security-notes)

## Architecture

```text
services/identity-service/
├── app/
│   ├── main.py                  create_app() factory: middleware, routers, error envelope, Swagger OAuth
│   ├── core/                    settings, errors, Principal, container (DI), rate limiter, logging
│   ├── api/
│   │   ├── dependencies/        get_current_user, require_permissions, require_platform_permissions, require_roles, ...
│   │   ├── health.py            /health, /health/db
│   │   └── v1/                  /api/v1/identity/... routers (thin: validate, call a service, map the result)
│   ├── services/                business rules: users.py, platform.py, bootstrap.py, seeding.py, principals.py, directory.py
│   ├── repositories/            base.py (interfaces), sql.py (SQLAlchemy/psycopg 3)
│   ├── integrations/cognito/    client.py (interface + errors), boto3_client.py (AWS adapter)
│   ├── models/                  ORM models for the "identity" schema
│   ├── schemas/                 Pydantic request/response models
│   └── rbac/catalog.py          built-in roles and permissions (single source of truth)
├── alembic/                     service-owned migrations (version table: identity.alembic_version)
├── scripts/                     seed_roles.py, bootstrap_system_admin.py, verify_cognito.py
└── tests/                       mocked unit tests (no database, no AWS)
```

A request goes through three separate steps:

1. **Cognito verification** (`packages/auth-core`). Checks the RS256 signature against the cached JWKS (refreshed on key rotation), `iss`, `exp`, that `token_use` is `access`, and that `client_id` is a configured app client. It knows nothing about roles.
2. **Account resolution**. Looks up the local user by `sub`. Disabled or unknown accounts are rejected. Roles and permissions are loaded from Neon **on every request**, never from headers or Cognito groups.
3. **Authorisation**. Deny-by-default checks on permissions, scoped per institution. Services repeat the institution check for the resource being acted on. Platform (System Admin) permissions are held outside any institution and are checked separately: they never satisfy an institution check, and institution permissions never satisfy a platform check.

## Quick start (PowerShell)

From the **repository root**:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -e ./packages/db-core -e ./packages/auth-core      # shared packages first (not on PyPI)
python -m pip install --group services/identity-service/pyproject.toml:dev
```

From **`services/identity-service`**:

```powershell
cd services\identity-service
python -m pytest                                   # mocked unit tests
python -m scripts.verify_cognito                   # read-only check of .env against Cognito
alembic upgrade head --sql                         # preview migration SQL (no connection)
uvicorn app.main:create_app --factory --port 8001 --loop asyncio:SelectorEventLoop
```

On Windows, `--loop asyncio:SelectorEventLoop` is required for psycopg async. Open http://localhost:8001/docs.

The service is not pip-installed: both services use the top-level package `app`, so each one runs from its own folder.

## Configuration

Settings come from the repository-root `.env` (or `$SCHEDENCE_ENV_FILE`). Process environment variables take precedence.

| Variable | Required | Meaning |
|---|---|---|
| `DATABASE_URL` | yes | Neon pooled URL (runtime). See [db-core](../../README.md#database-infrastructure-packagesdb-core) |
| `DIRECT_DATABASE_URL` | recommended | Neon direct URL, used by migrations and operator scripts |
| `APP_ENV` | no | `development` (default), `testing`, `production`. Production disables `/docs` and the DevTools client |
| `AWS_REGION` | yes | User pool Region, e.g. `ap-south-1`. Must match the pool ID prefix |
| `COGNITO_USER_POOL_ID` | yes | e.g. `ap-south-1_EXAMPLE123` |
| `COGNITO_APP_CLIENT_ID` | yes | The web app client (`Schedence-Web`). Access tokens must carry this `client_id` |
| `COGNITO_DEVTOOLS_CLIENT_ID` | no | Public SPA client for Swagger UI / Postman. Accepted **only** when `APP_ENV` is not `production` |
| `COGNITO_DOMAIN` | no | `https://<prefix>.auth.<region>.amazoncognito.com`. Enables Swagger sign-in |
| `COGNITO_ISSUER` | no | Optional. Derived from Region + pool ID; if set, it must match or the service refuses to start |
| `COGNITO_REQUIRED_SCOPES` | no | Space-separated scopes every access token must carry |
| `AWS_PROFILE` | yes for admin calls | AWS CLI profile boto3 uses for Cognito admin APIs (not a secret). Leave unset in AWS, where a role is used |
| `FRONTEND_URL` / `CORS_ALLOWED_ORIGINS` | no | Allowed browser origins (no wildcards) |
| `RATE_LIMIT_WRITES_PER_MINUTE` | no | Per-user limit on administrative writes (default 60, per process) |

The **issuer** is `https://cognito-idp.<AWS_REGION>.amazonaws.com/<COGNITO_USER_POOL_ID>`. The **JWKS** is at `<issuer>/.well-known/jwks.json`. Both are derived, not configured.

The **client secret** of the `Schedence-Web` (Traditional web application) client is **not** used by this service and must not be put in the root `.env`. It belongs only in the frontend's server-side `apps/web/.env.local` (never with a `NEXT_PUBLIC_` prefix).

## AWS Cognito and IAM

Configured user pool (summary of the setup guide):

| Setting | Value |
|---|---|
| Region / plan | `ap-south-1` (Mumbai), Essentials (Managed Login) |
| Sign-in | E-mail only; **self-registration disabled** (admin-created accounts) |
| Required attributes | `email` |
| MFA | Required, authenticator apps (managed login enrols users at first sign-in) |
| Recovery | Self-service, verified e-mail only |
| App client `Schedence-Web` | Traditional web application (confidential), Authorization Code + PKCE, scopes `openid email profile`, callback `http://localhost:3000/api/auth/callback/cognito` |
| App client `Schedence-DevTools` (optional) | SPA (public, PKCE), callbacks `http://localhost:8001/docs/oauth2-redirect`, `http://localhost:8002/docs/oauth2-redirect`, optionally `https://oauth.pstmn.io/v1/callback` |

The User Pool ID and App Client IDs are **identifiers, not credentials**. Admin operations (`AdminCreateUser`, `AdminDisableUser`, ...) need an IAM identity. Use a dedicated least-privilege profile, ideally from IAM Identity Center (short-lived credentials), and never root keys or keys in `.env`:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Sid": "SchedenceIdentityServiceUserAdmin",
    "Effect": "Allow",
    "Action": [
      "cognito-idp:AdminCreateUser", "cognito-idp:AdminGetUser", "cognito-idp:ListUsers",
      "cognito-idp:AdminUpdateUserAttributes", "cognito-idp:AdminDisableUser", "cognito-idp:AdminEnableUser",
      "cognito-idp:AdminUserGlobalSignOut", "cognito-idp:DescribeUserPool"
    ],
    "Resource": "arn:aws:cognito-idp:<REGION>:<ACCOUNT_ID>:userpool/<USER_POOL_ID>"
  }]
}
```

```powershell
aws configure sso --profile schedence-identity        # once (IAM Identity Center)
aws sso login --profile schedence-identity            # whenever the session expires
# then in the root .env:  AWS_PROFILE=schedence-identity
python -m scripts.verify_cognito                      # PASS/WARN/FAIL, prints no identifiers
```

`verify_cognito` checks the following without printing any identifiers:
- the issuer and JWKS
- that the managed login page loads
- self-registration disabled, e-mail sign-in, plan, domain, MFA, password length, recovery
- deletion protection
- that the profile is least-privilege: `ListUserPools` must be denied

The service never falls back silently to your `default` AWS profile from the verify script.

**Invitation e-mail:** the default Cognito sender is limited to **50 e-mails per day per AWS account** (resets 09:00 UTC). Configure Amazon SES (verified domain, production access) before onboarding real users.

## Database and migrations

Tables live in the `identity` schema of the existing Neon database. Nothing else is touched.

| Table | Purpose |
|---|---|
| `users` | Account linked to Cognito by unique `cognito_sub`; lower-case unique `email`; `account_status` ACTIVE/DISABLED |
| `institutions` | Institution registry: unique upper-case `code`, `name`, `status` ACTIVE/DISABLED. Never deleted; other services treat the ID as opaque |
| `roles`, `permissions`, `role_permissions` | Built-in system roles and fine-grained permissions |
| `user_roles` | Institution role per (user, institution), FK to `institutions`. Revocation is soft (`revoked_at`), so history is kept; a partial unique index allows one active assignment |
| `platform_role_assignments` | Platform role (SYSTEM_ADMIN) per user, outside any institution. Soft revocation; one active assignment per (user, role) |
| `user_provisioning` | Cognito → Neon onboarding state machine, FK to `institutions`; partial unique index allows one open onboarding per e-mail |
| `identity_audit_logs` | Append-only identity events with non-secret `safe_metadata` |

```powershell
cd services\identity-service
alembic upgrade head --sql      # 1. review the SQL (offline, no connection)
alembic upgrade head            # 2. apply - only after review/approval
alembic current                 # shows identity.alembic_version
```

`tests/test_migrations.py` proves that the hand-written migrations produce exactly the schema the models describe, and that upgrades only touch `identity`: no DROP/TRUNCATE/DELETE, and the only ALTERs are added foreign keys.

**Upgrading an existing database to `0002_platform_admin`** (adds System Admin):

1. `alembic upgrade head --sql`, review, then `alembic upgrade head`. In one transaction it creates `institutions`, registers every institution ID already used by a role or provisioning record under a placeholder code `INST-<first 8 hex digits>` (keeping the ID), adds the foreign keys, creates `platform_role_assignments`, and renames the `SUPER_ADMIN` role row to `INSTITUTION_ADMIN`. Current Super Admins keep their access as Institution Admins.
2. `python -m scripts.seed_roles --dry-run`, then `python -m scripts.seed_roles`: adds the SYSTEM_ADMIN role and the `platform:*` permissions.
3. [Bootstrap a System Admin](#bootstrapping-a-system-admin) with a dedicated e-mail address.
4. As the System Admin, rename the placeholder institution: `PATCH /platform/institutions/{id}` with `{"code": "SLIIT", "name": "..."}`.

The downgrade drops `institutions` and `platform_role_assignments` (and every SYSTEM_ADMIN grant) and renames the role back to `SUPER_ADMIN`.

## Seeding roles and permissions

```powershell
python -m scripts.seed_roles --dry-run   # show changes, write nothing
python -m scripts.seed_roles             # idempotent: re-running changes nothing
python -m scripts.seed_roles --prune     # also remove grants that left the catalog
```

## Bootstrapping a System Admin

SYSTEM_ADMIN is for the Schedence product owners. It is granted only by this operator command, which needs database credentials **and** AWS credentials and has no HTTP equivalent.

1. In the Cognito console, choose **Users > Create user** for a **dedicated** platform e-mail address (not an address you use inside an institution).
   - Send an e-mail invitation.
   - Tick *Mark e-mail as verified*.
   - Choose *Generate a password*.
2. Complete first sign-in through managed login: new password and authenticator app. The user must show **Confirmed**.
3. Run:
   ```powershell
   cd services\identity-service
   aws sso login --profile schedence-identity
   python -m scripts.seed_roles
   python -m scripts.bootstrap_system_admin --email owner@example.com
   ```

The command checks that the Cognito identity exists, is enabled, has a verified e-mail and is `CONFIRMED`. It asks you to retype the e-mail, then in one transaction (under an advisory lock) it:
- links the Cognito `sub` to a new or existing account
- grants SYSTEM_ADMIN platform-wide (no institution role)
- writes a `platform.system_admin_bootstrapped` audit entry that names the operator

It refuses an account that already holds roles in an institution (`INSTITUTION_ACCOUNT`): platform and institution access never share an account. Re-running it for the same person is a no-op, and each product owner is bootstrapped the same way.

With `--provision --full-name "..."` it sends the Cognito invitation instead. Re-run it after the person's first sign-in.

## Platform administration (System Admin)

A System Admin manages institutions and their Institution Admins, and nothing else:

- **Institutions.** Register (`code` + `name`), list/search, rename or change the code, disable and re-enable. While an institution is disabled, roles held in it grant **no permissions**, immediately and in every service. Its users can still sign in and see the institution's status in `GET /me`.
- **Institution Admins.** Create one (Cognito invitation + account + INSTITUTION_ADMIN in that institution), list them, resend the invitation, disable/enable the account, or revoke the role. If onboarding fails part-way, send the same `POST` again: it resumes the same provisioning record. Unlike an Institution Admin, a System Admin may remove an institution's **last** admin (e.g. a compromised account) and then create a new one.
- **No access inside institutions.** A System Admin cannot list, create or change lecturers, students or other users, read an institution's audit log, or use the Academic Data Service. `GET /me/permissions` returns no institutions for it, and every institution endpoint answers 403. The admin list shows only each admin's INSTITUTION_ADMIN assignment, not their other roles.
- **Audit.** Every platform change records a `platform.*` event carrying the institution's ID. It appears in `GET /platform/audit-logs` and in that institution's own `GET /audit-logs`.

## User onboarding flow

```text
POST /users ─▶ provisioning PENDING (committed)
              └─▶ Cognito AdminCreateUser (email_verified=true, invitation e-mail)
                    ├─ error ──────────▶ FAILED + error_category (safe code)  ─┐
                    └─ ok / user exists ▶ COGNITO_CREATED + sub (committed)    │ retry: POST /provisioning/{id}/retry
                                            └─▶ users + user_roles + audit      │   or the same POST /users again
                                                 ├─ ok ─▶ COMPLETED            │   (idempotent: an existing Cognito
                                                 └─ DB error ▶ FAILED ─────────┘    identity is linked, not duplicated)
```

- **No silent orphans.** A Cognito identity created before a database failure stays tracked as a FAILED record that carries its `sub`. It can only be retried, not cancelled.
- **Duplicates.** E-mail addresses are unique in `users`, and a partial unique index allows only one open onboarding per e-mail.
- **First sign-in.** The user follows the invitation and, in managed login: enters the temporary password, chooses a new password, sets up the authenticator app, then lands in the app. Temporary passwords expire after 7 days; use `POST /users/{id}/resend-invitation`.
- **Disable.** The local status flips first, so API access is blocked immediately, even for unexpired tokens. Then Cognito `AdminDisableUser` + `AdminUserGlobalSignOut` run. If Cognito is down, the response says `cognito_sync: "failed"`; call disable again to retry.

## Roles and permissions

| Role | Scope | Permissions |
|---|---|---|
| `SYSTEM_ADMIN` | platform | all `platform:*` (institutions, Institution Admins, platform audit) |
| `INSTITUTION_ADMIN` | institution | all `identity:*` and all `academic:*` |
| `ACADEMIC_ADMIN` | institution | `teaching:generate`, `teaching:approve`, `academic:positions:read`, `academic:appointments:read`, `academic:staff:read`, `academic:workload:read`, `venues:read` |
| `EXAMINATION_ADMIN` | institution | `examinations:generate`, `examinations:approve`, `academic:staff:read`, `academic:workload:read`, `venues:read` |
| `VENUE_MANAGER` | institution | `venues:read`, `venues:manage` |
| `LECTURER`, `STUDENT` | institution | none yet (portal access only) |

Rules enforced in `app/services/users.py` and `app/services/platform.py`:
- Everything is denied by default. Institution permissions are evaluated **per institution**; platform permissions are separate.
- SYSTEM_ADMIN is never granted through the API (`PLATFORM_ROLE_NOT_ASSIGNABLE`), and institution admins cannot touch an account that holds it (`PLATFORM_ACCOUNT`).
- `INSTITUTION_ADMIN` can be granted or revoked within an institution only by an INSTITUTION_ADMIN of that institution, or by a System Admin through the platform API.
- Institution Admin accounts can be modified only by Institution Admins of the same institution (or a System Admin).
- Nobody can change their own roles or disable their own account.
- The **last active Institution Admin** of an institution cannot be disabled or lose the role through the institution API. This is serialised with `pg_advisory_xact_lock`, so two admins cannot remove each other concurrently.

## API

Base path `/api/v1/identity`. Errors always look like `{"error": {"code", "message", "details?", "request_id"}}`.

| Method & path | Permission | Notes |
|---|---|---|
| `GET /health`, `GET /health/db` | none | `/health/db` runs `SELECT 1` |
| `GET /me` | authenticated | Profile, institution roles, `platform_roles`, and the `institutions` (code, name, status) the roles belong to |
| `GET /me/permissions` | authenticated | Effective permissions per institution, plus `platform` roles/permissions. **Also the contract other services call** with the caller's token; they authorise from `institutions` only |
| `POST /users` | `identity:users:create` + `identity:roles:assign` | `{email, full_name, role_code, institution_id}` → 201 |
| `GET /users` | `identity:users:read` | `search`, `status`, `role_code`, `page`, `page_size`; scoped to your institutions |
| `GET /users/{id}` | `identity:users:read` | |
| `PATCH /users/{id}` | `identity:users:update` | `{full_name}` only (synced to Cognito `name`) |
| `POST /users/{id}/disable`, `/enable` | `identity:users:disable` | |
| `GET /users/{id}/status` | `identity:users:read` | Local + live Cognito status |
| `GET /users/{id}/provisioning` | `identity:users:read` | Provisioning history |
| `POST /users/{id}/resend-invitation` | `identity:users:create` | Only while `FORCE_CHANGE_PASSWORD` |
| `PUT /users/{id}/roles` | `identity:roles:assign` | `{institution_id, role_codes}` replaces roles **in that institution** |
| `GET /provisioning`, `POST /provisioning/{id}/retry`, `/cancel` | `identity:users:create` | |
| `GET /roles` / `GET /permissions` / `GET /audit-logs` | `identity:roles:read` / `identity:permissions:read` / `identity:audit:read` | Roles and permissions an institution can grant (no SYSTEM_ADMIN, no `platform:*`) |

System Admin endpoints, under `/api/v1/identity/platform`:

| Method & path | Permission | Notes |
|---|---|---|
| `POST /institutions` | `platform:institutions:create` | `{code, name}` → 201; 409 `INSTITUTION_CODE_TAKEN` |
| `GET /institutions`, `GET /institutions/{id}` | `platform:institutions:read` | `search`, `status`, `page`, `page_size` |
| `PATCH /institutions/{id}` | `platform:institutions:update` | `{code?, name?}` |
| `POST /institutions/{id}/disable`, `/enable` | `platform:institutions:disable` | Optional `{reason}` |
| `GET /institutions/{id}/admins` | `platform:institution_admins:read` | Only each admin's INSTITUTION_ADMIN assignment is shown |
| `POST /institutions/{id}/admins` | `platform:institution_admins:create` | `{email, full_name}` → 201; 409 `INSTITUTION_DISABLED` / `EMAIL_ALREADY_REGISTERED`; resend to resume |
| `POST /institutions/{id}/admins/{user_id}/resend-invitation` | `platform:institution_admins:create` | Only while `FORCE_CHANGE_PASSWORD` |
| `DELETE /institutions/{id}/admins/{user_id}` | `platform:institution_admins:revoke` | Revokes the role; the account and its other roles stay |
| `POST /institutions/{id}/admins/{user_id}/disable`, `/enable` | `platform:institution_admins:disable` | The account; works for the last admin too |
| `GET /audit-logs` | `platform:audit:read` | `platform.*` events only |

## Testing with Swagger UI and Postman

**Swagger UI** (http://localhost:8001/docs):
1. Set `COGNITO_DEVTOOLS_CLIENT_ID` and `COGNITO_DOMAIN` in `.env`. The DevTools app client must allow `http://localhost:8001/docs/oauth2-redirect`.
2. Click **Authorize** → *CognitoOAuth*, then sign in through managed login. Swagger uses Authorization Code + PKCE and sends the **access token**.
3. Alternatively, paste an access token into *BearerToken*.

**Postman**:
1. On the request, choose *Authorization*: OAuth 2.0, *Authorization Code (With PKCE)*.
2. Set the Auth URL to `<COGNITO_DOMAIN>/oauth2/authorize` and the Access Token URL to `<COGNITO_DOMAIN>/oauth2/token`.
3. Use the DevTools client ID (no secret), scope `openid email profile`, and callback `https://oauth.pstmn.io/v1/callback`.
4. Send the **access_token**, not the id_token.

## Automated tests

```powershell
cd services\identity-service
python -m pytest
```

All tests are **mocked**:
- in-memory repositories that mimic PostgreSQL unique constraints and rollback
- an in-memory fake Cognito
- botocore `Stubber` for the boto3 adapter
- RSA keys generated in the test for JWTs

No database or AWS account is touched, and no test database exists. Coverage:
- seeding and idempotency
- JWT validation: signature, expiry, issuer, client, ID-token rejection, key rotation
- disabled and unknown accounts
- authorised and unauthorised user creation
- Cognito outages, a database failure after Cognito success, retry, linking an existing identity
- role validation, self-escalation, privileged-role rules
- last-Institution-Admin protection under stale permissions
- System Admin bootstrap rules (dedicated account, idempotency)
- the System Admin → Institution Admin → user chain, institution disabling, and the platform/institution access boundary in both directions
- migration/model consistency, the backfill order, and the downgrade

These tests do **not** prove live AWS or Neon integration. For that, use `python -m scripts.verify_cognito` and `/health/db`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Invalid configuration ... COGNITO_USER_POOL_ID belongs to a different Region` | `AWS_REGION` must equal the pool ID prefix |
| `COGNITO_ISSUER does not match` | Delete `COGNITO_ISSUER` or copy the `issuer` value from the pool's openid-configuration |
| 401 `INVALID_TOKEN` "An access token is required" | You sent the ID token; send the access token |
| 401 "issued to an unrecognised app client" | The token came from a client other than `COGNITO_APP_CLIENT_ID` (or DevTools outside production) |
| 403 `ACCOUNT_NOT_PROVISIONED` | Valid Cognito user without a Schedence account: create it via `POST /users`, `POST /platform/institutions/{id}/admins`, or the System Admin bootstrap |
| 403 on every institution endpoint for a working account | The institution is disabled (`GET /me` shows its status), or you are signed in as a System Admin |
| 503 `IDENTITY_PROVIDER_MISCONFIGURED` | `AWS_PROFILE` missing or expired (`aws sso login --profile ...`), or IAM policy lacks an action |
| Invitation e-mails stop arriving | Default Cognito e-mail quota (50/day) reached; configure SES |
| `/health/db` says `incompatible event loop` | Start uvicorn with `--loop asyncio:SelectorEventLoop` on Windows |
| `relation "identity.users" does not exist` | Migrations not applied yet (`alembic upgrade head` after review) |

## Security notes

- **Tokens.** Only verified **access** tokens are trusted. Role and permission headers from clients are ignored.
- **Tokens and offline checks.** Offline JWT checks cannot see Cognito revocation, so local account status is checked on every request. Keep access tokens short-lived (default 60 min).
- **Error responses.** Errors never contain raw AWS or database messages, tokens or submitted values. Logs pass through a redaction filter, and botocore is kept at WARNING.
- **Audit metadata.** Stores role codes, IDs and outcome codes, never secrets.
- **CORS.** Uses explicit origins only. Administrative writes are rate-limited per user. That limiter is per process, so also limit at the gateway in production.
- **Production.** Disables `/docs`, `/redoc`, `/openapi.json` and the DevTools client, and uses an IAM role instead of `AWS_PROFILE`.
