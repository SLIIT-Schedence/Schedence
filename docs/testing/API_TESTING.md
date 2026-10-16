# Testing every Schedence API endpoint

This guide tests **all 39 API operations**: 20 in the Identity Service and 19 in the Academic Data Service. It uses real Cognito sign-in against your local services and your Neon database.

| What | Where |
|---|---|
| Postman collection: 104 requests with automatic checks | [`schedence.postman_collection.json`](schedence.postman_collection.json) |
| Quick manual testing in the browser | Swagger UI: http://localhost:8001/docs and http://localhost:8002/docs |
| Automated unit tests (mocked, no AWS/DB) | `python -m pytest` in each service folder |

> **This writes real data.** These tests create real Cognito users, which receive real invitation e-mails, and real rows in your Neon `identity` and `academic` schemas.
>
> - **Test data is clearly marked.** Everything is named `TEST_…` / `Test …` with a random run suffix.
> - **There are no delete endpoints, by design.** Test accounts are disabled, not deleted, and appointments are ended, not deleted.
> - **E-mail quota.** Each new test user counts toward Cognito's **50 e-mails per day**: one full Postman run creates 2 users, and the optional M4 folder creates 2 more.

---

## 1. One-time preparation

### 1.1 Fix `.env`
Every line must be `KEY=value` or a comment starting with `#`. A plain-text note causes `python-dotenv could not parse statement` warnings.

### 1.2 Create the `Schedence-DevTools` app client (Cognito console)
Swagger UI and Postman run in your browser or app, so they need a **public** client (no secret). `Schedence-Web` is confidential and must stay that way.

1. Open the Cognito console in **Asia Pacific (Mumbai)**, then **User pools → Schedence → Applications → App clients → Create app client**.
   - **Application type:** Single-page application (SPA)
   - **Name:** `Schedence-DevTools`
   - **Return URL:** `http://localhost:8001/docs/oauth2-redirect`
   - Click **Create app client**.
2. Open the client's **Login pages** tab and choose **Edit**. Set these, then **Save changes**:

   | Setting | Value |
   |---|---|
   | Allowed callback URLs | `http://localhost:8001/docs/oauth2-redirect`<br>`http://localhost:8002/docs/oauth2-redirect`<br>`https://oauth.pstmn.io/v1/callback` |
   | Allowed sign-out URLs | `http://localhost:8001/docs` |
   | Identity providers | Cognito user pool directory |
   | OAuth 2.0 grant types | **Authorization code grant** only |
   | OpenID Connect scopes | **OpenID, Email, Profile** |

3. Under **Branding → Managed login**, make sure `Schedence-DevTools` has a style. If it doesn't, create one with the defaults.
4. Copy the client's **Client ID** into the root `.env`:
   ```env
   COGNITO_DEVTOOLS_CLIENT_ID=<client id>
   ```
5. Run `python -m scripts.verify_cognito` in `services\identity-service`. It should still say `Result: OK`.

Tokens from this client are accepted only while `APP_ENV` is not `production`.

### 1.3 Test e-mail addresses (Gmail plus-addressing)
Gmail delivers `yourname+anything@gmail.com` to `yourname@gmail.com`, and Cognito treats each `+tag` as a different e-mail. The collection uses this:
- the test Lecturer is `yourname+lect<suffix>@gmail.com`
- the test Academic Admin is `yourname+acad<suffix>@gmail.com`

Every invitation lands in your own inbox.

### 1.4 Authenticator app
MFA is required for every user. Keep your authenticator app (Google/Microsoft Authenticator) at hand. Each test user you sign in as adds one more entry to it.

### 1.5 Start the services

```powershell
cd C:\Users\DissanayakeDA\Documents\Final_Research\Schedence
.\.venv\Scripts\Activate.ps1
python scripts\run_services.py
```

Wait for `All services are running`. Both services must report `database: healthy`.

---

## 2. Option A: Swagger UI (quick, manual)

1. Open http://localhost:8001/docs and click **Authorize**.
2. In the **CognitoOAuth** section:
   - Leave `client_secret` empty.
   - Tick **openid, email, profile**.
   - Click **Authorize**.
3. A Cognito sign-in pop-up opens. Allow pop-ups for `localhost` if your browser blocks it. Sign in with your Super Admin e-mail, password and authenticator code. The pop-up closes and Swagger shows *Authorized*. Click **Close**.
4. Open any endpoint, then **Try it out → Execute**. Start with `GET /api/v1/identity/me`.
5. For the Academic service, open http://localhost:8002/docs and click **Authorize** again. Cognito remembers you, so it won't ask for the password a second time.

**Tips**
- **Expired token:** access tokens last **60 minutes**. When requests start returning `401 ... has expired`, click **Authorize** again.
- **Signing in as another user** (for example the test Lecturer): use a **private/incognito window**, because Cognito keeps the last user signed in for that browser. Or sign out first at `https://<COGNITO_DOMAIN>/logout?client_id=<COGNITO_DEVTOOLS_CLIENT_ID>&logout_uri=http://localhost:8001/docs`.
- **If the pop-up completes but Swagger shows "Auth error" or "Failed to fetch":** use Postman (Option B). Or paste an access token from Postman into the **BearerToken** box.

---

## 3. Option B: Postman (complete suite with automatic checks)

### 3.1 Import and configure
1. In Postman, choose **Import**, then select `docs/testing/schedence.postman_collection.json`.
2. Open the collection's **Variables** tab and set these values. All of them except your e-mail name are in `.env`, and none are secrets.

   | Variable | Value |
   |---|---|
   | `cognitoDomain` | value of `COGNITO_DOMAIN`, e.g. `https://xyz.auth.ap-south-1.amazoncognito.com` |
   | `devtoolsClientId` | value of `COGNITO_DEVTOOLS_CLIENT_ID` |
   | `institutionId` | value of `SLIIT_INSTITUTION_ID` |
   | `testEmailLocal` | your Gmail name before the `@`, e.g. `dumindudissanayake2k01` |
   | `testEmailDomain` | `gmail.com` (default) |
   | `identityUrl` / `academicUrl` | defaults `http://localhost:8001` / `http://localhost:8002` |

   Leave the other variables empty. The requests fill them in as they run.
3. Click **Save** (Ctrl+S).

### 3.2 Get a Super Admin access token
1. Open the collection's **Authorization** tab. OAuth 2.0 with *Authorization Code (With PKCE)* is already configured. If any field is empty, re-type it from the table below.
2. Scroll down, then click **Get New Access Token**.
3. In Postman's sign-in window, sign in as the **Super Admin**: your e-mail, password and authenticator code.
4. Click **Proceed**, then **Use Token**.

Every request in the collection now sends this access token, except those that deliberately use no token or another token.

| Field (if you need to set it manually) | Value |
|---|---|
| Grant type | Authorization Code (With PKCE) |
| Callback URL | `https://oauth.pstmn.io/v1/callback` (leave *Authorize using browser* unticked) |
| Auth URL | `{{cognitoDomain}}/oauth2/authorize` |
| Access Token URL | `{{cognitoDomain}}/oauth2/token` |
| Client ID | `{{devtoolsClientId}}`; Client Secret empty |
| Code Challenge Method | SHA-256 |
| Scope | `openid email profile` |
| Client Authentication | Send client credentials in body |

The token expires after 60 minutes. Click **Get New Access Token** again, or tick *Auto-refresh token*.

### 3.3 Run folders 00–09 (automatic)
Open the **Runner**, select the collection, and **untick the M1–M4 folders**. Keep the order and click **Run**. Every check should be green.

| Folder | What it proves |
|---|---|
| **00 Setup** | Picks a new random suffix. Always run it first, so a second run doesn't collide with the first |
| **01 Health** | Both services are alive and Neon answers `SELECT 1` (no token needed) |
| **02 Current user** | Your token resolves to the bootstrapped Super Admin with SLIIT-scoped permissions |
| **03 Roles, permissions, audit** | 6 system roles (no HOD/Dean/Coordinator), 27 permissions, the bootstrap audit entry |
| **04 User management** | Create Lecturer + Academic Admin (real invitation e-mails), list/search/filter, view, status, provisioning history, resend invitation, rename (synced to Cognito), assign and revoke roles, disable/enable (Cognito too), audit trail |
| **05 Identity validation and security** | 401 without/with a bad token, duplicate e-mail, HOD is not a login role, extra fields rejected, other institution, cannot disable yourself or change your own roles, 404/422 paths, non-retryable/non-cancellable onboarding |
| **06 Positions** | HOD / Program Coordinator / Dean created with configurable hours, details, update, new hours **version** (history kept), disable/enable |
| **07 Staff** | One staff member linked to the test Lecturer's login, one with **no** Schedence login |
| **08 Appointments and workload** | Assign HOD + Program Coordinator, hours override, audit trail, update, end, history, the workload contract (combined total only when the policy is resolved) |
| **09 Academic validation and conflicts** | Duplicate codes, other institution, CAPPED without cap, back-dated policy, a second HOD for one department, duplicate appointment, missing unit, reversed dates, no policy yet, unknown staff, editing/ending an ended appointment, disabling a position in use |

The 27-permission expectation includes `examinations:read` and
`examinations:manage`. Existing databases can still contain 25 permissions until
a separately approved RBAC data update is applied. Do not deploy the new
Examination permission checks or expect the collection's count check to pass
against that database before the update. Editing code does not update Neon.

`EXAMINATION_ADMIN` retains `examinations:generate`, `examinations:approve`,
`academic:staff:read`, `academic:workload:read` and `venues:read`, and receives
the two new permissions. Other roles receive no examination permissions.
Examination GET routes require `examinations:read`; seating PUT/DELETE routes,
including dry runs, require `examinations:manage`. Health routes stay public.

> Folders 05 and 09 contain **negative** tests. A green check there means the API correctly **refused** the request with the documented status and error code.
>
> The request *"ID token instead of access token"* in 05 needs the `idToken` variable. Open Postman's token details, copy the **id_token**, and paste it into `idToken`. Otherwise skip that one request.

### 3.4 Manual folders

**M1: as the Lecturer.** This proves academic appointments never grant login roles. The test Lecturer from folder 04 is now Head of Department.

1. Open the newest invitation e-mail for `yourname+lect<suffix>@gmail.com`. It contains a temporary password.
2. In the collection's **Authorization** tab, click **Get New Access Token**. In the sign-in window, click **Clear cookies**, then sign in as the Lecturer:
   1. Enter the temporary password.
   2. Set a new password.
   3. Scan the QR code into your authenticator app as a new entry.
   4. Enter the code.
3. In the token window, **copy the access token**. Don't click *Use Token*. Paste it into the collection variable `lecturerToken`, then **Save**.
4. In **Current Token**, make sure the **Super Admin** token is still selected.
5. Run M1. You should see:
   - `/me` shows only LECTURER.
   - Creating users is 403, even with spoofed `X-Role: SUPER_ADMIN` headers.
   - Every Academic endpoint is 403. Being an HOD gives no admin access.

**M2: disabled account blocked immediately.** Run M2 in order:
1. The Super Admin disables the Lecturer.
2. The Lecturer's still-valid token gets `403 ACCOUNT_DISABLED` from both services.
3. The account is enabled again and works.

**M3: Identity Service down (fail closed)**
1. Stop the launcher (Ctrl+C).
2. Start only the Academic service: `python scripts\run_services.py --only academic`.
3. Wait **20 seconds**, because read permissions are cached for 15 s.
4. Send the request. Expect `503 IDENTITY_SERVICE_UNAVAILABLE`.
5. Restart everything: `python scripts\run_services.py`.

**M4: Cognito outage, retry and cancel (optional)**
1. Stop the launcher. In `.env`, set `AWS_PROFILE=schedence-broken`, which is a profile that doesn't exist. Start the launcher again.
2. Run the first two requests. Both fail with `503 IDENTITY_PROVIDER_UNAVAILABLE`, and the collection saves their provisioning IDs.
3. Stop the launcher, restore `AWS_PROFILE=schedence-identity`, and start it again.
4. Run the remaining three requests:
   - the FAILED record is listed;
   - **retry** completes it (attempt 2, no duplicate Cognito user);
   - **cancel** works on the other one, which never reached Cognito.

---

## 4. Endpoint reference

Errors always look like:

```json
{"error": {"code": "…", "message": "…", "details": {…}, "request_id": "…"}}
```

### Identity Service: http://localhost:8001

| # | Method & path | Permission | Success | Typical errors |
|---|---|---|---|---|
| 1 | `GET /health` | none | 200 `{"status":"ok"}` | |
| 2 | `GET /health/db` | none | 200 `{"status":"healthy"}` | 503 database unreachable |
| 3 | `GET /api/v1/identity/me` | signed in | 200 profile + roles | 401 token, 403 `ACCOUNT_DISABLED` / `ACCOUNT_NOT_PROVISIONED` |
| 4 | `GET /api/v1/identity/me/permissions` | signed in | 200 permissions per institution | as above |
| 5 | `POST /api/v1/identity/users` | `identity:users:create` + `identity:roles:assign` | 201 user + provisioning | 409 `EMAIL_ALREADY_REGISTERED` / `PROVISIONING_IN_PROGRESS`; 422 `UNKNOWN_ROLE`; 403 other institution or `PRIVILEGED_ROLE_REQUIRED`; 503 Cognito |
| 6 | `GET /api/v1/identity/users` | `identity:users:read` | 200 page (`search`, `status`, `role_code`, `page`, `page_size`) | |
| 7 | `GET /api/v1/identity/users/{id}` | `identity:users:read` | 200 | 404 |
| 8 | `PATCH /api/v1/identity/users/{id}` | `identity:users:update` | 200 + `cognito_sync` | 422 other fields; 403 `PRIVILEGED_TARGET` |
| 9 | `POST /api/v1/identity/users/{id}/disable` | `identity:users:disable` | 200 + `cognito_sync` | 403 `CANNOT_DISABLE_SELF`; 409 `LAST_SUPER_ADMIN` |
| 10 | `POST /api/v1/identity/users/{id}/enable` | `identity:users:disable` | 200 | 503 Cognito |
| 11 | `GET /api/v1/identity/users/{id}/status` | `identity:users:read` | 200 local + live Cognito status | |
| 12 | `GET /api/v1/identity/users/{id}/provisioning` | `identity:users:read` | 200 history | |
| 13 | `POST /api/v1/identity/users/{id}/resend-invitation` | `identity:users:create` | 204 | 409 `INVITATION_NOT_APPLICABLE` (already signed in) |
| 14 | `PUT /api/v1/identity/users/{id}/roles` | `identity:roles:assign` | 200 | 403 `SELF_ROLE_CHANGE_FORBIDDEN` / `PRIVILEGED_ROLE_REQUIRED`; 422 `UNKNOWN_ROLE`; 409 `LAST_SUPER_ADMIN` |
| 15 | `GET /api/v1/identity/provisioning` | `identity:users:create` | 200 page (`status` filter) | |
| 16 | `POST /api/v1/identity/provisioning/{id}/retry` | `identity:users:create` | 200 | 409 `PROVISIONING_NOT_RETRYABLE` |
| 17 | `POST /api/v1/identity/provisioning/{id}/cancel` | `identity:users:create` | 200 | 409 `PROVISIONING_NOT_CANCELLABLE` |
| 18 | `GET /api/v1/identity/roles` | `identity:roles:read` | 200 (6) | |
| 19 | `GET /api/v1/identity/permissions` | `identity:permissions:read` | 200 (27) | |
| 20 | `GET /api/v1/identity/audit-logs` | `identity:audit:read` | 200 page (`action`, `actor_user_id`, `target_user_id`, `occurred_from`, `occurred_to`) | |

### Academic Data Service: http://localhost:8002

| # | Method & path | Permission | Success | Typical errors |
|---|---|---|---|---|
| 1 | `GET /health` | none | 200 | |
| 2 | `GET /health/db` | none | 200 | 503 |
| 3 | `POST /api/v1/academic/positions` | `academic:positions:create` | 201 position + policy v1 | 409 `POSITION_CODE_EXISTS`; 404 other institution; 422 |
| 4 | `GET /api/v1/academic/positions` | `academic:positions:read` | 200 page (`institution_id`, `include_inactive`) | |
| 5 | `GET /api/v1/academic/positions/{id}` | `academic:positions:read` | 200 with `policy_history` | 404 |
| 6 | `PATCH /api/v1/academic/positions/{id}` | `academic:positions:update` | 200 | 409 `POSITION_IN_USE` / `EXCLUSIVITY_CONFLICT` |
| 7 | `POST /api/v1/academic/positions/{id}/disable` | `academic:positions:disable` | 200 | 409 `POSITION_IN_USE` |
| 8 | `POST /api/v1/academic/positions/{id}/enable` | `academic:positions:disable` | 200 | |
| 9 | `POST /api/v1/academic/positions/{id}/workload-policies` | `academic:positions:update` | 201 new version | 422 `INVALID_EFFECTIVE_DATE`, CAPPED without cap |
| 10 | `GET /api/v1/academic/positions/{id}/workload-policies` | `academic:positions:read` | 200 versions | |
| 11 | `POST /api/v1/academic/staff` | `academic:staff:create` | 201 | 409 `STAFF_NUMBER_EXISTS` |
| 12 | `GET /api/v1/academic/staff` | `academic:staff:read` | 200 page (`institution_id`, `search`) | |
| 13 | `GET /api/v1/academic/staff/{id}` | `academic:staff:read` | 200 | 404 |
| 14 | `POST /api/v1/academic/staff/{staff_id}/appointments` | `academic:appointments:assign` | 201 | 404 `STAFF_NOT_FOUND` / `POSITION_NOT_FOUND`; 409 `DUPLICATE_APPOINTMENT` / `POSITION_ALREADY_HELD` / `POSITION_DISABLED`; 422 `INVALID_UNIT` / `NO_WORKLOAD_POLICY` |
| 15 | `GET /api/v1/academic/staff/{staff_id}/appointments` | `academic:appointments:read` | 200 (`include_ended=true` for history) | |
| 16 | `GET /api/v1/academic/appointments/{id}` | `academic:appointments:read` | 200 with `audit_trail` | 404 |
| 17 | `PATCH /api/v1/academic/appointments/{id}` | `academic:appointments:update` | 200 | 409 `APPOINTMENT_ENDED`; 422 other fields |
| 18 | `POST /api/v1/academic/appointments/{id}/end` | `academic:appointments:end` | 200 | 409 `APPOINTMENT_ENDED`; 422 dates |
| 19 | `GET /api/v1/academic/staff/{staff_id}/administrative-workload` | `academic:workload:read` | 200 contract (`as_of`) | 404 |

---

## 5. Troubleshooting

| Symptom | Fix |
|---|---|
| Cognito page says `redirect_mismatch` | The callback URL used (Swagger or Postman) is not in the DevTools client's **Allowed callback URLs** (section 1.2) |
| `invalid_client` / "client does not exist" | `devtoolsClientId` / `COGNITO_DEVTOOLS_CLIENT_ID` is wrong, or belongs to the old user pool |
| 401 `INVALID_TOKEN` "An access token is required" | You sent the **id_token**. Use the access token |
| 401 "issued to an unrecognised app client" | `COGNITO_DEVTOOLS_CLIENT_ID` is missing from `.env` (restart the launcher after adding it), or `APP_ENV=production` |
| 401 "has expired" | Get a new token (it lasts 60 min) |
| 403 `ACCOUNT_NOT_PROVISIONED` | Signed in as a Cognito user that has no Schedence account. Create it via `POST /users`, or sign in as the Super Admin |
| 503 `IDENTITY_PROVIDER_MISCONFIGURED` | AWS credentials problem: check `AWS_PROFILE=schedence-identity` and run `python -m scripts.verify_cognito` |
| 503 `IDENTITY_SERVICE_UNAVAILABLE` (Academic) | The Identity Service isn't running on `IDENTITY_SERVICE_URL` (the launcher sets it automatically) |
| First request after a break is slow or times out | Neon was asleep; retry. Add `DB_CONNECT_TIMEOUT=30` to `.env` if it happens often |
| Invitation e-mail doesn't arrive | Check spam. The Cognito default sender allows 50 e-mails/day per AWS account (resets 09:00 UTC = 14:30 Sri Lanka time) |
| Collection Runner: many failures after the first one | A request earlier in the run failed (for example the token expired), so later IDs are empty. Fix it and re-run from **00 Setup** |
