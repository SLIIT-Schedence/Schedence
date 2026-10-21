# Draft form builder: Phase 1

For the subsequent sending-only phase, see [Form sending backend](form-sending-backend.md).

This Teaching-owned module reuses shared Cognito access-token verification and
Identity's current `/api/v1/identity/me/permissions` response on every request.
The existing institution-scoped `teaching:generate` permission authorizes template
management (Academic Admin's existing grant). Role labels and appointment titles
alone grant nothing. No Identity, Academic Data or Resource code is changed.

The following routes exist under `/api/v1/teaching/form-templates` and its existing
`/api/teaching/form-templates` compatibility prefix:

| Method | Route | Purpose |
| --- | --- | --- |
| GET | `/presets?institution_id=...` | Fresh server-owned predefined definitions |
| POST | root | Create a blank draft or instantiate a predefined template |
| GET | `?institution_id=...&page=1&page_size=20` | Bounded institution-only list |
| GET | `/{id}?institution_id=...` | Read a saved draft |
| PUT | `/{id}` | Save full edits with `expected_revision`; stale saves return 409 |
| POST | `/{id}/duplicate` | Independent saved copy, new template ID, revision 1 |

Create example:

```json
{
  "institution_id": "11111111-1111-4111-8111-111111111111",
  "title": "Lecturer Teaching Preferences",
  "preset_id": "lecturer-teaching-preferences.v1"
}
```

For a blank form, omit `preset_id` and optionally supply `questions`. On save,
send `institution_id`, `expected_revision`, `title`, `description` and the complete
`questions` array from the previous GET, with desired edits. The response contains
the incremented revision. Duplication accepts `institution_id` and optional `title`.
Question IDs are local to a template and retained in copies for stable schema mapping.

The predefined template has three LOCKED system questions:

| Scheduler key | Type | Fixed definition |
| --- | --- | --- |
| `teaching_availability` | `availability_matrix` | Monday–Sunday × MORNING/AFTERNOON; Preferred, Available, Not Preferred, Not Available |
| `module_preferences` | `module_preferences` | Existing `offering_id` references; no invented module identities |
| `preferred_weekly_teaching_hours` | `number` | Optional preference, 0–168 hours; not a hard workload cap |

Only `label` and `help_text` on LOCKED questions may change. Their IDs, ownership,
field keys/types, required status, matrix and bounds are compared against the
versioned server definition on every save, including on duplicated templates.
They cannot be removed or replaced by a custom question. Reordering is allowed.
No exact time-slot input is accepted by the availability schema; numeric day-part
boundaries remain institution policy in the existing Teaching preference adapter.

Optional requirements is a removable/editable SUGGESTED long-text question.
CUSTOM fields support text, long text, number, boolean, date, single choice and
multiple choice. Suggested/custom fields have no scheduler field key. Their
answers will not become scheduling constraints automatically. Sending,
deadlines, recipient identity resolution, responses, submissions, publication
and conversion to scheduler inputs are outside this phase.

Persistence uses a separate `FormsBase` metadata and
`teaching.form_template_drafts`, with explicit tenant predicates, forced PostgreSQL
RLS and atomic revision comparisons. Existing immutable timetable history and its
security checks are unchanged. SQL errors use the existing safe API envelope.
The additive migration `20261021_0003_form_template_drafts.sql` is provided but
never applied automatically. A deployment owner must review/apply it and grant
SELECT/INSERT/UPDATE on this new table to the restricted runtime role before
draft saving works. There are no grants to PUBLIC and no delete endpoint.
In isolated-generation mode, forms use the isolated result database through the
existing runtime store; read-only local-preview mode leaves form storage disabled.

Focused tests use real JWT verification, mock Identity responses, an in-memory
repository and SQLite SQL storage with a synchronous-session async test adapter.
They do not connect to Neon, run PostgreSQL migrations, generate timetables or
invoke CP-SAT. PostgreSQL RLS deployment still requires a separately authorized
isolated PostgreSQL integration check.
