# Schedence web

Next.js 16 (App Router, Cache Components) front end. Sign-in happens on Schedence's own `/login` page: the Next.js server sends the user's answers to the AWS Cognito user pool API, and [Auth.js](https://authjs.dev) (`next-auth@5`) keeps the resulting session. Passwords and authenticator codes pass through the Next.js server on their way to Cognito; they are never stored or logged. Cognito still checks them, enforces the password policy and MFA, and locks out repeated failures.

## Setup (PowerShell)

```powershell
cd apps\web
npm install
if (-not (Test-Path .env.local)) { Copy-Item .env.example .env.local }
# Generate a value for AUTH_SECRET and paste it into .env.local:
node -e "console.log(require('crypto').randomBytes(32).toString('base64url'))"
```

Fill in the rest of `.env.local` (see [.env.example](.env.example)). Every variable is server-only, so none uses the `NEXT_PUBLIC_` prefix.

| Variable | Value |
|---|---|
| `AUTH_SECRET` | Encrypts the session cookie (random 32 bytes, see above) |
| `AUTH_URL` | `http://localhost:3000` |
| `AUTH_COGNITO_ID` | Client ID of the `Schedence-Web` app client |
| `AUTH_COGNITO_SECRET` | Client secret of `Schedence-Web`. Keep it only in `.env.local`, never in the root `.env` |
| `AUTH_COGNITO_ISSUER` | `https://cognito-idp.<region>.amazonaws.com/<user pool id>` |
| `IDENTITY_SERVICE_URL` | `http://localhost:8001` |
| `ACADEMIC_SERVICE_URL` | `http://localhost:8002` (Academic Admin console) |
| `TEACHING_SERVICE_URL` | `http://localhost:8004` (Academic Admin teaching timetables) |

In the Cognito console, open **App clients → Schedence-Web → App client information → Edit** and, under **Authentication flows**, make sure these are selected:

- **ALLOW_USER_PASSWORD_AUTH** (sign in with username and password). Without it, the sign-in form shows "Sign-in isn't set up correctly".
- **ALLOW_REFRESH_TOKEN_AUTH** (keeps users signed in when the access token expires).

The managed login pages, callback URLs and sign-out URLs are no longer used by this app.

Then start identity-service on port 8001 (see [its README](../../services/identity-service/README.md)) and run:

```powershell
npm run dev        # http://localhost:3000
```

## How sign-in works

1. `src/proxy.ts` sends signed-out visitors to `/login?callbackUrl=<path>`.
2. The form on `/login` posts to the `signInStep` server action (`src/lib/sign-in-actions.ts`), which calls Cognito `InitiateAuth` (`USER_PASSWORD_AUTH`, with the client secret hash). Cognito may then ask for more steps, each with its own screen:
   - **New password** on the first sign-in with the invitation's temporary password (`NEW_PASSWORD_REQUIRED`).
   - **Authenticator setup**: a QR code and key from `AssociateSoftwareToken`, then the first code via `VerifySoftwareToken` (`MFA_SETUP`).
   - **Authenticator code** on later sign-ins (`SOFTWARE_TOKEN_MFA`).
   - **Forgot password** sends an emailed code (`ForgotPassword`, then `ConfirmForgotPassword`).

   Between steps, Cognito's session string lives in an encrypted, HTTP-only cookie scoped to `/login` (`src/lib/sign-in-session.ts`); the browser only sees which step comes next.
3. When Cognito returns tokens, the action seals them into a one-minute handoff and calls Auth.js `signIn("credentials")` in the same server process. The Credentials provider in `src/auth.ts` only unseals that handoff, so a browser can't post its own tokens. Auth.js then stores the access and refresh tokens in its encrypted, HTTP-only session cookie.
4. Pages call identity-service from the server with the **access token** (`src/lib/identity.ts`, `GET /api/v1/identity/me`). The token never reaches browser JavaScript, and `/api/auth/session` strips it.
5. When the access token is about to expire (60 s before), the `jwt` callback in `src/auth.ts` refreshes it with `GetTokensFromRefreshToken`. If the refresh fails, the user is sent to `/login?error=SessionExpired`.
6. **Sign out** clears the Auth.js session and revokes the refresh token in Cognito (`RevokeToken`), so a copied session cookie stops working.

The home page reports accounts that are not provisioned (`ACCOUNT_NOT_PROVISIONED`) or disabled (`ACCOUNT_DISABLED`), and an identity service that is unreachable.

## System Admin console

Users whose `/me` response lists `SYSTEM_ADMIN` in `platform_roles` are sent from `/` to `/platform`. Everyone else who opens `/platform` is sent back to `/`. Every page calls identity-service's `/api/v1/identity/platform/*` endpoints from the server, with the user's own access token, so the backend still checks each permission.

| Page | What it does |
|---|---|
| `/platform` | KPI tiles (institutions, active, disabled, changes in the last 7 days), institutions A–Z, recent activity |
| `/platform/institutions` | Search by name or code, filter by status, pages of 20. **Register institution** opens a dialog and then goes to the new institution |
| `/platform/institutions/[id]` | Edit the name and code, disable or re-enable the institution, and invite, re-invite, disable, re-enable or remove its Institution Admins. Also shows the institution's activity |
| `/platform/audit` | The platform audit log, filtered by event, institution and Colombo date range, 25 entries per page |

Changes are server actions (`src/lib/platform-actions.ts`) that call `refresh()` on success. Backend error codes such as `INSTITUTION_CODE_TAKEN`, `EMAIL_ALREADY_REGISTERED` and `INVITATION_NOT_APPLICABLE` show up inside the dialog. Icons are Google Material Symbols (Rounded): `src/components/icon.tsx` asks Google Fonts for only the listed glyphs, so add a name there before using it.

## Institution Admin console

Users who hold `INSTITUTION_ADMIN` in an institution are sent from `/` to `/admin`. It uses the same shell, dialogs and cards as the System Admin console (`src/components/console/`), and every call goes to identity-service with the admin's own token. identity-service scopes the lists to the institutions where the admin has the needed permission.

| Page | What it does |
|---|---|
| `/admin` | KPI tiles (people, active share, lecturers, open invitations), quick-invite tiles for Academic Admins, Examination Admins, Venue Managers and Lecturers, a **People by role** bar chart, a **New accounts** chart (weekly, last 12 weeks, from `user.created` audit events, weeks start Monday in Colombo time), recent invitations and recent activity |
| `/admin/users` | Search by name or email, filter by role and status, 20 per page, and resend an invitation |
| `/admin/invitations` | Onboarding records: *Needs attention* (pending or failed), *Sent*, *Cancelled*, *All*. Retry or cancel, with the Cognito failure reason in words |

**Invite** (`POST /api/v1/identity/users`) offers `ACADEMIC_ADMIN`, `EXAMINATION_ADMIN`, `VENUE_MANAGER` and `LECTURER` (`INVITABLE_ROLE_CODES` in `src/lib/roles.ts`). Cognito emails a temporary password that works for 7 days. If Cognito fails, identity-service keeps the record as `FAILED`, and the dialog tells the admin to retry it from Invitations.

Charts (`src/components/console/charts.tsx`) are single-series and drawn with plain HTML in the primary blue, which is 4.2:1 on the white cards. Every value is reachable without hovering: bars carry their values, columns label the latest and the peak, and each chart has a **View as table** twin. Hover and keyboard focus show the same tooltip.

## Academic Admin console

Users who hold `ACADEMIC_ADMIN` in an institution are sent from `/` to `/academic` (Institution Admins go to `/admin` first). It uses the same shell and cards as the other consoles. Pages call academic-data-service (`ACADEMIC_SERVICE_URL`, `/api/v1/academic/*`) from the server with the user's own token, through the same request helper as identity-service (`src/lib/service-api.ts`). The service checks each permission and scopes every list to the user's institutions.

The console is **view only**, because the role only reads academic data (`academic:staff:read`, `academic:positions:read`, `academic:appointments:read`, `academic:workload:read`). Staff, positions and appointments are maintained by the Institution Admin.

Academic Admins also hold rescheduling permissions, so the sidebar has a **Scheduling** group and the overview has a button, both labelled **Adaptive rescheduling**, that open `/adaptive`. That console lives on `feat/adaptive-service`, so on a branch without it the link shows Next.js's 404 page until both are merged.

| Page | What it does |
|---|---|
| `/academic` | KPI tiles (staff, staff with a Schedence login, active positions, positions without hours in force), **Administrative hours by position** and **Staff by academic rank** bar charts, recently added staff, and a teaching-timetables card. The timetables card is a placeholder until the teaching service is connected |
| `/academic/staff` | The staff register, searchable by name, staff number or email, 20 per page |
| `/academic/staff/[id]` | Rank, email, login link, the administrative workload contract as of today (total protected hours, or each appointment's hours when policies don't combine), and current appointments or the full history |
| `/academic/positions` | Position types with their unit, weekly hours in force and combination rule; *Active* or *All* (including disabled) |
| `/academic/positions/[id]` | The position's definition and its versioned hours-policy history |

## Layout

```text
src/
├── auth.ts                     Auth.js config: Credentials handoff, route guard, token refresh
├── proxy.ts                    Route protection (Next 16 "proxy", formerly middleware)
├── lib/
│   ├── auth-actions.ts         Server action: sign out
│   ├── cognito.ts              Cognito user pool API calls (server-only)
│   ├── sign-in-actions.ts      Server action behind every sign-in step
│   ├── sign-in-session.ts      Encrypted challenge cookie and token handoff
│   ├── identity-api.ts         identity-service request helper with the user's token (server-only)
│   ├── identity.ts             The signed-in account (GET /me), including platform roles
│   ├── identity-records.ts     Shared identity-service record types and mappers
│   ├── form-state.ts           Dialog result type and backend-error messages
│   ├── platform.ts             System Admin reads: institutions, admins, audit log
│   ├── platform-actions.ts     System Admin changes (server actions)
│   ├── institution.ts          Institution Admin reads: users, invitations, audit log
│   ├── institution-actions.ts  Institution Admin changes: invite, resend, retry, cancel
│   ├── roles.ts                Institution role names, descriptions and icons
│   ├── weeks.ts                Monday-start weeks in Colombo time, for charts
│   ├── audit-events.ts         Labels and icons for platform.* audit events
│   └── safe-redirect.ts        Allows only same-site callback paths
├── components/                 Wordmark, Material Symbols icon, submit and sign-out buttons
│   └── console/                Admin console shell, dialogs, cards, charts, activity feed, user chip
└── app/
    ├── globals.css             Palette tokens (Tailwind v4 @theme)
    ├── login/                  Sign-in page: illustration slideshow and sign-in steps
    ├── (app)/                  Signed-in area: header and home
    ├── (platform)/platform/    System Admin console: overview, institutions, audit log
    ├── (institution)/admin/    Institution Admin console: dashboard, people, invitations
    └── api/auth/[...nextauth]/ Auth.js route handler
```

## Design tokens

| Token | Hex | Use |
|---|---|---|
| `primary` | `#4772DD` | Primary buttons, active and selected states, focus rings |
| `primary-strong` | `#3B62C9` | Hover, and small text or links on light surfaces |
| `secondary` | `#888EBA` | Secondary accents, calendar categories, timetable labels |
| `background` | `#F7F8FF` | App background |
| `accent` | `#E6F4F1` | Highlights, selected cards, informational sections |
| `ink` / `muted` / `line` | `#1D2142` / `#5A5F82` / `#E2E4F3` | Text, secondary text, borders |

Type: Archivo (variable width; expanded for headlines) and JetBrains Mono for times and codes.

Illustrations in `public/` are by [Storyset](https://storyset.com). Their free licence requires the attribution shown on the login page.
