# Required owner API contracts (pending owner implementation)

These are proposed configurable integration routes, **not currently implemented
Academic/Resource endpoints**. Teaching supplies GET-only clients and tests, not
changes to another component. Every route must verify the forwarded Cognito
token through its existing authentication/Identity integration and enforce the
explicit institution's permissions. A Teaching permission alone must never
bypass an owner's authorization. Use HTTPS outside loopback development.

| Owner | Required route | Scope |
| --- | --- | --- |
| Academic | `GET /api/v1/academic/teaching-inputs?institution_id=UUID&semester=1` | Complete authorized generation input export |
| Resource | `GET /api/v1/resource/teaching-inputs?institution_id=UUID` | Complete authorized venue/source export |
| Academic | `GET /api/v1/academic/form-recipients?institution_id=UUID` | Administrative eligible-lecturer directory |
| Academic | `GET /api/v1/academic/form-recipients/me?institution_id=UUID` | Current caller's unique staff/identity link only |

Academic's existing staff list is reusable for basic references, but cannot
establish teaching eligibility, department membership or student allocations.
The recipient self endpoint must be accessible to authorized lecturers without
granting them access to the full administrative staff roster.

## Generation exports

The read-only boundary initially preserves the existing owner's exported record
contracts so Teaching can reuse its existing owner-record/pre-solve validation.
Return JSON with `institution_id`, `complete: true` and `tables`. Every record
must include its existing nonzero `id` and matching `institution_id`. UUID
references remain existing owner IDs; do not create replacements or merge
duplicate module codes. An incomplete/paginated export cannot claim `complete`.

Academic `tables` must contain all these collections (empty arrays are explicit):

- `staff`, `source_datasets`, `departments`, `staff_teaching_profiles`;
- `specializations`, `modules`, `module_specializations`, `module_teaching_teams`;
- `lecturer_preferences`, `cohort_totals`, `pp1_specialization_allocations`,
  `pp1_student_groups`, `module_classification_reviews`.

Resource `tables` contains `source_datasets` and `venues`.

Records use the current owner fields already consumed by
`persistence/generation_inputs.py`: source checksums/provenance, staff-number and
profile links, module source IDs/record keys and original source values,
module-specialization/team links, allocation/group parents and counts, venue
codes/capacities/types and source IDs. These are service-private authorized
generation exports, **not** the public `teaching_shared` views. Preference input
may be disclosed only according to the Academic owner's permission policy;
never include login credentials, private Forms responses or student identity
details that generation does not need. Agree a narrower normalized contract
with the owner before replacing this existing validator input shape.

Teaching checks the envelope and every row's tenant, rejects duplicate record
IDs, validates the existing snapshot checksum and uses the existing pre-solve
validator. Scope/year/stream selection and Scheduling Rules remain Teaching's
responsibility. Upstream rows are never written back to their owners.

## Recipient exports

Return `institution_id`, `complete: true`, and `items` using the existing
`EligibleLecturer` contract in `forms/sending_contracts.py`:

```json
{
  "institution_id": "11111111-1111-4111-8111-111111111111",
  "complete": true,
  "items": [{
    "staff_id": "22222222-2222-4222-8222-222222222222",
    "staff_number": "SYNTHETIC-LECTURER",
    "full_name": "Synthetic Lecturer",
    "identity_user_id": null,
    "link_status": "NO_LINK",
    "department_ids": [],
    "departments": []
  }]
}
```

Only active Academic/Instructor profiles are eligible. Preserve staff UUIDs,
authoritative department UUIDs and explicit `identity_user_id` links. Compute
`READY`, `NO_LINK` or `AMBIGUOUS_LINK` using **all** staff links (including
inactive/non-teaching records), not emails, appointment titles or name matching.
The existing `AcademicLecturerDirectory.resolve` documents these semantics.
The `/me` response contains zero or one record for the actual token subject;
never accept an arbitrary user ID from a caller as identity authority.

Teaching rejects duplicate recipients/READY identity links and rechecks the
current self-link against the frozen recipient before lecturer access. Deadlines,
recipient tracking and immutability remain in Teaching's own RLS-protected
tables. Sending remains an explicit authorized operation; no mail or submissions
are introduced.

## Deployment prerequisites

Use the restricted Teaching runtime database credentials in the shared `.env`.
Apply the Teaching migrations/grants through `20261021_0004`. Supply an approved
Teaching snapshot/calendar in shared storage through the reviewed preparation
pipeline; no canonical snapshot was migrated from the excluded local experiments.
Until the owner APIs and approved inputs exist, HTTP error or MISSING readiness
checks are expected and do not take down timetables or draft Forms.
