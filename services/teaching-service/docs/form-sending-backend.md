# Form sending backend

This phase issues frozen forms and tracks recipients. It does not send email,
WhatsApp messages, accept answers, create submissions, or change scheduler inputs.
The existing draft builder remains editable after issuing; sent copies never change.

## Development setup

Migration `20261021_0004_form_sending.sql` adds two Teaching-owned tables, tenant
RLS, immutable-record triggers and a unique idempotency key per institution/actor.
It was applied only to local PostgreSQL `127.0.0.1:52870/schedence_step12b4_test`.
No Alembic history was fabricated for this pre-existing fixture database.
The restricted runtime role receives SELECT/INSERT on both tables and
UPDATE(first_opened_at) on recipients. No DELETE or issue UPDATE permission is needed.

Enable with `TEACHING_FORM_SENDING_ENABLED=true` only in the existing
`APP_ENV=development`, `TEACHING_GENERATION_MODE=DURABLE_ISOLATED` setup.
The result store must use loopback PostgreSQL. Each sending-store transaction
also checks the actual server address and rejects a Neon project. The existing
restricted Academic reader/readiness configuration is required. That reader
uses verified, institution-scoped, database-enforced READ ONLY transactions;
no Academic, Identity or production data is written.

`TEACHING_FORMS_PUBLIC_ORIGIN` is a trusted configured origin, default
`http://localhost:3000`; request bodies cannot choose a redirect/link host.
The share URL is `/forms/{distribution_id}?institution_id=...` at that origin.
The Academic Admin UI now provides sending controls, sent-form history, recipient
tracking and a Copy Shareable Link button using `share_url` directly. The lecturer
landing page remains deferred. A URL is a locator, never an anonymous permission or token.

## APIs

All paths below are under `/api/v1/teaching` (also `/api/teaching`) and require
the existing verified Cognito bearer authentication and fresh Identity grants.
Administrative endpoints reuse the institution's existing `teaching:generate`
permission; appointments and client-supplied identity claims grant no access.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/form-recipients?institution_id=...` | Eligible staff, departments and account-link problems |
| POST | `/form-templates/{id}/send` | Atomically freeze a revision and its recipient roster |
| GET | `/form-distributions?institution_id=...` | Paginated issued forms, computed OPEN/CLOSED status |
| GET | `/form-distributions/{id}?institution_id=...` | Frozen issue and copyable share URL |
| GET | `/form-distributions/{id}/recipients?institution_id=...` | Paginated roster and first-open tracking |
| GET | `/lecturer/form-distributions?institution_id=...` | Current lecturer's paginated assignment inbox |
| GET | `/lecturer/form-distributions/{id}?institution_id=...` | Authorized recipient's frozen form, read-only |
| POST | `/lecturer/form-distributions/{id}/opened?institution_id=...` | Explicit, idempotent first-open acknowledgement |

Send body (with required `Idempotency-Key` header):

```json
{
  "institution_id": "84651d6d-0c8a-470a-a798-5220666fd367",
  "expected_revision": 1,
  "channel": "SHARE_LINK",
  "audience": {"kind": "ALL_ELIGIBLE"},
  "deadline": "2030-01-01T17:00:00+05:30"
}
```

`DIRECT` creates targeted assignments without a share URL. `SHARE_LINK` returns
a copyable URL for the same frozen roster; forwarding does not add recipients.
Both channels accept `SELECTED` + `staff_ids`, `DEPARTMENT` + `department_id`, or
`ALL_ELIGIBLE`. All IDs are authoritative Academic UUIDs in the chosen institution.
Changing payload under an existing idempotency key gives 409; retries reuse the
original issue. Stale source revisions give 409 and create no partial issue/roster.
An empty form or audience, unknown/ineligible staff, naive/past deadline, or more
than 10000 recipients is rejected. Deadlines are stored in UTC and frozen.

## Eligibility and access

Eligibility means ACTIVE Academic staff with a teaching profile of type ACADEMIC
or INSTRUCTOR. Department selection uses the teaching profile department, never
an administrative appointment or guessed title. The Academic staff
`identity_user_id` is the only account link; names, staff numbers and email do
not authenticate a person. An ambiguous link is counted across *all* institution
staff, including inactive/non-teaching records.

Unlinked and ambiguous staff remain visible in the frozen roster as `NO_LINK`
or `AMBIGUOUS_LINK`; they cannot access the form. They are not silently omitted
or automatically granted access if linked later. Issue a new distribution after
the owning services correct a link. `READY` means uniquely linked, not emailed.

Lecturer access requires the current institution LECTURER role, the frozen
recipient user ID, and a fresh, unique, active Academic mapping to the same staff
ID. Disabled accounts are rejected by shared authentication. Link changes,
revoked roles, forwarded URLs, and cross-institution requests do not confer access.
At the deadline, lecturer reads/open acknowledgements return 410; admin tracking
remains available. GET never marks a form opened. No submitted status is fabricated.
All payloads explicitly return `submissions_enabled=false`. Future submissions
must reuse this recipient authorization/deadline gate; no endpoint accepts them now.

## Focused verification

`test_form_sending.py` uses signed test JWTs, mock Identity and Academic owner
adapters, and real SQL storage with SQLite to test HTTP authorization and workflow.
No production sign-in or authentication bypass is added to application code.
`test_form_sending_postgres.py` is opt-in via
`SCHEDENCE_FORMS_TEST_DATABASE_CONFIG`, accepts only the exact isolated local test
database, and rolls back every fixture. It verifies real PostgreSQL RLS,
restricted grants, unique linked recipients, immutable records, first-open
tracking and the actual async SQL repository. No solver is invoked.
